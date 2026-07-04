
from __future__ import annotations

from typing import Any

from .text_utils import normalize

def row_norm(row: Any) -> str:
    if isinstance(row, dict):
        return str(row.get("norm") or normalize(row.get("label") or ""))
    return str(getattr(row, "norm", "") or normalize(getattr(row, "label", "")))

def row_amounts(row: Any) -> list[int]:
    if isinstance(row, dict):
        return list(row.get("amounts") or [])
    return list(getattr(row, "amounts", []) or [])

def row_index(rows: list[Any], row: Any | None) -> int | None:
    if row is None:
        return None
    for index, candidate in enumerate(rows):
        if candidate is row:
            return index
    return None

def source_rank(row: Any) -> int:
    source = str(row.get("source") if isinstance(row, dict) else getattr(row, "source", "") or "")
    if "layout" in source:
        return 4
    if "period-column" in source or "pinned" in source:
        return 3
    if "cumulative" in source:
        return 2
    return 1

def is_taxation_candidate(row: Any) -> bool:
    label = row_norm(row)
    if not label or not row_amounts(row):
        return False
    if any(term in label for term in ["profit", "loss", "before tax", "before taxation", "before levies"]):
        return False
    return any(term in label for term in [
        "taxation", "income tax", "tax expense", "current tax", "deferred tax",
        "minimum tax", "final taxes", "levy",
    ])

def taxation_score(row: Any) -> int:
    if not is_taxation_candidate(row):
        return -1_000_000
    label = row_norm(row)
    score = source_rank(row) * 100
    if label in {"taxation", "income tax expense", "tax expense"}:
        score += 2000
    if "current and deferred tax" in label or "current tax" in label or "deferred tax" in label:
        score += 300
    if any(term in label for term in ["minimum tax", "final taxes", "levy and taxation", "levies and taxation"]):
        score -= 250
    return score

def is_profit_before_tax_candidate(row: Any) -> bool:
    label = row_norm(row)
    if not label or not row_amounts(row):
        return False
    has_profit_word = any(term in label for term in ["profit", "loss", "earnings"])
    has_tax_word = any(term in label for term in ["before tax", "before taxation", "before income tax"])
    return has_profit_word and has_tax_word

def profit_before_tax_score(row: Any, rows: list[Any], tax_row: Any | None) -> int:
    if not is_profit_before_tax_candidate(row):
        return -1_000_000
    label = row_norm(row)
    score = source_rank(row) * 100
    tax_pos = row_index(rows, tax_row)
    pbt_pos = row_index(rows, row)
    if tax_pos is not None and pbt_pos is not None:
        if pbt_pos < tax_pos:
            score += 3000 + max(0, 500 - (tax_pos - pbt_pos) * 20)
        else:
            score -= 1000
    if any(term in label for term in ["loss profit before taxation", "loss before taxation", "profit before taxation", "profit before income tax"]):
        score += 600
    if any(term in label for term in ["before levies", "before levy", "minimum tax", "final taxes"]):
        score -= 900
    return score

def select_taxation_row(rows: list[Any]) -> Any | None:
    candidates = sorted(rows, key=taxation_score, reverse=True)
    return candidates[0] if candidates and taxation_score(candidates[0]) > -1_000_000 else None

def select_profit_before_tax_row(rows: list[Any], tax_row: Any | None = None) -> Any | None:
    candidates = sorted(rows, key=lambda row: profit_before_tax_score(row, rows, tax_row), reverse=True)
    return candidates[0] if candidates and profit_before_tax_score(candidates[0], rows, tax_row) > -1_000_000 else None

def select_taxation_and_profit_before_tax_rows(rows: list[Any]) -> tuple[Any | None, Any | None]:
    tax_row = select_taxation_row(rows)
    pbt_row = select_profit_before_tax_row(rows, tax_row)
    return tax_row, pbt_row
