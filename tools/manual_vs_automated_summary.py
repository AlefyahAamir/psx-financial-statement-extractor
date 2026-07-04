from __future__ import annotations
import argparse
import re
from pathlib import Path

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""

def find_line(text: str, patterns: list[str]) -> str:
    for pat in patterns:
        m = re.search(pat, text, flags=re.I | re.M)
        if m:
            return m.group(0).strip()
    return "Not available"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual-score-dir", required=True)
    ap.add_argument("--automated-summary", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    manual_dir = Path(args.manual_score_dir)
    manual_summary = read(manual_dir / "benchmark_score_summary.txt")
    auto_summary = read(Path(args.automated_summary))

    out = []
    out.append("Manual-vs-Automated Validation Report")
    out.append("=====================================")
    out.append("")
    out.append("Purpose")
    out.append("-------")
    out.append("This report separates two different checks:")
    out.append("1. Automated PDF-text verification: confirms whether the extracted number appears somewhere in the source PDF.")
    out.append("2. Manual ground-truth verification: confirms whether the extracted number belongs to the correct line item and reporting period.")
    out.append("")
    out.append("Manual hand-labelled score")
    out.append("--------------------------")
    if manual_summary:
        # Preserve a compact summary.
        lines = [ln for ln in manual_summary.splitlines() if ln.strip()]
        out.extend(lines[:80])
    else:
        out.append("Manual score summary not found. Fill Expected_* columns and rerun 10_Score_Manual_10_After_Labeling.bat.")
    out.append("")
    out.append("Automated Client-50 PDF-text score")
    out.append("----------------------------------")
    if auto_summary:
        for label in [
            "Overall automated PDF-backed accuracy",
            "High-confidence accuracy",
            "Direct accuracy",
            "Calculated accuracy",
            "Review",
            "Arithmetic sanity failure reports",
            "Arithmetic sanity failure types",
            "Arithmetic sanity advisory reports",
            "Arithmetic sanity advisory types",
        ]:
            line = find_line(auto_summary, [rf".*{re.escape(label)}.*"])
            if line != "Not available":
                out.append(line)
    else:
        out.append("Automated Client-50 summary not found. Run RUN_ME_CLIENT_50_FULL_TEST.bat first.")
    out.append("")
    out.append("Interpretation")
    out.append("--------------")
    out.append("Automated verification is useful for coverage and quick regression testing, but it can overstate accuracy when a PDF contains several similar period columns or revenue subtotal lines. Manual validation is the stricter accuracy measure because it checks the correct financial line item and period.")
    out.append("")
    out.append("Client-safe wording")
    out.append("-------------------")
    out.append("The automated audit confirms extracted values against PDF text evidence. A separate manually labelled subset is used as a stronger validation layer to confirm correct field mapping and period selection.")
    out.append("")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"Created: {out_path}")

if __name__ == "__main__":
    main()
