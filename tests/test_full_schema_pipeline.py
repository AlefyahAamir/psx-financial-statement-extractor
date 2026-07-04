
from types import SimpleNamespace

from workers.extraction.pipeline import extract_values_from_content

def test_pipeline_extracts_extended_balance_sheet_fields():
    text = """
--- page 5 ---
Statement of Financial Position
Issued subscribed and paid up capital 1,000,000
Reserves 2,000,000
Unappropriated profit 3,000,000
Total equity 6,000,000
Total current assets 10,000,000
Cash and bank balances 1,100,000
Advances deposits prepayments and other receivables 1,200,000
Property plant and equipment 9,000,000
Other non current liabilities 700,000
Other liabilities 800,000
Surplus on revaluation 900,000
Subordinated loans 500,000
Long term borrowings 4,000,000
Current portion of long term borrowings 600,000
Short term borrowings 300,000
Stores spares and loose tools 200,000
Short term investments 400,000
Long term investments 5,000,000
Right of use assets 250,000
Lease liabilities 260,000
Current portion of lease liabilities 70,000
Deferred tax liability 75,000
Trade and other payables 850,000
Total current liabilities 7,000,000
--- page 7 ---
Statement of Cash Flows
Finance lease obtained 50,000
Operating lease obtained 60,000
"""
    values, evidence, warnings = extract_values_from_content(SimpleNamespace(text=text), {"reportType": "Annual"}, 2024)

    assert values["AdvancesAndReceivables"] == 1_200_000
    assert values["OtherLongTermLiabilities"] == 700_000
    assert values["OtherLiabilities"] == 800_000
    assert values["RevaluationSurplus"] == 900_000
    assert values["SubordinatedLoans"] == 500_000
    assert values["CurrentPortionLongTermLiabilities"] == 600_000
    assert values["StoresAndSpares"] == 200_000
    assert values["ShortTermInvestments"] == 400_000
    assert values["LongTermInvestments"] == 5_000_000
    assert values["OtherFixedAssets"] == 250_000
    assert values["LeaseFinance"] == 260_000
    assert values["DeferredLiabilities"] == 75_000
    assert values["FinanceLeaseObligations"] == 50_000
    assert values["OperatingLeaseObligations"] == 60_000
    assert values["CurrentLeaseFinance"] == 70_000
    assert values["WorkingCapital"] == 3_000_000
    assert not any("Pipeline mapping coverage warning" in warning for warning in warnings)
