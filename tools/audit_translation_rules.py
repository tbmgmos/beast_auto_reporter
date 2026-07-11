"""
Аудит покрытия rule-based переводчика английских маркеров.

Собирает все англоязычные описания маркеров из отчеты_learn, прогоняет через
MarkerTranslationService._translate_with_rules и группирует результаты:
- какие маркеры получили конкретный перевод,
- какие упали в грубые заглушки (generic fallback),
- какие остались на английском.

Запуск: ./venv/bin/python -m tools.audit_translation_rules [--show-generic]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.csv_importer import CSVImporter
from src.marker_translation_service import MarkerTranslationService
from tools.eval_subjective_conclusion import find_pairs, LEARN_ROOT

# Заглушки-обобщения, означающие «правило не распознало смысл»
GENERIC_FALLBACKS = {
    "обнаружен звуковой дефект на фрагменте",
    "присутствуют дефекты в реплике",
    "присутствуют дефекты в музыке",
    "обнаружен дефект звуковой дорожки",
    "отсутствует звуковой элемент",
    "уровень звука требует корректировки",
    "присутствуют посторонние шумы",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-generic", action="store_true", help="показать маркеры, упавшие в заглушки")
    parser.add_argument("--show-english", action="store_true", help="показать маркеры, оставшиеся на английском")
    args = parser.parse_args()

    logging.disable(logging.WARNING)
    importer = CSVImporter()
    service = MarkerTranslationService()

    english_markers: Counter = Counter()
    for pair in find_pairs(LEARN_ROOT):
        try:
            issues = importer.import_issues(str(pair["csv"]))
        except Exception:
            continue
        for issue in issues:
            text = (issue.description or "").strip()
            if text and service.detect_language(text) == "en":
                english_markers[text] += 1

    print(f"уникальных английских маркеров: {len(english_markers)} (всего вхождений: {sum(english_markers.values())})")

    specific, generic, untranslated = [], [], []
    for text, count in english_markers.items():
        translation = service._translate_with_rules(text)
        if not translation or re.search(r"[A-Za-z]{3}", translation):
            untranslated.append((count, text, translation))
        elif translation in GENERIC_FALLBACKS:
            generic.append((count, text, translation))
        else:
            specific.append((count, text, translation))

    n = sum(english_markers.values())
    s = sum(c for c, *_ in specific)
    g = sum(c for c, *_ in generic)
    u = sum(c for c, *_ in untranslated)
    print(f"конкретный перевод: {len(specific)} уник. ({s}/{n} вхождений, {s/n:.0%})")
    print(f"заглушка:           {len(generic)} уник. ({g}/{n} вхождений, {g/n:.0%})")
    print(f"остался английский: {len(untranslated)} уник. ({u}/{n} вхождений, {u/n:.0%})")

    if args.show_generic:
        print("\n=== ЗАГЛУШКИ (по частоте) ===")
        for count, text, translation in sorted(generic, reverse=True):
            print(f"  {count:2d}x {text[:90]}")
            print(f"      → {translation}")

    if args.show_english:
        print("\n=== ОСТАЛСЯ АНГЛИЙСКИЙ ===")
        for count, text, translation in sorted(untranslated, reverse=True):
            print(f"  {count:2d}x {text[:90]}")
            print(f"      → {(translation or '<пусто>')[:90]}")


if __name__ == "__main__":
    main()
