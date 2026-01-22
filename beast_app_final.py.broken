#!/usr/bin/env python3
"""
Beast Auto Reporter - Final Desktop Application

Desktop приложение для автоматического создания отчетов
НА ОСНОВЕ CSV ФАЙЛА с проблемами
"""

import sys
from pathlib import Path
import shutil
import re
from datetime import datetime
import logging

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QLabel, QFileDialog, QProgressBar, QMessageBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPalette, QColor

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent))

from src.csv_importer import CSVImporter
from src.technical_info_extractor import TechnicalInfoExtractor
from src.conclusion_generator import ConclusionGenerator
from src.exact_report_generator import ExactReportGenerator
from src.pdf_extractor import PDFExtractor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ProcessingThread(QThread):
    """Поток для обработки файлов"""
    
    status_update = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    finished = pyqtSignal(str, bool)
    
    def __init__(self, app_instance, input_folder):
        super().__init__()
        self.app = app_instance
        self.input_folder = input_folder
    
    def run(self):
        """Выполнение обработки"""
        try:
            # === ШАГ 1: Поиск файлов ===
            self.status_update.emit("🔍 Поиск файлов...")
            self.progress_update.emit(10)
            
            files = self.app.find_files_in_folder(self.input_folder)
            
            # Проверка CSV (обязателен)
            if not files['csv']:
                self.finished.emit(
                    "❌ CSV файл не найден!\n\n"
                    "В папке должен быть файл .csv с маркерами проблем.",
                    False
                )
                return
            
            # Проверяем наличие файлов и собираем предупреждения
            missing_files = []
            found_count = 1  # CSV уже найден
            
            file_checks = [
                ('audio_20_c', 'Аудио 2.0 cens'),
                ('audio_20_uc', 'Аудио 2.0 uncens'),
                ('audio_51_c', 'Аудио 5.1 cens'),
                ('audio_51_uc', 'Аудио 5.1 uncens'),
                ('pdf_20', 'PDF 2.0'),
                ('pdf_51', 'PDF 5.1'),
                ('video', 'Видео файл'),
                ('params', 'Параметры.txt'),
            ]
            
            for key, name in file_checks:
                if files[key]:
                    found_count += 1
                else:
                    missing_files.append(name)
            
            # Логируем информацию
            logger.info(f"✅ Найдено файлов: {found_count}")
            if missing_files:
                logger.warning(f"⚠️  Отсутствующие файлы: {', '.join(missing_files)}")
                logger.warning("Отчет будет создан на основе доступных файлов")
            
            # === ШАГ 2: Извлечение базового имени из аудио ===
            self.status_update.emit("📝 Определение названия...")
            self.progress_update.emit(15)
            
            # Берем название из первого найденного аудио файла
            source_file = None
            for key in ['audio_20_c', 'audio_51_c', 'audio_20_uc', 'audio_51_uc']:
                if files.get(key):
                    source_file = files[key]
                    break
            
            # Если аудио нет, берем из CSV
            if not source_file:
                source_file = files['csv']
                logger.warning("⚠️  Аудио файлы не найдены, используется имя CSV")
            
            base_name = self.app.extract_base_name(source_file.name)
            logger.info(f"Базовое имя: {base_name}")
            
            # === ШАГ 3: Создание выходной папки ===
            self.status_update.emit("📂 Создание выходной папки...")
            self.progress_update.emit(20)
            
            output_folder = self.app.create_output_folder(base_name)
            logger.info(f"Выходная папка: {output_folder}")
            
            # === ШАГ 4: Копирование только необходимых файлов ===
            self.status_update.emit("📋 Копирование файлов отчета...")
            self.progress_update.emit(25)
            
            # Копируем ТОЛЬКО CSV и PDF (они нужны для отчета и малы по размеру)
            # Аудио/видео НЕ копируем (слишком большие, данные уже извлечены)
            files_to_copy = []
            
            if files['csv']:
                files_to_copy.append(files['csv'])
            if files['pdf_20']:
                files_to_copy.append(files['pdf_20'])
            if files['pdf_51']:
                files_to_copy.append(files['pdf_51'])
            if files['params']:
                files_to_copy.append(files['params'])
            
            copied_count = 0
            for file_path in files_to_copy:
                try:
                    dest = output_folder / file_path.name
                    shutil.copy2(file_path, dest)
                    logger.info(f"✅ Скопирован: {file_path.name}")
                    copied_count += 1
                except OSError as e:
                    if e.errno == 28:  # No space left on device
                        logger.warning(f"⚠️ Недостаточно места для копирования: {file_path.name}")
                    else:
                        logger.error(f"Ошибка копирования {file_path.name}: {e}")
            
            logger.info(f"Скопировано файлов: {copied_count}/{len(files_to_copy)}")
            
            # === ШАГ 5: Импорт CSV ===
            self.status_update.emit("📊 Импорт проблем из CSV...")
            self.progress_update.emit(35)
            
            issues = self.app.csv_importer.import_issues(str(files['csv']))
            categories = self.app.csv_importer.categorize_issues(issues)
            
            logger.info(f"Импортировано {len(issues)} проблем")
            
            # === ШАГ 6: Извлечение технических параметров ===
            self.status_update.emit("⚙️ Извлечение технических параметров...")
            self.progress_update.emit(50)
            
            tech_info = {}
            
            # Извлекаем из аудио файлов
            if files['audio_20_c']:
                tech_info['audio_20_c'] = self.app.tech_extractor.extract_audio_info(str(files['audio_20_c']))
            
            if files['audio_20_uc']:
                tech_info['audio_20_uc'] = self.app.tech_extractor.extract_audio_info(str(files['audio_20_uc']))
            
            if files['audio_51_c']:
                tech_info['audio_51_c'] = self.app.tech_extractor.extract_audio_info(str(files['audio_51_c']))
            
            if files['audio_51_uc']:
                tech_info['audio_51_uc'] = self.app.tech_extractor.extract_audio_info(str(files['audio_51_uc']))
            
            if files['video']:
                tech_info['video'] = self.app.tech_extractor.extract_video_info(str(files['video']))
            
            # Извлекаем параметры
            if files['params']:
                tech_info['params'] = self.app.tech_extractor.read_params_file(str(files['params']))
            
            # Извлекаем данные из PDF
            self.status_update.emit("📊 Извлечение данных из PDF...")
            self.progress_update.emit(55)
            
            if files['pdf_20']:
                tech_info['pdf_20'] = self.app.pdf_extractor.extract_technical_info(str(files['pdf_20']))
                logger.info(f"PDF 2.0: LUFS={tech_info['pdf_20'].get('lufs')}, Peak={tech_info['pdf_20'].get('true_peak')}, LRA={tech_info['pdf_20'].get('lra')}")
            
            if files['pdf_51']:
                tech_info['pdf_51'] = self.app.pdf_extractor.extract_technical_info(str(files['pdf_51']))
                logger.info(f"PDF 5.1: LUFS={tech_info['pdf_51'].get('lufs')}, Peak={tech_info['pdf_51'].get('true_peak')}, LRA={tech_info['pdf_51'].get('lra')}")
            
            # Дублирование PDF данных если один из файлов отсутствует
            if files['pdf_20'] and not files['pdf_51']:
                tech_info['pdf_51'] = tech_info['pdf_20'].copy()
                logger.info(f"ℹ️  PDF 5.1 отсутствует, используются данные PDF 2.0")
            elif files['pdf_51'] and not files['pdf_20']:
                tech_info['pdf_20'] = tech_info['pdf_51'].copy()
                logger.info(f"ℹ️  PDF 2.0 отсутствует, используются данные PDF 5.1")
            
            # === ШАГ 7: Генерация заключения ===
            self.status_update.emit("📝 Генерация заключения...")
            self.progress_update.emit(70)
            
            # Техническая оценка (на основе технических параметров и PDF)
            params = tech_info.get('params', {}) if tech_info else {}
            technical_conclusion = self.app.conclusion_gen.generate_technical_conclusion(tech_info, params)
            
            # Субъективная оценка (на основе проблем из CSV)
            subjective_conclusion = self.app.conclusion_gen.generate_subjective_conclusion(issues)
            
            logger.info("Заключения сгенерированы")
            
            # === ШАГ 8: Генерация отчета ===
            self.status_update.emit("📄 Генерация итогового отчета...")
            self.progress_update.emit(90)
            
            timestamp = datetime.now().strftime('%Y_%m_%d')
            report_name = f"отчет_{base_name}_{timestamp}_rus.docx"
            report_path = output_folder / report_name
            
            self.app.report_gen.create_exact_report(
                issues=issues,
                output_path=str(report_path),
                tech_info=tech_info,
                pdf_20_path=str(files['pdf_20']) if files.get('pdf_20') else None,
                pdf_51_path=str(files['pdf_51']) if files.get('pdf_51') else None,
                conclusion_technical=technical_conclusion,
                conclusion_subjective=subjective_conclusion
            )
            
            # === ЗАВЕРШЕНО ===
            self.progress_update.emit(100)
            self.status_update.emit("✅ Готово!")
            
            # Формируем сообщение
            message_parts = [
                "✅ Отчет успешно создан!\n",
                f"📁 Папка: {output_folder.name}",
                f"📍 Расположение: Desktop",
                f"📝 Проблем: {len(issues)}",
                f"   • Блокеров: {len(categories['blockers'])}",
                f"   • Требуют исправления: {len(categories['fix_required'])}",
                f"   • Требуют комментария: {len(categories['comment_required'])}\n",
            ]
            
            # Добавляем информацию о файлах
            message_parts.append(f"📂 Использовано файлов: {found_count}")
            
            # Если есть отсутствующие файлы - предупреждаем
            if missing_files:
                message_parts.append(f"\n⚠️  Отсутствующие файлы ({len(missing_files)}):")
                for name in missing_files[:5]:  # Показываем до 5
                    message_parts.append(f"   • {name}")
                if len(missing_files) > 5:
                    message_parts.append(f"   ... и еще {len(missing_files) - 5}")
                message_parts.append("\nОтчет создан на основе доступных файлов.\n")
            
            message_parts.append("Папка откроется автоматически...")
            message = "\n".join(message_parts)
            
            # Открываем папку
            import subprocess
            subprocess.run(['open', str(output_folder)])
            
            self.finished.emit(message, True)
            
        except Exception as e:
            logger.exception("Ошибка обработки")
            self.finished.emit(f"Произошла ошибка:\n\n{str(e)}", False)
    


class BeastAutoReporterFinalApp(QMainWindow):
    """Главное окно приложения"""
    
    def __init__(self):
        super().__init__()
        
        self.input_folder = None
        self.processing_thread = None
        
        # Инициализация компонентов
        self.csv_importer = CSVImporter()
        self.tech_extractor = TechnicalInfoExtractor()
        self.conclusion_gen = ConclusionGenerator(use_llm=False)  # Без LLM по умолчанию
        self.report_gen = ExactReportGenerator()
        self.pdf_extractor = PDFExtractor()
        
        self.init_ui()
    
    def init_ui(self):
        """Инициализация интерфейса"""
        self.setWindowTitle("Beast Auto Reporter - Final")
        self.setGeometry(100, 100, 800, 600)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # ЗАГОЛОВОК
        title_label = QLabel("🎵 Beast Auto Reporter")
        title_font = QFont("Arial", 28, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #1f77b4; padding: 20px;")
        layout.addWidget(title_label)
        
        # SUBTITLE
        subtitle_label = QLabel("Генерация отчетов на основе CSV файла")
        subtitle_label.setFont(QFont("Arial", 12))
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet("color: #666; padding: 5px;")
        layout.addWidget(subtitle_label)
        
        # ИНСТРУКЦИИ
        instructions = QLabel("📁 Выберите папку с файлами:\n• CSV файл с проблемами\n• Аудио/видео файлы\n• Параметры.txt")
        instructions.setFont(QFont("Arial", 11))
        instructions.setAlignment(Qt.AlignCenter)
        instructions.setStyleSheet("color: #333; padding: 10px;")
        layout.addWidget(instructions)
        
        # ОБЛАСТЬ ВЫБОРА ПАПКИ
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
        
        # КНОПКА ВЫБОРА ПАПКИ
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
        """)
        self.select_button.clicked.connect(self.select_folder)
        layout.addWidget(self.select_button)
        
        # КНОПКА СОЗДАНИЯ ОТЧЕТА
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
            QPushButton:disabled {
                background-color: #ccc;
                color: #666;
            }
        """)
        self.process_button.setEnabled(False)
        self.process_button.clicked.connect(self.process_folder)
        layout.addWidget(self.process_button)
        
        # ПРОГРЕСС БАР
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
        
        # СТАТУС
        self.status_label = QLabel("Ожидание...")
        self.status_label.setFont(QFont("Arial", 12))
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #666; padding: 10px;")
        layout.addWidget(self.status_label)
        
        # FOOTER
        footer_label = QLabel("Beast Auto Reporter Final | Импорт CSV + Генерация заключения")
        footer_label.setFont(QFont("Arial", 9))
        footer_label.setAlignment(Qt.AlignCenter)
        footer_label.setStyleSheet("color: #999; padding: 10px;")
        layout.addWidget(footer_label)
        
        central_widget.setLayout(layout)
        
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
        
        self.select_button.setEnabled(False)
        self.process_button.setEnabled(False)
        self.progress_bar.setValue(0)
        
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
        self.select_button.setEnabled(True)
        self.process_button.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "Успех!", message)
        else:
            QMessageBox.critical(self, "Ошибка", message)
        
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
            'csv': None,
            'pdf_20': None,
            'pdf_51': None,
            'params': None,
            'all_files': []
        }
        
        for file_path in folder.iterdir():
            if file_path.is_file():
                files['all_files'].append(file_path)
                
                name_lower = file_path.name.lower()
                
                # CSV файл
                if file_path.suffix.lower() == '.csv':
                    files['csv'] = file_path
                
                # Параметры.txt
                elif 'параметры' in name_lower or 'parametry' in name_lower:
                    files['params'] = file_path
                
                # Аудио файлы (ВАЖНО: проверяем 5.1 ПЕРЕД 2.0!)
                elif file_path.suffix.lower() in ['.wav', '.mp3', '.aiff', '.flac']:
                    # Сначала проверяем 5.1 (улучшенные паттерны, как для PDF)
                    if ('5.1' in file_path.name or '5_1' in file_path.name or 
                        '_51_' in name_lower or '_51.' in name_lower or 
                        name_lower.endswith('51') or '51.' in name_lower):
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['audio_51_uc'] = file_path
                            logger.info(f"✅ Аудио 5.1 uncens найдено: {file_path.name}")
                        else:
                            files['audio_51_c'] = file_path
                            logger.info(f"✅ Аудио 5.1 cens найдено: {file_path.name}")
                    # Потом проверяем 2.0 (улучшенные паттерны)
                    elif ('2.0' in file_path.name or '2_0' in file_path.name or 
                          '_20_' in name_lower or '_20.' in name_lower or 
                          name_lower.endswith('20') or '20.' in name_lower or 
                          'stereo' in name_lower):
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['audio_20_uc'] = file_path
                            logger.info(f"✅ Аудио 2.0 uncens найдено: {file_path.name}")
                        else:
                            files['audio_20_c'] = file_path
                            logger.info(f"✅ Аудио 2.0 cens найдено: {file_path.name}")
                
                # Видео
                elif file_path.suffix.lower() in ['.mp4', '.mov', '.mkv', '.avi']:
                    files['video'] = file_path
                
                # PDF файлы (улучшенная логика поиска)
                elif file_path.suffix.lower() == '.pdf':
                    # Проверяем на 5.1 (улучшенные паттерны)
                    if ('5.1' in file_path.name or '5_1' in file_path.name or 
                        '_51_' in name_lower or '_51.' in name_lower or 
                        name_lower.endswith('51') or '51.' in name_lower):
                        if not files['pdf_51']:  # Берем первый найденный
                            files['pdf_51'] = file_path
                            logger.info(f"✅ PDF 5.1 найден: {file_path.name}")
                    # Проверяем на 2.0 (улучшенные паттерны)
                    elif ('2.0' in file_path.name or '2_0' in file_path.name or 
                          '_20_' in name_lower or '_20.' in name_lower or 
                          name_lower.endswith('20') or '20.' in name_lower or 
                          'stereo' in name_lower):
                        if not files['pdf_20']:  # Берем первый найденный
                            files['pdf_20'] = file_path
                            logger.info(f"✅ PDF 2.0 найден: {file_path.name}")
                    # Если не подошло - сохраняем как неопределенный
                    else:
                        # Если еще не нашли 2.0, считаем этот PDF файлом 2.0
                        if not files['pdf_20']:
                            files['pdf_20'] = file_path
                            logger.info(f"⚠️ PDF (по умолчанию 2.0): {file_path.name}")
                        # Если уже есть 2.0, но нет 5.1, считаем что это 5.1
                        elif not files['pdf_51']:
                            files['pdf_51'] = file_path
                            logger.info(f"⚠️ PDF (по умолчанию 5.1): {file_path.name}")
        
        return files
    
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


def main():
    """Главная функция"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = BeastAutoReporterFinalApp()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

