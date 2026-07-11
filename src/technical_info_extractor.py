"""
Technical Info Extractor Module

Извлекает только технические параметры без детекции дефектов:
- Битность (bit depth)
- Частота дискретизации (sample rate)
- Формат файла
- Количество каналов
- Порядок каналов (из Параметры.txt)
"""

import logging
from pathlib import Path
from typing import Dict, Optional
import re
import subprocess
import shutil
import sys
import os

logger = logging.getLogger(__name__)

# Известные "вещательные" частоты кадров, включая дробные NTSC-варианты
# (24000/1001 и т.п.). Сырое значение из ffprobe снэппится к ближайшей
# из них, чтобы избавиться от погрешности округления контейнера, но не
# схлопывается в жёсткие 24/25, как раньше.
KNOWN_FPS_RATES = [
    23.976, 24.0, 25.0, 29.97, 30.0, 47.952, 48.0, 50.0, 59.94, 60.0,
]
FPS_SNAP_TOLERANCE = 0.02


def normalize_fps(fps_str: str, default: float = 25.0) -> float:
    """Преобразует значение r_frame_rate ffprobe (например, '24000/1001')
    в float FPS, снэппая к ближайшей известной вещательной частоте."""
    if not fps_str:
        return default
    try:
        if '/' in fps_str:
            num, denom = fps_str.split('/')
            raw_fps = float(num) / float(denom)
        else:
            raw_fps = float(fps_str)
    except (ValueError, ZeroDivisionError):
        return default

    if raw_fps <= 0:
        return default

    nearest = min(KNOWN_FPS_RATES, key=lambda rate: abs(rate - raw_fps))
    if abs(nearest - raw_fps) <= FPS_SNAP_TOLERANCE:
        return nearest

    # Нестандартная частота (например, VFR-артефакт) — оставляем как есть,
    # округлив до сотых, чтобы не терять точность при проверке кратности кадру.
    return round(raw_fps, 2)


def format_fps(fps) -> str:
    """Форматирует FPS для отображения: целые значения без дробной части
    ('25' вместо '25.0'), дробные — с точностью до 3 знаков ('23.976')."""
    if fps is None:
        return ""
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        return str(fps)
    if fps.is_integer():
        return str(int(fps))
    return f"{fps:.3f}".rstrip('0').rstrip('.')


class TechnicalInfoExtractor:
    """Класс для извлечения технических параметров"""
    
    def __init__(self):
        logger.info("TechnicalInfoExtractor инициализирован")
        self.ffprobe_path = self._find_ffprobe()
    
    def _find_ffprobe(self) -> str:
        """Поиск ffprobe в системе (сначала в bundle, потом в PATH)"""
        # ПРИОРИТЕТ 1: Проверяем bundled ffprobe (PyInstaller)
        if getattr(sys, 'frozen', False):
            # Приложение запущено как bundle (PyInstaller)
            bundle_dir = sys._MEIPASS
            
            # Путь 1: Проверяем в _MEIPASS/ffmpeg/ (обычное расположение)
            bundled_ffprobe = os.path.join(bundle_dir, 'ffmpeg', 'ffprobe')
            if os.path.exists(bundled_ffprobe) and os.access(bundled_ffprobe, os.X_OK):
                logger.info(f"✅ ffprobe найден в bundle: {bundled_ffprobe}")
                return bundled_ffprobe
            
            # Путь 2: Проверяем в ../Frameworks/ffmpeg/ (macOS .app структура)
            frameworks_ffprobe = os.path.join(bundle_dir, '..', 'Frameworks', 'ffmpeg', 'ffprobe')
            frameworks_ffprobe = os.path.abspath(frameworks_ffprobe)
            if os.path.exists(frameworks_ffprobe) and os.access(frameworks_ffprobe, os.X_OK):
                logger.info(f"✅ ffprobe найден в Frameworks: {frameworks_ffprobe}")
                return frameworks_ffprobe
            
            logger.warning(f"⚠️  Bundled ffprobe не найден в: {bundled_ffprobe} или {frameworks_ffprobe}")
        
        # ПРИОРИТЕТ 2: Проверяем через shutil.which (проверяет PATH)
        ffprobe = shutil.which('ffprobe')
        if ffprobe:
            logger.info(f"✅ ffprobe найден в PATH: {ffprobe}")
            return ffprobe
        
        # ПРИОРИТЕТ 3: Проверяем стандартные места установки (Homebrew)
        common_paths = [
            '/opt/homebrew/bin/ffprobe',  # Apple Silicon Homebrew
            '/usr/local/bin/ffprobe',      # Intel Homebrew
            '/usr/bin/ffprobe',            # System
        ]
        
        for path in common_paths:
            if Path(path).exists():
                logger.info(f"✅ ffprobe найден: {path}")
                return path
        
        logger.error("❌ ffprobe не найден! Установите FFmpeg: brew install ffmpeg")
        return 'ffprobe'  # Возвращаем имя команды на случай если все же есть в PATH
    
    def extract_audio_info(self, file_path: str) -> Dict:
        """
        Извлечение технических параметров аудио файла
        
        Args:
            file_path: Путь к аудио файлу
            
        Returns:
            Словарь с техническими параметрами
        """
        try:
            logger.info(f"Извлечение технической информации из: {file_path}")
            
            file_path_obj = Path(file_path)
            
            # Читаем файл через soundfile
            import soundfile as sf
            info = sf.info(file_path)
            
            # Определяем порядок каналов из метаданных ffprobe
            channel_order = self._get_channel_layout_ffprobe(file_path)
            if not channel_order:
                # Нет метаданных layout → fallback "N channels"
                channel_order = self._get_channel_order(info.channels)

            tech_info = {
                'file_name': file_path_obj.name,
                'format': file_path_obj.suffix.upper().replace('.', ''),  # WAV, MP3, etc.
                'sample_rate': info.samplerate,  # Hz
                'bit_depth': info.subtype,  # PCM_16, PCM_24, FLOAT, etc.
                'channels': info.channels,  # 2, 6, etc.
                'channel_order': channel_order,  # L R C LFE Ls Rs или "6 channels"
                'duration': info.duration,  # seconds
                'frames': info.frames
            }
            
            logger.info(f"Технические параметры: {info.samplerate}Hz, {info.subtype}, {info.channels}ch")
            
            return tech_info
            
        except Exception as e:
            logger.warning(f"soundfile не смог прочитать файл: {e}")
            ff_info = self._probe_audio_info_ffprobe(file_path)
            if ff_info:
                return ff_info
            logger.error(f"Ошибка извлечения технической информации: {e}")
            return {}

    def _probe_audio_info_ffprobe(self, file_path: str) -> Dict:
        """Fallback: извлечение базовых метаданных через ffprobe"""
        try:
            import json
            file_path_obj = Path(file_path)
            cmd = [
                self.ffprobe_path,
                '-v', 'error',
                '-select_streams', 'a:0',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                str(file_path_obj)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.warning(f"ffprobe error: {result.stderr}")
                return {}
            
            data = json.loads(result.stdout or "{}")
            streams = data.get('streams', [])
            fmt = data.get('format', {})
            if not streams:
                return {}
            
            stream = streams[0]
            channels = int(stream.get('channels', 0) or 0)
            sample_rate = int(stream.get('sample_rate', 0) or 0)
            duration = float(fmt.get('duration', 0) or 0)
            bits = stream.get('bits_per_sample') or stream.get('bits_per_raw_sample')
            bit_depth = f"PCM_{bits}" if bits else "PCM_24"
            # Читаем channel_layout из метаданных ffprobe
            raw_layout = stream.get('channel_layout', '')
            if raw_layout and raw_layout.lower() not in ('unknown', '0 channels'):
                channel_order = self._LAYOUT_MAP.get(raw_layout.lower(), raw_layout)
            else:
                channel_order = self._get_channel_order(channels) if channels else ""

            tech_info = {
                'file_name': file_path_obj.name,
                'format': file_path_obj.suffix.upper().replace('.', ''),
                'sample_rate': sample_rate or 48000,
                'bit_depth': bit_depth,
                'channels': channels,
                'channel_order': channel_order,
                'duration': duration,
                'frames': 0
            }
            
            logger.info(f"ffprobe: {sample_rate}Hz, {bit_depth}, {channels}ch")
            return tech_info
        except Exception as e:
            logger.warning(f"ffprobe fallback failed: {e}")
            return {}
    
    # Маппинг ffprobe channel_layout → читаемый порядок каналов
    _LAYOUT_MAP = {
        "mono": "M",
        "stereo": "L R",
        "5.1": "L R C LFE Ls Rs",
        "5.1(side)": "L R C LFE Ls Rs",
        "5.1(back)": "L R C LFE Ls Rs",
        "7.1": "L R C LFE Ls Rs Lb Rb",
        "7.1(wide)": "L R C LFE Ls Rs FLc FRc",
    }

    def _get_channel_layout_ffprobe(self, file_path: str) -> str:
        """
        Извлечение channel_layout из метаданных файла через ffprobe.
        Возвращает строку порядка каналов или '' если метаданных нет.
        """
        try:
            cmd = [
                self.ffprobe_path,
                '-v', 'error',
                '-select_streams', 'a:0',
                '-show_entries', 'stream=channel_layout',
                '-of', 'csv=p=0',
                str(file_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            layout = result.stdout.strip() if result.returncode == 0 else ''
            if layout and layout.lower() not in ('unknown', '0 channels'):
                mapped = self._LAYOUT_MAP.get(layout.lower(), layout)
                logger.info(f"ffprobe channel_layout: '{layout}' → '{mapped}'")
                return mapped
            logger.info(f"ffprobe: channel_layout отсутствует в метаданных")
            return ''
        except Exception as e:
            logger.warning(f"ffprobe channel_layout failed: {e}")
            return ''

    def _get_channel_order(self, num_channels: int) -> str:
        """
        Fallback когда нет метаданных layout.
        Для 1/2 каналов порядок однозначный, для остальных — "{N} channels".
        """
        if num_channels == 1:
            return "M"
        if num_channels == 2:
            return "L R"
        return f"{num_channels} channels"
    
    def extract_video_info(self, file_path: str) -> Dict:
        """
        Извлечение технических параметров видео файла
        
        Args:
            file_path: Путь к видео файлу
            
        Returns:
            Словарь с техническими параметрами
        """
        try:
            logger.info(f"🎬 Извлечение информации из видео: {file_path}")
            
            file_path_obj = Path(file_path)
            
            # КРИТИЧЕСКАЯ ПРОВЕРКА: существует ли файл?
            if not file_path_obj.exists():
                logger.error(f"❌ ФАЙЛ НЕ СУЩЕСТВУЕТ: {file_path}")
                logger.error(f"   Полный путь: {file_path_obj.absolute()}")
                return {}
            
            if not file_path_obj.is_file():
                logger.error(f"❌ ЭТО НЕ ФАЙЛ: {file_path}")
                return {}
            
            logger.info(f"✅ Файл существует: {file_path_obj.name} ({file_path_obj.stat().st_size} bytes)")
            
            # Используем ffprobe для получения информации
            import json
            
            cmd = [
                self.ffprobe_path,  # Используем найденный путь
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                file_path
            ]
            
            logger.info(f"🔍 Запуск ffprobe: {self.ffprobe_path}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"❌ ffprobe вернул код {result.returncode}")
                logger.error(f"stderr: {result.stderr}")
                return {}
            
            data = json.loads(result.stdout)
            format_data = data.get('format', {})
            streams = data.get('streams', [])
            
            logger.info(f"📊 Найдено {len(streams)} потоков в видео файле")
            
            # Ищем аудио и видео стримы
            audio_stream = None
            video_stream = None
            for idx, stream in enumerate(streams):
                codec_type = stream.get('codec_type')
                logger.info(f"  Поток {idx}: тип={codec_type}, codec={stream.get('codec_name')}")
                
                if codec_type == 'audio' and not audio_stream:
                    audio_stream = stream
                    logger.info(f"  ✅ Аудио поток найден")
                elif codec_type == 'video' and not video_stream:
                    video_stream = stream
                    logger.info(f"  ✅ Видео поток найден")
            
            # Если нет ни аудио, ни видео потока
            if not audio_stream and not video_stream:
                logger.warning(f"⚠️  Не найдено аудио или видео потоков в файле")
                return {}
            
            # Извлекаем таймкод из метаданных
            timecode = None
            
            # Пробуем разные источники таймкода
            tags = format_data.get('tags', {})
            if 'timecode' in tags:
                timecode = tags['timecode']
            elif 'creation_time' in tags:
                timecode = tags['creation_time']
            
            # Проверяем метаданные видео стрима
            if not timecode and video_stream:
                video_tags = video_stream.get('tags', {})
                if 'timecode' in video_tags:
                    timecode = video_tags['timecode']
            
            # Извлекаем FPS из видео потока СНАЧАЛА
            fps = 25.0  # По умолчанию
            if video_stream:
                # Пытаемся извлечь FPS (учитываем дробные вещательные частоты
                # вроде 23.976, 29.97, 59.94 — не схлопываем всё в 24/25)
                fps_str = video_stream.get('r_frame_rate') or video_stream.get('avg_frame_rate') or '25/1'
                logger.info(f"🎞️  FPS (raw): {fps_str}")
                fps = normalize_fps(fps_str)
                logger.info(f"🎞️  FPS (итоговый): {fps}")
            
            # Если таймкода нет, используем start_time
            if not timecode:
                start_time = float(format_data.get('start_time', 0))
                if start_time > 0:
                    # Конвертируем в таймкод, используя извлеченный FPS
                    hours = int(start_time // 3600)
                    mins = int((start_time % 3600) // 60)
                    secs = int(start_time % 60)
                    frames = int((start_time % 1) * fps)  # Используем извлеченный FPS
                    timecode = f"{hours:02d}:{mins:02d}:{secs:02d}:{frames:02d}"
                else:
                    # Используем 01:00:00:00 по умолчанию
                    timecode = "01:00:00:00"
            
            # Приоритет: stream duration → nb_frames/fps → format duration
            # format.duration на Intel Mac может включать контейнерный паддинг
            duration = 0.0
            if video_stream:
                stream_dur = video_stream.get('duration')
                nb_frames = video_stream.get('nb_frames')
                if stream_dur and float(stream_dur) > 0:
                    duration = float(stream_dur)
                    logger.info(f"📏 Duration из видеопотока: {duration}")
                elif nb_frames and int(nb_frames) > 0 and fps > 0:
                    duration = int(nb_frames) / fps
                    logger.info(f"📏 Duration из nb_frames ({nb_frames}) / fps ({fps}): {duration}")
            if duration == 0.0:
                duration = float(format_data.get('duration', 0))
                logger.info(f"📏 Duration из format (fallback): {duration}")
            video_format = file_path_obj.suffix.upper().replace('.', '')
            
            tech_info = {
                'file_name': file_path_obj.name,
                'format': video_format,
                'duration': duration,
                'timecode': timecode,
                'fps': fps  # Добавляем FPS
            }
            
            logger.info(f"✅ Видео информация извлечена:")
            logger.info(f"   - Файл: {tech_info['file_name']}")
            logger.info(f"   - Формат: {tech_info['format']}")
            logger.info(f"   - Длительность: {tech_info['duration']:.2f}с")
            logger.info(f"   - FPS: {tech_info['fps']}")
            logger.info(f"   - Таймкод: {tech_info['timecode']}")
            
            # Добавляем аудио параметры если есть
            if audio_stream:
                tech_info.update({
                    'sample_rate': int(audio_stream.get('sample_rate', 0)),
                    'bit_depth': audio_stream.get('sample_fmt', 'Unknown'),
                    'channels': audio_stream.get('channels', 0),
                    'channel_order': self._get_channel_order(audio_stream.get('channels', 0)),
                    'codec': audio_stream.get('codec_name', 'Unknown')
                })
                logger.info(f"   - Аудио: {tech_info['sample_rate']}Hz, {tech_info['channels']}ch")
            
            return tech_info
            
        except subprocess.TimeoutExpired:
            logger.error(f"❌ ffprobe timeout при обработке видео")
            return {}
        except Exception as e:
            logger.error(f"❌ Ошибка извлечения информации из видео: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}
    
    def read_params_file(self, params_path: str) -> Dict:
        """
        Чтение файла Параметры.txt
        
        Args:
            params_path: Путь к файлу Параметры.txt
            
        Returns:
            Словарь с параметрами
        """
        try:
            logger.info(f"Чтение параметров из: {params_path}")
            
            with open(params_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            params = {
                'raw_content': content,
                'target_lufs': None,
                'true_peak': None,
                'lra_max': None,
                'sample_rate': None,
                'bit_depth': None,
                'channel_order_20': None,
                'channel_order_51': None
            }
            
            # Парсинг параметров (примеры)
            lufs_match = re.search(r'(-?\d+\.?\d*)\s*LUFS', content)
            if lufs_match:
                params['target_lufs'] = float(lufs_match.group(1))
            
            peak_match = re.search(r'TRUE\s*PEAK[:\s]+(-?\d+\.?\d*)', content, re.IGNORECASE)
            if peak_match:
                params['true_peak'] = float(peak_match.group(1))
            
            lra_match = re.search(r'LRA[:\s]+(\d+\.?\d*)', content, re.IGNORECASE)
            if lra_match:
                params['lra_max'] = float(lra_match.group(1))
            
            sr_match = re.search(r'(\d+)\s*[kK]?[Hh]z', content)
            if sr_match:
                sr_value = int(sr_match.group(1))
                params['sample_rate'] = sr_value if sr_value > 1000 else sr_value * 1000
            
            bit_match = re.search(r'(\d+)\s*[bB]it', content)
            if bit_match:
                params['bit_depth'] = int(bit_match.group(1))
            
            # Порядок каналов
            if '5.1' in content or '5 1' in content:
                order_51_match = re.search(r'5\.1[^:]*:[^:]*([LRC,\s]+)', content, re.IGNORECASE)
                if order_51_match:
                    params['channel_order_51'] = order_51_match.group(1).strip()
                else:
                    params['channel_order_51'] = "L, R, C, LFE, Ls, Rs"
            
            if '2.0' in content or 'stereo' in content.lower():
                params['channel_order_20'] = "L, R"
            
            logger.info(f"Параметры из файла: LUFS={params['target_lufs']}, SR={params['sample_rate']}, Bit={params['bit_depth']}")
            
            return params
            
        except Exception as e:
            logger.error(f"Ошибка чтения файла параметров: {e}")
            return {}


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    extractor = TechnicalInfoExtractor()
    
    # Тестируем на реальном файле
    audio_info = extractor.extract_audio_info('/Users/vladog/Desktop/ШАБЛОН/petr_2_s1_e2_5.1_cens_2024_09_12_rus.wav')
    
    print("\n=== ТЕХНИЧЕСКИЕ ПАРАМЕТРЫ АУДИО ===")
    for key, value in audio_info.items():
        print(f"{key}: {value}")
