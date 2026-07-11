"""
Beast Auto Reporter - Automatic Audio QC Report Generator

Heavy modules (numpy, scipy, librosa, matplotlib, etc.) are imported lazily
to keep application startup fast.
"""

__version__ = "1.0.0"
__author__ = "Beast Auto Reporter Team"


def __getattr__(name):
    """Lazy-load heavy submodules on first access."""
    if name == "AudioAnalyzer":
        from .audio_analyzer import AudioAnalyzer
        return AudioAnalyzer
    if name == "analyze_audio_metrics":
        from .audio_metrics import analyze_audio_metrics
        return analyze_audio_metrics
    if name == "DefectDetector":
        from .defect_detector import DefectDetector
        return DefectDetector
    if name == "ReportGenerator":
        from .report_generator import ReportGenerator
        return ReportGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AudioAnalyzer",
    "analyze_audio_metrics",
    "DefectDetector",
    "ReportGenerator",
]
