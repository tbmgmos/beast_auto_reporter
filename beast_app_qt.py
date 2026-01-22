#!/usr/bin/env python3
"""
Beast Auto Reporter - Desktop Application (PyQt5)

Desktop приложение для автоматического создания отчетов
"""

import sys
from pathlib import Path
import shutil
import re
from datetime import datetime
import logging

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QProgressBar, QMessageBox, QTextEdit
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPalette, QColor

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from audio_analyzer import AudioAnalyzer
from defect_detector import DefectDetector
from template_report_generator import TemplateReportGenerator
from pdf_extractor import PDFExtractor
from timecode_analyzer import TimecodeAnalyzer
import yaml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ProcessingThread(QThread):
    """Поток для обработки файлов"""
    
    status_update = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    finished = pyqtSignal(str, bool)  # (message, success)
    
    def __init__(self, app_instance, input_folder):
        super().__init__()
        self.app = app_instance
        self.input_folder = input_folder
    
    def run(self):
        """Выполнение обработки"""
        try:
            # === ШАГ 1: Поиск файлов ===
            self.status_update.emit("📁 Поиск файлов...")
            self.progress_update.emit(10)
            
            files = self.app.find_files_in_folder(self.input_folder)
            
            if not files['audio_20_c'] and not files['audio_51_c']:
                self.finished.emit(
                    "Ошибка: Не найдены аудио файлы!\n\nДолжен быть хотя бы один файл: 2.0 или 5.1",
                    False
                )
                return
            
            # === ШАГ 2: Извлечение базового имени ===
            self.status_update.emit("📝 Определение названия...")
            self.progress_update.emit(20)
            
            audio_file = files['audio_20_c'] or files['audio_51_c']
            base_name = self.app.extract_base_name(audio_file.name)
            
            logger.info(f"Базовое имя: {base_name}")
            
            # === ШАГ 3: Создание выходной папки ===
            self.status_update.emit("📂 Создание выходной папки...")
            self.progress_update.emit(30)
            
            output_folder = self.app.create_output_folder(base_name)
            logger.info(f"Выходная папка: {output_folder}")
            
            # === ШАГ 4: Копирование исходных файлов ===
            self.status_update.emit("📋 Копирование файлов...")
            self.progress_update.emit(40)
            
            for file_path in files['all_files']:
                dest = output_folder / file_path.name
                shutil.copy2(file_path, dest)
                logger.info(f"Скопирован: {file_path.name}")
            
            # === ШАГ 5: Чтение параметров ===
            if files['params']:
                self.status_update.emit("⚙️ Чтение параметров...")
                self.progress_update.emit(50)
                
                params_dict, params_text = self.app.read_params_file(files['params'])
                if params_dict:
                    self.app.config['audio'].update(params_dict)
                    self.app.analyzer = AudioAnalyzer(self.app.config)
                    self.app.detector = DefectDetector(self.app.config)
            
            # === ШАГ 6: Анализ хронометража ===
            self.status_update.emit("⏱️ Анализ хронометража...")
            self.progress_update.emit(60)
            
            timecode_info = self.app.tc_analyzer.analyze_all_files(
                audio_20=str(files['audio_20_c']) if files['audio_20_c'] else None,
                audio_51=str(files['audio_51_c']) if files['audio_51_c'] else None,
                video=str(files['video']) if files['video'] else None
            )
            
            # === ШАГ 7: Детекция дефектов ===
            self.status_update.emit("🔍 Детекция дефектов...")
            self.progress_update.emit(70)
            
            all_defects = []
            
            try:
                if files['audio_20_c']:
                    self.status_update.emit("🔍 Детекция в 2.0...")
                    try:
                        defects_20 = self.app.detector.analyze_file(str(files['audio_20_c']), "2.0")
                        all_defects.extend(defects_20)
                        logger.info(f"2.0: найдено {len(defects_20)} дефектов")
                    except Exception as e:
                        logger.error(f"Ошибка детекции в 2.0: {e}")
                        # Продолжаем без дефектов 2.0
                
                if files['audio_51_c']:
                    self.status_update.emit("🔍 Детекция в 5.1...")
                    try:
                        defects_51 = self.app.detector.analyze_file(str(files['audio_51_c']), "5.1")
                        all_defects.extend(defects_51)
                        logger.info(f"5.1: найдено {len(defects_51)} дефектов")
                    except Exception as e:
                        logger.error(f"Ошибка детекции в 5.1: {e}")
                        # Продолжаем без дефектов 5.1
                
                all_defects.sort(key=lambda d: getattr(d, 'timecode_in', '00:00:00:00'))
                
                logger.info(f"Всего найдено дефектов: {len(all_defects)}")
                
            except Exception as e:
                logger.error(f"Критическая ошибка детекции: {e}")
                # Продолжаем с пустым списком дефектов
                all_defects = []
            
            # === ШАГ 8: Генерация отчетов ===
            self.status_update.emit("📄 Генерация отчетов...")
            self.progress_update.emit(90)
            
            timestamp = datetime.now().strftime('%Y_%m_%d')
            report_name = f"отчет_{base_name}_{timestamp}_rus"
            
            # DOCX отчет
            docx_path = output_folder / f"{report_name}.docx"
            self.app.generator.create_report_from_template(
                defects=all_defects,
                output_path=str(docx_path),
                timecode_info=timecode_info
            )
            
            # CSV отчет (опционально)
            csv_path = output_folder / f"{base_name}_{timestamp}_rus.csv"
            self.app._create_csv_report(all_defects, csv_path)
            
            # === ЗАВЕРШЕНО ===
            self.progress_update.emit(100)
            self.status_update.emit("✅ Готово!")
            
            message = (
                f"✅ Отчет создан!\n\n"
                f"📁 Папка: {output_folder.name}\n"
                f"📍 Расположение: Desktop\n"
                f"📝 Дефектов: {len(all_defects)}\n\n"
                f"Папка откроется автоматически..."
            )
            
            # Открываем папку
            import subprocess
            subprocess.run(['open', str(output_folder)])
            
            self.finished.emit(message, True)
            
        except Exception as e:
            logger.exception("Ошибка обработки")
            self.finished.emit(f"Произошла ошибка:\n\n{str(e)}", False)


class BeastAutoReporterApp(QMainWindow):
    """Главное окно приложения"""
    
    def __init__(self):
        super().__init__()
        
        self.input_folder = None
        self.processing_thread = None
        
        # Загрузка конфигурации
        self.config = self.load_config()
        
        # Инициализация компонентов
        self.analyzer = AudioAnalyzer(self.config)
        self.detector = DefectDetector(self.config)
        self.generator = TemplateReportGenerator()
        self.pdf_extractor = PDFExtractor()
        self.tc_analyzer = TimecodeAnalyzer()
        
        self.init_ui()
    
    def load_config(self):
        """Загрузка конфигурации"""
        config_path = Path(__file__).parent / 'config' / 'settings.yaml'
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        return {'audio': {}}
    
    def init_ui(self):
        """Инициализация интерфейса"""
        self.setWindowTitle("Beast Auto Reporter")
        self.setGeometry(100, 100, 800, 600)
        
        # Центральный виджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Основной layout
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # === ЗАГОЛОВОК ===
        title_label = QLabel("🎵 Beast Auto Reporter")
        title_font = QFont("Arial", 28, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #1f77b4; padding: 20px;")
        layout.addWidget(title_label)
        
        # === ИНСТРУКЦИИ ===
        instructions = QLabel("📁 Выберите папку с файлами для создания отчета")
        instructions.setFont(QFont("Arial", 14))
        instructions.setAlignment(Qt.AlignCenter)
        instructions.setStyleSheet("color: #333; padding: 10px;")
        layout.addWidget(instructions)
        
        # === ОБЛАСТЬ ВЫБОРА ПАПКИ ===
        self.folder_label = QLabel("📂 Папка не выбрана")
        self.folder_label.setFont(QFont("Arial", 12))
        self.folder_label.setAlignment(Qt.AlignCenter)
        self.folder_label.setMinimumHeight(100)
        self.folder_label.setStyleSheet("""
            QLabel {
                background-color: #f0f2f6;
                border: 2px dashed #ccc;
                border-radius: 10px;
                padding: 20px;
                color: #666;
            }
        """)
        layout.addWidget(self.folder_label)
        
        # === КНОПКА ВЫБОРА ПАПКИ ===
        self.select_button = QPushButton("📁 Выбрать папку")
        self.select_button.setFont(QFont("Arial", 14, QFont.Bold))
        self.select_button.setMinimumHeight(50)
        self.select_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 10px;
                padding: 10px 30px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)
        self.select_button.clicked.connect(self.select_folder)
        layout.addWidget(self.select_button)
        
        # === КНОПКА СОЗДАНИЯ ОТЧЕТА ===
        self.process_button = QPushButton("🎯 СОЗДАТЬ ОТЧЕТ")
        self.process_button.setFont(QFont("Arial", 16, QFont.Bold))
        self.process_button.setMinimumHeight(60)
        self.process_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border-radius: 10px;
                padding: 15px 40px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
            QPushButton:disabled {
                background-color: #ccc;
                color: #666;
            }
        """)
        self.process_button.setEnabled(False)
        self.process_button.clicked.connect(self.process_folder)
        layout.addWidget(self.process_button)
        
        # === ПРОГРЕСС БАР ===
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(30)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid #ccc;
                border-radius: 10px;
                text-align: center;
                font-size: 14px;
            }
            QProgressBar::chunk {
                background-color: #2196F3;
                border-radius: 8px;
            }
        """)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        # === СТАТУС ===
        self.status_label = QLabel("Ожидание...")
        self.status_label.setFont(QFont("Arial", 12))
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #666; padding: 10px;")
        layout.addWidget(self.status_label)
        
        # === FOOTER ===
        footer_label = QLabel("Beast Auto Reporter V2 | Все обработки локальные")
        footer_label.setFont(QFont("Arial", 9))
        footer_label.setAlignment(Qt.AlignCenter)
        footer_label.setStyleSheet("color: #999; padding: 10px;")
        layout.addWidget(footer_label)
        
        central_widget.setLayout(layout)
        
        # Устанавливаем цвет фона
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor(255, 255, 255))
        self.setPalette(palette)
    
    def select_folder(self):
        """Выбор папки"""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку с файлами",
            str(Path.home() / "Desktop")
        )
        
        if folder:
            self.input_folder = Path(folder)
            self.folder_label.setText(f"✓ Выбрана папка:\n{self.input_folder.name}")
            self.folder_label.setStyleSheet("""
                QLabel {
                    background-color: #e8f5e9;
                    border: 2px solid #4CAF50;
                    border-radius: 10px;
                    padding: 20px;
                    color: #2e7d32;
                }
            """)
            self.process_button.setEnabled(True)
            self.status_label.setText("Готово к обработке")
    
    def process_folder(self):
        """Запуск обработки"""
        if not self.input_folder:
            QMessageBox.warning(self, "Ошибка", "Выберите папку!")
            return
        
        # Блокируем кнопки
        self.select_button.setEnabled(False)
        self.process_button.setEnabled(False)
        self.progress_bar.setValue(0)
        
        # Запускаем поток обработки
        self.processing_thread = ProcessingThread(self, self.input_folder)
        self.processing_thread.status_update.connect(self.update_status)
        self.processing_thread.progress_update.connect(self.update_progress)
        self.processing_thread.finished.connect(self.on_processing_finished)
        self.processing_thread.start()
    
    def update_status(self, text):
        """Обновление статуса"""
        self.status_label.setText(text)
    
    def update_progress(self, value):
        """Обновление прогресса"""
        self.progress_bar.setValue(value)
    
    def on_processing_finished(self, message, success):
        """Завершение обработки"""
        # Разблокируем кнопки
        self.select_button.setEnabled(True)
        self.process_button.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "Успех!", message)
        else:
            QMessageBox.critical(self, "Ошибка", message)
        
        # Сбрасываем
        self.progress_bar.setValue(0)
        self.status_label.setText("Ожидание...")
    
    def extract_base_name(self, filename: str) -> str:
        """Извлечение базового названия из имени файла"""
        name = Path(filename).stem
        
        remove_words = [
            r'_?5\.?1_?',
            r'_?2\.?0_?',
            r'_?stereo_?',
            r'_?cense?_?',
            r'_?uncense?_?',
            r'_?censored_?',
            r'_?uncensored_?',
            r'_?c_?',
            r'_?uc_?',
            r'_?audio_?',
            r'_?video_?',
        ]
        
        for word in remove_words:
            name = re.sub(word, '_', name, flags=re.IGNORECASE)
        
        name = re.sub(r'_+', '_', name)
        name = name.strip('_')
        
        return name
    
    def find_files_in_folder(self, folder: Path) -> dict:
        """Поиск файлов в папке"""
        files = {
            'audio_20_c': None,
            'audio_20_uc': None,
            'audio_51_c': None,
            'audio_51_uc': None,
            'video': None,
            'pdf_20': None,
            'pdf_51': None,
            'params': None,
            'all_files': []
        }
        
        for file_path in folder.iterdir():
            if file_path.is_file():
                files['all_files'].append(file_path)
                
                name_lower = file_path.name.lower()
                
                if 'параметры' in name_lower or 'parametry' in name_lower:
                    files['params'] = file_path
                
                elif file_path.suffix.lower() in ['.wav', '.mp3', '.aiff', '.flac']:
                    if '2.0' in name_lower or '20' in name_lower or 'stereo' in name_lower:
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['audio_20_uc'] = file_path
                        else:
                            files['audio_20_c'] = file_path
                    elif '5.1' in name_lower or '51' in name_lower:
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['audio_51_uc'] = file_path
                        else:
                            files['audio_51_c'] = file_path
                
                elif file_path.suffix.lower() in ['.mp4', '.mov', '.mkv', '.avi']:
                    files['video'] = file_path
                
                elif file_path.suffix.lower() == '.pdf':
                    if '2.0' in name_lower or '20' in name_lower:
                        files['pdf_20'] = file_path
                    elif '5.1' in name_lower or '51' in name_lower:
                        files['pdf_51'] = file_path
        
        return files
    
    def read_params_file(self, params_path: Path) -> tuple:
        """Чтение Параметры.txt"""
        try:
            with open(params_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            params = {
                'target_lufs': -23.0,
                'lufs_tolerance': 0.5,
                'true_peak': -2.0,
                'lra_max': 18.0
            }
            
            import re
            lufs_match = re.search(r'(-?\d+\.?\d*)\s*LUFS', content)
            if lufs_match:
                params['target_lufs'] = float(lufs_match.group(1))
            
            return params, content
        except Exception as e:
            logger.error(f"Ошибка чтения параметров: {e}")
            return None, None
    
    def create_output_folder(self, base_name: str) -> Path:
        """Создание выходной папки"""
        timestamp = datetime.now().strftime('%Y_%m_%d')
        folder_name = f"отчет_{base_name}_{timestamp}_rus"
        
        desktop = Path.home() / "Desktop"
        output_folder = desktop / folder_name
        
        counter = 1
        while output_folder.exists():
            output_folder = desktop / f"{folder_name}_{counter}"
            counter += 1
        
        output_folder.mkdir(parents=True, exist_ok=True)
        
        return output_folder
    
    def _create_csv_report(self, defects, output_path):
        """Создание CSV отчета"""
        import csv
        
        try:
            with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f, delimiter='\t')
                
                writer.writerow([
                    'Track name',
                    'Timecode In',
                    'Timecode Out',
                    'Description',
                    'Length',
                    '2.0 C',
                    '5.1 C',
                    'БЛОКЕР',
                    'ТРЕБУЕТ ИСПРАВЛЕНИЯ',
                    'ТРЕБУЕТ КОММЕНТАРИЯ'
                ])
                
                for defect in defects:
                    channels = getattr(defect, 'channels', [])
                    has_20 = any(c in ['*', '2.0'] for c in channels)
                    has_51 = any(c in ['*', '5.1'] for c in channels)
                    
                    severity = getattr(defect, 'severity', 'comment_required')
                    
                    writer.writerow([
                        'MARKERS DATA 1',
                        getattr(defect, 'timecode_in', ''),
                        getattr(defect, 'timecode_out', ''),
                        getattr(defect, 'description', ''),
                        int(getattr(defect, 'duration', 0)),
                        '*' if has_20 else '',
                        '*' if has_51 else '',
                        '*' if severity == 'blocker' else '',
                        '*' if severity == 'fix_required' else '',
                        '*' if severity == 'comment_required' else ''
                    ])
            
            logger.info(f"CSV создан: {output_path}")
        except Exception as e:
            logger.error(f"Ошибка создания CSV: {e}")


def main():
    """Главная функция"""
    app = QApplication(sys.argv)
    
    # Настройка стиля приложения
    app.setStyle("Fusion")
    
    window = BeastAutoReporterApp()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

