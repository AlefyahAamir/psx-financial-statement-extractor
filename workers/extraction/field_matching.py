
from __future__ import annotations

from .models import StatementRow
from .text_utils import normalize

def source_rank(row: StatementRow | dict) -> int:
    src = getattr(row, "source", None) if not isinstance(row, dict) else row.get("source")
    src = str(src or "")
    if "layout" in src:
        return 4
    if "period-column" in src or "pinned" in src:
        return 3
    if "cumulative" in src:
        return 2
    return 1

def contains_terms(norm_label: str, terms: list[str]) -> bool:
    return any(normalize(term) in norm_label for term in terms if normalize(term))

def reject_for_field(field: str, norm_label: str) -> bool:
    if field == "Taxation":
        return any(term in norm_label for term in ["profit", "loss", "before tax", "before taxation", "before levies"])
    if field == "Sales":
        return any(term in norm_label for term in ["sales tax", "tax collected", "gain on sale", "sale of", "proceeds"])
    if field == "CostOfSales":
        return any(term in norm_label for term in ["finance cost", "tax", "administrative", "distribution"])
    return False

def best_row(rows: list[StatementRow], field: str, synonyms: list[str]) -> StatementRow | None:
    best: tuple[int, StatementRow] | None = None
    wanted = [normalize(s) for s in synonyms]
    for row in rows:
        if not row.amounts or reject_for_field(field, row.norm):
            continue
        score = -1
        for term in wanted:
            if not term:
                continue
            if row.norm == term:
                score = max(score, 1000 + len(term))
            elif term in row.norm:
                score = max(score, 700 + len(term))
            else:
                tokens = [t for t in term.split() if len(t) > 2]
                if tokens and all(t in row.norm for t in tokens):
                    score = max(score, 400 + len(tokens))
        if score < 0:
            continue
        score += source_rank(row) * 50
        if row.norm.startswith("total "):
            score += 60
        if best is None or score > best[0]:
            best = (score, row)
    return best[1] if best else None
