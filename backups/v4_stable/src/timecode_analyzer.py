"""
Timecode Analyzer Module

Анализ хронометража аудио и видео файлов
Проверка совпадения хронометража между файлами
"""

import soundfile as sf
import subprocess
import json
from pathlib import Path
from typing import Dict, List, Tuple
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)


class TimecodeAnalyzer:
    """Класс для анализа хронометража"""
    
    def __init__(self):
        """Инициализация анализатора"""
        logger.info("TimecodeAnalyzer инициализирован")
    
    def get_audio_duration(self, audio_path: str) -> float:
        """
        Получение длительности аудиофайла
        
        Args:
            audio_path: Путь к аудиофайлу
            
        Returns:
            Длительность в секундах
        """
        try:
            info = sf.info(audio_path)
            duration = info.duration
            logger.info(f"Длительность аудио {Path(audio_path).name}: {duration:.2f} сек")
            return duration
        except Exception as e:
            logger.error(f"Ошибка получения длительности аудио: {e}")
            return 0.0
    
    def get_video_duration(self, video_path: str) -> float:
        """
        Получение длительности видеофайла через ffprobe
        
        Args:
            video_path: Путь к видеофайлу
            
        Returns:
            Длительность в секундах
        """
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                video_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            data = json.loads(result.stdout)
            duration = float(data['format']['duration'])
            
            logger.info(f"Длительность видео {Path(video_path).name}: {duration:.2f} сек")
            return duration
            
        except Exception as e:
            logger.error(f"Ошибка получения длительности видео: {e}")
            return 0.0
    
    def get_video_start_timecode(self, video_path: str) -> str:
        """
        Извлечение стартового таймкода из видео
        
        Args:
            video_path: Путь к видеофайлу
            
        Returns:
            Стартовый таймкод в формате HH:MM:SS:FF
        """
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-select_streams', 'v:0',
                video_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            data = json.loads(result.stdout)
            
            # Ищем тег timecode
            streams = data.get('streams', [])
            if streams:
                stream = streams[0]
                tags = stream.get('tags', {})
                timecode = tags.get('timecode', '01:00:00:00')
                
                logger.info(f"Стартовый таймкод видео: {timecode}")
                return timecode
            
            # По умолчанию
            return "01:00:00:00"
            
        except Exception as e:
            logger.error(f"Ошибка извлечения таймкода: {e}")
            return "01:00:00:00"
    
    def seconds_to_timecode(self, seconds: float, fps: int = 25) -> str:
        """
        Конвертация секунд в таймкод
        
        Args:
            seconds: Время в секундах
            fps: Кадров в секунду
            
        Returns:
            Таймкод в формате HH:MM:SS:FF
        """
        td = timedelta(seconds=seconds)
        hours = int(td.total_seconds() // 3600)
        minutes = int((td.total_seconds() % 3600) // 60)
        secs = int(td.total_seconds() % 60)
        frames = int((td.total_seconds() % 1) * fps)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"
    
    def timecode_to_seconds(self, timecode: str, fps: int = 25) -> float:
        """
        Конвертация таймкода в секунды
        
        Args:
            timecode: Таймкод HH:MM:SS:FF
            fps: Кадров в секунду
            
        Returns:
            Время в секундах
        """
        try:
            parts = timecode.split(':')
            if len(parts) == 4:
                hours, minutes, seconds, frames = map(int, parts)
                total_seconds = hours * 3600 + minutes * 60 + seconds + frames / fps
                return total_seconds
        except:
            return 0.0
    
    def compare_durations(
        self,
        file1_path: str,
        file2_path: str,
        tolerance: float = 0.5
    ) -> Dict:
        """
        Сравнение длительностей двух файлов
        
        Args:
            file1_path: Путь к первому файлу
            file2_path: Путь к второму файлу
            tolerance: Допустимая разница в секундах
            
        Returns:
            Словарь с результатами сравнения
        """
        # Определяем типы файлов
        ext1 = Path(file1_path).suffix.lower()
        ext2 = Path(file2_path).suffix.lower()
        
        audio_exts = ['.wav', '.mp3', '.aiff', '.flac', '.m4a']
        video_exts = ['.mp4', '.mov', '.mkv', '.avi']
        
        # Получаем длительности
        if ext1 in audio_exts:
            duration1 = self.get_audio_duration(file1_path)
        elif ext1 in video_exts:
            duration1 = self.get_video_duration(file1_path)
        else:
            duration1 = 0.0
        
        if ext2 in audio_exts:
            duration2 = self.get_audio_duration(file2_path)
        elif ext2 in video_exts:
            duration2 = self.get_video_duration(file2_path)
        else:
            duration2 = 0.0
        
        # Сравнение
        difference = abs(duration1 - duration2)
        matches = difference <= tolerance
        
        result = {
            'file1': Path(file1_path).name,
            'file2': Path(file2_path).name,
            'duration1': duration1,
            'duration2': duration2,
            'difference': difference,
            'matches': matches,
            'tolerance': tolerance
        }
        
        logger.info(f"Сравнение хронометража: {result}")
        
        return result
    
    def analyze_all_files(
        self,
        audio_20: str = None,
        audio_51: str = None,
        video: str = None,
        fps: int = 25
    ) -> Dict:
        """
        Полный анализ всех файлов
        
        Args:
            audio_20: Путь к аудио 2.0
            audio_51: Путь к аудио 5.1
            video: Путь к видео
            fps: Кадров в секунду
            
        Returns:
            Словарь с результатами анализа
        """
        results = {
            'files': {},
            'comparisons': [],
            'start_timecode': "01:00:00:00",
            'fps': fps
        }
        
        # Анализ файлов
        files = []
        
        if audio_20:
            duration = self.get_audio_duration(audio_20)
            results['files']['audio_20'] = {
                'path': audio_20,
                'name': Path(audio_20).name,
                'duration': duration,
                'duration_tc': self.seconds_to_timecode(duration, fps)
            }
            files.append(('audio_20', audio_20))
        
        if audio_51:
            duration = self.get_audio_duration(audio_51)
            results['files']['audio_51'] = {
                'path': audio_51,
                'name': Path(audio_51).name,
                'duration': duration,
                'duration_tc': self.seconds_to_timecode(duration, fps)
            }
            files.append(('audio_51', audio_51))
        
        if video:
            duration = self.get_video_duration(video)
            start_tc = self.get_video_start_timecode(video)
            results['files']['video'] = {
                'path': video,
                'name': Path(video).name,
                'duration': duration,
                'duration_tc': self.seconds_to_timecode(duration, fps),
                'start_timecode': start_tc
            }
            results['start_timecode'] = start_tc
            files.append(('video', video))
        
        # Сравнения между файлами
        for i, (name1, path1) in enumerate(files):
            for name2, path2 in files[i+1:]:
                comparison = self.compare_durations(path1, path2)
                comparison['file1_type'] = name1
                comparison['file2_type'] = name2
                results['comparisons'].append(comparison)
        
        return results


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    analyzer = TimecodeAnalyzer()
    print("TimecodeAnalyzer готов к использованию!")

