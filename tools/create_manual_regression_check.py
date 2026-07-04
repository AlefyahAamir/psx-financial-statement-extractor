from __future__ import annotations
import argparse
import csv
from pathlib import Path

WATCH = [
    ("DGKC", "2022", "Half Year"),
    ("INDU", "2023", "Half Year"),
    ("PSO", "2025", "Annual"),
    ("PSO", "2025", "Half Year"),
]

FIELDS = ["Sales", "CostOfSales", "GrossProfit", "CurrentAssets", "CurrentLiabilities", "WorkingCapital"]

def read_csv(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def val(row, col):
    return str(row.get(col, "") or "").strip()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="App_Data/benchmark/manual_10/manual_10_labels_TO_FILL.csv")
    ap.add_argument("--score-summary", default="App_Data/benchmark/manual_10/manual_score/benchmark_score_summary.txt")
    ap.add_argument("--output", default="App_Data/benchmark/manual_10/manual_regression_check.txt")
    args = ap.parse_args()

    labels = read_csv(Path(args.labels))
    out = []
    out.append("Manual Regression Check")
    out.append("===================================")
    out.append("")
    out.append("Purpose")
    out.append("-------")
    out.append("This file records the manually reviewed regression cases after the routing/arithmetic-sanity changes.")
    out.append("It is a paper trail that known fixed interim cases were re-checked instead of only asserted in fix notes.")
    out.append("")
    out.append("Tracked cases")
    out.append("-------------")

    for sym, year, rep in WATCH:
        matches = [r for r in labels if val(r, "Symbol").upper() == sym and val(r, "Year") == year and val(r, "RequestedReport").lower() == rep.lower()]
        out.append(f"{sym} {year} {rep}")
        if not matches:
            out.append("  Status: not present in manual label file")
            out.append("")
            continue
        r = matches[0]
        any_expected = False
        for f in FIELDS:
            ext = val(r, f"Extracted_{f}")
            exp = val(r, f"Expected_{f}")
            if exp:
                any_expected = True
            out.append(f"  {f}: Extracted={ext or 'blank'} | Expected={exp or 'NOT FILLED'}")
        out.append("  Manual status: " + ("Expected values filled for at least one tracked field." if any_expected else "Expected values not filled yet. Open PDF and fill Expected_* columns."))
        out.append("")

    summary_path = Path(args.score_summary)
    out.append("Manual score summary")
    out.append("--------------------")
    if summary_path.exists():
        out.extend(summary_path.read_text(encoding="utf-8", errors="ignore").splitlines()[:80])
    else:
        out.append("Manual score summary not found yet. Run 10_Score_Manual_10_After_Labeling.bat after filling Expected_* columns.")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(out), encoding="utf-8")
    print(f"Created: {output}")

if __name__ == "__main__":
    main()
