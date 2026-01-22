#!/usr/bin/env python3
"""
Example Usage - Beast Auto Reporter V2

Пример использования системы для создания отчета
"""

import sys
from pathlib import Path
import logging

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from audio_analyzer import AudioAnalyzer
from defect_detector import DefectDetector
from template_report_generator import TemplateReportGenerator
from pdf_extractor import PDFExtractor
from timecode_analyzer import TimecodeAnalyzer
from llm_integration import LLMIntegration
import yaml

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config():
    """Загрузка конфигурации"""
    config_path = Path(__file__).parent / 'config' / 'settings.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def read_params_file(params_path):
    """Чтение файла Параметры.txt"""
    with open(params_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    import re
    
    params = {
        'target_lufs': -23.0,
        'lufs_tolerance': 0.5,
        'true_peak': -2.0,
        'lra_max': 18.0,
        'sample_rate': 48000,
        'bit_depth': 24
    }
    
    # Парсинг
    lufs_match = re.search(r'(-?\d+\.?\d*)\s*LUFS', content)
    if lufs_match:
        params['target_lufs'] = float(lufs_match.group(1))
    
    return params, content


def main():
    """Основная функция - пример использования"""
    
    print("=" * 80)
    print("🎵 Beast Auto Reporter V2 - Example Usage")
    print("=" * 80)
    print()
    
    # === ШАГ 1: Загрузка конфигурации ===
    print("📁 Шаг 1: Загрузка конфигурации...")
    config = load_config()
    print("✓ Конфигурация загружена\n")
    
    # === ШАГ 2: Чтение параметров ===
    print("📝 Шаг 2: Чтение Параметры.txt...")
    
    # ЗАМЕНИТЕ на реальный путь к вашему файлу
    params_path = "/Users/vladog/Desktop/отчет_petr_2_s1_e2_2024_09_12_rus/Параметры.txt"
    
    if Path(params_path).exists():
        params_dict, params_text = read_params_file(params_path)
        config['audio'].update(params_dict)
        print(f"✓ Параметры загружены:")
        print(f"  - Target LUFS: {params_dict['target_lufs']}")
        print(f"  - TRUE PEAK: {params_dict['true_peak']}")
        print(f"  - LRA: {params_dict['lra_max']}\n")
    else:
        print(f"⚠️  Файл не найден: {params_path}")
        print("   Используются параметры по умолчанию\n")
        params_dict = None
        params_text = None
    
    # === ШАГ 3: Инициализация компонентов ===
    print("🔧 Шаг 3: Инициализация компонентов...")
    
    analyzer = AudioAnalyzer(config)
    detector = DefectDetector(config)
    generator = TemplateReportGenerator()
    pdf_extractor = PDFExtractor()
    tc_analyzer = TimecodeAnalyzer()
    llm = LLMIntegration(config)
    
    print("✓ Все компоненты инициализированы\n")
    
    # === ШАГ 4: Указание путей к файлам ===
    print("📂 Шаг 4: Пути к файлам...")
    print("⚠️  УКАЖИТЕ РЕАЛЬНЫЕ ПУТИ К ВАШИМ ФАЙЛАМ:\n")
    
    # ЗАМЕНИТЕ на реальные пути
    audio_20_path = "/path/to/your/audio_2.0.wav"
    audio_51_path = "/path/to/your/audio_5.1.wav"
    video_path = "/path/to/your/video.mp4"
    pdf_20_path = "/path/to/your/report_2.0.pdf"
    pdf_51_path = "/path/to/your/report_5.1.pdf"
    
    # Проверка существования файлов
    files_to_check = {
        "Аудио 2.0": audio_20_path,
        "Аудио 5.1": audio_51_path,
        "Видео": video_path,
        "PDF 2.0": pdf_20_path,
        "PDF 5.1": pdf_51_path
    }
    
    available_files = {}
    for name, path in files_to_check.items():
        if Path(path).exists():
            print(f"  ✓ {name}: {path}")
            available_files[name] = path
        else:
            print(f"  ✗ {name}: не найден")
    
    if not available_files:
        print("\n❌ Файлы не найдены!")
        print("📝 Отредактируйте example_usage.py и укажите реальные пути к файлам")
        return
    
    print()
    
    # === ШАГ 5: Анализ хронометража ===
    print("⏱️  Шаг 5: Анализ хронометража...")
    
    timecode_info = tc_analyzer.analyze_all_files(
        audio_20=available_files.get("Аудио 2.0"),
        audio_51=available_files.get("Аудио 5.1"),
        video=available_files.get("Видео")
    )
    
    print("✓ Хронометраж проанализирован:")
    for file_type, info in timecode_info['files'].items():
        print(f"  - {file_type}: {info['duration']:.2f} сек")
    
    # Проверка совпадений
    for comp in timecode_info['comparisons']:
        if comp['matches']:
            print(f"  ✓ {comp['file1_type']} и {comp['file2_type']}: совпадают")
        else:
            print(f"  ⚠️  {comp['file1_type']} и {comp['file2_type']}: разница {comp['difference']:.2f} сек")
    print()
    
    # === ШАГ 6: Извлечение из PDF ===
    if available_files.get("PDF 2.0") or available_files.get("PDF 5.1"):
        print("📄 Шаг 6: Извлечение данных из PDF...")
        
        if available_files.get("PDF 2.0"):
            pdf_info_20 = pdf_extractor.extract_technical_info(available_files["PDF 2.0"])
            print(f"  ✓ PDF 2.0: LUFS={pdf_info_20.get('lufs')}, TP={pdf_info_20.get('true_peak')}")
        
        if available_files.get("PDF 5.1"):
            pdf_info_51 = pdf_extractor.extract_technical_info(available_files["PDF 5.1"])
            print(f"  ✓ PDF 5.1: LUFS={pdf_info_51.get('lufs')}, TP={pdf_info_51.get('true_peak')}")
        print()
    
    # === ШАГ 7: Анализ аудио ===
    print("📊 Шаг 7: Анализ аудио параметров...")
    
    analysis_results = {}
    
    if available_files.get("Аудио 2.0"):
        print("  Анализ 2.0...")
        analysis_results['20'] = analyzer.analyze_file(available_files["Аудио 2.0"])
        print(f"    LUFS: {analysis_results['20']['measurements']['lufs']}")
        print(f"    TRUE PEAK: {analysis_results['20']['measurements']['true_peak']}")
        print(f"    LRA: {analysis_results['20']['measurements']['lra']}")
    
    if available_files.get("Аудио 5.1"):
        print("  Анализ 5.1...")
        analysis_results['51'] = analyzer.analyze_file(available_files["Аудио 5.1"])
        print(f"    LUFS: {analysis_results['51']['measurements']['lufs']}")
        print(f"    TRUE PEAK: {analysis_results['51']['measurements']['true_peak']}")
        print(f"    LRA: {analysis_results['51']['measurements']['lra']}")
    print()
    
    # === ШАГ 8: Детекция дефектов ===
    print("🔍 Шаг 8: Детекция дефектов...")
    
    all_defects = []
    
    if available_files.get("Аудио 2.0"):
        print("  Детекция в 2.0...")
        defects_20 = detector.analyze_file(available_files["Аудио 2.0"], "2.0")
        all_defects.extend(defects_20)
        print(f"    Найдено: {len(defects_20)} дефектов")
    
    if available_files.get("Аудио 5.1"):
        print("  Детекция в 5.1...")
        defects_51 = detector.analyze_file(available_files["Аудио 5.1"], "5.1")
        all_defects.extend(defects_51)
        print(f"    Найдено: {len(defects_51)} дефектов")
    
    print(f"\n  📝 Всего дефектов: {len(all_defects)}")
    
    # Сортируем по таймкоду
    all_defects.sort(key=lambda d: getattr(d, 'timecode_in', '00:00:00:00'))
    
    # Показываем первые 5
    print("\n  Первые 5 дефектов:")
    for i, defect in enumerate(all_defects[:5], 1):
        print(f"    {i}. {getattr(defect, 'timecode_in')} - {getattr(defect, 'description')[:50]}...")
    print()
    
    # === ШАГ 9: Генерация отчета ===
    print("📄 Шаг 9: Генерация отчета по шаблону...")
    
    output_path = Path(__file__).parent / 'output' / 'example_report.docx'
    output_path.parent.mkdir(exist_ok=True)
    
    report_path = generator.create_report_from_template(
        defects=all_defects,
        output_path=str(output_path),
        params_info=params_dict,
        timecode_info=timecode_info
    )
    
    print(f"✓ Отчет создан: {report_path}\n")
    
    # === ШАГ 10: Опционально - AI заключение ===
    if llm.check_ollama_status():
        print("🤖 Шаг 10: Генерация AI заключения...")
        
        primary_analysis = analysis_results.get('20') or analysis_results.get('51')
        file_info = {'file_name': 'example_audio.wav'}
        
        conclusion = llm.generate_conclusion(primary_analysis, all_defects, file_info)
        
        print("✓ Заключение сгенерировано:")
        print(conclusion[:200] + "...\n")
    else:
        print("⚠️  Шаг 10: Ollama недоступна, пропускаем AI заключение\n")
    
    # === ИТОГ ===
    print("=" * 80)
    print("✅ ГОТОВО!")
    print(f"📄 Отчет: {report_path}")
    print(f"📝 Дефектов: {len(all_defects)}")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹  Прервано пользователем")
    except Exception as e:
        print(f"\n\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

