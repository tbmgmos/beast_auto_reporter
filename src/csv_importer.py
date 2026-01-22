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


class CSVImporter:
    """Класс для импорта проблем из CSV"""
    
    def __init__(self):
        logger.info("CSVImporter инициализирован")
    
    def import_issues(self, csv_path: str) -> List[Issue]:
        """
        Импорт проблем из CSV файла
        
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
                
                reader = csv.DictReader(f, delimiter=delimiter)
                
                for row in reader:
                    # Пропускаем пустые строки
                    if not row.get('Timecode In', '').strip():
                        continue
                    
                    issue = Issue(
                        timecode_in=row.get('Timecode In', '').strip(),
                        timecode_out=row.get('Timecode Out', '').strip(),
                        description=row.get('Description', '').strip(),
                        audio_20_c=row.get('2.0 C', '').strip() == '*',
                        audio_20_uc=row.get('2.0 UC', '').strip() == '*',
                        audio_51_c=row.get('5.1 C', '').strip() == '*',
                        audio_51_uc=row.get('5.1 UC', '').strip() == '*',
                        blocker=row.get('БЛОКЕР', '').strip() == '*',
                        fix_required=row.get('ТРЕБУЕТ ИСПРАВЛЕНИЯ', '').strip() == '*',
                        comment_required=row.get('ТРЕБУЕТ КОММЕНТАРИЯ', '').strip() == '*',
                        comments=row.get('КОММЕНТАРИИ', '').strip() if 'КОММЕНТАРИИ' in row else ''
                    )
                    
                    issues.append(issue)
            
            logger.info(f"Импортировано {len(issues)} проблем из CSV")
            
        except Exception as e:
            logger.error(f"Ошибка импорта CSV: {e}")
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

