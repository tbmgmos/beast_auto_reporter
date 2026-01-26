"""
Beast Auto Reporter - Drag & Drop Version
Улучшенный интерфейс с поддержкой перетаскивания файлов
"""

import sys
import os
import logging
import shutil
from pathlib import Path
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QRadioButton, QTextEdit, QProgressBar,
    QCheckBox, QListWidget, QListWidgetItem, QFrame
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QMimeData
from PyQt5.QtGui import QFont, QDragEnterEvent, QDropEvent, QPalette, QColor

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent))

from src.exact_report_generator import ExactReportGenerator
from src.technical_info_extractor import TechnicalInfoExtractor
from src.csv_importer import CSVImporter
from src.pdf_extractor import PDFExtractor
from src.conclusion_generator import ConclusionGenerator

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DropZone(QFrame):
    """Drag & Drop зона для файлов"""
    
    files_dropped = pyqtSignal(list)  # Сигнал при добавлении файлов
    
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.init_ui()
    
    def init_ui(self):
        """Инициализация UI зоны"""
        self.setMinimumHeight(200)
        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        
        # Стиль по умолчанию
        self.default_style = """
            QFrame {
                background-color: #F5F5F5;
                border: 2px dashed #BDBDBD;
                border-radius: 12px;
            }
        """
        
        # Стиль при наведении
        self.hover_style = """
            QFrame {
                background-color: #E8F5E9;
                border: 2px dashed #4CAF50;
                border-radius: 12px;
            }
        """
        
        self.setStyleSheet(self.default_style)
        
        # Layout
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignCenter)
        
        # Иконка
        icon_label = QLabel("📁")
        icon_label.setFont(QFont("SF Pro Display", 48))
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet("background: transparent; border: none; color: #757575;")
        layout.addWidget(icon_label)
        
        # Текст
        text_label = QLabel("Перетащите файлы сюда")
        text_label.setFont(QFont("SF Pro Display", 16, QFont.DemiBold))
        text_label.setAlignment(Qt.AlignCenter)
        text_label.setStyleSheet("background: transparent; border: none; color: #424242;")
        layout.addWidget(text_label)
        
        # Подсказка
        hint_label = QLabel("Аудио, видео, CSV, PDF")
        hint_label.setFont(QFont("SF Pro Text", 11))
        hint_label.setAlignment(Qt.AlignCenter)
        hint_label.setStyleSheet("background: transparent; border: none; color: #757575;")
        layout.addWidget(hint_label)
        
        self.setLayout(layout)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """Обработка входа файла в зону"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self.hover_style)
    
    def dragLeaveEvent(self, event):
        """Обработка выхода файла из зоны"""
        self.setStyleSheet(self.default_style)
    
    def dropEvent(self, event: QDropEvent):
        """Обработка сброса файлов"""
        self.setStyleSheet(self.default_style)
        
        files = []
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                files.append(file_path)
        
        if files:
            self.files_dropped.emit(files)
        
        event.acceptProposedAction()


def convert_dragdrop_to_files(files_data):
    """Преобразует drag & drop файлы в формат v5.11"""
    files = {
        'audio_20_c': None,
        'audio_20_uc': None,
        'audio_51_c': None,
        'audio_51_uc': None,
        'video': None,
        'csv': None,
        'pdf_20': None,
        'pdf_51': None,
        'pdf_20_c': None,
        'pdf_20_uc': None,
        'pdf_51_c': None,
        'pdf_51_uc': None,
        'params': None,
        'all_files': []
    }
    
    # CSV
    if files_data.get('csv'):
        files['csv'] = Path(files_data['csv'][0])
        files['all_files'].append(files['csv'])
    
    # Video
    if files_data.get('video'):
        files['video'] = Path(files_data['video'][0])
        files['all_files'].append(files['video'])
    
    # Обработка аудио файлов (логика из v5.11)
    for audio_file in files_data.get('audio', []):
        file_path = Path(audio_file)
        files['all_files'].append(file_path)
        name_lower = file_path.name.lower()
        
        # Проверяем 5.1 ПЕРЕД 2.0 (как в v5.11)
        if ('5.1' in file_path.name or 
            '_51_' in name_lower or '_51.' in name_lower or 
            name_lower.endswith('_51') or
            '_50_' in name_lower or '_50.' in name_lower or
            '5.0' in file_path.name):
            
            if 'uncens' in name_lower or '_uc' in name_lower:
                files['audio_51_uc'] = file_path
            else:
                files['audio_51_c'] = file_path
        
        # Проверяем 2.0
        elif ('2.0' in file_path.name or 
              '_20_' in name_lower or '_20.' in name_lower or
              name_lower.endswith('_20') or
              '_21_' in name_lower or '_21.' in name_lower or
              '2.1' in file_path.name):
            
            if 'uncens' in name_lower or '_uc' in name_lower:
                files['audio_20_uc'] = file_path
            else:
                files['audio_20_c'] = file_path
    
    # Обработка PDF файлов (логика из v5.11)
    for pdf_file in files_data.get('pdf', []):
        file_path = Path(pdf_file)
        files['all_files'].append(file_path)
        name_lower = file_path.name.lower()
        
        # Проверяем 5.1 ПЕРЕД 2.0
        if ('5.1' in file_path.name or 
            '_51_' in name_lower or '_51.' in name_lower or
            name_lower.endswith('_51') or
            '_50_' in name_lower or '5.0' in file_path.name):
            
            if 'uncens' in name_lower or '_uc' in name_lower:
                files['pdf_51_uc'] = file_path
            elif 'cens' in name_lower or '_c.' in name_lower:
                files['pdf_51_c'] = file_path
            else:
                files['pdf_51'] = file_path
        
        # Проверяем 2.0
        elif ('2.0' in file_path.name or 
              '_20_' in name_lower or '_20.' in name_lower or
              name_lower.endswith('_20') or
              '_21_' in name_lower or '2.1' in file_path.name):
            
            if 'uncens' in name_lower or '_uc' in name_lower:
                files['pdf_20_uc'] = file_path
            elif 'cens' in name_lower or '_c.' in name_lower:
                files['pdf_20_c'] = file_path
            else:
                files['pdf_20'] = file_path
    
    return files


class ProcessingThread(QThread):
    """Поток для обработки файлов и генерации отчета"""
    
    status_update = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, app, files_data, report_type, output_folder):
        super().__init__()
        self.app = app
        self.files_data = files_data
        self.report_type = report_type
        self.output_folder = output_folder
    
    def run(self):
        """Запуск обработки"""
        try:
            import shutil
            
            logger.info(f"=== НАЧАЛО ОБРАБОТКИ ===")
            logger.info(f"Тип отчета: {self.report_type}")
            logger.info(f"Файлов: {sum(len(v) for v in self.files_data.values())}")
            
            # Определяем имя файла из аудио или видео
            audio_files = self.files_data.get('audio', [])
            video_files = self.files_data.get('video', [])
            csv_files = self.files_data.get('csv', [])
            pdf_files = self.files_data.get('pdf', [])
            
            base_name = "отчет"
            if audio_files:
                base_name = Path(audio_files[0]).stem.replace('_20_', '_').replace('_51_', '_').replace('_cens', '').replace('_uncens', '')
            elif video_files:
                base_name = Path(video_files[0]).stem
            
            # Создаем папку для отчета
            self.status_update.emit("📂 Создание папки отчета...")
            self.progress_update.emit(5)
            
            output_dir = Path(self.output_folder) / f"отчет_{base_name}"
            output_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Выходная папка: {output_dir}")
            
            # Копируем PDF файлы в выходную папку и разделяем по типам
            self.status_update.emit("📋 Копирование PDF файлов...")
            self.progress_update.emit(10)
            
            # Словари для хранения путей к разным типам PDF
            pdf_paths = {
                '20_c': None,
                '20_uc': None,
                '20': None,
                '51_c': None,
                '51_uc': None,
                '51': None
            }
            
            for pdf_file in pdf_files:
                dest = output_dir / Path(pdf_file).name
                shutil.copy2(pdf_file, dest)
                logger.info(f"✅ Скопирован PDF: {Path(pdf_file).name}")
                
                filename = Path(pdf_file).stem.lower()
                
                # Определяем тип PDF с приоритетом
                if ('_20_' in filename or '_2.0_' in filename) and 'cens' in filename and 'uncens' not in filename:
                    pdf_paths['20_c'] = str(dest)
                    logger.info(f"📄 PDF 2.0 CENS: {Path(pdf_file).name}")
                elif ('_20_' in filename or '_2.0_' in filename) and 'uncens' in filename:
                    pdf_paths['20_uc'] = str(dest)
                    logger.info(f"📄 PDF 2.0 UNCENS: {Path(pdf_file).name}")
                elif '_20_' in filename or '_2.0_' in filename:
                    pdf_paths['20'] = str(dest)
                    logger.info(f"📄 PDF 2.0: {Path(pdf_file).name}")
                elif ('_51_' in filename or '_5.1_' in filename) and 'cens' in filename and 'uncens' not in filename:
                    pdf_paths['51_c'] = str(dest)
                    logger.info(f"📄 PDF 5.1 CENS: {Path(pdf_file).name}")
                elif ('_51_' in filename or '_5.1_' in filename) and 'uncens' in filename:
                    pdf_paths['51_uc'] = str(dest)
                    logger.info(f"📄 PDF 5.1 UNCENS: {Path(pdf_file).name}")
                elif '_51_' in filename or '_5.1_' in filename:
                    pdf_paths['51'] = str(dest)
                    logger.info(f"📄 PDF 5.1: {Path(pdf_file).name}")
            
            # Выбираем PDF для вставки в отчет (приоритет: cens > uncens > общий)
            copied_pdf_20 = pdf_paths['20_c'] or pdf_paths['20_uc'] or pdf_paths['20']
            copied_pdf_51 = pdf_paths['51_c'] or pdf_paths['51_uc'] or pdf_paths['51']
            
            logger.info(f"Выбран PDF 2.0 для отчета: {copied_pdf_20}")
            logger.info(f"Выбран PDF 5.1 для отчета: {copied_pdf_51}")
            
            # Копируем CSV если есть
            if csv_files:
                dest = output_dir / Path(csv_files[0]).name
                shutil.copy2(csv_files[0], dest)
                logger.info(f"✅ Скопирован CSV: {Path(csv_files[0]).name}")
            
            # Извлечение технической информации
            self.status_update.emit("🔍 Анализ файлов...")
            self.progress_update.emit(20)
            
            tech_extractor = TechnicalInfoExtractor()
            tech_info = {}
            
            # Обработка аудио файлов
            for audio_file in audio_files:
                logger.info(f"Обработка аудио: {audio_file}")
                filename = Path(audio_file).stem.lower()
                
                # Извлекаем метаданные для определения количества каналов
                audio_info = tech_extractor.extract_audio_info(audio_file)
                if not audio_info:
                    logger.warning(f"⚠️  Не удалось извлечь информацию из аудио: {filename}")
                    continue
                
                channels = audio_info.get('channels', 0)
                is_51 = channels >= 6  # 6 каналов = 5.1
                is_20 = channels == 2  # 2 канала = 2.0
                
                # Определяем тип по имени файла (гибкая проверка)
                # Проверяем разные форматы: _5.1_, _51_, - 5.1 -, .5.1., и т.д.
                has_51_marker = ('_51_' in filename or '_5.1_' in filename or 
                                 ' 51 ' in filename or ' 5.1 ' in filename or
                                 '-51-' in filename or '-5.1-' in filename or
                                 '- 51 -' in filename or '- 5.1 -' in filename)
                
                has_20_marker = ('_20_' in filename or '_2.0_' in filename or 
                                 ' 20 ' in filename or ' 2.0 ' in filename or
                                 '-20-' in filename or '-2.0-' in filename or
                                 '- 20 -' in filename or '- 2.0 -' in filename)
                
                is_cens = 'cens' in filename and 'uncens' not in filename
                is_uncens = 'uncens' in filename
                
                # Определяем ключ (приоритет: маркер в имени файла, потом количество каналов)
                if (has_51_marker or is_51) and is_cens:
                    key = 'audio_51_c'
                elif (has_51_marker or is_51) and is_uncens:
                    key = 'audio_51_uc'
                elif (has_20_marker or is_20) and is_cens:
                    key = 'audio_20_c'
                elif (has_20_marker or is_20) and is_uncens:
                    key = 'audio_20_uc'
                elif is_51 and not (is_cens or is_uncens):
                    key = 'audio_51_c'  # По умолчанию cens для 5.1
                    logger.info(f"ℹ️  Нет маркера cens/uncens, используется 5.1 cens по умолчанию")
                elif is_20 and not (is_cens or is_uncens):
                    key = 'audio_20_c'  # По умолчанию cens для 2.0
                    logger.info(f"ℹ️  Нет маркера cens/uncens, используется 2.0 cens по умолчанию")
                else:
                    logger.warning(f"⚠️  Не удалось определить тип аудио: {filename} (channels={channels})")
                    continue
                
                tech_info[key] = audio_info
                logger.info(f"✅ {key}: {audio_info.get('file_name')} (channels={channels})")
            
            self.progress_update.emit(40)
            
            # Обработка видео файла
            if video_files:
                video_file = video_files[0]
                logger.info(f"📹 Обработка видео: {video_file}")
                video_info = tech_extractor.extract_video_info(video_file)
                if video_info:
                    tech_info['video'] = video_info
                    logger.info(f"✅ Видео: {video_info.get('file_name')}, fps={video_info.get('fps')}, format={video_info.get('format')}")
                else:
                    logger.warning("⚠️  Не удалось извлечь информацию о видео")
            
            self.progress_update.emit(50)
            
            # Обработка PDF файлов - извлекаем технические данные
            self.status_update.emit("📊 Извлечение данных из PDF...")
            
            for pdf_file in pdf_files:
                logger.info(f"Обработка PDF: {pdf_file}")
                filename = Path(pdf_file).stem.lower()
                
                # Гибкая проверка разных форматов: _5.1_, _51_, - 5.1 -, и т.д.
                has_51_marker = ('_51_' in filename or '_5.1_' in filename or 
                                 ' 51 ' in filename or ' 5.1 ' in filename or
                                 '-51-' in filename or '-5.1-' in filename or
                                 '- 51 -' in filename or '- 5.1 -' in filename)
                
                has_20_marker = ('_20_' in filename or '_2.0_' in filename or 
                                 ' 20 ' in filename or ' 2.0 ' in filename or
                                 '-20-' in filename or '-2.0-' in filename or
                                 '- 20 -' in filename or '- 2.0 -' in filename)
                
                is_cens = 'cens' in filename and 'uncens' not in filename
                is_uncens = 'uncens' in filename
                
                # Определяем тип PDF
                if has_51_marker and is_cens:
                    key = 'pdf_51_c'
                elif has_51_marker and is_uncens:
                    key = 'pdf_51_uc'
                elif has_51_marker:
                    key = 'pdf_51'
                elif has_20_marker and is_cens:
                    key = 'pdf_20_c'
                elif has_20_marker and is_uncens:
                    key = 'pdf_20_uc'
                elif has_20_marker:
                    key = 'pdf_20'
                else:
                    # Если нет явного маркера, пробуем извлечь из PDF и определить по количеству каналов
                    pdf_data = self.app.pdf_extractor.extract_technical_info(pdf_file)
                    if pdf_data and 'channels' in pdf_data:
                        channels_str = pdf_data.get('channels', '').lower()
                        if '5.1' in channels_str or '6' in channels_str:
                            key = 'pdf_51_c' if is_cens else ('pdf_51_uc' if is_uncens else 'pdf_51')
                            logger.info(f"ℹ️  Определен тип по каналам в PDF: {key}")
                        elif '2.0' in channels_str or '2' in channels_str:
                            key = 'pdf_20_c' if is_cens else ('pdf_20_uc' if is_uncens else 'pdf_20')
                            logger.info(f"ℹ️  Определен тип по каналам в PDF: {key}")
                        else:
                            logger.warning(f"⚠️  Не удалось определить тип PDF: {filename}")
                            continue
                    else:
                        logger.warning(f"⚠️  Не удалось определить тип PDF: {filename}")
                        continue
                    tech_info[key] = pdf_data
                    logger.info(f"✅ {key}: LUFS={pdf_data.get('lufs')}, Peak={pdf_data.get('true_peak')}, LRA={pdf_data.get('lra')}")
                    continue
                
                pdf_data = self.app.pdf_extractor.extract_technical_info(pdf_file)
                if pdf_data:
                    tech_info[key] = pdf_data
                    logger.info(f"✅ {key}: LUFS={pdf_data.get('lufs')}, Peak={pdf_data.get('true_peak')}, LRA={pdf_data.get('lra')}")
            
            self.progress_update.emit(60)
            
            # Импорт проблем из CSV
            self.status_update.emit("📊 Импорт проблем из CSV...")
            
            issues = []
            if csv_files:
                csv_file = csv_files[0]
                logger.info(f"Импорт CSV: {csv_file}")
                importer = CSVImporter()
                issues = importer.import_issues(csv_file)
                logger.info(f"✅ Импортировано проблем: {len(issues)}")
            
            self.progress_update.emit(70)
            
            # Генерация заключений
            self.status_update.emit("📝 Генерация заключений...")
            
            if self.report_type == "me_ours":
                technical_conclusion = "По технической оценке нареканий не выявлено."
                subjective_conclusion = "Ниже предоставлен список внесенных изменений:"
                logger.info("M&E наши работы: идеальные заключения")
            elif self.report_type == "tifflo":
                technical_conclusion = "По технической оценке нареканий не выявлено."
                subjective_conclusion = "По субъективной оценке нареканий не обнаружено."
                logger.info("TIFFLO: идеальный отчет")
            else:
                params = tech_info.get('params', {})
                technical_conclusion = self.app.conclusion_gen.generate_technical_conclusion(tech_info, params, self.report_type)
                subjective_conclusion = self.app.conclusion_gen.generate_subjective_conclusion(issues)
                logger.info("Стандартная генерация заключений")
            
            self.progress_update.emit(85)
            
            # Генерация отчета
            self.status_update.emit("📄 Создание отчета...")
            
            report_path = output_dir / f"отчет_{base_name}_rus.docx"
            
            logger.info(f"Генерация отчета: {report_path}")
            logger.info(f"PDF 2.0: {copied_pdf_20}")
            logger.info(f"PDF 5.1: {copied_pdf_51}")
            
            # Генерируем отчет через единый генератор
            self.app.report_gen.create_exact_report(
                issues=issues,
                output_path=str(report_path),
                tech_info=tech_info,
                pdf_20_path=copied_pdf_20,
                pdf_51_path=copied_pdf_51,
                conclusion_technical=technical_conclusion,
                conclusion_subjective=subjective_conclusion,
                report_type=self.report_type
            )
            
            self.progress_update.emit(100)
            self.status_update.emit("✅ Готово!")
            
            success_msg = f"Отчет создан:\n{report_path}"
            self.finished.emit(True, success_msg)
            
            logger.info(f"=== ЗАВЕРШЕНО УСПЕШНО ===")
            
        except Exception as e:
            logger.error(f"Ошибка при обработке: {e}", exc_info=True)
            self.finished.emit(False, f"Ошибка: {str(e)}")


class BeastApp(QMainWindow):
    """Главное окно приложения с Drag & Drop"""
    
    def __init__(self):
        super().__init__()
        
        # Инициализация компонентов (как в v5.11)
        self.report_gen = ExactReportGenerator()
        self.pdf_extractor = PDFExtractor()
        self.conclusion_gen = ConclusionGenerator(use_llm=False)
        self.csv_importer = CSVImporter()
        self.tech_extractor = TechnicalInfoExtractor()
        
        # Хранилище файлов
        self.files_data = {
            'audio': [],
            'video': [],
            'csv': [],
            'pdf': []
        }
        
        self.init_ui()
    
    def init_ui(self):
        """Инициализация интерфейса"""
        self.setWindowTitle("Beast Auto Reporter - Drag & Drop")
        self.setGeometry(100, 100, 800, 700)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)
        
        # Header
        header = self.create_header()
        layout.addWidget(header)
        
        # Тип отчета
        report_type_card = self.create_report_type_card()
        layout.addWidget(report_type_card)
        
        # AI опция
        ai_card = self.create_ai_card()
        layout.addWidget(ai_card)
        
        # Drag & Drop зона
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self.handle_dropped_files)
        layout.addWidget(self.drop_zone)
        
        # Список добавленных файлов
        files_label = QLabel("📂 Добавленные файлы:")
        files_label.setFont(QFont("SF Pro Display", 12, QFont.DemiBold))
        layout.addWidget(files_label)
        
        self.files_list = QListWidget()
        self.files_list.setMaximumHeight(150)
        self.files_list.setStyleSheet("""
            QListWidget {
                background-color: white;
                border: 1px solid #E0E0E0;
                border-radius: 8px;
                padding: 8px;
            }
            QListWidget::item {
                padding: 4px;
                border-bottom: 1px solid #F5F5F5;
            }
        """)
        layout.addWidget(self.files_list)
        
        # Кнопки управления
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(12)
        
        clear_btn = QPushButton("🗑️ Очистить")
        clear_btn.setFont(QFont("SF Pro Text", 11))
        clear_btn.setFixedHeight(40)
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #F5F5F5;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                color: #424242;
            }
            QPushButton:hover {
                background-color: #EEEEEE;
            }
        """)
        clear_btn.clicked.connect(self.clear_files)
        buttons_layout.addWidget(clear_btn)
        
        buttons_layout.addStretch()
        
        self.generate_btn = QPushButton("✨ Создать отчет")
        self.generate_btn.setFont(QFont("SF Pro Text", 12, QFont.DemiBold))
        self.generate_btn.setFixedHeight(48)
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #6200EE;
                border: none;
                border-radius: 12px;
                padding: 12px 32px;
                color: white;
            }
            QPushButton:hover {
                background-color: #3700B3;
            }
            QPushButton:disabled {
                background-color: #E0E0E0;
                color: #9E9E9E;
            }
        """)
        self.generate_btn.clicked.connect(self.start_processing)
        self.generate_btn.setEnabled(False)
        buttons_layout.addWidget(self.generate_btn)
        
        layout.addLayout(buttons_layout)
        
        # Прогресс бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 8px;
                background-color: #F5F5F5;
                height: 24px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #6200EE;
                border-radius: 8px;
            }
        """)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Статус
        self.status_label = QLabel("")
        self.status_label.setFont(QFont("SF Pro Text", 10))
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #757575;")
        layout.addWidget(self.status_label)
        
        central_widget.setLayout(layout)
    
    def create_header(self):
        """Создание заголовка"""
        header_widget = QWidget()
        header_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #6200EE, stop:1 #3700B3);
                border-radius: 16px;
                padding: 16px;
            }
        """)
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        
        title_label = QLabel("🎵 Beast Auto Reporter")
        title_label.setFont(QFont("SF Pro Display", 24, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: white; background: transparent;")
        header_layout.addWidget(title_label)
        
        subtitle_label = QLabel("Перетащите файлы для создания отчета")
        subtitle_label.setFont(QFont("SF Pro Display", 11))
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); background: transparent;")
        header_layout.addWidget(subtitle_label)
        
        header_widget.setLayout(header_layout)
        return header_widget
    
    def create_report_type_card(self):
        """Создание карточки выбора типа отчета"""
        card = QWidget()
        card.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 12px;
                border: 1px solid #E0E0E0;
            }
        """)
        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(8)
        
        card_title = QLabel("📋 Тип отчета")
        card_title.setFont(QFont("SF Pro Display", 13, QFont.DemiBold))
        card_title.setStyleSheet("color: #1C1B1F; background: transparent; border: none;")
        card_layout.addWidget(card_title)
        
        # Радиокнопки
        radio_layout = QHBoxLayout()
        radio_layout.setSpacing(16)
        
        self.report_type_main = QRadioButton("🎵 Основной")
        self.report_type_main.setFont(QFont("SF Pro Text", 10))
        self.report_type_main.setChecked(True)
        self.report_type_main.setStyleSheet("background: transparent; border: none;")
        radio_layout.addWidget(self.report_type_main)
        
        self.report_type_me = QRadioButton("🎼 M&E")
        self.report_type_me.setFont(QFont("SF Pro Text", 10))
        self.report_type_me.setStyleSheet("background: transparent; border: none;")
        radio_layout.addWidget(self.report_type_me)
        
        self.report_type_me_ours = QRadioButton("✅ M&E (наши)")
        self.report_type_me_ours.setFont(QFont("SF Pro Text", 10))
        self.report_type_me_ours.setStyleSheet("background: transparent; border: none; color: #1B5E20; font-weight: 600;")
        radio_layout.addWidget(self.report_type_me_ours)
        
        self.report_type_tifflo = QRadioButton("🎬 TIFFLO")
        self.report_type_tifflo.setFont(QFont("SF Pro Text", 10))
        self.report_type_tifflo.setStyleSheet("background: transparent; border: none; color: #1B5E20; font-weight: 600;")
        radio_layout.addWidget(self.report_type_tifflo)
        
        radio_layout.addStretch()
        card_layout.addLayout(radio_layout)
        
        card.setLayout(card_layout)
        return card
    
    def create_ai_card(self):
        """Создание карточки AI опций"""
        card = QWidget()
        card.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 12px;
                border: 1px solid #E0E0E0;
            }
        """)
        card_layout = QHBoxLayout()
        card_layout.setContentsMargins(16, 12, 16, 12)
        
        self.ai_enabled_checkbox = QCheckBox("🤖 AI генерация заключений (Beta)")
        self.ai_enabled_checkbox.setFont(QFont("SF Pro Text", 10))
        self.ai_enabled_checkbox.setStyleSheet("background: transparent; border: none;")
        self.ai_enabled_checkbox.setChecked(False)
        self.ai_enabled_checkbox.stateChanged.connect(self.toggle_ai_generation)
        card_layout.addWidget(self.ai_enabled_checkbox)
        
        card_layout.addStretch()
        
        card.setLayout(card_layout)
        return card
    
    def handle_dropped_files(self, files):
        """Обработка перетащенных файлов"""
        logger.info(f"Получено файлов: {len(files)}")
        
        for file_path in files:
            file_name = os.path.basename(file_path)
            file_ext = Path(file_path).suffix.lower()
            filename_lower = Path(file_path).stem.lower()
            
            # Определяем тип файла
            if file_ext in ['.wav', '.mp3', '.flac', '.aac']:
                self.files_data['audio'].append(file_path)
                # Определяем тип аудио для отображения
                if '20' in filename_lower and 'cens' in filename_lower and 'uncens' not in filename_lower:
                    icon = "🎵 [2.0 C]"
                elif '20' in filename_lower and 'uncens' in filename_lower:
                    icon = "🎵 [2.0 UC]"
                elif '51' in filename_lower and 'cens' in filename_lower and 'uncens' not in filename_lower:
                    icon = "🎵 [5.1 C]"
                elif '51' in filename_lower and 'uncens' in filename_lower:
                    icon = "🎵 [5.1 UC]"
                else:
                    icon = "🎵"
            elif file_ext in ['.mp4', '.mov', '.avi', '.mkv', '.mxf']:
                self.files_data['video'].append(file_path)
                icon = "🎬"
            elif file_ext == '.csv':
                self.files_data['csv'].append(file_path)
                icon = "📊"
            elif file_ext == '.pdf':
                self.files_data['pdf'].append(file_path)
                # Определяем тип PDF для отображения
                if '20' in filename_lower and 'cens' in filename_lower and 'uncens' not in filename_lower:
                    icon = "📄 [2.0 C]"
                elif '20' in filename_lower and 'uncens' in filename_lower:
                    icon = "📄 [2.0 UC]"
                elif '51' in filename_lower and 'cens' in filename_lower and 'uncens' not in filename_lower:
                    icon = "📄 [5.1 C]"
                elif '51' in filename_lower and 'uncens' in filename_lower:
                    icon = "📄 [5.1 UC]"
                else:
                    icon = "📄"
            else:
                continue
            
            # Добавляем в список
            item = QListWidgetItem(f"{icon} {file_name}")
            item.setToolTip(file_path)
            self.files_list.addItem(item)
        
        # Обновляем доступность кнопки
        total_files = sum(len(v) for v in self.files_data.values())
        self.generate_btn.setEnabled(total_files > 0)
        
        # Показываем детальную статистику
        stats = []
        if self.files_data['audio']:
            stats.append(f"🎵 Аудио: {len(self.files_data['audio'])}")
        if self.files_data['video']:
            stats.append(f"🎬 Видео: {len(self.files_data['video'])}")
        if self.files_data['csv']:
            stats.append(f"📊 CSV: {len(self.files_data['csv'])}")
        if self.files_data['pdf']:
            stats.append(f"📄 PDF: {len(self.files_data['pdf'])}")
        
        self.status_label.setText(" | ".join(stats) if stats else "Нет файлов")
    
    def clear_files(self):
        """Очистка списка файлов"""
        self.files_data = {
            'audio': [],
            'video': [],
            'csv': [],
            'pdf': []
        }
        self.files_list.clear()
        self.generate_btn.setEnabled(False)
        self.status_label.setText("")
    
    def toggle_ai_generation(self, state):
        """Переключение AI генерации"""
        enabled = state == Qt.Checked
        self.conclusion_gen.use_llm = enabled
        logger.info(f"AI генерация: {'ВКЛЮЧЕНА' if enabled else 'ВЫКЛЮЧЕНА'}")
    
    def get_report_type(self):
        """Получение выбранного типа отчета"""
        if self.report_type_me.isChecked():
            return "me"
        elif self.report_type_me_ours.isChecked():
            return "me_ours"
        elif self.report_type_tifflo.isChecked():
            return "tifflo"
        else:
            return "standard"
    
    def start_processing(self):
        """Запуск обработки файлов"""
        logger.info("=== ЗАПУСК ОБРАБОТКИ ===")
        
        import tempfile
        
        # Создаем временную папку для drag & drop файлов
        temp_dir = Path(tempfile.mkdtemp(prefix="beast_dragdrop_"))
        logger.info(f"Временная папка: {temp_dir}")
        
        # Копируем файлы во временную папку
        for file_list in self.files_data.values():
            for file_path in file_list:
                dest = temp_dir / Path(file_path).name
                shutil.copy2(file_path, dest)
                logger.info(f"Скопирован во временную папку: {Path(file_path).name}")
        
        # Отключаем кнопки
        self.generate_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        # Импортируем ProcessingThread из рабочей версии
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from beast_app_final import ProcessingThread as WorkingProcessingThread
        
        # Запускаем поток обработки с рабочей логикой v5.11
        report_type = self.get_report_type()
        self.thread = WorkingProcessingThread(self, temp_dir, report_type)
        self.thread.status_update.connect(self.status_label.setText)
        self.thread.progress_update.connect(self.progress_bar.setValue)
        self.thread.finished.connect(lambda msg, success: self.processing_finished_with_cleanup(msg, success, temp_dir))
        self.thread.start()
    
    def processing_finished_with_cleanup(self, message, success, temp_dir):
        """Завершение обработки с очисткой временной папки"""
        # Удаляем временную папку
        try:
            shutil.rmtree(temp_dir)
            logger.info(f"Временная папка удалена: {temp_dir}")
        except Exception as e:
            logger.warning(f"Не удалось удалить временную папку: {e}")
        
        # Вызываем обычный обработчик
        self.processing_finished(success, message)
    
    def processing_finished(self, success, message):
        """Завершение обработки"""
        self.progress_bar.setVisible(False)
        self.generate_btn.setEnabled(True)
        
        if success:
            self.status_label.setText("✅ Отчет успешно создан!")
            # Очищаем файлы после успешной генерации
            self.clear_files()
        else:
            self.status_label.setText(f"❌ Ошибка: {message}")
    
    def extract_base_name(self, filename: str) -> str:
        """Извлечение базового названия из имени файла (из v5.11)"""
        import re
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
        """Поиск файлов в папке (полная логика из v5.11)"""
        import re
        import subprocess
        
        def find_ffprobe():
            """Поиск ffprobe"""
            import shutil
            ffprobe_path = shutil.which('ffprobe')
            if ffprobe_path:
                return ffprobe_path
            return 'ffprobe'
        
        files = {
            'audio_20_c': None,
            'audio_20_uc': None,
            'audio_51_c': None,
            'audio_51_uc': None,
            'video': None,
            'csv': None,
            'pdf_20': None,
            'pdf_51': None,
            'pdf_20_c': None,
            'pdf_20_uc': None,
            'pdf_51_c': None,
            'pdf_51_uc': None,
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
                    # Сначала проверяем 5.1
                    if ('5.1' in file_path.name or 
                        '_51_' in name_lower or '_51.' in name_lower or 
                        name_lower.endswith('_51') or name_lower.endswith('_51.wav') or
                        '_50_' in name_lower or '_50.' in name_lower or
                        '5.0' in file_path.name):
                        
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['audio_51_uc'] = file_path
                        else:
                            files['audio_51_c'] = file_path
                    
                    # Потом проверяем 2.0
                    elif ('2.0' in file_path.name or 
                          '_20_' in name_lower or '_20.' in name_lower or
                          name_lower.endswith('_20') or name_lower.endswith('_20.wav') or
                          '_21_' in name_lower or '2.1' in file_path.name):
                        
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['audio_20_uc'] = file_path
                        else:
                            files['audio_20_c'] = file_path
                    
                    # Если не подошло - по каналам
                    else:
                        try:
                            ffprobe_cmd = find_ffprobe()
                            result = subprocess.run(
                                [ffprobe_cmd, '-v', 'error', '-select_streams', 'a:0', 
                                 '-show_entries', 'stream=channels', '-of', 
                                 'default=noprint_wrappers=1:nokey=1', str(file_path)],
                                capture_output=True, text=True, timeout=5
                            )
                            if result.returncode == 0 and result.stdout.strip():
                                channels = int(result.stdout.strip())
                                is_uncens = 'uncens' in name_lower or '_uc' in name_lower
                                
                                if channels == 2:
                                    files['audio_20_uc' if is_uncens else 'audio_20_c'] = file_path
                                elif channels == 6:
                                    files['audio_51_uc' if is_uncens else 'audio_51_c'] = file_path
                        except:
                            pass
                
                # Видео
                elif file_path.suffix.lower() in ['.mp4', '.mov', '.mkv', '.avi', '.mxf', '.m4v', '.webm', '.flv']:
                    files['video'] = file_path
                
                # PDF файлы
                elif file_path.suffix.lower() == '.pdf':
                    # Проверяем на 5.1
                    if ('5.1' in file_path.name or 
                        '_51_' in name_lower or '_51.' in name_lower or
                        name_lower.endswith('_51') or
                        '_50_' in name_lower or '5.0' in file_path.name):
                        
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['pdf_51_uc'] = file_path
                        else:
                            files['pdf_51_c'] = file_path
                        if not files['pdf_51']:
                            files['pdf_51'] = file_path
                    
                    # Проверяем на 2.0
                    elif ('2.0' in file_path.name or 
                          '_20_' in name_lower or '_20.' in name_lower or
                          name_lower.endswith('_20') or
                          '_21_' in name_lower or '2.1' in file_path.name):
                        
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['pdf_20_uc'] = file_path
                        else:
                            files['pdf_20_c'] = file_path
                        if not files['pdf_20']:
                            files['pdf_20'] = file_path
                    
                    # Автоопределение
                    else:
                        is_uncens = 'uncens' in name_lower or '_uc' in name_lower
                        if not files['pdf_20_c'] and not files['pdf_20_uc']:
                            files['pdf_20_uc' if is_uncens else 'pdf_20_c'] = file_path
                            if not files['pdf_20']:
                                files['pdf_20'] = file_path
                        elif not files['pdf_51_c'] and not files['pdf_51_uc']:
                            files['pdf_51_uc' if is_uncens else 'pdf_51_c'] = file_path
                            if not files['pdf_51']:
                                files['pdf_51'] = file_path
        
        return files
    
    def create_output_folder(self, base_name: str) -> Path:
        """Создание выходной папки (из v5.11)"""
        folder_name = f"отчет_{base_name}_rus"
        
        desktop = Path.home() / "Desktop"
        output_folder = desktop / folder_name
        
        counter = 1
        while output_folder.exists():
            output_folder = desktop / f"{folder_name}_{counter}"
            counter += 1
        
        output_folder.mkdir(parents=True, exist_ok=True)
        
        return output_folder


def main():
    app = QApplication(sys.argv)
    
    # Устанавливаем стиль приложения
    app.setStyle("Fusion")
    
    # Палитра Material You
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(250, 250, 250))
    palette.setColor(QPalette.WindowText, QColor(28, 27, 31))
    app.setPalette(palette)
    
    window = BeastApp()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
