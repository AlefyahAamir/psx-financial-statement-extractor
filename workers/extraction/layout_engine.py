from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .fields import BALNSHET_FIELDS
from .text_utils import clean_text, normalize


def is_number(value: Any) -> bool:
    try:
        if value is None:
            return False
        int(value)
        return True
    except Exception:
        return False


def layout_blank_values() -> Dict[str, Any]:
    return {field: None for field in BALNSHET_FIELDS}


def layout_extract_pdf_values(pdf_path: Path, report: Dict[str, Any], year: int, symbol: str, scanned_ocr_callback: Any = None) -> Tuple[Dict[str, Any], Dict[str, str], List[str]]:
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
                return scanned_ocr_callback(pdf_path, report, year, symbol) if scanned_ocr_callback else (_ for _ in ()).throw(RuntimeError('No scanned OCR callback was supplied to layout engine.'))
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



