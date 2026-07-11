"""
CSV Importer Module

Импортирует список проблем из CSV файла
"""

import csv
import logging
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Issue:
    """Класс для хранения информации о проблеме из CSV"""
    timecode_in: str
    timecode_out: str
    description: str
    audio_20_c: bool
    audio_20_uc: bool
    audio_51_c: bool
    audio_51_uc: bool
    blocker: bool
    fix_required: bool
    comment_required: bool
    comments: str = ""
    description_original: str = ""
    description_ru: str = ""
    source_language: str = "unknown"

    def __post_init__(self):
        if not self.description_original:
            self.description_original = self.description or ""
        if not self.description_ru and self.source_language == "ru":
            self.description_ru = self.description or ""


class CSVImporter:
    """Класс для импорта проблем из CSV"""
    
    def __init__(self):
        logger.info("CSVImporter инициализирован")
    
    def _get_column_value(self, row: Dict, *column_names: str) -> str:
        """
        Получить значение колонки по нескольким возможным названиям
        Пробует каждое название по порядку, возвращает первое найденное
        """
        for name in column_names:
            if name in row and row[name]:
                return row[name].strip()
        return ''
    
    def import_issues(self, csv_path: str) -> List[Issue]:
        """
        Импорт проблем из CSV файла
        Поддерживает английские и русские названия колонок
        
        Args:
            csv_path: Путь к CSV файлу
            
        Returns:
            Список проблем
        """
        issues = []
        
        try:
            logger.info(f"Импорт проблем из CSV: {csv_path}")
            
            with open(csv_path, 'r', encoding='utf-8') as f:
                # Определяем разделитель (может быть табуляция или запятая)
                first_line = f.readline()
                f.seek(0)
                
                delimiter = '\t' if '\t' in first_line else ','
                logger.info(f"Разделитель CSV: {'табуляция' if delimiter == chr(9) else 'запятая'}")
                
                reader = csv.DictReader(f, delimiter=delimiter)
                
                # Логируем найденные колонки
                if reader.fieldnames:
                    logger.info(f"Найдено колонок: {len(reader.fieldnames)}")
                    logger.info(f"Названия колонок: {reader.fieldnames}")
                
                row_count = 0
                for row in reader:
                    row_count += 1
                    
                    # Получаем таймкод (пробуем английский и русский варианты)
                    timecode_in = self._get_column_value(row, 'Timecode In', 'TC IN', 'TC_IN')
                    
                    # Пропускаем пустые строки
                    if not timecode_in:
                        logger.debug(f"Строка {row_count}: пропущена (нет таймкода)")
                        continue
                    
                    issue = Issue(
                        timecode_in=timecode_in,
                        timecode_out=self._get_column_value(row, 'Timecode Out', 'TC OUT', 'TC_OUT'),
                        description=self._get_column_value(row, 'Description', 'ОПИСАНИЕ ПРОБЛЕМЫ', 'ОПИСАНИЕ'),
                        audio_20_c=self._get_column_value(row, '2.0 C') == '*',
                        audio_20_uc=self._get_column_value(row, '2.0 UC') == '*',
                        audio_51_c=self._get_column_value(row, '5.1 C') == '*',
                        audio_51_uc=self._get_column_value(row, '5.1 UC') == '*',
                        blocker=self._get_column_value(row, 'БЛОКЕР', 'BLOCKER') == '*',
                        fix_required=self._get_column_value(row, 'ТРЕБУЕТ ИСПРАВЛЕНИЯ', 'FIX REQUIRED') == '*',
                        comment_required=self._get_column_value(row, 'ТРЕБУЕТ КОММЕНТАРИЯ', 'COMMENT REQUIRED') == '*',
                        comments=self._get_column_value(row, 'КОММЕНТАРИИ', 'COMMENTS')
                    )
                    
                    issues.append(issue)
                    logger.debug(f"Строка {row_count}: {timecode_in} - {issue.description[:30]}...")
            
            logger.info(f"✅ Импортировано {len(issues)} проблем из {row_count} строк CSV")
            
            if len(issues) == 0:
                logger.warning("⚠️  CSV файл пустой или не содержит корректных данных!")
                logger.warning("   Проверьте:")
                logger.warning("   1. Есть ли данные в строках?")
                logger.warning("   2. Правильная ли кодировка (UTF-8)?")
                logger.warning("   3. Есть ли колонка 'Timecode In' или 'TC IN'?")
            
        except UnicodeDecodeError as e:
            logger.error(f"❌ Ошибка кодировки CSV файла: {e}")
            logger.error("   Попробуйте сохранить CSV в кодировке UTF-8")
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка импорта CSV: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise
        
        return issues
    
    def categorize_issues(self, issues: List[Issue]) -> Dict:
        """
        Категоризация проблем по типам
        
        Args:
            issues: Список проблем
            
        Returns:
            Словарь с категориями
        """
        categories = {
            'blockers': [],
            'fix_required': [],
            'comment_required': [],
            'total': len(issues)
        }
        
        for issue in issues:
            if issue.blocker:
                categories['blockers'].append(issue)
            elif issue.fix_required:
                categories['fix_required'].append(issue)
            elif issue.comment_required:
                categories['comment_required'].append(issue)
        
        logger.info(f"Категоризация: {len(categories['blockers'])} блокеров, "
                   f"{len(categories['fix_required'])} требуют исправления, "
                   f"{len(categories['comment_required'])} требуют комментария")
        
        return categories


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    importer = CSVImporter()
    issues = importer.import_issues('/Users/vladog/Desktop/ШАБЛОН/petr_2_s1_e2_2024_09_12_rus.csv')
    
    print(f"\nИмпортировано: {len(issues)} проблем")
    print("\nПервые 3 проблемы:")
    for i, issue in enumerate(issues[:3], 1):
        print(f"\n{i}. {issue.timecode_in} - {issue.description[:50]}...")
        print(f"   Блокер: {issue.blocker}, Исправление: {issue.fix_required}, Комментарий: {issue.comment_required}")
    
    categories = importer.categorize_issues(issues)
    print(f"\n\nВсего: {categories['total']}")
    print(f"Блокеров: {len(categories['blockers'])}")
    print(f"Требуют исправления: {len(categories['fix_required'])}")
    print(f"Требуют комментария: {len(categories['comment_required'])}")
