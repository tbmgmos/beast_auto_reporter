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
    QPushButton, QLabel, QFileDialog, QProgressBar, QMessageBox, QRadioButton, QCheckBox
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
            
            # Проверяем тип отчета для специальных случаев
            if self.report_type == "me_ours":
                # M&E наши работы - всё идеально, используем M&E шаблон
                technical_conclusion = "По технической оценке нареканий не выявлено."
                subjective_conclusion = "Ниже предоставлен список внесенных изменений:"
                logger.info("M&E наши работы: M&E шаблон с идеальными заключениями")
            elif self.report_type == "tifflo":
                # TIFFLO - идеальный отчет
                technical_conclusion = "По технической оценке нареканий не выявлено."
                subjective_conclusion = "По субъективной оценке нареканий не обнаружено."
                logger.info("TIFFLO: идеальный отчет без замечаний")
            else:
                # Стандартная генерация для основного и M&E отчетов
                # Техническая оценка (на основе технических параметров и PDF)
                params = tech_info.get('params', {}) if tech_info else {}
                technical_conclusion = self.app.conclusion_gen.generate_technical_conclusion(tech_info, params)
                
                # Субъективная оценка (на основе проблем из CSV)
                subjective_conclusion = self.app.conclusion_gen.generate_subjective_conclusion(issues)
            
            logger.info("Заключения сгенерированы")
            
            # === ШАГ 8: Генерация отчета ===
            self.status_update.emit("📄 Генерация итогового отчета...")
            self.progress_update.emit(90)
            
            # Логируем tech_info перед генерацией
            logger.info("=== TECH_INFO ПЕРЕД ГЕНЕРАЦИЕЙ ОТЧЕТА ===")
            if tech_info:
                for key, value in tech_info.items():
                    if value:
                        if key == 'video':
                            logger.info(f"  {key}: файл={value.get('file_name')}, duration={value.get('duration')}, fps={value.get('fps')}, format={value.get('format')}")
                        elif key == 'params':
                            logger.info(f"  {key}: {value}")
                        elif key.startswith('pdf'):
                            logger.info(f"  {key}: lufs={value.get('lufs')}, peak={value.get('true_peak')}, lra={value.get('lra')}")
                        elif key.startswith('audio'):
                            logger.info(f"  {key}: файл={value.get('file_name')}, duration={value.get('duration')}")
                    else:
                        logger.warning(f"  {key}: ПУСТО!")
            else:
                logger.error("  tech_info = None!")
            
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
        self.conclusion_gen = ConclusionGenerator(use_llm=False)  # По умолчанию ВЫКЛЮЧЕНО (как в стабильной версии)
        self.report_gen = ExactReportGenerator()
        self.pdf_extractor = PDFExtractor()
        
        self.init_ui()
    
    def init_ui(self):
        """Инициализация интерфейса в стиле Material You"""
        self.setWindowTitle("Beast Auto Reporter")
        self.setGeometry(100, 100, 700, 650)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Material You: компактный layout
        layout = QVBoxLayout()
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)
        
        # === HEADER: компактный заголовок ===
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
        
        subtitle_label = QLabel("Автоматическая генерация отчетов")
        subtitle_label.setFont(QFont("SF Pro Display", 11))
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); background: transparent;")
        header_layout.addWidget(subtitle_label)
        
        header_widget.setLayout(header_layout)
        layout.addWidget(header_widget)
        
        # === КАРТОЧКА: Тип отчета (Material Card) ===
        report_card = QWidget()
        report_card.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 12px;
                border: 1px solid #E0E0E0;
            }
        """)
        report_card_layout = QVBoxLayout()
        report_card_layout.setContentsMargins(16, 12, 16, 12)
        report_card_layout.setSpacing(8)
        
        card_title = QLabel("📋 Тип отчета")
        card_title.setFont(QFont("SF Pro Display", 13, QFont.DemiBold))
        card_title.setStyleSheet("color: #1C1B1F; background: transparent; border: none;")
        report_card_layout.addWidget(card_title)
        
        # Компактные радиокнопки в 2 ряда
        radio_row1 = QHBoxLayout()
        radio_row1.setSpacing(12)
        
        self.report_type_main = QRadioButton("🎵 Основной")
        self.report_type_main.setFont(QFont("SF Pro Text", 10))
        self.report_type_main.setChecked(True)
        self.report_type_main.setStyleSheet("""
            QRadioButton {
                background: transparent;
                border: none;
                color: #49454F;
                padding: 4px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
            }
            QRadioButton::indicator::checked {
                background-color: #6200EE;
                border: 2px solid #6200EE;
                border-radius: 8px;
            }
        """)
        radio_row1.addWidget(self.report_type_main)
        
        self.report_type_me = QRadioButton("🎼 M&E")
        self.report_type_me.setFont(QFont("SF Pro Text", 10))
        self.report_type_me.setStyleSheet("""
            QRadioButton {
                background: transparent;
                border: none;
                color: #49454F;
                padding: 4px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
            }
        """)
        radio_row1.addWidget(self.report_type_me)
        radio_row1.addStretch()
        report_card_layout.addLayout(radio_row1)
        
        radio_row2 = QHBoxLayout()
        radio_row2.setSpacing(12)
        
        self.report_type_me_ours = QRadioButton("✅ M&E (наши)")
        self.report_type_me_ours.setFont(QFont("SF Pro Text", 10))
        self.report_type_me_ours.setStyleSheet("""
            QRadioButton {
                background: transparent;
                border: none;
                color: #1B5E20;
                font-weight: 600;
                padding: 4px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
            }
        """)
        radio_row2.addWidget(self.report_type_me_ours)
        
        self.report_type_tifflo = QRadioButton("🎬 TIFFLO")
        self.report_type_tifflo.setFont(QFont("SF Pro Text", 10))
        self.report_type_tifflo.setStyleSheet("""
            QRadioButton {
                background: transparent;
                border: none;
                color: #1B5E20;
                font-weight: 600;
                padding: 4px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
            }
        """)
        radio_row2.addWidget(self.report_type_tifflo)
        radio_row2.addStretch()
        report_card_layout.addLayout(radio_row2)
        
        report_card.setLayout(report_card_layout)
        layout.addWidget(report_card)
        
        # === КАРТОЧКА: AI генерация (компактная) ===
        ai_card = QWidget()
        ai_card.setStyleSheet("""
            QWidget {
                background-color: #FFF8E1;
                border-radius: 12px;
                border: 1px solid #FFD54F;
            }
        """)
        ai_card_layout = QHBoxLayout()
        ai_card_layout.setContentsMargins(16, 10, 16, 10)
        ai_card_layout.setSpacing(8)
        
        ai_icon = QLabel("🤖")
        ai_icon.setFont(QFont("SF Pro Display", 18))
        ai_icon.setStyleSheet("background: transparent; border: none;")
        ai_card_layout.addWidget(ai_icon)
        
        ai_text_layout = QVBoxLayout()
        ai_text_layout.setSpacing(2)
        
        self.ai_enabled_checkbox = QCheckBox("AI генерация (BETA)")
        self.ai_enabled_checkbox.setFont(QFont("SF Pro Text", 11, QFont.DemiBold))
        self.ai_enabled_checkbox.setChecked(False)
        self.ai_enabled_checkbox.setStyleSheet("""
            QCheckBox {
                background: transparent;
                border: none;
                color: #F57C00;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #F57C00;
            }
            QCheckBox::indicator:checked {
                background-color: #F57C00;
            }
        """)
        self.ai_enabled_checkbox.stateChanged.connect(self.toggle_ai_generation)
        ai_text_layout.addWidget(self.ai_enabled_checkbox)
        
        ai_hint = QLabel("Автоматическая субъективная оценка")
        ai_hint.setFont(QFont("SF Pro Text", 9))
        ai_hint.setStyleSheet("background: transparent; border: none; color: #795548;")
        ai_text_layout.addWidget(ai_hint)
        
        ai_card_layout.addLayout(ai_text_layout)
        ai_card_layout.addStretch()
        
        ai_card.setLayout(ai_card_layout)
        layout.addWidget(ai_card)
        
        # === КАРТОЧКА: Папка (компактная) ===
        folder_card = QWidget()
        folder_card.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 12px;
                border: 1px solid #E0E0E0;
            }
        """)
        folder_card_layout = QVBoxLayout()
        folder_card_layout.setContentsMargins(16, 16, 16, 16)
        folder_card_layout.setSpacing(12)
        
        self.folder_label = QLabel("📂 Папка не выбрана")
        self.folder_label.setFont(QFont("SF Pro Text", 11))
        self.folder_label.setAlignment(Qt.AlignCenter)
        self.folder_label.setStyleSheet("color: #79747E; background: transparent; border: none; padding: 12px;")
        folder_card_layout.addWidget(self.folder_label)
        
        # Кнопка выбора папки (Material filled)
        self.select_button = QPushButton("📁 Выбрать папку")
        self.select_button.setFont(QFont("SF Pro Text", 12, QFont.DemiBold))
        self.select_button.setMinimumHeight(40)
        self.select_button.setCursor(Qt.PointingHandCursor)
        self.select_button.setStyleSheet("""
            QPushButton {
                background-color: #E8DEF8;
                color: #21005D;
                border: none;
                border-radius: 20px;
                padding: 0px 24px;
            }
            QPushButton:hover {
                background-color: #D5C7E8;
            }
            QPushButton:pressed {
                background-color: #C4B5D7;
            }
        """)
        self.select_button.clicked.connect(self.select_folder)
        folder_card_layout.addWidget(self.select_button)
        
        folder_card.setLayout(folder_card_layout)
        layout.addWidget(folder_card)
        
        # === FAB: СОЗДАТЬ ОТЧЕТ (Material Floating Action Button) ===
        self.process_button = QPushButton("🎯 СОЗДАТЬ ОТЧЕТ")
        self.process_button.setFont(QFont("SF Pro Display", 14, QFont.Bold))
        self.process_button.setMinimumHeight(56)
        self.process_button.setCursor(Qt.PointingHandCursor)
        self.process_button.setStyleSheet("""
            QPushButton {
                background-color: #6200EE;
                color: white;
                border: none;
                border-radius: 28px;
                padding: 0px 32px;
            }
            QPushButton:hover {
                background-color: #7F39FB;
            }
            QPushButton:pressed {
                background-color: #5600D4;
            }
            QPushButton:disabled {
                background-color: #E0E0E0;
                color: #9E9E9E;
            }
        """)
        self.process_button.setEnabled(False)
        self.process_button.clicked.connect(self.process_folder)
        layout.addWidget(self.process_button)
        
        # === ПРОГРЕСС (Material Linear Progress) ===
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(6)
        self.progress_bar.setMaximumHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #E8DEF8;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #6200EE;
                border-radius: 3px;
            }
        """)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        # === СТАТУС (компактный) ===
        self.status_label = QLabel("Готов к работе")
        self.status_label.setFont(QFont("SF Pro Text", 11))
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #49454F; padding: 8px; background: transparent;")
        layout.addWidget(self.status_label)
        
        layout.addStretch()
        
        # === FOOTER (минималистичный) ===
        footer_label = QLabel("v5.0 • Material Design")
        footer_label.setFont(QFont("SF Pro Text", 9))
        footer_label.setAlignment(Qt.AlignCenter)
        footer_label.setStyleSheet("color: #79747E; background: transparent;")
        layout.addWidget(footer_label)
        
        central_widget.setLayout(layout)
        
        # Material You: светлый фон
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor("#FDFBFF"))
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
            self.folder_label.setText(f"✓ {self.input_folder.name}")
            self.folder_label.setStyleSheet("color: #1B5E20; background: transparent; border: none; padding: 12px; font-weight: 600;")
            self.process_button.setEnabled(True)
            self.status_label.setText("✓ Готово к обработке")
            self.status_label.setStyleSheet("color: #1B5E20; padding: 8px; background: transparent; font-weight: 600;")
    
    def toggle_ai_generation(self, state):
        """Переключение AI генерации"""
        enabled = state == Qt.Checked
        
        if enabled:
            # Включаем AI генерацию
            self.conclusion_gen = ConclusionGenerator(use_llm=True)
            logger.info("✨ AI генерация ВКЛЮЧЕНА (Ollama)")
            logger.info(f"   ConclusionGenerator.use_llm = {self.conclusion_gen.use_llm}")
            self.status_label.setText("🤖 AI режим активирован")
            self.status_label.setStyleSheet("color: #F57C00; padding: 8px; background: transparent; font-weight: 600;")
            QMessageBox.information(self, "AI включен", "✨ AI генерация субъективной оценки АКТИВИРОВАНА\n\nПри создании отчета будет использоваться Ollama для генерации заключений.")
        else:
            # Выключаем AI генерацию (заглушка)
            self.conclusion_gen = ConclusionGenerator(use_llm=False)
            logger.info("📝 AI генерация ВЫКЛЮЧЕНА (ручное заполнение)")
            logger.info(f"   ConclusionGenerator.use_llm = {self.conclusion_gen.use_llm}")
            self.status_label.setText("📝 Ручное заполнение")
            self.status_label.setStyleSheet("color: #49454F; padding: 8px; background: transparent;")
            QMessageBox.information(self, "AI выключен", "📝 AI генерация ВЫКЛЮЧЕНА\n\nСубъективная оценка будет добавлена с заглушкой [ЗАПОЛНИТЬ ВРУЧНУЮ].")
    
    def process_folder(self):
        """Запуск обработки"""
        if not self.input_folder:
            QMessageBox.warning(self, "Ошибка", "Выберите папку!")
            return
        
        self.select_button.setEnabled(False)
        self.process_button.setEnabled(False)
        self.progress_bar.setValue(0)
        
        # Определяем тип отчета
        if self.report_type_me.isChecked():
            report_type = "me"
        elif self.report_type_me_ours.isChecked():
            report_type = "me_ours"
        elif self.report_type_tifflo.isChecked():
            report_type = "tifflo"
        else:
            report_type = "main"
        
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
                
                # Видео (расширенный список форматов)
                elif file_path.suffix.lower() in ['.mp4', '.mov', '.mkv', '.avi', '.mxf', '.m4v', '.webm', '.flv']:
                    files['video'] = file_path
                    logger.info(f"🎬 Видеофайл найден: {file_path.name}")
                
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

