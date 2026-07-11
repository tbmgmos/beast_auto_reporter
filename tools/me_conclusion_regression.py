#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.conclusion_generator import ConclusionGenerator
from src.csv_importer import CSVImporter, Issue


DEFAULT_ROOT = Path("/Users/vlad/Desktop/отчеты_learn")
DEFAULT_EXCLUDE = ("араб",)


@dataclass
class ReportCase:
    directory: Path
    csv_path: Path
    report_type: str


def infer_report_type(name: str) -> str:
    lower = name.lower()
    if any(token in lower for token in ["m&e", "mn&e", "mne", "_me_", " me_", "_me.", " me.", "ме", "m&e_"]):
        return "me"
    return "main"


def iter_cases(root: Path, exclude_substrings: Iterable[str]) -> list[ReportCase]:
    excluded = tuple(part.lower() for part in exclude_substrings)
    cases: list[ReportCase] = []

    for directory in sorted(path for path in root.rglob("*") if path.is_dir()):
        if any(token in str(directory).lower() for token in excluded):
            continue

        csv_files = sorted(
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() == ".csv"
            and "explicit_words" not in path.name.lower()
            and "pyloudnorm_analysis" not in str(path).lower()
        )
        if not csv_files:
            continue

        csv_path = csv_files[0]
        report_type = infer_report_type(f"{directory.name} {csv_path.name}")
        cases.append(ReportCase(directory=directory, csv_path=csv_path, report_type=report_type))

    return cases


def render_items(conclusion: str) -> list[str]:
    return [line.strip() for line in conclusion.splitlines() if line.strip().startswith("-")]


def filter_issues(issues: list[Issue], needle: str) -> list[Issue]:
    if not needle:
        return issues
    lowered = needle.lower()
    return [
        issue for issue in issues
        if lowered in issue.description.lower() or lowered in issue.timecode_in.lower()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Быстрый regression-runner для субъективных M&E заключений по CSV из отчеты_learn."
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Корневая папка с отчётами learn")
    parser.add_argument("--report-type", default="me", choices=["me", "main", "all"], help="Какие отчёты брать")
    parser.add_argument("--contains", default="", help="Фильтр по подпути/имени файла")
    parser.add_argument("--issue-contains", default="", help="Фильтр по описанию маркера или таймкоду")
    parser.add_argument("--limit", type=int, default=10, help="Сколько кейсов показать")
    parser.add_argument("--show-issues", action="store_true", help="Показывать подходящие CSV-маркеры")
    parser.add_argument("--show-groups", action="store_true", help="Показывать внутренние группы генератора")
    parser.add_argument("--include-empty", action="store_true", help="Не пропускать кейсы с пустым CSV")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"Root folder not found: {root}")

    importer = CSVImporter()
    generator = ConclusionGenerator(use_llm=False)

    cases = iter_cases(root, DEFAULT_EXCLUDE)
    contains = args.contains.lower().strip()
    issue_contains = args.issue_contains.strip()

    shown = 0
    for case in cases:
        if args.report_type != "all" and case.report_type != args.report_type:
            continue
        if contains and contains not in str(case.directory).lower() and contains not in case.csv_path.name.lower():
            continue

        issues = importer.import_issues(str(case.csv_path))
        if not issues and not args.include_empty:
            continue
        matched_issues = filter_issues(issues, issue_contains)
        if issue_contains and not matched_issues:
            continue

        conclusion = generator.generate_subjective_conclusion(issues, case.report_type)
        items = render_items(conclusion)

        print("=" * 100)
        print(f"DIR: {case.directory}")
        print(f"CSV: {case.csv_path}")
        print(f"TYPE: {case.report_type} | ISSUES: {len(issues)} | MATCHED: {len(matched_issues) if issue_contains else len(issues)}")

        if args.show_groups:
            blockers = [issue for issue in issues if issue.blocker]
            regular_issues = [issue for issue in issues if not issue.blocker]
            groups = generator._smart_group_issues(regular_issues, case.report_type)
            if case.report_type in ("me", "me_ours"):
                groups = generator._merge_me_context_groups(groups)
                groups = generator._normalize_me_groups_for_conclusion(groups)
            else:
                blockers, groups = generator._merge_main_blockers_into_groups(blockers, groups, case.report_type)

            print("GROUPS:")
            for group_type, group_items in groups.items():
                print(f"  - {group_type}: {len(group_items)}")
            if blockers:
                print(f"  - blockers: {len(blockers)}")

        if args.show_issues:
            print("ISSUES:")
            for issue in (matched_issues if issue_contains else issues):
                print(f"  - {issue.timecode_in} | {issue.description}")

        print("CONCLUSION:")
        for item in items:
            print(f"  {item}")

        shown += 1
        if shown >= args.limit:
            break

    if shown == 0:
        print("No matching cases found.")


if __name__ == "__main__":
    main()
