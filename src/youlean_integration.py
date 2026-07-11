"""
Youlean Integration Helper Module

Функции для интеграции pyloudnorm данных в отчеты.
Использует audio_analyzer_extended для анализа аудио файлов.

Использование:
    from src.youlean_integration import (
        analyze_audio_files,
        export_audio_parameters
    )
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional
import os

logger = logging.getLogger(__name__)


def analyze_audio_files(
    file_paths: List[str],
    config: Dict = None
) -> List[Dict]:
    """
    Анализ аудиофайлов с использованием pyloudnorm
    
    Args:
        file_paths: Список путей к аудиофайлам
        config: Конфигурация
        
    Returns:
        Список словарей с параметрами
    """
    from src.audio_analyzer_extended import AudioAnalyzerExtended
    
    analyzer = AudioAnalyzerExtended(config)
    return analyzer.analyze_multiple(file_paths)


def export_audio_parameters(
    audio_files: List[str],
    output_dir: str,
    config: Dict = None,
    formats: List[str] = ['csv', 'html']
) -> Dict[str, str]:
    """
    Экспорт параметров аудио в различные форматы
    
    Args:
        audio_files: Список аудиофайлов
        output_dir: Директория для экспорта
        config: Конфигурация
        formats: Форматы ['csv', 'html', 'json']
        
    Returns:
        {'csv': path, 'html': path, ...}
    """
    from src.parameter_exporter import ParameterExporter
    from src.audio_analyzer_extended import AudioAnalyzerExtended
    
    # Анализ файлов
    analyzer = AudioAnalyzerExtended(config)
    params_list = analyzer.analyze_multiple(audio_files)
    
    # Экспорт
    exporter = ParameterExporter(config)
    return exporter.analyze_and_export(audio_files, output_dir, config, formats)


def populate_table_with_audio_params(
    params: Dict,
    table_row,
    column_indices: Dict = None,
    target_lufs: float = -23.0,
    lufs_tolerance: float = 0.5,
    true_peak_threshold: float = -2.0,
    lra_max: float = 18.0
) -> None:
    """
    Заполнение ячейки строки таблицы данными аудио анализа
    
    Args:
        params: Словарь с параметрами (из analyze_file)
        table_row: Строка таблицы Word
        column_indices: Индексы колонок
        target_lufs: Целевой LUFS
        lufs_tolerance: Допуск LUFS
        true_peak_threshold: Максимальный True Peak
        lra_max: Максимальный LRA
    """
    if column_indices is None:
        column_indices = {
            'file_name': 0,
            'duration_seconds': 1,
            'integrated_lufs': 2,
            'true_peak_dbtp': 3,
            'lra': 4,
            'overall_compliant': 5,
        }
    
    measurements = params.get('measurements', {})
    compliance = params.get('compliance', {})
    
    # LUFS
    lufs_col = column_indices.get('integrated_lufs', column_indices.get('lufs'))
    if lufs_col is not None:
        lufs = measurements.get('lufs')
        if lufs is not None:
            table_row.cells[lufs_col].text = f"{lufs:.1f} LUFS"
            
            # Цветовая индикация
            is_compliant = compliance.get('lufs_compliant', abs(lufs - target_lufs) <= lufs_tolerance)
            _set_cell_background(table_row.cells[lufs_col], is_compliant)
    
    # True Peak
    peak_col = column_indices.get('true_peak_dbtp', column_indices.get('true_peak'))
    if peak_col is not None:
        true_peak = measurements.get('true_peak')
        if true_peak is not None:
            sign = "+" if true_peak > 0 else ""
            table_row.cells[peak_col].text = f"{sign}{true_peak:.1f} dBTP"
            
            is_compliant = compliance.get('true_peak_compliant', true_peak <= true_peak_threshold)
            _set_cell_background(table_row.cells[peak_col], is_compliant)
    
    # LRA
    lra_col = column_indices.get('lra')
    if lra_col is not None:
        lra = measurements.get('lra')
        if lra is not None:
            table_row.cells[lra_col].text = f"{lra:.1f} LU"
            
            is_compliant = compliance.get('lra_compliant', lra <= lra_max)
            _set_cell_background(table_row.cells[lra_col], is_compliant)
    
    # File Name
    name_col = column_indices.get('file_name')
    if name_col is not None:
        table_row.cells[name_col].text = params.get('file_name', '')


def _set_cell_background(cell, is_compliant: bool) -> None:
    """
    Установка цвета фона ячейки
    
    Args:
        cell: Ячейка таблицы Word
        is_compliant: True = зеленый, False = красный
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    
    # Зеленый = Compliant, Красный = Non-Compliant
    color = "90EE90" if is_compliant else "FFB6C1"
    
    try:
        shading_elm = OxmlElement('w:shd')
        shading_elm.set(qn('w:fill'), color)
        cell._element.get_or_add_tcPr().append(shading_elm)
    except Exception as e:
        logger.debug(f"Не удалось установить фон ячейки: {e}")


def create_audio_params_summary(tech_info: Dict, params: Dict) -> str:
    """
    Создание краткого резюме параметров для заключения
    
    Args:
        tech_info: Техническая информация
        params: Параметры анализа
        
    Returns:
        Текст резюме
    """
    measurements = params.get('measurements', {})
    compliance = params.get('compliance', {})
    targets = params.get('targets', {})
    
    lufs = measurements.get('lufs')
    true_peak = measurements.get('true_peak')
    lra = measurements.get('lra')
    
    target_lufs = targets.get('target_lufs', -23.0)
    
    summary_parts = []
    
    # LUFS
    if lufs is not None:
        deviation = lufs - target_lufs
        deviation_str = f"+{deviation:.1f}" if deviation > 0 else f"{deviation:.1f}"
        status = "соответствует" if compliance.get('lufs_compliant') else "не соответствует"
        summary_parts.append(
            f"LUFS: {lufs:.1f} ({deviation_str} от {target_lufs}) - {status}"
        )
    
    # True Peak
    if true_peak is not None:
        status = "соответствует" if compliance.get('true_peak_compliant') else "превышен"
        summary_parts.append(
            f"True Peak: {true_peak:+.1f} dBTP - {status}"
        )
    
    # LRA
    if lra is not None:
        lra_max = targets.get('lra_max', 18.0)
        status = "соответствует" if compliance.get('lra_compliant') else "превышен"
        summary_parts.append(
            f"LRA: {lra:.1f} LU (макс. {lra_max}) - {status}"
        )
    
    return "\n".join(summary_parts)


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("Youlean Integration Helper - Test")
    print("=" * 60)
    
    # Пример данных
    sample_params = {
        'file_name': 'test.wav',
        'measurements': {
            'lufs': -23.1,
            'true_peak': -1.5,
            'lra': 12.5
        },
        'compliance': {
            'lufs_compliant': True,
            'true_peak_compliant': True,
            'lra_compliant': True,
            'overall_compliant': True
        },
        'targets': {
            'target_lufs': -23.0,
            'lufs_tolerance': 0.5,
            'true_peak_threshold': -2.0,
            'lra_max': 18.0
        }
    }
    
    # Создаем резюме
    summary = create_audio_params_summary({}, sample_params)
    print("\nAudio Parameters Summary:")
    print(summary)
    
    print("\n" + "=" * 60)
    print("Тест завершен")
    print("=" * 60)
