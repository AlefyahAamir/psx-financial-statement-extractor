from __future__ import annotations

"""
Shared arithmetic sanity checks for PSX extraction.

These checks are intentionally conservative. They are used by both:
- workers/psx_worker.py routing logic
- tools/pdf_audit_client_50.py automated audit

Purpose:
A value merely appearing somewhere in the PDF is not enough. These checks flag
internally inconsistent extracted values that often indicate wrong column, wrong
period, gross/net revenue mismatch, or false-positive label matching.
"""

import re
from typing import Any, Dict, List, Optional


def parse_numeric(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
    s = s.replace(",", "").replace("Rs.", "").replace("Rs", "").strip()
    s = s.strip("()")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        val = float(m.group(0))
        return -abs(val) if neg else val
    except Exception:
        return None


def _available(values: Dict[str, Any], *fields: str) -> bool:
    return all(parse_numeric(values.get(f)) is not None for f in fields)


def _tol(reference: float, pct: float = 0.005, floor: float = 1000.0) -> float:
    return max(floor, abs(reference) * pct)


def _result(name: str, fields: List[str], passed: bool, expected: Optional[float], actual: Optional[float], message: str, severity: str = "hard") -> Dict[str, Any]:
    return {
        "name": name,
        "fields": fields,
        "passed": bool(passed),
        "expected": expected,
        "actual": actual,
        "message": message,
        "severity": severity,
    }


def arithmetic_sanity_report(values: Dict[str, Any]) -> Dict[str, Any]:
    """Return per-check arithmetic sanity results.

    Checks are skipped if required fields are missing. A skipped check is not a
    failure because many reports do not disclose every field.
    """
    values = values or {}
    checks: Dict[str, Dict[str, Any]] = {}

    # 1. P&L gross profit triad
    if _available(values, "Sales", "CostOfSales", "GrossProfit"):
        sales = parse_numeric(values.get("Sales")) or 0.0
        cost = parse_numeric(values.get("CostOfSales")) or 0.0
        gp = parse_numeric(values.get("GrossProfit")) or 0.0
        expected = sales - abs(cost)
        passed = abs(expected - gp) <= _tol(sales)
        checks["pl_gross_profit"] = _result(
            "P/L Gross Profit",
            ["Sales", "CostOfSales", "GrossProfit"],
            passed,
            expected,
            gp,
            f"Sales - abs(CostOfSales) = {expected:.0f}; extracted GrossProfit = {gp:.0f}",
        )

    # 2. Working capital triad
    if _available(values, "CurrentAssets", "CurrentLiabilities", "WorkingCapital"):
        ca = parse_numeric(values.get("CurrentAssets")) or 0.0
        cl = parse_numeric(values.get("CurrentLiabilities")) or 0.0
        wc = parse_numeric(values.get("WorkingCapital")) or 0.0
        expected = ca - abs(cl)
        passed = abs(expected - wc) <= _tol(ca)
        checks["working_capital"] = _result(
            "Working Capital",
            ["CurrentAssets", "CurrentLiabilities", "WorkingCapital"],
            passed,
            expected,
            wc,
            f"CurrentAssets - abs(CurrentLiabilities) = {expected:.0f}; extracted WorkingCapital = {wc:.0f}",
        )

    # 3. Profit after tax triad
    if _available(values, "ProfitBeforeTax", "Taxation", "ProfitAfterTax"):
        pbt = parse_numeric(values.get("ProfitBeforeTax")) or 0.0
        tax = parse_numeric(values.get("Taxation")) or 0.0
        pat = parse_numeric(values.get("ProfitAfterTax")) or 0.0
        expected = pbt - abs(tax)
        passed = abs(expected - pat) <= _tol(pbt, pct=0.01)
        checks["profit_after_tax"] = _result(
            "Profit After Tax",
            ["ProfitBeforeTax", "Taxation", "ProfitAfterTax"],
            passed,
            expected,
            pat,
            f"ProfitBeforeTax - abs(Taxation) = {expected:.0f}; extracted ProfitAfterTax = {pat:.0f}",
        )

    # 4. Rough Profit Before Tax walk-down
    pbt_fields = ["Sales", "CostOfSales", "OperatingExpenses", "FinanceCosts", "OtherIncome", "OtherCharges", "ProfitBeforeTax"]
    if _available(values, *pbt_fields):
        sales = parse_numeric(values.get("Sales")) or 0.0
        cost = abs(parse_numeric(values.get("CostOfSales")) or 0.0)
        opex = abs(parse_numeric(values.get("OperatingExpenses")) or 0.0)
        fin = abs(parse_numeric(values.get("FinanceCosts")) or 0.0)
        other_income = parse_numeric(values.get("OtherIncome")) or 0.0
        other_charges = abs(parse_numeric(values.get("OtherCharges")) or 0.0)
        pbt = parse_numeric(values.get("ProfitBeforeTax")) or 0.0
        expected = sales - cost - opex - fin + other_income - other_charges
        # Looser: companies may include associates, exchange gains, share of profits, etc.
        passed = abs(expected - pbt) <= _tol(sales, pct=0.05, floor=50000.0)
        checks["rough_profit_before_tax"] = _result(
            "Rough Profit Before Tax",
            pbt_fields,
            passed,
            expected,
            pbt,
            f"Sales - CostOfSales - OperatingExpenses - FinanceCosts + OtherIncome - OtherCharges = {expected:.0f}; extracted ProfitBeforeTax = {pbt:.0f}",
            severity="advisory",
        )

    # 5. Shareholders equity rough build-up
    eq_fields = ["PaidUpCapital", "Reserves", "UnappropriatedProfit", "ShareholdersEquity"]
    if _available(values, *eq_fields):
        paid = parse_numeric(values.get("PaidUpCapital")) or 0.0
        reserves = parse_numeric(values.get("Reserves")) or 0.0
        unapp = parse_numeric(values.get("UnappropriatedProfit")) or 0.0
        eq = parse_numeric(values.get("ShareholdersEquity")) or 0.0
        expected = paid + reserves + unapp
        # Looser: some statements include surplus, non-controlling interest, other equity components.
        passed = abs(expected - eq) <= _tol(eq, pct=0.05, floor=50000.0)
        checks["shareholders_equity_build"] = _result(
            "Shareholders Equity Build",
            eq_fields,
            passed,
            expected,
            eq,
            f"PaidUpCapital + Reserves + UnappropriatedProfit = {expected:.0f}; extracted ShareholdersEquity = {eq:.0f}",
        )

    failed = {k: v for k, v in checks.items() if not v.get("passed") and v.get("severity", "hard") == "hard"}
    advisory = {k: v for k, v in checks.items() if not v.get("passed") and v.get("severity") == "advisory"}
    return {
        "checks": checks,
        "failed": failed,
        "advisory": advisory,
        "passed": len(failed) == 0,
        "failed_count": len(failed),
        "advisory_count": len(advisory),
        "failed_fields": sorted({field for item in failed.values() for field in item.get("fields", [])}),
        "summary": " | ".join(f"{v['name']}: {v['message']}" for v in failed.values()),
        "advisory_summary": " | ".join(f"{v['name']}: {v['message']}" for v in advisory.values()),
    }


def sanity_failed_for_fields(values: Dict[str, Any], fields: set[str]) -> Optional[str]:
    report = arithmetic_sanity_report(values)
    messages = []
    for item in report["failed"].values():
        if set(item.get("fields", [])) & fields:
            messages.append(f"{item['name']} failed: {item['message']}")
    return " | ".join(messages) if messages else None
