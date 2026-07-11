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
from src.marker_translation_service import MarkerTranslationService
from src.ollama_service import OllamaService
from src.technical_info_extractor import format_fps

logger = logging.getLogger(__name__)

STYLE_EXAMPLES_PATH = Path(__file__).resolve().with_name("manual_conclusion_style_examples.json")


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
        self.marker_translation_service = MarkerTranslationService(self.config, self.ollama_service)
        self.llm_model = self.ollama_service.model
        self.llm_temperature = self.ollama_service.temperature
        self.llm_max_tokens = self.ollama_service.max_tokens
        self.llm_timeout = self.ollama_service.timeout
        self.ollama_host = self.ollama_service.host
        logger.info(f"ConclusionGenerator инициализирован (LLM: {use_llm})")

    def check_ollama_status(self) -> bool:
        """Проверка доступности Ollama через настроенный host."""
        return self.ollama_service.check_status()

    def get_ollama_status(self) -> dict:
        """Детальный статус: доступен ли Ollama и установлена ли нужная модель."""
        return self.ollama_service.get_status()

    def _ollama_generate(self, prompt: str, *, model: Optional[str] = None, options: Optional[dict] = None) -> str:
        """Единая обёртка над вызовом Ollama."""
        return self.ollama_service.generate(
            prompt,
            model=model or self.llm_model,
            options=options or {},
        )

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
        
        for pdf_key in ['pdf_20_c', 'pdf_20_uc', 'pdf_20', 'pdf_51_c', 'pdf_51_uc', 'pdf_51']:
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

        for audio_key in ['audio_51_c', 'audio_51_uc', 'audio_20_c', 'audio_20_uc']:
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
        
        for key in ['audio_20_c', 'audio_51_c', 'audio_20_uc', 'audio_51_uc', 'video']:
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
        if self.use_llm and expected_items >= 3:
            try:
                llm_conclusion = self._write_conclusion_with_llm(blockers, groups, report_type)
                if llm_conclusion and self._validate_polished(python_conclusion, llm_conclusion, report_type):
                    logger.info("✅ Заключение написано через AI")
                    return llm_conclusion
                else:
                    logger.warning("AI-заключение не прошло валидацию, используем Python-версию")
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

    def _prepare_marker_data_for_llm(self, blockers: List[Issue], groups: dict, report_type: str = "main") -> str:
        """
        Подготавливает структурированные данные маркеров для LLM.
        Передаёт ПОЛНЫЕ описания, чтобы LLM могла написать осмысленное саммари.
        """
        lines = []

        # Блокеры
        if blockers:
            lines.append("БЛОКЕРЫ (критические проблемы):")
            for b in blockers:
                marker_text = self._issue_text(b)
                is_title = self._is_title_card_issue(marker_text)
                note = " [без TC в заключении]" if self._should_omit_timecode_in_conclusion(
                    b, self._classify_single_issue(b, report_type), report_type
                ) else ""
                lines.append(f"  {b.timecode_in} | {marker_text}" + (" [заставка]" if is_title else "") + note)
            lines.append("")

        # Сгруппированные обычные проблемы
        if groups:
            lines.append("ОБЫЧНЫЕ ПРОБЛЕМЫ (по группам):")
            for group_type, items in groups.items():
                group_label = self._contextual_group_label(group_type)

                lines.append(f"  [{group_label}] — {len(items)} маркер(ов):")
                for item in items:
                    marker_text = self._issue_text(item)
                    note = " [без TC в заключении]" if self._should_omit_timecode_in_conclusion(
                        item, group_type, report_type
                    ) else ""
                    lines.append(f"    {item.timecode_in} | {marker_text}{note}")
                lines.append("")

        return '\n'.join(lines)

    def _write_conclusion_with_llm(self, blockers: List[Issue], groups: dict, report_type: str = "main") -> str:
        """
        LLM пишет заключение-саммари на основе полных данных маркеров.
        Python контролирует: данные, валидацию, формат.
        LLM контролирует: формулировки, обобщение, стиль.
        """
        is_me = report_type in ("me", "me_ours")
        marker_data = self._prepare_marker_data_for_llm(blockers, groups, report_type)
        manual_examples_block = self._build_manual_style_examples_block("me" if is_me else "main")

        # Считаем ожидаемое количество пунктов для подсказки LLM
        if is_me:
            # В M&E: regular + blockers объединяются по типам → каждый тип = 1 пункт
            all_type_chunks = []
            for group_type, items in groups.items():
                if group_type == 'другие_проблемы':
                    all_type_chunks.extend(f'__unique_{i}' for i in range(len(items)))
                    continue
                all_type_chunks.extend(
                    f"{group_type}__chunk_{idx}"
                    for idx, _chunk in enumerate(self._build_me_issue_chunks(group_type, items))
                )
            for blocker in blockers:
                gt = self._classify_single_issue(blocker, report_type)
                all_type_chunks.append(gt if gt != 'другие_проблемы' else f'__blocker_{blocker.timecode_in}')
            expected_items = len(set(all_type_chunks))
        else:
            expected_items = len(blockers)
            for group_type, items in groups.items():
                if group_type == 'другие_проблемы':
                    expected_items += len(items)
                else:
                    expected_items += 1

        if is_me:
            prompt = f"""Ты — эксперт по контролю качества аудио для кинопроизводства. Напиши заключение-саммари для M&E (Music & Effects) дорожки строго на основе предоставленных данных маркер-листа.

КОНТЕКСТ M&E:
M&E — это дорожка без диалогов. Содержит только музыку, синхронные шумы (шаги, звуки действий) и звуковую атмосферу. Голос актёров в M&E — это проблема (блокер), а не норма.

ДАННЫЕ МАРКЕР-ЛИСТА:
{marker_data}

{manual_examples_block}

ЗАДАЧА: Напиши заключение из ~{expected_items} пунктов. Каждый пункт — саммари одной проблемы или группы проблем.
ВАЖНО: Пиши ТОЛЬКО о проблемах из данных. НЕ выдумывай проблем, которых нет в маркер-листе.
ОСОБО ВАЖНО: По стилю ориентируйся на ручные примеры из docx. Сохраняй конкретику, не подменяй её общими словами и обобщай только тогда, когда проблема действительно системная.

ФОРМАТ:
Верни СТРОГО JSON-объект без markdown-обёртки и без пояснений.
Схема:
{{
  "title": "По субъективной оценке выявлены следующие недочёты:",
  "items": [
    {{
      "kind": "blocker|specific|general",
      "timecodes": ["HH:MM:SS:FF"],
      "omit_timecode": false,
      "text": "текст пункта БЕЗ префикса На таймкоде/На таймкодах и без точки в конце"
    }}
  ]
}}

ПОРЯДОК ПУНКТОВ (СТРОГО!):
1. СНАЧАЛА — блокеры (реплики актёров, вздохи, тональные реакции)
2. ПОТОМ — обычные частные проблемы (шумы, атмосфера, музыка и т.д.) с таймкодами
3. ПОСЛЕДНИМИ — обобщённые проблемы без таймкодов

ОТДЕЛЬНОЕ ПРАВИЛО ДЛЯ M&E-ГОЛОСОВЫХ ГРУПП:
- НЕ смешивай в один пункт разные типы голосового материала
- "реплики актёров", "разборчивые гуры", "разборчивые реплики в гурах", "отсутствие гуров" и "материал для optional track" — это РАЗНЫЕ группы
- Если в маркере сказано вынести материал в отдельный или optional track, не переформулируй это как обычные "реплики актёров"
- Голоса из объявлений, радио, ТВ, переводов, громкоговорителей и подобных источников описывай как материал для optional track, если это следует из маркера
- НЕ смешивай проблемы саунд-дизайна и отличий от оригинальной дорожки с фоновыми шумами, синхронными шумами и музыкой

ОТДЕЛЬНОЕ ПРАВИЛО ДЛЯ M&E-ПОДТИПОВ ПРОБЛЕМ:
- Различай 6 смысловых доменов и не склеивай их:
  1. отсутствуют синхронные шумы
  2. отсутствуют фоновые шумы
  3. отсутствует музыка
  4. синхронные шумы отличаются от оригинальной звуковой дорожки
  5. фоновые шумы отличаются от оригинальной звуковой дорожки
  6. музыка отличается от оригинальной звуковой дорожки
- Если в маркере сказано "не хватает", "отсутствует", "пропали" — это группа отсутствия, а НЕ "отличается от оригинала"
- Если в маркере сказано "отличается", "ярче", "тише", "в мастере звучит иначе", "другая реверберация", "по звучанию не совпадает" — это группа отличия от оригинала, а НЕ отсутствия
- Если маркер про шаги, рукопожатие, движение рук, удары, взаимодействие с предметами, одежду, двери, бумагу и другие действия в кадре — это синхронные шумы
- Если маркер про атмосферу помещения, шум улицы, шум салона, фон, room tone — это фоновые шумы
- Если маркер про музыку, песню, музыкальные инструменты, фоновую музыку — это музыка

ПРАВИЛА ПО КОЛИЧЕСТВУ МАРКЕРОВ ОДНОГО ТИПА:
- 1 маркер → с таймкодом, ИСПОЛЬЗУЙ реальное описание из маркера (не шаблон): "На таймкоде TC описание"
- 2 маркера → оба таймкода + реальное описание: "На таймкодах TC1 и TC2 описание"
- 3+ маркеров → обобщи БЕЗ таймкодов, но КОНКРЕТНО: "В нескольких фрагментах описание"
- Если маркер в данных помечен как "[без TC в заключении]" — НЕ пиши его таймкод, даже если он один
- В поле "text" НЕ дублируй "На таймкоде", "На таймкодах", буллеты, заголовок и двоеточия после TC

ДЕТАЛИЗАЦИЯ (КРИТИЧНО):
- Для 1-2 маркеров: ПЕРЕДАЙ СУТЬ из реального описания маркера — конкретный звук, тип шума, что именно происходит
- Не заменяй конкретику шаблоном: вместо "посторонние шумы" пиши "гул кондиционера" или "звук улицы"
- Для 3+ маркеров: укажи тип проблемы достаточно конкретно, чтобы читатель понял без прослушивания
- Не объединяй в один пункт разнонаправленные изменения одной среды: усиление, ослабление и изменение звучания фона описывай раздельно

ПРАВИЛА ДЛЯ БЛОКЕРОВ (реплики/вздохи актёров):
- 1 блокер → с таймкодом: "На таймкоде TC присутствует реплика актёра..."
- 2+ блокеров одного типа → обобщи без таймкодов: "В нескольких фрагментах присутствуют реплики актёров"
- НЕ смешивай реплики и вздохи в одном пункте если их несколько

ЗАПРЕЩЁННЫЕ КОНСТРУКЦИИ:
- "на данном таймкоде", "в данном фрагменте", "здесь" — пустые слова, убирай
- "возможно", "вероятно", "предположительно" — пиши утвердительно
- "ощущение, что", "создаётся впечатление" — пиши прямо
- Точки в конце пунктов
- Выводы, рекомендации, оценочные суждения

ПРИМЕРЫ ХОРОШИХ ФОРМУЛИРОВОК ДЛЯ M&E:
ПЛОХО: "-    На таймкоде 00:15:22:10 присутствуют посторонние шумы"
ХОРОШО: "-    На таймкоде 00:15:22:10 слышен гул кондиционера в паузах между музыкой"

ПЛОХО: "-    В нескольких фрагментах отсутствуют синхронные шумы"
ХОРОШО: "-    В нескольких фрагментах отсутствуют синхронные шумы шагов и движений"

ПЛОХО: "-    В фонограмме присутствуют проблемы с атмосферой"
ХОРОШО: "-    На таймкодах 00:23:15:00 и 00:45:30:12 звуковая атмосфера улицы обрывается на склейке"

ПЛОХО: "-    В нескольких фрагментах проблемы с саунд-дизайном"
ХОРОШО: "-    В нескольких фрагментах синхронные шумы отличаются от оригинальной звуковой дорожки"

ПЛОХО: "-    В нескольких фрагментах отсутствует звук"
ХОРОШО: "-    В нескольких фрагментах отсутствуют фоновые шумы"

ПЛОХО: "-    На таймкоде 01:02:13:11 отсутствуют синхронные шумы"
ХОРОШО: "-    На таймкоде 01:02:13:11 звуки ударов пальцами по столу отличаются от оригинальной звуковой дорожки"

ПЛОХО: "-    На таймкоде 01:10:00:00 отдельная проблема с рукопожатием"
ХОРОШО: "-    На таймкоде 01:10:00:00 отсутствует звук рукопожатия"

ПРИМЕР ЭТАЛОННОГО M&E ЗАКЛЮЧЕНИЯ:
-    На таймкодах 00:05:11:00 и 00:23:44:08 отсутствуют синхронные шумы шагов
-    На таймкоде 00:15:22:10 слышен гул кондиционера в паузах между музыкой
-    В нескольких фрагментах звуковая атмосфера не соответствует пространству в кадре
-    На таймкоде 00:42:18:05 музыкальная тема задваивается — слышна дублирующая дорожка
-    В нескольких фрагментах присутствуют реплики актёров

Напиши ТОЛЬКО заключение:"""
        else:
            prompt = f"""Ты — эксперт по контролю качества аудио для кинопроизводства. Напиши заключение-саммари строго на основе предоставленных данных маркер-листа.

ДАННЫЕ МАРКЕР-ЛИСТА:
{marker_data}

{manual_examples_block}

ЗАДАЧА: Напиши заключение СТРОГО из {expected_items} пунктов. Каждый пункт — саммари одной проблемы или группы проблем из данных выше.
ВАЖНО: Пиши ТОЛЬКО о проблемах из данных. НЕ выдумывай проблем, которых нет в маркер-листе.
ОСОБО ВАЖНО: По стилю ориентируйся на ручные примеры из docx. Если маркер конкретный — сохраняй его лексику и смысл, а не заменяй абстрактным пересказом.

ФОРМАТ:
Верни СТРОГО JSON-объект без markdown-обёртки и без пояснений.
Схема:
{{
  "title": "По субъективной оценке выявлены следующие недочёты:",
  "items": [
    {{
      "kind": "blocker|specific|general",
      "timecodes": ["HH:MM:SS:FF"],
      "omit_timecode": false,
      "text": "текст пункта БЕЗ префикса На таймкоде/На таймкодах и без точки в конце"
    }}
  ]
}}

ПОРЯДОК ПУНКТОВ (СТРОГО!):
1. СНАЧАЛА — блокеры, КАЖДЫЙ ОТДЕЛЬНЫМ пунктом с таймкодом: "На таймкоде TC описание". НЕ объединяй блокеры!
2. ПОТОМ — частные проблемы С таймкодами (единичные маркеры, группы из 2-3 с перечислением TC)
3. ПОСЛЕДНИМИ — обобщённые проблемы БЕЗ таймкодов (группы из 4+ маркеров, заставки)

ПРАВИЛА:
- Блокеры про заставки — БЕЗ таймкода
- Любой маркер, помеченный в данных как "[без TC в заключении]" — описывай БЕЗ таймкода
- Группа из 1 маркера — с таймкодом, передай полный смысл маркера
- Группа из 2-3 маркеров (несинхронность, реверберация, отсутствие звука) — перечисли таймкоды: "На таймкодах TC1, TC2 и TC3 ..."
- Группа из 4+ маркеров — обобщи одной фразой БЕЗ таймкодов
- Прочее — каждый маркер отдельным пунктом с таймкодом
- В поле "text" НЕ пиши "На таймкоде", "На таймкодах", буллеты и заголовок
- НЕ добавляй выводов, рекомендаций, точек в конце пунктов

ДЕТАЛИЗАЦИЯ (КРИТИЧНО — НЕ ТЕРЯТЬ ДЕТАЛИ):
- Для блокеров и единичных проблем ОБЯЗАТЕЛЬНО сохраняй ВСЕ важные детали: конкретные слова (например "Бля"), величины смещения (например "~8 кадров"), названия эффектов, имена персонажей, конкретные звуки
- Единичный маркер = полный пересказ его сути. НЕ обобщай единичные маркеры!
- Для обобщённых групп (4+ маркеров) — кратко, но точно опиши тип проблемы

ЗАПРЕЩЁННЫЕ КОНСТРУКЦИИ (НЕ ИСПОЛЬЗУЙ):
- "на данном таймкоде" — таймкод уже указан в начале пункта, НЕ дублируй
- "в данном фрагменте", "в данном месте", "здесь" — это пустые слова, убирай
- "возможно", "вероятно", "предположительно", "скорее всего" — пиши утвердительно
- "ощущение, что", "создаётся впечатление", "складывается впечатление" — пиши прямо
- "по субъективным ощущениям" — это и так субъективная оценка
- НЕ повторяй одно и то же слово в одном пункте
Вместо: "на данном таймкоде возможно присутствует незамаскированное слово"
Пиши: "присутствует незамаскированное слово"

ПРИМЕРЫ ПЛОХИХ И ХОРОШИХ ФОРМУЛИРОВОК:
ПЛОХО (дублирование, «возможно»): "-    На таймкоде 01:15:45:23 на данном таймкоде возможно присутствует незамаскированное слово «Бля»"
ХОРОШО (чисто, утвердительно): "-    На таймкоде 01:15:45:23 присутствует незамаскированное слово «Бля», звучащее слитно со словом «Пугачёвы»"

ПЛОХО (пустой оборот): "-    На таймкоде 01:00:49:06 в данном фрагменте видео не синхронно"
ХОРОШО: "-    На таймкоде 01:00:49:06 видеофайл не синхронен со звуковыми дорожками (смещение ~8 кадров)"

ПЛОХО (потеряны детали): "-    На таймкоде 01:12:29:05 проблема с фоновым шумом"
ХОРОШО: "-    На таймкоде 01:12:29:05 слышна склейка фонового шума моря"

ПРИМЕР ЭТАЛОННОГО ЗАКЛЮЧЕНИЯ (порядок: блокеры → частные → общие):
-    На таймкоде 01:00:49:06 видеофайл не синхронен со звуковыми дорожками (смещение ~8 кадров)
-    На таймкоде 01:01:45:13 отсутствует маскировка нецензурной лексики (слово «Бля»)
-    На таймкодах 01:11:42:06 и 01:11:44:22 реплики несинхронны с изображением
-    На таймкоде 01:12:29:05 слышна склейка фонового шума моря
-    В фонограмме присутствуют посторонние щёлкающие звуки и яркие звуки слюны
-    В нескольких фрагментах изменяется реверберация на речи актёров — вместо реверберации автомобиля звучит реверберация крупного помещения

Напиши ТОЛЬКО заключение:"""

        logger.info(
            f"Генерация заключения через {self.llm_model} "
            f"(ожидаем ~{expected_items} пунктов, тип: {report_type}, host: {self.ollama_host})..."
        )

        raw_result = self._ollama_generate(
            prompt,
            options={
                'temperature': self.llm_temperature,
                'num_predict': self.llm_max_tokens,
                'top_p': 0.9,
            }
        )
        structured = self._format_structured_llm_output(raw_result, blockers, groups, report_type)
        if structured:
            return structured
        return self._clean_llm_output(raw_result)

    @staticmethod
    def _extract_json_object(text: str) -> Optional[str]:
        """Пытается извлечь JSON-объект из сырого ответа LLM."""
        if not text:
            return None

        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        return stripped[start:end + 1]

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

    def _build_structured_contract_item(self, kind: str, line: str) -> dict:
        """Строит ожидаемый structured item по итоговой строке Python-генерации."""
        clean_line = line.strip()
        return {
            "kind": kind,
            "timecodes": self._extract_timecodes_from_text(clean_line),
            "omit_timecode": not clean_line.startswith("На таймкод"),
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
                    ))

                items = regular_items
                if not items:
                    continue

                if group_type == 'другие_проблемы':
                    for item in items:
                        line = f"На таймкоде {item.timecode_in} {self._summarize_description(self._issue_text(item))}"
                        if group_type in blocker_types:
                            blocker_items.append(self._build_structured_contract_item("blocker", line))
                        else:
                            specific_items.append(self._build_structured_contract_item("specific", line))
                    continue

                chunks = self._build_me_issue_chunks(group_type, items)
                for chunk in chunks:
                    chunk_items = chunk['items']
                    line = self._format_me_issue_line(group_type, chunk_items, report_type)
                    if group_type in blocker_types:
                        blocker_items.append(self._build_structured_contract_item("blocker", line))
                    elif chunk['force_specific'] or len(chunk_items) <= 2:
                        specific_items.append(self._build_structured_contract_item("specific", line))
                    elif group_type == 'заставки' or len(chunk_items) >= 3:
                        general_items.append(self._build_structured_contract_item("general", line))
                    else:
                        specific_items.append(self._build_structured_contract_item("specific", line))

            return blocker_items + specific_items + general_items

        for group_type, items in groups.items():
            no_tc_items, regular_items = self._split_general_timeline_items(group_type, items, report_type)
            for item in no_tc_items:
                line = self._format_issue_without_timecode(item)
                general_items.append(self._build_structured_contract_item("general", line))

            items = regular_items
            if not items:
                continue

            count = len(items)
            if group_type == 'заставки':
                line = self._format_generalized_issue(group_type, items, report_type)
                general_items.append(self._build_structured_contract_item("general", line))
                continue

            if group_type == 'другие_проблемы':
                for item in items:
                    line = f"На таймкоде {item.timecode_in} {self._summarize_description(self._issue_text(item))}"
                    specific_items.append(self._build_structured_contract_item("specific", line))
                continue

            if count == 1:
                if self._should_omit_timecode_in_conclusion(items[0], group_type, report_type):
                    line = self._format_issue_without_timecode(items[0])
                    general_items.append(self._build_structured_contract_item("general", line))
                else:
                    line = f"На таймкоде {items[0].timecode_in} {self._summarize_description(self._issue_text(items[0]))}"
                    specific_items.append(self._build_structured_contract_item("specific", line))
            elif count in [2, 3] and self._is_important_type(group_type, report_type):
                timecodes = [item.timecode_in for item in items]
                if count == 2:
                    tc_text = f"{timecodes[0]} и {timecodes[1]}"
                else:
                    tc_text = f"{timecodes[0]}, {timecodes[1]} и {timecodes[2]}"
                line = f"На таймкодах {tc_text} {self._format_multiple_issue(group_type, items, report_type)}"
                specific_items.append(self._build_structured_contract_item("specific", line))
            else:
                line = self._format_generalized_issue(group_type, items, report_type)
                general_items.append(self._build_structured_contract_item("general", line))

        for blocker in blockers:
            group_type = self._classify_single_issue(blocker, report_type)
            if self._should_omit_timecode_in_conclusion(blocker, group_type, report_type):
                line = self._format_issue_without_timecode(blocker, use_blocker_summary=True)
            else:
                line = f"На таймкоде {blocker.timecode_in} {self._summarize_blocker(self._issue_text(blocker))}"
            blocker_items.append(self._build_structured_contract_item("blocker", line))

        return blocker_items + specific_items + general_items

    def _validate_structured_llm_payload(
        self,
        payload: dict,
        blockers: Optional[List[Issue]] = None,
        groups: Optional[dict] = None,
        report_type: str = "main",
    ) -> bool:
        """Проверяет structured JSON от LLM до сборки финального текста."""
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            logger.warning("Structured-валидация: отсутствует непустой список items")
            return False

        valid_kinds = {"blocker", "specific", "general"}
        kind_order = {"blocker": 0, "specific": 1, "general": 2}
        previous_kind_rank = -1

        expected_items = None
        if blockers is not None and groups is not None:
            expected_items = self._build_expected_structured_items(blockers, groups, report_type)
            if len(items) != len(expected_items):
                logger.warning(
                    "Structured-валидация: кол-во items отличается "
                    f"(Python: {len(expected_items)}, AI: {len(items)})"
                )
                return False

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                logger.warning(f"Structured-валидация: item #{index + 1} не является объектом")
                return False

            kind = str(item.get("kind", "")).strip().lower()
            if kind not in valid_kinds:
                logger.warning(f"Structured-валидация: item #{index + 1} содержит неизвестный kind '{kind}'")
                return False
            if kind_order[kind] < previous_kind_rank:
                logger.warning(f"Structured-валидация: item #{index + 1} нарушает порядок kind")
                return False
            previous_kind_rank = kind_order[kind]

            omit_timecode = item.get("omit_timecode")
            if not isinstance(omit_timecode, bool):
                logger.warning(f"Structured-валидация: item #{index + 1} содержит не-bool omit_timecode")
                return False

            timecodes = item.get("timecodes")
            if not isinstance(timecodes, list):
                logger.warning(f"Structured-валидация: item #{index + 1} содержит не-list timecodes")
                return False
            cleaned_timecodes = [str(tc).strip() for tc in timecodes if str(tc).strip()]

            text = str(item.get("text", "")).strip()
            if not text:
                logger.warning(f"Structured-валидация: item #{index + 1} не содержит текста")
                return False

            if omit_timecode and cleaned_timecodes:
                logger.warning(f"Structured-валидация: item #{index + 1} не должен содержать таймкоды")
                return False

            if expected_items is None:
                continue

            expected_item = expected_items[index]
            if kind != expected_item["kind"]:
                logger.warning(
                    f"Structured-валидация: item #{index + 1} имеет kind '{kind}', "
                    f"ожидался '{expected_item['kind']}'"
                )
                return False

            if omit_timecode != expected_item["omit_timecode"]:
                logger.warning(
                    f"Structured-валидация: item #{index + 1} имеет omit_timecode={omit_timecode}, "
                    f"ожидалось {expected_item['omit_timecode']}"
                )
                return False

            if cleaned_timecodes != expected_item["timecodes"]:
                logger.warning(
                    f"Structured-валидация: item #{index + 1} содержит неверные timecodes "
                    f"(AI: {cleaned_timecodes}, Python: {expected_item['timecodes']})"
                )
                return False

        return True

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

    def _format_structured_llm_output(
        self,
        text: str,
        blockers: Optional[List[Issue]] = None,
        groups: Optional[dict] = None,
        report_type: str = "main",
    ) -> Optional[str]:
        """
        Основной путь постобработки AI-ответа:
        ожидаем JSON-структуру и собираем финальное заключение Python'ом.
        """
        json_text = self._extract_json_object(text)
        if not json_text:
            return None

        try:
            payload = json.loads(json_text)
        except Exception as exc:
            logger.warning(f"Не удалось распарсить JSON-ответ LLM: {exc}")
            return None

        if not isinstance(payload, dict):
            return None

        if not self._validate_structured_llm_payload(payload, blockers, groups, report_type):
            return None

        title = str(payload.get("title") or "По субъективной оценке выявлены следующие недочёты:").strip()
        items = payload.get("items")

        formatted_items = []
        for item in items:
            line = self._format_structured_llm_item(item)
            if line:
                formatted_items.append(f"-    {line}")

        if not formatted_items:
            return None

        if not title.startswith("По субъективной оценке"):
            title = "По субъективной оценке выявлены следующие недочёты:"

        return title + "\n\n" + "\n".join(formatted_items)

    def _clean_llm_output(self, text: str) -> str:
        """Очистка вывода LLM: нормализация формата, удаление мусора и механических оборотов."""
        import re

        # Убираем "TC:" которые LLM может копировать из данных
        text = re.sub(r'(?:TC:\s*)', '', text)
        # Убираем двоеточие после таймкода (На таймкоде HH:MM:SS:FF: -> без двоеточия)
        text = re.sub(r'(На таймкод[еа]х?\s+[\d:,\s и]+\d{2}:\d{2}:\d{2}:\d{2}):\s*', r'\1 ', text)
        # Убираем точки в конце пунктов
        text = re.sub(r'\.\s*$', '', text, flags=re.MULTILINE)

        lines = text.split('\n')
        result_lines = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if result_lines and result_lines[-1].startswith("По субъективной оценке"):
                    result_lines.append("")
                continue

            if stripped.startswith("По субъективной оценке"):
                result_lines.append(stripped)
                continue

            if stripped.startswith('-'):
                content = stripped.lstrip('-').strip()

                # Очищаем содержимое пункта от механических оборотов
                # Разделяем "На таймкоде TC" от описания и чистим описание
                tc_match = re.match(
                    r'(На таймкод[еа]х?\s+[\d:,\s и]+\d{2}:\d{2}:\d{2}:\d{2})\s+(.*)',
                    content
                )
                if tc_match:
                    tc_part = tc_match.group(1)
                    desc_part = tc_match.group(2)
                    desc_part = self._clean_marker_description(desc_part)
                    content = f"{tc_part} {desc_part}"
                else:
                    # Пункт без реального таймкода — полная очистка через общий метод
                    content = self._clean_marker_description(content)

                result_lines.append(f"-    {content}")
                continue

            # Продолжение предыдущей строки
            if result_lines and result_lines[-1].startswith("-    "):
                result_lines[-1] += " " + stripped

        if not result_lines or not result_lines[0].startswith("По субъективной оценке"):
            result_lines.insert(0, "")
            result_lines.insert(0, "По субъективной оценке выявлены следующие недочёты:")

        return '\n'.join(result_lines)

    def _validate_polished(self, original: str, polished: str, report_type: str = "main") -> bool:
        """
        Валидация AI-заключения: структура должна быть корректной.
        """
        import re

        is_me = report_type in ("me", "me_ours")

        def count_items(text):
            return len([l for l in text.split('\n') if l.strip().startswith('-')])

        def extract_all_timecodes(text):
            return set(re.findall(r'\d{2}:\d{2}:\d{2}:\d{2}', text))

        def extract_blocker_timecodes(text):
            return set(re.findall(r'На таймкод[ае]х?\s+(\d{2}:\d{2}:\d{2}:\d{2})', text))

        orig_items = count_items(original)
        pol_items = count_items(polished)

        # AI не должен менять количество смысловых пунктов:
        # это приводит к ложным обобщениям или, наоборот, к повторному дроблению группы.
        if orig_items != pol_items:
            logger.warning(f"Валидация: кол-во пунктов отличается (Python: {orig_items}, AI: {pol_items})")
            return False

        pol_all_tc = extract_all_timecodes(polished)

        # Все таймкоды, которые Python-черновик решил показать явно,
        # должны остаться и в AI-версии.
        orig_required_tc = extract_all_timecodes(original)
        missing_required_tc = orig_required_tc - pol_all_tc
        if missing_required_tc:
            logger.warning(f"Валидация: пропущены обязательные TC: {missing_required_tc}")
            return False

        # Дополнительная проверка блокеров для основного отчёта.
        # В M&E блокеры могут быть обобщены, поэтому отдельную проверку не усиливаем.
        if not is_me:
            orig_blocker_tc = extract_blocker_timecodes(original)
            missing_blocker_tc = orig_blocker_tc - pol_all_tc
            if missing_blocker_tc:
                logger.warning(f"Валидация: пропущены TC блокеров: {missing_blocker_tc}")
                return False

        # Стартовый таймкод в заключении не показываем:
        # это служебный маркер для общих проблем всей фонограммы/видео.
        if re.search(r'На таймкод[еа]х?\s+.*(?:00:00:00:00|01:00:00:00)', polished):
            logger.warning("Валидация: в AI-заключении появился стартовый таймкод")
            return False

        # Заголовок должен быть
        if "По субъективной оценке" not in polished:
            logger.warning("Валидация: отсутствует заголовок")
            return False

        # Не должно быть выводов/рекомендаций
        lower = polished.lower()
        if any(kw in lower for kw in ['рекоменд', 'в целом', 'итого', 'вывод', 'резюм']):
            logger.warning("Валидация: обнаружены выводы/рекомендации")
            return False

        logger.info(f"Валидация пройдена ({pol_items} пунктов, {len(pol_all_tc)} TC, тип: {report_type})")
        return True

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
        important = ['несинхронность', 'отсутствие_звука', 'маскировка', 'реверберация']
        if report_type in ("me", "me_ours"):
            important += [
                'саунд_дизайн',
                'проблемы_реплик',
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
