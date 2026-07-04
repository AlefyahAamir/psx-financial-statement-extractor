
from workers.extraction.fields import CALCULATED_FIELDS, PRIMARY_EXTRACTION_FIELDS
from workers.extraction.pipeline import CASH_FLOW_MAP, FINANCIAL_POSITION_MAP, PROFIT_LOSS_MAP

def test_primary_pipeline_has_mapping_or_calculation_for_every_schema_field():
    mapped = set(FINANCIAL_POSITION_MAP) | set(PROFIT_LOSS_MAP) | set(CASH_FLOW_MAP)
    special = {"ProfitBeforeTax", "Taxation"}  # selected by dedicated taxation module
    missing = sorted(set(PRIMARY_EXTRACTION_FIELDS) - mapped - set(CALCULATED_FIELDS) - special)
    assert missing == []
