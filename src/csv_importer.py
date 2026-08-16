"""
CSV Importer Module

Импортирует список проблем из CSV файла
"""

import csv
import logging
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.spellcheck_service import SpellcheckService

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
    marker_id: str = ""
    me_tracks: Dict[str, bool] = field(default_factory=dict)

    def __post_init__(self):
        if not self.description_original:
            self.description_original = self.description or ""
        if not self.description_ru and self.source_language == "ru":
            self.description_ru = self.description or ""


class CSVImporter:
    """Класс для импорта проблем из CSV"""
    
    def __init__(self, config: Optional[dict] = None, generate_fn: Optional[Callable[..., str]] = None):
        """
        generate_fn — вызов LLM для батч-проверки орфографии (см.
        SpellcheckService.correct_texts_batch); обычно ConclusionGenerator.
        _ollama_generate, чтобы проверка опечаток шла через тот же
        провайдер (Ollama/Groq/YandexGPT/GigaChat), что выбран в UI. None —
        только локальный алгоритм (pymorphy3/pyspellchecker).
        """
        self._spellcheck = SpellcheckService(config=config, generate_fn=generate_fn)
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
    
    def _iter_rows(self, csv_path: str):
        """Итератор (номер_строки, row) по строкам CSV с определением

        разделителя и кодировки — общая часть import_issues и scan_spelling.

        utf-8-sig прозрачно отбрасывает BOM, который Excel ставит в
        начало файла при экспорте «CSV UTF-8» — с обычным utf-8 BOM
        прилипает к первому заголовку ('﻿Timecode In'), колонка
        не находится, и все строки пропускаются как «нет таймкода».
        Для файлов без BOM ведёт себя как обычный utf-8.
        """
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            # Определяем разделитель: табуляция, точка с запятой
            # (стандартный разделитель русской локали Excel) или запятая.
            first_line = f.readline()
            f.seek(0)

            if '\t' in first_line:
                delimiter = '\t'
            elif ';' in first_line:
                delimiter = ';'
            else:
                delimiter = ','
            delimiter_names = {'\t': 'табуляция', ';': 'точка с запятой', ',': 'запятая'}
            logger.info(f"Разделитель CSV: {delimiter_names[delimiter]}")

            reader = csv.DictReader(f, delimiter=delimiter)

            # Логируем найденные колонки
            if reader.fieldnames:
                logger.info(f"Найдено колонок: {len(reader.fieldnames)}")
                logger.info(f"Названия колонок: {reader.fieldnames}")

            for row_count, row in enumerate(reader, 1):
                yield row_count, row

    def scan_spelling(self, csv_path: str) -> List[Dict]:
        """Предварительный скан CSV на опечатки — БЕЗ изменения данных.

        Возвращает список предложений для диалога ревью (см.
        src/spellcheck_review.py): [{"timecode", "field", "old", "new",
        "context"}, ...]. "context" — полный текст поля, где нашлась
        опечатка, нужен диалогу, чтобы показать слово не в отрыве, а внутри
        живого маркера. Ошибки скана не поднимаются — пустой список просто
        означает «показывать нечего», генерация продолжается без исправлений.
        """
        proposals: List[Dict] = []
        try:
            rows = []
            for _row_count, row in self._iter_rows(csv_path):
                timecode_in = self._get_column_value(row, 'Timecode In', 'TC IN', 'TC_IN')
                if not timecode_in:
                    continue
                fields = [
                    ("Описание", self._get_column_value(row, 'Description', 'ОПИСАНИЕ ПРОБЛЕМЫ', 'ОПИСАНИЕ')),
                    ("Комментарии", self._get_column_value(row, 'КОММЕНТАРИИ', 'COMMENTS')),
                ]
                rows.append((timecode_in, fields))

            # Один batch-запрос на весь маркер-лист (LLM либо локальный
            # алгоритм внутри correct_texts_batch) вместо отдельного вызова
            # на каждое поле — см. SpellcheckService.correct_texts_batch.
            texts = [text for _tc, fields in rows for _label, text in fields]
            corrections_by_text = self._spellcheck.correct_texts_batch(texts)

            for timecode_in, fields in rows:
                for field_label, text in fields:
                    for old, new in corrections_by_text.get(text, []):
                        proposals.append({
                            "timecode": timecode_in,
                            "field": field_label,
                            "old": old,
                            "new": new,
                            "context": text,
                        })
        except Exception as exc:
            logger.warning(f"Скан орфографии CSV не удался: {exc}")
            return []
        return proposals

    def import_issues(self, csv_path: str, approved_corrections=None) -> List[Issue]:
        """
        Импорт проблем из CSV файла
        Поддерживает английские и русские названия колонок

        Args:
            csv_path: Путь к CSV файлу
            approved_corrections: None — применять все уверенные исправления
                орфографии автоматически (прежнее поведение); иначе —
                множество пар (было, стало), одобренных пользователем в
                диалоге ревью (пустое множество = ничего не исправлять).

        Returns:
            Список проблем
        """
        issues = []

        try:
            logger.info(f"Импорт проблем из CSV: {csv_path}")

            rows = list(self._iter_rows(csv_path))
            total_rows = len(rows)
            spelling_fixes_count = 0

            # approved_corrections is None — «авто» режим (нет предварительного
            # скана/диалога, например прямой вызов в тестах): считаем batch'ем
            # уверенные исправления и применяем их все. Если множество передано
            # явно (даже пустое) — оно уже получено из scan_spelling + диалога
            # ревью, повторный вызов LLM не нужен, просто подставляем одобренные
            # пары (см. SpellcheckService.apply_pairs).
            auto_corrections: Dict[str, List[Tuple[str, str]]] = {}
            if approved_corrections is None:
                texts = []
                for _row_count, row in rows:
                    if not self._get_column_value(row, 'Timecode In', 'TC IN', 'TC_IN'):
                        continue
                    texts.append(self._get_column_value(row, 'Description', 'ОПИСАНИЕ ПРОБЛЕМЫ', 'ОПИСАНИЕ'))
                    texts.append(self._get_column_value(row, 'КОММЕНТАРИИ', 'COMMENTS'))
                auto_corrections = self._spellcheck.correct_texts_batch(texts)

            for row_count, row in rows:
                # Получаем таймкод (пробуем английский и русский варианты)
                timecode_in = self._get_column_value(row, 'Timecode In', 'TC IN', 'TC_IN')

                # Пропускаем пустые строки
                if not timecode_in:
                    logger.debug(f"Строка {row_count}: пропущена (нет таймкода)")
                    continue

                raw_description = self._get_column_value(row, 'Description', 'ОПИСАНИЕ ПРОБЛЕМЫ', 'ОПИСАНИЕ')
                raw_comments = self._get_column_value(row, 'КОММЕНТАРИИ', 'COMMENTS')

                # Проверка орфографии (RU/EN): автоисправление либо только
                # одобренные пользователем замены — без повторного скана.
                if approved_corrections is None:
                    description, description_fixes = SpellcheckService.apply_pairs(
                        raw_description, auto_corrections.get(raw_description, []))
                    comments, comments_fixes = SpellcheckService.apply_pairs(
                        raw_comments, auto_corrections.get(raw_comments, []))
                else:
                    description, description_fixes = SpellcheckService.apply_pairs(
                        raw_description, approved_corrections)
                    comments, comments_fixes = SpellcheckService.apply_pairs(
                        raw_comments, approved_corrections)

                for old, new in description_fixes + comments_fixes:
                    spelling_fixes_count += 1
                    logger.info(f"  Строка {row_count}: орфография '{old}' → '{new}'")

                from src.me_tracks import marker_track_values

                me_tracks = marker_track_values(row)
                issue = Issue(
                    timecode_in=timecode_in,
                    timecode_out=self._get_column_value(row, 'Timecode Out', 'TC OUT', 'TC_OUT'),
                    description=description,
                    description_original=raw_description,
                    audio_20_c=me_tracks['me_20'],
                    audio_20_uc=self._get_column_value(row, '2.0 UC') == '*',
                    audio_51_c=me_tracks['me_51'],
                    audio_51_uc=self._get_column_value(row, '5.1 UC') == '*',
                    blocker=self._get_column_value(row, 'БЛОКЕР', 'BLOCKER') == '*',
                    fix_required=self._get_column_value(row, 'ТРЕБУЕТ ИСПРАВЛЕНИЯ', 'FIX REQUIRED') == '*',
                    comment_required=self._get_column_value(row, 'ТРЕБУЕТ КОММЕНТАРИЯ', 'COMMENT REQUIRED') == '*',
                    me_tracks=me_tracks,
                    comments=comments,
                    marker_id=self._get_column_value(row, 'ID'),
                )

                issues.append(issue)
                logger.debug(f"Строка {row_count}: {timecode_in} - {issue.description[:30]}...")

            logger.info(f"✅ Импортировано {len(issues)} проблем из {total_rows} строк CSV")
            if spelling_fixes_count:
                logger.info(f"✅ Автоисправлено опечаток (орфография RU/EN): {spelling_fixes_count}")
            
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
