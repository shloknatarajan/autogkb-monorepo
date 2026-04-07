"""
Consolidated evaluation for variant extraction experiments.

Provides evaluation functions that score extracted variants against the benchmark.
Can evaluate from a saved variants.json file or directly from a VariantExtractor.
"""

import json
from datetime import datetime
from pathlib import Path

from benchmark.v2.variant_bench import load_variant_bench_data, score_variants
from pipeline.modules.variant_finding.variant_extractor import VariantExtractor

RESULTS_DIR = Path(__file__).parent / "results"


def _score_and_summarize(
    extractor_name: str,
    variants_by_pmcid: dict[str, list[str]],
    run_name: str | None = None,
    save_results: bool = True,
) -> dict:
    """Score extracted variants against the benchmark and return standardized results.

    Args:
        extractor_name: Name of the extractor that produced the variants
        variants_by_pmcid: Dict mapping PMCID to list of extracted variant strings
        run_name: Run name used for the results file path. If None, generates one.
        save_results: Whether to save results to a JSON file

    Returns:
        Standardized results dict
    """
    benchmark_data = load_variant_bench_data()

    print(f"\nEvaluating {extractor_name} on {len(variants_by_pmcid)} articles\n")

    total_recall = 0.0
    total_precision = 0.0
    per_article_results = []
    processed = 0

    for pmcid, extracted in variants_by_pmcid.items():
        if pmcid not in benchmark_data:
            print(f"  ? {pmcid}: Not in benchmark, skipping")
            continue

        true_variants = benchmark_data[pmcid]
        result = score_variants(extracted, true_variants, pmcid)

        if len(extracted) > 0:
            precision = len(result.matches) / len(extracted)
        else:
            precision = 1.0 if len(true_variants) == 0 else 0.0

        total_recall += result.match_rate
        total_precision += precision
        processed += 1

        per_article_results.append(
            {
                "pmcid": pmcid,
                "recall": result.match_rate,
                "precision": precision,
                "true_count": len(true_variants),
                "extracted_count": len(extracted),
                "matches": result.matches,
                "misses": result.misses,
                "extras": result.extras,
            }
        )

        status = (
            "+" if result.match_rate == 1.0 else "o" if result.match_rate > 0 else "x"
        )
        print(
            f"  {status} {pmcid}: recall={result.match_rate:.0%} precision={precision:.0%} "
            f"(found {len(result.matches)}/{len(true_variants)}, extras={len(result.extras)})"
        )
        if result.misses:
            print(f"      Missed: {result.misses}")

    avg_recall = total_recall / processed if processed > 0 else 0
    avg_precision = total_precision / processed if processed > 0 else 0
    perfect_recall_count = sum(1 for r in per_article_results if r["recall"] == 1.0)

    results = {
        "extractor": extractor_name,
        "run_name": run_name,
        "timestamp": datetime.now().isoformat(),
        "articles_processed": processed,
        "avg_recall": avg_recall,
        "avg_precision": avg_precision,
        "perfect_recall_count": perfect_recall_count,
        "per_article_results": per_article_results,
    }

    print(f"\n{'=' * 60}")
    print(f"SUMMARY: {extractor_name}")
    print(f"{'=' * 60}")
    print(f"Articles processed: {processed}")
    print(f"Average Recall: {avg_recall:.1%}")
    print(f"Average Precision: {avg_precision:.1%}")
    if processed > 0:
        print(
            f"Perfect recall: {perfect_recall_count}/{processed} "
            f"({perfect_recall_count / processed:.0%})"
        )

    if save_results:
        if run_name is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_name = f"{extractor_name}_{timestamp}"
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = RESULTS_DIR / f"{run_name}.json"
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {output_path}")

    return results


def evaluate_from_file(
    variants_path: str | Path,
    save_results: bool = True,
) -> dict:
    """Evaluate variants from a saved variants.json file.

    Args:
        variants_path: Path to a variants.json file (from outputs/<run_name>/variants.json)
        save_results: Whether to save results to results/<run_name>.json

    Returns:
        Standardized results dict
    """
    variants_path = Path(variants_path)
    with open(variants_path) as f:
        data = json.load(f)

    extractor_name = data["extractor"]
    run_name = data.get("run_name")
    variants_by_pmcid = data["variants"]

    print(f"Loaded {len(variants_by_pmcid)} articles from {variants_path}")

    return _score_and_summarize(
        extractor_name=extractor_name,
        variants_by_pmcid=variants_by_pmcid,
        run_name=run_name,
        save_results=save_results,
    )


def evaluate_extractor(
    extractor: VariantExtractor,
    pmcids: list[str] | None = None,
    max_articles: int | None = None,
    save_results: bool = True,
) -> dict:
    """Run an extractor against the benchmark and return standardized results.

    This extracts variants inline and evaluates them. For the two-step workflow
    (extract then evaluate separately), use run.py to extract and evaluate_from_file
    to evaluate.

    Args:
        extractor: A VariantExtractor instance
        pmcids: Optional list of PMCIDs to evaluate. If None, uses all benchmark articles.
        max_articles: Optional cap on the number of articles to process
        save_results: Whether to save results to a JSON file

    Returns:
        Standardized results dict
    """
    benchmark_data = load_variant_bench_data()

    if pmcids is None:
        pmcids = list(benchmark_data.keys())
    if max_articles:
        pmcids = pmcids[:max_articles]

    print(f"\nRunning {extractor.name} on {len(pmcids)} articles\n")

    variants_by_pmcid = {}
    for pmcid in pmcids:
        try:
            extracted = extractor.get_variants(pmcid)
            variants_by_pmcid[pmcid] = extracted
        except Exception as e:
            print(f"  ! {pmcid}: Error - {e}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"{extractor.name}_{timestamp}"

    return _score_and_summarize(
        extractor_name=extractor.name,
        variants_by_pmcid=variants_by_pmcid,
        run_name=run_name,
        save_results=save_results,
    )
