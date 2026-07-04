
from __future__ import annotations

import re
from typing import Optional

_NUMBER_RE = re.compile(r"\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\d{5,}(?:\.\d+)?\)?")

def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()

def normalize(value: str) -> str:
    text = clean_text(value).lower()
    text = (
        text.replace("\ufb01", "fi").replace("\ufb02", "fl")
            .replace("\ufb03", "ffi").replace("\ufb04", "ffl")
            .replace("&", " and ")
    )
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    text = text.replace("un appropriated", "unappropriated")
    text = text.replace("paid up", "paidup")
    return text

def parse_number(token: str) -> Optional[int]:
    token = clean_text(token)
    if not token or token in {"-", "—", "--"}:
        return None
    negative = token.startswith("-") or (token.startswith("(") and token.endswith(")"))
    token = token.replace("(", "").replace(")", "").replace(",", "").replace(" ", "")
    token = token.replace("−", "-").replace("–", "-").replace("—", "-").lstrip("+")
    if token in {"", "-"}:
        return None
    try:
        value = int(round(float(token)))
    except ValueError:
        return None
    return -abs(value) if negative else value

def amount_candidates(line: str) -> list[int]:
    values: list[int] = []
    for match in _NUMBER_RE.finditer(line or ""):
        parsed = parse_number(match.group(0))
        if parsed is None:
            continue
        abs_value = abs(parsed)
        # Exclude standalone years and small note/page numbers.
        if 1990 <= abs_value <= 2040:
            continue
        if abs_value <= 200 and "," not in match.group(0):
            continue
        values.append(parsed)
    return values

def looks_like_amount_line(line: str) -> bool:
    return bool(amount_candidates(line))
