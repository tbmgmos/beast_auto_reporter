"""
Diagnostics Module

Диагностика системы и зависимостей при запуске приложения
"""

import sys
import os
import logging
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SystemDiagnostics:
    """Класс для сбора и логирования диагностической информации"""
    
    def __init__(self):
        self.info = {}
        self.errors = []
        self.warnings = []
    
    def collect_all(self) -> Dict:
        """
        Сбор всей диагностической информации
        
        Returns:
            Словарь с диагностической информацией
        """
        self.info['timestamp'] = datetime.now().isoformat()
        self.info['python'] = self._get_python_info()
        self.info['packages'] = self._get_package_versions()
        self.info['ffprobe'] = self._get_ffprobe_info()
        self.info['system'] = self._get_system_info()
        self.info['errors'] = self.errors
        self.info['warnings'] = self.warnings
        
        return self.info
    
    def _get_python_info(self) -> Dict:
        """Информация о Python"""
        return {
            'version': sys.version,
            'version_short': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'executable': sys.executable,
            'platform': sys.platform,
        }
    
    def _get_package_versions(self) -> Dict:
        """Версии ключевых пакетов"""
        packages = {}
        
        # PyMuPDF
        try:
            import fitz
            packages['PyMuPDF'] = fitz.__version__ if hasattr(fitz, '__version__') else 'unknown'
        except ImportError as e:
            packages['PyMuPDF'] = f'ERROR: {e}'
            self.errors.append(f"PyMuPDF не найден: {e}")
        
        # PyQt5
        try:
            from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
            packages['PyQt5'] = PYQT_VERSION_STR
            packages['Qt'] = QT_VERSION_STR
        except ImportError as e:
            packages['PyQt5'] = f'ERROR: {e}'
            self.errors.append(f"PyQt5 не найден: {e}")
        
        # ffmpeg-python
        try:
            import ffmpeg
            packages['ffmpeg-python'] = ffmpeg.__version__ if hasattr(ffmpeg, '__version__') else 'unknown'
        except ImportError as e:
            packages['ffmpeg-python'] = f'ERROR: {e}'
            self.warnings.append(f"ffmpeg-python не найден: {e}")
        
        # python-docx
        try:
            import docx
            packages['python-docx'] = docx.__version__ if hasattr(docx, '__version__') else 'unknown'
        except ImportError as e:
            packages['python-docx'] = f'ERROR: {e}'
            self.errors.append(f"python-docx не найден: {e}")
        
        # pyloudnorm
        try:
            import pyloudnorm
            packages['pyloudnorm'] = pyloudnorm.__version__ if hasattr(pyloudnorm, '__version__') else 'unknown'
        except ImportError as e:
            packages['pyloudnorm'] = f'ERROR: {e}'
            self.errors.append(f"pyloudnorm не найден: {e}")
        
        return packages
    
    def _get_ffprobe_info(self) -> Dict:
        """Информация о ffprobe"""
        ffprobe_info = {
            'found': False,
            'path': None,
            'version': None,
            'test_result': None,
            'test_error': None
        }
        
        # Поиск ffprobe
        ffprobe_path = self._find_ffprobe()
        
        if ffprobe_path:
            ffprobe_info['found'] = True
            ffprobe_info['path'] = str(ffprobe_path)
            
            # Получение версии
            try:
                result = subprocess.run(
                    [ffprobe_path, '-version'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    version_line = result.stdout.split('\n')[0]
                    ffprobe_info['version'] = version_line
                else:
                    ffprobe_info['version'] = f'ERROR: {result.stderr}'
                    self.warnings.append(f"Не удалось получить версию ffprobe: {result.stderr}")
            except Exception as e:
                ffprobe_info['version'] = f'ERROR: {e}'
                self.warnings.append(f"Ошибка при получении версии ffprobe: {e}")
            
            # Тест работы ffprobe
            test_result = self._test_ffprobe(ffprobe_path)
            ffprobe_info['test_result'] = test_result['status']
            ffprobe_info['test_error'] = test_result.get('error')
            
            if test_result['status'] != 'OK':
                self.warnings.append(f"Тест ffprobe провалился: {test_result.get('error')}")
        else:
            ffprobe_info['found'] = False
            self.errors.append("ffprobe не найден в системе")
        
        return ffprobe_info
    
    def _find_ffprobe(self) -> Optional[Path]:
        """Поиск ffprobe"""
        # 1. Проверяем bundle (для .app)
        if getattr(sys, 'frozen', False):
            bundle_dir = Path(sys._MEIPASS)
            
            # Вариант 1: ffmpeg/ffprobe
            ffprobe_bundle = bundle_dir / 'ffmpeg' / 'ffprobe'
            if ffprobe_bundle.exists():
                return ffprobe_bundle
            
            # Вариант 2: ../Frameworks/ffmpeg/ffprobe
            ffprobe_frameworks = bundle_dir.parent / 'Frameworks' / 'ffmpeg' / 'ffprobe'
            if ffprobe_frameworks.exists():
                return ffprobe_frameworks
        
        # 2. Проверяем PATH
        import shutil
        ffprobe_path = shutil.which('ffprobe')
        if ffprobe_path:
            return Path(ffprobe_path)
        
        # 3. Проверяем стандартные места
        standard_paths = [
            Path('/opt/homebrew/bin/ffprobe'),
            Path('/usr/local/bin/ffprobe'),
            Path.home() / '.local' / 'bin' / 'ffprobe'
        ]
        
        for path in standard_paths:
            if path.exists():
                return path
        
        return None
    
    def _test_ffprobe(self, ffprobe_path: Path) -> Dict:
        """
        Тест работы ffprobe
        
        Тестируем способность ffprobe обрабатывать синтетический аудио поток
        """
        try:
            # Тестовая команда: генерация синтетического аудио и анализ
            # lavfi = libavfilter (синтетические источники)
            # sine=frequency=1000:duration=1 = синусоида 1кГц, 1 секунда
            cmd = [
                str(ffprobe_path),
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-f', 'lavfi',
                '-i', 'sine=frequency=1000:duration=0.1'
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                # Пытаемся распарсить JSON
                try:
                    data = json.loads(result.stdout)
                    if 'streams' in data and len(data['streams']) > 0:
                        return {
                            'status': 'OK',
                            'details': 'ffprobe работает корректно'
                        }
                    else:
                        return {
                            'status': 'WARNING',
                            'error': 'Неожиданный формат вывода',
                            'details': result.stdout[:200]
                        }
                except json.JSONDecodeError as e:
                    return {
                        'status': 'WARNING',
                        'error': f'Ошибка парсинга JSON: {e}',
                        'details': result.stdout[:200]
                    }
            else:
                return {
                    'status': 'ERROR',
                    'error': f'ffprobe вернул код {result.returncode}',
                    'details': result.stderr[:200]
                }
        
        except subprocess.TimeoutExpired:
            return {
                'status': 'ERROR',
                'error': 'Таймаут при выполнении тестовой команды'
            }
        except Exception as e:
            return {
                'status': 'ERROR',
                'error': f'Исключение при тестировании: {e}'
            }
    
    def _get_system_info(self) -> Dict:
        """Информация о системе"""
        import platform
        
        return {
            'platform': platform.platform(),
            'machine': platform.machine(),
            'processor': platform.processor(),
            'python_implementation': platform.python_implementation(),
        }
    
    def log_to_file(self, output_dir: Path = None) -> Path:
        """
        Запись диагностической информации в файл
        
        Args:
            output_dir: Директория для файла (по умолчанию Desktop)
            
        Returns:
            Путь к созданному файлу
        """
        if output_dir is None:
            output_dir = Path.home() / 'Desktop'
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = output_dir / 'diagnostics.log'
        
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write("═" * 70 + "\n")
            f.write("  BEAST AUTO REPORTER - ДИАГНОСТИКА СИСТЕМЫ\n")
            f.write("═" * 70 + "\n\n")
            
            f.write(f"Дата: {self.info['timestamp']}\n\n")
            
            # Python
            f.write("─" * 70 + "\n")
            f.write("PYTHON\n")
            f.write("─" * 70 + "\n")
            python_info = self.info['python']
            f.write(f"Версия: {python_info['version_short']}\n")
            f.write(f"Полная версия: {python_info['version']}\n")
            f.write(f"Исполняемый файл: {python_info['executable']}\n")
            f.write(f"Платформа: {python_info['platform']}\n\n")
            
            # Пакеты
            f.write("─" * 70 + "\n")
            f.write("ПАКЕТЫ\n")
            f.write("─" * 70 + "\n")
            for package, version in self.info['packages'].items():
                f.write(f"{package:20s} : {version}\n")
            f.write("\n")
            
            # ffprobe
            f.write("─" * 70 + "\n")
            f.write("FFPROBE\n")
            f.write("─" * 70 + "\n")
            ffprobe_info = self.info['ffprobe']
            f.write(f"Найден: {'✅ Да' if ffprobe_info['found'] else '❌ Нет'}\n")
            if ffprobe_info['found']:
                f.write(f"Путь: {ffprobe_info['path']}\n")
                f.write(f"Версия: {ffprobe_info['version']}\n")
                f.write(f"Тест: {ffprobe_info['test_result']}\n")
                if ffprobe_info['test_error']:
                    f.write(f"Ошибка теста: {ffprobe_info['test_error']}\n")
            f.write("\n")
            
            # Система
            f.write("─" * 70 + "\n")
            f.write("СИСТЕМА\n")
            f.write("─" * 70 + "\n")
            system_info = self.info['system']
            for key, value in system_info.items():
                f.write(f"{key:25s} : {value}\n")
            f.write("\n")
            
            # Ошибки
            if self.errors:
                f.write("─" * 70 + "\n")
                f.write("ОШИБКИ\n")
                f.write("─" * 70 + "\n")
                for error in self.errors:
                    f.write(f"❌ {error}\n")
                f.write("\n")
            
            # Предупреждения
            if self.warnings:
                f.write("─" * 70 + "\n")
                f.write("ПРЕДУПРЕЖДЕНИЯ\n")
                f.write("─" * 70 + "\n")
                for warning in self.warnings:
                    f.write(f"⚠️  {warning}\n")
                f.write("\n")
            
            f.write("═" * 70 + "\n")
            f.write("КОНЕЦ ДИАГНОСТИКИ\n")
            f.write("═" * 70 + "\n")
        
        return log_file
    
    def print_summary(self):
        """Вывод краткой сводки в консоль"""
        print("\n" + "═" * 70)
        print("  ДИАГНОСТИКА СИСТЕМЫ")
        print("═" * 70)
        
        # Python
        python_ver = self.info['python']['version_short']
        print(f"Python: {python_ver}")
        
        # Ключевые пакеты
        packages = self.info['packages']
        print(f"PyMuPDF: {packages.get('PyMuPDF', 'не найден')}")
        print(f"PyQt5: {packages.get('PyQt5', 'не найден')}")
        print(f"python-docx: {packages.get('python-docx', 'не найден')}")
        
        # ffprobe
        ffprobe_info = self.info['ffprobe']
        if ffprobe_info['found']:
            print(f"ffprobe: ✅ ({ffprobe_info['test_result']})")
        else:
            print(f"ffprobe: ❌ НЕ НАЙДЕН")
        
        # Ошибки/предупреждения
        if self.errors:
            print(f"\n❌ Ошибок: {len(self.errors)}")
        if self.warnings:
            print(f"⚠️  Предупреждений: {len(self.warnings)}")
        
        print("═" * 70 + "\n")


def run_diagnostics(output_dir: Path = None, verbose: bool = False) -> SystemDiagnostics:
    """
    Запуск диагностики
    
    Args:
        output_dir: Директория для логов
        verbose: Подробный вывод
        
    Returns:
        Объект SystemDiagnostics с результатами
    """
    diag = SystemDiagnostics()
    
    if verbose:
        logger.info("Запуск диагностики системы...")
    
    # Сбор информации
    diag.collect_all()
    
    # Вывод в консоль
    if verbose:
        diag.print_summary()
    
    # Запись в файл
    try:
        log_file = diag.log_to_file(output_dir)
        if verbose:
            logger.info(f"Диагностика сохранена: {log_file}")
    except Exception as e:
        logger.error(f"Не удалось сохранить диагностику: {e}")
    
    return diag


# Пример использования
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    diag = run_diagnostics(verbose=True)
    
    # Проверка на критичные ошибки
    if diag.errors:
        print("\n⚠️  Обнаружены критичные ошибки!")
        for error in diag.errors:
            print(f"  - {error}")
        sys.exit(1)
    else:
        print("\n✅ Все проверки пройдены успешно!")
