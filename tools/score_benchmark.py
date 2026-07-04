from __future__ import annotations

r"""
Score extractor accuracy from a filled benchmark label CSV.

Run from inside PsxFinancialExtractor.Web after filling Expected_* columns:
    python .\tools\score_benchmark.py

Default input:
    App_Data\benchmark\benchmark_labels_template.csv

Outputs:
    App_Data\benchmark\benchmark_score_summary.txt
    App_Data\benchmark\benchmark_field_results.csv
    App_Data\benchmark\benchmark_report_results.csv
r"""

import argparse
import csv
import datetime as dt
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VALUE_FIELDS = [
    "PeriodEndDate", "PaidUpCapital", "Reserves", "UnappropriatedProfit", "ShareholdersEquity",
    "CurrentAssets", "CashAndBankBalances", "AdvancesAndReceivables", "FixedAssets", "LongTermLiabilities", "OtherLongTermLiabilities",
    "OtherLiabilities", "WorkingCapital", "Sales", "CostOfSales", "GrossProfit", "OperatingExpenses",
    "FinanceCosts", "OtherIncome", "OtherCharges", "ProfitBeforeTax", "Taxation", "ProfitAfterTax",
    "RevaluationSurplus", "CurrentRatio", "DebtRatio", "BreakupValue", "SubordinatedLoans", "LongTermBorrowings",
    "CurrentLiabilities", "CurrentPortionLongTermLiabilities", "ShortTermBorrowings", "TotalBorrowings", "TradeDebts", "StockInTrade",
    "StoresAndSpares", "ShortTermInvestments", "LongTermInvestments", "OtherFixedAssets", "LeaseFinance", "TradeAndOtherPayables",
    "CashFlowFromOperatingActivities", "CashFlowFromInvestingActivities", "CashFlowFromFinancingActivities", "DeferredLiabilities", "FinanceLeaseObligations",
    "OperatingLeaseObligations", "CurrentLeaseFinance", "DepreciationProvision", "OperatingProfit", "AmountMultiplier",
]

CORE_FIELDS = [
    "PeriodEndDate", "PaidUpCapital", "Reserves", "UnappropriatedProfit", "ShareholdersEquity", "CurrentAssets", "CashAndBankBalances", "FixedAssets",
    "CurrentLiabilities", "WorkingCapital", "Sales", "CostOfSales", "GrossProfit", "OperatingExpenses", "FinanceCosts",
    "OtherIncome", "ProfitBeforeTax", "Taxation", "ProfitAfterTax", "CashFlowFromOperatingActivities", "CashFlowFromInvestingActivities", "CashFlowFromFinancingActivities",
]

NA_VALUES = {"", "na", "n/a", "not applicable", "not disclosed", "nd", "not checked", "skip", "-", "—", "null", "none"}
DATE_FIELDS = {"PeriodEndDate"}
RATIO_FIELDS = {"CurrentRatio", "DebtRatio", "BreakupValue"}
# Client convention: expenses/tax/cost are stored as positive amounts even when
# the PDF prints them in brackets or with a minus sign. Treat opposite-sign
# expected labels for these fields as a match if the absolute amount matches.
POSITIVE_NORMALIZED_FIELDS = {
    "CostOfSales", "OperatingExpenses", "FinanceCosts", "OtherCharges", "Taxation", "DepreciationProvision",
}


def find_project_root(start: Path) -> Path:
    start = start.resolve()
    candidates = [start, Path.cwd().resolve(), *start.parents, *Path.cwd().resolve().parents]
    for base in candidates:
        if (base / "workers" / "psx_worker.py").exists():
            return base
        child = base / "PsxFinancialExtractor.Web"
        if (child / "workers" / "psx_worker.py").exists():
            return child
    raise FileNotFoundError("Could not locate PsxFinancialExtractor.Web project folder.")


def clean_text(v: Any) -> str:
    return str(v if v is not None else "").strip()


def is_blank_or_na(v: Any) -> bool:
    return re.sub(r"\s+", " ", clean_text(v).lower()) in NA_VALUES


def parse_date(v: Any) -> Optional[str]:
    s = clean_text(v)
    if not s:
        return None
    # Normalize YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, YYYY/MM/DD.
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:
        d, mo, y = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return s.lower()


def parse_number(v: Any) -> Optional[Decimal]:
    s = clean_text(v)
    if not s:
        return None
    neg = False
    # Accounting parentheses.
    if re.search(r"^\s*\(.*\)\s*$", s):
        neg = True
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    if s.strip().startswith("-"):
        neg = True
    # Remove currency/words, preserve digits, decimal point, minus.
    s2 = re.sub(r"(?i)rs\.?|pkr|rupees|million|thousand|billion|mn|000s|\(rupees\)|\(rs\.?\)", "", s)
    s2 = s2.replace(",", "")
    s2 = re.sub(r"[^0-9.\-]", "", s2)
    if not s2 or s2 in {"-", ".", "-."}:
        return None
    # If multiple '-' after cleanup, keep sign only.
    s2 = s2.replace("-", "")
    try:
        num = Decimal(s2)
    except InvalidOperation:
        return None
    if neg and num != 0:
        num = -num
    # Normalize -0.
    if num == 0:
        num = Decimal(0)
    return num


def numeric_match(actual: Decimal, expected: Decimal, field: str) -> Tuple[bool, str]:
    if field in RATIO_FIELDS:
        tol = Decimal("0.02")
    else:
        # Financial amounts should usually be exact. Allow tiny rounding tolerance.
        tol = max(Decimal("1"), abs(expected) * Decimal("0.0001"))
    diff = abs(actual - expected)
    return diff <= tol, str(diff)


def compare(field: str, extracted: Any, expected: Any) -> Tuple[str, str, str, str]:
    """Return result, normalized extracted, normalized expected, diff."""
    if is_blank_or_na(expected):
        return "IGNORED", clean_text(extracted), clean_text(expected), ""

    if field in DATE_FIELDS:
        a = parse_date(extracted)
        e = parse_date(expected)
        if not a and e:
            return "MISSING", "", str(e), ""
        return ("MATCH" if a == e else "MISMATCH"), str(a or ""), str(e or ""), ""

    e_num = parse_number(expected)
    a_num = parse_number(extracted)
    if e_num is not None:
        if a_num is None:
            return "MISSING", "", str(e_num), ""
        ok, diff = numeric_match(a_num, e_num, field)
        if (not ok) and field in POSITIVE_NORMALIZED_FIELDS:
            ok_abs, diff_abs = numeric_match(abs(a_num), abs(e_num), field)
            if ok_abs:
                return "MATCH", str(a_num), str(e_num), f"sign-normalized:{diff_abs}"
        return ("MATCH" if ok else "MISMATCH"), str(a_num), str(e_num), diff

    # Fallback textual compare for unusual labels.
    a = re.sub(r"\s+", " ", clean_text(extracted).lower())
    e = re.sub(r"\s+", " ", clean_text(expected).lower())
    if not a and e:
        return "MISSING", a, e, ""
    return ("MATCH" if a == e else "MISMATCH"), a, e, ""


def parse_args():
    p = argparse.ArgumentParser(description="Score a filled PSX extraction benchmark label sheet.")
    p.add_argument("--input", default="", help="Filled label CSV. Default: App_Data/benchmark/benchmark_labels_template.csv")
    p.add_argument("--output-dir", default="", help="Output directory. Default: App_Data/benchmark")
    return p.parse_args()


def pct(n: int, d: int) -> str:
    return "n/a" if d == 0 else f"{(100*n/d):.2f}%"


def main() -> int:
    args = parse_args()
    root = find_project_root(Path(__file__).parent)
    input_path = Path(args.input) if args.input else root / "App_Data" / "benchmark" / "benchmark_labels_template.csv"
    out_dir = Path(args.output_dir) if args.output_dir else root / "App_Data" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not input_path.exists():
        raise FileNotFoundError(f"Filled label CSV not found: {input_path}")

    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    # Coverage denominator: all non-empty extracted values in the label sheet.
    total_extracted_values = 0
    for _row in rows:
        for _field in VALUE_FIELDS:
            _actual = _row.get(f"Extracted_{_field}", _row.get(_field, ""))
            if not is_blank_or_na(_actual):
                total_extracted_values += 1

    field_results: List[Dict[str, Any]] = []
    report_summary: Dict[str, Dict[str, Any]] = {}
    field_summary: Dict[str, Dict[str, int]] = {field: {"checked": 0, "match": 0, "missing": 0, "mismatch": 0} for field in VALUE_FIELDS}

    for idx, row in enumerate(rows, start=1):
        case_key = f"{row.get('CaseNo') or idx} | {row.get('Symbol','')} {row.get('Year','')} {row.get('RequestedReport','')}"
        report_summary[case_key] = {
            "CaseKey": case_key,
            "Symbol": row.get("Symbol", ""),
            "Year": row.get("Year", ""),
            "RequestedReport": row.get("RequestedReport", ""),
            "Status": row.get("Status", ""),
            "CheckedFields": 0,
            "MatchedFields": 0,
            "MissingFields": 0,
            "MismatchedFields": 0,
            "CoreChecked": 0,
            "CoreMatched": 0,
            "Accuracy": "n/a",
            "CoreAccuracy": "n/a",
        }
        for field in VALUE_FIELDS:
            actual = row.get(f"Extracted_{field}", row.get(field, ""))
            expected = row.get(f"Expected_{field}", "")
            result, norm_actual, norm_expected, diff = compare(field, actual, expected)
            if result == "IGNORED":
                continue
            field_summary[field]["checked"] += 1
            report_summary[case_key]["CheckedFields"] += 1
            if field in CORE_FIELDS:
                report_summary[case_key]["CoreChecked"] += 1
            if result == "MATCH":
                field_summary[field]["match"] += 1
                report_summary[case_key]["MatchedFields"] += 1
                if field in CORE_FIELDS:
                    report_summary[case_key]["CoreMatched"] += 1
            elif result == "MISSING":
                field_summary[field]["missing"] += 1
                report_summary[case_key]["MissingFields"] += 1
            else:
                field_summary[field]["mismatch"] += 1
                report_summary[case_key]["MismatchedFields"] += 1
            field_results.append({
                "CaseKey": case_key,
                "Symbol": row.get("Symbol", ""),
                "Year": row.get("Year", ""),
                "RequestedReport": row.get("RequestedReport", ""),
                "Field": field,
                "Result": result,
                "ExtractedRaw": actual,
                "ExpectedRaw": expected,
                "ExtractedNormalized": norm_actual,
                "ExpectedNormalized": norm_expected,
                "Difference": diff,
                "SourcePage": row.get(f"SourcePage_{field}", ""),
                "PdfUrl": row.get("PdfUrl", ""),
                "CachedPdfPath": row.get("CachedPdfPath", ""),
            })

    for item in report_summary.values():
        item["Accuracy"] = pct(int(item["MatchedFields"]), int(item["CheckedFields"]))
        item["CoreAccuracy"] = pct(int(item["CoreMatched"]), int(item["CoreChecked"]))

    report_rows = list(report_summary.values())
    total_checked = sum(v["checked"] for v in field_summary.values())
    total_match = sum(v["match"] for v in field_summary.values())
    total_missing = sum(v["missing"] for v in field_summary.values())
    total_mismatch = sum(v["mismatch"] for v in field_summary.values())
    core_checked = sum(1 for r in field_results if r["Field"] in CORE_FIELDS)
    core_match = sum(1 for r in field_results if r["Field"] in CORE_FIELDS and r["Result"] == "MATCH")

    field_summary_rows = []
    for field, v in field_summary.items():
        if v["checked"] == 0:
            continue
        field_summary_rows.append({
            "Field": field,
            "Checked": v["checked"],
            "Matched": v["match"],
            "Missing": v["missing"],
            "Mismatched": v["mismatch"],
            "Accuracy": pct(v["match"], v["checked"]),
        })

    def write_csv(path: Path, rows_: List[Dict[str, Any]], fields: List[str]):
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader(); w.writerows(rows_)

    field_results_path = out_dir / "benchmark_field_results.csv"
    report_results_path = out_dir / "benchmark_report_results.csv"
    field_summary_path = out_dir / "benchmark_field_summary.csv"
    summary_path = out_dir / "benchmark_score_summary.txt"

    write_csv(field_results_path, field_results, [
        "CaseKey", "Symbol", "Year", "RequestedReport", "Field", "Result", "ExtractedRaw", "ExpectedRaw",
        "ExtractedNormalized", "ExpectedNormalized", "Difference", "SourcePage", "PdfUrl", "CachedPdfPath",
    ])
    write_csv(report_results_path, report_rows, [
        "CaseKey", "Symbol", "Year", "RequestedReport", "Status", "CheckedFields", "MatchedFields",
        "MissingFields", "MismatchedFields", "Accuracy", "CoreChecked", "CoreMatched", "CoreAccuracy",
    ])
    write_csv(field_summary_path, field_summary_rows, ["Field", "Checked", "Matched", "Missing", "Mismatched", "Accuracy"])

    lines = []
    lines.append("PSX BENCHMARK SCORE")
    lines.append(f"Created: {dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Input: {input_path}")
    lines.append("")
    lines.append(f"Reports in label sheet: {len(rows)}")
    lines.append(f"Fields checked: {total_checked}")
    lines.append(f"Matched: {total_match}")
    lines.append(f"Missing: {total_missing}")
    lines.append(f"Mismatched: {total_mismatch}")
    lines.append(f"Overall field accuracy: {pct(total_match, total_checked)}")
    lines.append(f"Core field accuracy: {pct(core_match, core_checked)}")
    lines.append(f"Coverage: {pct(total_checked, total_extracted_values)}")
    lines.append(f"Non-empty extracted values: {total_extracted_values}")
    lines.append("")
    lines.append("Coverage means: filled Expected_* fields divided by all non-empty Extracted_* values.")
    lines.append("Values labelled REVIEW are still shown to the client, but should not be counted as verified until Expected_* is filled and scored.")
    lines.append("")
    lines.append("Recommended claim rule:")
    lines.append("- Claim 92-95% only if Overall field accuracy >= 92% on at least 50 labelled reports and at least 500 checked fields.")
    lines.append("- For client delivery, also check benchmark_field_results.csv for high-impact mismatches such as profit, equity, sales, tax, and cash flows.")
    lines.append("")
    lines.append(f"Field results CSV: {field_results_path}")
    lines.append(f"Report results CSV: {report_results_path}")
    lines.append(f"Field summary CSV: {field_summary_path}")
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    print("Benchmark scored.")
    print("Overall field accuracy:", pct(total_match, total_checked))
    print("Core field accuracy:", pct(core_match, core_checked))
    print("Coverage:", pct(total_checked, total_extracted_values))
    print("Summary:", summary_path)
    print("Detailed mismatches:", field_results_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
