"""Распознавание и упорядочивание дорожек для M&E-отчетов."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_DYNAMIC_KEY_RE = re.compile(
    r"^(audio|pdf)_me_(dx|opt(?:_[a-z0-9]+)?)_(20|51)$",
    re.IGNORECASE,
)
_IGNORED_OPT_VARIANTS = {
    "20", "51", "2", "5", "stereo", "surround", "c", "uc", "cens",
    "uncens", "wav", "wave", "pdf", "audio", "true", "peak", "report",
}

# Обозначения собраны из Netflix M&E Creation/Delivery Guidelines,
# Netflix Sound Mix Specifications, Sony Master & Archive Specifications,
# Roku Branded Delivery Specifications и EBU R123. Содержательные OPT-метки
# (efforts/walla/foreign/archival/vocals) считаются эвристикой: UI просит
# подтвердить их перед генерацией, а не назначает молча.
_ME_PATTERNS = (
    re.compile(r"(?:^|[^a-z0-9])m\s*[&+]\s*e(?:[^a-z0-9]|$)", re.I),
    re.compile(r"(?:^|[^a-z0-9])m[ _.-]*n[ _.-]*e(?:[^a-z0-9]|$)", re.I),
    re.compile(r"(?:^|[^a-z0-9])m[ _.-]*e(?:[^a-z0-9]|$)", re.I),
    re.compile(r"\b(?:music[ _.-]*(?:and[ _.-]*)?effects|international[ _.-]*sound|intl[ _.-]*sound|footsteps)\b", re.I),
    re.compile(r"\b(?:mix[ _.-]*minus[ _.-]*(?:dialogue|dialog|dx)|minus[ _.-]*(?:dialogue|dialog|dx))\b", re.I),
    re.compile(r"\b(?:интершум|музык[а-яё]*[ _.-]*(?:и[ _.-]*)?эффект[а-яё]*)\b", re.I),
)
_DX_STRONG_PATTERNS = (
    re.compile(r"(?:^|[^a-z0-9])dx(?:[^a-z0-9]|$)", re.I),
    re.compile(r"\b(?:dialogue|dialog|dial|dia|dlg)[ _.-]*(?:guide|stem|remove)?\b", re.I),
    re.compile(r"\b(?:dx[ _.-]*(?:guide|remove)|guide[ _.-]*(?:dx|dialogue|dialog)|dialogue[ _.-]*remove)\b", re.I),
    re.compile(r"(?:^|[^a-z0-9])(?:guide|remove)(?:[^a-z0-9]|$)", re.I),
    re.compile(r"\b(?:диалог[а-яё]*|реплик[а-яё]*)\b", re.I),
)
_OPT_STRONG_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:m[ _.-]*e[ _.-]*)?(?:opts?|optionals?|options?|op)[ _.-]*([a-z]|\d+)?(?:[^a-z0-9]|$)",
    re.I,
)
_OPT_COMPACT_RE = re.compile(r"(?:^|[^a-z0-9])(?:opt|op)([a-z]|\d+)(?:[^a-z0-9]|$)", re.I)
_OPT_RU_RE = re.compile(r"\b(?:опц(?:ия|ионал)?|опциональн[а-яё]*)[ _.-]*([a-zа-яё]|\d+)?\b", re.I)
_OPT_CONTENT_VARIANTS = (
    ("a", re.compile(r"\b(?:efforts?|breaths?|grunts?|groans?|vocali[sz]ations?|усили[яе]|вздох[а-яё]*|дыхани[ея])\b", re.I)),
    ("b", re.compile(r"\b(?:walla|group[ _.-]*adr|crowd|reax|reactions?|chants?|гур|толп[а-яё]*|реакци[а-яё]*)\b", re.I)),
    ("c", re.compile(r"\b(?:foreign[ _.-]*(?:dialogue|dialog|dx)|foreign[ _.-]*language|иностранн[а-яё]*[ _.-]*(?:диалог|язык))\b", re.I)),
    ("d", re.compile(r"\b(?:archiv(?:al|e)?|pre[ _.-]*existing[ _.-]*ip|sourced?[ _.-]*(?:audio|media)|архивн[а-яё]*)\b", re.I)),
    ("e", re.compile(r"\b(?:song[ _.-]*vocals?|singing|sung[ _.-]*vocals?|vocals?|vox|песенн[а-яё]*[ _.-]*вокал|вокал[а-яё]*)\b", re.I)),
)


def infer_me_track_variant(name: str) -> Tuple[Optional[str], str, str]:
    """Распознаёт роль дорожки и возвращает (kind, variant, confidence).

    confidence: ``strong`` для явных ME/DX/OPT обозначений, ``heuristic``
    для названий содержимого optional и ``unknown`` при отсутствии признаков.
    """
    stem = Path(str(name)).stem.lower()
    searchable = re.sub(r"[_\-.]+", " ", stem)

    # OPT проверяется первым: ``foreign dialogue OPT`` не должен стать DX.
    for pattern in (_OPT_STRONG_RE, _OPT_RU_RE):
        match = pattern.search(searchable)
        if match:
            return "opt", (match.group(1) or "").lower(), "strong"

    for variant, pattern in _OPT_CONTENT_VARIANTS:
        if pattern.search(searchable):
            return "opt", variant, "heuristic"

    if any(pattern.search(searchable) for pattern in _DX_STRONG_PATTERNS):
        return "dx", "", "strong"
    if any(pattern.search(searchable) for pattern in _ME_PATTERNS):
        return "me", "", "strong"
    return None, "", "unknown"


def detect_me_track_variant(name: str) -> Tuple[str, str]:
    """Возвращает (тип, вариант): me/dx/opt и суффикс OPT (A/B/...)."""
    kind, variant, _confidence = infer_me_track_variant(name)
    return kind or "me", variant


def me_assignment_key(kind: str, channel: str, media: str = "audio", variant: str = "") -> str:
    """Ключ назначения из окна ручного распределения ME-файлов."""
    if kind == "me":
        return f"{media}_{channel}_c"
    return me_track_key(kind, channel, media, variant)


def parse_me_assignment_label(label: str, media: str) -> Optional[str]:
    """Преобразует редактируемую подпись ``2.0 OPT A`` в tech_info-ключ."""
    value = str(label or "").strip()
    if not value or value.casefold() in {"не использовать", "ignore", "—"}:
        return None
    normalized = value.upper().replace("2.0", "20").replace("5.1", "51")
    match = re.fullmatch(r"(20|51)\s+(ME|DX|OPT)(?:\s+([A-Z0-9]+))?", normalized)
    if not match:
        raise ValueError("Используйте формат: 2.0 ME, 5.1 DX или 2.0 OPT A")
    channel, kind_label, variant = match.groups()
    kind = kind_label.lower()
    return me_assignment_key(kind, channel, media, (variant or "").lower())


def me_assignment_label(key: str) -> str:
    """Человекочитаемая подпись канонического или dynamic ME-ключа."""
    parsed = dynamic_me_track_from_key(key)
    if parsed:
        _media, kind, variant, channel = parsed
        return me_track_label(kind, channel, variant).replace("20", "2.0", 1).replace("51", "5.1", 1)
    match = re.match(r"^(?:audio|pdf)_(20|51)(?:_c|_uc)?$", str(key))
    if match:
        channel = "2.0" if match.group(1) == "20" else "5.1"
        return f"{channel} ME"
    return str(key)


def me_track_key(kind: str, channel: str, media: str = "audio", variant: str = "") -> str:
    """Строит стабильный tech_info-ключ для дополнительной ME-дорожки."""
    if kind == "dx":
        return f"{media}_me_dx_{channel}"
    if kind == "opt":
        suffix = re.sub(r"[^a-z0-9]+", "_", variant.lower()).strip("_")
        return f"{media}_me_opt{f'_{suffix}' if suffix else ''}_{channel}"
    raise ValueError(f"Для базовой ME-дорожки используются канонические ключи: {kind}")


def dynamic_me_track_from_key(key: str) -> Optional[Tuple[str, str, str, str]]:
    """Разбирает dynamic key в (media, kind, variant, channel)."""
    match = _DYNAMIC_KEY_RE.match(str(key))
    if not match:
        return None
    media, descriptor, channel = match.groups()
    if descriptor.lower() == "dx":
        return media.lower(), "dx", "", channel
    parts = descriptor.lower().split("_", 1)
    return media.lower(), "opt", parts[1] if len(parts) > 1 else "", channel


def me_track_label(kind: str, channel: str, variant: str = "") -> str:
    channel_label = "20" if channel == "20" else "51"
    if kind == "me":
        return f"{channel_label} ME"
    if kind == "dx":
        return f"{channel_label} DX"
    suffix = f" {variant.upper()}" if variant else ""
    return f"{channel_label} OPT{suffix}"


def _variant_sort_key(variant: str):
    if not variant:
        return (0, 0, "")
    if variant.isdigit():
        return (1, int(variant), "")
    return (2, 0, variant.casefold())


def iter_dynamic_me_pairs(tech_info: Dict) -> Iterable[Tuple[str, str, str]]:
    """Выдает (label, audio_key, pdf_key) для DX/OPT в порядке шаблона."""
    tracks = set()
    for key in tech_info or {}:
        parsed = dynamic_me_track_from_key(key)
        if parsed:
            _media, kind, variant, channel = parsed
            tracks.add((kind, variant, channel))

    def sort_key(item):
        kind, variant, channel = item
        return (
            0 if kind == "dx" else 1,
            _variant_sort_key(variant),
            0 if channel == "20" else 1,
        )

    for kind, variant, channel in sorted(tracks, key=sort_key):
        audio_key = me_track_key(kind, channel, "audio", variant)
        pdf_key = me_track_key(kind, channel, "pdf", variant)
        yield me_track_label(kind, channel, variant), audio_key, pdf_key


def marker_track_values(row: Dict[str, str]) -> Dict[str, bool]:
    """Читает шесть новых колонок ME marker list (регистр не важен)."""
    normalized = {
        str(key).strip().casefold(): str(value or "").strip()
        for key, value in row.items()
        if key is not None
    }

    def marked(*names: str) -> bool:
        return any(normalized.get(name.casefold()) == "*" for name in names)

    return {
        "me_20": marked("2.0 ME", "20 ME", "2.0 C"),
        "me_51": marked("5.1 ME", "51 ME", "5.1 C"),
        "dx_20": marked("2.0 DX", "20 DX"),
        "dx_51": marked("5.1 DX", "51 DX"),
        "opt_20": marked("2.0 OPT", "20 OPT"),
        "opt_51": marked("5.1 OPT", "51 OPT"),
    }
