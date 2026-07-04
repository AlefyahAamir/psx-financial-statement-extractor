
import importlib
from types import SimpleNamespace

class MockPage:
    rect = SimpleNamespace(width=240.0, height=300.0)

    def get_text(self, kind):
        if kind != "words":
            return ""
        return [
            (155, 20, 175, 30, "2025"),
            (205, 20, 225, 30, "2024"),
            (20, 100, 80, 110, "Revenue"),
            (155, 100, 175, 110, "1,000"),
            (205, 100, 225, 110, "900"),
            (20, 125, 70, 135, "Taxation"),
            (155, 125, 175, 135, "(100)"),
            (205, 125, 225, 135, "(90)"),
        ]

def test_layout_rows_choose_target_year_column_not_comparative_column():
    worker = importlib.import_module("workers.psx_worker")
    rows = worker.layout_rows_from_text_page(MockPage(), 2025)

    by_label = {row["norm"]: row for row in rows}
    assert by_label["revenue"]["value"] == 1000
    assert by_label["taxation"]["value"] == -100
