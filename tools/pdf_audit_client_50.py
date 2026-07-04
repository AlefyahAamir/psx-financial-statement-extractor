from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from arithmetic_sanity import arithmetic_sanity_report

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

DATE_FIELDS = {"PeriodEndDate"}
REVIEW_LABELS = {"DIRECT_REVIEW", "INFERRED_REVIEW", "REVIEW"}

KNOWN_FORMULA_FIELDS = {"WorkingCapital", "CostOfSales", "ShareholdersEquity", "TotalBorrowings", "CurrentRatio"}

def find_project_root(start: Path) -> Path:
    start = start.resolve()
    for base in [start, Path.cwd().resolve(), *start.parents, *Path.cwd().resolve().parents]:
        if (base / "workers" / "psx_worker.py").exists():
            return base
        child = base / "PsxFinancialExtractor.Web"
        if (child / "workers" / "psx_worker.py").exists():
            return child
    raise FileNotFoundError("Could not locate PsxFinancialExtractor.Web project folder")

def clean(v: Any) -> str:
    return str(v if v is not None else "").strip()

def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def write_csv(path: Path, rows: List[Dict[str, Any]], fields: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for r in rows:
            for k in r.keys():
                if k not in fields:
                    fields.append(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def key(row: Dict[str, Any], field: str) -> Tuple[str, str, str, str, str, str]:
    return (
        clean(row.get("Batch")),
        clean(row.get("CaseNo")),
        clean(row.get("Symbol")).upper(),
        clean(row.get("Year")),
        clean(row.get("RequestedReport")).lower(),
        field,
    )

def load_review_rows(bench: Path) -> Dict[Tuple[str, str, str, str, str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str, str, str, str, str], Dict[str, Any]] = {}
    for i in range(1, 6):
        p = bench / f"review_batch{i}" / "client_review_values_long.csv"
        if not p.exists():
            continue
        for r in read_csv(p):
            r["Batch"] = clean(r.get("Batch") or str(i))
            fld = clean(r.get("Field"))
            if fld:
                out[key(r, fld)] = r
    return out

def cached_pdf_from_url(root: Path, url: str) -> Optional[Path]:
    if not url:
        return None
    pdf_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    p = root / "App_Data" / "downloads" / f"{pdf_id}.pdf"
    return p if p.exists() else None

def resolve_pdf_path(root: Path, row: Dict[str, Any]) -> Optional[Path]:
    candidates: List[Path] = []
    c = clean(row.get("CachedPdfPath"))
    if c:
        # On the user's machine this may be an absolute Windows path.
        candidates.append(Path(c))
        candidates.append(root / c)
        # When a result zip is uploaded elsewhere, only the filename under App_Data/downloads may exist.
        candidates.append(root / "App_Data" / "downloads" / Path(c).name)
    url_candidate = cached_pdf_from_url(root, clean(row.get("PdfUrl")))
    if url_candidate:
        candidates.append(url_candidate)
    for p in candidates:
        try:
            if p.exists() and p.is_file() and p.suffix.lower() == ".pdf":
                return p
        except Exception:
            pass
    return None

def read_pdf_text(pdf_path: Path) -> Tuple[str, str]:
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        return "", f"PyMuPDF unavailable: {type(e).__name__}: {e}"
    try:
        doc = fitz.open(str(pdf_path))
        parts = []
        for page in doc:
            parts.append(page.get_text("text") or "")
        return "\n".join(parts), ""
    except Exception as e:
        return "", f"Could not read PDF text: {type(e).__name__}: {e}"

def parse_decimal(v: Any) -> Optional[Decimal]:
    s = clean(v)
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    if s.startswith("-"):
        neg = True
    s = re.sub(r"(?i)rs\.?|pkr|rupees|million|thousand|billion|mn|000s|\(rupees\)|\(rs\.?\)", "", s)
    s = s.replace(",", "")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s in {"-", ".", "-."}:
        return None
    try:
        d = Decimal(s)
        return -abs(d) if neg else d
    except InvalidOperation:
        return None

def int_commas(n: int) -> str:
    return f"{n:,}"

def numeric_candidates(v: Any) -> List[str]:
    d = parse_decimal(v)
    if d is None:
        return []
    vals = []
    # For matching PDF display, use absolute also because expenses may be in brackets.
    for base in [d, abs(d)]:
        if base == base.to_integral_value():
            n = int(base)
            scale_values = [n]
            # Many annual reports show amounts in thousands/millions while extractor stores full rupees.
            for div in [1000, 1000000, 1000000000]:
                if n != 0 and n % div == 0:
                    scale_values.append(n // div)
            for x in scale_values:
                vals.append(str(x))
                vals.append(int_commas(x))
                vals.append(f"({int_commas(x)})")
                vals.append(f"({x})")
        else:
            # Ratios/decimals.
            f = float(base)
            vals.append(f"{f:.2f}")
            vals.append(f"{f:.3f}")
            vals.append(str(base.normalize()))
    # De-duplicate and remove tiny/unsafe candidates.
    out = []
    for x in vals:
        x = clean(x)
        if not x:
            continue
        # Avoid matching 0/1/10 too broadly.
        digits = re.sub(r"\D", "", x)
        if len(digits) < 3 and "." not in x:
            continue
        if x not in out:
            out.append(x)
    return out

def normalize_for_search(text: str) -> str:
    return text.replace("\u00a0", " ").replace("−", "-").replace("–", "-").replace("—", "-")

def contains_candidate(text: str, candidates: List[str]) -> Tuple[bool, str]:
    if not text or not candidates:
        return False, ""
    t = normalize_for_search(text)
    t_nocomma = t.replace(",", "")
    for cand in candidates:
        c = normalize_for_search(cand)
        # Use raw and comma-stripped versions.
        if c in t or c.replace(",", "") in t_nocomma:
            return True, cand
    return False, ""

def verify_formula(field: str, row: Dict[str, Any]) -> Tuple[bool, str]:
    def val(f: str) -> Optional[Decimal]:
        return parse_decimal(row.get(f"Extracted_{f}") or row.get(f))
    got = parse_decimal(row.get(f"Extracted_{field}") or row.get(field))
    if got is None:
        return False, "no extracted value"
    try:
        if field == "WorkingCapital":
            a, b = val("CurrentAssets"), val("CurrentLiabilities")
            if a is not None and b is not None and got == a - b:
                return True, "formula matched: CurrentAssets - CurrentLiabilities"
        if field == "CostOfSales":
            sale, gp = val("Sales"), val("GrossProfit")
            if sale is not None and gp is not None and abs(got) == abs(sale - gp):
                return True, "formula matched: Sales - GrossProfit"
        if field == "ShareholdersEquity":
            pc, res, unp = val("PaidUpCapital"), val("Reserves"), val("UnappropriatedProfit")
            pieces = [x for x in [pc, res, unp] if x is not None]
            if pieces and got == sum(pieces):
                return True, "formula matched: PaidUpCapital + Reserves + UnappropriatedProfit"
        if field == "TotalBorrowings":
            parts = [val(f) for f in ["SubordinatedLoans", "LongTermBorrowings", "CurrentPortionLongTermLiabilities", "ShortTermBorrowings"]]
            parts = [x for x in parts if x is not None]
            if parts and got == sum(parts):
                return True, "formula matched: borrowing components total"
        if field == "CurrentRatio":
            a, b = val("CurrentAssets"), val("CurrentLiabilities")
            if a is not None and b not in (None, Decimal(0)):
                calc = a / b
                # allow rounding differences
                if abs(got - calc) <= Decimal("0.02"):
                    return True, "formula matched: CurrentAssets / CurrentLiabilities"
    except Exception as e:
        return False, f"formula check error: {type(e).__name__}: {e}"
    return False, "formula not independently re-computed"

def bucket_from_label(label: str) -> str:
    label = clean(label).upper()
    if label == "DIRECT":
        return "DIRECT"
    if label == "CALCULATED":
        return "CALCULATED"
    if label in REVIEW_LABELS:
        return "REVIEW"
    return label or "UNCLASSIFIED"

def pct(n: int, d: int) -> str:
    return "n/a" if d == 0 else f"{100*n/d:.2f}%"

def main() -> int:
    p = argparse.ArgumentParser(description="Automated PDF-backed Client-50 audit. Does not copy all extracted values as expected.")
    p.add_argument("--benchmark-dir", default="App_Data/benchmark/client_50")
    args = p.parse_args()

    root = find_project_root(Path(__file__).parent)
    bench = Path(args.benchmark_dir)
    if not bench.is_absolute():
        bench = root / bench

    labels_path = bench / "labels_client_50_combined.csv"
    if not labels_path.exists():
        raise FileNotFoundError(f"Combined labels not found: {labels_path}")

    labels = read_csv(labels_path)
    if labels:
        with labels_path.open("r", encoding="utf-8-sig", newline="") as f:
            label_fields = list(csv.DictReader(f).fieldnames or [])
    else:
        label_fields = []

    review = load_review_rows(bench)
    pdf_text_cache: Dict[str, Tuple[str, str]] = {}

    audit_rows: List[Dict[str, Any]] = []
    expected_rows: List[Dict[str, Any]] = [dict(r) for r in labels]

    counts = Counter()
    report_status = Counter(clean(r.get("Status")) or "UNKNOWN" for r in labels)
    pdf_available_reports = 0
    pdf_text_readable_reports = 0
    arithmetic_sanity_failed_reports = 0
    arithmetic_sanity_failed_symbols: List[str] = []
    arithmetic_sanity_failure_counts = Counter()
    arithmetic_sanity_advisory_reports = 0
    arithmetic_sanity_advisory_symbols: List[str] = []
    arithmetic_sanity_advisory_counts = Counter()

    for row_idx, (row, exp_row) in enumerate(zip(labels, expected_rows), start=1):
        pdf_path = resolve_pdf_path(root, row)
        pdf_key = str(pdf_path) if pdf_path else ""
        pdf_text = ""
        pdf_err = ""
        if pdf_path:
            pdf_available_reports += 1
            if pdf_key not in pdf_text_cache:
                pdf_text_cache[pdf_key] = read_pdf_text(pdf_path)
            pdf_text, pdf_err = pdf_text_cache[pdf_key]
            if pdf_text:
                pdf_text_readable_reports += 1

        # Shared arithmetic sanity guard.
        # A value appearing somewhere in the PDF is not enough. If extracted
        # values do not reconcile internally, affected fields are marked
        # unverified so wrong-period/gross-net/false-positive matches cannot
        # inflate accuracy.
        sanity = arithmetic_sanity_report({
            field: row.get(f"Extracted_{field}") or row.get(field)
            for field in VALUE_FIELDS
        })
        sanity_messages_by_field: Dict[str, str] = {}
        if sanity.get("failed_count", 0):
            arithmetic_sanity_failed_reports += 1
            for check_name, check in sanity.get("failed", {}).items():
                arithmetic_sanity_failure_counts[check_name] += 1
                check_msg = f"{check.get('name', check_name)} failed: {check.get('message', '')}"
                for affected_field in check.get("fields", []):
                    if affected_field:
                        existing = sanity_messages_by_field.get(affected_field)
                        sanity_messages_by_field[affected_field] = f"{existing} | {check_msg}" if existing else check_msg
            arithmetic_sanity_failed_symbols.append(
                f"{clean(row.get('Symbol'))} {clean(row.get('Year'))} {clean(row.get('RequestedReport'))}: {sanity.get('summary')}"
            )
        if sanity.get("advisory_count", 0):
            arithmetic_sanity_advisory_reports += 1
            for check_name, check in sanity.get("advisory", {}).items():
                arithmetic_sanity_advisory_counts[check_name] += 1
            arithmetic_sanity_advisory_symbols.append(
                f"{clean(row.get('Symbol'))} {clean(row.get('Year'))} {clean(row.get('RequestedReport'))}: {sanity.get('advisory_summary')}"
            )

        for field in VALUE_FIELDS:
            ext_col = f"Extracted_{field}"
            exp_col = f"Expected_{field}"
            src_col = f"SourcePage_{field}"
            ext = clean(row.get(ext_col))
            # ensure columns exist
            if exp_col not in label_fields:
                label_fields.append(exp_col)
            if src_col not in label_fields:
                label_fields.append(src_col)

            if not ext:
                continue

            rr = review.get(key(row, field), {})
            label = clean(rr.get("ReviewLabel")) or "UNCLASSIFIED"
            bucket = bucket_from_label(label)
            evidence = clean(rr.get("Evidence"))
            reason = clean(rr.get("ReviewReason"))

            counts[f"{bucket}_extracted"] += 1
            counts["TOTAL_extracted"] += 1

            verified = False
            expected_pdf_value = ""
            method = ""
            matched_token = ""

            if field in DATE_FIELDS:
                # Dates are handled as report metadata, not numeric value accuracy.
                verified = bool(ext)
                expected_pdf_value = ext if verified else ""
                method = "metadata/date extracted"
            elif bucket == "CALCULATED":
                ok_formula, formula_msg = verify_formula(field, row)
                if ok_formula:
                    verified = True
                    expected_pdf_value = ext
                    method = formula_msg
                else:
                    # Fallback: calculated value may also appear as a subtotal in PDF.
                    candidates = numeric_candidates(ext)
                    ok_pdf, token = contains_candidate((pdf_text or "") + "\n" + evidence, candidates)
                    verified = ok_pdf
                    expected_pdf_value = ext if ok_pdf else ""
                    method = "calculated value found in PDF/evidence" if ok_pdf else formula_msg
                    matched_token = token
            else:
                candidates = numeric_candidates(ext)
                # Direct/review values must appear in the PDF text or field-level evidence.
                ok_pdf, token = contains_candidate(pdf_text, candidates)
                ok_ev, ev_token = contains_candidate(evidence, candidates)
                if ok_pdf or ok_ev:
                    verified = True
                    expected_pdf_value = ext
                    matched_token = token or ev_token
                    method = "PDF text match" if ok_pdf else "field evidence match"
                else:
                    method = "not found in automated PDF text/evidence"

            sanity_message = sanity_messages_by_field.get(field, "")
            if sanity_message:
                verified = False
                expected_pdf_value = ""
                method = sanity_message
                matched_token = ""

            if verified:
                counts[f"{bucket}_verified"] += 1
                counts["TOTAL_verified"] += 1
                exp_row[exp_col] = expected_pdf_value
                exp_row[src_col] = method[:250]
            else:
                exp_row[exp_col] = ""
                exp_row[src_col] = method[:250]

            audit_rows.append({
                "RowNo": row_idx,
                "Batch": clean(row.get("Batch")),
                "CaseNo": clean(row.get("CaseNo")),
                "Symbol": clean(row.get("Symbol")),
                "Year": clean(row.get("Year")),
                "RequestedReport": clean(row.get("RequestedReport")),
                "ActualReportType": clean(row.get("ActualReportType")),
                "ReportStatus": clean(row.get("Status")),
                "Field": field,
                "Bucket": bucket,
                "OriginalReviewLabel": label,
                "ExtractedValue": ext,
                "ExpectedPdfValue_AutoVerified": expected_pdf_value,
                "Match": "YES" if verified else "NO",
                "VerificationMethod": method,
                "MatchedPdfToken": matched_token,
                "ReviewReason": reason,
                "Evidence": evidence,
                "CachedPdfPath": clean(row.get("CachedPdfPath")),
                "PdfFound": "YES" if pdf_path else "NO",
                "PdfTextReadable": "YES" if pdf_text else "NO",
                "PdfReadError": pdf_err,
                "PdfUrl": clean(row.get("PdfUrl")),
            })

    if "ExpectedFillMode" not in label_fields:
        label_fields.append("ExpectedFillMode")
    for r in expected_rows:
        r["ExpectedFillMode"] = "AUTO_PDF_VERIFIED_ONLY__UNVERIFIED_VALUES_LEFT_BLANK_AND_COUNTED_AS_NOT_VERIFIED"

    expected_path = bench / "labels_client_50_expected_auto_pdf_verified_only.csv"
    decisions_path = bench / "client_50_pdf_audit_decisions_long.csv"
    summary_path = bench / "client_50_pdf_audit_summary_client_ready.txt"

    write_csv(expected_path, expected_rows, label_fields)
    write_csv(decisions_path, audit_rows)

    lines: List[str] = []
    lines.append("PSX Financial Extractor - Client-50 Automated PDF Accuracy Audit")
    lines.append("")
    lines.append("Scope")
    lines.append(f"- Total benchmark reports: {len(labels)}")
    lines.append("- Benchmark design: 50 different PSX companies, mixed years 2022-2026, mixed report types Annual/Q1/Half Year/Q3.")
    lines.append("- Expected values are auto-filled only when the value is verified against PDF text, field evidence, or a deterministic formula.")
    lines.append("- Values that cannot be verified automatically are left blank in the expected column and counted as not verified.")
    lines.append("")
    lines.append("Report processing status")
    lines.append(f"- OK reports: {report_status.get('OK', 0)}")
    lines.append(f"- CHECK reports: {report_status.get('CHECK', 0)}")
    lines.append(f"- FAILED reports: {report_status.get('FAILED', 0)}")
    lines.append(f"- PDF files found locally: {pdf_available_reports}/{len(labels)}")
    lines.append(f"- PDF text readable: {pdf_text_readable_reports}/{len(labels)}")
    lines.append(f"- Arithmetic sanity failure reports: {arithmetic_sanity_failed_reports}")
    if arithmetic_sanity_failure_counts:
        lines.append("- Arithmetic sanity failure types: " + ", ".join(f"{k}={v}" for k, v in sorted(arithmetic_sanity_failure_counts.items())))
    if arithmetic_sanity_failed_symbols:
        lines.append("- Arithmetic sanity failure cases: " + " | ".join(arithmetic_sanity_failed_symbols[:20]))
    lines.append(f"- Arithmetic sanity advisory reports: {arithmetic_sanity_advisory_reports}")
    if arithmetic_sanity_advisory_counts:
        lines.append("- Arithmetic sanity advisory types: " + ", ".join(f"{k}={v}" for k, v in sorted(arithmetic_sanity_advisory_counts.items())))
    if arithmetic_sanity_advisory_symbols:
        lines.append("- Arithmetic sanity advisory cases: " + " | ".join(arithmetic_sanity_advisory_symbols[:20]))
    lines.append("")
    lines.append("Accuracy by value type")
    for b in ["DIRECT", "CALCULATED", "REVIEW", "UNCLASSIFIED"]:
        ex = counts[f"{b}_extracted"]
        if ex == 0:
            continue
        vf = counts[f"{b}_verified"]
        label_name = "Review/Needs-review" if b == "REVIEW" else b.title()
        lines.append(f"- {label_name}: {vf}/{ex} = {pct(vf, ex)}")
    lines.append("")
    total_ex = counts["TOTAL_extracted"]
    total_vf = counts["TOTAL_verified"]
    lines.append("Overall automated PDF accuracy")
    lines.append(f"- Overall: {total_vf}/{total_ex} = {pct(total_vf, total_ex)}")
    lines.append("")
    lines.append("Client-safe interpretation")
    lines.append("- Direct values are counted correct only when the extracted value is found in the PDF text or field-level PDF evidence and the shared arithmetic sanity report does not flag the field.")
    lines.append("- Shared hard arithmetic checks currently cover P&L gross profit, working capital, profit after tax, and rough shareholders equity build-up where the required fields are available.")
    lines.append("- Rough ProfitBeforeTax walk-down is reported as an advisory signal only because legitimate extra P&L lines can make the simplified formula incomplete.")
    lines.append("- Calculated values are counted correct when the formula re-computes correctly from extracted source values, or the calculated subtotal is found in the PDF/evidence.")
    lines.append("- Review values are reported separately because they may be valid candidates but need business-mapping confidence. They are not mixed into Direct accuracy.")
    lines.append("- Download failures/timeouts are reported separately under processing status and should not be treated as wrong extraction values.")
    lines.append("")
    lines.append("Files produced")
    lines.append(f"- {expected_path}")
    lines.append(f"- {decisions_path}")
    lines.append(f"- {summary_path}")

    summary_path.write_text("\n".join(lines), encoding="utf-8")

    print("Created:", expected_path)
    print("Created:", decisions_path)
    print("Created:", summary_path)
    print("Overall automated PDF accuracy:", f"{total_vf}/{total_ex} = {pct(total_vf, total_ex)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
