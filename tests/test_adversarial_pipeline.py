
from types import SimpleNamespace

from workers.extraction.pipeline import extract_values_from_content

def test_pipeline_documents_first_column_current_period_assumption():
    """Plain-text extraction cannot read PDF x-coordinates.

    For plain embedded-text rows with two amounts, the pipeline currently treats
    the first amount as the current-period amount. This test documents that
    assumption; it is not proof that every PSX PDF orders columns this way.
    Coordinate-aware validation for target-year column selection is covered by
    test_layout_column_selection.py.
    """
    text = """
--- page 1 ---
Statement of Profit or Loss
Revenue 1,000,000 900,000
Cost of sales (600,000) (500,000)
Gross profit 400,000 400,000
Taxation (100,000) (90,000)
Profit for the year 300,000 310,000
"""
    values, _, _ = extract_values_from_content(SimpleNamespace(text=text), {"reportType": "Annual"}, 2024)

    assert values["Sales"] == 1_000_000
    assert values["CostOfSales"] == 600_000
    assert values["GrossProfit"] == 400_000
    assert values["Taxation"] == 100_000
    assert values["ProfitAfterTax"] == 300_000

def test_pipeline_handles_sign_from_loss_label():
    text = """
--- page 1 ---
Statement of Profit or Loss
Revenue 1,000,000
Cost of sales (1,200,000)
Gross loss (200,000)
Loss before taxation (250,000)
Taxation 10,000
Loss for the year (260,000)
"""
    values, _, _ = extract_values_from_content(SimpleNamespace(text=text), {"reportType": "Annual"}, 2024)

    assert values["GrossProfit"] == -200_000
    assert values["ProfitBeforeTax"] == -250_000
    assert values["ProfitAfterTax"] == -260_000
