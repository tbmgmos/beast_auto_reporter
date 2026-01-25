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
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QProgressBar, QMessageBox, QRadioButton
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
    
    def __init__(self, app_instance, input_folder, report_type="main"):
        super().__init__()
        self.app = app_instance
        self.input_folder = input_folder
        self.report_type = report_type
    
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
                ('pdf_20_c', 'PDF 2.0 cens'),
                ('pdf_20_uc', 'PDF 2.0 uncens'),
                ('pdf_51_c', 'PDF 5.1 cens'),
                ('pdf_51_uc', 'PDF 5.1 uncens'),
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
            if files['pdf_20_c']:
                files_to_copy.append(files['pdf_20_c'])
            if files['pdf_20_uc']:
                files_to_copy.append(files['pdf_20_uc'])
            if files['pdf_51_c']:
                files_to_copy.append(files['pdf_51_c'])
            if files['pdf_51_uc']:
                files_to_copy.append(files['pdf_51_uc'])
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
                logger.info(f"📹 Извлекаем информацию из видеофайла: {files['video']}")
                tech_info['video'] = self.app.tech_extractor.extract_video_info(str(files['video']))
                if tech_info['video']:
                    logger.info(f"✅ Видео: {tech_info['video'].get('file_name')}, duration={tech_info['video'].get('duration')}, fps={tech_info['video'].get('fps')}, format={tech_info['video'].get('format')}")
                else:
                    logger.warning(f"⚠️  Видео: Не удалось извлечь информацию")
            else:
                logger.warning("⚠️  Видеофайл не найден в files")
            
            # Извлекаем параметры
            if files['params']:
                tech_info['params'] = self.app.tech_extractor.read_params_file(str(files['params']))
            
            # Извлекаем данные из PDF
            self.status_update.emit("📊 Извлечение данных из PDF...")
            self.progress_update.emit(55)
            
            if files['pdf_20_c']:
                tech_info['pdf_20_c'] = self.app.pdf_extractor.extract_technical_info(str(files['pdf_20_c']))
                logger.info(f"PDF 2.0 cens: LUFS={tech_info['pdf_20_c'].get('lufs')}, Peak={tech_info['pdf_20_c'].get('true_peak')}, LRA={tech_info['pdf_20_c'].get('lra')}")
            
            if files['pdf_20_uc']:
                tech_info['pdf_20_uc'] = self.app.pdf_extractor.extract_technical_info(str(files['pdf_20_uc']))
                logger.info(f"PDF 2.0 uncens: LUFS={tech_info['pdf_20_uc'].get('lufs')}, Peak={tech_info['pdf_20_uc'].get('true_peak')}, LRA={tech_info['pdf_20_uc'].get('lra')}")
            
            if files['pdf_51_c']:
                tech_info['pdf_51_c'] = self.app.pdf_extractor.extract_technical_info(str(files['pdf_51_c']))
                logger.info(f"PDF 5.1 cens: LUFS={tech_info['pdf_51_c'].get('lufs')}, Peak={tech_info['pdf_51_c'].get('true_peak')}, LRA={tech_info['pdf_51_c'].get('lra')}")
            
            if files['pdf_51_uc']:
                tech_info['pdf_51_uc'] = self.app.pdf_extractor.extract_technical_info(str(files['pdf_51_uc']))
                logger.info(f"PDF 5.1 uncens: LUFS={tech_info['pdf_51_uc'].get('lufs')}, Peak={tech_info['pdf_51_uc'].get('true_peak')}, LRA={tech_info['pdf_51_uc'].get('lra')}")
            
            # Для обратной совместимости: если есть только старые ключи pdf_20/pdf_51
            if files['pdf_20'] and not (files['pdf_20_c'] or files['pdf_20_uc']):
                tech_info['pdf_20'] = self.app.pdf_extractor.extract_technical_info(str(files['pdf_20']))
                logger.info(f"PDF 2.0: LUFS={tech_info['pdf_20'].get('lufs')}, Peak={tech_info['pdf_20'].get('true_peak')}, LRA={tech_info['pdf_20'].get('lra')}")
            
            if files['pdf_51'] and not (files['pdf_51_c'] or files['pdf_51_uc']):
                tech_info['pdf_51'] = self.app.pdf_extractor.extract_technical_info(str(files['pdf_51']))
                logger.info(f"PDF 5.1: LUFS={tech_info['pdf_51'].get('lufs')}, Peak={tech_info['pdf_51'].get('true_peak')}, LRA={tech_info['pdf_51'].get('lra')}")
            
            # Дублирование PDF данных если один из файлов отсутствует (для обратной совместимости)
            if files['pdf_20'] and not files['pdf_51']:
                tech_info['pdf_51'] = tech_info.get('pdf_20', {}).copy()
                logger.info(f"ℹ️  PDF 5.1 отсутствует, используются данные PDF 2.0")
            elif files['pdf_51'] and not files['pdf_20']:
                tech_info['pdf_20'] = tech_info.get('pdf_51', {}).copy()
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
            
            # Название файла берется из base_name (дата уже в нем)
            report_name = f"отчет_{base_name}_rus.docx"
            report_path = output_folder / report_name
            
            # Определяем пути к PDF файлам (приоритет: cens версии, потом общие)
            pdf_20_path = None
            pdf_51_path = None
            
            if files.get('pdf_20_c'):
                pdf_20_path = str(files['pdf_20_c'])
            elif files.get('pdf_20_uc'):
                pdf_20_path = str(files['pdf_20_uc'])
            elif files.get('pdf_20'):
                pdf_20_path = str(files['pdf_20'])
            
            if files.get('pdf_51_c'):
                pdf_51_path = str(files['pdf_51_c'])
            elif files.get('pdf_51_uc'):
                pdf_51_path = str(files['pdf_51_uc'])
            elif files.get('pdf_51'):
                pdf_51_path = str(files['pdf_51'])
            
            self.app.report_gen.create_exact_report(
                issues=issues,
                output_path=str(report_path),
                tech_info=tech_info,
                pdf_20_path=pdf_20_path,
                pdf_51_path=pdf_51_path,
                conclusion_technical=technical_conclusion,
                conclusion_subjective=subjective_conclusion,
                report_type=self.report_type
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
        self.conclusion_gen = ConclusionGenerator(use_llm=True)  # С Ollama для генерации заключений
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
        
        # ВЫБОР ТИПА ОТЧЕТА
        report_type_group = QWidget()
        report_type_group.setStyleSheet("""
            QWidget {
                background-color: #f8f9fa;
                border: 2px solid #dee2e6;
                border-radius: 8px;
                padding: 15px;
            }
        """)
        report_type_layout = QVBoxLayout()
        report_type_layout.setContentsMargins(10, 10, 10, 10)
        
        report_type_label = QLabel("📋 Выберите тип отчета:")
        report_type_label.setFont(QFont("Arial", 13, QFont.Bold))
        report_type_label.setStyleSheet("background: transparent; border: none; color: #1f77b4;")
        report_type_layout.addWidget(report_type_label)
        
        # Радиокнопки в горизонтальном layout
        radio_layout = QHBoxLayout()
        radio_layout.setSpacing(30)
        
        self.report_type_main = QRadioButton("🎵 Основной (с LOUDNESS, TRUE PEAK, LRA)")
        self.report_type_main.setFont(QFont("Arial", 11))
        self.report_type_main.setChecked(True)  # По умолчанию
        self.report_type_main.setStyleSheet("""
            QRadioButton {
                background: transparent;
                border: none;
                spacing: 8px;
                color: #333;
            }
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        radio_layout.addWidget(self.report_type_main)
        
        self.report_type_me = QRadioButton("🎼 M&E (только TRUE PEAK)")
        self.report_type_me.setFont(QFont("Arial", 11))
        self.report_type_me.setStyleSheet("""
            QRadioButton {
                background: transparent;
                border: none;
                spacing: 8px;
                color: #333;
            }
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        radio_layout.addWidget(self.report_type_me)
        
        radio_layout.addStretch()
        report_type_layout.addLayout(radio_layout)
        
        report_type_group.setLayout(report_type_layout)
        layout.addWidget(report_type_group)
        
        # AI ГЕНЕРАЦИЯ (BETA)
        ai_generation_group = QWidget()
        ai_generation_group.setStyleSheet("""
            QWidget {
                background-color: #fff3cd;
                border: 2px solid #ffc107;
                border-radius: 8px;
                padding: 15px;
            }
        """)
        ai_generation_layout = QVBoxLayout()
        ai_generation_layout.setContentsMargins(10, 10, 10, 10)
        
        ai_label = QLabel("🤖 AI Генерация заключений (BETA)")
        ai_label.setFont(QFont("Arial", 13, QFont.Bold))
        ai_label.setStyleSheet("background: transparent; border: none; color: #ff6b00;")
        ai_generation_layout.addWidget(ai_label)
        
        self.ai_enabled_checkbox = QCheckBox("✨ Включить AI генерацию субъективной оценки (Ollama)")
        self.ai_enabled_checkbox.setFont(QFont("Arial", 11))
        self.ai_enabled_checkbox.setChecked(False)  # По умолчанию ВЫКЛЮЧЕНО
        self.ai_enabled_checkbox.setStyleSheet("""
            QCheckBox {
                background: transparent;
                border: none;
                spacing: 8px;
                color: #333;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
            }
        """)
        self.ai_enabled_checkbox.stateChanged.connect(self.toggle_ai_generation)
        ai_generation_layout.addWidget(self.ai_enabled_checkbox)
        
        ai_warning = QLabel("⚠️ BETA: Может давать неточные результаты. Рекомендуется проверка вручную.")
        ai_warning.setFont(QFont("Arial", 9))
        ai_warning.setStyleSheet("background: transparent; border: none; color: #856404; font-style: italic;")
        ai_generation_layout.addWidget(ai_warning)
        
        ai_generation_group.setLayout(ai_generation_layout)
        layout.addWidget(ai_generation_group)
        
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
    
    def toggle_ai_generation(self, state):
        """Переключение AI генерации"""
        enabled = state == Qt.Checked
        
        if enabled:
            # Включаем AI генерацию
            self.conclusion_gen = ConclusionGenerator(use_llm=True)
            logger.info("✨ AI генерация ВКЛЮЧЕНА (Ollama)")
            self.status_label.setText("✨ AI генерация включена (BETA)")
            self.status_label.setStyleSheet("color: #ff6b00; padding: 10px; font-weight: bold;")
        else:
            # Выключаем AI генерацию (заглушка)
            self.conclusion_gen = ConclusionGenerator(use_llm=False)
            logger.info("📝 AI генерация ВЫКЛЮЧЕНА (ручное заполнение)")
            self.status_label.setText("📝 Субъективная оценка: ручное заполнение")
            self.status_label.setStyleSheet("color: #666; padding: 10px;")
    
    def process_folder(self):
        """Запуск обработки"""
        if not self.input_folder:
            QMessageBox.warning(self, "Ошибка", "Выберите папку!")
            return
        
        self.select_button.setEnabled(False)
        self.process_button.setEnabled(False)
        self.progress_bar.setValue(0)
        
        # Определяем тип отчета
        report_type = "me" if self.report_type_me.isChecked() else "main"
        
        self.processing_thread = ProcessingThread(self, self.input_folder, report_type)
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
                    # Сначала проверяем 5.1 (включая НЕКОРРЕКТНЫЕ паттерны 50, 5.0)
                    # ВАЖНО: используем паттерны с разделителями, чтобы избежать ложных срабатываний (например, s3_e1_2)
                    if ('5.1' in file_path.name or 
                        '_51_' in name_lower or '_51.' in name_lower or '_51_cens' in name_lower or '_51_uncens' in name_lower or
                        name_lower.endswith('_51') or name_lower.endswith('_51.wav') or
                        # НЕКОРРЕКТНЫЕ паттерны (50, 5.0)
                        '_50_' in name_lower or '_50.' in name_lower or '_50_cens' in name_lower or '_50_uncens' in name_lower or
                        name_lower.endswith('_50') or name_lower.endswith('_50.wav') or
                        '5.0' in file_path.name or '_5_0_' in name_lower):
                        
                        # Проверяем на некорректный паттерн
                        is_incorrect = ('_50' in name_lower or '50.' in name_lower or 
                                       '5.0' in file_path.name or '5_0' in file_path.name)
                        
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['audio_51_uc'] = file_path
                            if is_incorrect:
                                logger.warning(f"⚠️  ОШИБКА В НАЗВАНИИ! Аудио 5.1 uncens: {file_path.name} (используйте '51' вместо '50')")
                            else:
                                logger.info(f"✅ Аудио 5.1 uncens найдено: {file_path.name}")
                        else:
                            files['audio_51_c'] = file_path
                            if is_incorrect:
                                logger.warning(f"⚠️  ОШИБКА В НАЗВАНИИ! Аудио 5.1 cens: {file_path.name} (используйте '51' вместо '50')")
                            else:
                                logger.info(f"✅ Аудио 5.1 cens найдено: {file_path.name}")
                    
                    # Потом проверяем 2.0 (включая НЕКОРРЕКТНЫЕ паттерны 21, 2.1)
                    # ВАЖНО: используем паттерны с разделителями
                    elif ('2.0' in file_path.name or 
                          '_20_' in name_lower or '_20.' in name_lower or '_20_cens' in name_lower or '_20_uncens' in name_lower or
                          name_lower.endswith('_20') or name_lower.endswith('_20.wav') or 
                          'stereo' in name_lower or
                          # НЕКОРРЕКТНЫЕ паттерны (21, 2.1)
                          '_21_' in name_lower or '_21.' in name_lower or '_21_cens' in name_lower or '_21_uncens' in name_lower or
                          name_lower.endswith('_21') or name_lower.endswith('_21.wav') or
                          '2.1' in file_path.name or '_2_0_' in name_lower):
                        
                        # Проверяем на некорректный паттерн
                        is_incorrect = ('_21' in name_lower or '21.' in name_lower or
                                       '2.1' in file_path.name or '2_1' in file_path.name)
                        
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['audio_20_uc'] = file_path
                            if is_incorrect:
                                logger.warning(f"⚠️  ОШИБКА В НАЗВАНИИ! Аудио 2.0 uncens: {file_path.name} (используйте '20' вместо '21')")
                            else:
                                logger.info(f"✅ Аудио 2.0 uncens найдено: {file_path.name}")
                        else:
                            files['audio_20_c'] = file_path
                            if is_incorrect:
                                logger.warning(f"⚠️  ОШИБКА В НАЗВАНИИ! Аудио 2.0 cens: {file_path.name} (используйте '20' вместо '21')")
                            else:
                                logger.info(f"✅ Аудио 2.0 cens найдено: {file_path.name}")
                    
                    # Если файл не подошел под паттерны - определяем тип по количеству каналов
                    else:
                        try:
                            import subprocess
                            result = subprocess.run(
                                ['ffprobe', '-v', 'error', '-select_streams', 'a:0', 
                                 '-show_entries', 'stream=channels', '-of', 
                                 'default=noprint_wrappers=1:nokey=1', str(file_path)],
                                capture_output=True, text=True, timeout=5
                            )
                            if result.returncode == 0 and result.stdout.strip():
                                channels = int(result.stdout.strip())
                                
                                # Определяем cens/uncens
                                is_uncens = 'uncens' in name_lower or '_uc' in name_lower
                                
                                if channels == 2:
                                    if is_uncens:
                                        files['audio_20_uc'] = file_path
                                        logger.info(f"✅ Аудио 2.0 uncens (автоопределение по каналам): {file_path.name}")
                                    else:
                                        files['audio_20_c'] = file_path
                                        logger.info(f"✅ Аудио 2.0 cens (автоопределение по каналам): {file_path.name}")
                                elif channels == 6:
                                    if is_uncens:
                                        files['audio_51_uc'] = file_path
                                        logger.info(f"✅ Аудио 5.1 uncens (автоопределение по каналам): {file_path.name}")
                                    else:
                                        files['audio_51_c'] = file_path
                                        logger.info(f"✅ Аудио 5.1 cens (автоопределение по каналам): {file_path.name}")
                                else:
                                    logger.warning(f"⚠️  Аудио с {channels} каналами: {file_path.name} (ожидается 2 или 6)")
                        except Exception as e:
                            logger.warning(f"⚠️  Не удалось определить тип аудио {file_path.name}: {e}")
                
                # Видео
                elif file_path.suffix.lower() in ['.mp4', '.mov', '.mkv', '.avi']:
                    files['video'] = file_path
                
                # PDF файлы (улучшенная логика поиска с поддержкой cens/uncens)
                elif file_path.suffix.lower() == '.pdf':
                    # Проверяем на 5.1 (включая НЕКОРРЕКТНЫЕ паттерны)
                    # ВАЖНО: используем паттерны с разделителями
                    if ('5.1' in file_path.name or 
                        '_51_' in name_lower or '_51.' in name_lower or '_51_cens' in name_lower or '_51_uncens' in name_lower or
                        name_lower.endswith('_51') or name_lower.endswith('_51.pdf') or
                        # НЕКОРРЕКТНЫЕ паттерны
                        '_50_' in name_lower or '_50.' in name_lower or '_50_cens' in name_lower or '_50_uncens' in name_lower or
                        name_lower.endswith('_50') or name_lower.endswith('_50.pdf') or
                        '5.0' in file_path.name or '_5_0_' in name_lower):
                        
                        # Проверяем на некорректный паттерн
                        is_incorrect = ('_50' in name_lower or '50.' in name_lower or
                                       '5.0' in file_path.name or '5_0' in file_path.name)
                        
                        # Проверяем cens/uncens
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['pdf_51_uc'] = file_path
                            if is_incorrect:
                                logger.warning(f"⚠️  ОШИБКА В НАЗВАНИИ! PDF 5.1 uncens: {file_path.name} (используйте '51' вместо '50')")
                            else:
                                logger.info(f"✅ PDF 5.1 uncens найден: {file_path.name}")
                        else:
                            files['pdf_51_c'] = file_path
                            if is_incorrect:
                                logger.warning(f"⚠️  ОШИБКА В НАЗВАНИИ! PDF 5.1 cens: {file_path.name} (используйте '51' вместо '50')")
                            else:
                                logger.info(f"✅ PDF 5.1 cens найден: {file_path.name}")
                        # Также сохраняем в общий ключ для обратной совместимости
                        if not files['pdf_51']:
                            files['pdf_51'] = file_path
                    
                    # Проверяем на 2.0 (включая НЕКОРРЕКТНЫЕ паттерны)
                    # ВАЖНО: используем паттерны с разделителями
                    elif ('2.0' in file_path.name or 
                          '_20_' in name_lower or '_20.' in name_lower or '_20_cens' in name_lower or '_20_uncens' in name_lower or
                          name_lower.endswith('_20') or name_lower.endswith('_20.pdf') or 
                          'stereo' in name_lower or
                          # НЕКОРРЕКТНЫЕ паттерны
                          '_21_' in name_lower or '_21.' in name_lower or '_21_cens' in name_lower or '_21_uncens' in name_lower or
                          name_lower.endswith('_21') or name_lower.endswith('_21.pdf') or
                          '2.1' in file_path.name or '_2_0_' in name_lower):
                        
                        # Проверяем на некорректный паттерн
                        is_incorrect = ('_21' in name_lower or '21.' in name_lower or
                                       '2.1' in file_path.name or '2_1' in file_path.name)
                        
                        # Проверяем cens/uncens
                        if 'uncens' in name_lower or '_uc' in name_lower:
                            files['pdf_20_uc'] = file_path
                            if is_incorrect:
                                logger.warning(f"⚠️  ОШИБКА В НАЗВАНИИ! PDF 2.0 uncens: {file_path.name} (используйте '20' вместо '21')")
                            else:
                                logger.info(f"✅ PDF 2.0 uncens найден: {file_path.name}")
                        else:
                            files['pdf_20_c'] = file_path
                            if is_incorrect:
                                logger.warning(f"⚠️  ОШИБКА В НАЗВАНИИ! PDF 2.0 cens: {file_path.name} (используйте '20' вместо '21')")
                            else:
                                logger.info(f"✅ PDF 2.0 cens найден: {file_path.name}")
                        # Также сохраняем в общий ключ для обратной совместимости
                        if not files['pdf_20']:
                            files['pdf_20'] = file_path
                    # Если не подошло - пытаемся сопоставить с аудио файлами по названию
                    else:
                        # Определяем cens/uncens
                        is_uncens = 'uncens' in name_lower or '_uc' in name_lower
                        
                        # Если еще не нашли 2.0, считаем этот PDF файлом 2.0
                        if not files['pdf_20_c'] and not files['pdf_20_uc']:
                            if is_uncens:
                                files['pdf_20_uc'] = file_path
                                logger.info(f"⚠️ PDF 2.0 uncens (автоопределение): {file_path.name}")
                            else:
                                files['pdf_20_c'] = file_path
                                logger.info(f"⚠️ PDF 2.0 cens (автоопределение): {file_path.name}")
                            if not files['pdf_20']:
                                files['pdf_20'] = file_path
                        # Если уже есть 2.0, но нет 5.1, считаем что это 5.1
                        elif not files['pdf_51_c'] and not files['pdf_51_uc']:
                            if is_uncens:
                                files['pdf_51_uc'] = file_path
                                logger.info(f"⚠️ PDF 5.1 uncens (автоопределение): {file_path.name}")
                            else:
                                files['pdf_51_c'] = file_path
                                logger.info(f"⚠️ PDF 5.1 cens (автоопределение): {file_path.name}")
                            if not files['pdf_51']:
                                files['pdf_51'] = file_path
        
        return files
    
    def create_output_folder(self, base_name: str) -> Path:
        """Создание выходной папки"""
        # Название папки берется из base_name (дата уже в нем)
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
    """Главная функция"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = BeastAutoReporterFinalApp()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

