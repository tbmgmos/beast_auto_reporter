#!/usr/bin/env python3
"""
Beast Auto Reporter - Desktop Application

Desktop приложение с drag & drop для создания отчетов
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sys
from pathlib import Path
import shutil
import re
from datetime import datetime
import threading
import logging

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


class BeastAutoReporterApp:
    """Desktop приложение Beast Auto Reporter"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Beast Auto Reporter")
        self.root.geometry("800x600")
        
        # Загрузка конфигурации
        self.config = self.load_config()
        
        # Инициализация компонентов
        self.analyzer = AudioAnalyzer(self.config)
        self.detector = DefectDetector(self.config)
        self.generator = TemplateReportGenerator()
        self.pdf_extractor = PDFExtractor()
        self.tc_analyzer = TimecodeAnalyzer()
        
        # Переменные
        self.input_folder = None
        self.processing = False
        
        self.setup_ui()
    
    def load_config(self):
        """Загрузка конфигурации"""
        config_path = Path(__file__).parent / 'config' / 'settings.yaml'
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        return {}
    
    def setup_ui(self):
        """Настройка интерфейса"""
        
        # Заголовок
        header = tk.Frame(self.root, bg="#1f77b4", height=80)
        header.pack(fill=tk.X)
        
        title_label = tk.Label(
            header,
            text="🎵 Beast Auto Reporter",
            font=("Arial", 24, "bold"),
            bg="#1f77b4",
            fg="white"
        )
        title_label.pack(pady=20)
        
        # Основная область
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Инструкции
        instructions = tk.Label(
            main_frame,
            text="📁 Выберите папку с файлами для создания отчета",
            font=("Arial", 14),
            fg="#333"
        )
        instructions.pack(pady=10)
        
        # Область drag & drop / выбора папки
        drop_frame = tk.Frame(
            main_frame,
            bg="#f0f2f6",
            relief=tk.RIDGE,
            borderwidth=2
        )
        drop_frame.pack(fill=tk.BOTH, expand=True, pady=20)
        
        self.folder_label = tk.Label(
            drop_frame,
            text="📂 Нажмите 'Выбрать папку' или перетащите папку сюда",
            font=("Arial", 12),
            bg="#f0f2f6",
            fg="#666"
        )
        self.folder_label.pack(pady=50)
        
        # Кнопка выбора папки
        select_button = tk.Button(
            main_frame,
            text="📁 Выбрать папку",
            font=("Arial", 14, "bold"),
            bg="#4CAF50",
            fg="white",
            padx=30,
            pady=10,
            command=self.select_folder
        )
        select_button.pack(pady=10)
        
        # Кнопка создания отчета
        self.process_button = tk.Button(
            main_frame,
            text="🎯 СОЗДАТЬ ОТЧЕТ",
            font=("Arial", 16, "bold"),
            bg="#2196F3",
            fg="white",
            padx=40,
            pady=15,
            command=self.process_folder,
            state=tk.DISABLED
        )
        self.process_button.pack(pady=20)
        
        # Прогресс бар
        self.progress = ttk.Progressbar(
            main_frame,
            mode='indeterminate',
            length=400
        )
        self.progress.pack(pady=10)
        
        # Статус
        self.status_label = tk.Label(
            main_frame,
            text="Ожидание...",
            font=("Arial", 11),
            fg="#666"
        )
        self.status_label.pack(pady=5)
        
        # Нижняя информация
        footer = tk.Frame(self.root, bg="#f0f2f6", height=40)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        
        footer_label = tk.Label(
            footer,
            text="Beast Auto Reporter V2 | Все обработки локальные",
            font=("Arial", 9),
            bg="#f0f2f6",
            fg="#999"
        )
        footer_label.pack(pady=10)
    
    def select_folder(self):
        """Выбор папки"""
        folder = filedialog.askdirectory(title="Выберите папку с файлами")
        if folder:
            self.input_folder = Path(folder)
            self.folder_label.config(
                text=f"✓ Выбрана папка:\n{self.input_folder.name}"
            )
            self.process_button.config(state=tk.NORMAL)
            self.status_label.config(text="Готово к обработке")
    
    def extract_base_name(self, filename: str) -> str:
        """
        Извлечение базового названия из имени файла
        Убирает: 51, 20, stereo, cense, uncense, и т.д.
        
        Args:
            filename: Имя файла
            
        Returns:
            Очищенное имя
        """
        # Убираем расширение
        name = Path(filename).stem
        
        # Слова для удаления
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
        
        # Удаляем каждое слово
        for word in remove_words:
            name = re.sub(word, '_', name, flags=re.IGNORECASE)
        
        # Убираем множественные подчеркивания
        name = re.sub(r'_+', '_', name)
        
        # Убираем подчеркивания в начале и конце
        name = name.strip('_')
        
        return name
    
    def find_files_in_folder(self, folder: Path) -> dict:
        """
        Поиск файлов в папке
        
        Returns:
            Словарь с найденными файлами
        """
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
                
                # Параметры.txt
                if 'параметры' in name_lower or 'parametry' in name_lower:
                    files['params'] = file_path
                
                # Аудио файлы
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
                
                # Видео
                elif file_path.suffix.lower() in ['.mp4', '.mov', '.mkv', '.avi']:
                    files['video'] = file_path
                
                # PDF
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
            
            # Парсинг
            import re
            lufs_match = re.search(r'(-?\d+\.?\d*)\s*LUFS', content)
            if lufs_match:
                params['target_lufs'] = float(lufs_match.group(1))
            
            return params, content
        except Exception as e:
            logger.error(f"Ошибка чтения параметров: {e}")
            return None, None
    
    def create_output_folder(self, base_name: str) -> Path:
        """
        Создание выходной папки
        
        Args:
            base_name: Базовое имя
            
        Returns:
            Path к созданной папке
        """
        # Формат: отчет_название_дата_rus
        timestamp = datetime.now().strftime('%Y_%m_%d')
        folder_name = f"отчет_{base_name}_{timestamp}_rus"
        
        # Создаем в Desktop
        desktop = Path.home() / "Desktop"
        output_folder = desktop / folder_name
        
        # Если существует, добавляем номер
        counter = 1
        original_folder = output_folder
        while output_folder.exists():
            output_folder = desktop / f"{folder_name}_{counter}"
            counter += 1
        
        output_folder.mkdir(parents=True, exist_ok=True)
        
        return output_folder
    
    def process_folder(self):
        """Обработка папки"""
        if self.processing:
            return
        
        if not self.input_folder:
            messagebox.showerror("Ошибка", "Выберите папку!")
            return
        
        # Запускаем в отдельном потоке
        thread = threading.Thread(target=self._process_folder_thread)
        thread.daemon = True
        thread.start()
    
    def _process_folder_thread(self):
        """Обработка в отдельном потоке"""
        self.processing = True
        self.process_button.config(state=tk.DISABLED)
        self.progress.start()
        
        try:
            # === ШАГ 1: Поиск файлов ===
            self.update_status("📁 Поиск файлов...")
            files = self.find_files_in_folder(self.input_folder)
            
            if not files['audio_20_c'] and not files['audio_51_c']:
                messagebox.showerror(
                    "Ошибка",
                    "Не найдены аудио файлы!\n\nДолжен быть хотя бы один файл: 2.0 или 5.1"
                )
                return
            
            # === ШАГ 2: Извлечение базового имени ===
            self.update_status("📝 Определение названия...")
            
            # Берем имя из первого найденного аудио файла
            audio_file = files['audio_20_c'] or files['audio_51_c']
            base_name = self.extract_base_name(audio_file.name)
            
            logger.info(f"Базовое имя: {base_name}")
            
            # === ШАГ 3: Создание выходной папки ===
            self.update_status("📂 Создание выходной папки...")
            output_folder = self.create_output_folder(base_name)
            
            logger.info(f"Выходная папка: {output_folder}")
            
            # === ШАГ 4: Копирование исходных файлов ===
            self.update_status("📋 Копирование файлов...")
            
            for file_path in files['all_files']:
                dest = output_folder / file_path.name
                shutil.copy2(file_path, dest)
                logger.info(f"Скопирован: {file_path.name}")
            
            # === ШАГ 5: Чтение параметров ===
            if files['params']:
                self.update_status("⚙️ Чтение параметров...")
                params_dict, params_text = self.read_params_file(files['params'])
                if params_dict:
                    self.config['audio'].update(params_dict)
                    self.analyzer = AudioAnalyzer(self.config)
                    self.detector = DefectDetector(self.config)
            
            # === ШАГ 6: Анализ хронометража ===
            self.update_status("⏱️ Анализ хронометража...")
            
            timecode_info = self.tc_analyzer.analyze_all_files(
                audio_20=str(files['audio_20_c']) if files['audio_20_c'] else None,
                audio_51=str(files['audio_51_c']) if files['audio_51_c'] else None,
                video=str(files['video']) if files['video'] else None
            )
            
            # === ШАГ 7: Детекция дефектов ===
            self.update_status("🔍 Детекция дефектов...")
            
            all_defects = []
            
            if files['audio_20_c']:
                self.update_status("🔍 Детекция в 2.0...")
                defects_20 = self.detector.analyze_file(str(files['audio_20_c']), "2.0")
                all_defects.extend(defects_20)
            
            if files['audio_51_c']:
                self.update_status("🔍 Детекция в 5.1...")
                defects_51 = self.detector.analyze_file(str(files['audio_51_c']), "5.1")
                all_defects.extend(defects_51)
            
            # Сортируем по таймкоду
            all_defects.sort(key=lambda d: getattr(d, 'timecode_in', '00:00:00:00'))
            
            logger.info(f"Найдено дефектов: {len(all_defects)}")
            
            # === ШАГ 8: Генерация отчетов ===
            self.update_status("📄 Генерация отчетов...")
            
            # Имя отчета
            timestamp = datetime.now().strftime('%Y_%m_%d')
            report_name = f"отчет_{base_name}_{timestamp}_rus"
            
            # DOCX отчет
            docx_path = output_folder / f"{report_name}.docx"
            self.generator.create_report_from_template(
                defects=all_defects,
                output_path=str(docx_path),
                timecode_info=timecode_info
            )
            
            # CSV отчет (опционально)
            csv_path = output_folder / f"{base_name}_{timestamp}_rus.csv"
            self._create_csv_report(all_defects, csv_path)
            
            # === ЗАВЕРШЕНО ===
            self.update_status("✅ Готово!")
            
            messagebox.showinfo(
                "Успех!",
                f"✅ Отчет создан!\n\n"
                f"📁 Папка: {output_folder.name}\n"
                f"📍 Расположение: Desktop\n"
                f"📝 Дефектов: {len(all_defects)}\n\n"
                f"Открыть папку?"
            )
            
            # Открываем папку
            import subprocess
            subprocess.run(['open', str(output_folder)])
            
        except Exception as e:
            logger.exception("Ошибка обработки")
            messagebox.showerror(
                "Ошибка",
                f"Произошла ошибка:\n\n{str(e)}"
            )
        
        finally:
            self.processing = False
            self.progress.stop()
            self.process_button.config(state=tk.NORMAL)
            self.update_status("Ожидание...")
    
    def _create_csv_report(self, defects, output_path):
        """Создание CSV отчета"""
        import csv
        
        try:
            with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f, delimiter='\t')
                
                # Заголовки
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
                
                # Данные
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
    
    def update_status(self, text: str):
        """Обновление статуса"""
        self.status_label.config(text=text)
        self.root.update()


def main():
    """Главная функция"""
    root = tk.Tk()
    app = BeastAutoReporterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

