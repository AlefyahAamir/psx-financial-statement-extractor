from __future__ import annotations

r"""
Run PSX manifest cases from test_cases/independent_100.csv.
This avoids live discovery ambiguity and uses exact PDF URLs when present and live discovery when a URL is blank.

Run from PsxFinancialExtractor.Web:
    python .\tools\run_manifest_cases.py --manifest .\test_cases\baseline_50.csv --batch 1
    python .\tools\run_manifest_cases.py --manifest .\test_cases\baseline_50.csv --batch 2
    python .\tools\run_manifest_cases.py --manifest .\test_cases\baseline_50.csv --batch all
r"""

import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import re
import sys
import traceback
import subprocess
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

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

CSV_FIELDS = [
    "CaseNo", "Batch", "Symbol", "Year", "RequestedReport", "ActualReportType", "PeriodEnded",
    "FiscalYearEndMonth", "PeriodBasedReportCheck", "Published", "Status", "FilledFieldCount",
    "PdfUrl", "CachedPdfPath", "Title",
] + VALUE_FIELDS + ["Warnings", "EvidenceForCrossCheck", "Trace"]


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


def load_worker(root: Path):
    worker_path = root / "workers" / "psx_worker.py"
    spec = importlib.util.spec_from_file_location("psx_worker", worker_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load worker from {worker_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["psx_worker"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def make_context(worker: Any, root: Path):
    ctx = worker.RunContext(
        root=root,
        app_data=root / "App_Data",
        downloads=root / "App_Data" / "downloads",
        saved=root / "App_Data" / "saved",
    )
    ctx.app_data.mkdir(parents=True, exist_ok=True)
    ctx.downloads.mkdir(parents=True, exist_ok=True)
    ctx.saved.mkdir(parents=True, exist_ok=True)
    (root / "App_Data" / "jobs").mkdir(parents=True, exist_ok=True)
    return ctx


def period_month(period: str) -> Optional[int]:
    m = re.search(r"^\d{4}[-/](\d{1,2})[-/]\d{1,2}$", str(period or "").strip())
    return int(m.group(1)) if m else None


def expected_report_from_period(period: str, fiscal_year_end_month: Any) -> str:
    month = period_month(period)
    try:
        fye = int(float(fiscal_year_end_month))
    except Exception:
        return ""
    if not month or not (1 <= fye <= 12):
        return ""
    offset = (month - fye) % 12
    if offset == 3:
        return "Q1"
    if offset == 6:
        return "Half Year"
    if offset == 9:
        return "Q3"
    if offset == 0:
        return "Annual/FY-end"
    return ""


def cached_pdf_path(root: Path, pdf_url: str) -> str:
    if not pdf_url:
        return ""
    pdf_id = hashlib.sha1(pdf_url.encode("utf-8")).hexdigest()[:16]
    return str(root / "App_Data" / "downloads" / f"{pdf_id}.pdf")


def count_filled_values(values: Dict[str, Any]) -> int:
    return sum(1 for k in VALUE_FIELDS if values.get(k) not in (None, ""))


def short_warning_text(warnings: Any, limit: int = 600) -> str:
    if not warnings:
        return ""
    text = " | ".join(str(x) for x in warnings[:10]) if isinstance(warnings, list) else str(warnings)
    return text[:limit]


def evidence_text(evidence: Any, limit: int = 1600) -> str:
    if not isinstance(evidence, dict):
        return ""
    parts = []
    for field in VALUE_FIELDS:
        ev = evidence.get(field)
        if ev:
            parts.append(f"{field}: {ev}")
    return " | ".join(parts)[:limit]


def read_manifest(path: Path, batch: str, max_cases: int = 0) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if batch.lower() != "all":
        rows = [r for r in rows if str(r.get("Batch", "")).strip() == str(batch).strip()]
    if max_cases and max_cases > 0:
        rows = rows[:max_cases]
    return rows


def row_to_case(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": str(row.get("Symbol", "")).strip().upper(),
        "companyName": str(row.get("CompanyName", "") or row.get("Title", "") or ""),
        "year": int(float(row.get("Year", 0) or 0)),
        "requestedReport": str(row.get("RequestedReport", "") or row.get("ActualReportType", "") or ""),
        "reportType": str(row.get("ActualReportType", "") or row.get("RequestedReport", "") or ""),
        "periodEnded": str(row.get("PeriodEnded", "") or ""),
        "fiscalYearEndMonth": str(row.get("FiscalYearEndMonth", "") or ""),
        "published": str(row.get("Published", "") or ""),
        "title": str(row.get("Title", "") or ""),
        "url": str(row.get("PdfUrl", "") or ""),
    }


def normalize_report_name(value: str) -> str:
    v = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    if v in {"annual", "ann", "annual report"}:
        return "Annual"
    if v in {"q1", "first quarter", "quarter 1"}:
        return "Q1"
    if v in {"q2", "half year", "half yearly", "six months", "six month"}:
        return "Half Year"
    if v in {"q3", "third quarter", "nine months", "nine month"}:
        return "Q3"
    return (value or "").strip()


def report_matches(found_type: str, wanted_type: str) -> bool:
    f = normalize_report_name(found_type)
    w = normalize_report_name(wanted_type)
    nf = re.sub(r"[^a-z0-9]+", " ", (found_type or "").lower()).strip()
    if f == w:
        return True
    if w == "Annual" and "annual" in nf:
        return True
    if w == "Q1" and ("q1" in nf or "first" in nf):
        return True
    if w == "Half Year" and ("half" in nf or "six" in nf or "q2" in nf):
        return True
    if w == "Q3" and ("q3" in nf or "nine" in nf or "third" in nf):
        return True
    return False


def resolve_case_via_discovery(worker: Any, case: Dict[str, Any]) -> Dict[str, Any]:
    """When a manifest row has no exact PdfUrl, discover the requested report live.

    Exact PdfUrl rows remain deterministic. Blank-url rows allow the clean
    benchmark to include more companies while still enforcing the manifest's
    company/year/report-type mix.
    """
    if str(case.get("url") or "").strip():
        return case
    symbol = str(case.get("symbol") or "").strip().upper()
    year = int(case.get("year") or 0)
    wanted = str(case.get("requestedReport") or case.get("reportType") or "").strip()
    result = worker.discover_reports({"symbol": symbol, "companyName": case.get("companyName", ""), "year": year, "reportType": "All"})
    reports = result.get("reports") or []
    matches = [r for r in reports if report_matches(str(r.get("reportType") or r.get("title") or ""), wanted)]
    if not matches:
        raise RuntimeError(f"No discovered {wanted} report for {symbol} {year}. Found {len(reports)} report(s).")
    chosen = matches[0]
    case = dict(case)
    case["reportType"] = str(chosen.get("reportType") or wanted)
    case["periodEnded"] = str(chosen.get("periodEnded") or case.get("periodEnded") or "")
    case["fiscalYearEndMonth"] = str(chosen.get("fiscalYearEndMonth") or case.get("fiscalYearEndMonth") or "")
    case["published"] = str(chosen.get("published") or case.get("published") or "")
    case["title"] = str(chosen.get("title") or case.get("title") or f"{symbol} {year} {wanted}")
    case["url"] = str(chosen.get("url") or "")
    if not case["url"]:
        raise RuntimeError(f"Discovered report for {symbol} {year} {wanted} had no PDF URL.")
    return case



def run_extract_subprocess(root: Path, payload: Dict[str, Any], timeout_seconds: int = 420) -> Dict[str, Any]:
    """Run the worker extraction in a child process so one slow PDF can time out
    and the manifest continues to the next case. This is important on Windows
    where Ctrl+C can fail inside VS Code while curl/OCR/PyMuPDF is busy.
    """
    jobs = root / "App_Data" / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S%f")
    input_path = jobs / f"manifest_case_{stamp}_input.json"
    output_path = jobs / f"manifest_case_{stamp}_output.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    script = root / "workers" / "psx_worker.py"
    cmd = [sys.executable, str(script), "extract", "--input", str(input_path), "--output", str(output_path), "--root", str(root)]
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    completed = subprocess.run(cmd, cwd=str(root), text=True, capture_output=True, timeout=timeout_seconds, env=env)
    if output_path.exists():
        data = json.loads(output_path.read_text(encoding="utf-8"))
        if completed.returncode != 0 and data.get("ok") is not False:
            data["ok"] = False
        return data
    raise RuntimeError(f"Worker produced no output JSON. exit={completed.returncode}; stderr={completed.stderr[-1200:]} stdout={completed.stdout[-800:]}")

def extract_manifest(worker: Any, ctx: Any, root: Path, manifest_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, manifest_row in enumerate(manifest_rows, start=1):
        case = row_to_case(manifest_row)
        symbol = case["symbol"]
        year = case["year"]
        requested = case["requestedReport"]
        try:
            case = resolve_case_via_discovery(worker, case)
        except Exception as disc_exc:
            print(f"\nEXTRACT {idx}/{len(manifest_rows)}: {symbol} {year} {requested}")
            print(f"DISCOVERY FAILED: {type(disc_exc).__name__}: {disc_exc}")
            out.append({
                "CaseNo": idx,
                "Batch": manifest_row.get("Batch", ""),
                "Symbol": symbol,
                "Year": year,
                "RequestedReport": requested,
                "ActualReportType": manifest_row.get("ActualReportType", ""),
                "PeriodEnded": manifest_row.get("PeriodEnded", ""),
                "FiscalYearEndMonth": manifest_row.get("FiscalYearEndMonth", ""),
                "PeriodBasedReportCheck": "",
                "Published": manifest_row.get("Published", ""),
                "Title": manifest_row.get("Title", ""),
                "PdfUrl": manifest_row.get("PdfUrl", ""),
                "CachedPdfPath": "",
                "Status": "FAILED",
                "FilledFieldCount": 0,
                "Warnings": f"Discovery failed: {type(disc_exc).__name__}: {disc_exc}",
                "EvidenceForCrossCheck": "",
                "Trace": traceback.format_exc(),
                **{field: "" for field in VALUE_FIELDS},
            })
            continue
        actual = case["reportType"]
        print(f"\nEXTRACT {idx}/{len(manifest_rows)}: {symbol} {year} {requested} ({actual})")
        print(f"PDF: {case.get('url', '')}")

        row: Dict[str, Any] = {
            "CaseNo": idx,
            "Batch": manifest_row.get("Batch", ""),
            "Symbol": symbol,
            "Year": year,
            "RequestedReport": requested,
            "ActualReportType": actual,
            "PeriodEnded": case.get("periodEnded", ""),
            "FiscalYearEndMonth": case.get("fiscalYearEndMonth", ""),
            "PeriodBasedReportCheck": expected_report_from_period(str(case.get("periodEnded", "")), case.get("fiscalYearEndMonth", "")),
            "Published": case.get("published", ""),
            "Title": case.get("title", ""),
            "PdfUrl": case.get("url", ""),
            "CachedPdfPath": cached_pdf_path(root, str(case.get("url", ""))),
            "Status": "NOT_RUN",
            "FilledFieldCount": 0,
            "Warnings": "",
            "EvidenceForCrossCheck": "",
            "Trace": "",
        }
        try:
            result = run_extract_subprocess(root, {
                "symbol": symbol,
                "companyName": case.get("companyName", ""),
                "year": year,
                "reports": [case],
            }, timeout_seconds=int(os.getenv("PSX_CASE_TIMEOUT_SECONDS", "420")))
            reports = result.get("reports") or []
            extracted = reports[0] if reports else {}
            values = extracted.get("values") or {}
            row["FilledFieldCount"] = count_filled_values(values)
            row["Warnings"] = short_warning_text(extracted.get("warnings") or extracted.get("warning") or result.get("warnings"))
            row["EvidenceForCrossCheck"] = evidence_text(extracted.get("evidence") or {})
            warn_lower = str(row["Warnings"] or "").lower()
            hard_review_terms = [
                "extraction failed", "tesseractnotfounderror", "ocr fallback skipped/failed",
                "ocr recovery unavailable/failed", "manual review", "needs manual review",
                "rejected likely", "scanned pdf", "embedded text was unavailable",
                "pdf text is too short", "poor parse", "could not resolve host", "download failed",
            ]
            filled = int(row.get("FilledFieldCount") or 0)
            if not extracted or filled == 0 or any(term in warn_lower for term in hard_review_terms):
                row["Status"] = "CHECK"
            elif filled >= 24:
                row["Status"] = "OK"
            else:
                row["Status"] = "CHECK"
            for field in VALUE_FIELDS:
                row[field] = values.get(field, "")
            row["_fullExtractedReport"] = extracted
            print(f"RESULT: {row['Status']}; filled fields={row['FilledFieldCount']}")
        except Exception as exc:
            row["Status"] = "FAILED"
            row["Warnings"] = f"{type(exc).__name__}: {exc}"
            row["Trace"] = traceback.format_exc()
            print(f"FAILED: {type(exc).__name__}: {exc}")
            print(row["Trace"])
        out.append(row)
    return out


def save_outputs(root: Path, extracted_rows: List[Dict[str, Any]], args: argparse.Namespace) -> Dict[str, Path]:
    jobs = root / "App_Data" / "jobs"
    bench = root / "App_Data" / "benchmark"
    jobs.mkdir(parents=True, exist_ok=True)
    bench.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = args.output_prefix or f"manifest_{args.batch}"
    csv_path = jobs / f"{prefix}_{stamp}.csv"
    json_path = jobs / f"{prefix}_{stamp}.json"
    latest_csv = jobs / f"{prefix}_latest.csv"
    latest_json = jobs / f"{prefix}_latest.json"
    generic_latest_csv = jobs / "other_company_stats_latest.csv"
    generic_latest_json = jobs / "other_company_stats_latest.json"

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(extracted_rows)
    text = csv_path.read_text(encoding="utf-8-sig")
    latest_csv.write_text(text, encoding="utf-8-sig")
    generic_latest_csv.write_text(text, encoding="utf-8-sig")

    full = {
        "createdOn": dt.datetime.now().isoformat(timespec="seconds"),
        "projectRoot": str(root),
        "args": vars(args),
        "extractedRows": extracted_rows,
        "note": "Manifest runner uses exact PDF URLs when present and live discovery when PdfUrl is blank. CSV is for cross-check; JSON contains full evidence.",
    }
    json_text = json.dumps(full, indent=2, ensure_ascii=False, default=str)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    generic_latest_json.write_text(json_text, encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "latest_csv": latest_csv, "latest_json": latest_json}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run exact fixed PSX benchmark cases from a manifest.")
    p.add_argument("--manifest", default="test_cases/independent_100.csv", help="Manifest CSV path")
    p.add_argument("--batch", default="all", help="1, 2, or all")
    p.add_argument("--max-cases", type=int, default=0, help="Limit number of cases; 0 = no limit")
    p.add_argument("--output-prefix", default="", help="Output file prefix")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = find_project_root(Path(__file__).parent)
    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        manifest = root / manifest
    if not manifest.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest}")
    worker = load_worker(root)
    ctx = make_context(worker, root)
    rows = read_manifest(manifest, args.batch, args.max_cases)
    if not rows:
        print(f"No manifest rows found for batch={args.batch}")
        return 2
    print("=" * 90)
    print("PSX EXACT MANIFEST TEST")
    print("Project root:", root)
    print("Manifest:", manifest)
    print("Batch:", args.batch)
    print("Cases:", len(rows))
    print("=" * 90)
    extracted = extract_manifest(worker, ctx, root, rows)
    paths = save_outputs(root, extracted, args)
    ok = sum(1 for r in extracted if r.get("Status") == "OK")
    check = sum(1 for r in extracted if r.get("Status") == "CHECK")
    failed = sum(1 for r in extracted if r.get("Status") == "FAILED")
    print("\n" + "=" * 90)
    print("DONE")
    print("Cases:", len(extracted))
    print("OK:", ok, "CHECK:", check, "FAILED:", failed)
    print("CSV:", paths["csv"])
    print("JSON:", paths["json"])
    print("Latest CSV:", paths["latest_csv"])
    print("For PDF cross-check, use PdfUrl, CachedPdfPath, EvidenceForCrossCheck and the extracted fields.")
    print("=" * 90)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
