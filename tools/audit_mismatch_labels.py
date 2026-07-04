from __future__ import annotations

r"""
Audit mismatch rows to identify likely label/seed errors before treating them as
extractor failures.

This does not re-read PDFs. It uses each row's EvidenceForCrossCheck text, which
contains direct PDF-row evidence such as:
    Sales: PDF page 12: Revenue => [87338997, 85625742]; unit=1000

Output files:
    mismatch_label_audit_details.csv
    mismatch_label_audit_summary.txt
"""

import argparse
import csv
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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

NA = {"", "na", "n/a", "not applicable", "not disclosed", "nd", "not checked", "skip", "-", "—", "null", "none"}
DATE_FIELDS = {"PeriodEndDate"}
RATIO_FIELDS = {"CurrentRatio", "DebtRatio", "BreakupValue"}
POSITIVE_NORMALIZED_FIELDS = {
    "CostOfSales", "OperatingExpenses", "FinanceCosts", "OtherCharges", "Taxation", "DepreciationProvision",
    "TradeAndOtherPayables", "CurrentPortionLongTermLiabilities", "ShortTermBorrowings", "LongTermBorrowings", "TotalBorrowings", "CurrentLiabilities",
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


def clean(v: Any) -> str:
    return str(v if v is not None else "").strip()


def is_blank(v: Any) -> bool:
    return re.sub(r"\s+", " ", clean(v).lower()) in NA


def parse_number(v: Any) -> Optional[Decimal]:
    s = clean(v)
    if not s:
        return None
    neg = False
    if re.search(r"^\s*\(.*\)\s*$", s):
        neg = True
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    if s.strip().startswith("-"):
        neg = True
    s = re.sub(r"(?i)rs\.?|pkr|rupees|million|thousand|billion|mn|000s|\(rupees\)|\(rs\.?\)", "", s)
    s = s.replace(",", "")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s in {"-", ".", "-."}:
        return None
    s = s.replace("-", "")
    try:
        n = Decimal(s)
    except InvalidOperation:
        return None
    if neg and n != 0:
        n = -n
    return Decimal(0) if n == 0 else n


def tol(field: str, expected: Decimal) -> Decimal:
    if field in RATIO_FIELDS:
        return Decimal("0.02")
    return max(Decimal("1"), abs(expected) * Decimal("0.0001"))


def numeric_equal(a: Optional[Decimal], b: Optional[Decimal], field: str) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol(field, b)


def numeric_equal_abs(a: Optional[Decimal], b: Optional[Decimal], field: str) -> bool:
    if a is None or b is None:
        return False
    return abs(abs(a) - abs(b)) <= tol(field, b)


def parse_date(v: Any) -> str:
    s = clean(v)
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:
        d, mo, y = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return s.lower()


def is_match(field: str, actual: Any, expected: Any) -> bool:
    if is_blank(expected):
        return False
    if field in DATE_FIELDS:
        return parse_date(actual) == parse_date(expected)
    return numeric_equal(parse_number(actual), parse_number(expected), field)


def evidence_parts_for_field(evidence: str, field: str) -> List[str]:
    parts = [p.strip() for p in evidence.split("|")]
    out = []
    for p in parts:
        if p.startswith(field + ":") or p.startswith(field + " ") or (field + ": PDF page") in p:
            out.append(p)
    return out


def parse_evidence_amount_lists(parts: Iterable[str]) -> List[Tuple[List[Decimal], int, str]]:
    lists: List[Tuple[List[Decimal], int, str]] = []
    for part in parts:
        unit = 1
        m_unit = re.search(r"unit\s*=\s*(-?\d+)", part, flags=re.I)
        if m_unit:
            try:
                unit = int(m_unit.group(1))
            except Exception:
                unit = 1
        # capture all bracketed amount lists in this evidence part
        for m in re.finditer(r"=>\s*\[([^\]]+)\]", part):
            raw = m.group(1)
            nums: List[Decimal] = []
            for token in raw.split(","):
                n = parse_number(token)
                if n is not None:
                    nums.append(n * Decimal(unit))
            if nums:
                lists.append((nums, unit, part[:500]))
    return lists


def index_in_list(value: Optional[Decimal], nums: List[Decimal], field: str, abs_ok: bool = False) -> Optional[int]:
    if value is None:
        return None
    for i, n in enumerate(nums):
        if numeric_equal(value, n, field) or (abs_ok and numeric_equal_abs(value, n, field)):
            return i
    return None


def classify_mismatch(row: Dict[str, Any], field: str, actual: str, expected: str) -> Tuple[str, str, str]:
    a = parse_number(actual)
    e = parse_number(expected)
    req = clean(row.get("RequestedReport") or row.get("ActualReportType")).lower()
    evidence = clean(row.get("EvidenceForCrossCheck"))
    parts = evidence_parts_for_field(evidence, field)
    amount_lists = parse_evidence_amount_lists(parts)

    if field in DATE_FIELDS:
        return "REAL_OR_MANUAL_REVIEW", "Date mismatch; manual check needed.", ""

    if a is not None and e is not None:
        if numeric_equal_abs(a, e, field) and not numeric_equal(a, e, field):
            if field in POSITIVE_NORMALIZED_FIELDS or a >= 0:
                return "LIKELY_LABEL_SIGN_CONVENTION", "Expected value has opposite sign but same absolute amount; extractor stores expenses/tax/cost as positive by client convention.", ""
        for factor in (Decimal(1000), Decimal(1000000)):
            if numeric_equal(a, e * factor, field) or numeric_equal(a * factor, e, field) or numeric_equal_abs(a, e * factor, field) or numeric_equal_abs(a * factor, e, field):
                return "LIKELY_LABEL_UNIT_SCALE", f"Actual and expected differ mainly by unit scale factor {factor}.", ""

    for nums, unit, snippet in amount_lists:
        ai = index_in_list(a, nums, field, abs_ok=(field in POSITIVE_NORMALIZED_FIELDS))
        ei = index_in_list(e, nums, field, abs_ok=True)
        if ai is not None and ei is not None and ai != ei:
            if ai == 0 and ei > 0:
                return "POSSIBLE_LABEL_COMPARATIVE_COLUMN_REVIEW", "Extractor value is first PDF amount while expected value is another amount from the same PDF row. This may be a label/seed comparative-column issue, but header/current-period convention must be checked manually.", snippet
            if ei == 0 and ai > 0:
                return "LIKELY_EXTRACTOR_COLUMN_ERROR", "Expected value is the first/current PDF amount, while extractor selected a later column from the same PDF row.", snippet
            return "COLUMN_SELECTION_REVIEW", "Actual and expected are both present in the same PDF row but in different columns; verify current-period convention.", snippet
        if ai is not None and ei is None:
            if ai == 0:
                return "POSSIBLE_LABEL_DIRECT_ROW_REVIEW", "Extractor value is directly evidenced as first PDF amount in its PDF row, but expected value is not in that evidence list. Manual PDF/header check needed before counting as extractor error.", snippet
            return "EXTRACTOR_DIRECT_ROW_REVIEW", "Extractor value is directly evidenced in PDF row, but not first column; manual review needed.", snippet
        if ai is None and ei is not None:
            if ei == 0:
                return "LIKELY_EXTRACTOR_VALUE_ERROR", "Expected value is first/current amount in the direct PDF row evidence; extractor value is not in that row.", snippet
            return "EXPECTED_IN_PDF_ROW_REVIEW", "Expected value appears in PDF row evidence but not as first/current column; manual review needed.", snippet

    if parts:
        return "REAL_OR_MAPPING_REVIEW", "Mismatch has field evidence but the actual/expected relation was not automatically explainable.", parts[0][:500]
    return "NO_DIRECT_EVIDENCE_REVIEW", "No direct field evidence found for this mismatch; manual PDF check needed.", ""


def load_label_file(path: Path, batch: str) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["_Batch"] = batch
    return rows


def parse_args():
    p = argparse.ArgumentParser(description="Audit PSX benchmark mismatches for likely label/seed errors.")
    p.add_argument("--batch1", default="", help="Batch 1 labels CSV")
    p.add_argument("--batch2", default="", help="Batch 2 labels CSV")
    p.add_argument("--input", default="", help="Single labels CSV")
    p.add_argument("--batch", default="", help="Batch label for --input, e.g. Batch 1")
    p.add_argument("--output-dir", default="", help="Output folder. Default: App_Data/benchmark/fresh50_label_audit")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = find_project_root(Path(__file__).parent)
    out_dir = Path(args.output_dir) if args.output_dir else root / "App_Data" / "benchmark" / "fresh50_label_audit"
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    if args.input:
        path = Path(args.input)
        if not path.is_absolute(): path = root / path
        rows.extend(load_label_file(path, args.batch or "Batch"))
    else:
        b1 = Path(args.batch1) if args.batch1 else root / "App_Data" / "benchmark" / "batch1_25" / "labels_batch1_25.csv"
        b2 = Path(args.batch2) if args.batch2 else root / "App_Data" / "benchmark" / "batch2_25" / "labels_batch2_25.csv"
        if not b1.is_absolute(): b1 = root / b1
        if not b2.is_absolute(): b2 = root / b2
        if b1.exists(): rows.extend(load_label_file(b1, "Batch 1"))
        if b2.exists(): rows.extend(load_label_file(b2, "Batch 2"))
    if not rows:
        raise FileNotFoundError("No label rows found. Run benchmark and create labels first.")

    details: List[Dict[str, Any]] = []
    checked = matched = mismatched = missing = 0
    likely_label = 0
    possible_label_or_column_review = 0
    likely_extractor = 0

    for row in rows:
        for field in VALUE_FIELDS:
            exp_col = f"Expected_{field}"
            act_col = f"Extracted_{field}"
            expected = row.get(exp_col, "")
            actual = row.get(act_col, row.get(field, ""))
            if is_blank(expected):
                continue
            checked += 1
            if is_match(field, actual, expected):
                matched += 1
                continue
            if is_blank(actual):
                missing += 1
                verdict, reason, snippet = "MISSING_EXTRACTED_VALUE", "Expected value exists but extractor produced blank.", ""
            else:
                mismatched += 1
                verdict, reason, snippet = classify_mismatch(row, field, clean(actual), clean(expected))
            if verdict.startswith("LIKELY_LABEL"):
                likely_label += 1
            if verdict.startswith("POSSIBLE_LABEL") or verdict == "COLUMN_SELECTION_REVIEW" or verdict == "EXPECTED_IN_PDF_ROW_REVIEW" or verdict == "EXTRACTOR_DIRECT_ROW_REVIEW":
                possible_label_or_column_review += 1
            if verdict.startswith("LIKELY_EXTRACTOR") or verdict == "MISSING_EXTRACTED_VALUE":
                likely_extractor += 1
            details.append({
                "Batch": row.get("_Batch", ""),
                "CaseNo": row.get("CaseNo", ""),
                "Symbol": row.get("Symbol", ""),
                "Year": row.get("Year", ""),
                "RequestedReport": row.get("RequestedReport", ""),
                "ActualReportType": row.get("ActualReportType", ""),
                "Field": field,
                "ExtractedValue": clean(actual),
                "ExpectedValue": clean(expected),
                "Verdict": verdict,
                "Reason": reason,
                "EvidenceSnippet": snippet,
            })

    adjusted_matches = matched + likely_label
    optimistic_matches = matched + likely_label + possible_label_or_column_review
    raw_acc = (matched / checked * 100) if checked else 0
    adjusted_acc = (adjusted_matches / checked * 100) if checked else 0
    optimistic_acc = (optimistic_matches / checked * 100) if checked else 0

    detail_path = out_dir / "mismatch_label_audit_details.csv"
    fieldnames = ["Batch", "CaseNo", "Symbol", "Year", "RequestedReport", "ActualReportType", "Field", "ExtractedValue", "ExpectedValue", "Verdict", "Reason", "EvidenceSnippet"]
    with detail_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(details)

    # Verdict summary CSV
    counts: Dict[str, int] = {}
    for d in details:
        counts[d["Verdict"]] = counts.get(d["Verdict"], 0) + 1
    verdict_path = out_dir / "mismatch_label_audit_verdict_summary.csv"
    with verdict_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Verdict", "Count"])
        writer.writeheader()
        for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
            writer.writerow({"Verdict": k, "Count": v})

    summary = [
        "Fresh-50 mismatch label audit",
        "================================",
        f"Checked expected values: {checked}",
        f"Matched values: {matched}",
        f"Mismatched values: {mismatched}",
        f"Missing extracted values: {missing}",
        f"Likely label/seed errors: {likely_label}",
        f"Possible label/column-review rows: {possible_label_or_column_review}",
        f"Likely extractor errors: {likely_extractor}",
        f"Raw direct accuracy: {raw_acc:.2f}%",
        f"Adjusted direct accuracy if likely label/seed errors are treated as extractor-correct: {adjusted_acc:.2f}%",
        f"Optimistic accuracy if likely + possible label/column-review rows are treated as extractor-correct: {optimistic_acc:.2f}%",
        "",
        "Verdict counts:",
    ]
    for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        summary.append(f"  {k}: {v}")
    summary_text = "\n".join(summary) + "\n"
    (out_dir / "mismatch_label_audit_summary.txt").write_text(summary_text, encoding="utf-8")

    print(summary_text)
    print(f"Details: {detail_path}")
    print(f"Verdict summary: {verdict_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
