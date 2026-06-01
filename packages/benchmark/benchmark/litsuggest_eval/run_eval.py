"""CLI entry point: evaluate LitSuggest triage accuracy using scores stored in the DB."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .db_reader import load_scores_from_db
from .loader import load_ground_truth
from .metrics import EvalResult, compute_metrics, map_ground_truth

REPORT_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "litsuggest" / "eval_report.json"
)

_LABELS = ("relevant", "borderline", "not_relevant")


def _print_report(result: EvalResult, coverage: tuple[int, int]) -> None:
    scored, total = coverage
    w = 62
    print(f"\n{'=' * w}")
    print("  LitSuggest Triage Eval  (scores sourced from DB)")
    print(f"{'=' * w}")
    print(f"  Coverage           : {scored}/{total} ground truth records ({scored / total:.1%})")
    print(f"  Articles evaluated : {result.n_total}")
    print(f"  Accuracy           : {result.accuracy:.3f}")
    print(f"  Macro F1           : {result.macro_f1:.3f}")
    print()
    print(f"  {'Label':<18} {'Precision':>10} {'Recall':>10} {'F1':>10} {'Support':>10}")
    print(f"  {'-' * 58}")
    for lbl in _LABELS:
        m = result.label_metrics[lbl]
        print(f"  {lbl:<18} {m.precision:>10.3f} {m.recall:>10.3f} {m.f1:>10.3f} {m.support:>10}")
    print()
    print("  Confusion matrix (rows = true label, cols = predicted label):")
    header = f"  {'':22}" + "".join(f"{lbl:>16}" for lbl in _LABELS)
    print(header)
    for true_lbl in _LABELS:
        row_counts = result.confusion.get(true_lbl, {})
        row = f"  {true_lbl:<22}" + "".join(f"{row_counts.get(p, 0):>16}" for p in _LABELS)
        print(row)
    print(f"{'=' * w}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate LitSuggest triage accuracy against PharmGKB ground truth"
        " using scores already stored in the triage_sessions database table."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL DSN (default: DATABASE_URL env var)",
    )
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("Error: DATABASE_URL must be set (--database-url or env var)")

    gt_records = load_ground_truth()
    gt_by_pmid = {r.pmid: r for r in gt_records}
    print(f"Loaded {len(gt_records)} ground truth records.")

    db_scores = load_scores_from_db(args.database_url)
    print(f"Found {len(db_scores)} scored PMIDs in the database.")

    common_pmids = sorted(set(gt_by_pmid) & set(db_scores))
    print(f"Overlap: {len(common_pmids)} PMIDs present in both datasets.")

    pairs: list[tuple[str, str]] = []
    for pmid in common_pmids:
        pred = db_scores[pmid].get("triage_label", "not_relevant")
        true = map_ground_truth(gt_by_pmid[pmid].curation_state)
        pairs.append((pred, true))

    result = compute_metrics(pairs)
    _print_report(result, coverage=(len(common_pmids), len(gt_records)))

    report = {
        "source": "database",
        "coverage": {"scored": len(common_pmids), "total": len(gt_records)},
        "n_total": result.n_total,
        "accuracy": result.accuracy,
        "macro_f1": result.macro_f1,
        "label_metrics": {
            lbl: {
                "precision": m.precision,
                "recall": m.recall,
                "f1": m.f1,
                "support": m.support,
            }
            for lbl, m in result.label_metrics.items()
        },
        "confusion": result.confusion,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report saved to {REPORT_PATH}")


if __name__ == "__main__":
    main()
