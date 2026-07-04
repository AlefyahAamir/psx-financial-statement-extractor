
from workers.extraction.taxation import (
    is_taxation_candidate,
    select_profit_before_tax_row,
    select_taxation_row,
)

def test_taxation_rejects_profit_before_taxation_subtotal():
    row = {"norm": "profit before levies and taxation", "amounts": [618835647]}
    assert not is_taxation_candidate(row)

def test_taxation_accepts_real_taxation_row():
    row = {"norm": "taxation", "amounts": [2089749712]}
    assert is_taxation_candidate(row)

def test_pbt_prefers_real_row_above_taxation_not_ias12_subtotal():
    rows = [
        {"norm": "profit before levies and taxation", "amounts": [618835647], "source": "text"},
        {"norm": "minimum tax", "amounts": [-490957242], "source": "text"},
        {"norm": "final taxes", "amounts": [-217739406], "source": "text"},
        {"norm": "loss profit before taxation", "amounts": [-89861001], "source": "text"},
        {"norm": "taxation", "amounts": [2089749712], "source": "text"},
        {"norm": "profit for the year", "amounts": [1999888711], "source": "text"},
    ]
    tax_row = select_taxation_row(rows)
    pbt_row = select_profit_before_tax_row(rows, tax_row)

    assert tax_row["norm"] == "taxation"
    assert pbt_row["norm"] == "loss profit before taxation"
