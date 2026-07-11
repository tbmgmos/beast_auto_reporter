"""
Tests for audio_metrics helper.
"""

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from audio_metrics import (
    analyze_audio_metrics,
    generate_loudness_history,
    parse_ebur128_output,
    parse_ffmpeg_metrics,
)


def _make_stereo_signal(sr: int = 48000, duration_sec: float = 2.0) -> np.ndarray:
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False, dtype=np.float64)
    tone = 0.1 * np.sin(2 * np.pi * 440.0 * t)
    noise = 0.005 * np.random.randn(t.shape[0])
    left = tone + noise
    right = 0.9 * tone + 0.8 * noise
    return np.stack([left, right], axis=1).astype(np.float64)


def _make_51_signal(sr: int = 48000, duration_sec: float = 2.0) -> np.ndarray:
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False, dtype=np.float64)
    base = 0.1 * np.sin(2 * np.pi * 220.0 * t)
    return np.stack(
        [base, 0.9 * base, 0.7 * base, 0.3 * base, 0.6 * base, 0.5 * base],
        axis=1,
    ).astype(np.float64)


def test_analyze_audio_metrics_numpy_returns_expected_keys():
    sr = 48000
    audio = _make_stereo_signal(sr=sr, duration_sec=2.0)
    result = analyze_audio_metrics(audio, sr=sr)

    required = {
        "integrated_lufs",
        "lra_lu",
        "true_peak_dbtp",
        "sample_peak_dbfs",
        "duration_sec",
        "source",
    }
    assert required.issubset(result.keys())
    assert isinstance(result["duration_sec"], float)
    assert result["duration_sec"] == pytest.approx(2.0, abs=0.02)
    assert result["integrated_lufs"] is not None


def test_analyze_audio_metrics_supports_channels_first():
    sr = 48000
    audio = _make_stereo_signal(sr=sr, duration_sec=1.5)
    channels_first = audio.T  # [channels, samples]
    result = analyze_audio_metrics(channels_first, sr=sr)
    assert result["integrated_lufs"] is not None
    assert result["duration_sec"] == pytest.approx(1.5, abs=0.02)


def test_analyze_audio_metrics_works_for_51_numpy():
    sr = 48000
    audio_51 = _make_51_signal(sr=sr, duration_sec=2.0)
    result = analyze_audio_metrics(audio_51, sr=sr)
    # 5.1 should not crash and should produce integrated loudness.
    assert result["integrated_lufs"] is not None
    assert result["duration_sec"] == pytest.approx(2.0, abs=0.02)

def test_parse_ebur128_summary_with_peak_only():
    sample = """
[Parsed_ebur128_0 @ 0x111] Summary:

  Integrated loudness:
    I:         -23.1 LUFS
    Threshold: -33.1 LUFS

  Loudness range:
    LRA:         7.2 LU
    Threshold: -43.0 LUFS
    LRA low:   -27.0 LUFS
    LRA high:  -19.8 LUFS

  True peak:
    Peak:      -1.6 dBFS
"""
    parsed = parse_ebur128_output(sample)
    assert parsed["integrated_lufs"] == -23.1
    assert parsed["lra_lu"] == 7.2
    assert parsed["true_peak_dbtp"] == -1.6


def test_parse_ffmpeg_metrics_extracts_momentary_and_short_term_max():
    sample = """
[Parsed_ebur128_0 @ 0x111] t: 0.4    TARGET:-23 LUFS    M: -18.3 S: -22.0     I: -70.0 LUFS       LRA:   0.0 LU  FTPK: -6.9 dBFS  TPK: -6.9 dBFS
[Parsed_ebur128_0 @ 0x111] t: 0.8    TARGET:-23 LUFS    M: -15.2 S: -19.1     I: -21.0 LUFS       LRA:   1.1 LU  FTPK: -4.0 dBFS  TPK: -4.0 dBFS
[Parsed_ebur128_0 @ 0x111] Summary:
  Integrated loudness:
    I:         -23.0 LUFS
  Loudness range:
    LRA:        12.7 LU
"""
    parsed = parse_ffmpeg_metrics(sample)
    assert parsed["integrated_lufs"] == -23.0
    assert parsed["lra"] == 12.7
    assert parsed["true_peak"] == -4.0
    assert parsed["short_term_max"] == -19.1
    assert parsed["momentary_max"] == -15.2


def test_parse_ffmpeg_metrics_prefers_ftpk_for_true_peak_51():
    sample = """
[Parsed_ebur128_0 @ 0x111] t: 0.4 TARGET:-23 LUFS M:-18.3 S:-22.0 I:-70.0 LUFS LRA:0.0 LU FTPK: -9.9 -8.8 -7.7 -inf -6.6 -5.5 dBFS TPK: -10.0 -9.0 -8.0 -inf -7.0 -6.0 dBFS
[Parsed_ebur128_0 @ 0x111] t: 0.8 TARGET:-23 LUFS M:-15.2 S:-19.1 I:-21.0 LUFS LRA:1.1 LU FTPK: -4.4 -4.3 -4.2 -inf -4.1 -4.0 dBFS TPK: -4.8 -4.7 -4.6 -inf -4.5 -4.4 dBFS
[Parsed_ebur128_0 @ 0x111] Summary:
  Integrated loudness:
    I: -23.0 LUFS
  Loudness range:
    LRA: 12.7 LU
  True peak:
    Peak: -4.9 dBFS
"""
    parsed = parse_ffmpeg_metrics(sample)
    # Must take the loudest value from FTPK across all channels/frames.
    assert parsed["true_peak"] == -4.0


def test_generate_loudness_history_uses_400ms_default():
    sr = 48000
    duration_sec = 2.0
    audio = _make_stereo_signal(sr=sr, duration_sec=duration_sec)
    times, values = generate_loudness_history(audio, sr)

    assert len(times) == len(values)
    assert len(times) == int(duration_sec / 0.4)
    assert np.isfinite(values).any()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available in PATH")
def test_analyze_audio_metrics_ffmpeg_values_from_file():
    sr = 48000
    audio = _make_stereo_signal(sr=sr, duration_sec=2.5)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        sf.write(str(tmp_path), audio, sr, subtype="PCM_24")
        result = analyze_audio_metrics(tmp_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    assert result["integrated_lufs"] is not None
    assert result["lra_lu"] is not None
    assert result["true_peak_dbtp"] is not None
