"""
Marker Translation Service

Подготавливает текст маркеров для русскоязычного заключения, не изменяя
оригинальный текст, который может понадобиться в таблицах отчёта.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Callable, Dict, List, Optional

from src.csv_importer import Issue
from src.ollama_service import OllamaService

logger = logging.getLogger(__name__)


class MarkerTranslationService:
    """Подготавливает русскоязычный аналитический текст маркеров."""

    def __init__(self, config: Optional[dict] = None, generate_fn: Optional[Callable[..., str]] = None):
        """
        generate_fn — вызов генерации текста (prompt, *, options) -> str.
        По умолчанию — напрямую локальная Ollama; ConclusionGenerator
        передаёт сюда свой _ollama_generate, чтобы перевод маркеров шёл
        через тот же провайдер (Ollama/Groq/YandexGPT), что и заключение,
        а не был жёстко привязан к локальной модели независимо от выбора
        пользователя в UI.
        """
        self.config = config or {}
        self._generate_fn = generate_fn or OllamaService(self.config).generate
        self._translation_cache: Dict[str, str] = {}

    def prepare_issues(self, issues: List[Issue], use_llm: bool = False) -> List[Issue]:
        """
        Возвращает копии Issue с заполненными description_original / description_ru.
        Оригинальное поле description не трогаем, чтобы не ломать маркер-лист в отчётах.
        """
        prepared: List[Issue] = []
        llm_candidates: List[str] = []
        llm_indexes: List[int] = []

        for issue in issues:
            prepared_issue = copy.copy(issue)
            original = (prepared_issue.description_original or prepared_issue.description or "").strip()
            prepared_issue.description_original = original

            detected_language = self.detect_language(original)
            if prepared_issue.source_language in ("", "unknown"):
                prepared_issue.source_language = detected_language

            if prepared_issue.description_ru:
                prepared.append(prepared_issue)
                continue

            if detected_language in {"ru", "mixed", "unknown"}:
                prepared_issue.description_ru = original
                prepared.append(prepared_issue)
                continue

            # Description на английском и требует перевода — сначала пробуем
            # готовый русский перевод из колонки комментариев, если он есть.
            comment_translation = self._extract_ru_from_comments(prepared_issue.comments)
            if comment_translation:
                prepared_issue.description_ru = comment_translation
                prepared.append(prepared_issue)
                continue

            fallback_translation = self._translate_with_rules(original)
            if fallback_translation:
                prepared_issue.description_ru = fallback_translation

            if use_llm and original:
                llm_candidates.append(original)
                llm_indexes.append(len(prepared))

            prepared.append(prepared_issue)

        if llm_candidates:
            llm_translations = self._translate_batch_with_llm(llm_candidates)
            for idx, original in zip(llm_indexes, llm_candidates):
                translated = llm_translations.get(original)
                if translated:
                    prepared[idx].description_ru = translated

        translated_count = sum(
            1 for issue in prepared
            if issue.source_language == "en" and issue.description_ru and issue.description_ru != issue.description_original
        )
        if translated_count:
            logger.info("Подготовлено русских переводов маркеров: %d", translated_count)

        return prepared

    @staticmethod
    def detect_language(text: str) -> str:
        """Грубая, но достаточная для маркеров детекция языка."""
        if not text:
            return "unknown"

        latin = len(re.findall(r"[A-Za-z]", text))
        cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))

        if latin == 0 and cyrillic == 0:
            return "unknown"
        if cyrillic >= max(2, latin * 2):
            return "ru"
        if latin >= max(2, cyrillic * 2):
            return "en"
        return "mixed"

    def _translate_batch_with_llm(self, texts: List[str]) -> Dict[str, str]:
        """
        Переводит англоязычные маркеры через настроенный провайдер (Ollama/
        Groq/YandexGPT). Если модель недоступна или вернула невалидный
        JSON, просто оставляем rule-based fallback.
        """
        pending = [text for text in texts if text and text not in self._translation_cache]
        if not pending:
            return {text: self._translation_cache.get(text, "") for text in texts}

        prompt_lines = [f'{idx}. {text}' for idx, text in enumerate(pending)]
        prompt = f"""Ты переводчик и редактор маркер-листов для аудио QC.

Переведи каждую английскую строку на русский язык для использования в профессиональном заключении.

ПРАВИЛА:
- Сохраняй точный смысл и конкретику маркера
- Не добавляй выводов, рекомендаций и оценок
- Сохраняй профессиональную терминологию аудио QC
- M&E, optional track, lip sync, room tone и похожие термины переводи профессионально и единообразно
- Если в строке есть мат, названия эффектов, имена персонажей или важные детали, не теряй их
- Верни СТРОГО JSON-массив объектов вида:
  [{{"index": 0, "translation": "..."}}]

СТРОКИ ДЛЯ ПЕРЕВОДА:
{chr(10).join(prompt_lines)}
"""

        try:
            raw = self._generate_fn(
                prompt,
                options={
                    "temperature": 0.05,
                    "num_predict": min(1200, max(300, 120 * len(pending))),
                    "top_p": 0.9,
                },
            )
            payload = self._extract_json_array(raw)
            if not isinstance(payload, list):
                return {text: self._translation_cache.get(text, "") for text in texts}

            for item in payload:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                translation = str(item.get("translation", "")).strip()
                if isinstance(index, int) and 0 <= index < len(pending) and translation:
                    self._translation_cache[pending[index]] = translation
        except Exception as exc:
            logger.debug("LLM-перевод маркеров недоступен: %s", exc)

        return {text: self._translation_cache.get(text, "") for text in texts}

    @staticmethod
    def _extract_json_array(raw: str):
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

    @staticmethod
    def _translate_with_rules(text: str) -> str:
        """
        Быстрый локальный fallback для типовых англоязычных маркеров.
        Лучше дать неидеальный, но русский текст, чем оставить заключение на английском.
        """
        if not text:
            return ""

        cleaned = " ".join(text.strip().split())
        lowered = cleaned.lower()

        def has_any(*patterns: str) -> bool:
            return any(re.search(pattern, lowered) for pattern in patterns)

        if has_any(r"\boptional track\b"):
            return "эти элементы следует вынести в опциональный трек"

        # Стерео-формат (заставка / титры / музыка)
        if has_any(
            r"\b(plays?\s+only\s+in\s+stereo|played?\s+only\s+in\s+stereo|only\s+(in\s+)?stereo\s+format|"
            r"stereo\s+format\s+only|only\s+sounds?\s+in\s+stereo)\b"
        ):
            if has_any(r"\b(screensaver)\b"):
                return "музыка на заставке воспроизводится только в формате стерео"
            if has_any(r"\b(credits|titles)\b"):
                return "музыка на титрах воспроизводится только в формате стерео"
            if has_any(r"\b(music)\b"):
                return "музыка воспроизводится только в формате стерео"
            return "звук воспроизводится только в формате стерео"

        if re.search(r"screensaver", lowered) and has_any(r"\bwithout\b", r"\bno\b") and has_any(r"\bcentral channel\b"):
            match = re.search(r"\b(?:the\s+)?([a-z0-9][a-z0-9\s'&-]*?)\s+screensaver\b", cleaned, re.IGNORECASE)
            name = match.group(1).strip() if match else "заставка"
            if name.lower() == "the":
                name = "заставка"
            if name != "заставка":
                return f'заставка "{name}" воспроизводится без центрального канала'
            return "заставка воспроизводится без центрального канала"

        if has_any(r"\bonly in (the )?central channel\b", r"\bsound is only in (the )?central channel\b"):
            return "звуковой сигнал присутствует только в центральном канале"

        if has_any(r"\b(left and right surround channels|ls and rs|surround channels)\b") and has_any(r"\b(disappeared|dissappeared|missing|absent|no sound)\b"):
            return "отсутствует звуковой сигнал в каналах Ls и Rs"

        # Лёгкая несинхронность — ДО общего правила про unsync
        if has_any(r"\b(looks little unsync|looks a little unsync|little unsync|slightly unsync)\b"):
            if has_any(r"\b(man('|')s phrase|man's phrase|actor('|')s line|phrase|line)\b"):
                return "реплика выглядит немного несинхронной"
            return "фрагмент выглядит немного несинхронным"

        if has_any(r"\b(out of sync|lip[\s-]?sync|sync drift|sync issue|desync|is unsync|unsync)\b"):
            return "реплики несинхронны с изображением"

        # «Possibly missing line/phrase» — отсутствие реплики (до общего missing)
        if has_any(r"\b(missing|possibly missing)\b") and has_any(r"\b(phrase|line|word|words)\b"):
            return "возможно отсутствует реплика"

        # Звук пропал / отсутствует (ambient/background) — ВАЖНО: до общего noise!
        if has_any(r"\b(missing|no|absent|lost|almost absent)\b") and has_any(r"\b(background|ambience|ambient|atmo|atmosphere|room tone|roomtone)\b"):
            return "отсутствует фоновый шум"

        # Часть звука пропала (ambience dissappeared / part of sound disappeared)
        if has_any(r"\b(dissappeared|disappeared|vanished|dropped)\b") and has_any(r"\b(ambience|ambient|atmosphere|atmo|background)\b"):
            return "пропадает фоновый шум"

        if has_any(r"\b(missing|no|absent|lost)\b") and has_any(r"\b(music|score|song)\b"):
            return "отсутствует музыка"

        if has_any(r"\b(missing|no|absent|lost)\b") and has_any(r"\b(sync|foley|footstep|cloth|movement|action sound|sfx)\b"):
            return "отсутствуют синхронные шумы"

        if has_any(r"\b(differs?|different|changed|does not match|mismatch)\b") and has_any(r"\b(background|ambience|atmo|atmosphere|room tone|roomtone)\b"):
            return "фоновые шумы отличаются от оригинальной звуковой дорожки"

        if has_any(r"\b(differs?|different|changed|does not match|mismatch)\b") and has_any(r"\b(music|score|song)\b"):
            return "музыка отличается от оригинальной звуковой дорожки"

        if has_any(r"\b(differs?|different|changed|does not match|mismatch)\b") and has_any(r"\b(sync|foley|footstep|cloth|movement|action sound|sfx)\b"):
            return "синхронные шумы отличаются от оригинальной звуковой дорожки"

        # Ambient sounds differs after this timecode — атмосфера отличается после таймкода
        if has_any(r"\b(differs?|different|changed)\b") and has_any(r"\b(ambient|ambience|atmo|atmosphere)\b"):
            return "звуковая атмосфера отличается на разных фрагментах"

        # Background noises + crackles — комбинированный шум с треском
        if has_any(r"\bbackground\s+noise") and has_any(r"\bcrackle"):
            return "слышны фоновый шум и треск"

        # Unwanted sound — неуточнённый посторонний звук
        if has_any(r"\bunwanted\s+sound\b"):
            if has_any(r"\b(voice|speech|phrase|phrases|line|lines)\b"):
                return "на реплике слышен посторонний звук"
            return "слышен посторонний звук"

        # Электрический фоновый шум / интерференция (до общего background noise!)
        if has_any(r"\b(electrical|electric)\s+(background\s+)?noise\b", r"\binterference\b"):
            if has_any(r"\b(voice|speech|phrase|phrases|line|lines)\b"):
                return "на реплике слышен электрический фоновый шум"
            return "присутствует электрический фоновый шум"

        # Фоновый шум появляется на действии (handshake, movement)
        if has_any(r"\b(background noise|white noise)\b") and has_any(
            r"\b(appearing|appears)\s+(with|on|during)\b",
            r"\bwith the\s+(handshake|movement|action|step)\b",
        ):
            return "фоновый шум появляется на действии"

        # Фоновый шум ослабевает / становится тише
        if has_any(r"\b(background\s+noise|ambience\s+noise|ambient\s+noise)\b") and has_any(
            r"\b(suddenly\s+became|got\s+|getting\s+|became)\s*(a\s+little\s+)?(quieter|louder)\b",
            r"\bgetting\s+quieter\b",
        ):
            if has_any(r"\bquieter\b"):
                return "фоновый шум становится тише"
            return "фоновый шум изменяется по уровню"

        # Часть фонового шума пропадает / гэп в шуме
        if has_any(r"\b(part of\s+the\s+(ambience|ambient)\s+(noise|sound))\b") and has_any(r"\b(disappeared|dissappeared|cuts|gap)\b"):
            return "пропадает фоновый шум"

        if has_any(r"\b(ambience|ambient)\s+(sound|noise)\s+cuts\b", r"\bgap in\s+ambience\b"):
            return "фоновый шум обрывается"

        # Drop out на реплике
        if has_any(r"\bdrop\s*out\b") and has_any(r"\b(phrase|line|speech|voice)\b"):
            return "на реплике слышен короткий пропадающий участок звука"
        if has_any(r"\bdrop\s*out\b"):
            return "слышно кратковременное пропадание звука"

        # Эффект задвоения голоса
        if has_any(r"\b(doubling|doubled)\s+effect\b", r"\bphrase\s+(is\s+)?doubled\b"):
            return "на реплике слышен эффект задвоения"

        # Гэп в музыке
        if has_any(r"\bpart of\s+the\s+music\s+(disappeared|dissappeared)\b", r"\bgap in\s+the\s+music\b"):
            return "пропадает фрагмент музыки"

        # Белый шум — отдельная формулировка (референсный стиль ручных отчётов)
        if has_any(r"\b(white\s+background\s+noise|backround\s+white\s+noise|white\s+noise)\b"):
            if has_any(r"\b(voice|speech|phrase|phrases|line|lines)\b"):
                return "на реплике слышен фоновый белый шум"
            return "слышен фоновый белый шум"

        if has_any(r"\b(background noise|background nosie|backgound noise)\b") and has_any(r"\b(voice|speech|phrase|phrases|line|lines)\b"):
            return "на реплике слышно постороннее шипение"

        # Фоновый шум без привязки к голосу
        if has_any(r"\b(background noise|background nosie|backgound noise)\b"):
            return "слышно постороннее шипение"

        # Реплика звучит "чисто", несмотря на пространство (без реверберации стекла и т.п.)
        if has_any(r"\bsounds?\s+clear\b") and has_any(r"\b(behind\s+glass|in the room|in the space|through\s+the\s+wall|in\s+the\s+phone)\b"):
            return "звучание реплики не соответствует пространству в кадре"

        if has_any(r"\b(whistle|whistling)\b") and has_any(r"\b(voice|speech|phrase|phrases|line|lines)\b", r"\bhigh frequency\b", r"\bexcessive high frequency\b"):
            return "на реплике слышен свистящий высокочастотный призвук"

        # Паттерн для кавычек всех видов: ", ', «, », curly double/single quotes
        _qc = '["\'\u00ab\u00bb\u201c\u201d\u2018\u2019]'

        # Звук «С» — специфичное правило ДО общего про букву в кавычках
        if has_any(
            _qc + r"s" + _qc + r"\s*sound",
            r"\bs sound\b",
        ) and has_any(r"\bexcessive\s+high\s+freq(?:uency|uncy|urncy|rncy|nency|unecy)?\b", r"\bhigh\s+freq(?:uency|uncy|urncy|rncy|nency|unecy)?\b", r"\bhiqh\s+freq", r"\bhgih\s+freq"):
            return "на реплике слышно яркое свистящее звучание звука «С»"

        # Резкое/яркое звучание конкретной буквы ("K" sound too loud, "S" excessive, etc.)
        if has_any(
            _qc + r"[a-zа-яё]" + _qc + r"\s*sound",
        ) and has_any(r"\b(too loud|excessive|bright|harsh|sharp|prominent)\b", r"\bhigh\s+freq(?:uency|uncy|urncy|rncy|nency|unecy)?\b", r"\bhiqh\s+freq", r"\bhgih\s+freq"):
            match = re.search(_qc + r"([a-zа-яё])" + _qc, lowered)
            letter = match.group(1).upper() if match else "?"
            return f"на реплике слышно резкое звучание буквы «{letter}»"


        # Резкое звучание буквы БЕЗ кавычек (K sound, S sound) + контекст excessive/loud
        _letter_match = re.search(r"\b([A-Zа-яё])\s+sound\b", cleaned)
        if _letter_match and has_any(r"\b(excessive|too\s+loud|bright|harsh)\b") and has_any(r"\bfreq(?:uency|uncy|urncy|rncy|nency|unecy)?\b", r"\bhiqh\b", r"\bhgih\b", r"\btoo\s+loud\b"):
            letter = _letter_match.group(1).upper()
            return f"на реплике слышно резкое звучание буквы «{letter}»"

        # ВЧ-щелчок (гибрид high frequency + click) — до общего HF/click правил
        if has_any(r"\b(high\s+freq(?:uency|uncy|urncy|rncy|nency|unecy)?|hgih\s+frequency|hiqh\s+frequency)\b") and has_any(r"\bclick"):
            return "на реплике слышен высокочастотный щелчок"

        # Низкочастотный звук / НЧ помехи (с опечатками frequrncy)
        if has_any(r"\b(low\s+freq(?:uency|uncy|urncy|rncy|nency|unecy)?)\b"):
            if has_any(r"\b(voice|speech|phrase|phrases|line|lines)\b"):
                return "на реплике слышен посторонний низкочастотный звук"
            return "слышен посторонний низкочастотный звук"

        # Высокочастотные звуки (с опечатками: frequnecy, frequrncy, frequnency, hiqh, Hgih)
        _hf = r"\b(high\s+freq(?:uency|uncy|urncy|rncy|nency|unecy)?|hgih\s+freq(?:uency|uncy)?|hiqh\s+freq(?:uency|uncy)?)\b"
        if has_any(_hf + r".*\b(sound|noise|interference)\b", r"\b(sound|noise|interference)\b.*" + _hf):
            if has_any(r"\b(before)\b") and has_any(r"\b(voice|speech|phrase|phrases|line|lines)\b"):
                return "перед репликой слышен посторонний высокочастотный звук"
            if has_any(r"\b(voice|speech|phrase|phrases|line|lines)\b"):
                return "на реплике слышен посторонний высокочастотный звук"
            return "слышен посторонний высокочастотный звук"

        if has_any(r"\bexcessive\s+high\s+freq(?:uency|uncy|nency)?\b", _hf):
            return "слышен посторонний высокочастотный призвук"

        # Слюна — отдельный перевод, чтобы в заключении LLM мог написать «щелчки и слюна»
        if has_any(r"\b(saliva)\b"):
            return "слышны яркие звуки слюны"

        # Щелчки, клики — с учётом опечаток (clik, clicing, cliks, clik's, littlce, etc.)
        if has_any(
            r"\b(click|clicks|clicking|clicing|cliick|cliicking|clik|cliks|clik's|littlce|mouth\s+click(?:s|'s)?|mouth\s+clik|lip\s+smack|pop)\b",
            r"\bclick\s*\(",  # "Click (maybe...)"
        ):
            return "слышны посторонние щёлкающие звуки"

        if has_any(r"\b(hiss|hissing|sibilance)\b"):
            return "слышно высокочастотное шипение"

        if has_any(r"\b(crackle|crackling)\b"):
            return "присутствует треск"

        if has_any(r"\b(clipping|clipped|distortion|distorted|overload)\b"):
            return "ощущается перегруз"

        if has_any(r"\b(reverb|reverberation|echo)\b"):
            return "изменяется реверберация"

        if has_any(r"\b(no audio|audio drop|audio dropout|dropout|silence|silent|missing audio)\b"):
            return "отсутствует звук"

        # Звук появляется позже изображения (sound missing while picture already started)
        if (
            has_any(
                r"\b(sound|audio|track)\s+(is\s+)?(missing|absent|not\s+(yet\s+)?present|gone|delayed|late)\b",
                r"\bno\s+(sound|audio)\b",
                r"\b(missing|absent|no)\s+(sound|audio)\b",
            )
            and has_any(
                r"\b(image|picture|video|scene|frame|footage)\b.*\b(started|begun|begin|begins|starts|playing|going|on)\b",
                r"\b(started|begun|begin|begins|starts|playing|going|on)\b.*\b(image|picture|video|scene|frame|footage)\b",
            )
        ):
            return "звук появляется позже изображения"

        # «sound is missing, but image has already started» — альтернативная формулировка
        if has_any(r"\b(sound|audio)\b") and has_any(r"\bmissing\b") and has_any(r"\b(image|picture|video)\b"):
            return "звук появляется позже изображения"

        # Аудио обрывается / прерывается
        if has_any(
            r"\b(cuts?\s+off|cut'?s\s+off|cuts?\s+out|drops?\s+out|drops?\s+off|breaks?\s+off|cuts?\s+short)\b",
            r"\b(audio|sound|track|music)\b.*\b(cut|cuts|cut's|cutoff|drop|drops|breaks?)\b\s*(off|out)?",
        ):
            return "звук обрывается"

        # Слышна склейка / монтажный стык
        if has_any(r"\b(hear the cut|hear a cut|audible cut|audible edit|cut between)\b"):
            return "слышна склейка в аудио"

        # Дыхание / breathing
        if has_any(r"\b(breath|breathing|inhale|exhale)\b") and has_any(r"\b(audible|loud|hear|present|too)\b"):
            return "слышно дыхание актёра"

        # Стучащие/постукивающие звуки
        if has_any(r"\b(knock|knocks|knocking|tap|tapping|taps|bang|banging|thud|thumps?|thumping|drumming)\b"):
            return "слышны посторонние стучащие звуки"

        # Посторонний голос, не являющийся частью реплики
        if has_any(r"\b(voice|speech)\b") and has_any(r"\bnot\s+(a\s+)?part\s+of\b"):
            if has_any(r"\bman('|')?s\b", r"\bactor('|')?s\b"):
                return "слышен посторонний мужской голос, который не является частью реплики актёра"
            if has_any(r"\bwoman('|')?s\b", r"\bactress('|')?s\b"):
                return "слышен посторонний женский голос, который не является частью реплики актрисы"
            return "слышен посторонний голос, который не является частью реплики"

        # Голос звучит иначе, чем в других репликах
        if has_any(r"\bvoice\b") and has_any(r"\b(sounds?\s+differs?|sounds?\s+different)\b", r"\bdiffers?\b.*\bcompared\b"):
            if has_any(r"\bactress('|')?s\b", r"\bwoman('|')?s\b"):
                return "голос актрисы звучит иначе, чем в остальных репликах"
            return "голос актёра звучит иначе, чем в остальных репликах"

        # Слишком громкий синхронный шум (шаг, удар) — до общего too loud
        if has_any(r"\b(step|steps|footstep|footsteps)\b") and has_any(r"\btoo\s+loud\b"):
            return "шаги звучат слишком громко"

        if has_any(r"\b(far and dirty|sounds far|dirty)\b") and has_any(r"\b(voice|speech|phrase|phrases|line|lines)\b"):
            return "реплика звучит глухо и грязно по сравнению с остальными"

        # Реплику плохо слышно из-за музыки / сложно разобрать
        if has_any(r"\b(hard to make out|hard to hear|hard to understand|unintelligible|inaudible)\b"):
            return "реплика плохо разборчива"

        if has_any(r"\b(voice|dialog|dialogue|line|actor('|')s line|actor line|speech)\b") and has_any(r"\b(audible|present|left|remains|remaining)\b"):
            return "присутствуют реплики актёров"

        if has_any(r"\b(beep|censor|censored|uncensored|profanity|obscene)\b"):
            return "отсутствует маскировка нецензурной лексики"

        # Громкость — слишком громко / тихо (до общего noise!)
        if has_any(r"\b(too loud|too quiet|too silent|too soft)\b"):
            return "уровень звука требует корректировки"

        if has_any(r"\b(noise|hum|buzz|rumble)\b"):
            return "присутствуют посторонние шумы"

        # Если в тексте по-прежнему преобладает латиница, не оставляем английский в заключении —
        # выбираем максимально близкое обобщение по общим аудио-категориям.
        latin_count = len(re.findall(r"[A-Za-z]", cleaned))
        cyrillic_count = len(re.findall(r"[А-Яа-яЁё]", cleaned))
        if latin_count == 0 or cyrillic_count >= latin_count:
            return cleaned

        if has_any(r"\b(missing|absent|gone|lost|removed)\b"):
            if has_any(r"\b(voice|speech|phrase|line|dialog|dialogue|word|words|actor|actress)\b"):
                return "возможно отсутствует реплика"
            if has_any(r"\b(music|score|song|melody)\b"):
                return "отсутствует музыка"
            if has_any(r"\b(background|ambience|ambient|atmo|atmosphere|room\s*tone)\b"):
                return "отсутствует фоновый шум"
            return "отсутствует звуковой элемент"

        if has_any(r"\b(loud|louder|too\s+loud|level|levels|volume|gain|quiet|quieter|too\s+quiet|low\s+level)\b"):
            return "уровень звука требует корректировки"

        # Дыхание (fallback без контекста loud/audible)
        if has_any(r"\b(breath|breathing|inhale|exhale)\b"):
            return "слышно дыхание актёра"

        if has_any(r"\b(voice|speech|phrase|line|dialog|dialogue|actor|actress|talking)\b"):
            return "присутствуют дефекты в реплике"

        if has_any(r"\b(music|score|song|melody|theme|composition)\b"):
            return "присутствуют дефекты в музыке"

        if has_any(r"\b(sound|audio|track|tone|beat|signal)\b"):
            return "обнаружен дефект звуковой дорожки"

        # Полный unknown с латиницей — отдаём универсальную русскую формулировку,
        # чтобы в заключении не остался англоязычный текст.
        return "обнаружен звуковой дефект на фрагменте"

    @staticmethod
    def _extract_ru_from_comments(comments: str) -> str:
        """
        Если в колонке комментариев уже есть русская редакторская формулировка,
        используем её как приоритетный источник для заключения.
        """
        if not comments:
            return ""

        cleaned = comments.replace("НОВЫЙ МАРКЕР", "").strip()
        cleaned = re.sub(r'^[\s\)\]\}\.\-,:;"]+', "", cleaned).strip()
        if not cleaned:
            return ""

        cyrillic = len(re.findall(r"[А-Яа-яЁё]", cleaned))
        if cyrillic < 3:
            return ""

        return cleaned
