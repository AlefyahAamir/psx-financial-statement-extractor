
from types import SimpleNamespace

from workers.extraction.pipeline import extract_values_from_content

def test_unknown_continuation_page_rows_are_available_to_balance_sheet_extraction():
    text = """
--- page 1 ---
Statement of Financial Position
Total current assets 10,000,000
--- page 2 ---
Total current liabilities 4,000,000
Cash and bank balances 2,000,000
"""
    values, _, _ = extract_values_from_content(SimpleNamespace(text=text), {"reportType": "Annual"}, 2024)

    assert values["CurrentAssets"] == 10_000_000
    assert values["CurrentLiabilities"] == 4_000_000
    assert values["CashAndBankBalances"] == 2_000_000
    assert values["WorkingCapital"] == 6_000_000
