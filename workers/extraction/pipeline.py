
from __future__ import annotations

from typing import Any

from .fields import BALNSHET_FIELDS, CALCULATED_FIELDS, EXPENSE_FIELDS, IMPORTANT_FIELDS, PRIMARY_EXTRACTION_FIELDS
from .field_matching import best_row
from .models import ExtractionContext, StatementRow
from .row_parser import detect_amount_multiplier, parse_rows_from_text, rows_by_section
from .taxation import select_taxation_and_profit_before_tax_rows
from .text_utils import normalize

try:
    from .arithmetic_sanity import arithmetic_sanity_report
except Exception:
    def arithmetic_sanity_report(values: dict[str, Any]) -> dict[str, Any]:
        return {"failed": {}, "advisory": {}, "summary": "", "advisory_summary": ""}

FINANCIAL_POSITION_MAP = {
    "PaidUpCapital": ["issued subscribed and paid up capital", "paid up capital", "paid-up capital", "share capital", "ordinary share capital"],
    "Reserves": ["reserves", "revenue reserves", "capital reserves", "other reserves", "general reserve"],
    "UnappropriatedProfit": ["unappropriated profit", "retained earnings", "accumulated profit", "accumulated profits"],
    "ShareholdersEquity": ["total equity", "shareholders equity", "total shareholders equity", "equity attributable to owners", "capital and reserves", "net assets"],
    "CurrentAssets": ["total current assets", "current assets"],
    "CurrentLiabilities": ["total current liabilities", "current liabilities"],
    "CashAndBankBalances": ["cash and bank balances", "cash and cash equivalents", "bank balances", "cash in hand and at bank"],
    "AdvancesAndReceivables": ["advances deposits prepayments and other receivables", "advances deposits and prepayments", "trade deposits and prepayments", "prepayments and other receivables", "loans and advances", "other receivables"],
    "FixedAssets": ["property plant and equipment", "operating fixed assets", "fixed assets", "property and equipment"],
    "OtherFixedAssets": ["right of use assets", "right-of-use assets", "intangible assets", "goodwill", "other non current assets", "other long term assets"],
    "LongTermLiabilities": ["total non current liabilities", "non current liabilities", "long term liabilities", "long-term liabilities"],
    "OtherLongTermLiabilities": ["other non current liabilities", "other long term liabilities", "retirement benefit obligation", "staff retirement benefits", "gratuity"],
    "OtherLiabilities": ["other liabilities", "accrued liabilities", "accrued expenses", "other accruals"],
    "RevaluationSurplus": ["surplus on revaluation", "revaluation surplus", "surplus on revaluation of fixed assets", "surplus on revaluation of property plant and equipment"],
    "SubordinatedLoans": ["subordinated loan", "subordinated loans", "subordinated debt", "subordinated borrowings"],
    "LongTermBorrowings": ["long term borrowings", "long term financing", "long-term financing", "borrowings", "term finance certificates", "sukuk bonds", "redeemable capital"],
    "CurrentPortionLongTermLiabilities": ["current portion of borrowings", "current maturity of borrowings", "current portion of long term borrowings", "current portion of long term financing", "current maturity of long term financing"],
    "ShortTermBorrowings": ["short term borrowings", "short-term borrowings", "running finance", "short term finance", "short term running finance"],
    "TotalBorrowings": ["total borrowings", "total financing", "total debt"],
    "TradeDebts": ["trade debts", "trade receivables", "accounts receivable"],
    "StockInTrade": ["stock in trade", "stock-in-trade", "inventories", "inventory", "finished goods"],
    "StoresAndSpares": ["stores spares and loose tools", "stores and spares", "stores spare parts and loose tools", "stores spares loose tools"],
    "ShortTermInvestments": ["short term investments", "short-term investments", "other financial assets", "current portion of long term investments"],
    "LongTermInvestments": ["long term investments", "investment in associates", "investment in subsidiaries", "investment in joint ventures", "investments in subsidiary and associates"],
    "LeaseFinance": ["lease liabilities", "finance lease liabilities", "lease financing", "long term lease liability", "right of use liability"],
    "CurrentLeaseFinance": ["current portion of lease liabilities", "current lease liability", "current maturity of lease liabilities", "short term portion of lease"],
    "DeferredLiabilities": ["deferred tax liability", "deferred taxation", "deferred liabilities", "deferred tax", "deferred income tax"],
    "TradeAndOtherPayables": ["trade and other payables", "trade creditors and other payables", "creditors accrued and other liabilities", "accounts payable and accrued liabilities"],
}

PROFIT_LOSS_MAP = {
    "Sales": ["net sales", "net revenue", "sales net", "revenue from contracts", "revenue", "turnover", "sales"],
    "CostOfSales": ["cost of sales", "cost of revenue", "cost of goods sold", "cost of services", "direct costs"],
    "GrossProfit": ["gross profit", "gross loss", "gross margin"],
    "OperatingExpenses": ["total operating expenses", "operating expenses", "administrative expenses", "distribution expenses", "selling expenses"],
    "FinanceCosts": ["finance cost", "finance costs", "financial charges", "mark up expense", "markup expense", "interest expense"],
    "OtherIncome": ["other income", "other operating income", "finance and other income"],
    "OtherCharges": ["other charges", "other expenses", "other operating expenses", "impairment loss"],
    "ProfitAfterTax": ["profit for the year", "profit for the period", "profit after taxation", "profit after tax", "loss for the year"],
    "OperatingProfit": ["operating profit", "profit from operations", "loss from operations"],
    "DepreciationProvision": ["depreciation and amortization", "depreciation", "amortization"],
}

CASH_FLOW_MAP = {
    "CashFlowFromOperatingActivities": ["net cash generated from operating activities", "net cash used in operating activities", "cash generated from operating activities", "cash used in operating activities", "cash flows from operating activities"],
    "CashFlowFromInvestingActivities": ["net cash generated from investing activities", "net cash used in investing activities", "cash generated from investing activities", "cash used in investing activities", "cash flows from investing activities"],
    "CashFlowFromFinancingActivities": ["net cash generated from financing activities", "net cash used in financing activities", "cash generated from financing activities", "cash used in financing activities", "cash flows from financing activities"],
    "FinanceLeaseObligations": ["finance lease obtained", "new finance leases", "right of use assets obtained under finance lease", "finance leases obtained during the year"],
    "OperatingLeaseObligations": ["operating lease obtained", "new operating leases", "right of use assets obtained under operating lease", "operating leases obtained during the year"],
}

def scaled_value(row: StatementRow | None, field: str) -> int | None:
    if not row or not row.amounts:
        return None
    value = int(row.amounts[0]) * int(row.multiplier or 1)
    label = row.norm
    if field in EXPENSE_FIELDS:
        return abs(value)
    if field in {"ProfitBeforeTax", "ProfitAfterTax", "GrossProfit", "OperatingProfit"} and "loss" in label:
        return -abs(value)
    if field.startswith("CashFlow") and any(term in label for term in ["used", "outflow", "utilized", "utilised"]):
        return -abs(value)
    return value

def set_from_row(values: dict[str, Any], ctx: ExtractionContext, field: str, row: StatementRow | None, note: str = "") -> None:
    value = scaled_value(row, field)
    if value is None:
        return
    values[field] = value
    ctx.evidence[field] = f"PDF page {row.page}: {row.label} => {row.amounts}"
    if note:
        ctx.evidence[field] += " | " + note

def extract_from_map(values: dict[str, Any], ctx: ExtractionContext, rows: list[StatementRow], mapping: dict[str, list[str]]) -> None:
    for field, synonyms in mapping.items():
        row = best_row(rows, field, synonyms)
        set_from_row(values, ctx, field, row)

def apply_taxation_selection(values: dict[str, Any], ctx: ExtractionContext, rows: list[StatementRow]) -> None:
    tax_row, pbt_row = select_taxation_and_profit_before_tax_rows(rows)
    if pbt_row:
        set_from_row(values, ctx, "ProfitBeforeTax", pbt_row, "Selected by ProfitBeforeTax/Taxation row proximity rule.")
    if tax_row:
        set_from_row(values, ctx, "Taxation", tax_row, "Selected by Taxation rule excluding profit/loss subtotal rows.")
    if values.get("ProfitBeforeTax") is not None and values.get("Taxation") is not None:
        if int(values["ProfitBeforeTax"]) == int(values["Taxation"]):
            ctx.warnings.append("ProfitBeforeTax equals Taxation after extraction; manual review recommended.")

def apply_calculated_fields(values: dict[str, Any], ctx: ExtractionContext) -> None:
    if values.get("CurrentAssets") is not None and values.get("CurrentLiabilities") is not None:
        current_assets = int(values["CurrentAssets"])
        current_liabilities = abs(int(values["CurrentLiabilities"]))
        values["WorkingCapital"] = current_assets - current_liabilities
        ctx.evidence["WorkingCapital"] = "Calculated as CurrentAssets - abs(CurrentLiabilities)."
        if current_liabilities:
            values["CurrentRatio"] = round(current_assets / current_liabilities, 4)
            ctx.evidence["CurrentRatio"] = "Calculated as CurrentAssets / abs(CurrentLiabilities)."

    if values.get("TotalBorrowings") is not None and values.get("ShareholdersEquity") is not None:
        equity = abs(int(values["ShareholdersEquity"]))
        if equity:
            values["DebtRatio"] = round(abs(int(values["TotalBorrowings"])) / equity, 4)
            ctx.evidence["DebtRatio"] = "Calculated as abs(TotalBorrowings) / abs(ShareholdersEquity)."

    if values.get("ShareholdersEquity") is not None and values.get("PaidUpCapital") is not None:
        paid_up = abs(int(values["PaidUpCapital"]))
        if paid_up:
            values["BreakupValue"] = round(abs(int(values["ShareholdersEquity"])) / paid_up * 10, 4)
            ctx.evidence["BreakupValue"] = "Calculated from ShareholdersEquity and PaidUpCapital using face value 10."

    if values.get("Sales") is not None and values.get("CostOfSales") is not None and values.get("GrossProfit") is None:
        values["GrossProfit"] = int(values["Sales"]) - abs(int(values["CostOfSales"]))
        ctx.evidence["GrossProfit"] = "Calculated as Sales - abs(CostOfSales)."


def add_sanity_warnings(values: dict[str, Any], ctx: ExtractionContext) -> None:
    report = arithmetic_sanity_report(values)
    if report.get("summary"):
        ctx.warnings.append("Arithmetic sanity warning: " + report["summary"])
    if report.get("advisory_summary"):
        ctx.warnings.append("Arithmetic sanity advisory: " + report["advisory_summary"])

def extract_values_from_content(content: Any, report: dict[str, Any], year: int) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    text = getattr(content, "text", "") or ""
    multiplier = detect_amount_multiplier(text)
    ctx = ExtractionContext(report=report or {}, year=int(year), multiplier=multiplier)
    values: dict[str, Any] = {field: None for field in BALNSHET_FIELDS}
    values["AmountMultiplier"] = multiplier

    rows = parse_rows_from_text(text, multiplier)
    grouped = rows_by_section(rows)

    unknown_rows = grouped.get("unknown", [])
    financial_position_rows = (grouped.get("financial_position", []) + unknown_rows) or rows
    profit_loss_rows = (grouped.get("profit_loss", []) + unknown_rows) or rows
    cash_flow_rows = (grouped.get("cash_flow", []) + unknown_rows) or rows

    extract_from_map(values, ctx, financial_position_rows, FINANCIAL_POSITION_MAP)
    extract_from_map(values, ctx, profit_loss_rows, PROFIT_LOSS_MAP)
    apply_taxation_selection(values, ctx, profit_loss_rows)
    extract_from_map(values, ctx, cash_flow_rows, CASH_FLOW_MAP)

    apply_calculated_fields(values, ctx)
    add_sanity_warnings(values, ctx)

    missing = [field for field in IMPORTANT_FIELDS if values.get(field) is None]
    if missing:
        ctx.warnings.append("Not found / needs manual review: " + ", ".join(missing))

    unmapped = sorted(
        field for field in PRIMARY_EXTRACTION_FIELDS
        if field not in FINANCIAL_POSITION_MAP
        and field not in PROFIT_LOSS_MAP
        and field not in CASH_FLOW_MAP
        and field not in CALCULATED_FIELDS
        and field not in {"ProfitBeforeTax", "Taxation"}
    )
    if unmapped:
        ctx.warnings.append("Pipeline mapping coverage warning: " + ", ".join(unmapped))
    ctx.warnings.append(
        f"Modular extraction pipeline used {len(rows)} parsed statement rows. "
        f"Rows by section: financial_position={len(grouped.get('financial_position', []))}, "
        f"profit_loss={len(grouped.get('profit_loss', []))}, cash_flow={len(grouped.get('cash_flow', []))}."
    )
    return values, ctx.evidence, ctx.warnings
