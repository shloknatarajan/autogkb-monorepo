"""CLI entry point: evaluate the LitSuggest VA triage pipeline against ground truth."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .fetcher import fetch_abstracts
from .loader import load_ground_truth
from .metrics import EvalResult, compute_metrics, map_ground_truth
from .scorer import score_articles

REPORT_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "litsuggest" / "eval_report.json"
)

_LABELS = ("relevant", "borderline", "not_relevant")


def _print_report(result: EvalResult, model: str) -> None:
    w = 62
    print(f"\n{'=' * w}")
    print(f"  LitSuggest Triage Eval — model: {model}")
    print(f"{'=' * w}")
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
    header = f"  {'':22}" + "".join(f"{l:>16}" for l in _LABELS)
    print(header)
    for true_lbl in _LABELS:
        row_counts = result.confusion.get(true_lbl, {})
        row = f"  {true_lbl:<22}" + "".join(
            f"{row_counts.get(p, 0):>16}" for p in _LABELS
        )
        print(row)
    print(f"{'=' * w}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate LitSuggest triage accuracy against PharmGKB ground truth"
    )
    parser.add_argument("--model", default="gpt-4o", help="LLM model for scoring (default: gpt-4o)")
    parser.add_argument("--limit", type=int, default=None, help="Max records (default: all 500)")
    parser.add_argument(
        "--ncbi-email",
        default=os.getenv("NCBI_EMAIL"),
        help="NCBI email address (or set NCBI_EMAIL env var)",
    )
    args = parser.parse_args()

    if not args.ncbi_email:
        raise SystemExit("Error: NCBI_EMAIL must be set (--ncbi-email or env var)")

    records = load_ground_truth()
    if args.limit:
        records = records[: args.limit]
    print(f"Loaded {len(records)} ground truth records.")

    pmids = [r.pmid for r in records]
    abstracts = fetch_abstracts(pmids, args.ncbi_email)

    articles = [
        (r.pmid, r.title, abstracts.get(r.pmid, {}).get("abstract"))
        for r in records
    ]
    scores = score_articles(articles, args.model)

    pairs: list[tuple[str, str]] = []
    for record in records:
        key = f"{record.pmid}:{args.model}"
        pred = scores.get(key, {}).get("label", "not_relevant")
        true = map_ground_truth(record.curation_state)
        pairs.append((pred, true))

    result = compute_metrics(pairs)
    _print_report(result, args.model)

    report = {
        "model": args.model,
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
