"""
Audio Analyzer Module

Гибридный аудио-анализ:
- Integrated loudness (LUFS) через pyloudnorm (ITU-R BS.1770)
- Loudness Range (LRA) и True Peak через ffmpeg ebur128=peak=true
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy imports for fast startup
np = None
pyln = None
sf = None


def _ensure_audio_libs():
    global np, pyln, sf
    if np is None:
        import numpy as _np
        import pyloudnorm as _pyln
        import soundfile as _sf
        np = _np
        pyln = _pyln
        sf = _sf


def _get_audio_metrics():
    try:
        from src.audio_metrics import analyze_audio_metrics, parse_ebur128_output as parse_ebur128_metrics
    except ImportError:
        from audio_metrics import analyze_audio_metrics, parse_ebur128_output as parse_ebur128_metrics
    return analyze_audio_metrics, parse_ebur128_metrics

class AudioAnalyzer:
    """Класс для анализа аудиофайлов"""

    def __init__(self, config: Dict = None):
        """
        Инициализация анализатора

        Args:
            config: Словарь с настройками из settings.yaml
        """
        self.config = config or {}
        self.audio_config = self.config.get("audio", {})

        self.sample_rate = int(self.audio_config.get("sample_rate", 48000))
        self.target_lufs = float(self.audio_config.get("target_lufs", -23.0))
        self.lufs_tolerance = float(self.audio_config.get("lufs_tolerance", 0.5))
        self.true_peak_threshold = float(self.audio_config.get("true_peak", -2.0))
        self.lra_max = float(self.audio_config.get("lra_max", 18.0))
        self.boundary_epsilon = float(self.audio_config.get("boundary_epsilon", 0.05))
        self.ffmpeg_timeout_sec = int(self.audio_config.get("ffmpeg_timeout_sec", 120))

        self._meter_cache: Dict = {}
        self.ffmpeg_path = self._find_ffmpeg()

        logger.info("AudioAnalyzer инициализирован")

    def _find_ffmpeg(self) -> Optional[str]:
        """Поиск ffmpeg в bundle и в PATH."""
        if getattr(sys, "frozen", False):
            bundle_dir = getattr(sys, "_MEIPASS", "")
            candidates = [
                os.path.join(bundle_dir, "ffmpeg", "ffmpeg"),
                os.path.abspath(os.path.join(bundle_dir, "..", "Frameworks", "ffmpeg", "ffmpeg")),
            ]
            for candidate in candidates:
                if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                    logger.info(f"✅ ffmpeg найден в bundle: {candidate}")
                    return candidate

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            logger.info(f"✅ ffmpeg найден в PATH: {ffmpeg}")
            return ffmpeg

        for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
            if Path(candidate).exists() and os.access(candidate, os.X_OK):
                logger.info(f"✅ ffmpeg найден: {candidate}")
                return candidate

        logger.warning("⚠️ ffmpeg не найден, LRA/True Peak будут считаться fallback методом")
        return None

    def _get_meter(self, sample_rate: int):
        """Кэшированный meter под конкретный sample rate."""
        _ensure_audio_libs()
        sr = int(sample_rate or self.sample_rate)
        if sr not in self._meter_cache:
            self._meter_cache[sr] = pyln.Meter(sr)
        return self._meter_cache[sr]

    @staticmethod
    def _normalize_value(value: Optional[float], decimals: int = 3) -> Optional[float]:
        """Преобразование к float с отбраковкой inf/nan."""
        if value is None:
            return None
        value = float(value)
        import math
        if not math.isfinite(value):
            return None
        return round(value, decimals)

    @staticmethod
    def _parse_numeric_token(token: str) -> Optional[float]:
        """Безопасный парсинг числа из ffmpeg-строки."""
        if token is None:
            return None
        try:
            value = float(token.strip())
        except ValueError:
            return None
        import math
        if not math.isfinite(value):
            return None
        return value

    def load_audio(self, file_path: str) -> Tuple:
        """
        Загрузка аудиофайла

        Args:
            file_path: Путь к аудиофайлу

        Returns:
            Tuple[audio_data, sample_rate]
        """
        try:
            _ensure_audio_libs()
            logger.info(f"Загрузка файла: {file_path}")
            data, sr = sf.read(file_path)

            # Конвертация в float32 если нужно
            if data.dtype != np.float32:
                data = data.astype(np.float32)

            # Проверка на моно/стерео/multi-channel
            if data.ndim == 1:
                logger.info("Обнаружен моно файл")
            else:
                logger.info(f"Обнаружен {data.shape[1]}-канальный файл")

            return data, int(sr)

        except Exception as e:
            logger.error(f"Ошибка загрузки файла {file_path}: {e}")
            raise

    def measure_lufs(self, audio_data, sample_rate: Optional[int] = None) -> Optional[float]:
        """
        Измерение интегральной громкости (LUFS) через pyloudnorm.
        """
        try:
            _ensure_audio_libs()
            work_audio = audio_data
            if isinstance(work_audio, np.ndarray) and work_audio.ndim == 2 and work_audio.shape[1] > 5:
                if work_audio.shape[1] == 6:
                    # pyloudnorm (в текущей версии) не принимает 6ch; LFE исключаем из integrated loudness.
                    work_audio = work_audio[:, [0, 1, 2, 4, 5]]
                    logger.info("LUFS compatibility: 5.1 detected, LFE dropped for pyloudnorm integrated")
                else:
                    work_audio = work_audio[:, :5]
                    logger.warning("LUFS compatibility: %s channels detected, using first 5 channels", audio_data.shape[1])

            meter = self._get_meter(sample_rate or self.sample_rate)
            loudness = meter.integrated_loudness(work_audio)
            loudness = self._normalize_value(loudness)
            logger.info(f"LUFS измерен (pyloudnorm): {loudness}")
            return loudness
        except Exception as e:
            logger.error(f"Ошибка измерения LUFS: {e}")
            return None

    def measure_true_peak(self, audio_data) -> Optional[float]:
        """
        Fallback измерение True Peak (dBTP) по сэмпловому пику.
        """
        try:
            _ensure_audio_libs()
            if audio_data.ndim == 1:
                peak = np.max(np.abs(audio_data))
            else:
                peak = np.max(np.abs(audio_data), axis=0).max()

            true_peak_db = 20 * np.log10(peak) if peak > 0 else None
            true_peak_db = self._normalize_value(true_peak_db)
            logger.info(f"TRUE PEAK измерен (fallback): {true_peak_db}")
            return true_peak_db
        except Exception as e:
            logger.error(f"Ошибка измерения TRUE PEAK: {e}")
            return None

    def measure_lra(self, audio_data, sample_rate: Optional[int] = None) -> Optional[float]:
        """
        Fallback измерение Loudness Range (LRA) через блочный анализ.
        """
        try:
            _ensure_audio_libs()
            sr = int(sample_rate or self.sample_rate)
            meter = self._get_meter(sr)

            block_size = int(0.4 * sr)
            if block_size <= 0:
                return None

            if audio_data.ndim == 1:
                blocks = [audio_data[i:i + block_size] for i in range(0, len(audio_data), block_size)]
            else:
                blocks = [audio_data[i:i + block_size, :] for i in range(0, len(audio_data), block_size)]

            block_loudness = []
            for block in blocks:
                if len(block) != block_size:
                    continue
                try:
                    loudness = meter.integrated_loudness(block)
                    if np.isfinite(loudness):
                        block_loudness.append(loudness)
                except Exception:
                    continue

            if not block_loudness:
                logger.warning("Не удалось рассчитать LRA (fallback)")
                return None

            p95 = np.percentile(block_loudness, 95)
            p10 = np.percentile(block_loudness, 10)
            lra = self._normalize_value(p95 - p10)
            logger.info(f"LRA измерен (fallback): {lra}")
            return lra

        except Exception as e:
            logger.error(f"Ошибка измерения LRA: {e}")
            return None

    @classmethod
    def parse_ebur128_output(cls, output: str) -> Dict[str, Optional[float]]:
        """
        Парсинг ffmpeg ebur128 вывода.

        Ищет I/LRA и значения True Peak (TPK/FTPK/Peak), возвращая числа с дробной частью.
        """
        _, parse_ebur128_metrics = _get_audio_metrics()
        parsed = parse_ebur128_metrics(output or "")
        peak_dbtp = parsed.get("sample_peak_dbfs")
        return {
            "i_lufs": parsed.get("integrated_lufs"),
            "lra": parsed.get("lra_lu"),
            "tpk_dbtp": parsed.get("tpk_dbtp"),
            "ftpk_dbtp": parsed.get("ftpk_dbtp"),
            "peak_dbtp": peak_dbtp,
            "true_peak_dbtp": parsed.get("true_peak_dbtp"),
        }

    def measure_lra_and_true_peak_ffmpeg(self, file_path: str) -> Dict[str, Optional[float]]:
        """
        Измерение LRA/True Peak через ffmpeg ebur128=peak=true:framelog=verbose.
        """
        empty_result = {
            "i_lufs": None,
            "lra": None,
            "tpk_dbtp": None,
            "ftpk_dbtp": None,
            "peak_dbtp": None,
            "true_peak_dbtp": None,
        }
        if not self.ffmpeg_path:
            return empty_result

        cmd = [
            self.ffmpeg_path,
            "-loglevel",
            "verbose",
            "-hide_banner",
            "-i",
            str(file_path),
            "-map",
            "0:a:0?",
            "-vn",
            "-sn",
            "-dn",
            "-filter:a",
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
                timeout=self.ffmpeg_timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"⚠️ ffmpeg ebur128 timeout ({self.ffmpeg_timeout_sec} c): {file_path}")
            return empty_result
        except Exception as e:
            logger.warning(f"⚠️ ffmpeg ebur128 failed для {file_path}: {e}")
            return empty_result

        output = f"{result.stderr or ''}\n{result.stdout or ''}"
        if result.returncode != 0:
            logger.warning(f"⚠️ ffmpeg завершился с кодом {result.returncode} для {file_path}")

        parsed = self.parse_ebur128_output(output)
        if not any(parsed.values()):
            logger.warning(f"⚠️ Не удалось распарсить ebur128 output для {file_path}")
        else:
            logger.info(
                "ffmpeg ebur128: I=%s LUFS, LRA=%s LU, TPK=%s dBTP, FTPK=%s dBTP",
                parsed.get("i_lufs"),
                parsed.get("lra"),
                parsed.get("tpk_dbtp"),
                parsed.get("ftpk_dbtp"),
            )
        return parsed

    def evaluate_compliance(
        self,
        lufs: Optional[float],
        true_peak: Optional[float],
        lra: Optional[float],
    ) -> Tuple[Dict[str, bool], Dict[str, Optional[float]], Dict[str, Optional[float]]]:
        """
        Проверка соответствия с явным epsilon-допуском для граничных значений.
        """
        lufs_deviation = None
        true_peak_excess = None
        lra_excess = None

        lufs_compliant = False
        true_peak_compliant = False
        lra_compliant = False

        lufs_borderline = False
        true_peak_borderline = False
        lra_borderline = False

        float_guard = 1e-9

        if lufs is not None:
            lufs_deviation = lufs - self.target_lufs
            distance = abs(lufs_deviation)
            lufs_compliant = distance <= (self.lufs_tolerance + self.boundary_epsilon + float_guard)
            lufs_borderline = abs(distance - self.lufs_tolerance) <= (self.boundary_epsilon + float_guard)

        if true_peak is not None:
            true_peak_excess = true_peak - self.true_peak_threshold
            true_peak_compliant = true_peak_excess <= (self.boundary_epsilon + float_guard)
            true_peak_borderline = abs(true_peak_excess) <= (self.boundary_epsilon + float_guard)

        if lra is not None:
            lra_excess = lra - self.lra_max
            lra_compliant = lra_excess <= (self.boundary_epsilon + float_guard)
            lra_borderline = abs(lra_excess) <= (self.boundary_epsilon + float_guard)

        compliance = {
            "lufs_compliant": lufs_compliant,
            "true_peak_compliant": true_peak_compliant,
            "lra_compliant": lra_compliant,
            "overall_compliant": all([lufs_compliant, true_peak_compliant, lra_compliant]),
        }

        details = {
            "boundary_epsilon": self.boundary_epsilon,
            "lufs_abs_delta": self._normalize_value(abs(lufs_deviation)) if lufs_deviation is not None else None,
            "true_peak_excess": self._normalize_value(true_peak_excess),
            "lra_excess": self._normalize_value(lra_excess),
            "lufs_is_borderline": lufs_borderline,
            "true_peak_is_borderline": true_peak_borderline,
            "lra_is_borderline": lra_borderline,
        }

        deviations = {
            "lufs_deviation": self._normalize_value(lufs_deviation),
            "true_peak_headroom": self._normalize_value(self.true_peak_threshold - true_peak) if true_peak is not None else None,
            "lra_headroom": self._normalize_value(self.lra_max - lra) if lra is not None else None,
        }

        return compliance, details, deviations

    def get_channel_layout(self, audio_data) -> str:
        """
        Определение конфигурации каналов

        Args:
            audio_data: Аудио данные

        Returns:
            Строка с описанием layout (например, "2.0", "5.1")
        """
        if audio_data.ndim == 1:
            return "1.0 (Mono)"
        channels = audio_data.shape[1]
        if channels == 2:
            return "2.0 (Stereo)"
        if channels == 6:
            return "5.1 (Surround)"
        if channels == 8:
            return "7.1 (Surround)"
        return f"{channels}.0 (Multi-channel)"

    def analyze_file(self, file_path: str) -> Dict:
        """
        Полный анализ аудиофайла

        Args:
            file_path: Путь к файлу

        Returns:
            Словарь с результатами анализа
        """
        try:
            _ensure_audio_libs()
            logger.info(f"Начало анализа файла: {file_path}")
            file_info = sf.info(file_path)
            sr = int(file_info.samplerate)
            channels = int(file_info.channels)
            duration = float(file_info.duration)

            if sr != self.sample_rate:
                logger.warning(
                    "Sample rate файла (%s) отличается от целевого (%s), LUFS считается в нативном SR файла",
                    sr,
                    self.sample_rate,
                )

            _analyze_audio_metrics, _ = _get_audio_metrics()
            metrics = _analyze_audio_metrics(file_path)
            lufs = metrics.get("integrated_lufs")
            true_peak = metrics.get("true_peak_dbtp")
            lra = metrics.get("lra_lu")

            # Strict EBU R128 mode: True Peak/LRA only from ffmpeg ebur128.
            if true_peak is None or lra is None:
                logger.warning(
                    "EBU R128 metrics incomplete for %s: true_peak=%s, lra=%s (no fallback applied)",
                    file_path,
                    true_peak,
                    lra,
                )

            _CHANNEL_INFO = {
                1: ("1.0 (Mono)", "C"),
                2: ("2.0 (Stereo)", "L R"),
                6: ("5.1 (Surround)", "L R C LFE Ls Rs"),
                8: ("7.1 (Surround)", "L R C LFE Ls Rs Lrs Rrs"),
            }
            if channels in _CHANNEL_INFO:
                channel_layout, channel_order = _CHANNEL_INFO[channels]
            else:
                channel_layout = f"{channels}.0 (Multi-channel)"
                channel_order = " ".join(f"Ch{i+1}" for i in range(channels))

            compliance, compliance_details, deviations = self.evaluate_compliance(lufs, true_peak, lra)

            results = {
                "file_path": file_path,
                "file_name": Path(file_path).name,
                "duration": round(duration, 2),
                "sample_rate": sr,
                "channels": channels,
                "channel_layout": channel_layout,
                "channel_order": channel_order,
                "analysis_method": "pyloudnorm+ffmpeg_ebur128",
                "measurements": {
                    "lufs": lufs,
                    "true_peak": true_peak,
                    "lra": lra,
                    "short_term_loudness": metrics.get("short_term_max"),
                    "momentary_loudness": metrics.get("momentary_max"),
                    "max_loudness": metrics.get("momentary_max"),
                    "min_loudness": metrics.get("momentary_min"),
                    "sample_peak_dbfs": metrics.get("sample_peak_dbfs"),
                    "ffmpeg_integrated_lufs": metrics.get("ffmpeg_i_lufs"),
                    "true_peak_tpk": metrics.get("ffmpeg_tpk_dbtp"),
                    "true_peak_ftpk": metrics.get("ffmpeg_ftpk_dbtp"),
                },
                "compliance": compliance,
                "compliance_details": compliance_details,
                "targets": {
                    "target_lufs": self.target_lufs,
                    "lufs_tolerance": self.lufs_tolerance,
                    "true_peak_threshold": self.true_peak_threshold,
                    "lra_max": self.lra_max,
                    "boundary_epsilon": self.boundary_epsilon,
                },
                "deviations": deviations,
            }

            logger.info("Анализ завершен успешно")
            return results

        except Exception as e:
            logger.error(f"Ошибка анализа файла {file_path}: {e}")
            raise

    def analyze_stereo_and_surround(
        self,
        stereo_path: str,
        surround_path: Optional[str] = None,
    ) -> Dict:
        """
        Анализ стерео (2.0) и surround (5.1) версий

        Args:
            stereo_path: Путь к стерео файлу
            surround_path: Путь к surround файлу (опционально)

        Returns:
            Словарь с результатами обоих анализов
        """
        results = {
            "stereo": None,
            "surround": None,
        }

        if stereo_path:
            logger.info("Анализ стерео версии...")
            results["stereo"] = self.analyze_file(stereo_path)

        if surround_path:
            logger.info("Анализ surround версии...")
            results["surround"] = self.analyze_file(surround_path)

        return results


if __name__ == "__main__":
    # Тестирование модуля
    logging.basicConfig(level=logging.INFO)
    
    # Пример использования
    config = {
        'audio': {
            'target_lufs': -23.0,
            'lufs_tolerance': 0.5,
            'true_peak': -2.0,
            'lra_max': 18.0,
            'sample_rate': 48000
        }
    }
    
    analyzer = AudioAnalyzer(config)
    
    # Тест с примером файла (замените на реальный путь)
    # results = analyzer.analyze_file("path/to/audio.wav")
    # print(results)
    
    print("AudioAnalyzer готов к использованию!")
