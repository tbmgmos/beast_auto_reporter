"""
Eval-харнесс для generate_subjective_conclusion.

Для каждой пары (CSV маркеры ↔ DOCX с ручным заключением) из отчеты_learn:
1. Загружает маркеры боевым CSVImporter.
2. Генерирует заключение ConclusionGenerator(use_llm=False) — детерминированный Python-путь.
3. Извлекает человеческое заключение из DOCX.
4. Сопоставляет пункты (по таймкоду, затем fuzzy по тексту) и считает метрики.

Запуск:
    ./venv/bin/python -m tools.eval_subjective_conclusion [--limit N] [--type me|main] [--verbose]
    ./venv/bin/python -m tools.eval_subjective_conclusion --dump-mismatches  # полный JSON с расхождениями
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.conclusion_generator import ConclusionGenerator
from src.csv_importer import CSVImporter

LEARN_ROOT = Path("/Users/vlad/Desktop/отчеты/отчеты_learn")
OUTPUT_JSON = Path(__file__).resolve().parent / "eval_subjective_results.json"

SUBJECTIVE_RE = re.compile(r"По субъективной оценке")
HEADER_RE = re.compile(
    r"По субъективной оценке\s*(?:выявлены следующие недоч[её]ты|стоит обратить внимание на)?\s*:?"
)
NO_ISSUES_RE = re.compile(r"По субъективной оценке\s+нареканий\s+не\s+(?:обнаружено|выявлено)")
TC_RE = re.compile(r"\b(\d{2}:\d{2}:\d{2}:\d{2})\b")
# Маркеры конца блока заключения внутри склеенного текста docx
END_MARKERS = [
    "По технической оценке",
    "По техническим характеристикам",
    "MARKER LIST",
    "МАРКЕР ЛИСТ",
    "МАРКЕР-ЛИСТ",
    "Timecode In",
    "TC IN",
    "ОБЪЕКТИВНАЯ ОЦЕНКА",
    "ДАТА ОТЧЕТА",
    "ДАТА ОТЧЁТА",
]


def infer_report_type(name: str) -> str:
    lower = name.lower()
    if any(token in lower for token in ["m&e", "mn&e", "mne", "_me_", " me_", "_me.", " me.", "m&e_", "_me ", "me_stem", "_мэ", "мэ_"]):
        return "me"
    return "main"


# ---------------------------------------------------------------------------
# Извлечение человеческого заключения из DOCX
# ---------------------------------------------------------------------------

def iter_docx_paragraphs(docx_path: Path):
    """
    Обходит ВСЕ параграфы документа в порядке следования, включая вложенные
    таблицы (doc.tables не рекурсивен — заключение часто лежит в таблице
    внутри таблицы, и стандартный обход его не видит).
    """
    doc = Document(str(docx_path))
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for p in doc.element.body.iter(f"{ns}p"):
        chunks = []
        for node in p.iter():
            if node.tag == f"{ns}t" and node.text:
                chunks.append(node.text)
            elif node.tag in (f"{ns}br", f"{ns}cr"):
                chunks.append("\n")
        text = "".join(chunks).strip()
        if not text:
            continue
        # Внутри одного w:p переносы w:br разделяют смысловые строки
        for line in text.split("\n"):
            line = line.strip()
            if line:
                yield line


def extract_human_conclusion(docx_path: Path) -> dict:
    """
    Возвращает {"found": bool, "no_issues": bool, "items": [str, ...]}.

    Работает по полному тексту документа: в части отчётов вся страница
    сведена в один w:p, где пункты заключения — отдельные run'ы без
    разделителей. Такие пункты режем эвристикой «строчная буква вплотную
    к заглавной» и по началу «На таймкоде».
    """
    full = "\n".join(iter_docx_paragraphs(docx_path))

    m = SUBJECTIVE_RE.search(full)
    if not m:
        return {"found": False, "no_issues": False, "items": []}
    if NO_ISSUES_RE.match(full, m.start()):
        return {"found": True, "no_issues": True, "items": []}

    header_match = HEADER_RE.match(full, m.start())
    tail = full[header_match.end() if header_match else m.end():]

    # Обрезаем по первому маркеру конца блока
    end_pos = len(tail)
    for marker in END_MARKERS:
        pos = tail.find(marker)
        if 0 <= pos < end_pos:
            end_pos = pos
    tail = tail[:end_pos].strip(" :\n\t")

    return {"found": True, "no_issues": False, "items": split_items(tail)}


def split_items(text: str) -> list[str]:
    """
    Разбивает текст заключения на пункты:
    1) по переносам строк и ведущим дефисам,
    2) по границе «строчная буква вплотную к заглавной» (склеенные run'ы),
    3) по началу нового «На таймкоде …».
    """
    parts = re.split(r"(?:^|\n)\s*[-–—]\s+|\n", text)

    result: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # склеенные run'ы: «…щёлкающие звукиВ новой версии…»
        subparts = re.split(r"(?<=[а-яёa-z0-9)»\"”])(?=[А-ЯЁ])", part)
        for sub in subparts:
            # отдельные «На таймкоде» внутри одного куска
            chunks = re.split(r"(?<!^)(?=На таймкоде )", sub)
            for chunk in chunks:
                chunk = re.sub(r"\s+", " ", chunk).strip(" -–—\t.,")
                if chunk and len(chunk) > 3:
                    result.append(chunk)
    return result


# ---------------------------------------------------------------------------
# Нормализация и сопоставление пунктов
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = TC_RE.sub(" ", text)
    text = re.sub(r"на таймкоде|таймкод[а-я]*", " ", text)
    text = re.sub(r"[^\wа-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def item_timecodes(text: str) -> set[str]:
    return set(TC_RE.findall(text))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


DEFECT_CATEGORIES: dict[str, str] = {
    "шипение": r"шипени|сибил|hiss",
    "щелчки": r"щелч|щёлк|клик|слюн",
    "вч_призвуки": r"высокочаст|свист|призвук",
    "разборчивость": r"разборчив",
    "атмосфера": r"атмосфер|фонов\w+ шум|звучание фона|скач[ое]к фона",
    "синхронные_шумы": r"синхронн\w+ шум|шаг(?:и|ов)|foley|синхрон\w* не хватает",
    "несинхрон": r"несинхрон|рассинхрон|не синхронн",
    "опциональный_трек": r"опциональн",
    "гур": r"гур",
    "дыхание": r"дыхан|вздох|выдох",
    "перевод_титры": r"перевод|титр|субтитр",
    "стерео_формат": r"стерео",
    "уровень": r"громкост|уровн|тише|громче",
    "обрыв": r"обрыв|заканчива|окончани",
    "реплики_пропали": r"пропал\w+ реплик|реплик\w+ пропал|отсутств\w+ реплик|не хватает реплик",
    "чистка_реплик": r"чистк|artefact|артефакт|вычищени",
    "посторонние_звуки": r"посторонн\w+ (?:звук|шум)",
    "маскировка": r"маскировк|нецензурн|мат\b",
}


def defect_categories(text: str) -> set[str]:
    lowered = text.lower().replace("ё", "е")
    return {name for name, pattern in DEFECT_CATEGORIES.items() if re.search(pattern, lowered)}


@dataclass
class MatchResult:
    matched: list[dict] = field(default_factory=list)      # (human, generated, score)
    missed_human: list[str] = field(default_factory=list)  # пункты человека без пары
    extra_generated: list[str] = field(default_factory=list)


def match_items(human_items: list[str], generated_items: list[str], threshold: float = 0.45) -> MatchResult:
    """
    Жадное сопоставление: сначала пары с общим таймкодом, затем лучшие fuzzy-пары.
    threshold подобран мягким: формулировки различаются, важно смысловое покрытие.
    """
    result = MatchResult()
    unused_gen = list(range(len(generated_items)))
    unused_hum = list(range(len(human_items)))

    # Скоринг всех пар: текстовая близость + общий таймкод + общая категория дефекта
    hum_cats = [defect_categories(h) for h in human_items]
    gen_cats = [defect_categories(g) for g in generated_items]
    scored: list[tuple[float, int, int]] = []
    for hi in unused_hum:
        for gi in unused_gen:
            score = similarity(human_items[hi], generated_items[gi])
            shared_tc = item_timecodes(human_items[hi]) & item_timecodes(generated_items[gi])
            if shared_tc:
                score += 0.35
            if hum_cats[hi] & gen_cats[gi]:
                score += 0.30
            scored.append((score, hi, gi))
    scored.sort(reverse=True)

    used_h: set[int] = set()
    used_g: set[int] = set()
    for score, hi, gi in scored:
        if score < threshold or hi in used_h or gi in used_g:
            continue
        used_h.add(hi)
        used_g.add(gi)
        result.matched.append({
            "human": human_items[hi],
            "generated": generated_items[gi],
            "score": round(score, 3),
        })

    result.missed_human = [human_items[i] for i in range(len(human_items)) if i not in used_h]
    result.extra_generated = [generated_items[i] for i in range(len(generated_items)) if i not in used_g]
    return result


# ---------------------------------------------------------------------------
# Основной прогон
# ---------------------------------------------------------------------------

def find_pairs(root: Path) -> list[dict]:
    pairs = []
    for directory in sorted(p for p in root.rglob("*") if p.is_dir()):
        files = [p for p in directory.iterdir() if p.is_file()]
        csv_files = [p for p in files if p.suffix.lower() == ".csv" and "explicit_words" not in p.name.lower() and "content_check" not in p.name.lower()]
        docx_files = [p for p in files if p.suffix.lower() == ".docx" and not p.name.startswith("~$")]
        if not csv_files or not docx_files:
            continue
        name_blob = directory.name + " " + csv_files[0].name + " " + docx_files[0].name
        pairs.append({
            "directory": directory,
            "csv": sorted(csv_files)[0],
            "docx": sorted(docx_files)[0],
            "report_type": infer_report_type(name_blob),
        })
    return pairs


def generated_conclusion_items(conclusion: str) -> list[str]:
    """Извлекает пункты из сгенерированного заключения."""
    lines = conclusion.splitlines()
    items = []
    for line in lines:
        line = line.strip()
        if not line or HEADER_RE.search(line) or NO_ISSUES_RE.search(line):
            continue
        line = re.sub(r"^[-–—]\s*", "", line).strip()
        if line:
            items.append(line)
    return items


RECHECK_RE = re.compile(
    r"замечани[яй][^.]{0,40}исправлен|исправлени[йя] пока не было|"
    r"почти все замечания|были попытки исправлen|попытк[аи] исправлени"
)


def is_recheck_conclusion(items: list[str]) -> bool:
    """
    Отчёт-перепроверка: человек сравнивает с предыдущей версией фонограммы
    («все замечания были исправлены»). Такой вердикт невозможно вывести из
    CSV-маркеров — оцениваем эти отчёты отдельно.
    """
    return any(RECHECK_RE.search(item.lower()) for item in items)


def run_eval(limit: int | None = None, only_type: str | None = None, verbose: bool = False, root: Path = LEARN_ROOT) -> dict:
    logging.disable(logging.WARNING)  # приглушаем болтливые логи боевого кода

    importer = CSVImporter()
    generator = ConclusionGenerator(use_llm=False)

    pairs = find_pairs(root)
    if only_type:
        pairs = [p for p in pairs if p["report_type"] == only_type]
    if limit:
        pairs = pairs[:limit]

    reports = []
    totals = {"human_items": 0, "matched": 0, "missed": 0, "extra": 0, "pairs_ok": 0, "pairs_failed": 0, "no_conclusion": 0}

    for pair in pairs:
        entry = {
            "directory": str(pair["directory"].relative_to(root)),
            "report_type": pair["report_type"],
        }
        try:
            human = extract_human_conclusion(pair["docx"])
            if not human["found"]:
                totals["no_conclusion"] += 1
                entry["skipped"] = "нет субъективного заключения в docx"
                reports.append(entry)
                continue

            issues = importer.import_issues(str(pair["csv"]))
            generated = generator.generate_subjective_conclusion(issues, pair["report_type"])
            gen_items = generated_conclusion_items(generated)

            match = match_items(human["items"], gen_items)
            entry["recheck"] = is_recheck_conclusion(human["items"])
            entry.update({
                "human_count": len(human["items"]),
                "generated_count": len(gen_items),
                "matched": len(match.matched),
                "missed_human": match.missed_human,
                "extra_generated": match.extra_generated,
                "matches": match.matched,
                "recall": round(len(match.matched) / len(human["items"]), 3) if human["items"] else 1.0,
                "precision": round(len(match.matched) / len(gen_items), 3) if gen_items else (1.0 if not human["items"] else 0.0),
            })
            totals["human_items"] += len(human["items"])
            totals["matched"] += len(match.matched)
            totals["missed"] += len(match.missed_human)
            totals["extra"] += len(match.extra_generated)
            totals["pairs_ok"] += 1
        except Exception as exc:
            totals["pairs_failed"] += 1
            entry["error"] = f"{type(exc).__name__}: {exc}"
        reports.append(entry)

        if verbose and "recall" in entry:
            print(f"[{entry['report_type']}] {entry['directory']}: recall={entry['recall']} precision={entry['precision']} (h={entry['human_count']} g={entry['generated_count']})")

    logging.disable(logging.NOTSET)

    evaluated = [r for r in reports if "recall" in r]

    def build_summary(rs: list[dict]) -> dict:
        human = sum(r["human_count"] for r in rs)
        matched = sum(r["matched"] for r in rs)
        extra = sum(len(r["extra_generated"]) for r in rs)
        return {
            "pairs": len(rs),
            "micro_recall": round(matched / human, 3) if human else None,
            "micro_precision": round(matched / (matched + extra), 3) if (matched + extra) else None,
            "macro_recall": round(sum(r["recall"] for r in rs) / len(rs), 3) if rs else None,
            "macro_precision": round(sum(r["precision"] for r in rs) / len(rs), 3) if rs else None,
            "human_items": human,
            "matched": matched,
            "missed": human - matched,
            "extra": extra,
        }

    core = [r for r in evaluated if not r.get("recheck")]
    recheck = [r for r in evaluated if r.get("recheck")]
    summary = {
        "pairs_total": len(pairs),
        "pairs_evaluated": totals["pairs_ok"],
        "pairs_failed": totals["pairs_failed"],
        "pairs_no_conclusion": totals["no_conclusion"],
        "core": build_summary(core),
        "recheck": build_summary(recheck),
    }
    return {"summary": summary, "reports": reports}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--type", choices=["me", "main"], default=None)
    parser.add_argument("--root", type=Path, default=LEARN_ROOT, help="корень с парами CSV↔DOCX")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dump-mismatches", action="store_true", help="печатать все missed/extra пункты")
    args = parser.parse_args()

    result = run_eval(limit=args.limit, only_type=args.type, verbose=args.verbose, root=args.root)
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    s = result["summary"]
    print("\n=== SUMMARY ===")
    for key in ("pairs_total", "pairs_evaluated", "pairs_failed", "pairs_no_conclusion"):
        print(f"{key}: {s[key]}")
    for scope in ("core", "recheck"):
        print(f"[{scope}]")
        for key, value in s[scope].items():
            print(f"  {key}: {value}")
    print(f"json={OUTPUT_JSON}")

    if args.dump_mismatches:
        print("\n=== MISSED (человек написал, мы — нет) ===")
        for r in result["reports"]:
            for item in r.get("missed_human", []):
                print(f"[{r['report_type']}] {r['directory']}\n    {item}")
        print("\n=== EXTRA (мы написали, человек — нет) ===")
        for r in result["reports"]:
            for item in r.get("extra_generated", []):
                print(f"[{r['report_type']}] {r['directory']}\n    {item}")


if __name__ == "__main__":
    main()
