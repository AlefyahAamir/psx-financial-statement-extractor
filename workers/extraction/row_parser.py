
from __future__ import annotations

import re
from typing import Any

from .models import StatementRow
from .text_utils import amount_candidates, clean_text, normalize

PAGE_RE = re.compile(r"\n---\s*page\s+(\d+)\s*---\n", re.I)

def split_pages(text: str) -> list[tuple[int, str]]:
    matches = list(PAGE_RE.finditer(text or ""))
    if not matches:
        return [(0, text or "")]
    pages: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        page_no = int(match.group(1))
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pages.append((page_no, text[match.end():next_start]))
    return pages

def detect_amount_multiplier(text: str) -> int:
    head = normalize((text or "")[:20000])
    if "rupees in million" in head or "rs in million" in head or "amounts in million" in head:
        return 1_000_000
    if (
        "rupees in 000" in head
        or "rs in 000" in head
        or "rupees in thousand" in head
        or "amounts in thousand" in head
        or "nearest thousand" in head
    ):
        return 1_000
    return 1

def classify_section(page_text: str) -> str:
    ntext = normalize(page_text[:4000])
    if "cash flow" in ntext:
        return "cash_flow"
    if "profit or loss" in ntext or "income statement" in ntext or "statement of comprehensive income" in ntext:
        return "profit_loss"
    if "financial position" in ntext or "balance sheet" in ntext:
        return "financial_position"
    return "unknown"

def parse_rows_from_text(text: str, default_multiplier: int) -> list[StatementRow]:
    rows: list[StatementRow] = []
    for page_no, page_text in split_pages(text or ""):
        section = classify_section(page_text)
        lines = [clean_text(line) for line in page_text.splitlines() if clean_text(line)]
        for line in lines:
            amounts = amount_candidates(line)
            if not amounts:
                continue
            label = line
            for amount in amounts:
                label = label.replace(f"{amount:,}", " ")
                label = label.replace(str(amount), " ")
            norm_label = normalize(label)
            if not norm_label or len(norm_label) < 3:
                continue
            rows.append(
                StatementRow(
                    label=clean_text(label),
                    norm=norm_label,
                    amounts=amounts,
                    page=page_no,
                    section=section,
                    raw=line,
                    multiplier=default_multiplier,
                )
            )
    return rows

def rows_by_section(rows: list[StatementRow]) -> dict[str, list[StatementRow]]:
    grouped = {"financial_position": [], "profit_loss": [], "cash_flow": [], "unknown": []}
    for row in rows:
        grouped.setdefault(row.section or "unknown", []).append(row)
    return grouped
