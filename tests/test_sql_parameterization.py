
import importlib

def test_review_sql_template_does_not_inline_string_values():
    worker = importlib.import_module("workers.psx_worker")
    row = {
        "Symbol": "ABC'; DROP TABLE dbo.BalnShet; --",
        "FinancialYear": 2025,
        "PeriodEndDate": "2025-06-30",
        "ReportType": "Annual",
        "PdfUrl": "https://example.test/report.pdf?name=O'Reilly",
        "ExtractionStatus": "OK",
    }

    sql = worker.build_insert_sql(row)

    assert "DROP TABLE" not in sql.split("-- Parameter values:")[0]
    assert "O'Reilly" not in sql.split("-- Parameter values:")[0]
    assert "?" in sql
    assert "updateParameters" in sql
    assert "insertParameters" in sql
