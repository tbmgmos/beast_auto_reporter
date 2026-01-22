"""
Defect Detector Module

Обнаруживает различные дефекты в аудио:
- Клипинг / перегруз
- Клики и треск
- Высокочастотное шипение
- Тишина / отсутствие звука
- Проблемы с каналами
"""

import numpy as np
import soundfile as sf
import librosa
from scipy import signal
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


@dataclass
class Defect:
    """Класс для хранения информации о дефекте"""
    type: str  # Тип дефекта
    timecode_in: str  # Начало в формате HH:MM:SS:FF
    timecode_out: Optional[str]  # Конец (если применимо)
    duration: float  # Длительность в секундах
    description: str  # Описание
    severity: str  # "blocker", "fix_required", "comment_required"
    channels: List[str]  # Затронутые каналы ["2.0", "5.1"]
    details: Dict  # Дополнительные детали


class DefectDetector:
    """Класс для детекции дефектов в аудио"""
    
    def __init__(self, config: Dict = None):
        """
        Инициализация детектора
        
        Args:
            config: Словарь с настройками из settings.yaml
        """
        self.config = config or {}
        detection_cfg = self.config.get('detection', {})
        
        # Пороги для детекции
        self.click_threshold = detection_cfg.get('click_threshold', 0.15)  # Увеличен с 0.05 до 0.15 для меньшего количества ложных срабатываний
        self.click_min_duration = detection_cfg.get('click_min_duration', 0.001)
        
        self.noise_threshold = detection_cfg.get('noise_threshold', -60)
        self.noise_freq_range = detection_cfg.get('noise_frequency_range', [8000, 20000])
        
        self.clipping_threshold = detection_cfg.get('clipping_threshold', -1.0)
        self.clipping_duration = detection_cfg.get('clipping_duration', 0.1)
        
        self.silence_threshold = detection_cfg.get('silence_threshold', -70)
        self.silence_duration = detection_cfg.get('silence_duration', 1.0)
        
        self.check_channel_order = detection_cfg.get('check_channel_order', True)
        self.expected_channels = detection_cfg.get('expected_channel_order', 
                                                   ["L", "R", "C", "LFE", "Ls", "Rs"])
        
        self.sample_rate = self.config.get('audio', {}).get('sample_rate', 48000)
        self.defects = []
        
        logger.info("DefectDetector инициализирован")
    
    def seconds_to_timecode(self, seconds: float, fps: int = 25) -> str:
        """
        Конвертация секунд в таймкод HH:MM:SS:FF
        
        Args:
            seconds: Время в секундах
            fps: Кадров в секунду (по умолчанию 25)
            
        Returns:
            Строка таймкода
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        frames = int((seconds % 1) * fps)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"
    
    def detect_clipping(self, audio_data: np.ndarray, sr: int) -> List[Defect]:
        """
        Детекция клипинга / перегруза
        
        Args:
            audio_data: Аудио данные
            sr: Sample rate
            
        Returns:
            Список найденных дефектов
        """
        defects = []
        
        try:
            # Порог в линейном виде
            threshold_linear = 10 ** (self.clipping_threshold / 20)
            
            # Проверка для каждого канала
            if audio_data.ndim == 1:
                channels_data = [audio_data]
                channel_names = ["Mono"]
            else:
                channels_data = [audio_data[:, i] for i in range(audio_data.shape[1])]
                channel_names = [f"Ch{i+1}" for i in range(audio_data.shape[1])]
            
            for ch_idx, (channel, ch_name) in enumerate(zip(channels_data, channel_names)):
                # Находим места, где амплитуда превышает порог
                clipped = np.abs(channel) >= threshold_linear
                
                # Ищем непрерывные участки клипинга
                clipped_diff = np.diff(np.concatenate(([0], clipped.astype(int), [0])))
                starts = np.where(clipped_diff == 1)[0]
                ends = np.where(clipped_diff == -1)[0]
                
                for start, end in zip(starts, ends):
                    duration = (end - start) / sr
                    
                    # Только если длительность превышает минимум
                    if duration >= self.clipping_duration:
                        start_time = start / sr
                        end_time = end / sr
                        
                        peak_db = 20 * np.log10(np.max(np.abs(channel[start:end])))
                        
                        defect = Defect(
                            type="clipping",
                            timecode_in=self.seconds_to_timecode(start_time),
                            timecode_out=self.seconds_to_timecode(end_time),
                            duration=round(duration, 2),
                            description=f"В данном фрагменте реплика актёра звучит пережато. Ощущение, что есть перегруз",
                            severity="fix_required",
                            channels=["*"],
                            details={
                                "peak_db": round(peak_db, 2),
                                "channel": ch_name,
                                "threshold": self.clipping_threshold
                            }
                        )
                        defects.append(defect)
            
            logger.info(f"Обнаружено {len(defects)} участков с клипингом")
            
        except Exception as e:
            logger.error(f"Ошибка детекции клипинга: {e}")
        
        return defects
    
    def detect_clicks(self, audio_data: np.ndarray, sr: int) -> List[Defect]:
        """
        Детекция кликов и треска
        
        Args:
            audio_data: Аудио данные
            sr: Sample rate
            
        Returns:
            Список найденных дефектов
        """
        defects = []
        
        try:
            # Используем дифференцирование для поиска резких изменений
            if audio_data.ndim == 1:
                channels_data = [audio_data]
            else:
                channels_data = [audio_data[:, i] for i in range(audio_data.shape[1])]
            
            for ch_idx, channel in enumerate(channels_data):
                # Первая производная
                diff = np.diff(channel)
                
                # Находим резкие скачки
                clicks = np.abs(diff) > self.click_threshold
                
                # Находим позиции кликов
                click_positions = np.where(clicks)[0]
                
                # Группируем близкие клики
                if len(click_positions) > 0:
                    groups = []
                    current_group = [click_positions[0]]
                    
                    for pos in click_positions[1:]:
                        if pos - current_group[-1] < sr * 0.05:  # 50ms окно
                            current_group.append(pos)
                        else:
                            groups.append(current_group)
                            current_group = [pos]
                    groups.append(current_group)
                    
                    # ОГРАНИЧЕНИЕ: максимум 50 групп кликов на канал
                    if len(groups) > 50:
                        logger.warning(f"Обнаружено слишком много групп кликов ({len(groups)}) в канале {ch_idx+1}, ограничиваем до 50 самых сильных")
                        # Сортируем по силе (количество кликов в группе)
                        groups.sort(key=lambda g: len(g), reverse=True)
                        groups = groups[:50]
                    
                    # Создаем дефекты для каждой группы
                    for group in groups:
                        start_sample = group[0]
                        end_sample = group[-1]
                        start_time = start_sample / sr
                        duration = (end_sample - start_sample) / sr
                        
                        # Определяем тип по количеству кликов
                        if len(group) > 10:
                            desc = "В данном фрагменте слышен посторонний треск на реплике актёра"
                        else:
                            desc = "Посторонний щёлкающий звук"
                        
                        defect = Defect(
                            type="click",
                            timecode_in=self.seconds_to_timecode(start_time),
                            timecode_out=self.seconds_to_timecode(start_time + duration) if duration > 0.1 else None,
                            duration=round(duration, 2) if duration > 0.1 else 0,
                            description=desc,
                            severity="fix_required" if len(group) > 5 else "comment_required",
                            channels=["*"],
                            details={
                                "click_count": len(group),
                                "channel": f"Ch{ch_idx+1}"
                            }
                        )
                        defects.append(defect)
            
            logger.info(f"Обнаружено {len(defects)} кликов/треска")
            
        except Exception as e:
            logger.error(f"Ошибка детекции кликов: {e}")
        
        return defects
    
    def detect_high_frequency_noise(self, audio_data: np.ndarray, sr: int) -> List[Defect]:
        """
        Детекция высокочастотного шипения
        
        Args:
            audio_data: Аудио данные
            sr: Sample rate
            
        Returns:
            Список найденных дефектов
        """
        defects = []
        
        try:
            # Высокочастотный фильтр
            nyquist = sr / 2
            low = self.noise_freq_range[0] / nyquist
            high = min(self.noise_freq_range[1] / nyquist, 0.99)
            
            b, a = signal.butter(4, [low, high], btype='band')
            
            if audio_data.ndim == 1:
                channels_data = [audio_data]
            else:
                channels_data = [audio_data[:, i] for i in range(audio_data.shape[1])]
            
            # Анализ по блокам (1 секунда)
            block_size = sr
            
            for ch_idx, channel in enumerate(channels_data):
                # Фильтруем высокие частоты
                filtered = signal.filtfilt(b, a, channel)
                
                # Анализируем энергию по блокам
                num_blocks = len(filtered) // block_size
                
                noisy_blocks = []
                for i in range(num_blocks):
                    block = filtered[i * block_size:(i + 1) * block_size]
                    # RMS энергия
                    rms = np.sqrt(np.mean(block ** 2))
                    rms_db = 20 * np.log10(rms + 1e-10)
                    
                    if rms_db > self.noise_threshold:
                        noisy_blocks.append((i, rms_db))
                
                # Группируем соседние блоки
                if noisy_blocks:
                    groups = []
                    current_group = [noisy_blocks[0]]
                    
                    for block_idx, rms_db in noisy_blocks[1:]:
                        if block_idx - current_group[-1][0] <= 2:  # Максимум 2 секунды разрыв
                            current_group.append((block_idx, rms_db))
                        else:
                            groups.append(current_group)
                            current_group = [(block_idx, rms_db)]
                    groups.append(current_group)
                    
                    # Создаем дефекты
                    for group in groups:
                        start_block = group[0][0]
                        end_block = group[-1][0]
                        
                        start_time = start_block
                        end_time = end_block + 1
                        duration = end_time - start_time
                        
                        avg_level = np.mean([level for _, level in group])
                        
                        # Описание в зависимости от длительности
                        if duration > 5:
                            desc = "В данном фрагменте слышно высокочастотное шипение на репликах актёров"
                        else:
                            desc = "В данном фрагменте слышно высокочастотное шипение на реплике актёра"
                        
                        defect = Defect(
                            type="high_frequency_noise",
                            timecode_in=self.seconds_to_timecode(start_time),
                            timecode_out=self.seconds_to_timecode(end_time),
                            duration=int(duration),
                            description=desc,
                            severity="fix_required",
                            channels=["*"],
                            details={
                                "avg_level_db": round(avg_level, 2),
                                "frequency_range": self.noise_freq_range
                            }
                        )
                        defects.append(defect)
            
            logger.info(f"Обнаружено {len(defects)} участков с шипением")
            
        except Exception as e:
            logger.error(f"Ошибка детекции шипения: {e}")
        
        return defects
    
    def detect_silence(self, audio_data: np.ndarray, sr: int) -> List[Defect]:
        """
        Детекция тишины / отсутствия звука
        
        Args:
            audio_data: Аудио данные
            sr: Sample rate
            
        Returns:
            Список найденных дефектов
        """
        defects = []
        
        try:
            threshold_linear = 10 ** (self.silence_threshold / 20)
            
            # Рассчитываем RMS по блокам
            block_size = int(sr * 0.1)  # 100ms блоки
            
            if audio_data.ndim == 1:
                channel = audio_data
            else:
                # Берем максимум по всем каналам
                channel = np.max(np.abs(audio_data), axis=1)
            
            # Находим тихие блоки
            silent = []
            for i in range(0, len(channel) - block_size, block_size):
                block = channel[i:i + block_size]
                rms = np.sqrt(np.mean(block ** 2))
                silent.append(rms < threshold_linear)
            
            # Ищем непрерывные участки тишины
            silent = np.array(silent)
            silent_diff = np.diff(np.concatenate(([0], silent.astype(int), [0])))
            starts = np.where(silent_diff == 1)[0]
            ends = np.where(silent_diff == -1)[0]
            
            for start, end in zip(starts, ends):
                duration = (end - start) * block_size / sr
                
                if duration >= self.silence_duration:
                    start_time = start * block_size / sr
                    end_time = end * block_size / sr
                    
                    defect = Defect(
                        type="silence",
                        timecode_in=self.seconds_to_timecode(start_time),
                        timecode_out=self.seconds_to_timecode(end_time),
                        duration=round(duration, 2),
                        description="В данном фрагменте отсутствует звук",
                        severity="blocker",
                        channels=["*"],
                        details={
                            "threshold_db": self.silence_threshold
                        }
                    )
                    defects.append(defect)
            
            logger.info(f"Обнаружено {len(defects)} участков тишины")
            
        except Exception as e:
            logger.error(f"Ошибка детекции тишины: {e}")
        
        return defects
    
    def check_channel_order_51(self, audio_data: np.ndarray, sr: int) -> List[Defect]:
        """
        Проверка правильности порядка каналов в 5.1
        
        Args:
            audio_data: Аудио данные
            sr: Sample rate
            
        Returns:
            Список дефектов (если найдены проблемы)
        """
        defects = []
        
        try:
            if audio_data.shape[1] != 6:
                return defects  # Не 5.1
            
            # Простая эвристика: LFE должен иметь низкую частоту
            # Центральный канал обычно имеет больше энергии в речевом диапазоне
            
            # Анализируем спектр каждого канала
            channel_energy = []
            for i in range(6):
                channel = audio_data[:, i]
                # FFT для первых 5 секунд
                sample_length = min(len(channel), sr * 5)
                fft = np.fft.rfft(channel[:sample_length])
                freqs = np.fft.rfftfreq(sample_length, 1/sr)
                
                # Энергия в низких частотах (< 120 Hz для LFE)
                low_freq_mask = freqs < 120
                low_energy = np.sum(np.abs(fft[low_freq_mask]))
                
                # Энергия в речевом диапазоне (300-3000 Hz для центра)
                speech_mask = (freqs >= 300) & (freqs <= 3000)
                speech_energy = np.sum(np.abs(fft[speech_mask]))
                
                channel_energy.append({
                    'low': low_energy,
                    'speech': speech_energy,
                    'total': np.sum(np.abs(fft))
                })
            
            # Проверяем, что канал 3 (LFE) имеет высокую низкочастотную энергию
            lfe_idx = 3
            lfe_low_ratio = channel_energy[lfe_idx]['low'] / (channel_energy[lfe_idx]['total'] + 1e-10)
            
            # Если LFE не содержит в основном низкие частоты, возможна проблема
            if lfe_low_ratio < 0.7:
                defect = Defect(
                    type="channel_order",
                    timecode_in="01:00:00:00",
                    timecode_out=None,
                    duration=0,
                    description="В звуковой дорожке 5.1 перепутана последовательность каналов",
                    severity="blocker",
                    channels=["5.1"],
                    details={
                        "expected_order": self.expected_channels,
                        "lfe_low_freq_ratio": round(lfe_low_ratio, 2)
                    }
                )
                defects.append(defect)
            
            logger.info(f"Проверка каналов 5.1: {'OK' if not defects else 'Проблемы'}")
            
        except Exception as e:
            logger.error(f"Ошибка проверки порядка каналов: {e}")
        
        return defects
    
    def detect_all(self, audio_data: np.ndarray, sr: int, channel_type: str = "2.0") -> List[Defect]:
        """
        Запуск всех детекторов
        
        Args:
            audio_data: Аудио данные
            sr: Sample rate
            channel_type: "2.0" или "5.1"
            
        Returns:
            Список всех найденных дефектов
        """
        all_defects = []
        
        logger.info(f"Запуск детекции для {channel_type}...")
        
        try:
            # Запускаем все детекторы с обработкой ошибок
            try:
                clipping_defects = self.detect_clipping(audio_data, sr)
                all_defects.extend(clipping_defects)
                logger.info(f"Клипинг: {len(clipping_defects)} дефектов")
            except Exception as e:
                logger.error(f"Ошибка детекции клипинга: {e}")
            
            try:
                click_defects = self.detect_clicks(audio_data, sr)
                all_defects.extend(click_defects)
                logger.info(f"Клики: {len(click_defects)} дефектов")
            except Exception as e:
                logger.error(f"Ошибка детекции кликов: {e}")
            
            try:
                noise_defects = self.detect_high_frequency_noise(audio_data, sr)
                all_defects.extend(noise_defects)
                logger.info(f"Шипение: {len(noise_defects)} дефектов")
            except Exception as e:
                logger.error(f"Ошибка детекции шипения: {e}")
            
            try:
                silence_defects = self.detect_silence(audio_data, sr)
                all_defects.extend(silence_defects)
                logger.info(f"Тишина: {len(silence_defects)} дефектов")
            except Exception as e:
                logger.error(f"Ошибка детекции тишины: {e}")
            
            # Проверка каналов только для 5.1
            if channel_type == "5.1" and audio_data.ndim > 1:
                try:
                    channel_defects = self.check_channel_order_51(audio_data, sr)
                    all_defects.extend(channel_defects)
                    logger.info(f"Каналы: {len(channel_defects)} дефектов")
                except Exception as e:
                    logger.error(f"Ошибка проверки каналов: {e}")
            
            # ОГРАНИЧЕНИЕ: максимум 200 дефектов на файл
            if len(all_defects) > 200:
                logger.warning(f"Слишком много дефектов ({len(all_defects)}), оставляем топ-200 по серьезности")
                # Сортируем по серьезности
                severity_order = {"blocker": 0, "fix_required": 1, "comment_required": 2}
                all_defects.sort(key=lambda d: (severity_order.get(d.severity, 3), d.timecode_in))
                all_defects = all_defects[:200]
            
            logger.info(f"Всего обнаружено {len(all_defects)} дефектов")
            
        except Exception as e:
            logger.error(f"Критическая ошибка в detect_all: {e}")
        
        return all_defects
    
    def analyze_file(self, file_path: str, channel_type: str = "2.0") -> List[Defect]:
        """
        Анализ файла на дефекты
        
        Args:
            file_path: Путь к файлу
            channel_type: "2.0" или "5.1"
            
        Returns:
            Список дефектов
        """
        try:
            logger.info(f"Загрузка файла для детекции: {file_path}")
            audio_data, sr = sf.read(file_path)
            
            if audio_data.dtype != np.float32:
                audio_data = audio_data.astype(np.float32)
            
            return self.detect_all(audio_data, sr, channel_type)
            
        except Exception as e:
            logger.error(f"Ошибка анализа файла {file_path}: {e}")
            raise


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    config = {
        'detection': {
            'click_threshold': 0.05,
            'noise_threshold': -60,
            'clipping_threshold': -1.0,
            'silence_threshold': -70
        },
        'audio': {
            'sample_rate': 48000
        }
    }
    
    detector = DefectDetector(config)
    print("DefectDetector готов к использованию!")

