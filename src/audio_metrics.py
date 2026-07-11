"""
Audio metrics helpers.

Primary path:
- Integrated loudness via pyloudnorm (BS.1770)
- LRA / True Peak / Short-term max / Momentary max via ffmpeg ebur128=peak=true
- PDF loudness report in a Youlean-like layout
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Lazy imports for fast startup
np = None
pyln = None
sf = None


def _ensure_audio_libs():
    """Import heavy audio libraries on first use."""
    global np, pyln, sf
    if np is None:
        import numpy as _np
        import pyloudnorm as _pyln
        import soundfile as _sf
        np = _np
        pyln = _pyln
        sf = _sf


ArrayLike = "np.ndarray"  # type alias (string for lazy import)
AudioInput = Union[str, Path, "np.ndarray"]

_FLOAT_RE = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)")


def _find_ffmpeg() -> Optional[str]:
    """Find ffmpeg in bundle or PATH."""
    if getattr(sys, "frozen", False):
        bundle_dir = getattr(sys, "_MEIPASS", "")
        candidates = [
            os.path.join(bundle_dir, "ffmpeg", "ffmpeg"),
            os.path.abspath(os.path.join(bundle_dir, "..", "Frameworks", "ffmpeg", "ffmpeg")),
        ]
        for candidate in candidates:
            if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                return candidate

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _normalize_audio_shape(audio: "np.ndarray") -> "np.ndarray":
    """
    Normalize audio shape to [samples, channels] or [samples].
    Supports mono, [samples, channels], and [channels, samples].
    """
    _ensure_audio_libs()
    arr = np.asarray(audio)
    if arr.ndim == 1:
        return arr.astype(np.float64, copy=False)
    if arr.ndim != 2:
        raise ValueError(f"Unsupported audio ndim={arr.ndim}, expected 1 or 2")

    rows, cols = arr.shape

    # Common heuristic for channels-first arrays (C, N), where C is small.
    if rows <= 32 and cols > rows:
        arr = arr.T
    elif rows < cols and cols <= 32:
        # Ambiguous small matrix, but channels are typically the smaller axis.
        arr = arr.T

    return arr.astype(np.float64, copy=False)


def _prepare_for_pyloudnorm(audio: np.ndarray) -> np.ndarray:
    """
    pyloudnorm in this project version rejects >5 channels.
    For 5.1 input we drop LFE (index 3), which is excluded from loudness weighting.
    """
    if audio.ndim == 1:
        return audio

    channels = audio.shape[1]
    if channels <= 5:
        return audio
    if channels == 6:
        logger.info("pyloudnorm compatibility: 5.1 detected, dropping LFE channel for Integrated LUFS")
        return audio[:, [0, 1, 2, 4, 5]]

    logger.warning("pyloudnorm compatibility: %s channels detected, using first 5 channels", channels)
    return audio[:, :5]


def _load_audio_input(input_data: AudioInput, sr: Optional[int]) -> Tuple["np.ndarray", int, Optional[Path]]:
    """Load audio from ndarray or file path."""
    _ensure_audio_libs()
    if isinstance(input_data, (str, Path)):
        source_path = Path(input_data)
        audio, file_sr = sf.read(str(source_path), always_2d=False)
        return _normalize_audio_shape(audio), int(file_sr), source_path

    audio = _normalize_audio_shape(np.asarray(input_data))
    if sr is None:
        raise ValueError("sr must be provided when input is a numpy array")
    return audio, int(sr), None


def _extract_last_summary_block(text: str) -> str:
    matches = list(re.finditer(r"\bSummary:\b", text, flags=re.IGNORECASE))
    if not matches:
        return text
    return text[matches[-1].start():]


def _extract_last_value(text: str, pattern: str) -> Optional[float]:
    matches = re.findall(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def _extract_marker_values(text: str, marker: str) -> list[float]:
    values: list[float] = []
    # Marker can appear at line start (summary) or inline in live meter lines.
    pattern = re.compile(
        rf"\b{re.escape(marker)}\s*:\s*([^\n\r]+?)(?=\s+\b[A-Z]{{1,5}}\s*:|$)",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in pattern.finditer(text):
        rhs = match.group(1)
        for token in _FLOAT_RE.findall(rhs):
            try:
                values.append(float(token))
            except ValueError:
                continue
    return values


def _to_float_or_nan(value: Optional[float]) -> float:
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def run_ffmpeg_ebur128(audio_file: Union[str, Path], timeout_sec: int = 120) -> str:
    """
    Run ffmpeg with ebur128 filter and return stderr text.

    Command:
    ffmpeg -i file -filter_complex "ebur128=peak=true:framelog=verbose" -f null -
    """
    ffmpeg_path = _find_ffmpeg()
    if not ffmpeg_path:
        raise FileNotFoundError("ffmpeg not found in PATH or known bundle locations")

    input_path = Path(audio_file)
    cmd = [
        ffmpeg_path,
        "-loglevel",
        "verbose",
        "-hide_banner",
        "-i",
        str(input_path),
        "-filter_complex",
        "ebur128=peak=true:framelog=verbose",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    return result.stderr or ""


def parse_ffmpeg_metrics(stderr_text: str) -> Dict[str, Optional[float]]:
    """
    Parse ffmpeg ebur128 text for:
    - integrated_lufs (I)
    - lra (LRA)
    - true_peak (TPK/FTPK/summary true peak)
    - short_term_max (max S)
    - momentary_max (max M)
    """
    parsed = {
        "integrated_lufs": None,
        "lra": None,
        "true_peak": None,
        "short_term_max": None,
        "momentary_max": None,
        "momentary_min": None,
    }
    if not stderr_text:
        return parsed

    summary = _extract_last_summary_block(stderr_text)

    integrated = _extract_last_value(summary, r"^\s*I:\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*LUFS\b")
    if integrated is None:
        integrated = _extract_last_value(stderr_text, r"^\s*I:\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*LUFS\b")
    parsed["integrated_lufs"] = integrated

    lra = _extract_last_value(summary, r"^\s*LRA:\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*LU\b")
    if lra is None:
        lra = _extract_last_value(stderr_text, r"^\s*LRA:\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*LU\b")
    parsed["lra"] = lra

    # Collect values from both summary and live lines.
    tpk_values = _extract_marker_values(summary, "TPK") + _extract_marker_values(stderr_text, "TPK")
    ftpk_values = _extract_marker_values(summary, "FTPK") + _extract_marker_values(stderr_text, "FTPK")

    summary_true_peak = _extract_last_value(
        summary,
        r"True\s+peak:\s*(?:\n|\r\n|\r)\s*Peak:\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*dBFS\b",
    )
    if ftpk_values:
        parsed["true_peak"] = max(ftpk_values)
    elif tpk_values:
        parsed["true_peak"] = max(tpk_values)
    elif summary_true_peak is not None:
        parsed["true_peak"] = summary_true_peak

    # Filter out silence values (-120.7 is ffmpeg's floor for -inf)
    s_values = [v for v in _extract_marker_values(stderr_text, "S") if math.isfinite(v) and v > -120]
    m_values = [v for v in _extract_marker_values(stderr_text, "M") if math.isfinite(v) and v > -120]
    if s_values:
        parsed["short_term_max"] = max(s_values)
    if m_values:
        parsed["momentary_max"] = max(m_values)
        parsed["momentary_min"] = min(m_values)

    return parsed


def parse_ebur128_output(stderr_text: str) -> Dict[str, Optional[float]]:
    """
    Backward-compatible parser used by existing code/tests.

    Returns:
    - integrated_lufs (I)
    - lra_lu (LRA)
    - true_peak_dbtp (max of FTPK/TPK or summary True peak)
    - sample_peak_dbfs (max of SPK/summary Sample peak)
    - tpk_dbtp / ftpk_dbtp for debug
    """
    parsed = {
        "integrated_lufs": None,
        "lra_lu": None,
        "true_peak_dbtp": None,
        "sample_peak_dbfs": None,
        "tpk_dbtp": None,
        "ftpk_dbtp": None,
        "short_term_max": None,
        "momentary_max": None,
        "momentary_min": None,
    }
    if not stderr_text:
        return parsed

    summary = _extract_last_summary_block(stderr_text)
    parsed_new = parse_ffmpeg_metrics(stderr_text)
    parsed["integrated_lufs"] = parsed_new["integrated_lufs"]
    parsed["lra_lu"] = parsed_new["lra"]
    parsed["true_peak_dbtp"] = parsed_new["true_peak"]
    parsed["short_term_max"] = parsed_new["short_term_max"]
    parsed["momentary_max"] = parsed_new["momentary_max"]
    parsed["momentary_min"] = parsed_new["momentary_min"]

    tpk_values = _extract_marker_values(summary, "TPK") + _extract_marker_values(stderr_text, "TPK")
    ftpk_values = _extract_marker_values(summary, "FTPK") + _extract_marker_values(stderr_text, "FTPK")
    if tpk_values:
        parsed["tpk_dbtp"] = max(tpk_values)
    if ftpk_values:
        parsed["ftpk_dbtp"] = max(ftpk_values)

    spk_values = _extract_marker_values(summary, "SPK") or _extract_marker_values(stderr_text, "SPK")
    summary_sample_peak = _extract_last_value(
        summary,
        r"Sample\s+peak:\s*(?:\n|\r\n|\r)\s*Peak:\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*dBFS\b",
    )
    sample_candidates = list(spk_values)
    if summary_sample_peak is not None:
        sample_candidates.append(summary_sample_peak)
    if sample_candidates:
        parsed["sample_peak_dbfs"] = max(sample_candidates)

    return parsed


def _run_ffmpeg_ebur128(file_path: Path, ffmpeg_path: Optional[str], timeout_sec: int = 120) -> Dict[str, Optional[float]]:
    if not ffmpeg_path:
        logger.warning("ffmpeg not found: LRA/True Peak/Sample Peak will be None")
        return {
            "integrated_lufs": None,
            "lra_lu": None,
            "true_peak_dbtp": None,
            "sample_peak_dbfs": None,
            "tpk_dbtp": None,
            "ftpk_dbtp": None,
        }

    cmd = [
        ffmpeg_path,
        "-loglevel",
        "verbose",
        "-hide_banner",
        "-i",
        str(file_path),
        "-filter_complex",
        "ebur128=peak=true:framelog=verbose",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg ebur128 timeout for %s", file_path)
        return {
            "integrated_lufs": None,
            "lra_lu": None,
            "true_peak_dbtp": None,
            "sample_peak_dbfs": None,
            "tpk_dbtp": None,
            "ftpk_dbtp": None,
        }
    except Exception as exc:
        logger.warning("ffmpeg ebur128 failed for %s: %s", file_path, exc)
        return {
            "integrated_lufs": None,
            "lra_lu": None,
            "true_peak_dbtp": None,
            "sample_peak_dbfs": None,
            "tpk_dbtp": None,
            "ftpk_dbtp": None,
        }

    parsed = parse_ebur128_output(result.stderr or "")
    if result.returncode != 0:
        logger.warning("ffmpeg returned code %s for %s", result.returncode, file_path)
    return parsed


def generate_loudness_history(
    audio_data: "np.ndarray",
    sr: int,
    window_sec: float = 0.4,
    hop_sec: float = 0.4,
) -> Tuple["np.ndarray", "np.ndarray"]:
    """
    Generate loudness history using pyloudnorm over 400ms windows.
    """
    _ensure_audio_libs()
    if sr <= 0:
        raise ValueError("sr must be > 0")
    if window_sec <= 0 or hop_sec <= 0:
        raise ValueError("window_sec and hop_sec must be > 0")

    audio = _prepare_for_pyloudnorm(_normalize_audio_shape(audio_data))
    total_samples = audio.shape[0]
    if total_samples == 0:
        return np.asarray([], dtype=np.float64), np.asarray([], dtype=np.float64)

    window_samples = max(int(round(window_sec * sr)), 1)
    hop_samples = max(int(round(hop_sec * sr)), 1)
    meter = pyln.Meter(sr, block_size=window_sec)

    times: list[float] = []
    loudness_values: list[float] = []

    for start in range(0, total_samples, hop_samples):
        end = min(start + window_samples, total_samples)
        segment = audio[start:end]
        if end - start < window_samples:
            if audio.ndim == 1:
                segment = np.pad(segment, (0, window_samples - (end - start)))
            else:
                segment = np.pad(segment, ((0, window_samples - (end - start)), (0, 0)))
        try:
            value = float(meter.integrated_loudness(segment))
            if not math.isfinite(value):
                value = float("nan")
        except Exception:
            value = float("nan")

        center_time = (start + window_samples * 0.5) / float(sr)
        times.append(center_time)
        loudness_values.append(value)

    return np.asarray(times, dtype=np.float64), np.asarray(loudness_values, dtype=np.float64)


def _format_time_label(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _build_time_ticks(duration_sec: float, count: int = 21) -> list[float]:
    if duration_sec <= 0:
        return [0.0]
    tick_count = max(2, count)
    step = duration_sec / float(tick_count - 1)
    return [step * i for i in range(tick_count)]


def _format_duration_label(duration_sec: float) -> str:
    total = max(0, int(round(duration_sec)))
    minutes, secs = divmod(total, 60)
    return f"{minutes}m {secs:02d}s"


def _extract_date_from_filename(audio_file: str) -> str:
    stem = Path(audio_file).stem
    match = re.search(r"(20\d{2})[_.-](\d{2})[_.-](\d{2})", stem)
    if match:
        year, month, day = match.group(1), match.group(2), match.group(3)
        return f"{day}.{month}.{year}"
    return datetime.now().strftime("%d.%m.%Y")


def _draw_metric_card(c, x: float, y: float, w: float, h: float, label: str, value: str, unit: str) -> None:
    from reportlab.pdfbase import pdfmetrics

    c.saveState()
    c.roundRect(x, y, w, h, 5, stroke=1, fill=0)

    c.setFont("Helvetica-Bold", 6.8)
    c.setFillColorRGB(0.33, 0.43, 0.52)
    c.drawString(x + 8, y + h - 14, label.upper())

    value_font_size = 19.0
    unit_font_size = 8.8
    value_x = x + 8
    value_y = y + h - 40

    unit_width = pdfmetrics.stringWidth(unit, "Helvetica-Bold", unit_font_size) if unit else 0.0
    max_value_width = max(16.0, (w - 16.0) - (unit_width + (4.0 if unit else 0.0)))
    while value_font_size > 9.0 and pdfmetrics.stringWidth(value, "Helvetica-Bold", value_font_size) > max_value_width:
        value_font_size -= 0.5

    c.setFont("Helvetica-Bold", value_font_size)
    c.setFillColorRGB(0.46, 0.52, 0.56)
    c.drawString(value_x, value_y, value)

    if unit:
        unit_x = value_x + pdfmetrics.stringWidth(value, "Helvetica-Bold", value_font_size) + 4
        c.setFont("Helvetica-Bold", unit_font_size)
        c.setFillColorRGB(0.31, 0.58, 0.53)
        c.drawString(unit_x, value_y + 1, unit)

    c.restoreState()


def _resample_loudness_for_plot(
    times_sec: np.ndarray,
    history_lufs: np.ndarray,
    max_points: int,
) -> Tuple[np.ndarray, np.ndarray]:
    if times_sec.size == 0 or history_lufs.size == 0 or max_points <= 0:
        return np.asarray([], dtype=np.float64), np.asarray([], dtype=np.float64)

    finite_mask = np.isfinite(times_sec) & np.isfinite(history_lufs)
    if not finite_mask.any():
        return np.asarray([], dtype=np.float64), np.asarray([], dtype=np.float64)

    t = np.asarray(times_sec[finite_mask], dtype=np.float64)
    v = np.asarray(history_lufs[finite_mask], dtype=np.float64)
    if t.size == 0:
        return np.asarray([], dtype=np.float64), np.asarray([], dtype=np.float64)

    order = np.argsort(t)
    t = t[order]
    v = v[order]

    t_unique, unique_idx = np.unique(t, return_index=True)
    v_unique = v[unique_idx]
    if t_unique.size <= 2:
        return t_unique, v_unique

    points = min(max_points, int(t_unique.size))
    if points < t_unique.size:
        t_new = np.linspace(float(t_unique[0]), float(t_unique[-1]), points)
        v_new = np.interp(t_new, t_unique, v_unique)
    else:
        t_new = t_unique
        v_new = v_unique

    # Light smoothing removes jittery visual artifacts on long programs.
    if v_new.size >= 5:
        kernel = np.ones(5, dtype=np.float64) / 5.0
        v_new = np.convolve(v_new, kernel, mode="same")

    return t_new.astype(np.float64), v_new.astype(np.float64)


def _draw_loudness_graph(
    c,
    graph_x: float,
    graph_y: float,
    graph_w: float,
    graph_h: float,
    times_sec: np.ndarray,
    history_lufs: np.ndarray,
    integrated_lufs: float,
) -> None:
    _ = integrated_lufs
    target_lufs = -23.0
    y_top = 0.0
    y_bottom = -55.0
    y_span = y_top - y_bottom
    duration_sec = max(float(times_sec[-1]) if times_sec.size else 0.0, 1.0)

    plot_x = graph_x + 36
    plot_y = graph_y + 28
    plot_w = graph_w - 48
    plot_h = graph_h - 36
    y_floor = plot_y

    def map_x(t: float) -> float:
        return plot_x + (max(0.0, min(t, duration_sec)) / duration_sec) * plot_w

    def map_y(v: float) -> float:
        clipped = max(y_bottom, min(y_top, v))
        return plot_y + ((clipped - y_bottom) / y_span) * plot_h

    c.saveState()
    c.setFillColorRGB(0.008, 0.067, 0.141)
    c.setStrokeColorRGB(0.42, 0.50, 0.58)
    c.roundRect(graph_x, graph_y, graph_w, graph_h, 5, stroke=1, fill=1)

    # Vertical grid (time)
    c.setStrokeColorRGB(0.05, 0.16, 0.28)
    c.setLineWidth(0.5)
    for t in _build_time_ticks(duration_sec, count=20):
        x = map_x(t)
        c.line(x, plot_y, x, plot_y + plot_h)

    # Horizontal grid (loudness)
    y_ticks = [0, -3, -6, -9, -18, -23, -27, -36, -45, -54]
    for tick in y_ticks:
        y = map_y(float(tick))
        if tick in (0, -18, -36, -54):
            c.setStrokeColorRGB(0.08, 0.22, 0.36)
            c.setLineWidth(0.9)
        else:
            c.setStrokeColorRGB(0.05, 0.16, 0.28)
            c.setLineWidth(0.5)
        c.line(plot_x, y, plot_x + plot_w, y)
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColorRGB(0.96, 0.80, 0.22) if tick == -23 else c.setFillColorRGB(0.22, 0.38, 0.54)
        c.drawRightString(plot_x - 6, y - 3, str(tick))

    # Target line (-23 LUFS)
    y_target = map_y(target_lufs)
    c.setStrokeColorRGB(0.96, 0.81, 0.25)
    c.setLineWidth(0.8)
    c.line(plot_x, y_target, plot_x + plot_w, y_target)

    if times_sec.size and history_lufs.size:
        sampled_t, sampled_v = _resample_loudness_for_plot(
            times_sec,
            history_lufs,
            max_points=max(120, int(plot_w / 2)),
        )
        c.setLineWidth(1.4)
        for t, v in zip(sampled_t.tolist(), sampled_v.tolist()):
            x = map_x(float(t))
            y = map_y(float(v))
            if v >= target_lufs:
                c.setStrokeColorRGB(0.95, 0.18, 0.14)
            else:
                c.setStrokeColorRGB(0.02, 0.80, 0.74)
            c.line(x, y_floor, x, y)

    # X axis time labels.
    c.setFillColorRGB(0.18, 0.33, 0.48)
    c.setFont("Helvetica-Bold", 7.2)
    for t in _build_time_ticks(duration_sec, count=12):
        c.drawCentredString(map_x(t), graph_y + 11, _format_time_label(t))

    c.restoreState()


def create_pdf_report(
    audio_file: str,
    pdf_path: str,
    times_sec: np.ndarray,
    history_lufs: np.ndarray,
    metrics: Dict[str, float],
) -> None:
    """
    Create Youlean-style PDF with loudness history graph and right metrics column.
    """
    from reportlab.pdfgen import canvas

    pdf_file = Path(pdf_path)
    pdf_file.parent.mkdir(parents=True, exist_ok=True)

    # Match the provided design PDF: A4 landscape points.
    width, height = (842.0, 595.0)
    c = canvas.Canvas(str(pdf_file), pagesize=(width, height))

    # Light page background.
    c.setFillColorRGB(0.95, 0.95, 0.95)
    c.rect(0, 0, width, height, stroke=0, fill=1)

    # Header
    file_title = Path(audio_file).stem
    report_date = _extract_date_from_filename(audio_file)
    duration_label = _format_duration_label(_to_float_or_nan(metrics.get("duration_sec")))

    c.setFillColorRGB(0.18, 0.75, 0.66)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(30, 538, "LOUDNESS REPORT")

    c.setFillColorRGB(0.59, 0.62, 0.67)
    c.setFont("Helvetica-Bold", 31 / 2.6)
    c.drawString(30, 513, file_title)

    c.setFillColorRGB(0.60, 0.65, 0.71)
    c.setFont("Helvetica-Bold", 7)
    c.drawRightString(812, 538, "DATE")
    c.drawRightString(812, 496, "DURATION")
    c.setFillColorRGB(0.29, 0.38, 0.47)
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(812, 519, report_date)
    c.drawRightString(812, 478, duration_label)

    # Graph panel
    graph_x = 31.0
    graph_y = 154.0
    graph_w = 780.0
    graph_h = 356.0
    _draw_loudness_graph(
        c,
        graph_x,
        graph_y,
        graph_w,
        graph_h,
        times_sec,
        history_lufs,
        _to_float_or_nan(metrics.get("integrated_lufs")),
    )

    def fmt_value(value: float) -> str:
        if not math.isfinite(value):
            return "N/A"
        return f"{float(value):.2f}"

    integrated = _to_float_or_nan(metrics.get("integrated_lufs"))
    ffmpeg_integrated = _to_float_or_nan(metrics.get("ffmpeg_integrated_lufs"))
    true_peak = _to_float_or_nan(metrics.get("true_peak"))
    # Keep integrated loudness consistent: prefer pyloudnorm, ffmpeg only as fallback.
    integrated_best = integrated if math.isfinite(integrated) else ffmpeg_integrated
    plr = float("nan")
    if math.isfinite(integrated_best) and math.isfinite(true_peak):
        plr = true_peak - integrated_best

    cards = [
        ("INTEGRATED", fmt_value(integrated_best), "LUFS"),
        ("LOUDNESS RANGE", fmt_value(_to_float_or_nan(metrics.get("lra"))), "LU"),
        ("AVG DYNAMICS (PLR)", fmt_value(plr), "dB"),
        ("MOMENTARY MAX", fmt_value(_to_float_or_nan(metrics.get("momentary_max"))), "LUFS"),
        ("SHORT TERM MAX", fmt_value(_to_float_or_nan(metrics.get("short_term_max"))), "LUFS"),
        ("TRUE PEAK MAX", fmt_value(true_peak), "dBTP"),
    ]

    cards_x = 31.0
    cards_y = 28.0
    cards_h = 112.0
    gap = 8.0
    card_w = (780.0 - gap * (len(cards) - 1)) / len(cards)
    c.setStrokeColorRGB(0.42, 0.50, 0.58)
    c.setLineWidth(1.2)
    for idx, (label, value, unit) in enumerate(cards):
        x = cards_x + idx * (card_w + gap)
        _draw_metric_card(c, x, cards_y, card_w, cards_h, label, value, unit)

    c.showPage()
    c.save()


def _generate_loudness_history_streaming(
    audio_file: str,
    sample_rate: int,
    num_channels: int,
    window_sec: float = 0.4,
    hop_sec: float = 0.4,
) -> Tuple["np.ndarray", "np.ndarray"]:
    """
    Streaming-версия generate_loudness_history для multichannel файлов.
    Читает файл блоками через sf.SoundFile, не загружая целиком в RAM.
    """
    _ensure_audio_libs()
    window_samples = max(int(round(window_sec * sample_rate)), 1)
    meter = pyln.Meter(sample_rate, block_size=window_sec)

    times: list[float] = []
    loudness_values: list[float] = []
    sample_offset = 0

    with sf.SoundFile(audio_file, mode='r') as f:
        while True:
            block = f.read(frames=window_samples, dtype='float64', always_2d=True)
            if block.shape[0] == 0:
                break

            # Подготовка для pyloudnorm (drop LFE для 5.1)
            if block.shape[1] > 5:
                if block.shape[1] == 6:
                    block = block[:, [0, 1, 2, 4, 5]]
                else:
                    block = block[:, :5]

            # Pad если блок короче окна
            if block.shape[0] < window_samples:
                block = np.pad(block, ((0, window_samples - block.shape[0]), (0, 0)))

            try:
                value = float(meter.integrated_loudness(block))
                if not math.isfinite(value):
                    value = float("nan")
            except Exception:
                value = float("nan")

            center_time = (sample_offset + window_samples * 0.5) / float(sample_rate)
            times.append(center_time)
            loudness_values.append(value)
            sample_offset += block.shape[0] if block.shape[0] <= window_samples else window_samples

    return np.asarray(times, dtype=np.float64), np.asarray(loudness_values, dtype=np.float64)


def generate_loudness_report(audio_file: str, pdf_path: str) -> Dict[str, float]:
    """
    Generate loudness metrics and a Youlean-style PDF report.

    Для multichannel файлов (>2 каналов) использует streaming-режим:
    файл читается блоками, не загружается целиком в RAM.

    Returns:
        {
          "integrated_lufs": float,
          "lra": float,
          "true_peak": float,
          "short_term_max": float,
          "momentary_max": float,
          "duration_sec": float,
          "ffmpeg_integrated_lufs": float,
        }
    """
    _ensure_audio_libs()
    # Проверяем каналы перед загрузкой
    file_info = sf.info(str(audio_file))
    num_channels = file_info.channels
    sample_rate = int(file_info.samplerate)
    duration_sec = float(file_info.duration)
    use_streaming = num_channels > 2

    if use_streaming:
        logger.info(
            "generate_loudness_report: %d каналов → streaming mode (файл НЕ загружается целиком)",
            num_channels,
        )

    # --- ffmpeg метрики (streaming, работает для любого размера) ---
    ffmpeg_metrics: Dict[str, Optional[float]]
    try:
        ffmpeg_text = run_ffmpeg_ebur128(audio_file)
        ffmpeg_metrics = parse_ffmpeg_metrics(ffmpeg_text)
    except Exception as exc:
        logger.warning("ffmpeg ebur128 unavailable for %s: %s", audio_file, exc)
        ffmpeg_metrics = {
            "integrated_lufs": None,
            "lra": None,
            "true_peak": None,
            "short_term_max": None,
            "momentary_max": None,
        }

    if use_streaming:
        # --- Streaming path: LUFS из ffmpeg, история блоками ---
        integrated_lufs = _to_float_or_nan(ffmpeg_metrics.get("integrated_lufs"))
        times_sec, history_lufs = _generate_loudness_history_streaming(
            audio_file, sample_rate, num_channels, window_sec=0.4, hop_sec=0.4,
        )
    else:
        # --- Обычный path: загрузка + pyloudnorm ---
        audio, sample_rate, _ = _load_audio_input(audio_file, sr=None)
        pyloudnorm_audio = _prepare_for_pyloudnorm(audio)
        meter = pyln.Meter(sample_rate)
        integrated_lufs = float(meter.integrated_loudness(pyloudnorm_audio))
        times_sec, history_lufs = generate_loudness_history(
            pyloudnorm_audio, sample_rate, window_sec=0.4, hop_sec=0.4,
        )

    # Precise True Peak (ffmpeg 8.0 ebur128 некорректен для multichannel)
    true_peak = _to_float_or_nan(ffmpeg_metrics.get("true_peak"))
    try:
        tp_result = measure_true_peak_precise(audio_file, oversampling=4)
        precise_tp = tp_result.get("true_peak_dbtp")
        if precise_tp is not None:
            true_peak = precise_tp
            logger.info(
                "Loudness report True Peak (precise): %.2f dBTP", precise_tp,
            )
    except Exception as exc:
        logger.warning("measure_true_peak_precise failed for loudness report: %s", exc)

    metrics: Dict[str, float] = {
        "integrated_lufs": integrated_lufs,
        "lra": _to_float_or_nan(ffmpeg_metrics.get("lra")),
        "true_peak": true_peak,
        "short_term_max": _to_float_or_nan(ffmpeg_metrics.get("short_term_max")),
        "momentary_max": _to_float_or_nan(ffmpeg_metrics.get("momentary_max")),
        "duration_sec": duration_sec,
        "ffmpeg_integrated_lufs": _to_float_or_nan(ffmpeg_metrics.get("integrated_lufs")),
    }

    create_pdf_report(audio_file, pdf_path, times_sec, history_lufs, metrics)
    return metrics


def analyze_audio_metrics(input_data: AudioInput, sr: Optional[int] = None) -> Dict[str, Optional[float]]:
    """
    Analyze audio metrics with pyloudnorm + ffmpeg ebur128.

    Для multichannel файлов (>2 каналов) использует ТОЛЬКО ffmpeg (streaming),
    чтобы не загружать весь файл в RAM (5.1 WAV 2.6GB → ~7GB float64).

    Args:
        input_data: np.ndarray + sr or file path
        sr: required when input_data is np.ndarray

    Returns:
        {
          integrated_lufs: float|None,
          lra_lu: float|None,
          true_peak_dbtp: float|None,
          sample_peak_dbfs: float|None,
          duration_sec: float|None,
          source: str,
        }
    """
    _ensure_audio_libs()
    ffmpeg_path = _find_ffmpeg()
    warnings = []

    # --- Для файлов: проверяем каналы ПЕРЕД загрузкой ---
    use_streaming = False
    if isinstance(input_data, (str, Path)):
        source_path = Path(input_data)
        try:
            file_info = sf.info(str(source_path))
            file_channels = file_info.channels
            file_duration = file_info.duration
            file_sr = file_info.samplerate
            if file_channels > 2:
                use_streaming = True
                logger.info(
                    "analyze_audio_metrics: %d каналов, streaming mode (ffmpeg only), "
                    "файл НЕ загружается в RAM",
                    file_channels,
                )
        except Exception:
            pass  # fallback к обычному пути

    if use_streaming:
        # --- Streaming path: ffmpeg для LUFS/LRA/S/M, precise TP для True Peak ---
        # ffmpeg 8.0 ebur128 измеряет True Peak только для первого канала в multichannel,
        # поэтому используем measure_true_peak_precise (ITU-R BS.1770-4, 4x oversampling).
        ffmpeg_metrics = _run_ffmpeg_ebur128(source_path, ffmpeg_path=ffmpeg_path)
        ffmpeg_available = ffmpeg_path is not None

        integrated_lufs = None
        if ffmpeg_metrics.get("integrated_lufs") is not None:
            integrated_lufs = float(ffmpeg_metrics["integrated_lufs"])

        # Precise True Peak (streaming, не загружает файл целиком)
        precise_tp = None
        try:
            tp_result = measure_true_peak_precise(source_path, oversampling=4)
            precise_tp = tp_result.get("true_peak_dbtp")
            logger.info(
                "Streaming True Peak (precise): %.2f dBTP, per-ch: %s",
                precise_tp if precise_tp is not None else float("nan"),
                tp_result.get("true_peak_per_channel"),
            )
        except Exception as exc:
            logger.warning("measure_true_peak_precise failed: %s", exc)
            warnings.append(f"precise_tp_failed: {exc}")
            precise_tp = ffmpeg_metrics.get("true_peak_dbtp")

        warnings.append("streaming_multichannel")
        if not ffmpeg_available:
            warnings.append("ffmpeg_unavailable")

        return {
            "integrated_lufs": integrated_lufs,
            "lra_lu": ffmpeg_metrics.get("lra_lu"),
            "true_peak_dbtp": precise_tp,
            "sample_peak_dbfs": ffmpeg_metrics.get("sample_peak_dbfs"),
            "short_term_max": ffmpeg_metrics.get("short_term_max"),
            "momentary_max": ffmpeg_metrics.get("momentary_max"),
            "momentary_min": ffmpeg_metrics.get("momentary_min"),
            "duration_sec": float(file_duration) if file_duration else None,
            "source": "ffmpeg+precise_tp-streaming" if ffmpeg_available else "precise_tp",
            "ffmpeg_i_lufs": ffmpeg_metrics.get("integrated_lufs"),
            "ffmpeg_tpk_dbtp": ffmpeg_metrics.get("tpk_dbtp"),
            "ffmpeg_ftpk_dbtp": ffmpeg_metrics.get("ftpk_dbtp"),
            "warnings": warnings,
        }

    # --- Обычный path: загрузка + pyloudnorm + ffmpeg ---
    audio, sample_rate, source_path = _load_audio_input(input_data, sr)
    duration_sec = float(audio.shape[0]) / float(sample_rate) if sample_rate else None

    pyloudnorm_audio = _prepare_for_pyloudnorm(audio)
    integrated_lufs: Optional[float] = None
    try:
        meter = pyln.Meter(sample_rate)
        integrated_lufs = float(meter.integrated_loudness(pyloudnorm_audio))
    except Exception as exc:
        logger.warning("pyloudnorm integrated loudness failed: %s", exc)
        warnings.append(f"pyloudnorm_failed: {exc}")

    temp_file: Optional[Path] = None
    try:
        ffmpeg_input = source_path
        if ffmpeg_input is None:
            with tempfile.NamedTemporaryFile(prefix="audio_metrics_", suffix=".wav", delete=False) as tmp:
                temp_file = Path(tmp.name)
            sf.write(str(temp_file), audio, sample_rate, subtype="PCM_24")
            ffmpeg_input = temp_file

        ffmpeg_metrics = _run_ffmpeg_ebur128(ffmpeg_input, ffmpeg_path=ffmpeg_path)
        ffmpeg_available = ffmpeg_path is not None
    finally:
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                logger.warning("Failed to delete temporary file: %s", temp_file)

    # Safety net for problematic multichannel pyloudnorm versions.
    if integrated_lufs is None and ffmpeg_metrics.get("integrated_lufs") is not None:
        integrated_lufs = float(ffmpeg_metrics["integrated_lufs"])
        warnings.append("integrated_lufs_fallback_from_ffmpeg")

    if not ffmpeg_available:
        warnings.append("ffmpeg_unavailable")

    # ffmpeg 8.0 ebur128 True Peak некорректен для multichannel (только 1-й канал).
    # Используем precise True Peak (ITU-R BS.1770-4, 4x oversampling) если доступен файл.
    true_peak_dbtp = ffmpeg_metrics.get("true_peak_dbtp")
    if source_path is not None:
        try:
            tp_result = measure_true_peak_precise(source_path, oversampling=4)
            precise_tp = tp_result.get("true_peak_dbtp")
            if precise_tp is not None:
                true_peak_dbtp = precise_tp
                logger.info(
                    "True Peak (precise): %.2f dBTP, per-ch: %s",
                    precise_tp,
                    tp_result.get("true_peak_per_channel"),
                )
        except Exception as exc:
            logger.warning("measure_true_peak_precise failed, using ffmpeg: %s", exc)
            warnings.append(f"precise_tp_failed: {exc}")

    return {
        "integrated_lufs": integrated_lufs,
        "lra_lu": ffmpeg_metrics.get("lra_lu"),
        "true_peak_dbtp": true_peak_dbtp,
        "sample_peak_dbfs": ffmpeg_metrics.get("sample_peak_dbfs"),
        "short_term_max": ffmpeg_metrics.get("short_term_max"),
        "momentary_max": ffmpeg_metrics.get("momentary_max"),
        "momentary_min": ffmpeg_metrics.get("momentary_min"),
        "duration_sec": duration_sec,
        "source": "pyloudnorm+precise_tp+ffmpeg" if ffmpeg_available else "pyloudnorm+precise_tp",
        "ffmpeg_i_lufs": ffmpeg_metrics.get("integrated_lufs"),
        "ffmpeg_tpk_dbtp": ffmpeg_metrics.get("tpk_dbtp"),
        "ffmpeg_ftpk_dbtp": ffmpeg_metrics.get("ftpk_dbtp"),
        "warnings": warnings,
    }


def _true_peak_channel_chunked(
    channel_data: np.ndarray,
    oversampling: int,
    chunk_samples: int,
) -> float:
    """
    True Peak одного канала с чанковой обработкой (экономия RAM).

    Файл обрабатывается блоками по chunk_samples, в каждом блоке делается
    oversampling через resample_poly, ищется максимум.  Между чанками
    добавляется overlap чтобы не потерять inter-sample peak на границе.
    """
    from scipy.signal import resample_poly

    total = len(channel_data)
    # Overlap = длина FIR фильтра resample_poly (~10 * oversampling),
    # берём с запасом 256 сэмплов.
    overlap = min(256, chunk_samples // 4)
    peak_linear = 0.0

    start = 0
    while start < total:
        end = min(start + chunk_samples, total)
        # Берём чанк с overlap от предыдущего (кроме первого)
        read_start = max(0, start - overlap) if start > 0 else 0
        chunk = channel_data[read_start:end]

        upsampled = resample_poly(chunk, oversampling, 1)
        chunk_peak = float(np.max(np.abs(upsampled)))
        if chunk_peak > peak_linear:
            peak_linear = chunk_peak

        del upsampled  # сразу освобождаем память

        start = end

    if peak_linear > 0:
        return round(20.0 * np.log10(peak_linear), 2)
    return float("-inf")


def measure_true_peak_precise(
    input_data: AudioInput,
    sr: Optional[int] = None,
    oversampling: int = 4,
    chunk_sec: float = 30.0,
) -> Dict[str, Optional[float]]:
    """
    Точное измерение True Peak по ITU-R BS.1770-4 с oversampling.

    Возвращает значения с точностью до сотых (2 знака), аналогично iZotope RX.
    Использует scipy.signal.resample_poly для качественной интерполяции.

    Для файлов использует потоковое чтение (sf.SoundFile) — файл НИКОГДА не
    загружается целиком в RAM.  Для 5.1 WAV 2.6GB пиковое потребление ≈ 100-150MB.

    Args:
        input_data: путь к файлу или np.ndarray
        sr: sample rate (обязателен для ndarray)
        oversampling: кратность передискретизации (по умолчанию 4x по стандарту)
        chunk_sec: длина чанка в секундах (по умолчанию 30с)

    Returns:
        {
          "true_peak_dbtp": float — максимальный True Peak по всем каналам (dBTP),
          "true_peak_per_channel": list[float] — True Peak по каждому каналу,
          "peak_channel": int — индекс канала с максимальным пиком,
          "sample_rate": int,
          "oversampling": int,
        }
    """
    _ensure_audio_libs()
    # Для numpy-массивов — старый путь (данные уже в RAM)
    if not isinstance(input_data, (str, Path)):
        audio = _normalize_audio_shape(np.asarray(input_data))
        if sr is None:
            raise ValueError("sr must be provided when input is a numpy array")
        sample_rate = int(sr)
        if audio.ndim == 1:
            audio = audio[:, np.newaxis]
        num_channels = audio.shape[1]
        total_samples = audio.shape[0]
        chunk_samples = max(int(chunk_sec * sample_rate), sample_rate)
        logger.info(
            "True Peak precise (ndarray): %d ch, %d samples (%.1f s), %dx oversampling",
            num_channels, total_samples, total_samples / sample_rate, oversampling,
        )
        channel_peaks_db: list[float] = []
        for ch in range(num_channels):
            peak_db = _true_peak_channel_chunked(audio[:, ch], oversampling, chunk_samples)
            channel_peaks_db.append(peak_db)
            logger.debug("  ch %d: %.2f dBTP", ch, peak_db)
        max_peak = max(channel_peaks_db) if channel_peaks_db else None
        peak_channel = channel_peaks_db.index(max_peak) if max_peak is not None else 0
        return {
            "true_peak_dbtp": max_peak,
            "true_peak_per_channel": channel_peaks_db,
            "peak_channel": peak_channel,
            "sample_rate": sample_rate,
            "oversampling": oversampling,
        }

    # --- Потоковое чтение файла (не загружаем целиком) ---
    source_path = Path(input_data)
    return _measure_true_peak_streaming(source_path, oversampling, chunk_sec)


def _measure_true_peak_streaming(
    file_path: Path,
    oversampling: int = 4,
    chunk_sec: float = 30.0,
) -> Dict[str, Optional[float]]:
    """
    Потоковое измерение True Peak — файл читается блоками, никогда целиком.

    Пиковое потребление RAM ≈ chunk_sec * sr * channels * 8 байт + oversampled chunk.
    Для 30с × 48kHz × 6ch = ~69MB на чтение + ~46MB на upsample одного канала ≈ 115MB.
    """
    from scipy.signal import resample_poly

    with sf.SoundFile(str(file_path), mode='r') as f:
        sample_rate = f.samplerate
        num_channels = f.channels
        total_frames = f.frames
        chunk_frames = max(int(chunk_sec * sample_rate), sample_rate)
        # Overlap для resample_poly FIR фильтра (не терять inter-sample peaks на границах)
        overlap = min(256, chunk_frames // 4)

        logger.info(
            "True Peak precise (streaming): %s — %d ch, %d frames (%.1f s), "
            "chunk=%d frames, %dx oversampling",
            file_path.name, num_channels, total_frames,
            total_frames / sample_rate, chunk_frames, oversampling,
        )

        # Пики по каждому каналу (linear amplitude)
        channel_peaks_linear = [0.0] * num_channels

        # Буфер предыдущего overlap для каждого канала
        prev_overlap: list[Optional[np.ndarray]] = [None] * num_channels

        frames_read = 0
        chunk_idx = 0

        while frames_read < total_frames:
            # Сколько фреймов читать
            frames_to_read = min(chunk_frames, total_frames - frames_read)
            chunk_all = f.read(frames=frames_to_read, dtype='float64', always_2d=True)
            actual_read = chunk_all.shape[0]
            if actual_read == 0:
                break

            for ch in range(num_channels):
                channel_chunk = chunk_all[:, ch]

                # Добавляем overlap от предыдущего чанка
                if prev_overlap[ch] is not None:
                    channel_chunk = np.concatenate([prev_overlap[ch], channel_chunk])

                # Сохраняем overlap для следующего чанка
                if actual_read >= overlap:
                    prev_overlap[ch] = chunk_all[-overlap:, ch].copy()
                else:
                    prev_overlap[ch] = chunk_all[:, ch].copy()

                # Upsample и поиск пика
                upsampled = resample_poly(channel_chunk, oversampling, 1)
                chunk_peak = float(np.max(np.abs(upsampled)))
                if chunk_peak > channel_peaks_linear[ch]:
                    channel_peaks_linear[ch] = chunk_peak
                del upsampled

            del chunk_all
            frames_read += actual_read
            chunk_idx += 1

            if chunk_idx % 10 == 0:
                logger.debug(
                    "  streaming: %d/%d frames (%.0f%%)",
                    frames_read, total_frames, 100.0 * frames_read / total_frames,
                )

    # Конвертируем linear → dBTP
    channel_peaks_db: list[float] = []
    for ch, peak_lin in enumerate(channel_peaks_linear):
        if peak_lin > 0:
            db = round(20.0 * np.log10(peak_lin), 2)
        else:
            db = float("-inf")
        channel_peaks_db.append(db)
        logger.debug("  ch %d: %.2f dBTP", ch, db)

    max_peak = max(channel_peaks_db) if channel_peaks_db else None
    peak_channel = channel_peaks_db.index(max_peak) if max_peak is not None else 0

    logger.info(
        "True Peak precise result: %.2f dBTP (ch %d), per-channel: %s",
        max_peak if max_peak is not None else float("nan"),
        peak_channel,
        [f"{v:.2f}" for v in channel_peaks_db],
    )

    return {
        "true_peak_dbtp": max_peak,
        "true_peak_per_channel": channel_peaks_db,
        "peak_channel": peak_channel,
        "sample_rate": sample_rate,
        "oversampling": oversampling,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sr = 48000
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False, dtype=np.float64)
    tone = 0.1 * np.sin(2 * np.pi * 440 * t)
    metrics = analyze_audio_metrics(tone, sr=sr)
    print(metrics)
