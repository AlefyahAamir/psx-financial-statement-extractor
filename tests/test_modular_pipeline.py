
from types import SimpleNamespace

from workers.extraction.pipeline import extract_values_from_content

def test_pipeline_extracts_taxation_and_profit_before_tax_from_ias12_rows():
    text = """
--- page 10 ---
Statement of Profit or Loss
Revenue 1,000,000
Cost of sales (400,000)
Gross profit 600,000
Profit before levies and taxation 618,835,647
Minimum tax (490,957,242)
Final taxes (217,739,406)
Loss / profit before taxation (89,861,001)
Taxation 2,089,749,712
Profit for the year 1,999,888,711
"""
    content = SimpleNamespace(text=text)
    values, evidence, warnings = extract_values_from_content(content, {"reportType": "Annual"}, 2024)

    assert values["ProfitBeforeTax"] == -89861001
    assert values["Taxation"] == 2089749712
    assert values["ProfitAfterTax"] == 1999888711
    assert "profit before levies" not in evidence["Taxation"].lower()

def test_pipeline_calculates_working_capital():
    text = """
--- page 5 ---
Statement of Financial Position
Total current assets 5,000,000
Total current liabilities 3,000,000
"""
    content = SimpleNamespace(text=text)
    values, _, _ = extract_values_from_content(content, {"reportType": "Annual"}, 2024)

    assert values["CurrentAssets"] == 5000000
    assert values["CurrentLiabilities"] == 3000000
    assert values["WorkingCapital"] == 2000000
