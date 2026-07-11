"""
Тесты для AudioAnalyzer
"""

import pytest
import numpy as np
import sys
from pathlib import Path

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from audio_analyzer import AudioAnalyzer


class TestAudioAnalyzer:
    """Тесты для класса AudioAnalyzer"""
    
    @pytest.fixture
    def analyzer(self):
        """Фикстура для создания анализатора"""
        config = {
            'audio': {
                'target_lufs': -23.0,
                'lufs_tolerance': 0.5,
                'true_peak': -2.0,
                'lra_max': 18.0,
                'sample_rate': 48000
            }
        }
        return AudioAnalyzer(config)
    
    @pytest.fixture
    def test_audio(self):
        """Создание тестового аудио сигнала"""
        sr = 48000
        duration = 5  # секунд
        t = np.linspace(0, duration, sr * duration)
        
        # Простой синусоидальный сигнал
        frequency = 440  # Hz (нота A)
        amplitude = 0.1  # -20 dBFS
        signal = amplitude * np.sin(2 * np.pi * frequency * t)
        
        return signal.astype(np.float32), sr
    
    def test_initialization(self, analyzer):
        """Тест инициализации анализатора"""
        assert analyzer.sample_rate == 48000
        assert analyzer.target_lufs == -23.0
        assert analyzer.lufs_tolerance == 0.5
        assert analyzer.true_peak_threshold == -2.0
    
    def test_measure_true_peak(self, analyzer, test_audio):
        """Тест измерения TRUE PEAK"""
        audio, sr = test_audio
        
        true_peak = analyzer.measure_true_peak(audio)
        
        assert true_peak is not None
        assert isinstance(true_peak, float)
        assert true_peak < 0  # Должен быть отрицательным (в dB)
    
    def test_measure_lufs(self, analyzer, test_audio):
        """Тест измерения LUFS"""
        audio, sr = test_audio
        
        lufs = analyzer.measure_lufs(audio)
        
        assert lufs is not None
        assert isinstance(lufs, float)
        # LUFS обычно в диапазоне -60 до 0
        assert -60 < lufs < 0
    
    def test_get_channel_layout_mono(self, analyzer):
        """Тест определения моно layout"""
        mono_audio = np.random.randn(48000).astype(np.float32)
        
        layout = analyzer.get_channel_layout(mono_audio)
        
        assert "Mono" in layout
    
    def test_get_channel_layout_stereo(self, analyzer):
        """Тест определения стерео layout"""
        stereo_audio = np.random.randn(48000, 2).astype(np.float32)
        
        layout = analyzer.get_channel_layout(stereo_audio)
        
        assert "Stereo" in layout or "2.0" in layout
    
    def test_get_channel_layout_51(self, analyzer):
        """Тест определения 5.1 layout"""
        surround_audio = np.random.randn(48000, 6).astype(np.float32)
        
        layout = analyzer.get_channel_layout(surround_audio)
        
        assert "5.1" in layout

    def test_parse_ebur128_output(self):
        """Парсер ebur128 должен извлекать I/LRA/TPK/FTPK."""
        sample_output = """
[Parsed_ebur128_0 @ 0x111111] Summary:
  Integrated loudness:
    I:         -23.04 LUFS
    Threshold: -33.04 LUFS

  Loudness range:
    LRA:         6.27 LU
    Threshold: -43.33 LUFS
    LRA low:   -25.77 LUFS
    LRA high:  -19.50 LUFS

  True peak:
    TPK:       -2.40 dBFS
    FTPK:      -2.12 dBFS
"""
        parsed = AudioAnalyzer.parse_ebur128_output(sample_output)
        assert parsed["i_lufs"] == -23.04
        assert parsed["lra"] == 6.27
        assert parsed["tpk_dbtp"] == -2.4
        assert parsed["ftpk_dbtp"] == -2.12
        assert parsed["true_peak_dbtp"] == -2.12

    def test_evaluate_compliance_with_boundary_epsilon(self):
        """Граничные значения должны проходить по epsilon допуску."""
        config = {
            'audio': {
                'target_lufs': -23.0,
                'lufs_tolerance': 0.5,
                'true_peak': -2.0,
                'lra_max': 18.0,
                'boundary_epsilon': 0.05,
                'sample_rate': 48000
            }
        }
        analyzer = AudioAnalyzer(config)

        compliance, details, deviations = analyzer.evaluate_compliance(
            lufs=-22.45,      # abs delta = 0.55 (0.5 + epsilon)
            true_peak=-1.96,  # excess = 0.04 (inside epsilon)
            lra=18.03         # excess = 0.03 (inside epsilon)
        )

        assert compliance["lufs_compliant"] is True
        assert compliance["true_peak_compliant"] is True
        assert compliance["lra_compliant"] is True
        assert compliance["overall_compliant"] is True

        assert details["lufs_is_borderline"] is True
        assert details["true_peak_is_borderline"] is True
        assert details["lra_is_borderline"] is True

        assert deviations["lufs_deviation"] == 0.55


if __name__ == "__main__":
    # Запуск тестов
    pytest.main([__file__, '-v'])
