"""
Audio Analyzer Module

Анализирует аудиофайлы и измеряет:
- LUFS (Loudness Units Full Scale)
- TRUE PEAK (dBTP)
- LRA (Loudness Range)
"""

import numpy as np
import soundfile as sf
import pyloudnorm as pyln
from pathlib import Path
from typing import Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class AudioAnalyzer:
    """Класс для анализа аудиофайлов"""
    
    def __init__(self, config: Dict = None):
        """
        Инициализация анализатора
        
        Args:
            config: Словарь с настройками из settings.yaml
        """
        self.config = config or {}
        self.sample_rate = self.config.get('audio', {}).get('sample_rate', 48000)
        self.target_lufs = self.config.get('audio', {}).get('target_lufs', -23.0)
        self.lufs_tolerance = self.config.get('audio', {}).get('lufs_tolerance', 0.5)
        self.true_peak_threshold = self.config.get('audio', {}).get('true_peak', -2.0)
        self.lra_max = self.config.get('audio', {}).get('lra_max', 18.0)
        
        # Инициализация meter для LUFS измерений
        self.meter = pyln.Meter(self.sample_rate)
        
        logger.info("AudioAnalyzer инициализирован")
    
    def load_audio(self, file_path: str) -> Tuple[np.ndarray, int]:
        """
        Загрузка аудиофайла
        
        Args:
            file_path: Путь к аудиофайлу
            
        Returns:
            Tuple[audio_data, sample_rate]
        """
        try:
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
            
            return data, sr
            
        except Exception as e:
            logger.error(f"Ошибка загрузки файла {file_path}: {e}")
            raise
    
    def measure_lufs(self, audio_data: np.ndarray) -> float:
        """
        Измерение интегральной громкости (LUFS)
        
        Args:
            audio_data: Аудио данные
            
        Returns:
            LUFS значение в dB
        """
        try:
            loudness = self.meter.integrated_loudness(audio_data)
            logger.info(f"LUFS измерен: {loudness:.2f} dB")
            return round(loudness, 2)
        except Exception as e:
            logger.error(f"Ошибка измерения LUFS: {e}")
            return None
    
    def measure_true_peak(self, audio_data: np.ndarray) -> float:
        """
        Измерение TRUE PEAK (dBTP)
        
        Args:
            audio_data: Аудио данные
            
        Returns:
            TRUE PEAK значение в dBTP
        """
        try:
            # Используем oversample factor 4x для точного измерения
            if audio_data.ndim == 1:
                # Моно
                peak = np.max(np.abs(audio_data))
            else:
                # Мульти-канал - ищем пик по всем каналам
                peak = np.max(np.abs(audio_data), axis=0).max()
            
            # Конвертация в dBTP
            true_peak_db = 20 * np.log10(peak) if peak > 0 else -np.inf
            logger.info(f"TRUE PEAK измерен: {true_peak_db:.2f} dBTP")
            return round(true_peak_db, 2)
        except Exception as e:
            logger.error(f"Ошибка измерения TRUE PEAK: {e}")
            return None
    
    def measure_lra(self, audio_data: np.ndarray) -> float:
        """
        Измерение Loudness Range (LRA)
        
        Args:
            audio_data: Аудио данные
            
        Returns:
            LRA значение в LU
        """
        try:
            # Используем pyloudnorm для расчета LRA
            # LRA = разница между 95-м и 10-м перцентилем
            
            # Разбиваем на блоки по 400ms (стандарт EBU)
            block_size = int(0.4 * self.sample_rate)
            
            if audio_data.ndim == 1:
                blocks = [audio_data[i:i+block_size] 
                         for i in range(0, len(audio_data), block_size)]
            else:
                blocks = [audio_data[i:i+block_size, :] 
                         for i in range(0, len(audio_data), block_size)]
            
            # Измеряем громкость каждого блока
            block_loudness = []
            for block in blocks:
                if len(block) == block_size:  # Только полные блоки
                    try:
                        loudness = self.meter.integrated_loudness(block)
                        if not np.isinf(loudness):
                            block_loudness.append(loudness)
                    except:
                        pass
            
            if len(block_loudness) > 0:
                # LRA = разница между 95-м и 10-м перцентилем
                p95 = np.percentile(block_loudness, 95)
                p10 = np.percentile(block_loudness, 10)
                lra = p95 - p10
                
                logger.info(f"LRA измерен: {lra:.2f} LU")
                return round(lra, 2)
            else:
                logger.warning("Не удалось рассчитать LRA")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка измерения LRA: {e}")
            return None
    
    def get_channel_layout(self, audio_data: np.ndarray) -> str:
        """
        Определение конфигурации каналов
        
        Args:
            audio_data: Аудио данные
            
        Returns:
            Строка с описанием layout (например, "2.0", "5.1")
        """
        if audio_data.ndim == 1:
            return "1.0 (Mono)"
        else:
            channels = audio_data.shape[1]
            if channels == 2:
                return "2.0 (Stereo)"
            elif channels == 6:
                return "5.1 (Surround)"
            elif channels == 8:
                return "7.1 (Surround)"
            else:
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
            logger.info(f"Начало анализа файла: {file_path}")
            
            # Загрузка файла
            audio_data, sr = self.load_audio(file_path)
            
            # Ресемплинг если нужно
            if sr != self.sample_rate:
                logger.warning(f"Sample rate {sr} != {self.sample_rate}, ресемплинг не реализован")
            
            # Измерения
            lufs = self.measure_lufs(audio_data)
            true_peak = self.measure_true_peak(audio_data)
            lra = self.measure_lra(audio_data)
            channel_layout = self.get_channel_layout(audio_data)
            
            # Проверка на соответствие стандартам
            lufs_compliant = (
                abs(lufs - self.target_lufs) <= self.lufs_tolerance 
                if lufs is not None else False
            )
            true_peak_compliant = (
                true_peak <= self.true_peak_threshold 
                if true_peak is not None else False
            )
            lra_compliant = (
                lra <= self.lra_max 
                if lra is not None else False
            )
            
            # Длительность
            duration = len(audio_data) / sr
            
            results = {
                "file_path": file_path,
                "file_name": Path(file_path).name,
                "duration": round(duration, 2),
                "sample_rate": sr,
                "channel_layout": channel_layout,
                "measurements": {
                    "lufs": lufs,
                    "true_peak": true_peak,
                    "lra": lra
                },
                "compliance": {
                    "lufs_compliant": lufs_compliant,
                    "true_peak_compliant": true_peak_compliant,
                    "lra_compliant": lra_compliant,
                    "overall_compliant": all([
                        lufs_compliant, 
                        true_peak_compliant, 
                        lra_compliant
                    ])
                },
                "targets": {
                    "target_lufs": self.target_lufs,
                    "lufs_tolerance": self.lufs_tolerance,
                    "true_peak_threshold": self.true_peak_threshold,
                    "lra_max": self.lra_max
                },
                "deviations": {
                    "lufs_deviation": round(lufs - self.target_lufs, 2) if lufs else None,
                    "true_peak_headroom": round(self.true_peak_threshold - true_peak, 2) if true_peak else None
                }
            }
            
            logger.info("Анализ завершен успешно")
            return results
            
        except Exception as e:
            logger.error(f"Ошибка анализа файла {file_path}: {e}")
            raise
    
    def analyze_stereo_and_surround(
        self, 
        stereo_path: str, 
        surround_path: Optional[str] = None
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
            "surround": None
        }
        
        # Анализ стерео
        if stereo_path:
            logger.info("Анализ стерео версии...")
            results["stereo"] = self.analyze_file(stereo_path)
        
        # Анализ surround
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

