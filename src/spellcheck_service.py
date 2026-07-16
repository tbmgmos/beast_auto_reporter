"""
Spellcheck Service

Проверка орфографии и автоисправление опечаток в тексте маркер-листов
(колонки Description / Comments) на русском и английском языках.

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

import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

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

        def replace(match: "re.Match") -> str:
            word = match.group(0)
            stripped = word.strip("'-")
            if cls._should_skip(word, stripped):
                return word
            if _likely_proper_noun(text, match.start(), stripped):
                return word

            if _is_cyrillic(stripped):
                lowered = stripped.lower()
                if cls._is_known_ru(lowered):
                    return word
                suggestion = cls._correct_ru(lowered)
            else:
                lowered = stripped.lower()
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
