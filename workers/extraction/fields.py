
from __future__ import annotations

BALNSHET_FIELDS = [
    "TransactionNumber", "CompanyCode", "FinancialYear", "PeriodEndDate",
    "PaidUpCapital", "Reserves", "UnappropriatedProfit", "ShareholdersEquity",
    "CurrentAssets", "CashAndBankBalances", "AdvancesAndReceivables", "FixedAssets",
    "LongTermLiabilities", "OtherLongTermLiabilities", "OtherLiabilities", "WorkingCapital",
    "Sales", "CostOfSales", "GrossProfit", "OperatingExpenses", "FinanceCosts",
    "OtherIncome", "OtherCharges", "ProfitBeforeTax", "Taxation", "ProfitAfterTax",
    "RevaluationSurplus", "CurrentRatio", "DebtRatio", "BreakupValue", "SubordinatedLoans",
    "LongTermBorrowings", "CurrentLiabilities", "CurrentPortionLongTermLiabilities",
    "ShortTermBorrowings", "TotalBorrowings", "TradeDebts", "StockInTrade",
    "StoresAndSpares", "ShortTermInvestments", "LongTermInvestments", "OtherFixedAssets",
    "LeaseFinance", "TradeAndOtherPayables", "CashFlowFromOperatingActivities",
    "CashFlowFromFinancingActivities", "CashFlowFromInvestingActivities",
    "DeferredLiabilities", "FinanceLeaseObligations", "OperatingLeaseObligations",
    "AmountMultiplier", "CurrentLeaseFinance", "DepreciationProvision", "OperatingProfit",
]

EXPENSE_FIELDS = {"CostOfSales", "OperatingExpenses", "FinanceCosts", "Taxation", "OtherCharges"}
SIGNED_FIELDS = {
    "GrossProfit", "OperatingProfit", "ProfitBeforeTax", "ProfitAfterTax",
    "CashFlowFromOperatingActivities", "CashFlowFromFinancingActivities", "CashFlowFromInvestingActivities",
    "Reserves", "UnappropriatedProfit",
}

IMPORTANT_FIELDS = [
    "Sales", "CostOfSales", "GrossProfit", "ProfitBeforeTax", "Taxation", "ProfitAfterTax",
    "CurrentAssets", "CurrentLiabilities", "WorkingCapital", "ShareholdersEquity",
    "AdvancesAndReceivables", "OtherLongTermLiabilities", "OtherLiabilities",
    "RevaluationSurplus", "SubordinatedLoans", "CurrentPortionLongTermLiabilities",
    "StoresAndSpares", "ShortTermInvestments", "LongTermInvestments", "OtherFixedAssets",
    "LeaseFinance", "DeferredLiabilities", "FinanceLeaseObligations",
    "OperatingLeaseObligations", "CurrentLeaseFinance",
]

# Fields the primary modular extraction pipeline should actively map or calculate.
# Metadata fields are handled by the worker/database layer. Ratios are calculated
# when possible and are not direct PDF row matches.
PRIMARY_EXTRACTION_FIELDS = [
    field for field in BALNSHET_FIELDS
    if field not in {
        "TransactionNumber", "CompanyCode", "FinancialYear", "PeriodEndDate",
        "CurrentRatio", "DebtRatio", "BreakupValue", "AmountMultiplier",
    }
]

CALCULATED_FIELDS = {"WorkingCapital", "CurrentRatio", "DebtRatio", "BreakupValue"}

