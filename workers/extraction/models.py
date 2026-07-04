
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass
class StatementRow:
    label: str
    norm: str
    amounts: list[int]
    page: int = 0
    section: str = ""
    source: str = "text"
    raw: str = ""
    multiplier: int = 1

@dataclass
class ExtractionContext:
    report: dict[str, Any]
    year: int
    multiplier: int
    evidence: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
