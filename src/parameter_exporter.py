"""
Parameter Exporter Module

Экспортер параметров аудиоанализа в различные форматы:
- CSV
- HTML
- JSON
"""

import csv
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)


class ParameterExporter:
    """Экспортер параметров в различные форматы"""
    
    def __init__(self, config: Dict = None):
        """
        Инициализация экспортера
        
        Args:
            config: Словарь с настройками
        """
        self.config = config or {}
        self._analyzer = None
        
        # Настройки экспорта
        self.export_config = self.config.get('export', {})
        self.csv_config = self.export_config.get('csv', {})
        self.html_config = self.export_config.get('html', {})
        
        # Настройки по умолчанию
        self.csv_delimiter = self.csv_config.get('delimiter', ',')
        self.csv_encoding = self.csv_config.get('encoding', 'utf-8')
        self.include_css = self.html_config.get('include_css', True)
        self.include_timestamp = self.html_config.get('include_timestamp', True)
        
        logger.info("ParameterExporter инициализирован")

    @property
    def analyzer(self):
        if self._analyzer is None:
            from src.audio_analyzer import AudioAnalyzer
            self._analyzer = AudioAnalyzer(self.config)
        return self._analyzer

    def analyze_audio_files(self, file_paths: List[str]) -> List[Dict]:
        """
        Анализ списка аудиофайлов
        
        Args:
            file_paths: Список путей к аудиофайлам
            
        Returns:
            Список словарей с результатами анализа
        """
        results = []
        
        for file_path in file_paths:
            try:
                logger.info(f"Анализ файла: {file_path}")
                analysis = self.analyzer.analyze_file(file_path)
                results.append(analysis)
            except Exception as e:
                logger.error(f"Ошибка анализа файла {file_path}: {e}")
                results.append({
                    'file_path': file_path,
                    'file_name': Path(file_path).name,
                    'error': str(e)
                })
        
        logger.info(f"Проанализировано файлов: {len(results)}")
        return results
    
    def export_to_csv(self, params_list: List[Dict], output_path: str) -> bool:
        """
        Экспорт параметров в CSV файл
        
        Args:
            params_list: Список словарей с параметрами
            output_path: Путь к выходному CSV файлу
            
        Returns:
            True если экспорт успешен, False в случае ошибки
        """
        try:
            logger.info(f"Экспорт в CSV: {output_path}")
            
            # Расширенный список полей для CSV
            fieldnames = [
                'file_name',
                'file_path',
                'duration_seconds',
                'duration_formatted',
                'sample_rate',
                'bit_depth',
                'channels',
                'channel_layout',
                'integrated_lufs',
                'true_peak_dbtp',
                'lra',
                'short_term_loudness',
                'momentary_loudness',
                'max_loudness',
                'min_loudness',
                'lufs_compliant',
                'true_peak_compliant',
                'lra_compliant',
                'overall_compliant',
                'target_lufs',
                'lufs_tolerance',
                'true_peak_threshold',
                'lra_max',
                'lufs_deviation',
                'true_peak_headroom',
                'analysis_timestamp'
            ]
            
            def format_duration(seconds):
                """Форматирование длительности в ЧЧ:ММ:СС"""
                if seconds is None:
                    return "N/A"
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                secs = int(seconds % 60)
                return f"{hours:02d}:{minutes:02d}:{secs:02d}"
            
            with open(output_path, 'w', newline='', encoding=self.csv_encoding) as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter=self.csv_delimiter)
                writer.writeheader()
                
                for params in params_list:
                    if 'error' in params:
                        logger.warning(f"Пропуск файла с ошибкой: {params['file_name']}")
                        continue
                    
                    measurements = params.get('measurements', {})
                    compliance = params.get('compliance', {})
                    targets = params.get('targets', {})
                    deviations = params.get('deviations', {})
                    
                    row = {
                        'file_name': params.get('file_name', ''),
                        'file_path': params.get('file_path', ''),
                        'duration_seconds': params.get('duration', ''),
                        'duration_formatted': format_duration(params.get('duration')),
                        'sample_rate': params.get('sample_rate', ''),
                        'bit_depth': '',  # Можно добавить если есть в анализе
                        'channels': params.get('channels', ''),
                        'channel_layout': params.get('channel_layout', ''),
                        'integrated_lufs': measurements.get('lufs', ''),
                        'true_peak_dbtp': measurements.get('true_peak', ''),
                        'lra': measurements.get('lra', ''),
                        'short_term_loudness': measurements.get('short_term_loudness', ''),
                        'momentary_loudness': measurements.get('momentary_loudness', ''),
                        'max_loudness': measurements.get('max_loudness', ''),
                        'min_loudness': measurements.get('min_loudness', ''),
                        'lufs_compliant': compliance.get('lufs_compliant', ''),
                        'true_peak_compliant': compliance.get('true_peak_compliant', ''),
                        'lra_compliant': compliance.get('lra_compliant', ''),
                        'overall_compliant': compliance.get('overall_compliant', ''),
                        'target_lufs': targets.get('target_lufs', ''),
                        'lufs_tolerance': targets.get('lufs_tolerance', ''),
                        'true_peak_threshold': targets.get('true_peak_threshold', ''),
                        'lra_max': targets.get('lra_max', ''),
                        'lufs_deviation': deviations.get('lufs_deviation', ''),
                        'true_peak_headroom': deviations.get('true_peak_headroom', ''),
                        'analysis_timestamp': datetime.now().isoformat()
                    }
                    writer.writerow(row)
            
            logger.info(f"CSV экспорт завершен: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка экспорта CSV: {e}")
            return False
    
    def export_to_html(self, params_list: List[Dict], output_path: str, 
                       title: str = "Audio Analysis Report") -> bool:
        """
        Экспорт параметров в HTML файл с таблицей
        
        Args:
            params_list: Список словарей с параметрами
            output_path: Путь к выходному HTML файлу
            title: Заголовок отчета
            
        Returns:
            True если экспорт успешен, False в случае ошибки
        """
        try:
            logger.info(f"Экспорт в HTML: {output_path}")
            
            def format_duration(seconds):
                """Форматирование длительности в ЧЧ:ММ:СС"""
                if seconds is None:
                    return "N/A"
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                secs = int(seconds % 60)
                return f"{hours:02d}:{minutes:02d}:{secs:02d}"

            def format_number(value, signed: bool = False):
                """Строковое представление чисел до сотых."""
                if value is None:
                    return "N/A"
                import math
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    return str(value)
                if not math.isfinite(numeric):
                    return "N/A"
                text = f"{numeric:.2f}"
                if signed and numeric > 0:
                    return f"+{text}"
                return text
                return str(value)
            
            # Генерация таблицы с расширенными параметрами
            table_rows = []
            for params in params_list:
                if 'error' in params:
                    continue
                
                file_name = params.get('file_name', 'N/A')
                duration = params.get('duration', 'N/A')
                duration_formatted = format_duration(duration)
                sample_rate = params.get('sample_rate', 'N/A')
                channel_layout = params.get('channel_layout', 'N/A')
                channel_order = params.get('channel_order', '')

                measurements = params.get('measurements', {})
                lufs = measurements.get('lufs', 'N/A')
                true_peak = measurements.get('true_peak', 'N/A')
                lra = measurements.get('lra', 'N/A')
                short_term = measurements.get('short_term_loudness', 'N/A')
                momentary = measurements.get('momentary_loudness', 'N/A')
                lufs_text = format_number(lufs)
                true_peak_text = format_number(true_peak)
                lra_text = format_number(lra)
                short_term_text = format_number(short_term)
                momentary_text = format_number(momentary)
                
                compliance = params.get('compliance', {})
                overall_compliant = compliance.get('overall_compliant', False)
                
                targets = params.get('targets', {})
                deviations = params.get('deviations', {})
                
                # Класс для статуса
                status_class = 'compliant' if overall_compliant else 'non-compliant'
                status_text = '✓ Compliant' if overall_compliant else '✗ Non-Compliant'
                
                # Цвет deviation
                lufs_dev = deviations.get('lufs_deviation')
                dev_color = '#4CAF50' if lufs_dev is None or abs(lufs_dev) <= 0.5 else ('#FF9800' if abs(lufs_dev) <= 1.0 else '#F44336')
                
                lufs_dev_text = format_number(lufs_dev, signed=True)

                row = f"""
                <tr>
                    <td><strong>{file_name}</strong></td>
                    <td>{duration_formatted}</td>
                    <td>{sample_rate} Hz</td>
                    <td>{channel_layout}{"<br><small>" + channel_order + "</small>" if channel_order else ""}</td>
                    <td><strong>{lufs_text} LUFS</strong><br><small style="color:{dev_color}">Δ {lufs_dev_text} LUFS</small></td>
                    <td>{true_peak_text} dBTP</td>
                    <td>{lra_text} LU</td>
                    <td>{short_term_text} LUFS</td>
                    <td>{momentary_text} LUFS</td>
                    <td class="{status_class}">{status_text}</td>
                </tr>
                """
                table_rows.append(row)
            
            # CSS стили
            css_styles = ""
            if self.include_css:
                css_styles = """
                <style>
                    body {
                        font-family: Arial, sans-serif;
                        margin: 20px;
                        background-color: #f5f5f5;
                    }
                    h1 {
                        color: #333;
                        border-bottom: 3px solid #6200EE;
                        padding-bottom: 10px;
                    }
                    h2 {
                        color: #555;
                        margin-top: 30px;
                    }
                    .summary {
                        display: flex;
                        gap: 20px;
                        margin-bottom: 20px;
                        flex-wrap: wrap;
                    }
                    .summary-box {
                        background: white;
                        padding: 15px 20px;
                        border-radius: 8px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    }
                    .summary-box strong {
                        color: #6200EE;
                        font-size: 24px;
                    }
                    .summary-box span {
                        color: #666;
                        font-size: 12px;
                    }
                    table {
                        border-collapse: collapse;
                        width: 100%;
                        background-color: white;
                        box-shadow: 0 1px 3px rgba(0,0,0,0.12);
                        margin-top: 20px;
                    }
                    th {
                        background-color: #6200EE;
                        color: white;
                        padding: 12px;
                        text-align: left;
                        font-size: 12px;
                    }
                    td {
                        padding: 10px;
                        border-bottom: 1px solid #ddd;
                        font-size: 12px;
                    }
                    tr:hover {
                        background-color: #f5f5f5;
                    }
                    .compliant {
                        background-color: #E8F5E9;
                        color: #2E7D32;
                        font-weight: bold;
                        padding: 4px 8px;
                        border-radius: 4px;
                    }
                    .non-compliant {
                        background-color: #FFEBEE;
                        color: #C62828;
                        font-weight: bold;
                        padding: 4px 8px;
                        border-radius: 4px;
                    }
                    .timestamp {
                        color: #666;
                        font-size: 12px;
                        margin-top: 20px;
                        padding: 10px;
                        background: white;
                        border-radius: 8px;
                    }
                    .params-info {
                        margin-top: 20px;
                        padding: 15px;
                        background: white;
                        border-radius: 8px;
                        font-size: 11px;
                        color: #666;
                    }
                    .params-info h3 {
                        margin: 0 0 10px 0;
                        color: #333;
                    }
                </style>
                """
            
            # Подсчет статистики
            total_files = len([p for p in params_list if 'error' not in p])
            compliant_count = len([p for p in params_list 
                                   if 'error' not in p 
                                   and p.get('compliance', {}).get('overall_compliant', False)])
            
            targets = params_list[0].get('targets', {}) if params_list else {}
            target_lufs = targets.get('target_lufs', -23.0)
            
            # Сборка HTML
            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    {css_styles}
</head>
<body>
    <h1>🎵 {title}</h1>
    
    <div class="summary">
        <div class="summary-box">
            <strong>{total_files}</strong><br>
            <span>Всего файлов</span>
        </div>
        <div class="summary-box">
            <strong style="color: #4CAF50;">{compliant_count}</strong><br>
            <span>Соответствует</span>
        </div>
        <div class="summary-box">
            <strong style="color: #F44336;">{total_files - compliant_count}</strong><br>
            <span>Не соответствует</span>
        </div>
        <div class="summary-box">
            <strong>{target_lufs} LUFS</strong><br>
            <span>Целевая громкость</span>
        </div>
    </div>
    
    <table>
        <thead>
            <tr>
                <th>Файл</th>
                <th>Длительность</th>
                <th>Sample Rate</th>
                <th>Каналы</th>
                <th>LUFS</th>
                <th>True Peak</th>
                <th>LRA</th>
                <th>Short-term</th>
                <th>Momentary</th>
                <th>Статус</th>
            </tr>
        </thead>
        <tbody>
            {''.join(table_rows)}
        </tbody>
    </table>
    
    <div class="params-info">
        <h3>📋 Параметры анализа:</h3>
        <p>
            <strong>Целевая громкость:</strong> {target_lufs} LUFS | 
            <strong>Tolerance:</strong> ±{targets.get('lufs_tolerance', 0.5)} LUFS | 
            <strong>True Peak Limit:</strong> {targets.get('true_peak_threshold', -2.0)} dBTP | 
            <strong>Max LRA:</strong> {targets.get('lra_max', 18.0)} LU
        </p>
    </div>
    
    <div class="timestamp">
        🕐 Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    </div>
</body>
</html>
"""
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            logger.info(f"HTML экспорт завершен: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка экспорта HTML: {e}")
            return False
    
    def export_to_json(self, params_list: List[Dict], output_path: str) -> bool:
        """
        Экспорт параметров в JSON файл
        
        Args:
            params_list: Список словарей с параметрами
            output_path: Путь к выходному JSON файлу
            
        Returns:
            True если экспорт успешен, False в случае ошибки
        """
        try:
            logger.info(f"Экспорт в JSON: {output_path}")
            
            def convert_numpy(obj):
                """Конвертация numpy типов в Python типы для JSON сериализации"""
                try:
                    import numpy as _np
                    if isinstance(obj, _np.floating):
                        return float(obj)
                    elif isinstance(obj, _np.integer):
                        return int(obj)
                    elif isinstance(obj, _np.bool_):
                        return bool(obj)
                    elif isinstance(obj, _np.ndarray):
                        return obj.tolist()
                except ImportError:
                    pass
                if isinstance(obj, dict):
                    return {k: convert_numpy(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_numpy(item) for item in obj]
                return obj
            
            # Конвертируем numpy типы
            export_data = {
                'export_timestamp': datetime.now().isoformat(),
                'total_files': len(params_list),
                'files': [convert_numpy(f) for f in params_list]
            }
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"JSON экспорт завершен: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка экспорта JSON: {e}")
            return False
    
    def export_to_txt(self, params_list: List[Dict], output_path: str) -> bool:
        """
        Экспорт параметров в TXT файл в виде списка
        
        Args:
            params_list: Список словарей с параметрами
            output_path: Путь к выходному TXT файлу
            
        Returns:
            True если экспорт успешен, False в случае ошибки
        """
        try:
            logger.info(f"Экспорт в TXT: {output_path}")
            
            def format_duration(seconds):
                """Форматирование длительности в ЧЧ:ММ:СС"""
                if seconds is None:
                    return "N/A"
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                secs = int(seconds % 60)
                return f"{hours:02d}:{minutes:02d}:{secs:02d}"
            
            def format_value(value, unit: str = ""):
                """Форматирование значения с единицей измерения"""
                if value is None:
                    return "N/A"
                import math
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    return str(value)
                if not math.isfinite(numeric):
                    return "N/A"
                text = f"{numeric:.2f}"
                return f"{text}{unit}" if unit else text
            
            lines = []
            lines.append("=" * 70)
            lines.append("AUDIO ANALYSIS REPORT")
            lines.append("=" * 70)
            lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"Total files analyzed: {len([p for p in params_list if 'error' not in p])}")
            lines.append("")
            
            for i, params in enumerate(params_list, 1):
                lines.append("-" * 70)
                
                if 'error' in params:
                    lines.append(f"FILE {i}: {params.get('file_name', 'Unknown')}")
                    lines.append(f"  ERROR: {params['error']}")
                    continue
                
                file_name = params.get('file_name', 'N/A')
                duration = params.get('duration', 'N/A')
                duration_formatted = format_duration(duration)
                sample_rate = params.get('sample_rate', 'N/A')
                channels = params.get('channels', 'N/A')
                channel_layout = params.get('channel_layout', 'N/A')
                channel_order = params.get('channel_order', '')

                lines.append(f"FILE {i}: {file_name}")
                lines.append("")

                lines.append("  BASIC INFO:")
                lines.append(f"    - Duration: {duration_formatted} ({duration} seconds)")
                lines.append(f"    - Sample Rate: {sample_rate} Hz")
                lines.append(f"    - Channels: {channels}")
                lines.append(f"    - Channel Layout: {channel_layout}")
                if channel_order:
                    lines.append(f"    - Channel Order: {channel_order}")
                lines.append("")
                
                measurements = params.get('measurements', {})
                lufs = measurements.get('lufs')
                true_peak = measurements.get('true_peak')
                lra = measurements.get('lra')
                short_term = measurements.get('short_term_loudness')
                momentary = measurements.get('momentary_loudness')
                max_loudness = measurements.get('max_loudness')
                min_loudness = measurements.get('min_loudness')
                
                lines.append("  LOUDNESS MEASUREMENTS:")
                lines.append(f"    - Integrated LUFS: {format_value(lufs, ' LUFS')}")
                lines.append(f"    - True Peak: {format_value(true_peak, ' dBTP')}")
                lines.append(f"    - Loudness Range (LRA): {format_value(lra, ' LU')}")
                lines.append(f"    - Short-term Loudness: {format_value(short_term, ' LUFS')}")
                lines.append(f"    - Momentary Loudness: {format_value(momentary, ' LUFS')}")
                lines.append(f"    - Max Loudness: {format_value(max_loudness, ' LUFS')}")
                lines.append(f"    - Min Loudness: {format_value(min_loudness, ' LUFS')}")
                lines.append("")
                
                compliance = params.get('compliance', {})
                targets = params.get('targets', {})
                deviations = params.get('deviations', {})
                
                target_lufs = targets.get('target_lufs', -23.0)
                lufs_tolerance = targets.get('lufs_tolerance', 0.5)
                true_peak_threshold = targets.get('true_peak_threshold', -2.0)
                lra_max = targets.get('lra_max', 18.0)
                
                lines.append("  COMPLIANCE CHECK:")
                lines.append(f"    - Target LUFS: {target_lufs} ±{lufs_tolerance} LUFS")
                lines.append(f"    - True Peak Limit: ≤ {true_peak_threshold} dBTP")
                lines.append(f"    - Max LRA: ≤ {lra_max} LU")
                lines.append("")
                
                lufs_dev = deviations.get('lufs_deviation')
                peak_headroom = deviations.get('true_peak_headroom')
                
                lines.append("  DEVIATIONS:")
                lines.append(f"    - LUFS Deviation: {format_value(lufs_dev, ' LUFS')}")
                lines.append(f"    - True Peak Headroom: {format_value(peak_headroom, ' dB')}")
                lines.append("")
                
                lines.append("  STATUS:")
                compliant = compliance.get('overall_compliant', False)
                status_text = "✓ COMPLIANT" if compliant else "✗ NON-COMPLIANT"
                lines.append(f"    - {status_text}")
                
                lufs_status = "✓" if compliance.get('lufs_compliant') else "✗"
                peak_status = "✓" if compliance.get('true_peak_compliant') else "✗"
                lra_status = "✓" if compliance.get('lra_compliant') else "✗"
                
                lines.append(f"    - LUFS {lufs_status} | True Peak {peak_status} | LRA {lra_status}")
                
                lines.append("")
            
            lines.append("=" * 70)
            lines.append("END OF REPORT")
            lines.append("=" * 70)
            
            content = "\n".join(lines)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"TXT экспорт завершен: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка экспорта TXT: {e}")
            return False
    
    def analyze_and_export(
        self,
        audio_files: List[str],
        output_dir: str,
        formats: List[str] = ['csv', 'html'],
        report_name: str = "audio_analysis"
    ) -> Dict[str, str]:
        """
        Полный анализ файлов и экспорт во все форматы
        
        Args:
            audio_files: Список путей к аудиофайлам
            output_dir: Выходная директория
            formats: Список форматов для экспорта ['csv', 'html', 'json']
            report_name: Базовое имя для файлов отчета
            
        Returns:
            Словарь с путями к созданным файлам
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Анализ файлов
        logger.info(f"Начало анализа {len(audio_files)} файлов")
        params_list = self.analyze_audio_files(audio_files)
        
        # Экспорт в запрошенные форматы
        result_paths = {}
        
        if 'csv' in formats:
            csv_path = str(output_path / f"{report_name}.csv")
            if self.export_to_csv(params_list, csv_path):
                result_paths['csv'] = csv_path
        
        if 'html' in formats:
            html_path = str(output_path / f"{report_name}.html")
            if self.export_to_html(params_list, html_path, title=f"Audio Analysis Report - {report_name}"):
                result_paths['html'] = html_path
        
        if 'json' in formats:
            json_path = str(output_path / f"{report_name}.json")
            if self.export_to_json(params_list, json_path):
                result_paths['json'] = json_path
        
        if 'txt' in formats:
            txt_path = str(output_path / f"{report_name}.txt")
            if self.export_to_txt(params_list, txt_path):
                result_paths['txt'] = txt_path
        
        logger.info(f"Экспорт завершен. Создано файлов: {len(result_paths)}")
        return result_paths


def export_audio_report(
    audio_files: List[str],
    output_dir: str,
    config: Dict = None,
    formats: List[str] = ['csv', 'html', 'txt']
) -> Dict[str, str]:
    """
    Удобная функция для быстрого экспорта аудио отчета
    
    Args:
        audio_files: Список путей к аудиофайлам
        output_dir: Выходная директория
        config: Настройки
        formats: Форматы экспорта ['csv', 'html', 'json', 'txt']
        
    Returns:
        Словарь с путями к созданным файлам
    """
    exporter = ParameterExporter(config)
    return exporter.analyze_and_export(audio_files, output_dir, formats)


if __name__ == "__main__":
    # Тестирование модуля
    logging.basicConfig(level=logging.INFO)
    
    # Пример конфигурации
    config = {
        'audio': {
            'target_lufs': -23.0,
            'lufs_tolerance': 0.5,
            'true_peak': -2.0,
            'lra_max': 18.0,
            'sample_rate': 48000
        },
        'export': {
            'csv': {
                'delimiter': ',',
                'encoding': 'utf-8'
            },
            'html': {
                'include_css': True,
                'include_timestamp': True
            }
        }
    }
    
    exporter = ParameterExporter(config)
    
    # Тест с примером файла (замените на реальный путь)
    # result = exporter.analyze_and_export(
    #     audio_files=['path/to/audio.wav'],
    #     output_dir='./exports',
    #     formats=['csv', 'html'],
    #     report_name='test_report'
    # )
    # print(result)
    
    print("ParameterExporter готов к использованию!")
