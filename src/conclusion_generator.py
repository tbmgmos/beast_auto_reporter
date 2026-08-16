"""
Conclusion Generator Module

Генерирует заключение по субъективной оценке на основе импортированных проблем
Использует локальный LLM (Ollama) для генерации
"""

import logging
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.csv_importer import Issue
from src.gigachat_service import GigaChatService
from src.groq_service import GroqService
from src.marker_translation_service import MarkerTranslationService
from src.ollama_service import OllamaService
from src.technical_info_extractor import format_fps
from src.yandexgpt_service import YandexGPTService

logger = logging.getLogger(__name__)

STYLE_EXAMPLES_PATH = Path(__file__).resolve().with_name("manual_conclusion_style_examples.json")

# «Якорные» куски текста маркера, которые LLM не должна терять или путать
# между пунктами заключения при перефразировании — см. _extract_anchor_tokens.
_QUOTE_ANCHOR_RE = re.compile(r'[«"]([^»"]+)[»"]')
_NUMERIC_UNIT_ANCHOR_RE = re.compile(
    r'(?:~\s*)?-?\d+(?:[.,]\d+)?\s*(?:кадр\w*|дБ|LUFS|LRA|сек\w*|мс|fps|кГц|Гц)',
    re.IGNORECASE,
)


def _extract_anchor_tokens(text: str) -> set:
    """Извлекает из текста маркера дословные «якоря» — цитаты в кавычках

    (конкретные слова/реплики) и числа с единицами измерения (величины
    смещения, громкости и т.п.). Эти детали LLM обязана сохранять дословно
    (см. промпт: "ДЕТАЛИЗАЦИЯ (КРИТИЧНО)"), поэтому пропажа якоря из текста
    пункта или его появление в чужом пункте — надёжный признак того, что
    модель перепутала или потеряла деталь при перефразировании.
    """
    if not text:
        return set()
    anchors = set()
    for match in _QUOTE_ANCHOR_RE.finditer(text):
        quoted = match.group(1).strip()
        if quoted:
            anchors.add(quoted.lower())
    for match in _NUMERIC_UNIT_ANCHOR_RE.finditer(text):
        anchors.add(re.sub(r'\s+', ' ', match.group(0).strip().lower()))
    return anchors


# Промпт для summarize_version_changes. Подстановка — через .replace
# ("{brief}" -> бриф), а не .format: в тексте брифа могут встречаться
# фигурные скобки из описаний маркеров, и экранировать их негде.
_VERSION_SUMMARY_PROMPT = """Ты — помощник звукорежиссёра, который готовит отчёты об ошибках (QC-отчёты) для сериалов.

Ниже — текущие технические параметры новой версии отчёта и машинный список её различий со старой версией. Сформулируй по этим данным связную подробную сводку на русском языке: что исправлено, что добавлено, каковы сейчас ключевые технические показатели и что требует внимания.

Правила:
- 5–10 предложений обычным текстом, без списков, заголовков и markdown-разметки.
- Опирайся ТОЛЬКО на данные ниже — ничего не додумывай и не интерпретируй за пределами фактов.
- Обязательно назови ключевые технические параметры новой версии со значениями: громкость (LOUDNESS), пики (TRUE PEAK/SAMPLE PEAK), LRA, хронометраж, формат файла — те, что есть в данных. Отдельно отметь, все ли они в норме.
- Таймкоды приводи как есть; если изменений много, не перечисляй все подряд — назови самые важные (в первую очередь блокеры) и общее количество.
- Блокеры и параметры с пометкой «НЕ В НОРМЕ» в новой версии упомяни обязательно и явно.
- Если блокеры или нарушения нормы по сравнению со старой версией исчезли — отметь это как улучшение.
- Пиши сразу по существу, без вступлений вроде «В новой версии отчёта…».

Данные:
{brief}"""


class ConclusionGenerator:
    """Класс для генерации заключения"""
    
    def __init__(self, use_llm: bool = False, config: Optional[dict] = None):
        """
        Инициализация генератора
        
        Args:
            use_llm: Использовать ли LLM (Ollama) для генерации
        """
        self.use_llm = use_llm
        self.config = config or {}
        self._manual_style_examples_cache = None
        self.ollama_service = OllamaService(self.config)
        self.groq_service = GroqService(self.config)
        self.yandexgpt_service = YandexGPTService(self.config)
        self.gigachat_service = GigaChatService(self.config)
        self.marker_translation_service = MarkerTranslationService(self.config, self._ollama_generate)
        self.llm_model = self.ollama_service.model
        self.llm_temperature = self.ollama_service.temperature
        self.llm_max_tokens = self.ollama_service.max_tokens
        self.llm_timeout = self.ollama_service.timeout
        self.ollama_host = self.ollama_service.host

        llm_cfg = self.config.get("llm", {})

        # Ollama по умолчанию даёт модели окно всего 4096 токенов независимо
        # от заявленного максимума (у gemma4 — 131072) — на больших маркер-
        # листах (100+ маркеров) промпт+ответ вплотную подходят к этому
        # потолку (см. реальный замер: 2051 токен промпта + 2000 на ответ на
        # файле из 107 маркеров, запас 45 токенов). Держим больше про запас.
        self.num_ctx = int(llm_cfg.get("num_ctx", 8192))

        # "ollama" (по умолчанию, локально), "groq", "yandexgpt" или "gigachat"
        # (облако, для больших маркер-листов — см. UI-переключатель и подсказку
        # в главном окне). YandexGPT/GigaChat — варианты для регионов, где Groq/
        # OpenAI/Anthropic недоступны без VPN; GigaChat к тому же бесплатен
        # (1 млн токенов/мес), в отличие от YandexGPT.
        self.llm_provider = llm_cfg.get("provider", "ollama")

        # Пороги автовыбора облачного провайдера (см. maybe_auto_select_provider):
        # объём (число маркеров) ИЛИ разнообразие типов проблем — любой из двух
        # признаков сам по себе означает, что локальной gemma4 будет труднее.
        self.auto_select_marker_threshold = int(llm_cfg.get("auto_select_marker_threshold", 40))
        self.auto_select_distinct_types_threshold = int(llm_cfg.get("auto_select_distinct_types_threshold", 6))
        # Функция опциональна — переключается флажком в меню выбора модели
        # (см. _show_model_picker в главном окне).
        self.auto_select_llm_enabled = bool(llm_cfg.get("auto_select_enabled", True))

        logger.info(f"ConclusionGenerator инициализирован (LLM: {use_llm}, провайдер: {self.llm_provider})")

    def set_llm_model(self, model: str) -> None:
        """Переключает модель Ollama на лету (без пересоздания генератора) —

        используется UI-переключателем моделей рядом с индикатором AI-генерации.
        """
        self.llm_model = model
        self.ollama_service.model = model

    def set_llm_provider(self, provider: str) -> None:
        """Переключает провайдера ("ollama"/"groq") на лету — используется

        тем же UI-переключателем, что и set_llm_model.
        """
        self.llm_provider = provider

    def set_auto_select_llm_enabled(self, enabled: bool) -> None:
        """Включает/выключает автовыбор облачного провайдера (см.

        maybe_auto_select_provider) — переключается флажком в меню выбора модели.
        """
        self.auto_select_llm_enabled = enabled

    def check_ollama_status(self) -> bool:
        """Проверка доступности Ollama через настроенный host."""
        return self.ollama_service.check_status()

    def get_ollama_status(self) -> dict:
        """Детальный статус: доступен ли Ollama и установлена ли нужная модель."""
        return self.ollama_service.get_status()

    def get_groq_status(self) -> dict:
        """Детальный статус подключения к Groq (задан ли ключ, отвечает ли API)."""
        return self.groq_service.get_status()

    def get_yandexgpt_status(self) -> dict:
        """Детальный статус подключения к YandexGPT (заданы ли ключ и folder_id)."""
        return self.yandexgpt_service.get_status()

    def get_gigachat_status(self) -> dict:
        """Детальный статус подключения к GigaChat (задан ли ключ, отвечает ли OAuth)."""
        return self.gigachat_service.get_status()

    # Порядок предпочтения облачных провайдеров при автовыборе: GigaChat —
    # бесплатный и без VPN; YandexGPT — без VPN, но платный с первого токена;
    # Groq — обычно требует VPN в РФ. Пробуем в порядке убывания удобства для
    # пользователя, а не просто первый настроенный.
    _AUTO_SELECT_PROVIDER_PRIORITY = ("gigachat", "yandexgpt", "groq")

    def _provider_has_credentials(self, provider: str) -> bool:
        if provider == "groq":
            return bool(self.groq_service.get_api_key())
        if provider == "yandexgpt":
            return bool(self.yandexgpt_service.get_api_key() and self.yandexgpt_service.get_folder_id())
        if provider == "gigachat":
            return bool(self.gigachat_service.get_auth_key())
        return False

    def _probe_provider_reachable(self, provider: str, timeout: int = 4) -> bool:
        """Быстрая проверка реальной доступности с укороченным таймаутом —

        чтобы автовыбор не подвисал на десятки секунд, если, например, Groq
        недоступен без VPN. YandexGPTService.check_status() сети не дёргает
        по design (см. её докстринг) — там проверяются только учётные данные.
        """
        service = {
            "groq": self.groq_service,
            "yandexgpt": self.yandexgpt_service,
            "gigachat": self.gigachat_service,
        }[provider]
        original_timeout = service.timeout
        service.timeout = timeout
        try:
            return service.check_status()
        except Exception:
            return False
        finally:
            service.timeout = original_timeout

    def count_distinct_issue_types(self, issues: List[Issue], report_type: str = "main") -> int:
        """Число различных group_type среди маркеров — грубая мера

        «разнообразия» списка (см. maybe_auto_select_provider): много непохожих
        друг на друга проблем сложнее обобщить корректно, чем даже большое,
        но однородное количество маркеров одного типа.
        """
        return len({self._classify_single_issue(issue, report_type) for issue in issues})

    def maybe_auto_select_provider(self, issues: List[Issue], report_type: str = "main") -> tuple:
        """Решает, стоит ли на время генерации ЭТОГО заключения переключиться

        с локальной Ollama на облачный провайдер — по объёму маркер-листа ИЛИ
        разнообразию типов проблем (см. auto_select_marker_threshold/
        auto_select_distinct_types_threshold). Не трогает провайдера, если
        пользователь уже сам выбрал что-то отличное от "ollama" — автовыбор
        только помогает с локальной моделью, а не переопределяет явный выбор.

        Возвращает (provider_or_None, cloud_unavailable): provider_or_None —
        что временно подставить вызывающему коду (или None — остаться на
        Ollama); cloud_unavailable=True — сложность/объём оправдывали облако,
        но ни один настроенный облачный провайдер не отозвался (чтобы вызывающий
        код мог один раз ненавязчиво предупредить пользователя).
        """
        if not self.use_llm or self.llm_provider != "ollama" or not self.auto_select_llm_enabled:
            return None, False

        marker_count = len(issues)
        distinct_types = self.count_distinct_issue_types(issues, report_type)
        if marker_count < self.auto_select_marker_threshold and distinct_types < self.auto_select_distinct_types_threshold:
            return None, False

        any_configured = False
        for provider in self._AUTO_SELECT_PROVIDER_PRIORITY:
            if not self._provider_has_credentials(provider):
                continue
            any_configured = True
            if self._probe_provider_reachable(provider):
                return provider, False

        return None, any_configured

    def _ollama_generate(self, prompt: str, *, model: Optional[str] = None, options: Optional[dict] = None) -> str:
        """Единая обёртка над вызовом LLM — локальной Ollama или одного из

        облачных провайдеров (Groq/YandexGPT/GigaChat), в зависимости от
        self.llm_provider (переключается в UI).
        """
        if self.llm_provider == "groq":
            return self.groq_service.generate(prompt, model=model, options=options or {})
        if self.llm_provider == "yandexgpt":
            return self.yandexgpt_service.generate(prompt, model=model, options=options or {})
        if self.llm_provider == "gigachat":
            return self.gigachat_service.generate(prompt, model=model, options=options or {})
        return self.ollama_service.generate(
            prompt,
            model=model or self.llm_model,
            options=options or {},
        )

    def summarize_version_changes(self, comparison, old_label: str = None, new_label: str = None) -> str:
        """Связная LLM-сводка различий между двумя версиями отчёта — по готовому

        ReportComparison (см. report_uploader.compare_two_versions /
        compare_with_previous). Данные для промпта собираются
        format_comparison_brief'ом — сама функция только формулирует их
        человеческим языком. Идёт через текущего провайдера
        (_ollama_generate); основной сценарий — provider="groq" (быстро и
        без локальной модели), но работает и с Ollama/YandexGPT.
        """
        from src.report_uploader import format_comparison_brief

        brief = format_comparison_brief(comparison, old_label=old_label, new_label=new_label)
        prompt = _VERSION_SUMMARY_PROMPT.replace("{brief}", brief)
        logger.info("Генерация сводки изменений между версиями отчёта (провайдер: %s)...", self.llm_provider)
        # Низкая температура: задача пересказа фактов, не творческая.
        # num_predict ограничен — подробной сводке хватает десятка предложений.
        return self._ollama_generate(prompt, options={"temperature": 0.2, "num_predict": 900}).strip()

    def _load_manual_style_examples(self) -> list:
        """Загружает ручные эталонные примеры заключений из docx-выборки."""
        if self._manual_style_examples_cache is not None:
            return self._manual_style_examples_cache

        if not STYLE_EXAMPLES_PATH.exists():
            logger.warning(f"Файл с эталонными примерами не найден: {STYLE_EXAMPLES_PATH}")
            self._manual_style_examples_cache = []
            return self._manual_style_examples_cache

        try:
            with STYLE_EXAMPLES_PATH.open("r", encoding="utf-8") as fh:
                self._manual_style_examples_cache = json.load(fh)
        except Exception as exc:
            logger.warning(f"Не удалось загрузить эталонные примеры заключений: {exc}")
            self._manual_style_examples_cache = []

        return self._manual_style_examples_cache

    def _build_manual_style_examples_block(self, report_type: str) -> str:
        """
        Возвращает дистиллированные принципы из ручных docx-заключений.
        Нам важен стиль и уровень конкретики, но не нужно перегружать LLM
        сырыми длинными примерами, которые могут увести обобщение в сторону.
        """
        examples = [
            example
            for example in self._load_manual_style_examples()
            if example.get("report_type") == report_type
        ]
        if not examples:
            return ""

        style_notes = []
        for example in examples:
            note = (example.get("style_notes") or "").strip()
            if note and note not in style_notes:
                style_notes.append(note)

        lines = ["ПРИНЦИПЫ, ВЫВЕДЕННЫЕ ИЗ РУЧНЫХ DOCX-ЗАКЛЮЧЕНИЙ:"]
        lines.append("Ориентируйся на них как на стиль ручной редакторской работы.")
        lines.append("- Сохраняй конкретику маркера и не подменяй её общими словами без необходимости")
        lines.append("- Единичные и парные маркеры описывай конкретно, почти дословно по смыслу")
        lines.append("- Обобщай только действительно повторяющуюся или системную проблему")
        lines.append("- Опирайся на смысловую группу проблемы, а не на буквальное совпадение формулировок маркеров")
        lines.append("- Не смешивай разные по смыслу проблемы в одно обобщение")
        lines.append("- Если проблема относится ко всей структуре дорожки или стемов, допустим один крупный обобщённый пункт")
        lines.append("- Если маркеры разнородные, лучше несколько точных пунктов, чем одно расплывчатое обобщение")
        for note in style_notes:
            lines.append(f"- {note}")

        return "\n".join(lines).strip()
    
    def generate_technical_conclusion(self, tech_info: dict, params: dict = None, report_type: str = "standard") -> str:
        """
        Генерация технического заключения на основе параметров

        Args:
            tech_info: Техническая информация из аудио/видео/PDF
            params: Номинальные параметры из Параметры.txt
            report_type: Тип отчета (standard, me, me_ours, dcp)

        Returns:
            Текст технического заключения
        """
        # Сначала собираем все проблемы
        problems = []

        # Параметры по умолчанию
        target_lufs = params.get('target_lufs', -23.0) if params else -23.0
        # Дефолт True Peak должен совпадать с остальными модулями (-2.0)
        target_peak = params.get('true_peak', -2.0) if params else -2.0
        # Дефолт LRA должен совпадать с остальными модулями (18.0)
        target_lra = params.get('lra_max', 18.0) if params else 18.0
        lufs_tolerance = 0.5
        use_sample_peak = report_type == "dcp"
        peak_metric_key = 'sample_peak' if use_sample_peak else 'true_peak'

        # Для M&E отчетов НЕ проверяем LUFS и LRA (только TRUE PEAK)
        # Для DCP отчетов НЕ проверяем LUFS, LRA и SAMPLE PEAK
        check_lufs_lra = (report_type not in ("me", "dcp"))
        check_peak = True  # Всегда проверяем peak (для DCP — порог 0 dBFS)
        
        # Проверяем LUFS, TRUE PEAK, LRA для PDF файлов
        lufs_issues_20 = []
        lufs_issues_51 = []
        peak_issues_20 = []
        peak_issues_51 = []
        lra_issues_20 = []
        lra_issues_51 = []

        # Сохраняем значения, чтобы понять, когда нужно fallback на PyLoudNorm
        lufs_values_20 = []
        lufs_values_51 = []
        peak_values_20 = []
        peak_values_51 = []
        lra_values_20 = []
        lra_values_51 = []
        
        for pdf_key in [key for key in tech_info if str(key).startswith('pdf_')]:
            if pdf_key in tech_info and tech_info[pdf_key]:
                pdf_data = tech_info[pdf_key]
                is_20 = "20" in pdf_key
                
                # LUFS (пропускаем для M&E)
                if check_lufs_lra:
                    lufs = pdf_data.get('lufs')
                    if lufs is not None:
                        if is_20:
                            lufs_values_20.append(lufs)
                        else:
                            lufs_values_51.append(lufs)
                        if abs(lufs - target_lufs) > lufs_tolerance:
                            if is_20:
                                lufs_issues_20.append(lufs)
                            else:
                                lufs_issues_51.append(lufs)
                
                # Peak metric (TRUE PEAK для стандартных, SAMPLE PEAK для DCP)
                # Для DCP: не проверяем peak в заключении
                peak_value = pdf_data.get(peak_metric_key)
                if peak_value is None and use_sample_peak:
                    # Fallback для совместимости: если sample_peak отсутствует, используем true_peak.
                    peak_value = pdf_data.get('true_peak')
                if peak_value is not None:
                    if is_20:
                        peak_values_20.append(peak_value)
                    else:
                        peak_values_51.append(peak_value)
                    dcp_peak_threshold = 0 if report_type == "dcp" else target_peak
                    if peak_value > dcp_peak_threshold:
                        if is_20:
                            peak_issues_20.append(peak_value)
                        else:
                            peak_issues_51.append(peak_value)
                
                # LRA (пропускаем для M&E)
                if check_lufs_lra:
                    lra = pdf_data.get('lra')
                    if lra is not None:
                        if is_20:
                            lra_values_20.append(lra)
                        else:
                            lra_values_51.append(lra)
                        if lra > target_lra:
                            if is_20:
                                lra_issues_20.append(lra)
                            else:
                                lra_issues_51.append(lra)

        # Fallback: если нет данных PDF по конкретной метрике, берем из PyLoudNorm CSV
        fallback = self._load_pyloudnorm_fallback(tech_info)
        if fallback:
            if check_lufs_lra:
                if not lufs_values_20 and fallback.get('20'):
                    for row in fallback['20']:
                        lufs = row.get('lufs')
                        if lufs is not None and abs(lufs - target_lufs) > lufs_tolerance:
                            lufs_issues_20.append(lufs)
                    logger.info("Fallback LUFS 2.0: данные из PyLoudNorm")
                if not lufs_values_51 and fallback.get('51'):
                    for row in fallback['51']:
                        lufs = row.get('lufs')
                        if lufs is not None and abs(lufs - target_lufs) > lufs_tolerance:
                            lufs_issues_51.append(lufs)
                    logger.info("Fallback LUFS 5.1: данные из PyLoudNorm")

                if not lra_values_20 and fallback.get('20'):
                    for row in fallback['20']:
                        lra = row.get('lra')
                        if lra is not None and lra > target_lra:
                            lra_issues_20.append(lra)
                    logger.info("Fallback LRA 2.0: данные из PyLoudNorm")
                if not lra_values_51 and fallback.get('51'):
                    for row in fallback['51']:
                        lra = row.get('lra')
                        if lra is not None and lra > target_lra:
                            lra_issues_51.append(lra)
                    logger.info("Fallback LRA 5.1: данные из PyLoudNorm")

            fb_peak_threshold = 0 if report_type == "dcp" else target_peak
            if not peak_values_20 and fallback.get('20'):
                for row in fallback['20']:
                    peak_value = row.get('true_peak')
                    if peak_value is not None and peak_value > fb_peak_threshold:
                        peak_issues_20.append(peak_value)
                if use_sample_peak:
                    logger.info("Fallback Sample Peak 2.0: данные из PyLoudNorm true_peak")
                else:
                    logger.info("Fallback True Peak 2.0: данные из PyLoudNorm")
            if not peak_values_51 and fallback.get('51'):
                for row in fallback['51']:
                    peak_value = row.get('true_peak')
                    if peak_value is not None and peak_value > fb_peak_threshold:
                        peak_issues_51.append(peak_value)
                if use_sample_peak:
                    logger.info("Fallback Sample Peak 5.1: данные из PyLoudNorm true_peak")
                else:
                    logger.info("Fallback True Peak 5.1: данные из PyLoudNorm")
        
        # Формируем проблемы по интегральной громкости (только если не M&E)
        if check_lufs_lra:
            if lufs_issues_20 and lufs_issues_51:
                problems.append("Параметр «интегральная громкость» не соответствует допустимым значениям в фонограммах 2.0 и 5.1")
            elif lufs_issues_20:
                problems.append("Параметр «интегральная громкость» не соответствует допустимым значениям в фонограмме 2.0")
            elif lufs_issues_51:
                problems.append("Параметр «интегральная громкость» не соответствует допустимым значениям в фонограмме 5.1")
        
        # Формируем проблемы по пиковым значениям
        # Для DCP используем текст "sample peak", для остальных — "пиковые значения"
        peak_label = "sample peak" if report_type == "dcp" else "пиковые значения"
        if peak_issues_20 and peak_issues_51:
            problems.append(f"Параметр «{peak_label}» превышает допустимое значение в фонограммах 2.0 и 5.1")
        elif peak_issues_20:
            problems.append(f"Параметр «{peak_label}» превышает допустимое значение в фонограмме 2.0")
        elif peak_issues_51:
            problems.append(f"Параметр «{peak_label}» превышает допустимое значение в фонограмме 5.1")
        
        # Формируем проблемы по диапазону громкости (только если не M&E)
        if check_lufs_lra:
            if lra_issues_20 and lra_issues_51:
                problems.append("Параметр «диапазон громкости» превышает допустимое значение в фонограммах 2.0 и 5.1")
            elif lra_issues_20:
                problems.append("Параметр «диапазон громкости» превышает допустимое значение в фонограмме 2.0")
            elif lra_issues_51:
                problems.append("Параметр «диапазон громкости» превышает допустимое значение в фонограмме 5.1")
        
        # Проверяем порядок каналов (только 5.1) и формат 48/24
        STANDARD_51_ORDER = "L R C LFE Ls Rs"

        for audio_key in [key for key in tech_info if str(key).startswith('audio_')]:
            if audio_key not in tech_info or not tech_info[audio_key]:
                continue
            data = tech_info[audio_key]
            is_51 = "51" in audio_key
            track_label = "5.1" if is_51 else "2.0"

            # Порядок каналов — только для 5.1
            if is_51:
                co = data.get('channel_order', '')
                if not co or "channels" in co.lower() or co.lower() == "unknown":
                    problems.append(
                        f"Отсутствуют метаданные порядка каналов в звуковой дорожке {track_label} ({STANDARD_51_ORDER})"
                    )
                elif co.strip() != STANDARD_51_ORDER:
                    problems.append(
                        f"Порядок каналов неверный в звуковой дорожке {track_label} ({co.strip()})"
                    )

            # Проверяем формат 48/24 — для всех дорожек
            sr = data.get('sample_rate', 0)
            bd = str(data.get('bit_depth', '')).replace('PCM_', '')
            try:
                sr_khz = sr // 1000 if sr else 0
                bd_int = int(bd) if bd else 0
            except (ValueError, TypeError):
                sr_khz, bd_int = 0, 0
            if sr_khz != 48 or bd_int != 24:
                problems.append(
                    f"Формат аудио дорожки {track_label} не соответствует стандарту 48 kHz / 24 bit"
                )
        
        # Проверяем хронометраж
        durations = {}
        has_video = False
        has_audio = False
        
        for key in [key for key in tech_info if str(key).startswith('audio_')] + ['video']:
            if key in tech_info and tech_info[key]:
                data = tech_info[key]
                duration = data.get('duration')
                if duration and duration > 0:
                    durations[key] = duration
                    if key == 'video':
                        has_video = True
                    elif key.startswith('audio'):
                        has_audio = True
        
        # Проверяем совпадение хронометража
        if len(durations) > 1:
            duration_list = list(durations.values())
            reference_duration = duration_list[0]
            
            # Проверяем несовпадение видео и аудио
            video_audio_mismatch = False
            audio_mismatch = False
            
            if has_video and has_audio:
                video_dur = durations.get('video', 0)
                for key, dur in durations.items():
                    if key.startswith('audio'):
                        # Сравниваем округлённые миллисекунды (как в отчёте)
                        if int(dur * 1000) != int(video_dur * 1000):
                            video_audio_mismatch = True
                            break

            # Проверяем несовпадение между аудиофайлами
            audio_durations = [dur for key, dur in durations.items() if key.startswith('audio')]
            if len(audio_durations) > 1:
                ref_audio = audio_durations[0]
                for dur in audio_durations[1:]:
                    # Сравниваем округлённые миллисекунды (как в отчёте)
                    if int(dur * 1000) != int(ref_audio * 1000):
                        audio_mismatch = True
                        break
            
            if video_audio_mismatch:
                problems.append("Хронометраж видеофайла и аудиодорожек не совпадает")
            if audio_mismatch:
                problems.append("Звуковые файлы имеют разный хронометраж")
        
        # Проверяем кратность кадру только если приложен видеофайл:
        # без видео у нас нет надежного источника кадровой сетки для проверки дорожек.
        if has_video:
            fps = tech_info['video'].get('fps', 25)

            # Маппинг ключей tech_info → человекочитаемые названия дорожек
            track_labels = {
                'audio_20_c': '2.0 cens',
                'audio_20_uc': '2.0 uncens',
                'audio_51_c': '5.1 cens',
                'audio_51_uc': '5.1 uncens',
            }
            for audio_key in tech_info:
                if str(audio_key).startswith('audio_') and audio_key not in track_labels:
                    track_labels[audio_key] = str(audio_key).replace('audio_me_', '').replace('_20', ' 2.0').replace('_51', ' 5.1').upper()

            # Проверка кратности кадру (с допуском 0.5 мс)
            def is_frame_aligned(duration_seconds, fps=25):
                if duration_seconds <= 0:
                    return True
                ms = duration_seconds * 1000
                frame_ms = 1000.0 / fps
                nearest_frame = round(ms / frame_ms)
                return abs(ms - nearest_frame * frame_ms) < 0.5

            frame_issue_tracks = []
            for audio_key, label in track_labels.items():
                if audio_key in tech_info and tech_info[audio_key]:
                    dur = tech_info[audio_key].get('duration', 0)
                    if dur and dur > 0:
                        if not is_frame_aligned(dur, fps):
                            frame_issue_tracks.append(label)

            if frame_issue_tracks:
                for track in frame_issue_tracks:
                    problems.append(
                        f"Хронометраж дорожки {track} не кратен кадру ({format_fps(fps)} fps)"
                    )
        
        # Формируем заключение
        if not problems:
            return "По технической оценке нареканий не обнаружено."
        
        # ОТКЛЮЧЕНО: LLM для технического заключения (используем только шаблоны)
        # if self.use_llm:
        #     try:
        #         return self._generate_technical_with_llm(problems)
        #     except Exception as e:
        #         logger.error(f"Ошибка генерации через Ollama: {e}")
        #         logger.info("Переключаемся на шаблонную генерацию")
        #         # Продолжаем с шаблонным методом
        
        # Шаблонная генерация
        conclusion = "По техническим характеристикам выявлены следующие недочёты:\n"
        conclusion += "\n".join(f"- {problem}" for problem in problems)
        
        logger.info(f"Техническое заключение: {len(problems)} проблем")
        return conclusion

    def _load_pyloudnorm_fallback(self, tech_info: dict) -> dict:
        """
        Fallback: читаем CSV PyLoudNorm и возвращаем значения по каналам 2.0 / 5.1
        Возвращает: {'20': [{'lufs':..., 'true_peak':..., 'lra':...}, ...],
                     '51': [{'lufs':..., 'true_peak':..., 'lra':...}, ...]}
        """
        csv_path = tech_info.get('audio_analysis_csv') if tech_info else None
        if not csv_path:
            return {}

        path = Path(csv_path)
        if not path.exists():
            logger.warning(f"PyLoudNorm CSV не найден: {csv_path}")
            return {}

        def parse_float(value):
            if value is None:
                return None
            if isinstance(value, (int, float)):
                return float(value)
            value = str(value).strip()
            if value == "":
                return None
            try:
                return float(value)
            except ValueError:
                return None

        def detect_channel(name: str) -> str:
            n = (name or "").lower()
            if any(x in n for x in ["5.1", "5_1", "5-1", "_51", " 51 ", "51_", "51.", "5.0", "surround", "6ch", "6 ch"]):
                return "51"
            if any(x in n for x in ["2.0", "2_0", "2-0", "_20", " 20 ", "20_", "20.", "stereo"]):
                return "20"
            return ""

        result = {"20": [], "51": []}
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    file_name = row.get("file_name", "")
                    channel = detect_channel(file_name) or detect_channel(row.get("channel_layout", ""))
                    if channel not in ("20", "51"):
                        continue

                    entry = {
                        "lufs": parse_float(row.get("integrated_lufs")),
                        "true_peak": parse_float(row.get("true_peak_dbtp")),
                        "lra": parse_float(row.get("lra")),
                        "file_name": file_name
                    }
                    result[channel].append(entry)
        except Exception as e:
            logger.error(f"Ошибка чтения PyLoudNorm CSV: {e}")
            return {}

        return result
    
    def generate_subjective_conclusion(self, issues: List[Issue], report_type: str = "main") -> str:
        """
        Генерация субъективного заключения на основе списка проблем

        Args:
            issues: Список проблем из CSV
            report_type: Тип отчёта ("main", "me", "me_ours", и др.)

        Returns:
            Текст субъективного заключения (заглушка если LLM выключен)
        """
        if not issues:
            return "По субъективной оценке нареканий не обнаружено."

        issues = self._filter_conclusion_worthy(issues)
        if not issues:
            return "По субъективной оценке нареканий не обнаружено."

        issues = self._prepare_issues_for_subjective_conclusion(issues)

        try:
            return self._generate_subjective_with_llm(issues, report_type)
        except Exception as e:
            logger.error(f"Ошибка генерации заключения: {e}")
            return self._generate_simple_fallback(issues)

    def _filter_conclusion_worthy(self, issues: List[Issue]) -> List[Issue]:
        """
        Отбирает маркеры, достойные упоминания в заключении.
        В ручных отчётах редакторы не включают в заключение:
        - маркеры с пометкой «не критично» — они остаются в маркер-листе;
        - вопросы к заказчику («так задумано?») и просьбы («can we add
          fade out?») — но констатация дефекта из того же маркера в
          заключение попадает («We hear the cut. Can we fix it?» →
          остаётся только факт склейки).
        Блокеры не фильтруем никогда.
        """
        import copy as _copy

        worthy = []
        for issue in issues:
            if issue.blocker:
                worthy.append(issue)
                continue
            text = (issue.description_ru or issue.description or issue.description_original or "").strip()
            lowered = text.lower()
            if 'не критично' in lowered or 'not critical' in lowered:
                continue
            reduced = self._strip_question_part(text)
            if reduced is None:
                continue
            if reduced != text:
                issue = _copy.copy(issue)
                issue.description = reduced
                issue.description_original = reduced
                issue.description_ru = ""
            worthy.append(issue)
        return worthy

    @staticmethod
    def _is_client_question(text: str) -> bool:
        """Вопрос о намерении или просьба к заказчику — не констатация дефекта."""
        return bool(re.search(
            r'так задуман|это норма|точно необходим|точно нужн|нужн[аоы]? ли|надо ли|'
            r'это (?:спец)?эффект|похож на спецэффект|'
            r'можно ли|нельзя ли|\bможно\b|стоит ли|'
            r'is (?:this|it|that) (?:intended|normal|necessary|needed|ok|a\s+special\s+effect)|as intended|'
            r'\b(?:can|could|may) (?:we|you)\b|\bplease\b',
            text.lower(),
        ))

    def _strip_question_part(self, text: str) -> Optional[str]:
        """
        Убирает из маркера вопросительную/просительную часть.
        Возвращает None, если маркер целиком является вопросом о намерении
        («так задумано?») — такому не место в заключении.
        Чистый tentative-вопрос («Missing phrase?») трактуем как
        осторожную констатацию дефекта — оставляем без «?».
        """
        stripped = text.strip()
        if '?' not in stripped:
            return stripped

        sentences = re.split(r'(?<=[.!?])\s+', stripped)
        statements = [s for s in sentences if not s.rstrip(')»"\' ').endswith('?')]
        questions = [s for s in sentences if s.rstrip(')»"\' ').endswith('?')]

        if statements:
            result = ' '.join(statements).strip()
            return result if result else None

        # Маркер целиком из вопросов
        if any(self._is_client_question(q) for q in questions):
            return None
        result = ' '.join(q.rstrip('?)»"\' ').strip() for q in questions).strip()
        return result or None

    def _prepare_issues_for_subjective_conclusion(self, issues: List[Issue]) -> List[Issue]:
        """Подготавливает русскоязычный аналитический текст маркеров для заключения."""
        return self.marker_translation_service.prepare_issues(issues, use_llm=self.use_llm)

    @staticmethod
    def _issue_text(issue: Issue, prefer_original: bool = False) -> str:
        """Возвращает текст маркера для аналитики или оригинал при необходимости."""
        if prefer_original:
            return (
                issue.description_original
                or issue.description
                or issue.description_ru
                or ""
            ).strip()
        return (
            issue.description_ru
            or issue.description
            or issue.description_original
            or ""
        ).strip()
    
    def _generate_simple_fallback(self, issues: List[Issue]) -> str:
        """
        Упрощенное заключение без LLM (fallback)
        
        Args:
            issues: Список проблем
            
        Returns:
            Простое заключение со списком проблем
        """
        # Разделяем блокеры и обычные проблемы
        blockers = [issue for issue in issues if issue.blocker]
        regular_issues = [issue for issue in issues if not issue.blocker]

        problem_list = []

        # Сначала обычные проблемы (общие → частные)
        for issue in regular_issues:
            clean_desc = self._clean_marker_description(self._issue_text(issue))
            group_type = self._classify_single_issue(issue)
            if self._should_omit_timecode_in_conclusion(issue, group_type):
                problem_list.append(self._capitalize_summary(clean_desc))
            else:
                problem_list.append(f"На таймкоде {issue.timecode_in} {clean_desc}")

        # Блокеры последними
        for blocker in blockers:
            clean_desc = self._clean_marker_description(self._issue_text(blocker))
            group_type = self._classify_single_issue(blocker)
            if self._should_omit_timecode_in_conclusion(blocker, group_type):
                problem_list.append(self._capitalize_summary(clean_desc))
            else:
                problem_list.append(f"На таймкоде {blocker.timecode_in} {clean_desc}")

        conclusion = "По субъективной оценке выявлены следующие недочёты:\n"
        conclusion += "\n".join(f"-    {problem}" for problem in problem_list)
        
        logger.info(f"Субъективное заключение (fallback): {len(issues)} проблем")
        return conclusion
    
    def _generate_technical_with_llm(self, problems: list) -> str:
        """
        Генерация технического заключения через Ollama
        
        Args:
            problems: Список выявленных технических проблем
            
        Returns:
            Сгенерированное заключение
        """
        try:
            problems_text = "\n".join(f"- {problem}" for problem in problems)
            
            prompt = f"""Ты - эксперт по техническому контролю качества аудио/видео материалов. Составь техническое заключение по выявленным проблемам.

ВАЖНО: Начни заключение СТРОГО с фразы "По техническим характеристикам выявлены следующие недочёты:"

ВЫЯВЛЕННЫЕ ТЕХНИЧЕСКИЕ ПРОБЛЕМЫ:
{problems_text}

ТРЕБОВАНИЯ К ЗАКЛЮЧЕНИЮ:
1. Начни СТРОГО с "По техническим характеристикам выявлены следующие недочёты:"
2. После заголовка перейди на новую строку
3. Каждую проблему начинай с "- " (дефис с пробелом)
4. Используй простые, понятные формулировки
5. Сохраняй все технические значения и параметры из исходных проблем
6. Стиль: лаконичный, технический, но доступный

СТИЛЬ ФОРМУЛИРОВОК:
- Простой и понятный язык
- Короткие фразы без излишних деталей
- Сохраняй точные значения (LUFS, dBTP, fps и т.д.)
- НЕ добавляй выводы или рекомендации

ПРИМЕРЫ ИЗ РЕАЛЬНОГО ОТЧЁТА:
- Хронометраж видеофайла и аудиодорожек не совпадает
- Порядок каналов в 5.1 аудиодорожке некорректный
- Интегральная громкость (LUFS) фонограммы 2.0 (-22.3 LUFS) отклоняется от номинального значения (-23.0 LUFS)
- Длительность файла 2.0 cens не кратна кадру (25 fps, длительность кадра 40.00 мс)

Сгенерируй заключение:"""
            
            logger.info("Генерация технического заключения через Ollama...")
            
            conclusion = self._ollama_generate(
                prompt,
                options={
                    'temperature': min(self.llm_temperature, 0.2),  # тех. текст должен быть стабильнее
                    'num_predict': min(self.llm_max_tokens, 400),
                    'num_ctx': self.num_ctx,
                }
            )
            
            # Проверяем, что заключение начинается правильно
            if not conclusion.startswith("По техническим характеристикам"):
                conclusion = "По техническим характеристикам выявлены следующие недочеты:\n" + conclusion
            
            logger.info("Техническое заключение сгенерировано через Ollama")
            return conclusion
            
        except Exception as e:
            logger.error(f"Ошибка Ollama при генерации технического заключения: {e}")
            raise
    
    def _generate_subjective_with_llm(self, issues: List[Issue], report_type: str = "main") -> str:
        """
        Генерация субъективного заключения.
        Python группирует данные → LLM пишет саммари (если AI включен).
        Fallback: Python-версия (шаблонная).

        Args:
            issues: Список проблем из CSV
            report_type: Тип отчёта ("main", "me", "me_ours", и др.)

        Returns:
            Сгенерированное заключение
        """
        logger.info("=== Генерация субъективного заключения ===")

        # Разделяем блокеры и обычные проблемы
        blockers = [issue for issue in issues if issue.blocker]
        regular_issues = [issue for issue in issues if not issue.blocker]

        logger.info(f"Всего проблем: {len(issues)} (блокеров: {len(blockers)}, обычных: {len(regular_issues)})")

        # Группируем обычные проблемы по типу
        groups = self._smart_group_issues(regular_issues, report_type)
        if report_type in ("me", "me_ours"):
            groups = self._merge_me_context_groups(groups)
            groups = self._normalize_me_groups_for_conclusion(groups)
        else:
            blockers, groups = self._merge_main_blockers_into_groups(blockers, groups, report_type)

        logger.info(f"Сгруппировано в {len(groups)} групп:")
        for group_type, items in groups.items():
            logger.info(f"  - {group_type}: {len(items)} проблем")

        # ШАГ 1: Python формирует fallback-заключение (всегда нужен для валидации)
        python_conclusion = self._python_format_conclusion(blockers, groups, report_type)

        # Считаем ожидаемые пункты
        expected_items = len(blockers)
        for group_type, items in groups.items():
            if group_type == 'другие_проблемы':
                expected_items += len(items)
            else:
                expected_items += 1

        # ШАГ 2: LLM пишет заключение-саммари (если AI включен и есть что суммировать)
        # _write_conclusion_with_llm пишет по кусочкам: структуру уже решил Python
        # (см. python_conclusion выше), LLM только заполняет формулировки и сама
        # откатывается на python-текст для тех пунктов, где не справилась — поэтому
        # отдельная валидация всего результата целиком (как раньше) больше не нужна.
        if self.use_llm and expected_items >= 3:
            try:
                llm_conclusion = self._write_conclusion_with_llm(blockers, groups, report_type)
                if llm_conclusion:
                    logger.info("✅ Заключение написано через AI (по пунктам, с python-фолбэком)")
                    return llm_conclusion
            except Exception as e:
                logger.warning(f"AI недоступен ({e}), используем Python-версию")

        logger.info("✅ Заключение сформировано (Python)")
        return python_conclusion

    def _merge_main_blockers_into_groups(self, blockers: List[Issue], groups: dict, report_type: str = "main"):
        """
        Для основного отчёта часть блокеров лучше включать в общую смысловую группу,
        если это тот же повторяющийся дефект (например, задвоение музыки в нескольких местах).
        Иначе в заключении появляются одновременно и частные строки, и обобщение по одной проблеме.
        """
        if report_type in ("me", "me_ours"):
            return blockers, groups

        merged_groups = {group_type: list(items) for group_type, items in groups.items()}
        remaining_blockers = []

        for blocker in blockers:
            group_type = self._classify_single_issue(blocker, report_type)
            if self._should_keep_blocker_separate(group_type, blocker, report_type):
                remaining_blockers.append(blocker)
                continue

            merged_groups.setdefault(group_type, []).append(blocker)

        for items in merged_groups.values():
            items.sort(key=lambda item: item.timecode_in)

        return remaining_blockers, merged_groups

    def _should_keep_blocker_separate(self, group_type: str, issue: Issue, report_type: str = "main") -> bool:
        """
        Определяет, должен ли blocker остаться отдельным пунктом в основном отчёте.
        Критичные смысловые типы сохраняем явно; повторяющиеся технические/шумовые
        проблемы можно включать в общую группу.
        """
        if report_type in ("me", "me_ours"):
            return True

        if self._is_title_card_issue(self._issue_text(issue)):
            return True

        base_group_type = self._base_group_type(group_type)
        separate_types = {
            'другие_проблемы',
            'несинхронность',
            'отсутствие_звука',
            'маскировка',
            'замена_текста',
            'проблемы_реплик',
        }
        return base_group_type in separate_types

    def _write_conclusion_with_llm(self, blockers: List[Issue], groups: dict, report_type: str = "main") -> str:
        """
        LLM пишет заключение по кусочкам: Python уже решил структуру (сколько
        пунктов, какого они kind, какие у них таймкоды —
        _build_expected_structured_items), LLM для каждого пункта отдельно
        получает только его маркеры и пишет ОДНО предложение-саммари.

        Раньше один большой вызов сам решал и структуру, и формулировки —
        из-за этого модель либо не помещала все маркеры в контекст (см.
        num_ctx), либо на больших маркер-листах галлюцинировала, подставляя
        вместо реальных данных примеры из своего же промпта, либо просто
        группировала иначе, чем Python, и весь ответ браковался целиком (см.
        разбор реальных отчётов в этой сессии). При поблочной генерации
        каждый вызов маленький и видит только свои 1-4 маркера, а при сбое
        одного пункта откатывается только он (_summarize_item_with_llm), а
        не всё заключение сразу.
        """
        is_me = report_type in ("me", "me_ours")
        expected_items = self._build_expected_structured_items(blockers, groups, report_type)

        lines = []
        for expected_item in expected_items:
            llm_text = self._summarize_item_with_llm(expected_item, is_me)
            rendered = None
            if llm_text:
                rendered = self._format_structured_llm_item({
                    "omit_timecode": expected_item["omit_timecode"],
                    "timecodes": expected_item["timecodes"],
                    "text": llm_text,
                })
            lines.append(rendered or expected_item["line"])

        return "По субъективной оценке выявлены следующие недочёты:\n\n" + "\n".join(f"-    {line}" for line in lines)

    def _build_item_summary_prompt(self, expected_item: dict, is_me: bool) -> str:
        """Короткий промпт на один пункт заключения — только маркеры этого

        конкретного пункта, без общей JSON-схемы и без банка примеров с
        конкретными таймкодами (в этой сессии именно длинный общий промпт с
        примерами провоцировал модель копировать их вместо реальных данных
        на больших маркер-листах).
        """
        source_texts = expected_item.get("source_issue_texts") or []
        count = len(source_texts)
        markers_block = "\n".join(f"- {t}" for t in source_texts)

        domain_hint = (
            "Контекст: это M&E-дорожка (без диалогов — только музыка, синхронные "
            "шумы и звуковая атмосфера; голос актёра здесь — проблема, а не норма).\n\n"
            if is_me else ""
        )

        if count <= 1:
            count_rule = (
                "Единичное замечание — НЕ обобщай его, перескажи суть ПОЛНОСТЬЮ, "
                "сохрани все детали (конкретные слова в кавычках, величины, названия)."
            )
        elif count <= 3:
            count_rule = (
                f"{count} маркера/маркеров — сохрани конкретику каждого (это ещё не "
                "«частые повторения», обобщать вместо перечисления рано), но "
                "сформулируй компактно одним предложением."
            )
        else:
            count_rule = (
                f"{count} маркеров одного типа — обобщи КОНКРЕТНО одной фразой, "
                "не перечисляя каждый по отдельности. НЕ приводи дословные цитаты/"
                "реплики из отдельных маркеров — это уже не обобщение, а перечисление, "
                "если в фразе набирается несколько цитат подряд. Если среди них есть "
                "РАЗНЫЕ источники/подтипы (например: реплики актёра, речь из радио/ТВ/"
                "фильма в кадре, иностранный язык) — коротко назови эти категории "
                "(например, «в репликах персонажей, речи из смартфона и анимационных "
                "фрагментах»), не своди всё к одному слову вроде «реплики актёров», но "
                "и не превращай фразу в список конкретных слов/реплик в кавычках."
            )

        style_block = self._build_manual_style_examples_block("me" if is_me else "main")

        return f"""Ты — эксперт по контролю качества аудио для кинопроизводства.
{domain_hint}Маркеры одной проблемы из маркер-листа:
{markers_block}

{style_block}

Напиши ОДНО предложение — саммари этой проблемы для заключения.
{count_rule}

ПРАВИЛА:
- Используй только маркеры выше — не привноси факты, которых там нет
- Не меняй смысл маркеров и не делай выводов сверх того, что в них написано
- Сохраняй конкретные слова в кавычках, величины смещения, названия — дословно
- Пиши утвердительно, без "возможно"/"вероятно"
- Не пиши "на данном таймкоде"/"в данном фрагменте" — это пустые слова
- Не пиши таймкод, буллет и точку в конце — только саму фразу
- Не поясняй эти правила и не пиши ничего, кроме самой фразы

Напиши ТОЛЬКО фразу:"""

    def _summarize_item_with_llm(self, expected_item: dict, is_me: bool) -> Optional[str]:
        """Просит LLM сформулировать ОДНО предложение для уже готового

        (структуру решил Python) пункта заключения. При пустом ответе, сбое
        Ollama или потере обязательных деталей маркера — None, вызывающий
        код (_write_conclusion_with_llm) откатывается на python-текст именно
        этого пункта, а не всего заключения.
        """
        if not expected_item.get("source_issue_texts"):
            return None

        prompt = self._build_item_summary_prompt(expected_item, is_me)
        try:
            raw = self._ollama_generate(
                prompt,
                options={
                    "temperature": self.llm_temperature,
                    "num_predict": 200,
                    "num_ctx": 2048,
                },
            )
        except Exception as exc:
            logger.debug(f"LLM-саммари пункта не удалось получить: {exc}")
            return None

        text = raw.strip().strip('"').strip()
        if not text:
            return None

        lower = text.lower()
        if any(kw in lower for kw in ("рекоменд", "в целом", "итого", "вывод", "резюм")):
            logger.debug(f"LLM-саммари содержит вывод/рекомендацию вместо описания: {text!r}")
            return None

        # Якоря (цитаты/числа) обязаны сохраниться дословно только для 1-3
        # маркеров — там промпт (_build_item_summary_prompt, count_rule)
        # прямо требует полного пересказа/сохранения конкретики. Для 4+
        # маркеров правило противоположное: "обобщи КОНКРЕТНО, не перечисляя
        # каждый" — требовать при этом дословного присутствия ВСЕХ цитат
        # из всех объединяемых маркеров невозможно математически (сама и
        # просили обобщить), и это раньше приводило к тому, что валидная
        # обобщённая формулировка отклонялась и молча заменялась на голый
        # python-фолбэк (см. разбор реального GAMES-отчёта в этой сессии).
        source_count = len(expected_item.get("source_issue_texts") or [])
        required_anchors = expected_item.get("anchors") or set()
        if required_anchors and source_count <= 3:
            missing = required_anchors - _extract_anchor_tokens(text)
            if missing:
                logger.debug(
                    f"LLM-саммари потеряло детали маркера {sorted(missing)}, используем python-текст"
                )
                return None

        return text

    @staticmethod
    def _format_timecode_list(timecodes: List[str]) -> str:
        """Форматирует список таймкодов в человекочитаемый вид."""
        cleaned = [tc.strip() for tc in timecodes if tc and tc.strip()]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} и {cleaned[1]}"
        return ", ".join(cleaned[:-1]) + f" и {cleaned[-1]}"

    @staticmethod
    def _extract_timecodes_from_text(text: str) -> List[str]:
        """Извлекает все таймкоды из строки в порядке появления."""
        if not text:
            return []
        return re.findall(r'\d{2}:\d{2}:\d{2}:\d{2}', text)

    def _build_structured_contract_item(
        self, kind: str, line: str, source_issues: Optional[List[Issue]] = None,
    ) -> dict:
        """Строит один пункт заключения по той же python-логике, что и

        python-фолбэк (см. _python_format_conclusion) — используется и как
        сам фолбэк ("line"), и как техзадание для LLM-саммари конкретно
        этого пункта (_write_conclusion_with_llm/_summarize_item_with_llm).

        source_issues — маркеры, из которых собран этот пункт. Якоря (цитаты,
        числа с единицами) достаются ИЗ НИХ, а не из Python-версии текста —
        сама Python-строка уже перефразирует и не годится в эталон для
        дословных деталей.
        """
        clean_line = line.strip()
        anchors = set()
        source_texts = []
        for issue in source_issues or []:
            issue_text = self._issue_text(issue)
            anchors |= _extract_anchor_tokens(issue_text)
            source_texts.append(issue_text)
        return {
            "kind": kind,
            "timecodes": self._extract_timecodes_from_text(clean_line),
            "omit_timecode": not clean_line.startswith("На таймкод"),
            "anchors": anchors,
            "source_issue_texts": [t for t in source_texts if t],
            "line": clean_line,
        }

    def _build_expected_structured_items(self, blockers: List[Issue], groups: dict, report_type: str = "main") -> List[dict]:
        """
        Собирает ожидаемую structured-структуру из той же Python-логики,
        которая управляет fallback-заключением.
        """
        is_me = report_type in ("me", "me_ours")
        blocker_items = []
        specific_items = []
        general_items = []

        if is_me:
            all_groups = {}
            blocker_types = set()

            for group_type, items in groups.items():
                all_groups.setdefault(group_type, []).extend(items)

            for blocker in blockers:
                group_type = self._classify_single_issue(blocker, report_type)
                all_groups.setdefault(group_type, []).append(blocker)
                blocker_types.add(group_type)

            for group_type, items in all_groups.items():
                no_tc_items, regular_items = self._split_general_timeline_items(group_type, items, report_type)

                for item in no_tc_items:
                    line = self._format_issue_without_timecode(item)
                    target = blocker_items if group_type in blocker_types else general_items
                    target.append(self._build_structured_contract_item(
                        "blocker" if group_type in blocker_types else "general",
                        line,
                        source_issues=[item],
                    ))

                items = regular_items
                if not items:
                    continue

                if group_type == 'другие_проблемы':
                    for item in items:
                        line = f"На таймкоде {item.timecode_in} {self._summarize_description(self._issue_text(item))}"
                        if group_type in blocker_types:
                            blocker_items.append(self._build_structured_contract_item("blocker", line, source_issues=[item]))
                        else:
                            specific_items.append(self._build_structured_contract_item("specific", line, source_issues=[item]))
                    continue

                chunks = self._build_me_issue_chunks(group_type, items)
                for chunk in chunks:
                    chunk_items = chunk['items']
                    line = self._format_me_issue_line(group_type, chunk_items, report_type)
                    if group_type in blocker_types:
                        blocker_items.append(self._build_structured_contract_item("blocker", line, source_issues=chunk_items))
                    elif chunk['force_specific'] or len(chunk_items) <= 2:
                        specific_items.append(self._build_structured_contract_item("specific", line, source_issues=chunk_items))
                    elif group_type == 'заставки' or len(chunk_items) >= 3:
                        general_items.append(self._build_structured_contract_item("general", line, source_issues=chunk_items))
                    else:
                        specific_items.append(self._build_structured_contract_item("specific", line, source_issues=chunk_items))

            return blocker_items + specific_items + general_items

        for group_type, items in groups.items():
            no_tc_items, regular_items = self._split_general_timeline_items(group_type, items, report_type)
            for item in no_tc_items:
                line = self._format_issue_without_timecode(item)
                general_items.append(self._build_structured_contract_item("general", line, source_issues=[item]))

            items = regular_items
            if not items:
                continue

            count = len(items)
            if group_type == 'заставки':
                line = self._format_generalized_issue(group_type, items, report_type)
                general_items.append(self._build_structured_contract_item("general", line, source_issues=items))
                continue

            if group_type == 'другие_проблемы':
                for item in items:
                    line = f"На таймкоде {item.timecode_in} {self._summarize_description(self._issue_text(item))}"
                    specific_items.append(self._build_structured_contract_item("specific", line, source_issues=[item]))
                continue

            if count == 1:
                if self._should_omit_timecode_in_conclusion(items[0], group_type, report_type):
                    line = self._format_issue_without_timecode(items[0])
                    general_items.append(self._build_structured_contract_item("general", line, source_issues=[items[0]]))
                else:
                    line = f"На таймкоде {items[0].timecode_in} {self._summarize_description(self._issue_text(items[0]))}"
                    specific_items.append(self._build_structured_contract_item("specific", line, source_issues=[items[0]]))
            elif count in [2, 3] and self._is_important_type(group_type, report_type):
                timecodes = [item.timecode_in for item in items]
                if count == 2:
                    tc_text = f"{timecodes[0]} и {timecodes[1]}"
                else:
                    tc_text = f"{timecodes[0]}, {timecodes[1]} и {timecodes[2]}"
                line = f"На таймкодах {tc_text} {self._format_multiple_issue(group_type, items, report_type)}"
                specific_items.append(self._build_structured_contract_item("specific", line, source_issues=items))
            else:
                line = self._format_generalized_issue(group_type, items, report_type)
                general_items.append(self._build_structured_contract_item("general", line, source_issues=items))

        for blocker in blockers:
            group_type = self._classify_single_issue(blocker, report_type)
            if self._should_omit_timecode_in_conclusion(blocker, group_type, report_type):
                line = self._format_issue_without_timecode(blocker, use_blocker_summary=True)
            else:
                line = f"На таймкоде {blocker.timecode_in} {self._summarize_blocker(self._issue_text(blocker))}"
            blocker_items.append(self._build_structured_contract_item("blocker", line, source_issues=[blocker]))

        return blocker_items + specific_items + general_items

    def _format_structured_llm_item(self, item: dict) -> Optional[str]:
        """Собирает финальную строку заключения из одного JSON item."""
        if not isinstance(item, dict):
            return None

        omit_timecode = bool(item.get("omit_timecode"))
        timecodes = item.get("timecodes") or []
        if not isinstance(timecodes, list):
            return None

        text = self._clean_marker_description(str(item.get("text", "")).strip())
        if not text:
            return None

        # На всякий случай вычищаем дублирующие префиксы, если модель их всё же вернула.
        text = re.sub(r"^На таймкод[еа]х?\s+[\d:,\sи]+\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"^-+\s*", "", text).strip()
        text = text.rstrip(".").strip()
        if not text:
            return None

        if omit_timecode or not timecodes:
            return self._capitalize_summary(text)

        tc_text = self._format_timecode_list(timecodes)
        if not tc_text:
            return self._capitalize_summary(text)

        prefix = "На таймкоде" if len(timecodes) == 1 else "На таймкодах"
        return f"{prefix} {tc_text} {text}"

    def _collapse_repeated_issues(self, items: List[Issue]) -> list:
        """
        Группирует маркеры с одинаковым по смыслу описанием, сохраняя порядок.
        Возвращает [(представитель, [все маркеры группы]), ...].
        Один и тот же дефект на многих таймкодах человек описывает одной
        обобщённой строкой, а не перечислением.
        """
        grouped: dict = {}
        order: list = []
        for item in items:
            key = self._normalize_description_for_grouping(self._issue_text(item))
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(item)
        return [(grouped[key][0], grouped[key]) for key in order]

    def _python_format_conclusion(self, blockers: List[Issue], groups: dict, report_type: str = "main") -> str:
        """
        Генерация заключения в саммари-стиле (без LLM).
        Порядок: блокеры → частные (с TC) → общие (без TC).
        """
        is_me = report_type in ("me", "me_ours")

        # ── M&E: отдельная ветка ─────────────────────────────────────────────
        if is_me:
            # Объединяем все маркеры (обычные + блокеры) по типам.
            # Каждый тип появляется в заключении ровно один раз.
            all_groups: dict = {}      # type → [все маркеры]
            blocker_types: set = set() # типы с хотя бы одним блокером

            for group_type, items in groups.items():
                all_groups.setdefault(group_type, []).extend(items)

            for blocker in blockers:
                gt = self._classify_single_issue(blocker, report_type)
                all_groups.setdefault(gt, []).append(blocker)
                blocker_types.add(gt)

            specific_lines = []  # 1-2 маркера, с таймкодами
            blocker_lines = []   # блокеры
            general_lines = []   # 3+ маркеров, обобщённо без таймкодов

            for group_type, items in all_groups.items():
                no_tc_items, regular_items = self._split_general_timeline_items(group_type, items, report_type)

                for item in no_tc_items:
                    line = f"-    {self._format_issue_without_timecode(item)}"
                    if group_type in blocker_types:
                        blocker_lines.append(line)
                    else:
                        general_lines.append(line)

                items = regular_items
                if not items:
                    continue

                if group_type == 'другие_проблемы':
                    target_lines = blocker_lines if group_type in blocker_types else specific_lines
                    for representative, same_items in self._collapse_repeated_issues(items):
                        if len(same_items) >= 3:
                            desc = self._clean_marker_description(self._issue_text(representative))
                            if desc:
                                general_lines.append(f"-    В нескольких фрагментах {desc}")
                            continue
                        for item in same_items:
                            desc = self._summarize_description(self._issue_text(item))
                            if not desc.strip():
                                continue
                            if item.timecode_in:
                                target_lines.append(f"-    На таймкоде {item.timecode_in} {desc}")
                            else:
                                general_lines.append(f"-    {self._format_issue_without_timecode(item)}")
                    continue

                chunks = self._build_me_issue_chunks(group_type, items)
                for chunk in chunks:
                    chunk_items = chunk['items']
                    line = f"-    {self._format_me_issue_line(group_type, chunk_items, report_type)}"
                    if group_type in blocker_types:
                        blocker_lines.append(line)
                    elif chunk['force_specific'] or len(chunk_items) <= 2:
                        specific_lines.append(line)
                    elif group_type == 'заставки' or len(chunk_items) >= 3:
                        general_lines.append(line)
                    else:
                        specific_lines.append(line)

            # Порядок: блокеры → единичные (с TC) → обобщённые (без TC)
            conclusion_lines = ["По субъективной оценке выявлены следующие недочёты:", ""]
            conclusion_lines.extend(blocker_lines)
            conclusion_lines.extend(specific_lines)
            conclusion_lines.extend(general_lines)
            logger.info("✅ Субъективное заключение M&E (Python) сформировано")
            return '\n'.join(conclusion_lines)

        # ── Основной отчёт ───────────────────────────────────────────────────
        general_lines = []   # обобщённые проблемы (без таймкодов)
        specific_lines = []  # частные проблемы (с таймкодами)
        blocker_lines = []   # блокеры (последними)

        for group_type, items in groups.items():
            no_tc_items, regular_items = self._split_general_timeline_items(group_type, items, report_type)
            for item in no_tc_items:
                general_lines.append(f"-    {self._format_issue_without_timecode(item)}")

            items = regular_items
            if not items:
                continue

            count = len(items)

            if group_type == 'заставки':
                general_lines.append(f"-    {self._format_generalized_issue(group_type, items, report_type)}")
                continue

            if group_type == 'другие_проблемы':
                for representative, same_items in self._collapse_repeated_issues(items):
                    if len(same_items) >= 3:
                        desc = self._clean_marker_description(self._issue_text(representative))
                        if desc:
                            general_lines.append(f"-    В нескольких фрагментах {desc}")
                        continue
                    for item in same_items:
                        desc = self._summarize_description(self._issue_text(item))
                        if not desc.strip():
                            continue
                        if item.timecode_in:
                            specific_lines.append(f"-    На таймкоде {item.timecode_in} {desc}")
                        else:
                            general_lines.append(f"-    {self._format_issue_without_timecode(item)}")
                continue

            if count == 1:
                if not items[0].timecode_in or self._should_omit_timecode_in_conclusion(items[0], group_type, report_type):
                    general_lines.append(f"-    {self._format_issue_without_timecode(items[0])}")
                else:
                    specific_lines.append(
                        f"-    На таймкоде {items[0].timecode_in} {self._summarize_description(self._issue_text(items[0]))}"
                    )
            elif count in [2, 3] and self._is_important_type(group_type, report_type) and all(item.timecode_in for item in items):
                timecodes = [item.timecode_in for item in items]
                if count == 2:
                    tc_text = f"{timecodes[0]} и {timecodes[1]}"
                else:
                    tc_text = f"{timecodes[0]}, {timecodes[1]} и {timecodes[2]}"
                specific_lines.append(
                    f"-    На таймкодах {tc_text} {self._format_multiple_issue(group_type, items, report_type)}"
                )
            else:
                general_lines.append(f"-    {self._format_generalized_issue(group_type, items, report_type)}")

        for representative, same_blockers in self._collapse_repeated_issues(blockers):
            if len(same_blockers) >= 3:
                desc = self._clean_marker_description(self._issue_text(representative))
                if desc:
                    blocker_lines.append(f"-    В нескольких фрагментах {desc}")
                continue
            for blocker in same_blockers:
                desc = self._issue_text(blocker)
                if not desc.strip():
                    continue
                group_type = self._classify_single_issue(blocker, report_type)
                if not blocker.timecode_in or self._should_omit_timecode_in_conclusion(blocker, group_type, report_type):
                    blocker_lines.append(f"-    {self._format_issue_without_timecode(blocker, use_blocker_summary=True)}")
                    continue
                blocker_lines.append(
                    f"-    На таймкоде {blocker.timecode_in} {self._summarize_blocker(desc)}"
                )

        conclusion_lines = ["По субъективной оценке выявлены следующие недочёты:", ""]
        conclusion_lines.extend(blocker_lines)
        conclusion_lines.extend(specific_lines)
        conclusion_lines.extend(general_lines)
        logger.info("✅ Субъективное заключение (Python) сформировано")
        return '\n'.join(conclusion_lines)

    @staticmethod
    def _normalize_marker_audio_change(desc: str) -> str:
        """
        Нормализует типовые описания изменения уровня/фона в более естественный вид.
        Работает только для распознанных аудио-сущностей; иначе возвращает исходный текст.
        """
        import re

        if not desc:
            return desc

        def normalize_subject(subject: str) -> Optional[str]:
            subject = re.sub(r'^\s*уровень\s+', '', subject.strip(), flags=re.IGNORECASE)
            subject_patterns = [
                (r'фонов(?:ого|ой)\s+шума|фоновый\s+шум', 'фоновый шум'),
                (r'звуков(?:ой|ого)\s+атмосфер(?:ы|а)|звуковая\s+атмосфера', 'звуковая атмосфера'),
                (r'атмосфер(?:ы|а)', 'атмосфера'),
                (r'музык(?:и|а)', 'музыка'),
                (r'ревербераци(?:и|я)', 'реверберация'),
                (r'громкост(?:и|ь)', 'громкость'),
                (r'фон(?:а)?', 'фон'),
                (r'шум(?:а)?', 'шум'),
            ]
            for pattern, replacement in subject_patterns:
                if re.fullmatch(pattern, subject, flags=re.IGNORECASE):
                    return replacement
            return None

        def replace_level_change(match: re.Match) -> str:
            modifier = match.group('modifier') or ''
            subject = normalize_subject(match.group('subject'))
            if not subject:
                return match.group(0)
            direction = match.group('direction').lower()
            up_words = {'громче', 'выше', 'сильнее', 'громким'}
            verb = 'усиливается' if direction in up_words else 'ослабевает'
            return f"{modifier}{verb} {subject}".strip()

        patterns = [
            r'\b(?:уровень\s+)?(?P<subject>[а-яёa-z0-9\s-]{2,60}?)\s+'
            r'(?P<modifier>резко\s+)?становится\s+'
            r'(?P<direction>громче|тише|выше|ниже|сильнее|слабее)\b',
            r'\b(?P<modifier>резко\s+)?'
            r'(?P<direction>громче|тише|выше|ниже|сильнее|слабее)\s+'
            r'становится\s+(?:уровень\s+)?(?P<subject>[а-яёa-z0-9\s-]{2,60}?)\b',
        ]
        for pattern in patterns:
            desc = re.sub(pattern, replace_level_change, desc, flags=re.IGNORECASE)

        return desc

    @staticmethod
    def _normalize_description_for_grouping(description: str) -> str:
        """
        Нормализует текст для смысловой классификации маркеров.
        Здесь важнее устойчиво распознать тип проблемы, чем сохранить
        буквальную редактуру исходного текста.
        """
        desc = ConclusionGenerator._clean_marker_description(description or "")
        typo_fixes = [
            (r'\bпаралельно\b', 'параллельно'),
            (r'\bпаралельн', 'параллельн'),
            (r'\bнеммного\b', 'немного'),
            (r'\bпосоторонн', 'посторонн'),
        ]
        for pattern, replacement in typo_fixes:
            desc = re.sub(pattern, replacement, desc, flags=re.IGNORECASE)
        return desc.lower()

    @staticmethod
    def _is_music_duplication_issue_text(desc: str) -> bool:
        """
        Смысловая детекция кейсов, где в музыке слышится задвоение,
        параллельная вторая тема или лишняя музыкальная дорожка.
        """
        if not desc:
            return False

        music_context = any(token in desc for token in ['музык', 'саундтрек', 'трек', 'мелод'])
        if not music_context:
            return False

        if any(token in desc for token in ['задво', 'двоит', 'задваива', 'двойн', 'дублиру']):
            return True

        if re.search(r'парал+ел+ь?но\s+(звучит|играет)', desc):
            return True

        if any(phrase in desc for phrase in [
            'еще какая-то музыка',
            'ещё какая-то музыка',
            'посторонняя музыкальная дорожка',
            'лишняя музыкальная дорожка',
            'вторая музыкальная дорожка',
        ]):
            return True

        return 'гряз' in desc and 'музык' in desc and 'парал' in desc

    def _pick_group_context_description(self, items: List[Issue], report_type: str = "main") -> str:
        """
        Выбирает наиболее характерную формулировку по группе маркеров.
        Это лучше, чем всегда брать первый маркер: при 3+ кейсах ориентируемся
        на доминирующий смысл в группе.
        """
        descriptions = []
        for item in items:
            desc = self._summarize_description(self._issue_text(item))
            if desc:
                descriptions.append(desc)

        if not descriptions:
            return ""

        most_common_desc, count = Counter(descriptions).most_common(1)[0]
        if count >= 2:
            return most_common_desc

        return descriptions[0]

    def _build_me_issue_chunks(self, group_type: str, items: List[Issue]) -> List[dict]:
        """
        Разбивает M&E-группу на более точные чанки.

        Используется очень консервативно: только для рассинхрона и только
        когда внутри повторяющейся группы есть ровно один особый маркер,
        который важно сохранить отдельно.
        """
        base_group_type = self._base_group_type(group_type)
        if len(items) <= 2 or base_group_type != 'несинхронность':
            global_sync_items = [item for item in items if self._is_global_sync_issue(item, group_type)]
            non_global_items = [item for item in items if item not in global_sync_items]
            if global_sync_items and non_global_items:
                result = [
                    {'group_type': group_type, 'items': global_sync_items, 'force_specific': True},
                    {'group_type': group_type, 'items': non_global_items, 'force_specific': False},
                ]
                result.sort(key=lambda chunk: chunk['items'][0].timecode_in if chunk['items'] else '')
                return result
            return [{'group_type': group_type, 'items': items, 'force_specific': False}]

        by_description = {}
        for item in items:
            clean_desc = self._summarize_description(self._issue_text(item))
            key = clean_desc or self._issue_text(item).strip() or item.timecode_in
            by_description.setdefault(key, []).append(item)

        if len(by_description) == 1:
            return [{'group_type': group_type, 'items': items, 'force_specific': False}]

        repeated_chunks = []
        unique_chunks = []
        for chunk_items in by_description.values():
            chunk_items = sorted(chunk_items, key=lambda issue: issue.timecode_in)
            target = repeated_chunks if len(chunk_items) >= 2 else unique_chunks
            target.append({
                'group_type': group_type,
                'items': chunk_items,
                'force_specific': len(chunk_items) == 1,
            })

        # Если все маркеры уникальны, либо уникальных несколько, либо нет повторяющейся
        # базы для обобщения — не дробим группу и возвращаем старое поведение.
        if not repeated_chunks or len(unique_chunks) != 1:
            global_sync_items = [item for item in items if self._is_global_sync_issue(item, group_type)]
            non_global_items = [item for item in items if item not in global_sync_items]
            if global_sync_items and non_global_items:
                result = [
                    {'group_type': group_type, 'items': global_sync_items, 'force_specific': True},
                    {'group_type': group_type, 'items': non_global_items, 'force_specific': False},
                ]
                result.sort(key=lambda chunk: chunk['items'][0].timecode_in if chunk['items'] else '')
                return result
            return [{'group_type': group_type, 'items': items, 'force_specific': False}]

        result = repeated_chunks + unique_chunks
        result.sort(key=lambda chunk: chunk['items'][0].timecode_in if chunk['items'] else '')
        return result

    @staticmethod
    def _is_timeline_start_timecode(tc: str) -> bool:
        """
        Старт таймлайна может приходить как 00:00:00:00 или 01:00:00:00.
        Для монтажных файлов второй вариант тоже фактически означает
        "нулевую" позицию фонограммы.
        """
        tc = (tc or '').strip()
        return tc in {'00:00:00:00', '01:00:00:00'}

    @staticmethod
    def _capitalize_summary(text: str) -> str:
        """Делает первую букву заглавной, если текст не пустой."""
        return text[0].upper() + text[1:] if text else text

    @staticmethod
    def _has_global_scope_description(description: str) -> bool:
        """
        Определяет, что маркер описывает не локальный фрагмент,
        а общую проблему всей фонограммы, микса или видео.
        """
        desc = (description or '').lower()
        global_scope_markers = [
            'обе дорожки', 'две дорожки', 'все дорожки', 'все аудиодорож',
            'звуковые дорожки', 'обе аудиодорож', 'аудиодорож',
            'обе фонограмм', 'вся фонограмма', 'по всей фонограмме',
            'фонограмма целиком', 'на всей фонограмме',
            'весь микс', 'по всему миксу', 'микс целиком',
            'весь файл', 'во всем файле', 'во всём файле', 'по всему файлу',
            'на всем протяжении', 'на всём протяжении',
            'всё видео', 'все видео', 'по всему видео', 'видеофайл',
            'видеоряд', '2.0 и 5.1', '5.1 и 2.0',
        ]
        return any(marker in desc for marker in global_scope_markers)

    def _should_omit_timecode_in_conclusion(
        self,
        issue: Issue,
        group_type: str = "",
        report_type: str = "main",
    ) -> bool:
        """
        Стартовый маркер общего/system-wide дефекта в заключении пишем без таймкода.
        Это правило действует для всех типов отчётов.
        """
        desc = self._issue_text(issue)
        if self._is_title_card_issue(desc):
            return True

        if not self._is_timeline_start_timecode(issue.timecode_in):
            return False

        if self._is_global_sync_issue(issue, group_type):
            return True

        return self._has_global_scope_description(desc)

    def _split_general_timeline_items(
        self,
        group_type: str,
        items: List[Issue],
        report_type: str = "main",
    ) -> tuple[list[Issue], list[Issue]]:
        """
        Выделяет стартовые общие маркеры в отдельные пункты без таймкодов,
        чтобы они не смешивались с обычными локальными маркерами той же группы.
        """
        no_tc_items = [
            item for item in items
            if self._should_omit_timecode_in_conclusion(item, group_type, report_type)
        ]
        regular_items = [item for item in items if item not in no_tc_items]
        return no_tc_items, regular_items

    def _format_issue_without_timecode(self, issue: Issue, use_blocker_summary: bool = False) -> str:
        """Форматирует общий системный маркер без префикса 'На таймкоде'."""
        summary_fn = self._summarize_blocker if use_blocker_summary else self._summarize_description
        return self._capitalize_summary(summary_fn(self._issue_text(issue)))

    def _is_global_sync_issue(self, issue: Issue, group_type: str = "") -> bool:
        """
        Стартовый таймкод для маркера рассинхрона обеих дорожек трактуем как
        проблему всей фонограммы, а не локального фрагмента.
        """
        if self._base_group_type(group_type or self._classify_single_issue(issue)) != 'несинхронность':
            return False

        tc = (issue.timecode_in or '').strip()
        if not self._is_timeline_start_timecode(tc):
            return False

        desc = self._issue_text(issue).lower()
        global_track_markers = [
            'обе дорожки', 'две дорожки', 'двух дорож', 'обе аудиодорож',
            'звуковые дорожки', 'обе фонограмм', 'вся фонограмма',
            'фонограмма целиком', '2.0 и 5.1', '5.1 и 2.0',
        ]
        return any(marker in desc for marker in global_track_markers)

    def _is_generic_me_absence_issue(self, issue: Issue, group_type: str) -> bool:
        """
        Для M&E отличаем общий маркер вида "отсутствуют синхронные шумы"
        от конкретных локальных маркеров про серьги, руки, шаги и т.д.
        Если рядом есть конкретные маркеры того же домена, общий пункт в
        заключении становится избыточным и должен быть подавлен.
        """
        if self._base_group_type(group_type) != 'отсутствие_звука':
            return False

        desc = self._summarize_description(self._issue_text(issue)).lower()
        if not desc:
            return False

        generic_patterns = {
            'отсутствие_звука__sync': [
                'отсутствуют синхронные шумы',
                'не хватает синхронных шумов',
                'пропали синхронные шумы',
            ],
            'отсутствие_звука__background': [
                'отсутствует фоновый шум',
                'отсутствуют фоновые шумы',
                'не хватает фоновых шумов',
                'отсутствует атмосфера',
            ],
            'отсутствие_звука__music': [
                'отсутствует музыка',
                'не хватает музыки',
                'пропала музыка',
            ],
        }

        return any(pattern in desc for pattern in generic_patterns.get(group_type, []))

    def _normalize_me_groups_for_conclusion(self, groups: dict) -> dict:
        """
        Контекстная нормализация M&E-групп перед генерацией заключения.

        Если есть стартовый глобальный маркер рассинхрона всей фонограммы,
        локальные маркеры рассинхрона в субъективном заключении не дублируем:
        глобальный пункт уже покрывает эту проблему.
        """
        normalized = {}
        for group_type, items in groups.items():
            base_group_type = self._base_group_type(group_type)

            if base_group_type == 'несинхронность':
                global_sync_items = [item for item in items if self._is_global_sync_issue(item, group_type)]
                if global_sync_items:
                    normalized[group_type] = global_sync_items
                else:
                    normalized[group_type] = items
                continue

            if base_group_type == 'отсутствие_звука':
                generic_items = [item for item in items if self._is_generic_me_absence_issue(item, group_type)]
                specific_items = [item for item in items if item not in generic_items]
                if generic_items and specific_items:
                    normalized[group_type] = specific_items
                else:
                    normalized[group_type] = items
                continue

            normalized[group_type] = items

        return normalized

    @staticmethod
    def _split_group_type(group_type: str):
        if '__' in group_type:
            return group_type.split('__', 1)
        return group_type, None

    @staticmethod
    def _base_group_type(group_type: str) -> str:
        return ConclusionGenerator._split_group_type(group_type)[0]

    @staticmethod
    def _contextual_group_label(group_type: str) -> str:
        base_group_type, subtype = ConclusionGenerator._split_group_type(group_type)
        labels = {
            'щелчки_слюна': 'Щёлкающие звуки / слюна',
            'яркие_согласные': 'Яркое звучание согласных',
            'шипение': 'Высокочастотное шипение',
            'шипение__s_sound': 'Свистящее звучание звука «С»',
            'шипение__whistle': 'Свистящий высокочастотный призвук',
            'шипение__high_freq': 'Посторонние высокочастотные призвуки',
            'несинхронность': 'Несинхронность с изображением',
            'перегруз': 'Перегруз / пережатость',
            'реверберация': 'Проблемы реверберации',
            'заставки': 'Заставки (без звукового оформления)',
            'отсутствие_звука': 'Отсутствие звука',
            'треск': 'Треск',
            'шумы': 'Посторонние шумы',
            'саунд_дизайн': 'Отличия саунд-дизайна от оригинала',
            'саунд_дизайн__sync': 'Синхронные шумы отличаются от оригинала',
            'саунд_дизайн__background': 'Фоновые шумы отличаются от оригинала',
            'саунд_дизайн__music': 'Музыка отличается от оригинала',
            'маскировка': 'Маскировка нецензурной лексики',
            'проблемы_реплик': 'Проблемы с репликами',
            'разборчивые_гуры': 'Разборчивые гуры',
            'реплики_в_гурах': 'Разборчивые реплики в гурах',
            'отсутствие_гуров': 'Отсутствие гуров',
            'опциональный_трек': 'Материал для optional track',
            'вздохи_тональные': 'Вздохи и тональные реакции актёров',
            'громкость': 'Проблемы громкости',
            'центр_канал': 'Звук только в центральном канале',
            'surround_missing': 'Отсутствует сигнал в surround-каналах',
            'отсутствие_звука__sync': 'Отсутствие синхронных шумов',
            'отсутствие_звука__background': 'Отсутствие фоновых шумов',
            'отсутствие_звука__music': 'Отсутствие музыки',
            'атмосфера': 'Атмосфера / звуковое окружение',
            'замена_текста': 'Замена текста',
            'исправления': 'Незавершённые исправления',
            'другие_проблемы': 'Прочее',
            'атмосфера__noise_up': 'Фоновый шум усиливается',
            'атмосфера__noise_down': 'Фоновый шум ослабевает / пропадает',
            'атмосфера__noise_change': 'Меняется звучание фонового шума',
            'атмосфера__noise_jump': 'Скачок / склейка фонового шума',
            'атмосфера__noise_mixed': 'Изменения фонового шума',
        }
        return labels.get(group_type, labels.get(base_group_type, group_type if subtype is None else base_group_type))

    @staticmethod
    def _merge_me_context_groups(groups: dict) -> dict:
        """
        Для M&E объединяет родственные подтипы фонового шума в один общий пункт,
        если внутри семьи встречаются разнонаправленные изменения.
        """
        noise_group_keys = [
            key for key in groups
            if key.startswith('атмосфера__noise_') and key != 'атмосфера__noise_mixed'
        ]
        if len(noise_group_keys) <= 1:
            return groups

        merged = {}
        merged_items = []
        inserted = False

        for group_type, items in groups.items():
            if group_type in noise_group_keys:
                merged_items.extend(items)
                if not inserted:
                    merged['атмосфера__noise_mixed'] = []
                    inserted = True
                continue
            merged[group_type] = items

        if inserted:
            merged_items.sort(key=lambda item: item.timecode_in)
            merged['атмосфера__noise_mixed'] = merged_items

        return merged

    def _contextualize_group_type(self, group_type: str, description: str, report_type: str = "main") -> str:
        """
        Уточняет слишком широкие группы по смыслу самого маркера.
        Это нужно, чтобы не смешивать в одной группе противоположные изменения
        вроде "фоновый шум усиливается" и "фоновый шум ослабевает".
        """
        base_group_type = self._base_group_type(group_type)
        raw = (description or '').lower()
        clean = self._summarize_description(description).lower()
        text = f"{raw} | {clean}"

        def has_any(markers):
            return any(marker in text for marker in markers)

        background_markers = [
            'фоновый шум', 'фонового шума', 'фоновго шума', 'фоновой шум',
            'слой фонового шума', 'звучание фонового шума', 'скачок фона',
            'скачек фона', 'искажение фона', 'атмосфер', 'атмосфера',
            'салона автомобиля', 'шум из салона', 'цифровая тишина',
        ]
        music_markers = [
            'музык', 'песня', 'фортепиано', 'инструмент', 'саундтрек',
            'вокал', 'аплодисмент',
        ]
        sync_markers = [
            'синхронные шумы', 'слой синхронных шумов', 'шаг', 'движен', 'бумаг',
            'лист', 'стол', 'двер', 'автомобил', 'машин', 'удар', 'хлоп',
            'нож', 'стакан', 'предмет', 'рукопож', 'кувалд', 'камн',
            'одежд', 'шкаф', 'полк', 'папк', 'скрип пола', 'рук', 'лиц',
            'взаимодейств', 'касани', 'трогает',
        ]

        if report_type in ("me", "me_ours") and base_group_type in {'отсутствие_звука', 'саунд_дизайн'}:
            if has_any(music_markers):
                return f"{base_group_type}__music"
            if has_any(background_markers):
                return f"{base_group_type}__background"
            if base_group_type == 'отсутствие_звука':
                # Для M&E отсутствие звука почти всегда должно уйти в доменную группу.
                # Если это не музыка и не фон, по умолчанию считаем это отсутствием
                # синхронных шумов: предметные действия, шаги, удары, одежда и т.д.
                return f"{base_group_type}__sync"
            if has_any(sync_markers):
                return f"{base_group_type}__sync"
            # Для sound design, если домен не удалось точно распознать, безопаснее
            # считать это отличием синхронных шумов, чем оставлять абстрактную группу.
            return f"{base_group_type}__sync"

        if base_group_type == 'шипение':
            if has_any(['звука «с»', 'звука "с"', "'s' sound", '"s" sound', 'яркое свистящее звучание']):
                return 'шипение__s_sound'
            if has_any(['свистящ', 'whistle']):
                return 'шипение__whistle'
            if has_any(['высокочастот', 'high frequency']):
                return 'шипение__high_freq'
            return group_type

        if base_group_type not in {'атмосфера', 'шумы', 'громкость'}:
            return group_type

        is_background_noise = has_any(background_markers)
        if not is_background_noise:
            return group_type

        if has_any([
            'становится тише', 'ослабевает фоновый шум', 'пропадает слой',
            'пропадает фоновый шум', 'провал по громкости', 'обрыв', 'обрывается',
            'пропадант'
        ]):
            return 'атмосфера__noise_down'

        if has_any([
            'становится громче', 'усиливается фоновый шум',
            'возрастает громкость фонового шума', 'громкость фонового шума'
        ]):
            return 'атмосфера__noise_up'

        if has_any([
            'меняется звучание фонового шума', 'изменяется звучание фонового шума',
            'резко меняется звучание', 'звучание фонового шума'
        ]):
            return 'атмосфера__noise_change'

        if has_any([
            'скачок фона', 'скачек фона', 'искажение фона', 'склейка',
            'склейки', 'склейку'
        ]):
            return 'атмосфера__noise_jump'

        return group_type

    @staticmethod
    def _clean_marker_description(desc: str) -> str:
        """
        Общая очистка описания маркера от шаблонных и механических оборотов.
        Используется и в _summarize_blocker, и в _summarize_description.

        Убирает:
        - Дублирование таймкодов ("на данном таймкоде", "в данном месте")
        - Шаблонные обороты ("В данном фрагменте", "Ощущение, что")
        - Неуверенные конструкции ("возможно", "вероятно")
        - Механические повторы

        Сохраняет:
        - Конкретные слова, величины, названия
        - Содержательные скобки (слово "Бля", смещение ~8 кадров)
        """
        import re

        desc = desc.strip()
        if not desc:
            return desc

        # === 1. Убираем шаблонные ПРЕФИКСЫ (начало строки) ===
        prefixes_to_strip = [
            # Ссылки на таймкод (дублируют "На таймкоде TC" в заключении)
            r'[Нн]а данном таймкод[ее]\s+',
            r'[Вв] данном таймкод[ее]\s+',
            r'[Сс] этого таймкод[ае]\s+',
            r'[Нн]ачиная с этого таймкод[ае]\s+',
            r'[Нн]а данном фрагменте\s+',
            r'[Вв] данном фрагменте\s+',
            r'[Вв] данном фрагмент\s+',
            r'[Вв] данном месте\s+',
            r'[Нн]а данном месте\s+',
            r'[Вв] этом фрагменте\s+',
            r'[Вв] этом месте\s+',
            r'[Сс] этого момента\s+',
            r'[Нн]ачиная с этого момента\s+',
            r'[Зз]десь\s+',
            # Шаблонные вступления
            r'[Оо]щущение,?\s+что\s+',
            r'[Сс]оздаётся впечатление,?\s+что\s+',
            r'[Сс]оздается впечатление,?\s+что\s+',
            r'[Ее]сть ощущение,?\s+что\s+',
            r'[Сс]кладывается впечатление,?\s+что\s+',
            r'[Сс]убъективно,?\s+',
            r'[Пп]о ощущениям,?\s+',
        ]
        for pattern in prefixes_to_strip:
            desc = re.sub(r'^' + pattern, '', desc, count=1)

        # === 2. Убираем дубли таймкода ВНУТРИ текста ===
        # "на данном таймкоде возможно присутствует..." → "возможно присутствует..."
        desc = re.sub(r'[нН]а данном таймкод[ее]\s+', '', desc)
        desc = re.sub(r'[вВ] данном таймкод[ее]\s+', '', desc)
        desc = re.sub(r'[сС] этого таймкод[ае]\s+', '', desc)
        desc = re.sub(r'[нН]ачиная с этого таймкод[ае]\s+', '', desc)
        desc = re.sub(r'[нН]а данном фрагменте\s+', '', desc)
        desc = re.sub(r'[вВ] данном фрагменте\s+', '', desc)
        desc = re.sub(r'[вВ] данном месте\s+', '', desc)
        desc = re.sub(r'[сС] этого момента\s+', '', desc)
        desc = re.sub(r'[нН]ачиная с этого момента\s+', '', desc)

        # === 3. Убираем неуверенные/механические конструкции ===
        # "возможно присутствует" → "присутствует"
        desc = re.sub(r'\bвозможно\s+', '', desc, flags=re.IGNORECASE)
        desc = re.sub(r'\bвероятно\s+', '', desc, flags=re.IGNORECASE)
        desc = re.sub(r'\bпредположительно\s+', '', desc, flags=re.IGNORECASE)
        desc = re.sub(r'\bпо всей видимости\s+', '', desc, flags=re.IGNORECASE)
        desc = re.sub(r'\bскорее всего\s+', '', desc, flags=re.IGNORECASE)
        desc = re.sub(r'\bкак будто\s+', '', desc, flags=re.IGNORECASE)

        # === 4. Повторная чистка начала — после шагов 2-3 могут оголиться новые шаблонные префиксы ===
        # Пример: "возможно здесь присутствует..." → "здесь присутствует..." → "присутствует..."
        residual_prefixes = [
            r'^[Зз]десь\s+',
            r'^[Вв] данном месте\s+',
            r'^[Вв] этом месте\s+',
        ]
        for pattern in residual_prefixes:
            desc = re.sub(pattern, '', desc)

        # === 5. Убираем редакторские хвосты и служебные скобки ===
        # Редакторские пометки в конце маркера
        editorial_tails = [
            r'[.!]?\s*[Вв] предыдущей версии такого не было\.?$',
            r'[.!]?\s*[Тт]акого не было в предыдущей версии\.?$',
            r'[.!]?\s*[Вв] предыдущей версии было нормально\.?$',
            r'[.!]?\s*[Рр]аньше такого не было\.?$',
        ]
        for pattern in editorial_tails:
            desc = re.sub(pattern, '', desc).strip()

        # Длинные редакторские скобки с "но тут", "хотя", "не так страшно"
        desc = re.sub(r'\s*\([^)]{20,}\)', lambda m: m.group(0)
                      if not any(kw in m.group(0).lower() for kw in
                                 ['но тут', 'хотя', 'не так страшно', 'но это не', 'впрочем'])
                      else '', desc)

        desc = re.sub(r'\(нарезка[^)]*\)\s*', '', desc)

        # === 6. Убираем дублирование "таймкод" с конкретным TC внутри описания ===
        # "Звуковая дорожка начинается с таймкоде 01:00:07:12" → без дублирующего TC
        desc = re.sub(
            r'^звуковая дорожка начинается (?:с|на)\s+таймкод[еа]?\s+\d{2}:\d{2}:\d{2}:\d{2}',
            'звуковая дорожка начинается позже ожидаемого', desc, flags=re.IGNORECASE
        )

        # === 7. Семантические замены ===
        # "без звука вообще" → "отсутствует звук"
        desc = re.sub(r'^без звука.*$', 'отсутствует звук', desc, flags=re.IGNORECASE)
        typo_fixes = [
            (r'\bпропадант\b', 'пропадает'),
            (r'\bотсутсвует\b', 'отсутствует'),
            (r'\bотсутсвуют\b', 'отсутствуют'),
            (r'\bотсутстствует\b', 'отсутствует'),
            (r'\bотсутстствуют\b', 'отсутствуют'),
            (r'\bфоновго\b', 'фонового'),
            (r'\bакатрис', 'актрис'),
            (r'\bпереодическ', 'периодическ'),
            (r'\bпаралельно\b', 'параллельно'),
            (r'\bпаралельн', 'параллельн'),
            (r'\bскачек\b', 'скачок'),
            (r'\bиздавайем', 'издаваем'),
            (r'\bзвуко шагов\b', 'звука шагов'),
            (r'\bнеммного\b', 'немного'),
            (r'\bпосоторонн', 'посторонн'),
        ]
        for pattern, replacement in typo_fixes:
            desc = re.sub(pattern, replacement, desc, flags=re.IGNORECASE)
        desc = ConclusionGenerator._normalize_marker_audio_change(desc)

        # === 8. Убираем двойные пробелы после всех замен ===
        desc = re.sub(r'\s{2,}', ' ', desc).strip()

        # === 9. Строчная первая буква (если не имя собственное / кавычка) ===
        if desc and desc[0].isupper() and not desc[0] == '"' and not desc[0] == '«':
            desc = desc[0].lower() + desc[1:]

        return desc

    @staticmethod
    def _summarize_blocker(description: str) -> str:
        """
        Саммари для блокера — сохраняет максимум деталей.
        Блокеры критичны, описание должно быть информативным.
        """
        return ConclusionGenerator._clean_marker_description(description)

    @staticmethod
    def _is_title_card_issue(description: str) -> bool:
        """Проверка, относится ли проблема к заставкам."""
        desc = description.lower()
        return any(kw in desc for kw in ['заставк', 'заставка'])

    @staticmethod
    def _summarize_description(description: str) -> str:
        """
        Краткое саммари описания маркера для заключения.
        Использует общую очистку _clean_marker_description.
        """
        return ConclusionGenerator._clean_marker_description(description)

    @staticmethod
    def _is_me_sync_noise_issue(description: str) -> bool:
        """
        Для M&E локальные маркеры вида "звук/шаги выглядят несинхронно"
        обычно означают не общий рассинхрон дорожек, а отсутствие или
        некорректную синхронизацию синхронных шумов.
        """
        desc = (description or '').lower()
        has_sync = any(token in desc for token in [
            'несинхрон', 'несинх', 'не синхр', 'рассинхр',
            'задержк', 'опережа', 'отста', 'отстав', 'смещен', 'смещён', 'сдвиг',
        ])
        if not has_sync:
            return False

        # Общий рассинхрон дорожек/фонограммы сюда не попадает.
        if any(token in desc for token in [
            'обе дорожки', 'звуковые дорожки', 'аудиодорож', 'фонограмма',
            '2.0 и 5.1', '5.1 и 2.0',
        ]):
            return False

        # Реплики/голос — это отдельный смысл, не синхронные шумы.
        if any(token in desc for token in [
            'реплик', 'голос', 'диалог', 'фраз', 'говор', 'слышны реплики',
        ]):
            return False

        return any(token in desc for token in [
            'звук ', 'звука ', 'шаг', 'движен', 'рук', 'лиц', 'одежд',
            'нож', 'сковород', 'щипц', 'берет', 'берёт', 'кладет', 'кладёт',
            'взаимодейств', 'касани', 'трогает',
        ])

    @staticmethod
    def _is_missing_sound_element(desc: str) -> bool:
        """
        Маркеры вида «нет шагов», «пропали шаги актрисы», «нет акцентного слоя
        падения папок» — отсутствие синхронного шумового элемента, даже если
        слова «отсутствует»/«не хватает» не использованы.
        """
        if not desc:
            return False
        if not re.search(r'\b(нет|пропал[аио]?|пропадают|исчез(?:ла|ли|ло)?)\b', desc):
            return False
        # Реплики/голос — отдельный смысл
        if re.search(r'реплик|голос|диалог|говор|перевод|субтитр|титр', desc):
            return False
        return bool(re.search(
            r'шаг|шурш|удар|хлоп|слой|звук|падени|скрип|штанг|двер|бумаг|посуд|прибор|стук',
            desc,
        ))

    @staticmethod
    def _classify_me_voice_group(desc: str) -> Optional[str]:
        """
        M&E-специфичная детализация голосовых маркеров.
        Не даёт смешивать обычные реплики актёров с гурами и optional track.
        """
        if not desc:
            return None

        gur_markers = ['гур', 'гуры', 'гур-гур']
        gur_context = any(marker in desc for marker in gur_markers)

        missing_gur_markers = [
            'отсутствуют гуры', 'отсутствует гур', 'нет гур', 'нет гура',
            'без гуров', 'без гура', 'не хватает гур', 'не хватает гура',
        ]
        if gur_context and any(marker in desc for marker in missing_gur_markers):
            return 'отсутствие_гуров'

        optional_track_markers = [
            'опциональн', 'optional track', 'отдельный трек', 'отдельную дорож',
            'вынести', 'убрать из m&e', 'убрать из me', 'вынести из m&e', 'вынести из me',
        ]
        optional_content_markers = [
            'объявл', 'аэропорт', 'радио', 'раци', 'телевиз', 'тв ', ' tv',
            'телепрограмм', 'передач', 'фильм', 'из колонок', 'из динамик',
            'по громкоговор', 'перевод', 'дубляж', 'диктор', 'ведущ',
        ]
        optional_voice_markers = [
            'реплик', 'голос', 'говор', 'произнос', 'фраз', 'слово', 'русск',
            'тональн', 'реакц', 'вздох', 'смех', 'кашл',
        ]
        if (
            any(marker in desc for marker in optional_track_markers)
            and any(marker in desc for marker in optional_voice_markers + gur_markers)
        ):
            return 'опциональный_трек'

        if (
            any(marker in desc for marker in optional_content_markers)
            and any(marker in desc for marker in optional_voice_markers)
        ):
            return 'опциональный_трек'

        discernible_voice_markers = [
            'разборчив', 'неразбор', 'непонятн', 'слышн', 'голос', 'реплик',
            'фраз', 'слово', 'говор', 'произнос', 'русск',
        ]
        if gur_context and any(marker in desc for marker in discernible_voice_markers):
            if any(marker in desc for marker in ['реплик', 'фраз', 'слово', 'говор', 'произнос', 'русск']):
                return 'реплики_в_гурах'
            return 'разборчивые_гуры'

        return None

    @staticmethod
    def _is_me_sound_design_issue(desc: str) -> bool:
        """
        M&E-кейсы, где проблема описана как отличие от оригинальной дорожки/мастера
        по звучанию, реверберации, плотности, уровню или составу эффектов.
        """
        if not desc:
            return False

        reference_markers = [
            'оригинальн', 'в оригинале', 'оригинальной звуковой дорожки',
            'в мастере', 'мастере звучит',
        ]
        if not any(marker in desc for marker in reference_markers):
            return False

        # Реплики/optional track обрабатываются отдельно.
        if any(marker in desc for marker in ['реплик', 'голос', 'диктор', 'объявл', 'перевод']):
            return False

        comparison_markers = [
            'отличает', 'по звучанию', 'по уровню громкости', 'ярче', 'глуше',
            'тише', 'громче', 'более плотно', 'плотно', 'реверб',
            'не хватает', 'нехватает', 'отсутствует', 'нет в оригинальной',
            'которого нет', 'который отсутствует',
        ]
        effect_markers = [
            'звук', 'звуки', 'удар', 'хлоп', 'закрыван', 'открыван', 'двер',
            'автомобил', 'машин', 'бумаг', 'лист', 'стол', 'предмет',
            'нож', 'рукопож', 'камн', 'кувалд', 'шаг', 'движен',
        ]
        return (
            any(marker in desc for marker in comparison_markers)
            and any(marker in desc for marker in effect_markers)
        )
    
    def _smart_group_issues(self, issues: List[Issue], report_type: str = "main") -> dict:
        """Умная группировка проблем по типам"""
        groups = {}
        is_me = report_type in ("me", "me_ours")

        for issue in issues:
            desc = self._normalize_description_for_grouping(self._issue_text(issue))
            me_voice_group = self._classify_me_voice_group(desc) if is_me else None

            # Определяем тип проблемы
            # Щелчки и слюна — ОДНА группа (артикуляционные дефекты)
            if any(kw in desc for kw in ['щелч', 'щёлк', 'клик', 'click', 'clik', 'clicing',
                                          'цокан', 'щелкающ', 'слюна', 'слюн',
                                          'saliva', 'lip smack', 'mouth click', 'mouth clik']):
                group_type = 'щелчки_слюна'
            # Яркое звучание согласных ("резкое звучание букв Б и П", "резкое К в слове...")
            elif any(kw in desc for kw in ['резкое звучание', 'резкое произношение',
                                            'яркое звучание', 'яркие согласн',
                                            'резк' if ('букв' in desc or 'слов' in desc) else '\x00']):
                group_type = 'яркие_согласные'
            elif any(kw in desc for kw in ['шип', 'шипение', 'свист', 'сибилянт', 'высокочастот',
                                          'hiss', 'sibilance', 'high frequency', 'high frequnecy']):
                group_type = 'шипение'
            elif is_me and self._is_me_sync_noise_issue(self._issue_text(issue)):
                group_type = 'отсутствие_звука'
            elif is_me and self._is_me_sound_design_issue(desc):
                group_type = 'саунд_дизайн'
            elif any(kw in desc for kw in ['несинхрон', 'несинх', 'не синхр', 'рассинхр',
                                            'задержк', 'опережа', 'отста', 'отстав',
                                            'смещен', 'смещён', 'сдвиг',
                                            'липсинк', 'lip sync', 'lip-sync', 'unsync',
                                            'out of sync', 'desync', 'sync drift',
                                            'появляется позже', 'позже изображения']):
                group_type = 'несинхронность'
            elif any(kw in desc for kw in ['перегруз', 'пережат', 'клиппинг', 'клипп', 'дисторш',
                                            'distort', 'overload', 'clipping', 'clipped']):
                group_type = 'перегруз'
            elif any(kw in desc for kw in ['реверб', 'ревербер', 'эхо', 'reverb', 'echo']):
                group_type = 'реверберация'
            # Заставки — отдельная группа (без таймкодов в заключении)
            elif any(kw in desc for kw in ['заставк', 'заставка', 'screensaver']):
                group_type = 'заставки'
            elif any(kw in desc for kw in ['только в центральном канале', 'только в центральный канал',
                                            'only in the central channel', 'only in central channel']):
                group_type = 'центр_канал'
            elif any(kw in desc for kw in ['каналах ls и rs', 'каналах ls', 'каналах rs', 'surround-каналах',
                                            'surround channels', 'left and right surround']):
                group_type = 'surround_missing'
            elif any(kw in desc for kw in ['отсутств', 'нет звука', 'без звука', 'тишина', 'пропал звук',
                                            'не хватает', 'нехватает', 'недостаёт', 'недостает',
                                            'обрыв', 'обрыва', 'обрывается',
                                            'no audio', 'missing audio', 'silence', 'disappeared',
                                            'dissappeared', 'absent']) or self._is_missing_sound_element(desc):
                group_type = 'отсутствие_звука'
            elif any(kw in desc for kw in ['треск', 'трещ', 'потрескив', 'crackle', 'crackling']):
                group_type = 'треск'
            elif any(kw in desc for kw in ['фоновый шум', 'фоновог', 'фоновой', 'фоновом',
                                            'фоновую', 'фоновг', 'слой фона', 'слой атмосф',
                                            'звучание фона', 'изменяется фон']):
                group_type = 'атмосфера'
            elif any(kw in desc for kw in ['шум', 'шуршан', 'noise', 'гул', 'стук', 'стучащ', 'постукива',
                                          'hum', 'buzz', 'rumble', 'knock', 'bang',
                                          'electrical', 'interference', 'электрический']):
                group_type = 'шумы'
            elif any(kw in desc for kw in ['маскир', 'цензур', 'нецензур', 'бип', 'beep',
                                          'censor', 'profanity', 'obscene']):
                group_type = 'маскировка'
            elif any(kw in desc for kw in ['исправлен', 'попытк']):
                group_type = 'исправления'
            elif any(kw in desc for kw in ['замена', 'видна замена', 'виден', 'видно']):
                group_type = 'замена_текста'
            elif me_voice_group:
                group_type = me_voice_group
            # Вздохи и тональные реакции — отдельная группа для M&E (проверяем ДО реплик)
            elif is_me and any(kw in desc for kw in ['вздох', 'выдох', 'вдох', 'дыхан', 'дышит',
                                                      'тональн', 'реакц', 'смех', 'смеёт',
                                                      'смеет', 'хихик', 'кашл', 'покашл', 'хмык',
                                                      'хмм', 'фырк', 'стон', 'оханье', 'аханье',
                                                      'breath', 'breathing', 'inhale', 'exhale',
                                                      'laugh', 'cough', 'sigh']):
                group_type = 'вздохи_тональные'
            elif is_me and re.search(r'чита[юе]тся|слышн[оыа]|звучит|доносит', desc) and re.search(r'фраз|слов[оа]\b|шепот|шёпот|речь', desc):
                # В M&E любая разборчивая речь — дефект, даже без слова «реплика»
                group_type = 'проблемы_реплик'
            elif any(kw in desc for kw in ['неразбор', 'разбор', 'непонятн', 'голос', 'реплик',
                                          'unintelligible', 'hard to make out', 'hard to hear']):
                group_type = 'проблемы_реплик'
            elif any(kw in desc for kw in ['громк', 'громч', 'тих', 'тише', 'уровень',
                                          'too loud', 'too quiet', 'volume', 'too silent']):
                group_type = 'громкость'
            elif any(kw in desc for kw in ['склейк', 'склейка', 'hear the cut', 'audible cut',
                                          'cut between', 'splice']):
                group_type = 'шумы'
            elif self._is_music_duplication_issue_text(desc):
                group_type = 'задвоение_музыки'
            elif any(kw in desc for kw in ['звук', 'атмосфер']):
                group_type = 'атмосфера'
            else:
                group_type = 'другие_проблемы'

            group_type = self._contextualize_group_type(group_type, self._issue_text(issue), report_type)

            if group_type not in groups:
                groups[group_type] = []
            groups[group_type].append(issue)

        return groups
    
    def _is_important_type(self, group_type: str, report_type: str = "main") -> bool:
        """Проверка, является ли тип проблемы важным для перечисления таймкодов"""
        group_type = self._base_group_type(group_type)
        # проблемы_реплик — уже "важный" тип для M&E (ниже) и уже держится
        # отдельным пунктом для main-блокеров (см. _should_keep_blocker_separate) —
        # отсутствие его здесь для обычных (не-блокерных) main-групп было
        # несоответствием: 2-3 разных по сути маркера неразборчивости реплик
        # (например, про разных персонажей в разных местах серии) молча
        # схлопывались в одно расплывчатое обобщение вместо перечисления
        # таймкодов, как для несинхронности/отсутствия звука.
        # шипение — та же история: реальный кейс с двумя маркерами шипения на
        # РАЗНЫХ конкретных репликах («Не парься...», «Ты мне нравишься очень»)
        # схлопывался в общую фразу без таймкодов, хотя это два разных места.
        important = [
            'несинхронность', 'отсутствие_звука', 'маскировка', 'реверберация',
            'проблемы_реплик', 'шипение',
        ]
        if report_type in ("me", "me_ours"):
            important += [
                'саунд_дизайн',
                'разборчивые_гуры',
                'реплики_в_гурах',
                'отсутствие_гуров',
                'опциональный_трек',
                'вздохи_тональные',
            ]
        return group_type in important

    def _classify_single_issue(self, issue: Issue, report_type: str = "main") -> str:
        """Возвращает group_type для одной проблемы (использует ту же логику, что и _smart_group_issues)."""
        groups = self._smart_group_issues([issue], report_type)
        for gt in groups:
            return gt
        return 'другие_проблемы'

    def _format_me_single_issue(self, group_type: str) -> str:
        """
        M&E-специфичный текст для единичных случаев (вместо сырого описания).
        Возвращает None, если тип не требует переопределения.
        """
        group_type = self._base_group_type(group_type)
        me_single = {
            'отсутствие_звука': 'отсутствуют синхронные шумы',
            'проблемы_реплик': 'присутствуют реплики актёров',
            'вздохи_тональные': 'присутствуют вздохи и тональные реакции актёров',
        }
        return me_single.get(group_type)

    def _format_me_issue_line(self, group_type: str, items: List[Issue], report_type: str) -> str:
        """
        Единая функция форматирования одной строки для M&E заключения.
        Правило: 1 маркер → с TC, 2 маркера → оба TC, 3+ → без TC.
        Для типов где категория сама по себе несёт смысл (голос, отсутствие шумов) —
        используются шаблонные фразы. Для остальных — фактическое описание из маркера.
        """
        # Типы, у которых категория = информация (голос в M&E).
        # отсутствие_звука при count==1 использует реальное описание (конкретнее шаблона),
        # при count>=2 — шаблон "отсутствуют синхронные шумы".
        TEMPLATE_TYPES = {
            'саунд_дизайн',
            'проблемы_реплик',
            'разборчивые_гуры',
            'реплики_в_гурах',
            'отсутствие_гуров',
            'опциональный_трек',
            'вздохи_тональные',
            'отсутствие_звука',
        }
        TEMPLATE_TYPES_SINGLE = {'проблемы_реплик', 'вздохи_тональные'}  # только для count==1

        count = len(items)
        base_group_type = self._base_group_type(group_type)

        # Заставки — всегда без TC, независимо от количества
        if base_group_type == 'заставки':
            return self._format_generalized_issue(group_type, items, report_type)

        if count == 1:
            item = items[0]
            if self._should_omit_timecode_in_conclusion(item, group_type, report_type):
                return self._format_issue_without_timecode(item)
            if base_group_type in TEMPLATE_TYPES_SINGLE:
                me_text = self._format_me_single_issue(group_type)
                if me_text:
                    return f"На таймкоде {item.timecode_in} {me_text}"
            return f"На таймкоде {item.timecode_in} {self._summarize_description(self._issue_text(item))}"

        if count == 2:
            tc1, tc2 = items[0].timecode_in, items[1].timecode_in
            if base_group_type in TEMPLATE_TYPES or '__' in group_type:
                phrase = self._format_multiple_issue(group_type, items, report_type)
                return f"На таймкодах {tc1} и {tc2} {phrase}"
            # Используем фактическое описание первого маркера — конкретнее шаблона
            desc = self._summarize_description(self._issue_text(items[0]))
            return f"На таймкодах {tc1} и {tc2} {desc}"

        # count >= 3
        if base_group_type in TEMPLATE_TYPES or '__' in group_type or base_group_type == 'несинхронность':
            return self._format_generalized_issue(group_type, items, report_type)
        # Для 3+ маркеров берём доминирующую формулировку по группе, а не первый маркер
        desc = self._pick_group_context_description(items, report_type)
        if desc:
            desc = desc[0].lower() + desc[1:]
        return f"В нескольких фрагментах {desc}" if desc else self._format_generalized_issue(group_type, items, report_type)
    
    def _format_single_issue(self, description: str) -> str:
        """Форматирование единичной проблемы"""
        desc = description.strip()
        if desc.startswith('В данном фрагменте'):
            desc = desc[len('В данном фрагменте'):].strip()
        if not desc[0].islower():
            desc = desc[0].lower() + desc[1:]
        return f"присутствует {desc}" if not desc.startswith('присутств') else desc
    
    def _format_multiple_issue(self, group_type: str, items: List[Issue], report_type: str = "main") -> str:
        """Форматирование для случая ровно 2 маркеров одного типа (используется с указанием обоих TC)."""
        is_me = report_type in ("me", "me_ours")
        base_group_type, subtype = self._split_group_type(group_type)

        if group_type == 'атмосфера__noise_mixed':
            return "меняется звучание фонового шума"
        elif group_type == 'атмосфера__noise_up':
            return "усиливается фоновый шум"
        elif group_type == 'атмосфера__noise_down':
            return "ослабевает или пропадает слой фонового шума"
        elif group_type == 'атмосфера__noise_change':
            return "резко меняется звучание фонового шума"
        elif group_type == 'атмосфера__noise_jump':
            return "слышны скачки или склейки фонового шума"
        elif base_group_type == 'щелчки_слюна':
            # Тот же баг класса, что и с 'шипение' ниже — этой ветки не было
            # вообще, случай 2-3 маркеров проваливался в generic "присутствуют
            # проблемы" (см. _format_generalized_issue — там для 4+ такая же
            # логика уже есть).
            has_clicks = any(any(kw in self._issue_text(i).lower()
                                for kw in ['щелч', 'щёлк', 'клик', 'click', 'цокан', 'щелкающ'])
                            for i in items)
            has_saliva = any(any(kw in self._issue_text(i).lower()
                                for kw in ['слюна', 'слюн'])
                            for i in items)
            if has_clicks and has_saliva:
                return "присутствуют посторонние щёлкающие звуки и яркие звуки слюны"
            elif has_saliva:
                return "присутствуют яркие звуки слюны"
            else:
                return "присутствуют посторонние щёлкающие звуки"

        if base_group_type == 'несинхронность':
            if is_me:
                return "звуковые дорожки несинхронны с изображением"
            return "присутствуют реплики, которые выглядят несинхронно с изображением"
        elif base_group_type == 'отсутствие_звука':
            if is_me:
                if group_type == 'отсутствие_звука__background':
                    return "отсутствуют фоновые шумы"
                if group_type == 'отсутствие_звука__music':
                    return "отсутствует музыка"
                return "отсутствуют синхронные шумы"
            return "отсутствует звук"
        elif base_group_type == 'саунд_дизайн' and is_me:
            if group_type == 'саунд_дизайн__sync':
                return "синхронные шумы отличаются от оригинальной звуковой дорожки"
            if group_type == 'саунд_дизайн__background':
                return "фоновые шумы отличаются от оригинальной звуковой дорожки"
            if group_type == 'саунд_дизайн__music':
                return "музыка отличается от оригинальной звуковой дорожки"
            return "саунд-дизайн отличается от оригинальной звуковой дорожки"
        elif base_group_type == 'разборчивые_гуры' and is_me:
            return "присутствуют разборчивые гуры"
        elif base_group_type == 'реплики_в_гурах' and is_me:
            return "в гур-гуре присутствуют разборчивые реплики"
        elif base_group_type == 'отсутствие_гуров' and is_me:
            return "отсутствуют гуры"
        elif base_group_type == 'опциональный_трек' and is_me:
            return "эти элементы следует вынести в опциональный трек"
        elif base_group_type == 'маскировка':
            return "отсутствует маскировка нецензурной лексики"
        elif base_group_type == 'реверберация':
            return "изменяется реверберация на речи актёров"
        elif base_group_type == 'проблемы_реплик' and is_me:
            return "присутствуют реплики актёров"
        elif base_group_type == 'вздохи_тональные' and is_me:
            return "присутствуют вздохи и тональные реакции актёров"
        elif base_group_type == 'центр_канал':
            return "присутствует звуковой сигнал только в центральном канале"
        elif base_group_type == 'surround_missing':
            return "отсутствует звуковой сигнал в каналах Ls и Rs"
        elif group_type == 'шипение__s_sound':
            return "слышно яркое свистящее звучание звука «С»"
        elif group_type == 'шипение__whistle':
            return "слышен свистящий высокочастотный призвук"
        elif group_type == 'шипение__high_freq':
            return "слышен посторонний высокочастотный призвук"
        elif base_group_type == 'шипение':
            # Раньше этой ветки не было вообще (только под if is_me: ниже) —
            # для report_type="main" без конкретного подтипа код проваливался
            # в generic "присутствуют проблемы" (см. реальный баг: LLM-ответ
            # для этого пункта отклонён anchor-проверкой, а python-фолбэк
            # оказался пустым по содержанию).
            return "слышно постороннее шипение"
        # M&E-специфичные фразы для типов без отдельной обработки выше
        if is_me:
            if base_group_type == 'атмосфера':
                return "несоответствует звуковая атмосфера"
            elif base_group_type == 'задвоение_музыки':
                return "задваивается музыкальная дорожка"
            elif base_group_type == 'шумы':
                return "присутствуют посторонние шумы"
            elif base_group_type == 'треск':
                return "присутствуют посторонние трескающие звуки"
            elif base_group_type == 'шипение':
                return "слышно высокочастотное шипение"
            elif base_group_type == 'перегруз':
                return "ощущается перегруз"
            elif base_group_type == 'громкость':
                return "присутствуют проблемы с уровнем громкости"
            elif base_group_type == 'яркие_согласные':
                return "присутствует яркое звучание согласных"
            elif base_group_type == 'щелчки_слюна':
                return "присутствуют посторонние щёлкающие звуки"
            elif base_group_type == 'исправления':
                return "остались незакрытые замечания"
        return "присутствуют проблемы"
    
    def _format_generalized_issue(self, group_type: str, items: List[Issue], report_type: str = "main") -> str:
        """Форматирование обобщенной проблемы (2+ случаев)"""
        is_me = report_type in ("me", "me_ours")
        base_group_type, subtype = self._split_group_type(group_type)

        if group_type == 'атмосфера__noise_mixed':
            return "В некоторых фрагментах меняется звучание фонового шума"
        elif group_type == 'атмосфера__noise_up':
            return "В нескольких фрагментах усиливается фоновый шум"
        elif group_type == 'атмосфера__noise_down':
            return "В нескольких фрагментах ослабевает или пропадает слой фонового шума"
        elif group_type == 'атмосфера__noise_change':
            return "В нескольких фрагментах резко меняется звучание фонового шума"
        elif group_type == 'атмосфера__noise_jump':
            return "В нескольких фрагментах слышны скачки или склейки фонового шума"
        elif base_group_type == 'щелчки_слюна':
            # Проверяем, есть ли и щелчки, и слюна
            has_clicks = any(any(kw in self._issue_text(i).lower()
                                for kw in ['щелч', 'щёлк', 'клик', 'click', 'цокан', 'щелкающ'])
                            for i in items)
            has_saliva = any(any(kw in self._issue_text(i).lower()
                                for kw in ['слюна', 'слюн'])
                            for i in items)
            if has_clicks and has_saliva:
                return "В фонограмме присутствуют посторонние щёлкающие звуки и яркие звуки слюны"
            elif has_saliva:
                return "В фонограмме присутствуют яркие звуки слюны"
            else:
                return "В фонограмме присутствуют посторонние щёлкающие звуки"
        elif group_type == 'шипение__s_sound':
            return "На некоторых репликах слышно яркое свистящее звучание звука «С»"
        elif group_type == 'шипение__whistle':
            return "На некоторых репликах слышны свистящие высокочастотные призвуки"
        elif group_type == 'шипение__high_freq':
            return "На некоторых репликах слышны посторонние высокочастотные призвуки"
        elif base_group_type == 'шипение':
            return "В фонограмме присутствует высокочастотное шипение на репликах актёров"
        elif base_group_type == 'несинхронность':
            if is_me:
                return "В нескольких фрагментах звуковые дорожки несинхронны с изображением"
            return "В некоторых фрагментах реплики актёров выглядят несинхронно с изображением"
        elif base_group_type == 'перегруз':
            return "Некоторые реплики звучат пережато, ощущается перегруз"
        elif base_group_type == 'реверберация':
            return "В нескольких фрагментах изменяется реверберация на речи актёров на несоответствующую пространству в кадре"
        elif base_group_type == 'заставки':
            return "Отсутствует звуковое оформление заставок"
        elif base_group_type == 'отсутствие_звука':
            if is_me:
                if group_type == 'отсутствие_звука__background':
                    return "В нескольких фрагментах отсутствуют фоновые шумы"
                if group_type == 'отсутствие_звука__music':
                    return "В нескольких фрагментах отсутствует музыка"
                return "Не хватает некоторых синхронных шумов"
            return "В нескольких фрагментах отсутствует звук"
        elif base_group_type == 'саунд_дизайн':
            if group_type == 'саунд_дизайн__sync':
                return "В нескольких фрагментах синхронные шумы отличаются от оригинальной звуковой дорожки"
            if group_type == 'саунд_дизайн__background':
                return "В нескольких фрагментах фоновые шумы отличаются от оригинальной звуковой дорожки"
            if group_type == 'саунд_дизайн__music':
                return "В нескольких фрагментах музыка отличается от оригинальной звуковой дорожки"
            return "В нескольких фрагментах саунд-дизайн отличается от оригинальной звуковой дорожки"
        elif base_group_type == 'разборчивые_гуры':
            return "В нескольких фрагментах присутствуют разборчивые гуры"
        elif base_group_type == 'реплики_в_гурах':
            return "В нескольких фрагментах в гур-гуре присутствуют разборчивые реплики"
        elif base_group_type == 'отсутствие_гуров':
            return "В нескольких фрагментах отсутствуют гуры"
        elif base_group_type == 'опциональный_трек':
            return "В нескольких фрагментах элементы следует вынести в опциональный трек"
        elif base_group_type == 'треск':
            return "В фонограмме присутствуют посторонние трескающие звуки"
        elif base_group_type == 'шумы':
            # Уточняем обобщение по содержимому маркеров
            texts_lower = [self._issue_text(i).lower() for i in items]
            has_bg_noise = any(any(kw in t for kw in ['фоновый', 'белый шум', 'background noise',
                                                       'white noise']) for t in texts_lower)
            has_knock = any(any(kw in t for kw in ['стук', 'стучащ', 'постукива', 'knock',
                                                    'bang', 'thud']) for t in texts_lower)
            if has_bg_noise and not has_knock:
                return "На некоторых репликах присутствует постороннее шипение"
            elif has_knock and not has_bg_noise:
                return "В фонограмме присутствуют посторонние стучащие звуки"
            return "В фонограмме присутствуют посторонние шумы"
        elif base_group_type == 'исправления':
            return "Есть ряд маркеров, где были попытки исправления, однако проблемы остались"
        elif base_group_type == 'проблемы_реплик':
            if is_me:
                return "В нескольких фрагментах присутствуют реплики актёров"
            return "В нескольких фрагментах присутствуют проблемы с разборчивостью реплик"
        elif base_group_type == 'вздохи_тональные':
            return "В нескольких фрагментах присутствуют вздохи и тональные реакции актёров"
        elif base_group_type == 'центр_канал':
            return "В звуковой дорожке 5.1 есть фрагменты, в которых присутствует звуковой сигнал только в центральном канале"
        elif base_group_type == 'surround_missing':
            return "В звуковой дорожке 5.1 есть сцены, в которых отсутствует звуковой сигнал в каналах Ls и Rs"
        elif base_group_type == 'замена_текста':
            return "В нескольких фрагментах видна замена текста"
        elif base_group_type == 'громкость':
            return "В фонограмме присутствуют фрагменты со слишком громким звучанием отдельных элементов"
        elif base_group_type == 'яркие_согласные':
            return "В фонограмме присутствует яркое звучание согласных на репликах актёров"
        elif base_group_type == 'атмосфера':
            return "В нескольких фрагментах присутствуют проблемы со звуковой атмосферой"
        elif base_group_type == 'задвоение_музыки':
            return "В нескольких фрагментах в музыке слышится задвоение или параллельная посторонняя музыкальная дорожка"
        elif base_group_type == 'маскировка':
            return "В нескольких фрагментах отсутствует маскировка нецензурной лексики"
        else:
            # Fallback — очищаем от шаблонных оборотов
            return self._clean_marker_description(self._issue_text(items[0])).capitalize()
    
    # _merge_clicks_and_saliva удалён — щелчки и слюна теперь в одной группе 'щелчки_слюна'
    
    def generate_conclusion(self, issues: List[Issue], categories: dict, tech_info: dict = None) -> str:
        """
        Генерация заключения на основе списка проблем
        
        Args:
            issues: Список проблем из CSV
            categories: Категоризация проблем
            tech_info: Техническая информация (опционально)
            
        Returns:
            Текст заключения
        """
        if self.use_llm:
            try:
                return self._generate_with_llm(issues, categories, tech_info)
            except Exception as e:
                logger.error(f"Ошибка генерации через LLM: {e}")
                logger.info("Переключаемся на шаблонную генерацию")
                return self._generate_template_based(issues, categories, tech_info)
        else:
            return self._generate_template_based(issues, categories, tech_info)
    
    def _generate_with_llm(self, issues: List[Issue], categories: dict, tech_info: dict = None) -> str:
        """
        Генерация заключения с помощью LLM (Ollama)
        
        Args:
            issues: Список проблем
            categories: Категоризация
            tech_info: Техническая информация
            
        Returns:
            Сгенерированное заключение
        """
        try:
            # Подготавливаем контекст для LLM
            prompt = self._prepare_llm_prompt(issues, categories, tech_info)
            
            logger.info("Генерация заключения через Ollama...")
            conclusion = self._ollama_generate(prompt)
            
            logger.info("Заключение сгенерировано через LLM")
            
            return conclusion
            
        except Exception as e:
            logger.error(f"Ошибка Ollama: {e}")
            raise
    
    def _prepare_llm_prompt(self, issues: List[Issue], categories: dict, tech_info: dict = None) -> str:
        """
        Подготовка промпта для LLM
        
        Args:
            issues: Список проблем
            categories: Категоризация
            tech_info: Техническая информация
            
        Returns:
            Текст промпта
        """
        # Суммаризация проблем
        issue_summary = []
        
        for issue in issues[:10]:  # Первые 10 для примера
            issue_summary.append(f"- {issue.timecode_in}: {self._issue_text(issue)}")
        
        prompt = f"""Ты - эксперт по аудио контролю качества. На основе списка выявленных проблем составь краткое профессиональное заключение на русском языке.

СТАТИСТИКА:
- Всего проблем: {categories['total']}
- Блокеров (критических): {len(categories['blockers'])}
- Требуют исправления: {len(categories['fix_required'])}
- Требуют комментария: {len(categories['comment_required'])}

ПРИМЕРЫ ПРОБЛЕМ:
{chr(10).join(issue_summary)}

ТРЕБОВАНИЯ К ЗАКЛЮЧЕНИЮ:
1. Объем: 3-5 предложений
2. Стиль: профессиональный, формальный
3. Укажи общую оценку качества аудио
4. Укажи основные категории проблем
5. Дай рекомендации (исправить/принять к сведению)

ЗАКЛЮЧЕНИЕ:"""
        
        return prompt
    
    def _generate_template_based(self, issues: List[Issue], categories: dict, tech_info: dict = None) -> str:
        """
        Генерация заключения на основе шаблонов (fallback без LLM)
        
        Args:
            issues: Список проблем
            categories: Категоризация
            tech_info: Техническая информация
            
        Returns:
            Сгенерированное заключение
        """
        total = categories['total']
        blockers = len(categories['blockers'])
        fix_required = len(categories['fix_required'])
        comment_required = len(categories['comment_required'])
        
        # Определяем общую оценку
        if blockers > 0:
            quality_assessment = "выявлены критические проблемы, требующие обязательного исправления"
        elif fix_required > 10:
            quality_assessment = "выявлено значительное количество проблем, требующих исправления"
        elif fix_required > 0:
            quality_assessment = "выявлены проблемы, требующие исправления"
        else:
            quality_assessment = "качество в целом соответствует требованиям, выявлены незначительные замечания"
        
        # Категоризация проблем по типам
        problem_types = self._analyze_problem_types(issues)
        
        # Формируем заключение
        conclusion_parts = []
        
        # Вступление
        conclusion_parts.append(
            f"В результате контроля качества аудиодорожек было обнаружено {total} замечаний."
        )
        
        # Общая оценка
        conclusion_parts.append(
            f"По результатам проверки {quality_assessment}."
        )
        
        # Детали по категориям
        if blockers > 0:
            conclusion_parts.append(
                f"Обнаружено {blockers} критических дефектов (блокеров), препятствующих выпуску материала."
            )
        
        if fix_required > 0:
            conclusion_parts.append(
                f"Выявлено {fix_required} замечаний, требующих исправления."
            )
        
        if comment_required > 0:
            conclusion_parts.append(
                f"Зафиксировано {comment_required} замечаний, требующих комментария или принятия решения."
            )
        
        # Основные типы проблем
        if problem_types:
            types_str = ", ".join([f"{ptype} ({count})" for ptype, count in list(problem_types.items())[:3]])
            conclusion_parts.append(
                f"Основные выявленные проблемы: {types_str}."
            )
        
        # Рекомендации
        if blockers > 0:
            conclusion_parts.append(
                "Рекомендуется устранить все критические дефекты перед выпуском материала."
            )
        elif fix_required > 5:
            conclusion_parts.append(
                "Рекомендуется исправить выявленные замечания для повышения качества материала."
            )
        else:
            conclusion_parts.append(
                "Материал может быть принят после рассмотрения и согласования выявленных замечаний."
            )
        
        conclusion = " ".join(conclusion_parts)
        
        logger.info("Заключение сгенерировано по шаблону")
        
        return conclusion
    
    def _analyze_problem_types(self, issues: List[Issue]) -> dict:
        """
        Анализ типов проблем
        
        Args:
            issues: Список проблем
            
        Returns:
            Словарь с подсчетом типов проблем
        """
        types = {}
        
        keywords = {
            'клик': 'щёлкающий',
            'шипение': 'шипение',
            'перегруз': 'перегруз',
            'треск': 'треск',
            'синхронизация': 'несинхронно',
            'отсутствие звука': 'отсутствует звук',
            'каналы': 'перепутана последовательность каналов',
            'склейка': 'склейка'
        }
        
        for issue in issues:
            desc_lower = self._issue_text(issue).lower()
            for key, keyword in keywords.items():
                if keyword in desc_lower:
                    types[key] = types.get(key, 0) + 1
        
        # Сортируем по количеству
        sorted_types = dict(sorted(types.items(), key=lambda x: x[1], reverse=True))
        
        return sorted_types


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    from src.csv_importer import CSVImporter
    
    # Импортируем проблемы
    importer = CSVImporter()
    issues = importer.import_issues('/Users/vladog/Desktop/ШАБЛОН/petr_2_s1_e2_2024_09_12_rus.csv')
    categories = importer.categorize_issues(issues)
    
    # Генерируем заключение
    generator = ConclusionGenerator(use_llm=False)  # Без LLM для теста
    conclusion = generator.generate_conclusion(issues, categories)
    
    print("\n=== ЗАКЛЮЧЕНИЕ ===\n")
    print(conclusion)
    print("\n" + "="*50 + "\n")
