from __future__ import annotations
r"""
Create client-facing review files from an extraction CSV/JSON.

Purpose:
  * Keep every extracted value visible.
  * Mark each value as DIRECT, CALCULATED, INFERRED_REVIEW, or REVIEW.
  * Improve practical review coverage without pretending low-confidence values are verified.

Run examples:
  python .\PSX_Create_Review_Output.py --input .\App_Data\jobs\batch1_25_latest.csv --json .\App_Data\jobs\batch1_25_latest.json --output-dir .\App_Data\benchmark\batch1_25\review
  python .\PSX_Create_Review_Output.py --input .\App_Data\jobs\batch2_25_latest.csv --json .\App_Data\jobs\batch2_25_latest.json --output-dir .\App_Data\benchmark\batch2_25\review
r"""
import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
META_FIELDS = [
    "CaseNo", "Batch", "Symbol", "Year", "RequestedReport", "ActualReportType", "PeriodEnded",
    "FiscalYearEndMonth", "Published", "Status", "FilledFieldCount", "PdfUrl", "CachedPdfPath", "Title",
]


def find_project_root(start: Path) -> Path:
    start = start.resolve()
    for base in [start, Path.cwd().resolve(), *start.parents, *Path.cwd().resolve().parents]:
        if (base / "workers" / "psx_worker.py").exists():
            return base
        child = base / "PsxFinancialExtractor.Web"
        if (child / "workers" / "psx_worker.py").exists():
            return child
    raise FileNotFoundError("Could not locate PsxFinancialExtractor.Web project folder")


def norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("ﬁ", "fi").replace("ﬂ", "fl").lower()).strip()


def is_blank(v: Any) -> bool:
    return str(v or "").strip() == ""


def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_evidence_by_case(json_path: Path) -> Dict[str, Dict[str, str]]:
    data = read_json(json_path)
    out: Dict[str, Dict[str, str]] = {}
    for row in data.get("extractedRows", []) if isinstance(data, dict) else []:
        key = str(row.get("CaseNo", ""))
        report = row.get("_fullExtractedReport") or {}
        ev = report.get("evidence") or {}
        if isinstance(ev, dict):
            out[key] = {str(k): str(v) for k, v in ev.items() if v is not None}
    return out


def suspicious_reason(field: str, evidence: str) -> str:
    ev = norm(evidence)
    if not ev:
        return "no field-level evidence"
    # Labels that generated known mismatches in earlier batches. These are generic
    # concept checks, not company-specific hardcoding.
    if field == "Reserves" and any(x in ev for x in ["share capital and reserves", "shareholders equity", "shareholders' equity"]):
        return "review: equity header/subtotal may have been used as reserves"
    if field == "Sales" and any(x in ev for x in [
        "revenue reserve", "unappropriated profit", "accumulated profit", "fee and commission income",
        "other income fee", "dividend income"
    ]):
        return "review: source label may not represent sales/revenue/total income"
    if field == "OperatingExpenses" and "total expenses" in ev and "operating" not in ev:
        return "review: total expenses may not equal operating expenses"
    if field == "OtherCharges" and any(x in ev for x in ["credit loss allowance", "write offs", "write-offs"]):
        return "review: impairment/credit-loss row may not be other charges for client mapping"
    if field in {"OtherLongTermLiabilities", "DeferredLiabilities"} and any(x in ev for x in ["staff retirement", "retirement benefit", "defined benefit", "gratuity"]):
        return "review: employee benefit obligation mapping should be reviewed"
    if field == "AmountMultiplier":
        return "review: unit/multiplier metadata"
    if field == "PeriodEndDate":
        return "review: date inferred or textual"
    return ""


def classify(field: str, value: Any, row_status: str, evidence: str, warnings: str) -> Tuple[str, str]:
    if is_blank(value):
        return "BLANK", "no extracted value"
    ev = str(evidence or "")
    evn = norm(ev)
    wn = norm(warnings)
    sus = suspicious_reason(field, ev)
    hard_warning = any(x in wn for x in [
        "manual review", "needs manual review", "poor parse", "scanned pdf", "ocr fallback skipped",
        "download failed", "extraction failed", "not found / needs manual review"
    ])
    if sus:
        return "REVIEW", sus
    if "calculated" in evn or "inferred" in evn or "calculated" in evn or "calculated" in evn:
        if field in {"WorkingCapital", "CurrentRatio", "DebtRatio", "BreakupValue", "ShareholdersEquity", "TotalBorrowings", "CostOfSales"}:
            return "CALCULATED", "formula/derived value; verify source inputs"
        return "INFERRED_REVIEW", "inferred/calculated; verify manually if material"
    if "pdf page" in evn or "ocr pdf page" in evn:
        if row_status != "OK" or hard_warning:
            return "DIRECT_REVIEW", "direct PDF evidence exists but row/report status needs review"
        return "DIRECT", "direct PDF/OCR evidence"
    if row_status != "OK" or hard_warning:
        return "REVIEW", "row/report status needs review"
    return "REVIEW", "value shown but field-level source evidence is not strong enough"


def pct(n: int, d: int) -> str:
    return "n/a" if d == 0 else f"{100*n/d:.2f}%"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="", help="Extraction CSV, default App_Data/jobs/other_company_stats_latest.csv")
    p.add_argument("--json", default="", help="Matching extraction JSON, default inferred from input path")
    p.add_argument("--output-dir", default="", help="Output directory")
    args = p.parse_args()

    root = find_project_root(Path(__file__).parent)
    input_csv = Path(args.input) if args.input else root / "App_Data" / "jobs" / "other_company_stats_latest.csv"
    if not input_csv.is_absolute():
        input_csv = root / input_csv
    if args.json:
        input_json = Path(args.json)
        if not input_json.is_absolute():
            input_json = root / input_json
    else:
        input_json = input_csv.with_suffix(".json")
    out_dir = Path(args.output_dir) if args.output_dir else root / "App_Data" / "benchmark" / "review_output"
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(input_csv)
    evidence_by_case = load_evidence_by_case(input_json)

    long_rows: List[Dict[str, Any]] = []
    wide_rows: List[Dict[str, Any]] = []
    counts = {"total_values": 0, "direct": 0, "direct_review": 0, "calculated": 0, "inferred_review": 0, "review": 0, "blank": 0}

    for row in rows:
        case_no = str(row.get("CaseNo", ""))
        status = str(row.get("Status", ""))
        warnings = str(row.get("Warnings", ""))
        ev_map = evidence_by_case.get(case_no, {})
        wide = {m: row.get(m, "") for m in META_FIELDS if m in row}
        for field in VALUE_FIELDS:
            value = row.get(field, "")
            evidence = ev_map.get(field, "")
            label, reason = classify(field, value, status, evidence, warnings)
            if label == "BLANK":
                counts["blank"] += 1
            else:
                counts["total_values"] += 1
                key = label.lower().replace("_review", "_review")
                if label == "DIRECT": counts["direct"] += 1
                elif label == "DIRECT_REVIEW": counts["direct_review"] += 1
                elif label == "CALCULATED": counts["calculated"] += 1
                elif label == "INFERRED_REVIEW": counts["inferred_review"] += 1
                else: counts["review"] += 1
            wide[f"Value_{field}"] = value
            wide[f"ReviewLabel_{field}"] = label
            wide[f"ReviewReason_{field}"] = reason
            wide[f"Evidence_{field}"] = evidence
            if label != "BLANK":
                long_rows.append({
                    "CaseNo": case_no,
                    "Batch": row.get("Batch", ""),
                    "Symbol": row.get("Symbol", ""),
                    "Year": row.get("Year", ""),
                    "RequestedReport": row.get("RequestedReport", ""),
                    "ActualReportType": row.get("ActualReportType", ""),
                    "Status": status,
                    "Field": field,
                    "ExtractedValue": value,
                    "ReviewLabel": label,
                    "ReviewReason": reason,
                    "Evidence": evidence,
                    "PdfUrl": row.get("PdfUrl", ""),
                    "CachedPdfPath": row.get("CachedPdfPath", ""),
                })
        wide_rows.append(wide)

    long_path = out_dir / "client_review_values_long.csv"
    wide_path = out_dir / "client_review_values_wide.csv"
    summary_path = out_dir / "client_review_summary.txt"

    def write_csv(path: Path, data: List[Dict[str, Any]]):
        if not data:
            path.write_text("", encoding="utf-8-sig")
            return
        fields: List[str] = []
        for d in data:
            for k in d.keys():
                if k not in fields:
                    fields.append(k)
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader(); w.writerows(data)

    write_csv(long_path, long_rows)
    write_csv(wide_path, wide_rows)

    total = counts["total_values"]
    direct_plus_calc = counts["direct"] + counts["calculated"]
    values_visible = total
    review_visible = total - direct_plus_calc
    lines = [
        "PSX CLIENT REVIEW OUTPUT SUMMARY",
        f"Input CSV: {input_csv}",
        f"Input JSON: {input_json}",
        "",
        f"Reports: {len(rows)}",
        f"Non-empty extracted values: {total}",
        f"DIRECT values: {counts['direct']}",
        f"DIRECT_REVIEW values: {counts['direct_review']}",
        f"CALCULATED values: {counts['calculated']}",
        f"INFERRED_REVIEW values: {counts['inferred_review']}",
        f"REVIEW values: {counts['review']}",
        "",
        f"Strict confidence coverage (DIRECT + CALCULATED): {pct(direct_plus_calc, total)}",
        f"Visible review coverage (all non-empty values shown with labels): {pct(values_visible, total)}",
        f"Values shown with a review label: {review_visible}",
        "",
        "Meaning:",
        "- DIRECT = source row found in PDF/OCR evidence.",
        "- CALCULATED = formula/derived value; verify the input values.",
        "- DIRECT_REVIEW / INFERRED_REVIEW / REVIEW = value is displayed but should be reviewed before treating as verified.",
        "",
        f"Long review file: {long_path}",
        f"Wide review file: {wide_path}",
    ]
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    print("Created review output:")
    print(long_path)
    print(wide_path)
    print(summary_path)
    print("Strict confidence coverage:", pct(direct_plus_calc, total))
    print("Visible review coverage:", pct(values_visible, total))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
