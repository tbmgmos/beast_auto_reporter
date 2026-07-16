"""Общие функции нечёткого сравнения строк (имена файлов, папок)."""


def _levenshtein(a: str, b: str) -> int:
    """Расстояние Левенштейна (число правок для превращения a в b)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _bases_match(a: str, b: str, min_len_for_fuzzy: int = 10, max_typo_distance: int = 2) -> bool:
    """Считает две базы «одним материалом»: точное совпадение, либо

    небольшая опечатка (расстояние Левенштейна <= 2) — но только для
    достаточно длинных имён. На коротких именах (< 10 симв.) даже один
    отличающийся символ обычно означает *другой* материал (например,
    "movie_a" / "movie_b"), поэтому там разрешено только точное совпадение.
    """
    if a == b:
        return True
    if min(len(a), len(b)) < min_len_for_fuzzy:
        return False
    return _levenshtein(a, b) <= max_typo_distance
