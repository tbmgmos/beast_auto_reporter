"""
Audio Analyzer Extended Module

Расширенный анализ аудио файлов с использованием pyloudnorm.
Генерирует CSV/HTML отчеты с параметрами громкости.
"""

import csv
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class AudioAnalyzerExtended:
    """
    Расширенный анализатор аудио с экспортом в CSV/HTML
    
    Использование:
        analyzer = AudioAnalyzerExtended()
        params = analyzer.analyze_with_pyloudnorm("/path/to/audio.wav")
    """
    
    def __init__(self, config: Dict = None):
        """
        Инициализация
        
        Args:
            config: Словарь с настройками из settings.yaml
        """
        self.config = config or {}
        self.audio_config = self.config.get('audio', {})
        
        # Настройки по умолчанию
        self.target_lufs = self.audio_config.get('target_lufs', -23.0)
        self.lufs_tolerance = self.audio_config.get('lufs_tolerance', 0.5)
        self.true_peak_threshold = self.audio_config.get('true_peak', -2.0)
        self.lra_max = self.audio_config.get('lra_max', 18.0)
        
        logger.info("AudioAnalyzerExtended инициализирован")
    
    def analyze_with_pyloudnorm(self, file_path: str, config: Dict = None) -> Dict:
        """
        Анализ аудиофайла с использованием pyloudnorm
        
        Args:
            file_path: Путь к аудиофайлу
            config: Конфигурация
            
        Returns:
            Словарь с параметрами
        """
        # Прямой импорт для избежания циклических зависимостей
        import importlib.util
        spec = importlib.util.spec_from_file_location("audio_analyzer", 
            os.path.join(os.path.dirname(__file__), "audio_analyzer.py"))
        audio_analyzer_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audio_analyzer_mod)
        
        AudioAnalyzer = audio_analyzer_mod.AudioAnalyzer
        analyzer = AudioAnalyzer(config or self.config)
        result = analyzer.analyze_file(file_path)
        
        # Добавляем специфичные поля
        result['source'] = 'pyloudnorm'
        result['analysis_method'] = 'pyloudnorm'
        
        return result
    
    def analyze_multiple(self, file_paths: List[str]) -> List[Dict]:
        """
        Анализ множества файлов
        
        Args:
            file_paths: Список путей
            
        Returns:
            Список словарей с параметрами
        """
        results = []
        for file_path in file_paths:
            try:
                result = self.analyze_with_pyloudnorm(file_path)
                results.append(result)
            except Exception as e:
                logger.error(f"Ошибка анализа {file_path}: {e}")
                results.append({
                    'file_path': file_path,
                    'file_name': Path(file_path).name,
                    'error': str(e),
                    'analysis_method': 'pyloudnorm',
                    'timestamp': datetime.utcnow().isoformat() + 'Z'
                })
        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    import yaml
    try:
        with open('config/settings.yaml', 'r') as f:
            config = yaml.safe_load(f)
    except:
        config = {}
    
    analyzer = AudioAnalyzerExtended(config)
    
    # Тест (без реальных файлов)
    print("AudioAnalyzerExtended готов!")
