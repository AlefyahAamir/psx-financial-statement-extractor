
from workers.extraction.arithmetic_sanity import arithmetic_sanity_report

def test_gross_profit_sanity_passes_for_consistent_values():
    report = arithmetic_sanity_report({
        "Sales": 1_000_000,
        "CostOfSales": 600_000,
        "GrossProfit": 400_000,
    })
    assert "pl_gross_profit" not in report["failed"]

def test_gross_profit_sanity_fails_for_inconsistent_values():
    report = arithmetic_sanity_report({
        "Sales": 1_000_000,
        "CostOfSales": 600_000,
        "GrossProfit": 300_000,
    })
    assert "pl_gross_profit" in report["failed"]

def test_rough_pbt_is_advisory_not_hard_failure():
    report = arithmetic_sanity_report({
        "Sales": 1_000_000,
        "CostOfSales": 600_000,
        "OperatingExpenses": 200_000,
        "FinanceCosts": 100_000,
        "OtherIncome": 0,
        "OtherCharges": 0,
        "ProfitBeforeTax": 900_000,
    })
    assert "rough_profit_before_tax" in report["advisory"]
    assert "rough_profit_before_tax" not in report["failed"]
