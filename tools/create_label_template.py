from __future__ import annotations

r"""
Create a manual-labelling template from App_Data/jobs/other_company_stats_latest.csv.

Run from the project folder:
    python .\tools\create_label_template.py

It creates:
    App_Data\benchmark\benchmark_labels_template.csv

Fill the Expected_* columns by reading each PDF. Leave blanks for fields you did not check.
Use NA / N/A / not applicable for fields that genuinely do not apply.
"""

import argparse
import csv
from pathlib import Path
from typing import List

VALUE_FIELDS = [
    "PeriodEndDate", "PaidUpCapital", "Reserves", "UnappropriatedProfit", "ShareholdersEquity",
    "CurrentAssets", "CashAndBankBalances", "AdvancesAndReceivables", "FixedAssets", "LongTermLiabilities", "OtherLongTermLiabilities",
    "OtherLiabilities", "WorkingCapital", "Sales", "CostOfSales", "GrossProfit", "OperatingExpenses",
    "FinanceCosts", "OtherIncome", "OtherCharges", "ProfitBeforeTax", "Taxation", "ProfitAfterTax",
    "RevaluationSurplus", "CurrentRatio", "DebtRatio", "BreakupValue", "SubordinatedLoans", "LongTermBorrowings",
    "CurrentLiabilities", "CurrentPortionLongTermLiabilities", "ShortTermBorrowings", "TotalBorrowings", "TradeDebts", "StockInTrade",
    "StoresAndSpares", "ShortTermInvestments", "LongTermInvestments", "OtherFixedAssets", "LeaseFinance", "TradeAndOtherPayables",
    "CashFlowFromOperatingActivities", "CashFlowFromInvestingActivities", "CashFlowFromFinancingActivities", "DeferredLiabilities", "FinanceLeaseObligations",
    "OperatingLeaseObligations", "CurrentLeaseFinance", "DepreciationProvision", "OperatingProfit", "AmountMultiplier",
]

META_FIELDS = [
    "CaseNo", "Symbol", "Year", "RequestedReport", "ActualReportType", "PeriodEnded",
    "FiscalYearEndMonth", "PeriodBasedReportCheck", "Published", "Status", "FilledFieldCount",
    "PdfUrl", "CachedPdfPath", "Title", "Warnings", "EvidenceForCrossCheck",
]


def find_project_root(start: Path) -> Path:
    start = start.resolve()
    candidates = [start, Path.cwd().resolve(), *start.parents, *Path.cwd().resolve().parents]
    for base in candidates:
        if (base / "workers" / "psx_worker.py").exists():
            return base
        child = base / "PsxFinancialExtractor.Web"
        if (child / "workers" / "psx_worker.py").exists():
            return child
    raise FileNotFoundError("Could not locate PsxFinancialExtractor.Web project folder.")


def parse_args():
    p = argparse.ArgumentParser(description="Create benchmark label template from latest extractor CSV.")
    p.add_argument("--input", default="", help="Path to extracted CSV. Default: App_Data/jobs/other_company_stats_latest.csv")
    p.add_argument("--output", default="", help="Output label template. Default: App_Data/benchmark/benchmark_labels_template.csv")
    p.add_argument("--include-empty-extracted", action="store_true", help="Include Expected_* columns for fields that were empty in extraction. Default includes all fields anyway; kept for compatibility.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = find_project_root(Path(__file__).parent)
    input_path = Path(args.input) if args.input else root / "App_Data" / "jobs" / "other_company_stats_latest.csv"
    output_path = Path(args.output) if args.output else root / "App_Data" / "benchmark" / "benchmark_labels_template.csv"
    if not input_path.exists():
        raise FileNotFoundError(f"Extractor CSV not found: {input_path}\nRun PSX_Terminal_Stats_Clean.py or RUN_BROAD_ACCURACY_TEST.bat first.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    out_fields: List[str] = []
    for col in META_FIELDS:
        if col in (rows[0].keys() if rows else []):
            out_fields.append(col)
    out_fields += ["LabelStatus", "LabelNotes"]
    for field in VALUE_FIELDS:
        out_fields.append(f"Extracted_{field}")
        out_fields.append(f"Expected_{field}")
        out_fields.append(f"SourcePage_{field}")

    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = {col: row.get(col, "") for col in out_fields}
            out["LabelStatus"] = "PENDING"
            out["LabelNotes"] = ""
            for field in VALUE_FIELDS:
                out[f"Extracted_{field}"] = row.get(field, "")
                out[f"Expected_{field}"] = ""
                out[f"SourcePage_{field}"] = ""
            writer.writerow(out)

    print("Created label template:", output_path)
    print("Rows:", len(rows))
    print("Next: open this CSV, fill Expected_* values from the PDFs, then run PSX_Benchmark_Score.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
