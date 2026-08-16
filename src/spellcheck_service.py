"""
Spellcheck Service

Проверка орфографии и автоисправление опечаток в тексте маркер-листов
(колонки Description / Comments) на русском и английском языках.

Основной путь — LLM (см. correct_texts_batch/_correct_texts_llm): один
batch-запрос на весь маркер-лист через выбранный в UI провайдер
(Ollama/Groq/YandexGPT/GigaChat, тот же generate_fn, что и у
ConclusionGenerator/MarkerTranslationService), с собственным промптом,
заточенным под правки орфографии/пунктуации без изменения структуры и
смысла. Возвращает точные пары (было, стало) — CSV/таймкоды/теги LLM не
видит вообще, в него уходит только текст полей Description/Comments.

Без generate_fn (например, отключено в настройках) или при сбое LLM —
локальный алгоритмический фолбэк, полностью автономный (без сети):

Русский язык проверяется через морфологический анализ (pymorphy3) —
обычные словари типа hunspell/pyspellchecker слишком малы для русского
и дают много ложных "исправлений" на профессиональной лексике
(например "дефект", "таймкод"). pymorphy3 опирается на словарь
OpenCorpora и надёжно отличает реальные слова от опечаток.

Английский язык проверяется через pyspellchecker — для английского
(без сложной морфологии) частотный словарь работает хорошо.

Обе библиотеки — pure Python, без C-расширений, легко собираются
в PyInstaller .app.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from src.app_paths import CONFIG_DIR, atomic_write_text

logger = logging.getLogger(__name__)

CUSTOM_CORRECTIONS_FILE = CONFIG_DIR / "spellcheck_custom_corrections.json"


def load_custom_corrections(path: Path = CUSTOM_CORRECTIONS_FILE) -> dict:
    """Словарь исправлений, которые пользователь один раз ввёл вручную в

    диалоге ревью орфографии (было в нижнем регистре -> стало). Заменяет
    догадку алгоритма (pymorphy3/pyspellchecker) при повторной встрече того
    же слова в будущих отчётах — см. SpellcheckService.correct_text.
    Отсутствующий/битый файл — не ошибка, просто пустой словарь.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Не удалось прочитать пользовательские исправления орфографии: {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def save_custom_corrections(corrections: dict, path: Path = CUSTOM_CORRECTIONS_FILE) -> None:
    try:
        atomic_write_text(
            path,
            json.dumps(corrections, ensure_ascii=False, indent=2, sort_keys=True),
        )
    except OSError as exc:
        logger.warning(f"Не удалось сохранить пользовательские исправления орфографии: {exc}")


def remember_custom_correction(old: str, new: str, path: Path = CUSTOM_CORRECTIONS_FILE) -> None:
    """Запоминает исправление, введённое пользователем вручную в диалоге ревью

    (см. src/spellcheck_review.py) — ключ нормализуется в нижний регистр,
    регистр самого исправления сохраняется как есть и подгоняется под
    регистр найденного слова через _apply_case при применении.
    """
    corrections = load_custom_corrections(path)
    corrections[old.strip().lower()] = new.strip()
    save_custom_corrections(corrections, path)

try:
    from spellchecker import SpellChecker
    _EN_AVAILABLE = True
except ImportError:
    _EN_AVAILABLE = False

try:
    import pymorphy3
    _RU_AVAILABLE = True
except ImportError:
    _RU_AVAILABLE = False

# Термины предметной области (аудио QC / маркер-листы), которые не входят
# в общие словари, но не являются опечатками.
DOMAIN_WHITELIST = {
    'lufs', 'lra', 'dbtp', 'dbfs', 'tp', 'sfx', 'foley', 'фоли', 'фоули',
    'm&e', 'sync', 'синхрон', 'таймкод', 'timecode', 'тс', 'tc',
    'блокер', 'маркер', 'маркеры', 'маркер-лист', 'маркерлист', 'маркерлисты',
    'cens', 'uncens', 'censored', 'uncensored', 'stereo', 'стерео',
    'surround', 'сарраунд', 'клиппинг', 'clipping', 'реверб', 'reverb',
    'wav', 'mp3', 'pdf', 'csv', 'docx', 'roomtone', 'atmo',
}

_RU_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
_EN_ALPHABET = "abcdefghijklmnopqrstuvwxyz"

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё'-]+")

_SENTENCE_ENDERS = ".!?…"


def _is_cyrillic(word: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", word))


def _likely_proper_noun(text: str, start: int, word: str) -> bool:
    """Слово с Заглавной буквы НЕ в начале предложения — скорее всего имя

    собственное (персонаж, топоним, название), которого нет в общих
    словарях: его нельзя ни «исправлять», ни помечать как опечатку —
    автозамена молча портила бы имена в отчёте. В начале текста/предложения
    заглавная буква — обычная капитализация, там проверка идёт как всегда.
    """
    if not word[:1].isupper() or word.isupper():
        return False
    prefix = text[:start].rstrip(" \t")
    if not prefix or prefix.endswith("\n"):
        return False  # начало текста или строки
    return prefix[-1] not in _SENTENCE_ENDERS


def _apply_case(source: str, correction: str) -> str:
    if source.isupper():
        return correction.upper()
    if source[:1].isupper():
        return correction[:1].upper() + correction[1:]
    return correction


def _extract_json_array(raw: str):
    """Достаёт JSON-массив из ответа LLM, даже если он обёрнут в ```json

    код-блок или содержит текст до/после массива — модели часто добавляют
    пояснения, несмотря на просьбу вернуть строго JSON.
    """
    if not raw:
        return None

    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        return json.loads(stripped[start:end + 1])
    except json.JSONDecodeError:
        return None


def _edits1(word: str, alphabet: str) -> set:
    """Все слова на расстоянии редактирования 1 от word (правки Норвига)."""
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    deletes = [left + right[1:] for left, right in splits if right]
    transposes = [left + right[1] + right[0] + right[2:] for left, right in splits if len(right) > 1]
    replaces = [left + c + right[1:] for left, right in splits if right for c in alphabet]
    inserts = [left + c + right for left, right in splits for c in alphabet]
    return set(deletes + transposes + replaces + inserts)


class SpellcheckService:
    """Проверяет и по возможности исправляет опечатки в маркер-листах (RU/EN)."""

    _en_checker: Optional["SpellChecker"] = None
    _ru_analyzer = None
    _load_failed = False
    # Кэши на время жизни процесса: pymorphy-разбор недешёвый, а слова в
    # маркер-листах сильно повторяются; _correct_ru для одного неизвестного
    # слова перебирает тысячи кандидатов (см. _edits1) — без кэша большой
    # CSV с повторяющимися незнакомыми словами импортируется десятки секунд.
    _known_ru_cache: dict = {}
    _ru_correction_cache: dict = {}

    def __init__(self, config: Optional[dict] = None, generate_fn: Optional[Callable[..., str]] = None):
        """
        generate_fn — вызов LLM (prompt, *, options) -> str, тот же
        провайдер, что выбран в UI (см. ConclusionGenerator._ollama_generate,
        так же передаётся в MarkerTranslationService). None (по умолчанию) —
        батч-проверка орфографии идёт только через локальный алгоритм
        (pymorphy3/pyspellchecker), как и раньше.
        """
        self.config = config or {}
        self._generate_fn = generate_fn

    def correct_texts_batch(self, texts: List[str]) -> Dict[str, List[Tuple[str, str]]]:
        """Batch-поиск опечаток сразу во всех текстах — один вызов LLM на

        весь маркер-лист вместо отдельного запроса на каждое поле (по
        аналогии с MarkerTranslationService._translate_batch_with_llm).
        Без generate_fn или при сбое LLM (сеть/ключ/невалидный JSON) —
        откат на локальный алгоритм для каждого текста по отдельности,
        чтобы проверка орфографии никогда не блокировала генерацию отчёта.

        Возвращает {текст: [(было, стало), ...]}; тексты без опечаток в
        словаре отсутствуют.
        """
        unique_texts = list(dict.fromkeys(t for t in texts if t and t.strip()))
        if not unique_texts:
            return {}

        if self._generate_fn is not None:
            try:
                return self._correct_texts_llm(unique_texts)
            except Exception as exc:
                logger.warning(f"LLM-проверка орфографии недоступна, откат на локальный алгоритм: {exc}")

        result: Dict[str, List[Tuple[str, str]]] = {}
        for text in unique_texts:
            _fixed, fixes = self.correct_text(text)
            if fixes:
                result[text] = fixes
        return result

    def _correct_texts_llm(self, texts: List[str]) -> Dict[str, List[Tuple[str, str]]]:
        prompt_lines = [f"{idx}. {text}" for idx, text in enumerate(texts)]
        prompt = f"""Ты — редактор русского и английского текста в маркер-листах аудио QC.

Проверь орфографию, пунктуацию и явные грамматические ошибки в каждой строке ниже.

Правила:
1. Исправляй ТОЛЬКО орфографические, пунктуационные и явные грамматические ошибки.
2. Не перефразируй и не меняй стиль и структуру текста без необходимости.
3. Не добавляй новых слов и не убирай смысловые элементы.
4. Если не уверен в исправлении — не трогай строку вообще: лучше пропустить настоящую опечатку, чем испортить корректный текст.
5. НЕ считай ошибками:
   - профессиональные термины аудио QC (LUFS, LRA, dBTP, dBFS, SFX, foley, таймкод, timecode, синхрон, sync, клиппинг, реверб, M&E, cens/uncens и т.п.)
   - имена собственные (персонажи, названия, топонимы)
   - содержимое технических маркеров/тегов, если они встретились в тексте: [TAG], <tag>, {{code}}, #ID, таймкоды вида 00:00:00
   - фрагменты транскрипции живой речи — не выравнивай их до литературной нормы, исправляй только явные опечатки
   - корректные слова, даже редкие или разговорные
6. Если строка смешивает русский и английский — исправляй только русскую часть, если это безопасно для смысла.

Для каждой найденной ошибки верни точную подстроку "old" ровно так, как она написана в строке (с сохранением регистра и пунктуации), и "new" — исправленный вариант. Если в строке ошибок нет — пустой список corrections.

Верни СТРОГО JSON-массив вида:
[{{"index": 0, "corrections": [{{"old": "...", "new": "..."}}]}}]

СТРОКИ:
{chr(10).join(prompt_lines)}
"""
        raw = self._generate_fn(
            prompt,
            options={
                "temperature": 0.05,
                "num_predict": min(1500, max(300, 150 * len(texts))),
                "top_p": 0.9,
            },
        )
        payload = _extract_json_array(raw)
        if not isinstance(payload, list):
            raise ValueError(f"LLM вернул не JSON-массив: {raw[:200] if raw else raw!r}")

        result: Dict[str, List[Tuple[str, str]]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if not isinstance(index, int) or not (0 <= index < len(texts)):
                continue
            text = texts[index]
            pairs: List[Tuple[str, str]] = []
            for corr in item.get("corrections", []) or []:
                if not isinstance(corr, dict):
                    continue
                old = str(corr.get("old", "")).strip()
                new = str(corr.get("new", "")).strip()
                if old and new and old != new and old in text:
                    pairs.append((old, new))
            if pairs:
                result[text] = pairs
        return result

    @staticmethod
    def apply_pairs(text: str, pairs) -> Tuple[str, List[Tuple[str, str]]]:
        """Подставляет уже готовые пары (было, стало) в текст по границам

        слова, без повторного скана — используется при импорте CSV, когда
        исправления уже известны заранее (одобрены пользователем в диалоге
        ревью, либо найдены батчем при скане в auto-режиме). pairs — любой
        перебираемый набор пар (список или set).
        """
        if not text or not pairs:
            return text, []
        result = text
        applied: List[Tuple[str, str]] = []
        for old, new in pairs:
            if not old:
                continue
            pattern = re.compile(r"(?<![A-Za-zА-Яа-яЁё])" + re.escape(old) + r"(?![A-Za-zА-Яа-яЁё])")
            new_result, count = pattern.subn(lambda _m, _new=new: _new, result)
            if count:
                result = new_result
                applied.extend([(old, new)] * count)
        return result, applied

    @classmethod
    def _ensure_loaded(cls) -> bool:
        if cls._load_failed:
            return False
        if cls._en_checker is not None and cls._ru_analyzer is not None:
            return True
        if not (_EN_AVAILABLE and _RU_AVAILABLE):
            logger.warning(
                "Словари орфографии недоступны (не установлены pyspellchecker/pymorphy3) — "
                "проверка маркер-листов пропущена."
            )
            cls._load_failed = True
            return False
        try:
            cls._en_checker = SpellChecker(language="en")
            cls._ru_analyzer = pymorphy3.MorphAnalyzer()
        except Exception as exc:
            logger.warning(f"Не удалось загрузить словари орфографии: {exc}")
            cls._load_failed = True
            return False
        return True

    @classmethod
    def _is_known_ru(cls, word: str) -> bool:
        cached = cls._known_ru_cache.get(word)
        if cached is None:
            cached = bool(cls._ru_analyzer.parse(word)[0].is_known)
            cls._known_ru_cache[word] = cached
        return cached

    @classmethod
    def _correct_ru(cls, word: str) -> Optional[str]:
        """Ищет единственный уверенный вариант исправления среди правок на расстоянии 1."""
        if word in cls._ru_correction_cache:
            return cls._ru_correction_cache[word]
        result = cls._correct_ru_uncached(word)
        cls._ru_correction_cache[word] = result
        return result

    @classmethod
    def _correct_ru_uncached(cls, word: str) -> Optional[str]:
        candidates = {c for c in _edits1(word, _RU_ALPHABET) if cls._is_known_ru(c)}
        if not candidates:
            return None
        # Если вариантов несколько, берём тот, у кого выше морфологическая
        # уверенность (score) — при равенстве не угадываем, чтобы не испортить текст.
        scored = sorted(
            candidates,
            key=lambda c: cls._ru_analyzer.parse(c)[0].score,
            reverse=True,
        )
        best = scored[0]
        if len(scored) > 1:
            best_score = cls._ru_analyzer.parse(best)[0].score
            second_score = cls._ru_analyzer.parse(scored[1])[0].score
            if best_score == second_score:
                return None
        return best

    @classmethod
    def _should_skip(cls, word: str, stripped: str) -> bool:
        if len(stripped) <= 2:
            return True
        if stripped.isupper():
            return True
        if any(ch.isdigit() for ch in word):
            return True
        if stripped.lower() in DOMAIN_WHITELIST:
            return True
        return False

    @classmethod
    def correct_text(cls, text: str, approved=None) -> Tuple[str, List[Tuple[str, str]]]:
        """
        Проверяет и по возможности исправляет опечатки в тексте.

        approved: None — применять все уверенные исправления (автоматический
        режим); иначе — множество пар (было, стало), одобренных пользователем
        в диалоге ревью: исправление вне множества не применяется.

        Returns:
            (исправленный_текст, [(было, стало), ...])
        """
        if not text or not text.strip() or not cls._ensure_loaded():
            return text, []

        corrections: List[Tuple[str, str]] = []
        custom_corrections = load_custom_corrections()

        def replace(match: "re.Match") -> str:
            word = match.group(0)
            stripped = word.strip("'-")
            if cls._should_skip(word, stripped):
                return word
            if _likely_proper_noun(text, match.start(), stripped):
                return word

            lowered = stripped.lower()
            custom = custom_corrections.get(lowered)
            if custom:
                # Ручное исправление, ранее запомненное пользователем в
                # диалоге ревью — приоритетнее догадки алгоритма (может
                # заменять даже то, что pymorphy/pyspellchecker сочли бы
                # другим "уверенным" вариантом).
                suggestion = custom
            elif _is_cyrillic(stripped):
                if cls._is_known_ru(lowered):
                    return word
                suggestion = cls._correct_ru(lowered)
            else:
                if lowered in cls._en_checker:
                    return word
                suggestion = cls._en_checker.correction(lowered)

            if not suggestion or suggestion == lowered:
                return word

            fixed = _apply_case(stripped, suggestion)
            if fixed == stripped:
                return word
            if approved is not None and (word, fixed) not in approved:
                return word

            corrections.append((word, fixed))
            return word.replace(stripped, fixed, 1)

        corrected_text = _WORD_RE.sub(replace, text)
        return corrected_text, corrections

    @classmethod
    def check_text(cls, text: str) -> List[str]:
        """Возвращает слова с подозрением на опечатку, без исправления."""
        if not text or not text.strip() or not cls._ensure_loaded():
            return []

        misspelled = []
        for match in _WORD_RE.finditer(text):
            word = match.group(0)
            stripped = word.strip("'-")
            if cls._should_skip(word, stripped):
                continue
            if _likely_proper_noun(text, match.start(), stripped):
                continue

            lowered = stripped.lower()
            if _is_cyrillic(stripped):
                if not cls._is_known_ru(lowered):
                    misspelled.append(word)
            elif lowered not in cls._en_checker:
                misspelled.append(word)

        return misspelled
