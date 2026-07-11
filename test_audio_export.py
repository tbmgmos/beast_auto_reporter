"""
Test Script for Audio Analysis CSV/HTML Integration

Тестирование новых модулей с прямым импортом.

Запуск:
    python3 test_audio_export.py
"""

import os
import sys
import tempfile
import logging

# Добавляем путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_audio_analyzer(config):
    """Тест AudioAnalyzer"""
    logger.info("=" * 60)
    logger.info("Тест 1: AudioAnalyzer")
    logger.info("=" * 60)
    
    try:
        # Прямой импорт
        import importlib.util
        spec = importlib.util.spec_from_file_location("audio_analyzer", "src/audio_analyzer.py")
        audio_analyzer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audio_analyzer)
        
        AudioAnalyzer = audio_analyzer.AudioAnalyzer
        analyzer = AudioAnalyzer(config)
        logger.info("✅ AudioAnalyzer инициализирован")
        
        # Создаем тестовый WAV файл
        import numpy as np
        import soundfile as sf
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            sample_rate = 48000
            duration = 5
            t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
            audio_data = np.random.randn(len(t)).astype(np.float32) * 0.1
            sf.write(tmp.name, audio_data, sample_rate)
            logger.info(f"✅ Тестовый файл создан")
        
        result = analyzer.analyze_file(tmp.name)
        
        logger.info(f"✅ Анализ завершен")
        logger.info(f"   File: {result['file_name']}")
        logger.info(f"   Duration: {result['duration']}s")
        
        if 'measurements' in result:
            m = result['measurements']
            logger.info(f"   LUFS: {m.get('lufs')}")
            logger.info(f"   True Peak: {m.get('true_peak')}")
            logger.info(f"   LRA: {m.get('lra')}")
        
        if 'compliance' in result:
            c = result['compliance']
            logger.info(f"   Overall Compliant: {c.get('overall_compliant')}")
        
        os.unlink(tmp.name)
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_parameter_exporter(config, audio_result):
    """Тест ParameterExporter"""
    logger.info("\n" + "=" * 60)
    logger.info("Тест 2: ParameterExporter")
    logger.info("=" * 60)
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("parameter_exporter", "src/parameter_exporter.py")
        param_exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(param_exporter)
        
        ParameterExporter = param_exporter.ParameterExporter
        exporter = ParameterExporter(config)
        logger.info("✅ ParameterExporter инициализирован")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, 'test.csv')
            html_path = os.path.join(tmpdir, 'test.html')
            
            if audio_result:
                success = exporter.export_csv([audio_result], csv_path)
                if success:
                    logger.info(f"✅ CSV создан: {csv_path}")
                    with open(csv_path, 'r') as f:
                        content = f.read()
                        if 'integrated_lufs' in content:
                            logger.info("✅ CSV содержит данные LUFS")
                
                success = exporter.export_html([audio_result], html_path)
                if success:
                    logger.info(f"✅ HTML создан: {html_path}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_table_parser():
    """Тест TableParser"""
    logger.info("\n" + "=" * 60)
    logger.info("Тест 3: TableParser")
    logger.info("=" * 60)
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("table_parser", "src/table_parser.py")
        tbl_parser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tbl_parser)
        
        TableParser = tbl_parser.TableParser
        parser = TableParser()
        logger.info("✅ TableParser инициализирован")
        
        sample_csv = """file_name,duration_seconds,integrated_lufs,true_peak_dbtp,lra,overall_compliant
test.wav,100.5,-23.1,-1.5,12.5,True
"""
        
        with tempfile.NamedTemporaryFile(suffix='.csv', mode='w', delete=False) as tmp:
            tmp.write(sample_csv)
            tmp_path = tmp.name
        
        columns = parser.auto_detect_columns(tmp_path)
        logger.info(f"✅ Автоопределено колонок: {len(columns)}")
        logger.info(f"   {columns}")
        
        os.unlink(tmp_path)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_youlean_analyzer(config):
    """Тест YouleanAnalyzer"""
    logger.info("\n" + "=" * 60)
    logger.info("Тест 4: YouleanAnalyzer")
    logger.info("=" * 60)
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("youlean_analyzer", "src/youlean_analyzer.py")
        youlean_analyzer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(youlean_analyzer)
        
        YouleanAnalyzer = youlean_analyzer.YouleanAnalyzer
        analyzer = YouleanAnalyzer(config)
        logger.info("✅ YouleanAnalyzer инициализирован")
        
        # Проверяем наличие методов
        assert hasattr(analyzer, 'parse_youlean_csv'), "Нет parse_youlean_csv"
        assert hasattr(analyzer, 'analyze_with_pyloudnorm'), "Нет analyze_with_pyloudnorm"
        assert hasattr(analyzer, 'analyze_file'), "Нет analyze_file"
        logger.info("✅ Все методы присутствуют")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_youlean_integration(config, audio_result):
    """Тест youlean_integration"""
    logger.info("\n" + "=" * 60)
    logger.info("Тест 5: youlean_integration")
    logger.info("=" * 60)
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("youlean_integration", "src/youlean_integration.py")
        youlean_int = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(youlean_int)
        
        create_summary = youlean_int.create_audio_params_summary
        populate_table = youlean_int.populate_table_with_audio_params
        logger.info("✅ Функции импортированы")
        
        # Тест создания резюме
        sample_params = {
            'file_name': 'test.wav',
            'measurements': {'lufs': -23.1, 'true_peak': -1.5, 'lra': 12.5},
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
        
        summary = create_summary({}, sample_params)
        logger.info("✅ Резюме создано:")
        for line in summary.split('\n'):
            logger.info(f"   {line}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_full_workflow(config):
    """Тест полного workflow"""
    logger.info("\n" + "=" * 60)
    logger.info("Тест 6: Полный workflow")
    logger.info("=" * 60)
    
    try:
        import importlib.util
        import numpy as np
        import soundfile as sf
        
        # Загружаем модули
        spec_ya = importlib.util.spec_from_file_location("youlean_analyzer", "src/youlean_analyzer.py")
        youlean_analyzer = importlib.util.module_from_spec(spec_ya)
        spec_ya.loader.exec_module(youlean_analyzer)
        
        spec_pe = importlib.util.spec_from_file_location("parameter_exporter", "src/parameter_exporter.py")
        param_exporter = importlib.util.module_from_spec(spec_pe)
        spec_pe.loader.exec_module(param_exporter)
        
        YouleanAnalyzer = youlean_analyzer.YouleanAnalyzer
        ParameterExporter = param_exporter.ParameterExporter
        
        # Создаем тестовый файл
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            sample_rate = 48000
            duration = 2
            t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
            audio_data = np.random.randn(len(t)).astype(np.float32) * 0.1
            sf.write(tmp.name, audio_data, sample_rate)
            test_file = tmp.name
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Анализ
            analyzer = YouleanAnalyzer(config)
            params = analyzer.analyze_with_pyloudnorm(test_file, config)
            logger.info(f"✅ Анализ: LUFS={params['measurements']['lufs']}")
            
            # Экспорт
            exporter = ParameterExporter(config)
            paths = exporter.analyze_and_export([test_file], tmpdir, ['csv', 'html'])
            
            logger.info("✅ Экспорт завершен")
            for fmt, path in paths.items():
                if path and os.path.exists(path):
                    size = os.path.getsize(path)
                    logger.info(f"   {fmt.upper()}: {path} ({size} bytes)")
            
            # Проверка CSV
            if paths.get('csv'):
                with open(paths['csv'], 'r') as f:
                    content = f.read()
                    if 'integrated_lufs' in content:
                        logger.info("✅ CSV валиден")
        
        os.unlink(test_file)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Главная функция"""
    logger.info("\n" + "=" * 60)
    logger.info("Audio Analysis CSV/HTML Integration - Tests")
    logger.info("=" * 60)
    
    # Загрузка конфигурации
    try:
        import yaml
        with open('config/settings.yaml', 'r') as f:
            config = yaml.safe_load(f)
        logger.info("✅ Конфигурация загружена")
    except Exception as e:
        logger.error(f"❌ Ошибка конфигурации: {e}")
        config = {}
    
    results = []
    
    # Тесты
    audio_result = test_audio_analyzer(config)
    results.append(("AudioAnalyzer", audio_result is not None))
    
    results.append(("ParameterExporter", test_parameter_exporter(config, audio_result)))
    results.append(("TableParser", test_table_parser()))
    results.append(("YouleanAnalyzer", test_youlean_analyzer(config)))
    results.append(("youlean_integration", test_youlean_integration(config, audio_result)))
    results.append(("Полный workflow", test_full_workflow(config)))
    
    # Итоги
    logger.info("\n" + "=" * 60)
    logger.info("ИТОГИ")
    logger.info("=" * 60)
    
    passed = sum(1 for _, s in results if s)
    failed = sum(1 for _, s in results if not s)
    
    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        logger.info(f"   {status}: {name}")
    
    logger.info(f"\nВсего: {passed}/{len(results)} тестов пройдено")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
