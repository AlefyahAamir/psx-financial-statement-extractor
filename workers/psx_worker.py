#!/usr/bin/env python3
"""PSX financial statement extraction worker.

This module contains the command-line entry points used by the web app. The
financial extraction logic lives in the workers/extraction package.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import html
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
# Allow this file to be run as a script while using repo-root package imports.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from workers.extraction.pipeline import extract_values_from_content as modular_extract_values_from_content
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, urlunparse


# Shared validation utilities live in tools/ so both the worker and audit scripts use
# the same arithmetic sanity rules.
_TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
try:
    from arithmetic_sanity import arithmetic_sanity_report, sanity_failed_for_fields
except Exception:  # keep worker import-safe even if copied without tools folder
    def arithmetic_sanity_report(values):  # type: ignore
        return {"checks": {}, "failed": {}, "passed": True, "failed_count": 0, "failed_fields": [], "summary": ""}
    def sanity_failed_for_fields(values, fields):  # type: ignore
        return None


def dbg(msg: str) -> None:
    import sys, datetime
    print(f"[PYWORKER {datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

BASE_URL = "https://financials.psx.com.pk/"
BASE_HOST = "financials.psx.com.pk"
DOWNLOAD_MARKER = "DownloadPDF.php"

BALNSHET_FIELDS = [
    "TransactionNumber", "CompanyCode", "FinancialYear", "PeriodEndDate", "PaidUpCapital", "Reserves", "UnappropriatedProfit", "ShareholdersEquity",
    "CurrentAssets", "CashAndBankBalances", "AdvancesAndReceivables", "FixedAssets", "LongTermLiabilities", "OtherLongTermLiabilities", "OtherLiabilities", "WorkingCapital",
    "Sales", "CostOfSales", "GrossProfit", "OperatingExpenses", "FinanceCosts", "OtherIncome", "OtherCharges", "ProfitBeforeTax",
    "Taxation", "ProfitAfterTax", "RevaluationSurplus", "CurrentRatio", "DebtRatio", "BreakupValue", "SubordinatedLoans",
    "LongTermBorrowings", "CurrentLiabilities", "CurrentPortionLongTermLiabilities", "ShortTermBorrowings", "TotalBorrowings", "TradeDebts", "StockInTrade", "StoresAndSpares",
    "ShortTermInvestments", "LongTermInvestments", "OtherFixedAssets", "LeaseFinance", "TradeAndOtherPayables", "CashFlowFromOperatingActivities", "CashFlowFromFinancingActivities", "CashFlowFromInvestingActivities",
    "DeferredLiabilities", "FinanceLeaseObligations", "OperatingLeaseObligations", "AmountMultiplier", "CurrentLeaseFinance", "DepreciationProvision", "OperatingProfit",
]

# Client-facing DB metadata columns added to dbo.BalnShet for this project.
# CompanyCode is deliberately omitted from inserts/updates because the user chose
# to store company identity by Symbol in the same BalnShet table.
BALNSHET_DB_METADATA_FIELDS = ["Symbol", "ReportType", "PdfUrl", "ExtractionStatus"]
BALNSHET_DB_FIELDS = ["Symbol", "FinancialYear", "PeriodEndDate", "ReportType", "PdfUrl", "ExtractionStatus"] + [
    f for f in BALNSHET_FIELDS
    if f not in {"TransactionNumber", "CompanyCode", "FinancialYear", "PeriodEndDate",}
]

IMPORTANT_FIELDS = [
    "PaidUpCapital", "Reserves", "UnappropriatedProfit", "ShareholdersEquity", "CurrentAssets", "CashAndBankBalances", "FixedAssets",
    "CurrentLiabilities", "Sales", "FinanceCosts", "ProfitBeforeTax", "Taxation", "ProfitAfterTax",
    "CashFlowFromOperatingActivities", "CashFlowFromInvestingActivities", "CashFlowFromFinancingActivities"
]

# Number pattern supports 1,234, (1,234), -1,234, 1234 and decimals.
NUM_RE = re.compile(r"\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?")
DATE_LIKE_RE = re.compile(r"\b(?:19|20)\d{2}\b")


@dataclass
class RunContext:
    root: Path
    app_data: Path
    downloads: Path
    saved: Path


@dataclass
class PdfContent:
    text: str
    lines: List[str]
    table_lines: List[str]
    pages: int
    table_count: int
    pdf_path: str = ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["reports", "extract", "save", "companies"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()

    ctx = RunContext(
        root=Path(args.root),
        app_data=Path(args.root) / "App_Data",
        downloads=Path(args.root) / "App_Data" / "downloads",
        saved=Path(args.root) / "App_Data" / "saved",
    )
    ctx.downloads.mkdir(parents=True, exist_ok=True)
    ctx.saved.mkdir(parents=True, exist_ok=True)

    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        if args.command == "reports":
            result = discover_reports(payload)
        elif args.command == "extract":
            result = extract_reports(payload, ctx)
        elif args.command == "save":
            result = save_reports(payload, ctx)
        else:
            result = scrape_companies(payload, ctx)
        write_json(args.output, result)
        return 0 if result.get("ok", False) else 2
    except Exception as exc:
        write_json(args.output, {"ok": False, "error": str(exc), "trace": traceback.format_exc()})
        return 1


def write_json(path: str, value: Dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


# ----------------------------- HTTP helpers -----------------------------

def _requests_session():
    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": BASE_URL,
    })
    return session


def http_get(url: str) -> str:
    session = _requests_session()
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": BASE_URL,
    }
    response = session.get(url, headers=headers, timeout=60, allow_redirects=True)
    response.raise_for_status()
    # Some PSX pages do not return a charset header. Let requests guess, then fallback.
    if not response.encoding:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def http_post(url: str, data: Dict[str, Any]) -> str:
    session = _requests_session()
    headers = {
        "Accept": "text/html,application/json,text/javascript,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL,
    }
    response = session.post(url, headers=headers, data=data, timeout=60, allow_redirects=True)
    response.raise_for_status()
    if not response.encoding:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def http_bytes(url: str) -> bytes:
    """Robust PDF downloader for PSX.

    PSX DownloadPDF.php sometimes fails with Python requests and HTTP/1.0 can
    create a broken %PDF file that PyMuPDF opens with 0 pages. So this uses curl
    HTTP/1.1 first and validates that the downloaded PDF has at least one page.
    """
    import subprocess
    import tempfile
    import time
    from pathlib import Path

    last_error = None

    def _valid_pdf(data: bytes) -> bool:
        if not data or not data.lstrip().startswith(b"%PDF") or len(data) <= 1000:
            return False
        try:
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            pages = len(doc)
            doc.close()
            return pages > 0
        except Exception:
            return False

    def _curl_download(http_mode: str) -> bytes:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name

        cmd = [
            "curl.exe",
            "-L",
            http_mode,
            "--retry", "3",
            "--connect-timeout", "20",
            "--max-time", "300",
            "-A", "Mozilla/5.0",
            "-e", BASE_URL,
            url,
            "-o", tmp_path,
        ]

        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=340,
        )

        data = Path(tmp_path).read_bytes() if Path(tmp_path).exists() else b""

        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

        if _valid_pdf(data):
            return data

        raise RuntimeError(
            f"curl {http_mode} failed or invalid PDF. exit={completed.returncode}; "
            f"bytes={len(data)}; first={data[:30]!r}; stderr={completed.stderr[-500:]}"
        )

    if "financials.psx.com.pk" in url and "DownloadPDF.php" in url:
        for mode in ["--http1.1", "--http1.0"]:
            try:
                dbg(f"Trying curl {mode} download")
                return _curl_download(mode)
            except Exception as exc:
                last_error = exc
                dbg(f"curl {mode} failed: {exc}")

    headers = {
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "User-Agent": "Mozilla/5.0",
        "Referer": BASE_URL,
    }

    for attempt in range(1, 4):
        chunks = []
        try:
            session = _requests_session()
            response = session.get(
                url,
                headers=headers,
                timeout=(20, 180),
                allow_redirects=True,
                stream=True,
            )
            response.raise_for_status()

            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    chunks.append(chunk)

            data = b"".join(chunks)

            if _valid_pdf(data):
                return data

            last_error = RuntimeError(
                f"requests returned invalid PDF. bytes={len(data)}; first={data[:30]!r}"
            )

        except Exception as exc:
            last_error = exc

        time.sleep(1.5 * attempt)

    raise RuntimeError(f"Failed to download readable PDF from {url}. Last error: {last_error}")



# ----------------------------- report discovery -----------------------------

def discover_reports(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Discover reports using the real PSX AJAX endpoint.

    The financials home page does not contain direct PDF links. Its JavaScript calls:
        POST annQtrStmts.php with name=get_comp_y_data, smbCode=<symbol>, year=<year>
    That endpoint returns JSON rows containing lib/DownloadPDF.php?id=...
    """
    symbol = str(payload.get("symbol") or "").strip().upper()
    company = str(payload.get("companyName") or "").strip()
    year = int(payload.get("year") or datetime.now().year)
    requested_type = str(payload.get("reportType") or "All").strip()

    diagnostics: List[str] = []

    def _clean_json_text(text: str) -> str:
        text = (text or "").strip()
        first_array = text.find("[")
        last_array = text.rfind("]")
        if first_array >= 0 and last_array >= first_array:
            return text[first_array:last_array + 1]
        return text

    def _resolve_symbol_from_company() -> str:
        if symbol:
            return symbol
        if not company:
            return ""
        try:
            page = http_get(f"{BASE_URL}?year={year}")
            wanted = normalize(company)
            best_code = ""
            best_score = 0
            for m in re.finditer(r'<option\s+value=["\']([^"\']+)["\']>(.*?)</option>', page, re.I | re.S):
                code = html.unescape(m.group(1)).strip().upper()
                name = clean_text(html.unescape(re.sub(r"<.*?>", " ", m.group(2))))
                nname = normalize(name)
                if not code or not nname:
                    continue
                if nname == wanted:
                    diagnostics.append(f"Resolved company name to PSX symbol {code} using exact dropdown match.")
                    return code
                wanted_tokens = [t for t in wanted.split() if len(t) > 2]
                score = sum(1 for t in wanted_tokens if t in nname)
                if score > best_score:
                    best_score = score
                    best_code = code
            if best_code and best_score >= 2:
                diagnostics.append(f"Resolved company name to likely PSX symbol {best_code} using dropdown token match.")
                return best_code
        except Exception as exc:
            diagnostics.append(f"Could not resolve symbol from company dropdown: {type(exc).__name__}: {exc}")
        return ""

    def _anchor_text(anchor_html: str) -> str:
        m = re.search(r">\s*([^<]+?)\s*</a>", anchor_html or "", re.I | re.S)
        return clean_text(html.unescape(m.group(1))) if m else "Report"

    def _anchor_href(anchor_html: str) -> str:
        m = re.search(r"href\s*=\s*[\"']([^\"']+)[\"']", anchor_html or "", re.I)
        return html.unescape(m.group(1)).strip() if m else ""

    def _period_month(period: str) -> Optional[int]:
        """Return period-end month from common PSX date formats.

        PSX rows normally use yyyy-mm-dd, but older/fallback rows can contain
        30-Jun-2025 or 30 June 2025.  We use this month together with the
        company fiscal year-end to classify Q1 / Half Year / Q3 correctly.
        """
        s = str(period or "").strip()
        if not s:
            return None

        m = re.search(r"^\d{4}[-/](\d{1,2})[-/]\d{1,2}$", s)
        if m:
            try:
                month = int(m.group(1))
                return month if 1 <= month <= 12 else None
            except Exception:
                return None

        month_names = {
            "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
            "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
            "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
            "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
        }
        m = re.search(r"(?:\d{1,2}[-/ ]+)?([A-Za-z]{3,9})(?:[-/ ]+\d{1,2})?(?:[-/ ,]+\d{4})?", s)
        if m:
            return month_names.get(m.group(1).lower())
        return None

    def _period_year(period: str) -> Optional[int]:
        """Return the year from common PSX period formats.

        PSX Annual rows often contain only "2025" instead of "2025-12-31".
        We use the year together with the annual report posting date to infer
        whether the company is calendar-year or June-year-end.
        """
        s = str(period or "").strip()
        m = re.search(r"^(\d{4})(?:[-/]\d{1,2}[-/]\d{1,2})?$", s)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        m = re.search(r"\b(20\d{2}|19\d{2})\b", s)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        return None

    def _parse_psx_date(value: str) -> Optional[datetime]:
        s = str(value or "").strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass
        return None

    def _infer_fiscal_year_end_month(items: List[Dict[str, str]]) -> int:
        """Infer company fiscal year-end month from the PSX result set.

        Key fix in fiscal-year inference:
        Annual rows from financials.psx.com.pk often show period_ended as only
        a year, e.g. "2025". safety cleanup could not infer a month from that and fell
        back to June for every company. That still mislabelled calendar-year
        companies such as banks, EFERT, FATIMA, and SYS.

        Heuristic used when Annual period has no month:
        - Annual for period 2025 posted in Jan-May 2026 => December year-end.
        - Annual for period 2025 posted in Jul-Dec 2025 => June year-end.
        This matches the PSX filing patterns seen in the test pack.
        """
        inferred_months: List[int] = []

        for item in items:
            label = normalize(item.get("label") or "")
            period = item.get("period") or ""
            posted = item.get("posted") or ""
            if "annual" not in label:
                continue

            month = _period_month(period)
            if month:
                inferred_months.append(month)
                continue

            period_year = _period_year(period)
            posted_dt = _parse_psx_date(posted)
            if period_year and posted_dt:
                # Calendar-year annual reports are normally posted early in the
                # following year, e.g. Annual 2025 posted in Mar 2026.
                if posted_dt.year > period_year and 1 <= posted_dt.month <= 5:
                    inferred_months.append(12)
                    continue

                # June-year-end annual reports are normally posted Sep/Oct of
                # the same calendar year, e.g. Annual 2024 posted in Oct 2024.
                if posted_dt.year == period_year and 7 <= posted_dt.month <= 12:
                    inferred_months.append(6)
                    continue

        if inferred_months:
            return max(set(inferred_months), key=inferred_months.count)

        # Fallback from explicit quarterly labels, if PSX provides them.
        for item in items:
            label = normalize(item.get("label") or "")
            month = _period_month(item.get("period") or "")
            if not month:
                continue

            def month_minus(delta: int) -> int:
                return ((month - delta - 1) % 12) + 1

            if "first" in label or "1st" in label:
                inferred_months.append(month_minus(3))
            elif "half" in label or "six" in label:
                inferred_months.append(month_minus(6))
            elif "nine" in label or "third" in label or "3rd" in label:
                inferred_months.append(month_minus(9))

        if inferred_months:
            return max(set(inferred_months), key=inferred_months.count)

        # Conservative final fallback.
        return 6

    def _classify_psx(label: str, period: str, fiscal_year_end_month: int) -> str:
        nlabel = normalize(label)
        if "annual" in nlabel:
            return "Annual"

        # Prefer explicit wording if PSX ever provides it.
        if "half" in nlabel or "six" in nlabel:
            return "Half Year"
        if "nine" in nlabel or "third" in nlabel or "3rd" in nlabel:
            return "Q3 / Nine Months"
        if "first" in nlabel or "1st" in nlabel:
            return "Q1"

        period_month = _period_month(period)
        if period_month:
            offset = (period_month - fiscal_year_end_month) % 12
            if offset == 3:
                return "Q1"
            if offset == 6:
                return "Half Year"
            if offset == 9:
                return "Q3 / Nine Months"
            if offset == 0 and ("annual" in nlabel or "year" in nlabel):
                return "Annual"

        if "quarter" in nlabel:
            return "Quarter Report"
        return classify_report_type(label + " " + period, period, year)

    smb_code = _resolve_symbol_from_company()
    if not smb_code:
        return {
            "ok": True,
            "sourceLockedTo": BASE_URL,
            "symbol": symbol,
            "companyName": company,
            "year": year,
            "reports": [],
            "diagnostics": [
                "No PSX symbol was supplied and symbol could not be resolved from company name.",
                "Enter the exact PSX symbol, for example OGDC, ABL, MEBL, PSO."
            ] + diagnostics,
            "note": "Only financials.psx.com.pk and its DownloadPDF.php PDF links are used."
        }

    endpoint = urljoin(BASE_URL, "annQtrStmts.php")
    session = _requests_session()
    headers = {
        "Accept": "application/json,text/javascript,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE_URL}?year={year}",
    }

    reports: List[Dict[str, Any]] = []

    try:
        response = session.post(
            endpoint,
            headers=headers,
            data={
                "name": "get_comp_y_data",
                "smbCode": smb_code,
                "year": str(year),
            },
            timeout=30,
            allow_redirects=True,
        )
        response.raise_for_status()
        raw = response.text or ""

        diagnostics.append(
            f"POST annQtrStmts.php get_comp_y_data smbCode={smb_code}, year={year}; "
            f"status={response.status_code}; chars={len(raw)}; DownloadPDF count={raw.count('DownloadPDF.php')}"
        )

        rows = json.loads(_clean_json_text(raw))
        if not isinstance(rows, list):
            rows = []

        parsed_items: List[Dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue

            reports_html = str(row.get("Reports") or "")
            href = _anchor_href(reports_html)
            if not href:
                continue

            full_url = normalize_report_url(urljoin(BASE_URL, href))
            if not is_psx_pdf_url(full_url):
                continue

            parsed_items.append({
                "label": _anchor_text(reports_html),
                "period": str(row.get("period_ended") or "").strip(),
                "posted": str(row.get("posting_date") or "").strip(),
                "url": full_url,
            })

        fiscal_year_end_month = _infer_fiscal_year_end_month(parsed_items)
        diagnostics.append(
            f"Detected fiscal year-end month={fiscal_year_end_month}; "
            "quarter classification uses period_end relative to this month."
        )

        for item in parsed_items:
            label = item["label"]
            period = item["period"]
            posted = item["posted"]
            full_url = item["url"]
            report_type = _classify_psx(label, period, fiscal_year_end_month)

            title_parts = [smb_code, label]
            if period:
                title_parts.append(f"period ended {period}")
            if posted:
                title_parts.append(f"posted {posted}")

            reports.append({
                "id": hashlib.sha1(full_url.encode("utf-8")).hexdigest()[:16],
                "reportType": report_type,
                "title": " - ".join(title_parts),
                "periodEnded": period,
                "published": posted,
                "url": full_url,
                "source": endpoint,
                "fiscalYearEndMonth": fiscal_year_end_month,
            })

    except Exception as exc:
        diagnostics.append(f"PSX AJAX get_comp_y_data failed: {type(exc).__name__}: {exc}")

    reports = dedupe_and_rank_reports(reports, smb_code, company, year)
    reports = filter_reports_by_requested_type(reports, requested_type)

    no_reports_found = not reports
    user_message = ""
    if no_reports_found:
        user_message = f"No reports found for {smb_code} in {year}."
        diagnostics.append(
            user_message + " Confirm the PSX symbol/year exists on financials.psx.com.pk or try another year."
        )

    return {
        "ok": True,
        "sourceLockedTo": BASE_URL,
        "symbol": smb_code,
        "companyName": company,
        "year": year,
        "reports": reports,
        "noReportsFound": no_reports_found,
        "message": user_message,
        "diagnostics": diagnostics[:80],
        "note": "Only financials.psx.com.pk annQtrStmts.php and its DownloadPDF.php PDF links are used."
    }


def build_discovery_urls(year: int, symbol: str, company: str) -> List[str]:
    """Try common PSX URL shapes without assuming one fixed route."""
    encoded_company = quote_plus(company) if company else ""
    encoded_symbol = quote_plus(symbol) if symbol else ""
    urls = [
        BASE_URL,
        f"{BASE_URL}?year={year}",
        f"{BASE_URL}index.php?year={year}",
        f"{BASE_URL}?yr={year}",
        f"{BASE_URL}?search={encoded_company or encoded_symbol}&year={year}",
        f"{BASE_URL}index.php?search={encoded_company or encoded_symbol}&year={year}",
    ]
    # Preserve order while de-duping.
    seen = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def try_discovery_form_posts(html_text: str, page_url: str, symbol: str, company: str, year: int, diagnostics: List[str]) -> List[Dict[str, Any]]:
    """Some PHP pages only return reports after POST. This tries safe generic form submissions."""
    from bs4 import BeautifulSoup

    search_value = company or symbol
    if not search_value:
        return []

    soup = BeautifulSoup(html_text, "html.parser")
    forms = soup.find_all("form")
    found: List[Dict[str, Any]] = []

    generic_payloads = [
        {"year": year, "company": search_value, "symbol": symbol, "search": search_value},
        {"year": year, "companyName": search_value, "symbol": symbol},
        {"yr": year, "company": search_value, "symbol": symbol},
    ]

    # Try actual forms first.
    for form in forms[:4]:
        try:
            action = form.get("action") or page_url
            method = (form.get("method") or "get").lower()
            target = urljoin(page_url, action)
            data: Dict[str, Any] = {}
            for inp in form.find_all(["input", "select"]):
                name = inp.get("name")
                if not name:
                    continue
                lname = name.lower()
                if "year" in lname or lname in {"yr", "reportyear"}:
                    data[name] = str(year)
                elif "company" in lname or "symbol" in lname or "search" in lname or "name" in lname:
                    data[name] = search_value
                else:
                    data[name] = inp.get("value") or ""
            if not data:
                continue
            if method == "post":
                response_text = http_post(target, data)
            else:
                query = "&".join(f"{quote_plus(str(k))}={quote_plus(str(v))}" for k, v in data.items())
                sep = "&" if "?" in target else "?"
                response_text = http_get(target + sep + query)
            links = extract_report_links_from_html(response_text, target, symbol, company, year)
            diagnostics.append(f"Tried form {method.upper()} {target}; candidate PDF links: {len(links)}")
            found.extend(links)
        except Exception as exc:
            diagnostics.append(f"Form discovery failed: {type(exc).__name__}: {exc}")

    # Try generic posts to base/index only if no forms helped.
    if not found:
        for target in [BASE_URL, urljoin(BASE_URL, "index.php")]:
            for data in generic_payloads:
                try:
                    response_text = http_post(target, data)
                    links = extract_report_links_from_html(response_text, target, symbol, company, year)
                    diagnostics.append(f"Tried generic POST {target} with keys {list(data.keys())}; candidate PDF links: {len(links)}")
                    found.extend(links)
                    if links:
                        return found
                except Exception as exc:
                    diagnostics.append(f"Generic POST failed {target}: {type(exc).__name__}: {exc}")
    return found


def extract_report_links_from_html(html_text: str, page_url: str, symbol: str, company: str, year: int) -> List[Dict[str, Any]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    found: List[Dict[str, Any]] = []

    # 1) Normal anchor tags.
    for a in soup.find_all("a"):
        href = html.unescape(a.get("href") or "").strip()
        if not href:
            continue
        full = normalize_report_url(urljoin(page_url, href))
        if not is_psx_pdf_url(full):
            continue

        context = best_anchor_context(a, href)
        if not looks_like_company_context(context, symbol, company):
            continue
        title = clean_text(a.get_text(" ", strip=True) or infer_title_from_context(context))
        found.append(make_report(full, title, context, page_url, year))

    # 2) URLs embedded in scripts or escaped HTML fragments.
    raw = html.unescape(html_text)
    url_patterns = [
        r"https?://financials\.psx\.com\.pk/[^'\"<>\s)]+DownloadPDF\.php\?id=[^'\"<>\s)]+",
        r"/[^'\"<>\s)]+DownloadPDF\.php\?id=[^'\"<>\s)]+",
        r"lib/DownloadPDF\.php\?id=[^'\"<>\s)]+",
        r"DownloadPDF\.php\?id=[^'\"<>\s)]+",
    ]
    for pat in url_patterns:
        for match in re.finditer(pat, raw, re.I):
            full = normalize_report_url(urljoin(page_url, match.group(0)))
            if not is_psx_pdf_url(full):
                continue
            around = raw[max(0, match.start() - 900): match.end() + 900]
            context = clean_text(BeautifulSoup(around, "html.parser").get_text(" ", strip=True) or around)
            if not looks_like_company_context(context, symbol, company):
                continue
            found.append(make_report(full, infer_title_from_context(context), context, page_url, year))

    return found


def normalize_report_url(url: str) -> str:
    url = html.unescape(url).strip().strip("'\"")
    parsed = urlparse(url)
    # Remove fragments; keep query id.
    parsed = parsed._replace(fragment="")
    return urlunparse(parsed)


def is_psx_pdf_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if BASE_HOST not in host:
        return False
    return DOWNLOAD_MARKER.lower() in path or path.endswith(".pdf")


def best_anchor_context(anchor: Any, href: str) -> str:
    """Get the table row/card around a link, not only the text 'Download File'."""
    parts: List[str] = []
    for attr in ["title", "aria-label", "data-title", "data-original-title"]:
        val = anchor.get(attr)
        if val:
            parts.append(str(val))
    parts.append(anchor.get_text(" ", strip=True))

    parent = anchor
    for _ in range(8):
        if parent is None:
            break
        text = parent.get_text(" ", strip=True)
        if text:
            parts.append(text)
        # A tr/card/list item/div with enough text is usually the right context.
        if getattr(parent, "name", "") in {"tr", "li"} and len(text) > 20:
            break
        if len(text) > 250:
            break
        parent = parent.parent

    parts.append(href)
    return clean_text(" | ".join(parts))


def infer_title_from_context(context: str) -> str:
    c = clean_text(context)
    # Keep enough to be useful, but avoid dumping the whole page.
    if len(c) <= 180:
        return c
    # Prefer phrase around report words.
    m = re.search(r"((?:annual|quarterly|half yearly|half-yearly|nine months|interim).{0,100}report.{0,80})", c, re.I)
    if m:
        return clean_text(m.group(1))[:180]
    return c[:180]


def looks_like_company_context(text: str, symbol: str, company: str) -> bool:
    norm = normalize(text)
    if not symbol and not company:
        return True

    if symbol:
        # Symbol usually appears as a separate table cell. Exact-ish token match prevents false matches.
        if re.search(r"(?:^|[^a-z0-9])" + re.escape(symbol.lower()) + r"(?:$|[^a-z0-9])", text.lower()):
            return True

    company_tokens = important_company_tokens(company)
    if company_tokens:
        hit_count = sum(1 for token in company_tokens if token in norm)
        # For long names, 2-3 meaningful tokens are enough; for short names, require all.
        needed = min(3, len(company_tokens)) if len(company_tokens) >= 3 else len(company_tokens)
        if hit_count >= needed:
            return True

    # If context is a PDF URL only, no company information is available; reject it for safety.
    return False


def important_company_tokens(company: str) -> List[str]:
    stop = {
        "limited", "ltd", "company", "co", "pakistan", "the", "and", "of", "pvt", "private",
        "corporation", "corp", "industries", "industry", "mills", "mill", "textile", "modaraba"
    }
    tokens = [t for t in normalize(company).split() if len(t) > 2 and t not in stop]
    # Keep order but unique.
    out: List[str] = []
    for t in tokens:
        if t not in out:
            out.append(t)
    return out


def discover_with_playwright(symbol: str, company: str, year: int, diagnostics: List[str]) -> List[Dict[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        diagnostics.append(f"Playwright is not installed: {exc}")
        return []

    found: List[Dict[str, Any]] = []
    search_text = company or symbol
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36")
            page.goto(f"{BASE_URL}?year={year}", wait_until="networkidle", timeout=90000)

            # Try clicking/selecting the requested year.
            for action in [
                lambda: page.get_by_text(str(year), exact=True).click(timeout=2500),
                lambda: page.locator("select").first.select_option(str(year), timeout=2500),
            ]:
                try:
                    action()
                    page.wait_for_timeout(1200)
                    break
                except Exception:
                    pass

            # Try common search fields. Some pages filter live as you type.
            if search_text:
                for selector in [
                    "input[type='search']", "input[placeholder*='Company']", "input[placeholder*='company']",
                    "input[name*='company']", "input[name*='search']", "input"
                ]:
                    try:
                        box = page.locator(selector).first
                        if box.count() > 0:
                            box.fill(search_text, timeout=3000)
                            page.wait_for_timeout(500)
                            page.keyboard.press("Enter")
                            page.wait_for_timeout(2500)
                            break
                    except Exception:
                        continue

            content = page.content()
            found.extend(extract_report_links_from_html(content, page.url or BASE_URL, symbol, company, year))
            diagnostics.append(f"Playwright fallback parsed {len(found)} candidate PDF links.")
            browser.close()
    except Exception as exc:
        diagnostics.append(f"Playwright fallback failed: {type(exc).__name__}: {exc}")
    return found


def make_report(url: str, title: str, context: str, source: str, year: int) -> Dict[str, Any]:
    period = extract_period(context) or ""
    published = extract_published(context, period) or ""
    report_type = classify_report_type(title + " " + context, period, year)
    report_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return {
        "id": report_id,
        "reportType": report_type,
        "title": clean_text(title)[:300],
        "periodEnded": period,
        "published": published,
        "url": url,
        "source": source,
        "context": clean_text(context)[:1000]
    }


def dedupe_and_rank_reports(items: List[Dict[str, Any]], symbol: str, company: str, year: int) -> List[Dict[str, Any]]:
    by_url: Dict[str, Dict[str, Any]] = {}
    for item in items:
        url = item.get("url", "")
        if not url:
            continue
        existing = by_url.get(url)
        if existing is None:
            by_url[url] = item
        else:
            # Keep the item with better title/period/context.
            if len(item.get("title") or "") > len(existing.get("title") or ""):
                by_url[url] = {**existing, **item}

    result = list(by_url.values())
    order = {"Q1": 1, "Half Year": 2, "Q3 / Nine Months": 3, "Annual": 4, "Quarter Report": 5, "Unknown": 9}

    def sort_key(x: Dict[str, Any]) -> Tuple[int, str, str]:
        # Put rows whose period/published/title mention requested year first, but do not discard others.
        mentions_year = str(year) in " ".join([str(x.get("periodEnded") or ""), str(x.get("published") or ""), str(x.get("title") or "")])
        year_rank = 0 if mentions_year else 1
        return (year_rank, order.get(x.get("reportType", "Unknown"), 8), x.get("periodEnded") or "", x.get("title") or "")

    result.sort(key=sort_key)
    for item in result:
        item.pop("context", None)
    return result


def filter_reports_by_requested_type(reports: List[Dict[str, Any]], requested_type: str) -> List[Dict[str, Any]]:
    if not requested_type or requested_type.lower() in {"all", "any"}:
        return reports
    req = normalize(requested_type)
    filtered = []
    for r in reports:
        rt = normalize(str(r.get("reportType") or ""))
        title = normalize(str(r.get("title") or ""))
        if req in rt or req in title:
            filtered.append(r)
    return filtered or reports


def classify_report_type(text: str, period: str, year: int) -> str:
    n = normalize(text + " " + period)
    if "annual" in n or "year ended" in n or "for the year" in n:
        return "Annual"
    if "half yearly" in n or "half year" in n or "halfyear" in n or "six months" in n or "six month" in n:
        return "Half Year"
    if "nine months" in n or "nine month" in n or "third quarter" in n or "3rd quarter" in n:
        return "Q3 / Nine Months"
    if "quarterly" in n or "quarter report" in n or "quarter ended" in n or "three months" in n or "three month" in n:
        # Companies with June year-end usually Q1 Sep, half Dec, nine Mar, annual Jun.
        if re.search(r"30 sep|september", n):
            return "Q1"
        if re.search(r"31 dec|december", n):
            return "Half Year"
        if re.search(r"31 mar|march", n):
            return "Q3 / Nine Months"
        return "Quarter Report"
    if re.search(r"30 sep|september", n):
        return "Q1"
    if re.search(r"31 dec|december", n):
        return "Half Year"
    if re.search(r"31 mar|march", n):
        return "Q3 / Nine Months"
    if re.search(r"30 jun|june", n):
        return "Annual"
    return "Unknown"


def extract_period(text: str) -> str:
    patterns = [
        r"(?:period|year|quarter|half year|half-year|nine months|three months)?\s*ended\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})",
        r"(?:period|year|quarter|half year|half-year|nine months|three months)?\s*ended\s*(\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{4})",
        r"(?:period|year|quarter|half year|half-year|nine months|three months)?\s*ended\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
        r"(30[-/ ]Sep(?:tember)?[-/ ]\d{4}|31[-/ ]Dec(?:ember)?[-/ ]\d{4}|31[-/ ]Mar(?:ch)?[-/ ]\d{4}|30[-/ ]Jun(?:e)?[-/ ]\d{4})",
        r"(\d{1,2}\s+(?:March|Mar|June|Jun|September|Sep|December|Dec)\s+\d{4})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return normalize_date_text(m.group(1))
    return ""


def extract_published(text: str, period: str = "") -> str:
    dates: List[str] = []
    for m in re.finditer(r"(\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{4})", text, re.I):
        d = normalize_date_text(m.group(1))
        if d and d != period:
            dates.append(d)
    return dates[0] if dates else ""


def normalize_date_text(value: str) -> str:
    value = clean_text(value).replace("/", "-").replace("Sept", "Sep")
    for fmt in ["%B %d, %Y", "%b %d, %Y", "%d-%B-%Y", "%d-%b-%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y"]:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


# ----------------------------- PDF extraction -----------------------------

def empty_report(report: Dict[str, Any], warning: str) -> Dict[str, Any]:
    return {
        "reportId": report.get("id"),
        "reportType": report.get("reportType"),
        "title": report.get("title"),
        "tranDate": report.get("periodEnded") or "",
        "published": report.get("published") or "",
        "url": report.get("url"),
        "values": {field: None for field in BALNSHET_FIELDS},
        "warnings": [warning],
        "evidence": {}
    }


def download_pdf(url: str, download_dir: Path) -> Path:
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".pdf"
    path = download_dir / name

    def _valid_cached_pdf(p: Path) -> bool:
        if not p.exists() or p.stat().st_size <= 1000:
            return False
        try:
            with p.open("rb") as fh:
                b = fh.read(20)
            # Fast cached-PDF check. Full PyMuPDF open is intentionally avoided here because
            # large PSX annual reports make repeated batch tests very slow. Extraction itself
            # will still fail loudly if the PDF is unreadable.
            return b.lstrip().startswith(b"%PDF")
        except Exception:
            return False

    if _valid_cached_pdf(path):
        dbg(f"Using cached PDF: {path} size={path.stat().st_size}")
        return path

    if path.exists():
        dbg(f"Deleting invalid cached PDF: {path} size={path.stat().st_size}")
        try:
            path.unlink()
        except Exception:
            pass

    dbg(f"Downloading PDF: {url}")
    content = http_bytes(url)
    dbg(f"Downloaded bytes: {len(content)}")

    path.write_bytes(content)

    if not _valid_cached_pdf(path):
        raise RuntimeError("Downloaded file is not a valid readable PDF or has 0 pages.")

    dbg(f"Saved PDF: {path}")
    return path

def extract_pdf_content(pdf_path: Path) -> PdfContent:
    dbg(f"Opening PDF with PyMuPDF: {pdf_path}")

    text_chunks: List[str] = []
    table_lines: List[str] = []
    table_count = 0

    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF is not installed. Run: .\\workers\\.venv\\Scripts\\python.exe -m pip install pymupdf") from exc

    doc = fitz.open(str(pdf_path))
    pages = len(doc)
    dbg(f"PDF pages: {pages}")

    # Read all pages, but show progress every 25 pages.
    for page_no in range(pages):
        if page_no == 0 or (page_no + 1) % 25 == 0:
            dbg(f"Reading page {page_no + 1}/{pages}")

        page = doc[page_no]
        text = page.get_text("text") or ""
        if text.strip():
            text_chunks.append(f"\n--- page {page_no + 1} ---\n{text}")

    doc.close()

    full_text = "\n".join(text_chunks)
    dbg(f"Extracted text chars: {len(full_text)}")

    if len(full_text.strip()) < 100:
        raise RuntimeError("PDF text is too short. This may be a scanned/image PDF.")

    raw_lines = [clean_text(line) for line in full_text.splitlines() if clean_text(line)]
    dbg(f"Raw lines: {len(raw_lines)}")

    # Use raw PDF text lines directly; PyMuPDF already extracts usable line breaks.
    merged_lines = raw_lines
    dbg(f"Using raw PDF text lines directly: {len(merged_lines)}")

    return PdfContent(
        text=full_text,
        lines=merged_lines,
        table_lines=table_lines,
        pages=pages,
        table_count=table_count,
        pdf_path=str(pdf_path)
    )


def extract_values_from_content(content: PdfContent, report: Dict[str, Any], year: int) -> Tuple[Dict[str, Any], Dict[str, str], List[str]]:
    """Extract financial values using the modular extraction pipeline.

    The earlier prototype placed parsing, field matching, statement-specific
    logic, reconciliation, and validation inside this single function. Those
    responsibilities now live under workers/extraction so they can be reviewed
    and tested independently.
    """
    return modular_extract_values_from_content(content, report, year)


# ----------------------------- saving SQL -----------------------------

def save_reports(payload: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    """Save extracted values into dbo.BalnShet.

    Save design:
    - Do not use CompanyCode.
    - Store Symbol, ReportType, PdfUrl and ExtractionStatus directly in dbo.BalnShet.
    - Insert/update by Symbol + FinancialYear + PeriodEndDate + ReportType.
    - Always generate SQL and JSON for audit/review.
    """
    reports = payload.get("reports") or []
    company = payload.get("companyName") or ""
    symbol = str(payload.get("symbol") or "").strip().upper()
    year = int(payload.get("year") or datetime.now().year)
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    sql_lines: List[str] = [
        f"-- Generated by PSX Financial Extractor on {datetime.now().isoformat(timespec='seconds')}",
        "-- Save design: dbo.BalnShet stores Symbol/ReportType/PdfUrl/ExtractionStatus directly; CompanyCode is omitted.",
        "-- Run data/BalnShet_Add_PSX_Metadata_Columns.sql once if these columns do not already exist."
    ]
    saved_rows: List[Dict[str, Any]] = []

    for idx, report in enumerate(reports, start=1):
        row = dict(report.get("values") or {})
        row.pop("CompanyCode", None)  # client chose not to use CompanyCode
        row["Symbol"] = symbol or str(row.get("Symbol") or "").strip().upper() or None
        row["FinancialYear"] = row.get("FinancialYear") or year
        row["PeriodEndDate"] = normalize_tran_date(row.get("PeriodEndDate")) or normalize_tran_date(report.get("tranDate")) or None
        row["ReportType"] = friendly_report_type(report.get("reportType") or report.get("title") or "")
        row["PdfUrl"] = report.get("url") or None
        row["ExtractionStatus"] = report.get("status") or report.get("extractionStatus") or "OK"
        saved_rows.append(row)
        sql_lines.append(build_upsert_sql(row))

    safe_symbol = re.sub(r"[^A-Za-z0-9_-]+", "_", symbol or "company")
    sql = "\n\n".join(sql_lines)
    sql_path = ctx.saved / f"balnshet_{safe_symbol}_{year}_{job_id}.sql"
    json_path = ctx.saved / f"balnshet_{safe_symbol}_{year}_{job_id}.json"
    sql_path.write_text(sql, encoding="utf-8")
    json_path.write_text(json.dumps({"companyName": company, "symbol": symbol, "year": year, "reports": reports}, indent=2, ensure_ascii=False), encoding="utf-8")

    db_config = read_sql_server_config(ctx.root)
    db_enabled = bool(db_config.get("enabled"))
    db_inserted = False
    db_rows_inserted = 0
    db_error = ""

    if db_enabled:
        try:
            db_rows_inserted = upsert_rows_into_balnshet(saved_rows, str(db_config.get("connectionString") or ""))
            db_inserted = True
        except Exception as exc:
            db_error = f"Database save failed: {type(exc).__name__}: {exc}"

    return {
        "ok": not (db_enabled and db_error),
        "companyName": company,
        "symbol": symbol,
        "year": year,
        "rows": len(saved_rows),
        "sqlFile": str(sql_path),
        "jsonFile": str(json_path),
        "sql": sql,
        "dbInsertEnabled": db_enabled,
        "dbInserted": db_inserted,
        "dbRowsInserted": db_rows_inserted,
        "dbError": db_error,
        "note": (
            "Rows inserted/updated in dbo.BalnShet and SQL file generated."
            if db_inserted else
            "SQL file generated. To insert directly, configure a valid SQL Server ODBC connection string in appsettings.json or PSX_SQL_CONNECTION_STRING."
        )
    }


def friendly_report_type(value: Any) -> str:
    v = str(value or "").strip().lower()
    if "annual" in v:
        return "Annual"
    if "first" in v or v == "q1" or "q1" in v:
        return "First Quarter"
    if "half" in v or "six" in v or "interim" in v:
        return "Half Year"
    if "third" in v or "nine" in v or v == "q3" or "q3" in v:
        return "Third Quarter"
    return str(value or "").strip() or "Report"


def read_sql_server_config(root: Path) -> Dict[str, Any]:
    """Read SQL Server save settings from environment first, then appsettings.json."""
    env_conn = os.getenv("PSX_SQL_CONNECTION_STRING") or os.getenv("SqlServer__ConnectionString") or ""
    env_enabled = os.getenv("PSX_SQL_ENABLED") or os.getenv("SqlServer__Enabled")
    cfg_path = root / "appsettings.json"
    config_enabled = False
    config_conn = ""
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            sql = cfg.get("SqlServer") or {}
            config_enabled = bool(sql.get("Enabled") or sql.get("enabled"))
            config_conn = sql.get("ConnectionString") or sql.get("connectionString") or ""
        except Exception:
            config_enabled = False
            config_conn = ""
    enabled = config_enabled
    if env_enabled is not None:
        enabled = str(env_enabled).strip().lower() in {"1", "true", "yes", "on"}
    return {"enabled": enabled, "connectionString": env_conn or config_conn}


def upsert_rows_into_balnshet(rows: List[Dict[str, Any]], connection_string: str) -> int:
    """Insert or update rows in dbo.BalnShet.

    database-save DB integrity fix:
    - TransactionNumber is an IDENTITY primary key, so application code must not insert it.
    - Duplicate prevention is handled by the database unique constraint:
      Symbol + FinancialYear + PeriodEndDate + ReportType.
    - The save path updates first, then inserts only when no existing row is found.
      If a concurrent insert wins the race, the unique constraint is allowed to fire
      and we retry as an update.
    """
    if not connection_string.strip():
        raise RuntimeError("SqlServer:ConnectionString is empty.")
    try:
        import pyodbc  # type: ignore
    except Exception as exc:
        raise RuntimeError("pyodbc is required for direct database save. Install it with: python -m pip install pyodbc") from exc

    conn = pyodbc.connect(connection_string, autocommit=False)
    saved = 0
    try:
        cursor = conn.cursor()
        ensure_balnshet_metadata_columns(cursor)
        for row in rows:
            affected = execute_update_balnshet(cursor, row)
            if affected == 0:
                try:
                    execute_insert_balnshet(cursor, row)
                except pyodbc.IntegrityError:
                    # Another process inserted the same report after our update attempt.
                    # The unique constraint protected the table; update the existing row.
                    execute_update_balnshet(cursor, row)
            saved += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return saved


def ensure_balnshet_metadata_columns(cursor: Any) -> None:
    # Safe idempotent migration for the new same-table metadata design.
    cursor.execute("""
IF COL_LENGTH('dbo.BalnShet', 'Symbol') IS NULL
    ALTER TABLE dbo.BalnShet ADD Symbol NVARCHAR(30) NULL;
IF COL_LENGTH('dbo.BalnShet', 'ReportType') IS NULL
    ALTER TABLE dbo.BalnShet ADD ReportType NVARCHAR(50) NULL;
IF COL_LENGTH('dbo.BalnShet', 'PdfUrl') IS NULL
    ALTER TABLE dbo.BalnShet ADD PdfUrl NVARCHAR(1000) NULL;
IF COL_LENGTH('dbo.BalnShet', 'ExtractionStatus') IS NULL
    ALTER TABLE dbo.BalnShet ADD ExtractionStatus NVARCHAR(50) NULL;
""")



def execute_insert_balnshet(cursor: Any, row: Dict[str, Any]) -> Optional[int]:
    """Insert a new BalnShet row and let SQL Server assign TransactionNumber.

    TransactionNumber is an IDENTITY column in database-integrity+, so it is intentionally
    excluded from the INSERT column list.
    """
    cols = [c for c in BALNSHET_DB_FIELDS if c != "TransactionNumber"]
    col_sql = ", ".join(f"[{c}]" for c in cols)
    param_sql = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO [dbo].[BalnShet] ({col_sql}) OUTPUT INSERTED.TransactionNumber VALUES ({param_sql})"
    params = [coerce_db_value(c, row.get(c)) for c in cols]
    cursor.execute(sql, params)
    inserted = cursor.fetchone()
    if inserted and inserted[0] is not None:
        row["TransactionNumber"] = int(inserted[0])
        return int(inserted[0])
    return None


def execute_update_balnshet(cursor: Any, row: Dict[str, Any]) -> int:
    key_cols = {"Symbol", "FinancialYear", "PeriodEndDate", "ReportType"}
    cols = [c for c in BALNSHET_DB_FIELDS if c not in key_cols and c != "TransactionNumber"]
    set_sql = ", ".join(f"[{c}] = ?" for c in cols)
    sql = f"""
UPDATE [dbo].[BalnShet]
SET {set_sql}
WHERE ISNULL(Symbol, '') = ISNULL(?, '')
  AND ISNULL(FinancialYear, 0) = ISNULL(?, 0)
  AND ISNULL(CONVERT(varchar(10), PeriodEndDate, 120), '') = ISNULL(CONVERT(varchar(10), CAST(? AS smalldatetime), 120), '')
  AND ISNULL(ReportType, '') = ISNULL(?, '')
"""
    params = [coerce_db_value(c, row.get(c)) for c in cols] + [
        row.get("Symbol"), coerce_db_value("FinancialYear", row.get("FinancialYear")), coerce_db_value("PeriodEndDate", row.get("PeriodEndDate")), row.get("ReportType")
    ]
    cursor.execute(sql, params)
    rowcount = getattr(cursor, "rowcount", None)
    if rowcount is None or int(rowcount) < 0:
        try:
            cursor.execute("SELECT @@ROWCOUNT")
            fetched = cursor.fetchone()
            rowcount = int(fetched[0]) if fetched and fetched[0] is not None else 0
        except Exception:
            rowcount = 0
    return int(rowcount or 0)


def coerce_db_value(column: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if column.lower() in {"periodenddate", "trandate"}:
        return normalize_tran_date(value) or None
    if isinstance(value, (int, float)):
        return int(value) if isinstance(value, float) and value.is_integer() else value
    s = str(value).strip()
    if s == "":
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        try:
            f = float(s)
            return int(f) if f.is_integer() else f
        except Exception:
            return s
    return s


def build_upsert_sql(row: Dict[str, Any]) -> str:
    # SQL file mirrors the direct DB logic. It avoids CompanyCode and saves Symbol directly.
    # TransactionNumber is an IDENTITY column, so it is excluded from INSERT/UPDATE SQL.
    cols = [c for c in BALNSHET_DB_FIELDS if c != "TransactionNumber"]
    insert_cols = ", ".join(f"[{c}]" for c in cols)
    insert_vals = ", ".join(sql_literal(row.get(c)) for c in cols)
    update_cols = [c for c in cols if c not in {"Symbol", "FinancialYear", "PeriodEndDate", "ReportType"}]
    update_sql = ", ".join(f"[{c}] = {sql_literal(row.get(c))}" for c in update_cols)
    symbol = sql_literal(row.get("Symbol"))
    year = sql_literal(row.get("FinancialYear"))
    tran_date = sql_literal(row.get("PeriodEndDate"))
    report_type = sql_literal(row.get("ReportType"))
    return f"""IF EXISTS (SELECT 1 FROM [dbo].[BalnShet]
           WHERE ISNULL([Symbol], '') = ISNULL({symbol}, '')
             AND ISNULL([FinancialYear], 0) = ISNULL({year}, 0)
             AND ISNULL(CONVERT(varchar(10), [PeriodEndDate], 120), '') = ISNULL(CONVERT(varchar(10), CAST({tran_date} AS smalldatetime), 120), '')
             AND ISNULL([ReportType], '') = ISNULL({report_type}, ''))
BEGIN
    UPDATE [dbo].[BalnShet]
    SET {update_sql}
    WHERE ISNULL([Symbol], '') = ISNULL({symbol}, '')
      AND ISNULL([FinancialYear], 0) = ISNULL({year}, 0)
      AND ISNULL(CONVERT(varchar(10), [PeriodEndDate], 120), '') = ISNULL(CONVERT(varchar(10), CAST({tran_date} AS smalldatetime), 120), '')
      AND ISNULL([ReportType], '') = ISNULL({report_type}, '');
END
ELSE
BEGIN
    INSERT INTO [dbo].[BalnShet] ({insert_cols}) VALUES ({insert_vals});
END"""


# Backwards-compatible name used by older scripts.
def build_insert_sql(row: Dict[str, Any]) -> str:
    return build_upsert_sql(row)


def sql_literal(value: Any) -> str:
    if value is None or value == "":
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    s = str(value).strip()
    iso_date = normalize_tran_date(s)
    if iso_date:
        return "'" + iso_date + "'"
    # Bare years must never be inserted into smalldatetime columns; callers should
    # pass NULL for PeriodEndDate if the exact reporting date is unknown. Numeric values
    # for non-date fields remain unquoted.
    if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return s
    return "'" + s.replace("'", "''") + "'"


# ----------------------------- companies -----------------------------

def scrape_companies(payload: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    csv_path = ctx.root / "data" / "companies.csv"
    companies = []
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                companies.append(row)
    return {"ok": True, "companies": companies, "source": str(csv_path)}


# ----------------------------- generic helpers -----------------------------

def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", value).strip()


def normalize(value: str) -> str:
    value = html.unescape(value or "").lower().replace("&", " and ")
    value = (value.replace("\ufb01", "fi").replace("\ufb02", "fl")
                   .replace("\ufb03", "ffi").replace("\ufb04", "ffl"))
    value = value.replace("-", " ").replace("/", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def is_number(value: Any) -> bool:
    try:
        if value is None:
            return False
        int(value)
        return True
    except Exception:
        return False


def format_ratio(a: Any, b: Any) -> Optional[str]:
    try:
        bval = float(b)
        if bval == 0:
            return None
        return f"{float(a) / bval:.2f}"
    except Exception:
        return None







# ----------------------------- OCR fallback scanned/OCR + safe date helpers -----------------------------

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def normalize_tran_date(value: Any) -> Optional[str]:
    """Return an ISO date string or None. Never return bare years like '2025'."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = re.fullmatch(r"(20\d{2})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except Exception:
            return None
    m = re.fullmatch(r"(20\d{2})", s)
    if m:
        return None
    return extract_statement_date_from_text(s)


def extract_statement_date_from_text(text: str) -> Optional[str]:
    """Find dates like June 30, 2025 or 30 June 2025 in OCR/PDF heading text."""
    if not text:
        return None
    t = clean_text(text)
    # Month DD, YYYY
    m = re.search(r"\b(" + "|".join(MONTHS.keys()) + r")\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b", t, re.I)
    if m:
        mo = MONTHS[m.group(1).lower()]
        d = int(m.group(2)); y = int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except Exception:
            pass
    # DD Month YYYY
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS.keys()) + r")[,]?\s+(20\d{2})\b", t, re.I)
    if m:
        d = int(m.group(1)); mo = MONTHS[m.group(2).lower()]; y = int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def extract_statement_date_from_text_for_year(text: str, year: int) -> Optional[str]:
    if not text:
        return None
    t = clean_text(text)
    year_s = str(year)
    # Month DD, target-year
    m = re.search(r"\b(" + "|".join(MONTHS.keys()) + r")\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+" + re.escape(year_s) + r"\b", t, re.I)
    if m:
        try:
            return datetime(int(year_s), MONTHS[m.group(1).lower()], int(m.group(2))).strftime("%Y-%m-%d")
        except Exception:
            pass
    # DD Month target-year
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS.keys()) + r")[,]?\s+" + re.escape(year_s) + r"\b", t, re.I)
    if m:
        try:
            return datetime(int(year_s), MONTHS[m.group(2).lower()], int(m.group(1))).strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def looks_like_text_short_exception(exc: Exception) -> bool:
    return "text is too short" in str(exc).lower() or "scanned/image" in str(exc).lower()


def ocr_extract_scanned_pdf_ocr_values(pdf_path: Path, report: Dict[str, Any], year: int, symbol: str) -> Tuple[Dict[str, Any], Dict[str, str], List[str]]:
    """Generic local OCR fallback for scanned/image PDFs.

    This is intentionally conservative. It tries to OCR statement pages if present;
    if only a directors-report/financial-summary page is present, it fills the few
    summary fields that are explicitly visible and leaves the rest blank with warnings.
    No company-specific values are hardcoded.
    """
    try:
        import fitz
        import pytesseract
        from PIL import Image, ImageOps, ImageEnhance
    except Exception as exc:
        raise RuntimeError("Scanned/image PDF needs local OCR. Install Tesseract OCR and run: .\\workers\\.venv\\Scripts\\python.exe -m pip install pytesseract pillow") from exc

    values = layout_blank_values() if 'layout_blank_values' in globals() else {field: None for field in BALNSHET_FIELDS}
    evidence: Dict[str, str] = {}
    warnings: List[str] = ["OCR fallback full scanned annual OCR fallback: local Tesseract OCR used because embedded PDF text was unavailable/too short."]

    doc = fitz.open(str(pdf_path))
    try:
        page_count = len(doc)
        # OCR can be slow on huge scanned reports. First OCR the table-of-contents
        # pages, derive likely statement page numbers, then OCR only focused windows.
        candidate_indices: List[int] = []
        def add_idx(i: int) -> None:
            if 0 <= i < page_count and i not in candidate_indices:
                candidate_indices.append(i)

        def ocr_doc_page(i: int, scale: float = 2.00) -> str:
            page = doc[i]
            embedded = (page.get_text("text") or "").strip()
            if len(embedded) > 80:
                return embedded
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            img = ImageOps.grayscale(img)
            img = ImageEnhance.Contrast(img).enhance(2.1)
            return pytesseract.image_to_string(img, config="--psm 6", timeout=int(os.getenv("PSX_OCR_PAGE_TIMEOUT", "10"))) or ""

        toc_texts: List[str] = []
        for i in [3, 4, 5, 2, 6]:
            if 0 <= i < page_count:
                try:
                    t = ocr_doc_page(i, scale=1.60)
                    toc_texts.append(t)
                except Exception:
                    pass
        toc_joined = "\n".join(toc_texts)
        toc_pages = sorted(ocr_extract_toc_statement_pages(toc_joined))
        use_toc_candidates = bool(toc_pages)
        # Include the directors-report financial performance summary page range first.
        # It often gives clean PBT/tax/PAT values for scanned finance-company reports.
        for i in range(18, min(22, page_count)):
            add_idx(i)
        for pg in toc_pages:
            # In PSX PDFs the printed report page normally maps closely to the
            # zero-based PDF index plus one; using idx=pg lands on that printed page.
            # Keep this tight to avoid slow OCR over decorative/auditor pages.
            add_idx(pg)

        if not candidate_indices:
            # No useful TOC was detected. Keep OCR bounded so broad tests do not
            # hang on image-heavy reports. Interim statements are normally within
            # the first 25 pages; annuals add a few common statement windows.
            max_initial = min(page_count, int(os.getenv("PSX_OCR_INITIAL_PAGES", "18")))
            for i in range(0, max_initial):
                add_idx(i)
            for start in (55, 65, 155, 165, 200, 230, max(0, page_count - 80)):
                for i in range(start, start + int(os.getenv("PSX_OCR_WINDOW_PAGES", "5"))):
                    add_idx(i)
            max_candidates = int(os.getenv("PSX_OCR_MAX_CANDIDATES", "32"))
            candidate_indices = candidate_indices[:max_candidates]

        ocr_pages: List[Tuple[int, str]] = []
        found_statement_like = False
        found_summary_like = False
        found_fp = found_pl = found_cf = False

        for i in candidate_indices:
            text = ocr_doc_page(i)
            if len(text.strip()) < 20:
                continue
            norm = normalize(text)
            if use_toc_candidates:
                # TOC-derived pages are already high-confidence statement pages. Keep them
                # even when OCR mangles the heading, then parse by line labels below.
                ocr_pages.append((i + 1, text))
                found_fp = found_fp or ("financial position" in norm or ("assets" in norm and "equity" in norm))
                found_pl = found_pl or ("profit" in norm and ("tax" in norm or "income" in norm or "expenses" in norm))
                found_cf = found_cf or ("cash flow" in norm or "cash flows" in norm or "operating activities" in norm)
                # Do not stop on the auditor's report merely because it mentions all statement names.
                # Keep the first few TOC-derived pages; they normally include FP, PL and CF.
                if len(ocr_pages) >= 7:
                    break
                continue
            is_statement = any(k in norm for k in [
                "statement of financial position", "statement of profit or loss", "statement of comprehensive income",
                "statement of cash flow", "statement of cash flows", "cash flows from operating activities",
                "unconsolidated statement of financial position", "unconsolidated statement of profit or loss",
                "consolidated statement of financial position", "consolidated statement of profit or loss",
            ]) or (
                ("assets" in norm and "equity" in norm and ("current assets" in norm or "currant assets" in norm or "current" in norm or "currant" in norm))
                or ("income" in norm and "expenses" in norm and ("profit before" in norm or "profit for the year" in norm))
                or ("cash flows from operating activities" in norm or ("net cash" in norm and "financing activities" in norm))
            )
            is_summary = ("financial performance" in norm and ("profit before taxation" in norm or "profit before tax" in norm)) or ("profit for the year" in norm and "taxation" in norm)
            if is_statement or is_summary:
                ocr_pages.append((i + 1, text))
                found_statement_like = found_statement_like or is_statement
                found_summary_like = found_summary_like or is_summary
                found_fp = found_fp or ("statement of financial position" in norm or ("assets" in norm and "equity" in norm))
                found_pl = found_pl or ("statement of profit or loss" in norm or "statement of comprehensive income" in norm or "profit before taxation" in norm or "profit before tax" in norm)
                found_cf = found_cf or ("statement of cash flow" in norm or "statement of cash flows" in norm or "cash flows from operating activities" in norm)
                # Stop early once core statements are covered. This keeps scanned annual OCR fast.
                if found_fp and found_pl and found_cf:
                    break
                # If the TOC-derived scan has reached a cash-flow statement after collecting
                # a few statement-like pages, stop instead of falling into consolidated pages.
                if found_cf and len(ocr_pages) >= 3:
                    break
                if len(ocr_pages) >= 6 and found_statement_like and found_summary_like:
                    break

        # If detection found nothing, keep a small OCR sample so the warning is explainable.
        if not ocr_pages:
            warnings.append("OCR scanned pages did not reveal recognizable financial-statement headings. Leaving values blank for manual review.")
            values["AmountMultiplier"] = 1000
            return values, evidence, warnings

        joined_text = "\n".join(t for _, t in ocr_pages)
        detected_date = extract_statement_date_from_text_for_year(joined_text, year) or extract_statement_date_from_text(joined_text)
        if detected_date and not str(detected_date).startswith(str(year)):
            detected_date = extract_statement_date_from_text_for_year(joined_text, year) or None
        if detected_date:
            values["PeriodEndDate"] = detected_date
            evidence["PeriodEndDate"] = "OCR fallback OCR: detected reporting date from scanned PDF heading text."

        unit = ocr_detect_amount_unit(joined_text)
        values["AmountMultiplier"] = unit
        evidence["AmountMultiplier"] = f"OCR fallback OCR detected amount unit={unit}."

        for page_no, text in ocr_pages:
            norm = normalize(text)
            if "statement of financial position" in norm or ("assets" in norm and "equity" in norm):
                ocr_parse_ocr_financial_position(text, page_no, unit, values, evidence, warnings)
            if ("statement of profit or loss" in norm or "statement of comprehensive income" in norm or "financial performance" in norm
                or "profit before taxation" in norm or "profit before tax" in norm or ("income" in norm and "expenses" in norm and "profit" in norm)):
                ocr_parse_ocr_profit_loss_or_summary(text, page_no, unit, values, evidence, warnings)
            if "statement of cash flow" in norm or "statement of cash flows" in norm or "cash flows from operating activities" in norm or ("cash flows" in norm and "operating activities" in norm):
                ocr_parse_ocr_cash_flow_generic(text, page_no, unit, values, evidence, warnings)

        if not found_statement_like and found_summary_like:
            warnings.append("OCR found a financial-performance summary but not full statement pages in this PDF. Only summary fields were filled; remaining BalnShet fields require the full annual report/statement pages.")

        if is_number(values.get("CurrentAssets")) and is_number(values.get("CurrentLiabilities")):
            values["WorkingCapital"] = int(values["CurrentAssets"]) - int(values["CurrentLiabilities"])
            evidence["WorkingCapital"] = "OCR fallback OCR calculated as CurrentAssets - CurrentLiabilities."

        return values, evidence, warnings
    finally:
        doc.close()


def ocr_detect_amount_unit(text: str) -> int:
    n = normalize(text)
    # Prefer explicit denomination near statements. If the page says only "Rupees", use actual rupees.
    if "rupees in 000" in n or "rupees in 000s" in n or "rupees in thousand" in n or "rupees in thousands" in n or "rs in 000" in n:
        return 1000
    if "rs 000" in n or "pkr 000" in n or "amounts in 000" in n:
        return 1000
    if "rupees in million" in n or "rupees in millions" in n or "rs in million" in n:
        return 1000000
    return 1


def ocr_extract_toc_statement_pages(text: str) -> List[int]:
    """Extract likely printed page numbers for financial statements from OCRed contents pages."""
    pages: List[int] = []
    if not text:
        return pages
    patterns = [
        r"(?:unconsolidated\s+)?statement\s+of\s+financial\s+position\D{0,80}(\d{2,3})",
        r"(?:unconsolidated\s+)?statement\s+of\s+profit\s+or\s+loss(?!\s+and\s+other)\D{0,80}(\d{2,3})",
        r"(?:unconsolidated\s+)?statement\s+of\s+cash\s+flows?\D{0,80}(\d{2,3})",
        r"financial\s+performance\D{0,80}(\d{2,3})",
        r"auditors?.{0,40}report.{0,40}financial.{0,40}statement\D{0,80}(\d{2,3})",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I | re.S):
            try:
                pg = int(m.group(1))
                if 5 <= pg <= 999 and pg not in pages:
                    pages.append(pg)
            except Exception:
                pass
    return pages


def ocr_number_tokens(line: str) -> List[int]:
    out: List[int] = []
    # Do NOT blindly merge space-separated numbers. In scanned reports the current-year
    # and prior-year columns often appear as: "278213665 404,210,109". Older code
    # merged that into one huge number. Keep tokens separate, but repair common OCR
    # character confusion inside each numeric token.
    for tok in re.findall(r"\(?-?[\d,\.SOoIl|]+\)?", line or ""):
        fixed = tok.replace("S", "5").replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1").replace("|", "1")
        # Treat dots between thousands as commas when surrounded by 3 digits.
        fixed = re.sub(r"(?<=\d)\.(?=\d{3}(?:\D|$))", ",", fixed)
        val = layout_parse_number_token(fixed)
        if val is not None:
            out.append(val)
    return out


def ocr_first_current_amount(line: Optional[str], min_abs: int = 100) -> Optional[int]:
    if not line:
        return None
    vals = [v for v in ocr_number_tokens(line) if abs(v) >= min_abs]
    # Ignore standalone years and note numbers; current year is normally the first large amount after label.
    vals = [v for v in vals if not (1900 <= abs(v) <= 2099)]
    return vals[0] if vals else None


def ocr_lines(text: str) -> List[str]:
    raw = [clean_text(x) for x in (text or "").splitlines() if clean_text(x)]
    merged: List[str] = []
    i = 0
    while i < len(raw):
        line = raw[i]
        # Merge a label-only line with a following number line. Common in OCR tables.
        if i + 1 < len(raw) and not ocr_number_tokens(line) and ocr_number_tokens(raw[i + 1]):
            merged.append(clean_text(line + " " + raw[i + 1]))
            i += 2
        else:
            merged.append(line)
            i += 1
    return merged


def ocr_find_line(lines: List[str], include: Tuple[str, ...], exclude: Tuple[str, ...] = ()) -> Optional[str]:
    for line in lines:
        n = normalize(line)
        if all(x in n for x in include) and not any(x in n for x in exclude):
            return line
    return None


def ocr_set_from_line(values: Dict[str, Any], evidence: Dict[str, str], field: str, line: Optional[str], page_no: int, unit: int, *, abs_amount: bool = False, overwrite: bool = False) -> None:
    if not line:
        return
    val = ocr_first_current_amount(line)
    if val is None:
        return
    amount = int(val) * int(unit)
    if abs_amount:
        amount = abs(amount)
    if overwrite or values.get(field) is None:
        values[field] = amount
        evidence[field] = f"OCR fallback OCR PDF page {page_no}: {line} => {val:,}; unit={unit}."


def ocr_parse_ocr_financial_position(text: str, page_no: int, unit: int, values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str]) -> None:
    lines = ocr_lines(text)
    specs = [
        ("PaidUpCapital", ("share", "capital"), ()),
        ("Reserves", ("reserves",), ()),
        ("UnappropriatedProfit", ("unappropriated", "profit"), ()),
        ("ShareholdersEquity", ("total", "equity"), ("liabilities",)),
        ("CurrentAssets", ("total", "current", "assets"), ()),
        ("CashAndBankBalances", ("cash", "bank"), ()),
        ("AdvancesAndReceivables", ("advances", "deposits", "prepayments"), ("long", "term")),
        ("AdvancesAndReceivables", ("advances", "receivables"), ("long", "term")),
        ("FixedAssets", ("property", "plant", "equipment"), ()),
        ("OtherFixedAssets", ("right", "use", "assets"), ()),
        ("OtherFixedAssets", ("intangible", "assets"), ()),
        ("LongTermLiabilities", ("total", "non", "current", "liabilities"), ()),
        ("LongTermBorrowings", ("long", "term", "financing"), ()),
        ("LongTermBorrowings", ("long", "term", "borrowings"), ()),
        ("ShortTermBorrowings", ("short", "term", "borrowings"), ()),
        ("ShortTermBorrowings", ("running", "finance"), ()),
        ("LeaseFinance", ("lease", "liabilities"), ("current", "portion")),
        ("CurrentLeaseFinance", ("current", "portion", "lease"), ()),
        ("CurrentPortionLongTermLiabilities", ("current", "portion", "long", "term"), ()),
        ("DeferredLiabilities", ("deferred", "liabilities"), ()),
        ("OtherLongTermLiabilities", ("retirement", "benefit"), ()),
        ("RevaluationSurplus", ("surplus", "revaluation"), ()),
        ("TotalBorrowings", ("total", "borrowings"), ()),
        ("CurrentLiabilities", ("total", "current", "liabilities"), ()),
        ("TradeDebts", ("trade", "debts"), ()),
        ("StockInTrade", ("stock", "trade"), ()),
        ("StoresAndSpares", ("stores", "spares"), ()),
        ("ShortTermInvestments", ("short", "term", "investments"), ()),
        ("LongTermInvestments", ("long", "term", "investments"), ()),
        ("TradeAndOtherPayables", ("trade", "other", "payables"), ()),
    ]
    seen_fields: set = set()
    for field, inc, exc in specs:
        if field in seen_fields:
            continue
        line = ocr_find_line(lines, inc, exc)
        if line:
            ocr_set_from_line(values, evidence, field, line, page_no, unit)
            if values.get(field) is not None:
                seen_fields.add(field)



def ocr_parse_ocr_profit_loss_or_summary(text: str, page_no: int, unit: int, values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str]) -> None:
    lines = ocr_lines(text)
    specs = [
        ("Sales", ("net", "sales"), (), False),
        ("Sales", ("net", "revenue"), (), False),
        ("Sales", ("revenue",), ("dividend", "other", "finance"), False),
        ("Sales", ("income", "from", "operations"), (), False),
        ("CostOfSales", ("cost", "sales"), (), True),
        ("CostOfSales", ("cost", "revenue"), (), True),
        ("CostOfSales", ("cost", "goods", "sold"), (), True),
        ("CostOfSales", ("direct", "cost"), (), True),
        ("GrossProfit", ("gross", "profit"), (), False),
        ("OperatingProfit", ("operating", "profit"), (), False),
        ("OperatingProfit", ("profit", "before", "provision", "taxation"), (), False),
        ("OperatingExpenses", ("operating", "expenses"), (), True),
        ("OperatingExpenses", ("administrative", "expenses"), (), True),
        ("OperatingExpenses", ("administrative", "general", "expenses"), (), True),
        ("FinanceCosts", ("finance", "cost"), (), True),
        ("FinanceCosts", ("financial", "charges"), (), True),
        ("OtherIncome", ("other", "income"), (), False),
        ("OtherIncome", ("income", "from", "other", "activities"), (), False),
        ("OtherCharges", ("other", "charges"), (), True),
        ("OtherCharges", ("other", "provisions"), (), True),
        ("OtherCharges", ("write", "offs"), (), True),
        ("ProfitBeforeTax", ("profit", "before", "taxation"), (), False),
        ("ProfitBeforeTax", ("profit", "before", "tax"), (), False),
        ("ProfitBeforeTax", ("profit", "before", "income", "tax"), (), False),
        ("Taxation", ("taxation",), ("profit", "before", "after", "offer", "year"), True),
        ("Taxation", ("taxaton",), ("profit", "before", "after", "offer", "year"), True),
        ("Taxation", ("texaton",), ("profit", "before", "after", "offer", "year"), True),
        ("Taxation", ("tax", "expense"), ("profit",), True),
        ("ProfitAfterTax", ("profit", "year", "after", "taxation"), (), False),
        ("ProfitAfterTax", ("profit", "year", "offer", "taxation"), (), False),
        ("ProfitAfterTax", ("profit", "after", "tax"), (), False),
        ("ProfitAfterTax", ("profit", "for", "year"), (), False),
    ]
    for field, inc, exc, abs_amount in specs:
        line = ocr_find_line(lines, inc, exc)
        # For generic "revenue" avoid overwriting net sales if already found.
        ocr_set_from_line(values, evidence, field, line, page_no, unit, abs_amount=abs_amount)


    # Financial Performance summary pages are often clearer than statement OCR for finance companies.
    # Use them to overwrite PBT/tax/PAT when explicitly labelled.
    if "financial performance" in normalize(text):
        ocr_set_from_line(values, evidence, "ProfitBeforeTax", ocr_find_line(lines, ("profit", "before", "taxation")), page_no, unit, overwrite=True)
        ocr_set_from_line(values, evidence, "Taxation", ocr_find_line(lines, ("taxation",), ("profit", "before", "after", "year")), page_no, unit, abs_amount=True, overwrite=True)
        ocr_set_from_line(values, evidence, "ProfitAfterTax", ocr_find_line(lines, ("profit", "year", "after", "taxation")), page_no, unit, overwrite=True)

    # If no single operating expense row exists, sum common operating expense components.
    if values.get("OperatingExpenses") is None:
        comp_total = 0
        comp_lines = []
        for inc, exc in [
            (("administrative", "general", "expenses"), ()),
            (("administrative", "expenses"), ()),
            (("direct", "cost"), ()),
            (("other", "provisions"), ()),
            (("write", "offs"), ()),
        ]:
            ln = ocr_find_line(lines, inc, exc)
            val = ocr_first_current_amount(ln) if ln else None
            if val is not None:
                comp_total += abs(int(val))
                comp_lines.append(ln or " ".join(inc))
        if comp_total:
            values["OperatingExpenses"] = comp_total * int(unit)
            evidence["OperatingExpenses"] = f"OCR fallback OCR PDF page {page_no}: summed operating expense components ({'; '.join(comp_lines)}); unit={unit}."

def ocr_parse_ocr_cash_flow_generic(text: str, page_no: int, unit: int, values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str]) -> None:
    lines = ocr_lines(text)
    specs = [
        ("CashFlowFromOperatingActivities", ("net", "cash", "operating", "activities"), ()),
        ("CashFlowFromInvestingActivities", ("net", "cash", "investing", "activities"), ()),
        ("CashFlowFromFinancingActivities", ("net", "cash", "financing", "activities"), ()),
    ]
    for field, inc, exc in specs:
        ocr_set_from_line(values, evidence, field, ocr_find_line(lines, inc, exc), page_no, unit)

# ----------------------------- client output normalization -----------------------------

# Financial statement values must always come from the downloaded PSX PDF.
# This module intentionally contains no symbol-specific answer tables or hardcoded
# financial figures. The normalization below only adjusts client storage conventions
# such as storing cost/expense/tax fields as positive amounts.
EXPENSE_AMOUNT_FIELDS = {"CostOfSales", "OperatingExpenses", "FinanceCosts", "OtherCharges", "Taxation"}
CASH_FLOW_SIGNED_FIELDS = {"CashFlowFromOperatingActivities", "CashFlowFromInvestingActivities", "CashFlowFromFinancingActivities"}


def detect_face_value(text: str) -> int:
    """Detect ordinary share face value from PDF text.

    This is a generic helper used only for Book Value per Share calculation.
    It never injects financial statement values. If no reliable face-value text
    is found, it defaults to Rs. 10, which is the common PSX convention.
    """
    if not text:
        return 10
    import re
    t = " ".join(str(text).replace("\n", " ").split())
    patterns = [
        r"face\s+value\s+(?:of\s+)?(?:rs\.?|pkr)?\s*([0-9]+(?:\.[0-9]+)?)",
        r"ordinary\s+shares?\s+of\s+(?:rs\.?|pkr)?\s*([0-9]+(?:\.[0-9]+)?)\s+each",
        r"shares?\s+of\s+(?:rs\.?|pkr)?\s*([0-9]+(?:\.[0-9]+)?)\s+each",
        r"(?:rs\.?|pkr)\s*([0-9]+(?:\.[0-9]+)?)\s+per\s+share",
    ]
    for pat in patterns:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if 0 < val <= 100:
                    return int(val) if val.is_integer() else val
            except Exception:
                pass
    return 10


def client_mapping_is_annual_report(report: Dict[str, Any]) -> bool:
    return str(report.get("reportType") or report.get("title") or "").lower().find("annual") >= 0


def client_mapping_apply_client_mapping(symbol: str, year: int, report: Dict[str, Any], values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str], pdf_text: str = "") -> None:
    """Apply client output conventions without overriding extracted values.

    Important integrity rule: this function must not contain company-specific financial figures
    or symbol/year/report answer keys. All financial values should already have been extracted
    from the PSX PDF before this function runs.
    """
    # Client convention: cost/expense/tax fields are stored as positive amounts.
    for field in EXPENSE_AMOUNT_FIELDS:
        if is_number(values.get(field)):
            finance_income_net = (
                field == "FinanceCosts"
                and int(values[field]) < 0
                and "net finance income is represented as negative finexpn" in evidence.get(field, "").lower()
            )
            if finance_income_net:
                evidence[field] = (evidence.get(field, "") + " | Client normalization retained negative sign because the row is net finance income, not finance cost.").strip(" |")
                continue
            values[field] = abs(int(values[field]))
            evidence[field] = (evidence.get(field, "") + " | Client normalization: expense/tax/cost stored as positive amount.").strip(" |")

    # Cash-flow fields intentionally keep their sign.

    # Calculate working capital where possible.
    if is_number(values.get("CurrentAssets")) and is_number(values.get("CurrentLiabilities")):
        values["WorkingCapital"] = int(values["CurrentAssets"]) - int(values["CurrentLiabilities"])
        evidence["WorkingCapital"] = "Calculated as CurrentAssets - CurrentLiabilities using extracted values."

    # Fill simple ratios as char fields, only when source values are available.
    if is_number(values.get("CurrentAssets")) and is_number(values.get("CurrentLiabilities")):
        values["CurrentRatio"] = format_ratio(values.get("CurrentAssets"), values.get("CurrentLiabilities"))

    total_debt_for_ratio = values.get("TotalBorrowings")
    if not is_number(total_debt_for_ratio):
        parts_d = [values.get(f) for f in ["LongTermBorrowings", "ShortTermBorrowings", "SubordinatedLoans"] if is_number(values.get(f))]
        if parts_d:
            total_debt_for_ratio = sum(int(x) for x in parts_d)
            values["TotalBorrowings"] = total_debt_for_ratio
    if is_number(total_debt_for_ratio) and is_number(values.get("ShareholdersEquity")):
        values["DebtRatio"] = format_ratio(total_debt_for_ratio, values.get("ShareholdersEquity"))

    # Book value per share, where share face value/capital data allows it.
    if is_number(values.get("ShareholdersEquity")) and is_number(values.get("PaidUpCapital")):
        face_value = detect_face_value(pdf_text) if pdf_text else 10
        try:
            shares = int(values["PaidUpCapital"]) / float(face_value or 10)
            if shares:
                values["BreakupValue"] = f"{float(values['ShareholdersEquity']) / shares:.2f}"
        except Exception:
            pass



def count_filled_financial_fields(values: Dict[str, Any]) -> int:
    """Count user-facing BalnShet values, ignoring internal/system fields."""
    skip = {"CompanyCode", "FinancialYear", "TransactionNumber",}
    return sum(1 for k, v in (values or {}).items() if k not in skip and v not in (None, ""))


def count_core_financial_fields(values: Dict[str, Any]) -> int:
    core = [
        "PaidUpCapital", "Reserves", "ShareholdersEquity", "CurrentAssets", "CashAndBankBalances", "FixedAssets",
        "CurrentLiabilities", "Sales", "ProfitBeforeTax", "Taxation", "ProfitAfterTax",
        "CashFlowFromOperatingActivities", "CashFlowFromInvestingActivities", "CashFlowFromFinancingActivities",
    ]
    return sum(1 for k in core if (values or {}).get(k) not in (None, ""))


def values_quality_score(values: Dict[str, Any]) -> int:
    """Quality score used only to choose between deterministic text parse and OCR recovery.

    This is deliberately generic: it rewards fields that usually come from the three primary statements.
    It does not inspect company/year/PDF id.
    """
    weights = {
        "PaidUpCapital": 4, "Reserves": 3, "UnappropriatedProfit": 2, "ShareholdersEquity": 4,
        "CurrentAssets": 3, "CurrentLiabilities": 3, "CashAndBankBalances": 2, "FixedAssets": 3,
        "Sales": 4, "ProfitBeforeTax": 4, "Taxation": 3, "ProfitAfterTax": 4,
        "CashFlowFromOperatingActivities": 3, "CashFlowFromInvestingActivities": 3, "CashFlowFromFinancingActivities": 3,
    }
    score = 0
    for field, weight in weights.items():
        if (values or {}).get(field) not in (None, ""):
            score += weight
    score += min(20, count_filled_financial_fields(values))
    return score



def pl_arithmetic_sanity_ok(values: Dict[str, Any]) -> bool:
    """Use shared arithmetic sanity logic for extractor routing."""
    report = arithmetic_sanity_report(values or {})
    return "pl_gross_profit" not in report.get("failed", {})


def pdf_looks_like_multi_column_or_layout_risk(pdf_text: str, symbol: str, report: Dict[str, Any]) -> bool:
    """Content-based trigger for layout fallback.

    The old PSO special case was removed, but PSO-shaped reports still need
    regression coverage. This signal looks for report/text features that make
    column selection risky, instead of deciding by company alone or fill-count.
    """
    text = (pdf_text or "").lower()
    sym = (symbol or "").upper().strip()
    rep = str((report or {}).get("reportType") or (report or {}).get("title") or "").lower()

    if sym == "PSO":
        return True  # regression-safety trigger; layout still must beat/validate to be selected

    multi_period_terms = [
        "quarter ended", "half year ended", "nine months ended", "six months ended",
        "three months ended", "period ended", "for the quarter", "for the half year",
    ]
    if any(t in text for t in multi_period_terms):
        return True

    if any(t in rep for t in ["q1", "quarter", "half", "nine", "q3"]):
        return True

    # Revenue buildup formats are risky because the first revenue-like row may be
    # gross sales while GrossProfit reconciles against net sales/revenue.
    revenue_buildup_terms = [
        "sales tax", "sales return", "discount", "tariff", "net sales",
        "net revenue", "gross sales", "gross revenue", "turnover",
    ]
    if sum(1 for t in revenue_buildup_terms if t in text) >= 2:
        return True

    return False


def should_try_layout_fallback(values: Dict[str, Any], pdf_text: str, symbol: str, report: Dict[str, Any], base_score: int) -> bool:
    """Decide whether to try layout fallback.

    This intentionally combines presence and correctness signals:
    - weak field coverage,
    - risky multi-column/PSO-shaped content,
    - or failed P&L arithmetic.
    """
    if base_score < int(os.getenv("PSX_LAYOUT_FALLBACK_SCORE", "25")):
        return True
    if not pl_arithmetic_sanity_ok(values):
        return True
    if pdf_looks_like_multi_column_or_layout_risk(pdf_text, symbol, report):
        return True
    return False


def choose_better_extraction(
    base_values: Dict[str, Any],
    base_evidence: Dict[str, Any],
    base_warnings: List[str],
    layout_values: Dict[str, Any],
    layout_evidence: Dict[str, Any],
    layout_warnings: List[str],
    base_score: int,
    layout_score: int,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """Choose between legacy/OCR and layout extraction.

    A higher fill-count alone is not enough. Prefer a result that passes P&L
    arithmetic; otherwise use score as the secondary tie-breaker.
    """
    base_ok = pl_arithmetic_sanity_ok(base_values)
    layout_ok = pl_arithmetic_sanity_ok(layout_values)

    if layout_ok and not base_ok:
        return layout_values, layout_evidence, [
            f"layout fallback selected by P&L sanity; embedded/OCR score {base_score}, layout score {layout_score}."
        ] + layout_warnings

    if layout_ok == base_ok and layout_score > base_score:
        return layout_values, layout_evidence, [
            f"layout fallback selected by quality score; embedded/OCR score {base_score}, layout score {layout_score}."
        ] + layout_warnings

    reason = "P&L sanity passed" if base_ok else "layout did not improve P&L sanity"
    return base_values, base_evidence, base_warnings + [
        f"layout fallback tried but original extraction kept; {reason}; score {base_score} vs layout {layout_score}."
    ]



def text_looks_garbled(pdf_text: str) -> bool:
    """Detect embedded text extracted with a broken custom font/encoding.

    Examples seen in PSX PDFs include control-like characters and pages where vowels disappear,
    causing labels such as financial position/profit/cash flow to be unreadable. In that case OCR
    is safer than trusting the PDF text stream.
    """
    if not pdf_text:
        return False
    sample = pdf_text[:60000]
    if len(sample) < 1000:
        return False
    bad = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\n\r\t")
    replacementish = sample.count("\ufffd") + sample.count("�")
    alpha = sum(1 for ch in sample if ch.isalpha())
    # Ratio catches custom-font gibberish while normal English PDFs stay safely below it.
    if bad + replacementish > max(25, len(sample) * 0.01):
        return True
    if alpha < len(sample) * 0.22 and any(tok in sample for tok in ["\x02", "\x03", "\x04", "\x05"]):
        return True
    return False


def should_try_ocr_recovery(values: Dict[str, Any], warnings: List[str], pdf_text: str) -> bool:
    """Try OCR only for weak/garbled parses, not for every normal text report."""
    filled = count_filled_financial_fields(values)
    core = count_core_financial_fields(values)
    warn_text = " | ".join(str(x) for x in warnings).lower()
    if "tesseractnotfounderror" in warn_text:
        return False
    if text_looks_garbled(pdf_text):
        return True
    if filled <= 5 or core <= 3:
        return True
    return False


def normalize_extraction_warnings(values: Dict[str, Any], warnings: List[str], symbol: str = "") -> List[str]:
    """Separate true risk from known non-applicable/missing-disclosure situations.

    This does not make unsafe values OK. It only changes wording so the terminal tester can distinguish:
    - OCR/scanned/failed/poor parse = CHECK
    - bank current-asset/current-liability not applicable = informational
    - retained earnings not separately disclosed = informational when the report otherwise parsed well
    """
    out: List[str] = []
    filled = count_filled_financial_fields(values)
    core = count_core_financial_fields(values)
    for w in warnings or []:
        text = str(w)
        low = text.lower()
        if "not found / needs manual review" in low:
            missing_part = text.split(":", 1)[1] if ":" in text else text
            missing = [x.strip() for x in missing_part.split(",") if x.strip()]
            # If an optional AI/OCR fallback filled a value after the original warning was created,
            # do not keep the stale field in the manual-review list.
            missing = [m for m in missing if values.get(m) is None]
            # These are not meaningful for banks and should not mark extraction as failed.
            bank_non_applicable = {"CurrentAssets", "CurrentLiabilities", "WorkingCapital", "CurrentRatio"}
            missing = [m for m in missing if m not in bank_non_applicable]
            # If UnappropriatedProfit is not separately disclosed but equity/reserves are populated, flag disclosure not failure.
            if "UnappropriatedProfit" in missing and values.get("Reserves") is not None and values.get("ShareholdersEquity") is not None and filled >= 30:
                out.append("INFO: UnappropriatedProfit not separately disclosed; retained earnings may be included in reserves/equity disclosure.")
                missing = [m for m in missing if m != "UnappropriatedProfit"]
            # If cash-flow fields are absent from a very strong parse, keep soft warning not hard review.
            cf_missing = {"CashFlowFromOperatingActivities", "CashFlowFromInvestingActivities", "CashFlowFromFinancingActivities"}
            if filled >= 36 and set(missing).issubset(cf_missing):
                out.append("INFO: Cash-flow subtotal field(s) not separately identified by the generic mapper; main statement values parsed.")
                missing = []
            if missing:
                out.append("Not found / needs manual review: " + ", ".join(missing))
            continue
        if "unapprft was not separately disclosed" in low and filled >= 30:
            out.append("INFO: UnappropriatedProfit not separately disclosed; not treated as extraction failure when reserve/equity totals are present.")
            continue
        out.append(text)
    return out



# -----------------------------
# offline AI optional LangChain/Ollama fallback
# -----------------------------
def offline_ai_enabled() -> bool:
    """Offline AI fallback is opt-in so the no-API deterministic build remains stable."""
    return os.getenv("PSX_ENABLE_LANGCHAIN_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}


def missing_fields_for_ai(values: Dict[str, Any]) -> List[str]:
    """Fields worth asking the fallback model about.

    We intentionally keep the list focused. The AI fallback must not become a free-form
    second extractor for every field; it should only recover important missing values.
    """
    preferred = [
        "Sales", "CostOfSales", "GrossProfit", "OperatingExpenses", "FinanceCosts", "OtherIncome",
        "ProfitBeforeTax", "Taxation", "ProfitAfterTax", "PaidUpCapital", "Reserves", "UnappropriatedProfit",
        "ShareholdersEquity", "CurrentAssets", "CurrentLiabilities", "CashAndBankBalances", "CashFlowFromOperatingActivities", "CashFlowFromInvestingActivities", "CashFlowFromFinancingActivities",
    ]
    return [f for f in preferred if f in BALNSHET_FIELDS and values.get(f) is None]


def should_try_offline_ai(values: Dict[str, Any], warnings: List[str], text: str) -> bool:
    if not offline_ai_enabled():
        return False
    if not (text or "").strip():
        return False
    missing = missing_fields_for_ai(values)
    min_missing = int(os.getenv("PSX_LANGCHAIN_MIN_MISSING", "3") or "3")
    if len(missing) >= min_missing:
        return True
    warn = " | ".join(str(w).lower() for w in (warnings or []))
    return any(x in warn for x in ["poor parse", "needs manual review", "not found / needs manual review"])


def split_pages_for_ai(full_text: str) -> List[Tuple[int, str]]:
    matches = list(re.finditer(r"\n---\s*page\s+(\d+)\s*---\n", full_text or "", re.I))
    if not matches:
        return [(0, full_text or "")]
    pages: List[Tuple[int, str]] = []
    for i, m in enumerate(matches):
        page_no = int(m.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        pages.append((page_no, full_text[m.end():end]))
    return pages


def compact_text_for_ai(full_text: str, max_chars: int = 18000) -> str:
    """Send only likely statement pages to the LLM.

    This keeps Ollama prompts manageable and reduces hallucination risk from narrative pages.
    """
    pages = split_pages_for_ai(full_text)
    scored: List[Tuple[int, int, str]] = []
    keywords = [
        "statement of financial position", "balance sheet", "assets", "equity and liabilities",
        "statement of profit or loss", "statement of comprehensive income", "revenue", "gross profit",
        "profit before", "taxation", "cash flows", "operating activities", "investing activities", "financing activities",
    ]
    for page_no, page_text in pages:
        low = page_text.lower()
        score = sum(8 for k in keywords if k in low)
        score += min(30, len(re.findall(r"\(?-?\d{1,3}(?:,\d{3})+\)?", page_text)))
        if score > 0:
            scored.append((score, page_no, page_text))
    if not scored:
        return (full_text or "")[:max_chars]
    scored.sort(key=lambda x: (-x[0], x[1]))
    # Keep top pages, then restore natural order.
    chosen = sorted(scored[:8], key=lambda x: x[1])
    chunks = []
    for _, page_no, page_text in chosen:
        # Remove very long whitespace but keep row-ish line breaks.
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in page_text.splitlines()]
        lines = [ln for ln in lines if ln]
        chunks.append(f"--- PAGE {page_no} ---\n" + "\n".join(lines[:220]))
    compact = "\n\n".join(chunks)
    return compact[:max_chars]


def json_schema_for_offline_ai(fields: List[str]) -> Dict[str, Any]:
    field_obj = {
        "type": "object",
        "properties": {
            "value": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "source_label": {"type": "string"},
            "source_page": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
        },
        "required": ["value", "source_label", "source_page", "confidence", "reason"],
        "additionalProperties": False,
    }
    return {
        "title": "PsxFinancialFieldFallback",
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "properties": {f: field_obj for f in fields},
                "additionalProperties": False,
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["fields", "warnings"],
        "additionalProperties": False,
    }


def to_plain_dict_for_ai(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return {}


def try_offline_ai_fallback(
    values: Dict[str, Any],
    evidence: Dict[str, str],
    warnings: List[str],
    report: Dict[str, Any],
    year: int,
    symbol: str,
    full_text: str,
) -> None:
    """Optional offline LLM fallback using LangChain + Ollama.

    Safety rules:
    - Opt-in only via PSX_ENABLE_LANGCHAIN_FALLBACK=1.
    - Fills only missing fields. It never overwrites deterministic values.
    - Requires page/label/confidence and rejects low-confidence values.
    - Values are expected in actual rupees, after applying the PDF unit (thousand/million).
    """
    if not should_try_offline_ai(values, warnings, full_text):
        return

    missing_fields = missing_fields_for_ai(values)
    if not missing_fields:
        return

    try:
        from langchain_ollama import ChatOllama  # type: ignore
    except Exception as exc:
        warnings.append(
            "LangChain/Ollama fallback enabled but packages are missing. "
            "Run INSTALL_LANGCHAIN_OLLAMA.bat. "
            f"Import error: {type(exc).__name__}: {exc}"
        )
        return

    model = os.getenv("PSX_OLLAMA_MODEL", "llama3.2")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    min_conf = float(os.getenv("PSX_LANGCHAIN_MIN_CONFIDENCE", "0.82") or "0.82")
    fields = missing_fields[: int(os.getenv("PSX_LANGCHAIN_MAX_FIELDS", "12") or "12")]
    compact_text = compact_text_for_ai(full_text, int(os.getenv("PSX_LANGCHAIN_MAX_CHARS", "18000") or "18000"))

    prompt = {
        "task": "Extract missing BalnShet fields from Pakistani PSX financial statement text.",
        "rules": [
            "Use ONLY the supplied statement text; do not guess.",
            "Return values in actual rupees. If the PDF says 'Rupees in thousand', multiply by 1000. If it says million, multiply by 1000000.",
            "Use the current target period/year column, not comparative columns.",
            "For Q3/Nine Months use cumulative nine-month figures, not quarter-only figures.",
            "Brackets mean negative values.",
            "If a value is not clearly present, return null with confidence below 0.5.",
            "Prefer exact total rows over subtotals and notes.",
        ],
        "report": {
            "symbol": symbol,
            "year": year,
            "reportType": report.get("reportType"),
            "title": report.get("title"),
            "periodEnded": report.get("periodEnded"),
        },
        "missingFields": fields,
        "existingDeterministicValues": {k: values.get(k) for k in BALNSHET_FIELDS if values.get(k) is not None},
        "statementText": compact_text,
    }

    try:
        llm = ChatOllama(model=model, base_url=base_url, temperature=0)
        structured = llm.with_structured_output(json_schema_for_offline_ai(fields))
        result = structured.invoke(json.dumps(prompt, ensure_ascii=False))
        data = to_plain_dict_for_ai(result)
    except Exception as exc:
        warnings.append(f"LangChain/Ollama fallback failed; deterministic values kept: {type(exc).__name__}: {exc}")
        return

    applied: List[str] = []
    rejected: List[str] = []
    fields_data = data.get("fields") or {}
    if not isinstance(fields_data, dict):
        warnings.append("LangChain/Ollama fallback returned no structured fields; deterministic values kept.")
        return

    for field in fields:
        if values.get(field) is not None:
            continue
        info = fields_data.get(field)
        if not isinstance(info, dict):
            continue
        val = info.get("value")
        conf = float(info.get("confidence") or 0)
        label = str(info.get("source_label") or "").strip()
        page = info.get("source_page")
        reason = str(info.get("reason") or "").strip()
        if val is None:
            continue
        try:
            intval = int(val)
        except Exception:
            rejected.append(f"{field}: non-integer value")
            continue
        if conf < min_conf or not label:
            rejected.append(f"{field}: low confidence/no label ({conf:.2f})")
            continue
        # Guard against obvious statement-date false positives.
        if abs(intval) in {30, 31, 30000, 31000} and re.search(r"march|june|september|december|as at|ended", label, re.I):
            rejected.append(f"{field}: looked like date token")
            continue
        values[field] = intval
        evidence[field] = f"LangChain/Ollama fallback page {page}: {label} => {intval}; confidence={conf:.2f}; {reason}"
        applied.append(field)

    if applied:
        warnings.append("LangChain/Ollama fallback filled missing fields: " + ", ".join(sorted(applied)))
    if rejected:
        warnings.append("LangChain/Ollama fallback rejected unsafe fields: " + " | ".join(rejected[:8]))
    for w in data.get("warnings") or []:
        if isinstance(w, str) and w.strip():
            warnings.append("LangChain/Ollama fallback note: " + w.strip())

def extract_reports(payload: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:  # type: ignore[override]
    reports = payload.get("reports") or []
    year = int(payload.get("year") or datetime.now().year)
    symbol = str(payload.get("symbol") or "").strip().upper()
    company = str(payload.get("companyName") or "").strip()
    out_reports: List[Dict[str, Any]] = []
    all_warnings: List[str] = []

    for report in reports:
        url = str(report.get("url") or "").strip()
        if not url or BASE_HOST not in urlparse(url).netloc.lower():
            out_reports.append(empty_report(report, "Skipped: URL is not financials.psx.com.pk"))
            continue
        try:
            pdf_path = download_pdf(url, ctx.downloads)
            legacy_content = None
            _mapping_text = ""

            # database-save: choose extraction strategy from PDF/text quality, not ticker hardcoding.
            # First try the embedded-text statement-page engine. If the PDF has no usable
            # embedded text, use OCR. If the embedded-text result is weak, try the layout
            # extractor and keep it only when it scores better.
            try:
                legacy_content = extract_pdf_content(pdf_path)
                _mapping_text = getattr(legacy_content, "text", "") or ""
                values, evidence, warnings = extract_values_from_content(legacy_content, report, year)
                warnings = ["hybrid extraction: embedded-text statement-page engine used, then client normalization applied."] + warnings
            except Exception as legacy_exc:
                if looks_like_text_short_exception(legacy_exc):
                    values, evidence, warnings = ocr_extract_scanned_pdf_ocr_values(pdf_path, report, year, symbol)
                    warnings = ["hybrid extraction: embedded text was unavailable, so scanned/OCR fallback was used."] + warnings
                else:
                    try:
                        values, evidence, warnings = layout_extract_pdf_values(pdf_path, report, year, symbol)
                        warnings = [f"hybrid extraction: embedded-text parser failed ({type(legacy_exc).__name__}); layout extractor used."] + warnings
                    except Exception:
                        raise legacy_exc

            try:
                base_score = values_quality_score(values)
                if should_try_layout_fallback(values, _mapping_text, symbol, report, base_score):
                    layout_values, layout_evidence, layout_warnings = layout_extract_pdf_values(pdf_path, report, year, symbol)
                    layout_score = values_quality_score(layout_values)
                    values, evidence, warnings = choose_better_extraction(
                        values, evidence, warnings,
                        layout_values, layout_evidence, layout_warnings,
                        base_score, layout_score,
                    )
                else:
                    warnings.append(f"layout fallback not needed; score {base_score}, content/routing sanity checks passed.")
            except Exception as layout_exc:
                warnings.append(f"layout fallback unavailable/failed; original extraction kept: {type(layout_exc).__name__}: {layout_exc}")

            client_mapping_apply_client_mapping(symbol, year, report, values, evidence, warnings, _mapping_text)

            # OCR recovery: if embedded PDF text gave a weak or garbled parse, try local OCR
            # as a recovery path. This is generic and only accepts OCR values when
            # they score materially better than the original text parse.
            try:
                if should_try_ocr_recovery(values, warnings, _mapping_text):
                    before_score = values_quality_score(values)
                    # Heavy OCR over long annual PDFs can take many minutes. By default,
                    # run automatic OCR recovery only on smaller/interim PDFs. Users can
                    # enable long-report OCR with: set PSX_ENABLE_HEAVY_OCR=1
                    try:
                        import fitz as _ocr_fitz
                        _ocr_doc = _ocr_fitz.open(str(pdf_path))
                        _ocr_pages = len(_ocr_doc)
                        _ocr_doc.close()
                    except Exception:
                        _ocr_pages = 0
                    if _ocr_pages > int(os.getenv("PSX_OCR_AUTO_MAX_PAGES", "90")) and os.getenv("PSX_ENABLE_HEAVY_OCR", "0") != "1":
                        warnings.append(f"OCR recovery skipped for {_ocr_pages}-page report to avoid long runtime. Set PSX_ENABLE_HEAVY_OCR=1 for heavy OCR.")
                        raise RuntimeError("OCR recovery heavy OCR intentionally skipped")
                    ocr_values, ocr_evidence, ocr_warnings = ocr_extract_scanned_pdf_ocr_values(pdf_path, report, year, symbol)
                    client_mapping_apply_client_mapping(symbol, year, report, ocr_values, ocr_evidence, ocr_warnings, "")
                    after_score = values_quality_score(ocr_values)
                    if after_score >= before_score + 10:
                        values, evidence, warnings = ocr_values, ocr_evidence, [
                            f"OCR recovery replaced weak embedded-text parse; score {before_score} -> {after_score}."
                        ] + ocr_warnings
                    else:
                        warnings.append(f"OCR recovery tried but deterministic text parse was kept; score {before_score} vs OCR {after_score}.")
            except Exception as ocr_exc:
                warnings.append(f"OCR recovery unavailable/failed; deterministic text parse kept: {type(ocr_exc).__name__}: {ocr_exc}")

            # offline AI optional offline AI fallback. Runs only when explicitly enabled and only
            # fills missing fields; deterministic values remain the source of truth.
            try:
                try_offline_ai_fallback(values, evidence, warnings, report, year, symbol, _mapping_text)
            except Exception as ai_exc:
                warnings.append(f"LangChain/Ollama fallback failed safely; deterministic values kept: {type(ai_exc).__name__}: {ai_exc}")

            warnings = normalize_extraction_warnings(values, warnings, symbol)

            values["CompanyCode"] = payload.get("compCode") or None
            values["FinancialYear"] = year
            values["PeriodEndDate"] = normalize_tran_date(report.get("periodEnded")) or normalize_tran_date(values.get("PeriodEndDate")) or None
            values["AmountMultiplier"] = values.get("AmountMultiplier") or 1000

            out_reports.append({
                "reportId": report.get("id"),
                "reportType": report.get("reportType"),
                "title": report.get("title"),
                "tranDate": values.get("PeriodEndDate") or normalize_tran_date(report.get("periodEnded")) or "",
                "published": report.get("published") or "",
                "url": url,
                "localPdf": str(pdf_path),
                "values": values,
                "warnings": warnings[:80],
                "evidence": evidence,
            })
            all_warnings.extend([f"{report.get('reportType') or report.get('title')}: {w}" for w in warnings])
        except Exception as exc:
            out_reports.append(empty_report(report, f"Extraction failed: {type(exc).__name__}: {exc}"))
            all_warnings.append(f"{report.get('title')}: {type(exc).__name__}: {exc}")

    return {
        "ok": True,
        "symbol": symbol,
        "companyName": company,
        "year": year,
        "reports": out_reports,
        "summary": {
            "selectedReports": len(reports),
            "extractedReports": len(out_reports),
            "sourceLockedTo": BASE_URL,
            "warnings": all_warnings[:80]
        }
    }


def layout_blank_values() -> Dict[str, Any]:
    return {field: None for field in BALNSHET_FIELDS}


def layout_extract_pdf_values(pdf_path: Path, report: Dict[str, Any], year: int, symbol: str) -> Tuple[Dict[str, Any], Dict[str, str], List[str]]:
    import fitz
    values = layout_blank_values()
    evidence: Dict[str, str] = {}
    warnings: List[str] = []

    doc = fitz.open(str(pdf_path))
    try:
        # First try text/layout extraction. This handles PSO Annual 2025 and other normal text PDFs.
        text_page_count = 0
        for page in doc:
            if (page.get_text("text") or "").strip():
                text_page_count += 1
        warnings.append(f"PSX extractor: PDF pages={len(doc)}, embedded-text pages={text_page_count}.")

        if text_page_count > 0:
            layout_extract_text_layout(doc, year, values, evidence, warnings)
        elif len(doc) > 0:
            # Generic scanned annual fallback for any symbol routed through this extractor.
            try:
                return ocr_extract_scanned_pdf_ocr_values(pdf_path, report, year, symbol)
            except Exception as exc:
                warnings.append(f"Scanned annual OCR fallback skipped/failed: {type(exc).__name__}: {exc}")

        # For quarterly/scanned reports, use local OCR to read image statement pages.
        # OCR expansion expands OCR beyond cash flows: it also maps the quarterly financial
        # position and profit/loss pages when those pages are images. No API is used.
        is_quarterly = "quarter" in str(report.get("title") or report.get("reportType") or "").lower() or "q" in str(report.get("reportType") or "").lower()
        if is_quarterly:
            try:
                layout_extract_ocr_quarterly_statements(doc, year, values, evidence, warnings)
            except Exception as exc:
                warnings.append(f"OCR fallback skipped/failed: {type(exc).__name__}: {exc}")

        # Derived values and final checks.
        if values.get("WorkingCapital") is None and is_number(values.get("CurrentAssets")) and is_number(values.get("CurrentLiabilities")):
            values["WorkingCapital"] = int(values["CurrentAssets"]) - int(values["CurrentLiabilities"])
            evidence["WorkingCapital"] = "Calculated as CurrentAssets - CurrentLiabilities."

        values["AmountMultiplier"] = 1000
        warnings.append("no-API mode: PSO uses layout/OCR extraction; non-PSO uses legacy general statement-page fallback when stronger.")
    finally:
        doc.close()

    return values, evidence, warnings


def layout_extract_text_layout(doc: Any, year: int, values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str]) -> None:
    """Coordinate-based extraction for text PDFs."""
    fp_pages: List[int] = []
    pl_pages: List[int] = []
    cf_pages: List[int] = []
    for i, page in enumerate(doc):
        txt = normalize(page.get_text("text") or "")
        if not txt:
            continue
        # Prefer pages that contain statement-like row content. PSO annual text lacks the title on the page, so use row clues.
        has_unit_table = ("rupees in" in txt or "rs in" in txt or "amounts in" in txt) and "note" in txt
        if has_unit_table and ("assets" in txt and "equity" in txt and "liabilities" in txt and ("current assets" in txt or "total assets" in txt)):
            fp_pages.append(i)
        if has_unit_table and (("net sales" in txt or "gross sales" in txt or "revenue" in txt) and ("profit before" in txt or "profit for" in txt or "gross profit" in txt)):
            pl_pages.append(i)
        if has_unit_table and ("cash flows from operating activities" in txt and ("net cash" in txt or "cash generated" in txt)):
            cf_pages.append(i)

    # Keep first statement pages with actual Note/Rupees table headers. This avoids analysis/ratio pages.
    fp_pages = fp_pages[:2]
    pl_pages = pl_pages[:2]
    cf_pages = cf_pages[:2]
    warnings.append(f"Text-layout candidate pages: FP={[p+1 for p in fp_pages]}, PL={[p+1 for p in pl_pages]}, CF={[p+1 for p in cf_pages]}.")

    # Financial position values.
    for pi in fp_pages:
        rows = layout_rows_from_text_page(doc[pi], year)
        if not rows:
            continue
        layout_map_fp_rows(rows, pi + 1, values, evidence)

    # Profit/loss values.
    for pi in pl_pages:
        rows = layout_rows_from_text_page(doc[pi], year)
        if not rows:
            continue
        layout_map_pl_rows(rows, pi + 1, values, evidence)

    # Cash-flow values.
    for pi in cf_pages:
        rows = layout_rows_from_text_page(doc[pi], year)
        if not rows:
            continue
        layout_map_cf_rows(rows, pi + 1, values, evidence)


def layout_rows_from_text_page(page: Any, year: int) -> List[Dict[str, Any]]:
    words_raw = page.get_text("words") or []
    words = []
    for w in words_raw:
        x0, y0, x1, y1, text = w[:5]
        text = clean_text(str(text))
        if text:
            words.append({"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1), "text": text})
    if not words:
        return []

    # Detect the x-position of target year and comparative year in the page header.
    target_xs = [((w["x0"] + w["x1"]) / 2) for w in words if w["text"] == str(year) and w["y0"] < page.rect.height * 0.35 and ((w["x0"] + w["x1"]) / 2) > page.rect.width * 0.55]
    current_x = min(target_xs) if target_xs else None
    if current_x is None:
        # fallback: first amount column on right half
        current_x = page.rect.width * 0.70

    # Group words into lines using y coordinate.
    lines: Dict[int, List[Dict[str, Any]]] = {}
    for w in words:
        key = int(round(w["y0"] / 5.0) * 5)
        lines.setdefault(key, []).append(w)

    out = []
    for _, line_words in sorted(lines.items()):
        line_words = sorted(line_words, key=lambda x: x["x0"])
        label_tokens = []
        nums = []
        for w in line_words:
            t = w["text"]
            cx = (w["x0"] + w["x1"]) / 2
            if layout_is_number_token(t):
                # Ignore note numbers and year/header numbers.
                if cx < page.rect.width * 0.62 and re.fullmatch(r"\d{1,2}(?:\.\d)?", t):
                    continue
                if re.fullmatch(r"(?:19|20)\d{2}", t):
                    continue
                nums.append({"token": t, "x": cx, "value": layout_parse_number_token(t)})
            else:
                if w["x0"] < page.rect.width * 0.62:
                    label_tokens.append(t)
        label = clean_text(" ".join(label_tokens))
        if not label or not nums:
            continue
        # Choose current-year column by nearest x position to target year header.
        nums = [n for n in nums if n["value"] is not None]
        if not nums:
            continue
        chosen = min(nums, key=lambda n: abs(float(n["x"]) - float(current_x)))
        out.append({"label": label, "norm": normalize(label), "value": chosen["value"], "raw": chosen["token"], "x": chosen["x"], "current_x": current_x})
    return out


def layout_is_number_token(t: str) -> bool:
    t = t.strip().replace("−", "-").replace("–", "-").replace("—", "-")
    return re.fullmatch(r"\(?-?[\d,]+(?:\.\d+)?\)?[\]\}]?|-", t) is not None


def layout_parse_number_token(t: str) -> Optional[int]:
    s = t.strip().replace("−", "-").replace("–", "-").replace("—", "-")
    if s == "-":
        return 0
    neg = "(" in s and ")" in s
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s == "-":
        return None
    try:
        val = float(s)
        if neg and val > 0:
            val = -val
        return int(round(val))
    except Exception:
        return None


def layout_set(values: Dict[str, Any], evidence: Dict[str, str], field: str, val_printed: Optional[int], page_no: int, label: str, source: str = "layout") -> None:
    if val_printed is None:
        return
    # Keep first good value unless replacing blank.
    if values.get(field) is None:
        values[field] = int(val_printed) * 1000
        evidence[field] = f"PDF page {page_no}: {label} => {val_printed:,}; unit=1000; source={source}; selected target-year/current-period column."


def layout_find_row(rows: List[Dict[str, Any]], *needles: str, exclude: Tuple[str, ...] = ()) -> Optional[Dict[str, Any]]:
    for r in rows:
        n = r["norm"]
        if all(x in n for x in needles) and not any(x in n for x in exclude):
            return r
    return None


def layout_map_fp_rows(rows: List[Dict[str, Any]], page_no: int, values: Dict[str, Any], evidence: Dict[str, str]) -> None:
    mapping = [
        ("PaidUpCapital", ("share", "capital"), ()),
        ("Reserves", ("reserves",), ()),
        ("FixedAssets", ("property", "plant", "equipment"), ()),
        ("CashAndBankBalances", ("cash", "bank", "balances"), ()),
        ("StockInTrade", ("stock", "trade"), ()),
        ("StoresAndSpares", ("stores", "spares"), ()),
        ("TradeDebts", ("trade", "debts"), ()),
        ("LongTermInvestments", ("long", "term", "investments"), ()),
        ("ShortTermInvestments", ("short", "term", "investments"), ()),
        ("TradeAndOtherPayables", ("trade", "other", "payables"), ()),
    ]
    for field, inc, exc in mapping:
        row = layout_find_row(rows, *inc, exclude=exc)
        if row:
            layout_set(values, evidence, field, row["value"], page_no, row["label"])

    # Sum current assets/liabilities from components. This avoids unlabeled total rows and proves PSO annual correctly.
    curr_asset_specs = [
        (("stores", "spares"), ()), (("stock", "trade"), ()), (("trade", "debts"), ()),
        (("loans", "advances"), ("long", "term")),
        (("short", "term", "deposits", "prepayments"), ()), (("other", "receivables"), ("long", "term")),
        (("taxation", "net"), ()), (("short", "term", "investments"), ()), (("cash", "bank", "balances"), ()),
    ]
    asset_sum = 0; asset_rows = []
    for inc, exc in curr_asset_specs:
        row = layout_find_row(rows, *inc, exclude=exc)
        if row:
            asset_sum += int(row["value"])
            asset_rows.append(row["label"])
    if asset_rows and values.get("CurrentAssets") is None:
        values["CurrentAssets"] = asset_sum * 1000
        evidence["CurrentAssets"] = f"PDF page {page_no}: calculated from current asset rows ({'; '.join(asset_rows)}); unit=1000."

    curr_liab_specs = [
        (("trade", "other", "payables"), ()), (("short", "term", "borrowings"), ()), (("accrued", "interest"), ()),
        (("provisions",), ()), (("current", "portion", "lease", "liabilities"), ()), (("unclaimed", "dividend"), ()),
    ]
    liab_sum = 0; liab_rows = []
    for inc, exc in curr_liab_specs:
        row = layout_find_row(rows, *inc, exclude=exc)
        if row:
            liab_sum += int(row["value"])
            liab_rows.append(row["label"])
    if liab_rows and values.get("CurrentLiabilities") is None:
        values["CurrentLiabilities"] = liab_sum * 1000
        evidence["CurrentLiabilities"] = f"PDF page {page_no}: calculated from current liability rows ({'; '.join(liab_rows)}); unit=1000."

    if values.get("ShareholdersEquity") is None and is_number(values.get("PaidUpCapital")) and is_number(values.get("Reserves")):
        values["ShareholdersEquity"] = int(values["PaidUpCapital"]) + int(values["Reserves"])
        evidence["ShareholdersEquity"] = "Calculated as PaidUpCapital + Reserves because UnappropriatedProfit was not separately disclosed."


def layout_find_row_exact_or_specific(rows: List[Dict[str, Any]], exact_norms: Tuple[str, ...] = (), include: Tuple[str, ...] = (), exclude: Tuple[str, ...] = ()) -> Optional[Dict[str, Any]]:
    """Find a row without allowing broad labels like 'profit before taxation' to match taxation."""
    exact_set = {normalize(x) for x in exact_norms}
    for r in rows:
        n = r["norm"]
        if n in exact_set:
            return r
    if include:
        return layout_find_row(rows, *include, exclude=exclude)
    return None


def layout_map_pl_rows(rows: List[Dict[str, Any]], page_no: int, values: Dict[str, Any], evidence: Dict[str, str]) -> None:
    # Core P/L rows. Keep broad matching for normal fields, but handle tax and operating expenses below
    # because rows such as "Profit Before Taxation" also contain the word "taxation".
    candidates = [
        ("Sales", [("net", "sales"), ("revenue",)]),
        ("CostOfSales", [("cost", "products", "sold"), ("cost", "sales")]),
        ("GrossProfit", [("gross", "profit")]),
        ("OtherIncome", [("other", "income")]),
        ("FinanceCosts", [("finance", "costs"), ("finance", "cost")]),
        ("ProfitBeforeTax", [("profit", "before", "taxation"), ("profit", "before", "tax")]),
        ("ProfitAfterTax", [("profit", "for", "year"), ("profit", "for", "period")]),
    ]
    for field, incs in candidates:
        for inc in incs:
            row = layout_find_row(rows, *inc)
            if row:
                layout_set(values, evidence, field, row["value"], page_no, row["label"])
                break

    # Tax/provision must come from the actual tax row only, not "Profit Before Taxation".
    tax_row = layout_find_row_exact_or_specific(
        rows,
        exact_norms=("taxation", "provision for taxation", "income tax expense", "tax expense"),
        include=("taxation",),
        exclude=("profit", "before", "after", "asset", "net", "minimum", "final")
    )
    if tax_row:
        layout_set(values, evidence, "Taxation", tax_row["value"], page_no, tax_row["label"])

    # Operating expense should be total operating costs/expenses when disclosed.
    # PSO style shows components, so sum distribution/marketing + administrative + other expenses.
    total_op_row = layout_find_row(rows, "total", "operating", "costs") or layout_find_row(rows, "total", "operating", "expenses")
    if total_op_row:
        layout_set(values, evidence, "OperatingExpenses", total_op_row["value"], page_no, total_op_row["label"])
    elif values.get("OperatingExpenses") is None:
        op_component_specs = [
            (("distribution", "marketing", "expenses"), ()),
            (("selling", "distribution", "expenses"), ()),
            (("administrative", "expenses"), ()),
            (("admin", "expenses"), ()),
            (("other", "expenses"), ("comprehensive",)),
        ]
        total = 0
        used = []
        seen_labels = set()
        for inc, exc in op_component_specs:
            row = layout_find_row(rows, *inc, exclude=exc)
            if row and row["label"] not in seen_labels:
                total += int(row["value"] or 0)
                used.append(row["label"])
                seen_labels.add(row["label"])
        # Use sum only when we found at least two components; one component alone may be too narrow.
        if len(used) >= 2:
            values["OperatingExpenses"] = total * 1000
            evidence["OperatingExpenses"] = f"PDF page {page_no}: calculated total operating expense from components ({'; '.join(used)}); unit=1000; selected target-year/current-period column."
        elif len(used) == 1:
            # Fallback for companies that only disclose one operating expense line.
            values["OperatingExpenses"] = total * 1000
            evidence["OperatingExpenses"] = f"PDF page {page_no}: {used[0]} => {total:,}; unit=1000; source=layout; selected target-year/current-period column."


def layout_map_cf_rows(rows: List[Dict[str, Any]], page_no: int, values: Dict[str, Any], evidence: Dict[str, str]) -> None:
    for field, inc in [
        ("CashFlowFromOperatingActivities", ("net", "cash", "operating", "activities")),
        ("CashFlowFromInvestingActivities", ("net", "cash", "investing", "activities")),
        ("CashFlowFromFinancingActivities", ("net", "cash", "financing", "activities")),
    ]:
        row = layout_find_row(rows, *inc)
        if row:
            layout_set(values, evidence, field, row["value"], page_no, row["label"])



def layout_extract_ocr_quarterly_statements(doc: Any, year: int, values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str]) -> None:
    """OCR fallback for scanned/image quarterly reports.

    This uses local Tesseract only. It detects the main quarterly statement pages
    and maps the current-period column. For scanned quarterly PDFs, this
    fills financial position, profit/loss, and cash-flow fields.
    """
    try:
        import fitz  # noqa
        import pytesseract
        from PIL import Image, ImageOps, ImageEnhance
    except Exception as exc:
        raise RuntimeError("Install local OCR requirements: pip install pytesseract pillow, and install Tesseract OCR for Windows.") from exc

    def ocr_page(page: Any, scale: int = 4) -> str:
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img = ImageOps.grayscale(img)
        img = ImageEnhance.Contrast(img).enhance(2.5)
        return pytesseract.image_to_string(img, config="--psm 6")

    found_fp = found_pl = found_cf = False
    # Likely PSX quarterly statement pages first, then scan early pages.
    candidate_indices: List[int] = []
    for idx in [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]:
        if 0 <= idx < len(doc) and idx not in candidate_indices:
            candidate_indices.append(idx)
    for idx in range(min(len(doc), 22)):
        if idx not in candidate_indices:
            candidate_indices.append(idx)

    for i in candidate_indices:
        page = doc[i]
        # OCR is only needed for pages without useful embedded text.
        if (page.get_text("text") or "").strip():
            continue
        text = ocr_page(page)
        norm = normalize(text)
        if not found_fp and "statement of financial position" in norm:
            found_fp = True
            warnings.append(f"OCR financial-position page detected: PDF page {i+1}; Tesseract local OCR used.")
            layout_parse_ocr_fp_text(text, i + 1, values, evidence, warnings)
        elif not found_pl and "statement of profit or loss" in norm:
            found_pl = True
            warnings.append(f"OCR profit/loss page detected: PDF page {i+1}; Tesseract local OCR used.")
            layout_parse_ocr_pl_text(text, i + 1, values, evidence, warnings)
        elif not found_cf and ("statement of cash flows" in norm or "cash flows from operating activities" in norm):
            found_cf = True
            warnings.append(f"OCR cash-flow page detected: PDF page {i+1}; Tesseract local OCR used.")
            layout_parse_ocr_cashflow_text(text, i + 1, values, evidence, warnings)
        if found_fp and found_pl and found_cf:
            break

    if not found_fp:
        warnings.append("OCR fallback did not find a financial-position statement page.")
    if not found_pl:
        warnings.append("OCR fallback did not find a profit/loss statement page.")
    if not found_cf:
        warnings.append("OCR fallback did not find a cash-flow statement page.")


def layout_ocr_set(values: Dict[str, Any], evidence: Dict[str, str], field: str, val_printed: Optional[int], page_no: int, label: str) -> None:
    if val_printed is None:
        return
    values[field] = int(val_printed) * 1000
    evidence[field] = f"OCR PDF page {page_no}: {label} => {val_printed:,}; unit=1000; selected current-period column."


def layout_parse_ocr_fp_text(text: str, page_no: int, values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str]) -> None:
    """Map OCR text from a quarterly statement of financial position."""
    upper = text.upper()
    norm = normalize(text)


    # Generic OCR fallback for other quarterly FP pages. This will not be as strong as
    # it helps fill common rows and leaves warnings.
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]

    def first_num(line: Optional[str]) -> Optional[int]:
        if not line:
            return None
        for tok in re.findall(r"\(?-?[\d,§SOo]+(?:\.\d+)?\)?", line):
            fixed = tok.replace("§", "5").replace("S", "5").replace("O", "0").replace("o", "0")
            val = layout_parse_number_token(fixed)
            if val is not None and abs(val) >= 1000:
                return val
        return None

    def line_contains(*parts: str) -> Optional[str]:
        for line in lines:
            n = normalize(line)
            if all(p in n for p in parts):
                return line
        return None

    generic_map = [
        ("PaidUpCapital", ("share", "capital")),
        ("Reserves", ("reserves",)),
        ("FixedAssets", ("property", "plant", "equipment")),
        ("LongTermInvestments", ("long", "term", "investments")),
        ("StoresAndSpares", ("stores", "spares")),
        ("StockInTrade", ("stock", "trade")),
        ("TradeDebts", ("trade", "debts")),
        ("CashAndBankBalances", ("cash", "bank", "balances")),
        ("TradeAndOtherPayables", ("trade", "other", "payables")),
        ("ShortTermBorrowings", ("short", "term", "borrowings")),
    ]
    for field, parts in generic_map:
        ln = line_contains(*parts)
        val = first_num(ln)
        if val is not None:
            layout_ocr_set(values, evidence, field, val, page_no, ln or field)


def layout_parse_ocr_pl_text(text: str, page_no: int, values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str]) -> None:
    """Map OCR text from a quarterly profit/loss page."""
    upper = text.upper()


    # Generic OCR fallback for other quarterly PL pages.
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]

    def first_num(line: Optional[str]) -> Optional[int]:
        if not line:
            return None
        for tok in re.findall(r"\(?-?[\d,§SOo]+(?:\.\d+)?\)?", line):
            fixed = tok.replace("§", "5").replace("S", "5").replace("O", "0").replace("o", "0")
            val = layout_parse_number_token(fixed)
            if val is not None and abs(val) >= 1000:
                return val
        return None

    def line_contains(*parts: str) -> Optional[str]:
        for line in lines:
            n = normalize(line)
            if all(p in n for p in parts):
                return line
        return None

    for field, parts in [
        ("Sales", ("net", "sales")),
        ("CostOfSales", ("cost", "products", "sold")),
        ("GrossProfit", ("gross", "profit")),
        ("OtherIncome", ("other", "income")),
        ("FinanceCosts", ("finance", "costs")),
        ("ProfitBeforeTax", ("profit", "before", "taxation")),
        ("ProfitAfterTax", ("profit", "period")),
    ]:
        ln = line_contains(*parts)
        val = first_num(ln)
        if val is not None:
            layout_ocr_set(values, evidence, field, val, page_no, ln or field)

    tax_line = line_contains("taxation")
    tax_val = first_num(tax_line)
    if tax_val is not None and "profit" not in normalize(tax_line or ""):
        layout_ocr_set(values, evidence, "Taxation", tax_val, page_no, tax_line or "Taxation")


def layout_extract_ocr_cashflows(doc: Any, year: int, values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str]) -> None:
    try:
        import fitz  # noqa
        import pytesseract
        from PIL import Image, ImageOps, ImageEnhance
    except Exception as exc:
        raise RuntimeError("Install local OCR requirements: pip install pytesseract pillow, and install Tesseract OCR for Windows.") from exc

    found = False
    # Quarterly PSX PDFs usually put financial statement image pages early.
    # Try the likely cash-flow page first for PSO-style Q1 reports, then scan nearby pages.
    candidate_indices = []
    for idx in [10, 9, 8, 7, 6, 11, 12, 13, 14, 15]:
        if 0 <= idx < len(doc) and idx not in candidate_indices:
            candidate_indices.append(idx)
    for idx in range(min(len(doc), 20)):
        if idx not in candidate_indices:
            candidate_indices.append(idx)

    for i in candidate_indices:
        page = doc[i]
        # Skip pages with embedded text unless likely quarterly image statement pages.
        if (page.get_text("text") or "").strip():
            continue
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img = ImageOps.grayscale(img)
        img = ImageEnhance.Contrast(img).enhance(2.5)
        text = pytesseract.image_to_string(img, config="--psm 6")
        norm = normalize(text)
        if "statement of cash flows" not in norm and "cash flows from operating activities" not in norm:
            continue
        found = True
        warnings.append(f"OCR cash-flow page detected: PDF page {i+1}; Tesseract local OCR used.")
        layout_parse_ocr_cashflow_text(text, i + 1, values, evidence, warnings)
        break
    if not found:
        warnings.append("OCR fallback did not find a cash-flow statement page.")


def layout_parse_ocr_cashflow_text(text: str, page_no: int, values: Dict[str, Any], evidence: Dict[str, str], warnings: List[str]) -> None:
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    joined = "\n".join(lines)

    def first_num(line: str) -> Optional[int]:
        nums = re.findall(r"\(?-?[\d,]+(?:\.\d+)?\)?[\]\}]?", line)
        # Ignore note/year numbers and choose first large amount.
        for tok in nums:
            val = layout_parse_number_token(tok)
            if val is not None and abs(val) >= 1000:
                return val
        return None

    def line_contains(*parts: str) -> Optional[str]:
        for line in lines:
            n = normalize(line)
            if all(p in n for p in parts):
                return line
        return None

    # Operating: prefer total row, fallback to component sum.
    op_line = line_contains("net", "cash", "operating", "activities")
    op_val = first_num(op_line) if op_line else None
    if op_val is None:
        comp_parts = [
            ("cash generated from operations",), ("long term loans", "receivables"), ("long term deposits",),
            ("taxes paid",), ("finance costs paid",), ("retirement", "benefits", "paid"),
        ]
        vals = []
        for parts in comp_parts:
            ln = line_contains(*parts)
            if ln:
                v = first_num(ln)
                if v is not None: vals.append(v)
        if vals: op_val = sum(vals)
    if op_val is not None:
        values["CashFlowFromOperatingActivities"] = op_val * 1000
        evidence["CashFlowFromOperatingActivities"] = f"OCR PDF page {page_no}: net operating cash flow/current-period column => {op_val:,}; unit=1000."

    # Investing: compute from components when possible to avoid OCR total ambiguity.
    inv_components = []
    for parts in [("capital expenditure",), ("proceeds", "disposal", "operating"), ("dividend received",)]:
        ln = line_contains(*parts)
        if ln:
            v = first_num(ln)
            if v is not None: inv_components.append(v)
    inv_val = sum(inv_components) if len(inv_components) >= 2 else None
    inv_line = line_contains("net", "cash", "investing", "activities")
    if inv_val is None and inv_line:
        inv_val = first_num(inv_line)
    if inv_val is not None:
        values["CashFlowFromInvestingActivities"] = inv_val * 1000
        evidence["CashFlowFromInvestingActivities"] = f"OCR PDF page {page_no}: investing cash flow from current-period column/components => {inv_val:,}; unit=1000."

    # Financing: compute from components to correct common OCR error 50↔60 in total line.
    fin_components = []
    for parts in [("short", "borrowings", "net"), ("lease rentals paid",), ("dividends paid",)]:
        ln = line_contains(*parts)
        if ln:
            v = first_num(ln)
            if v is not None: fin_components.append(v)
    fin_val = sum(fin_components) if len(fin_components) >= 2 else None
    fin_line = line_contains("net", "cash", "financing", "activities")
    fin_total = first_num(fin_line) if fin_line else None
    if fin_val is None:
        fin_val = fin_total
    elif fin_total is not None and abs(fin_val - fin_total) > 1000000:
        warnings.append(f"OCR financing total disagreed with component sum ({fin_total:,} vs {fin_val:,}); used component sum.")
    if fin_val is not None:
        values["CashFlowFromFinancingActivities"] = fin_val * 1000
        evidence["CashFlowFromFinancingActivities"] = f"OCR PDF page {page_no}: financing cash flow from current-period components => {fin_val:,}; unit=1000."



if __name__ == "__main__":
    sys.exit(main())
