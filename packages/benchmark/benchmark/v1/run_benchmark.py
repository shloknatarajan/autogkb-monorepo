"""
Run benchmarks for the given annotations and save the results to a json file.
Takes in (proposed_annotation: json, ground_truth_annotation: json) --> score json

Usage Examples:
--------------

1. Run benchmarks on all files and save results:
   pixi run python -m src.benchmarks.run_benchmark

2. Run benchmark on a single file (e.g., PMC384715):
   pixi run python -m src.benchmarks.run_benchmark --single_file PMC384715

3. Generate detailed analysis JSON files for all results:
   pixi run python -m src.benchmarks.run_benchmark --save_analysis

4. Run single file with detailed analysis:
   pixi run python -m src.benchmarks.run_benchmark --single_file PMC384715 --save_analysis

Analysis JSON Output Format:
---------------------------
The analysis JSONs in data/analysis/ have the following structure:
- Overall scores at the top
- For each benchmark:
  - ground_truth_annotations: List of all GT items with match status
    - matched items include: overall_match_score, field_scores, and matched_prediction
    - unmatched items include: just the annotation
  - extra_predictions: Predictions not found in ground truth
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from tqdm import tqdm
from .drug_benchmark import evaluate_drug_annotations
from .fa_benchmark import evaluate_fa_from_articles
from .pheno_benchmark import evaluate_phenotype_annotations
from .study_parameters_benchmark import evaluate_study_parameters


def load_annotation_file(file_path: Path) -> Dict[str, Any]:
    """Load annotation JSON file."""
    with open(file_path, "r") as f:
        return json.load(f)


def run_single_benchmark(
    ground_truth_file: Path, proposed_file: Path, verbose: bool = False
) -> Dict[str, Any]:
    """
    Run all benchmarks for a single annotation pair.

    Args:
        ground_truth_file: Path to ground truth annotation JSON
        proposed_file: Path to proposed annotation JSON
        verbose: If True, print detailed results

    Returns:
        Dict with benchmark results for all annotation types
    """
    # Load annotations
    gt_data = load_annotation_file(ground_truth_file)
    prop_data = load_annotation_file(proposed_file)

    results = {
        "pmid": gt_data.get("pmid"),
        "pmcid": gt_data.get("pmcid"),
        "title": gt_data.get("title"),
        "benchmarks": {},
    }

    # Run Drug Annotations Benchmark
    if "var_drug_ann" in gt_data and "var_drug_ann" in prop_data:
        gt_drug = gt_data["var_drug_ann"]
        prop_drug = prop_data["var_drug_ann"]
        if gt_drug or prop_drug:
            try:
                drug_results = evaluate_drug_annotations([gt_drug, prop_drug])
                results["benchmarks"]["drug_annotations"] = drug_results
                if verbose:
                    print(
                        f"  Drug Annotations Score: {drug_results['overall_score']:.3f}"
                    )
            except Exception as e:
                if verbose:
                    print(f"  Drug Annotations Error: {e}")
                results["benchmarks"]["drug_annotations"] = {"error": str(e)}

    # Run Phenotype Annotations Benchmark
    if "var_pheno_ann" in gt_data and "var_pheno_ann" in prop_data:
        gt_pheno = gt_data["var_pheno_ann"]
        prop_pheno = prop_data["var_pheno_ann"]
        if gt_pheno or prop_pheno:
            try:
                pheno_results = evaluate_phenotype_annotations([gt_pheno, prop_pheno])
                results["benchmarks"]["phenotype_annotations"] = pheno_results
                if verbose:
                    print(
                        f"  Phenotype Annotations Score: {pheno_results['overall_score']:.3f}"
                    )
            except Exception as e:
                if verbose:
                    print(f"  Phenotype Annotations Error: {e}")
                results["benchmarks"]["phenotype_annotations"] = {"error": str(e)}

    # Run Functional Analysis Benchmark
    if "var_fa_ann" in gt_data and "var_fa_ann" in prop_data:
        gt_fa = gt_data["var_fa_ann"]
        prop_fa = prop_data["var_fa_ann"]
        if gt_fa or prop_fa:
            try:
                # Use evaluate_fa_from_articles for proper alignment
                fa_results = evaluate_fa_from_articles(
                    {"var_fa_ann": gt_fa}, {"var_fa_ann": prop_fa}
                )
                results["benchmarks"]["functional_analysis"] = fa_results
                if verbose:
                    print(
                        f"  Functional Analysis Score: {fa_results['overall_score']:.3f}"
                    )
            except Exception as e:
                if verbose:
                    print(f"  Functional Analysis Error: {e}")
                results["benchmarks"]["functional_analysis"] = {"error": str(e)}

    # Run Study Parameters Benchmark
    if "study_parameters" in gt_data and "study_parameters" in prop_data:
        gt_params = gt_data["study_parameters"]
        prop_params = prop_data["study_parameters"]
        if gt_params or prop_params:
            try:
                params_results = evaluate_study_parameters([gt_params, prop_params])
                results["benchmarks"]["study_parameters"] = params_results
                if verbose:
                    print(
                        f"  Study Parameters Score: {params_results['overall_score']:.3f}"
                    )
            except Exception as e:
                if verbose:
                    print(f"  Study Parameters Error: {e}")
                results["benchmarks"]["study_parameters"] = {"error": str(e)}

    # Calculate overall score as weighted average across all benchmarks
    benchmark_scores = []
    benchmark_weights = {
        "drug_annotations": 1.5,
        "phenotype_annotations": 1.5,
        "functional_analysis": 1.0,
        "study_parameters": 1.0,
    }

    total_weight = 0.0
    weighted_sum = 0.0

    for benchmark_name, benchmark_result in results["benchmarks"].items():
        if "overall_score" in benchmark_result:
            weight = benchmark_weights.get(benchmark_name, 1.0)
            weighted_sum += benchmark_result["overall_score"] * weight
            total_weight += weight
            benchmark_scores.append(benchmark_result["overall_score"])

    results["overall_score"] = weighted_sum / total_weight if total_weight > 0 else 0.0
    results["num_benchmarks"] = len(benchmark_scores)

    return results


def run_all_benchmarks(
    ground_truth_dir: Path,
    proposed_dir: Path,
    output_file: Optional[Path] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Run benchmarks for all annotation pairs in the directories.

    Args:
        ground_truth_dir: Directory containing ground truth annotations
        proposed_dir: Directory containing proposed annotations
        output_file: Optional path to save results JSON
        verbose: If True, print progress and results

    Returns:
        Dict with aggregate results across all files
    """
    gt_files = sorted(Path(ground_truth_dir).glob("*.json"))

    if verbose:
        print(f"Found {len(gt_files)} ground truth annotation files")

    all_results = []
    missing_proposed = []

    # Use tqdm for progress bar
    file_iterator = tqdm(gt_files, desc="Running benchmarks", disable=not verbose)

    for gt_file in file_iterator:
        pmcid = gt_file.stem
        prop_file = Path(proposed_dir) / f"{pmcid}.json"

        if not prop_file.exists():
            missing_proposed.append(pmcid)
            continue

        file_iterator.set_postfix(file=pmcid)
        result = run_single_benchmark(gt_file, prop_file, verbose=False)
        all_results.append(result)

    # Calculate aggregate statistics
    all_scores = [r["overall_score"] for r in all_results]

    aggregate_results = {
        "total_files": len(all_results),
        "missing_proposed_files": len(missing_proposed),
        "missing_proposed_pmcids": missing_proposed,
        "overall_mean_score": sum(all_scores) / len(all_scores) if all_scores else 0.0,
        "overall_min_score": min(all_scores) if all_scores else 0.0,
        "overall_max_score": max(all_scores) if all_scores else 0.0,
        "individual_results": all_results,
    }

    # Calculate per-benchmark statistics
    benchmark_names = [
        "drug_annotations",
        "phenotype_annotations",
        "functional_analysis",
        "study_parameters",
    ]
    aggregate_results["per_benchmark_stats"] = {}

    for benchmark_name in benchmark_names:
        scores = []
        for result in all_results:
            if (
                benchmark_name in result["benchmarks"]
                and "overall_score" in result["benchmarks"][benchmark_name]
            ):
                scores.append(result["benchmarks"][benchmark_name]["overall_score"])

        if scores:
            aggregate_results["per_benchmark_stats"][benchmark_name] = {
                "mean_score": sum(scores) / len(scores),
                "min_score": min(scores),
                "max_score": max(scores),
                "num_files": len(scores),
            }

    # Save to file if requested
    if output_file:
        with open(output_file, "w") as f:
            json.dump(aggregate_results, f, indent=2)
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"Results saved to: {output_file}")

    # Always save a timestamped analysis summary
    save_analysis_summary(aggregate_results, verbose=verbose)

    # Print summary
    if verbose:
        print(f"\n{'=' * 60}")
        print("FINAL RESULTS")
        print(f"{'=' * 60}")
        print(f"Total files processed: {aggregate_results['total_files']}")
        print(f"Missing proposed files: {aggregate_results['missing_proposed_files']}")
        print(f"\nOverall Mean Score: {aggregate_results['overall_mean_score']:.3f}")
        print(f"Overall Min Score: {aggregate_results['overall_min_score']:.3f}")
        print(f"Overall Max Score: {aggregate_results['overall_max_score']:.3f}")
        print("\nPer-Benchmark Statistics:")
        for benchmark_name, stats in aggregate_results["per_benchmark_stats"].items():
            print(f"  {benchmark_name}:")
            print(f"    Mean: {stats['mean_score']:.3f}")
            print(f"    Min: {stats['min_score']:.3f}")
            print(f"    Max: {stats['max_score']:.3f}")
            print(f"    Files: {stats['num_files']}")

    return aggregate_results


def save_analysis(
    result: Dict[str, Any], output_dir: Optional[Path] = None
) -> Optional[Path]:
    """
    Save detailed analysis of benchmark results to a JSON file.

    Args:
        result: Result dict from run_single_benchmark or a single entry from run_all_benchmarks
        output_dir: Directory to save analysis JSON files (default: data/analysis)

    Returns:
        Path to the saved JSON file, or None if PMCID is not available
    """
    pmcid = result.get("pmcid", "Unknown")
    if pmcid == "Unknown":
        return None

    if output_dir is None:
        output_dir = Path("data/analysis")

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build analysis structure
    analysis = {
        "pmcid": pmcid,
        "pmid": result.get("pmid"),
        "title": result.get("title"),
        "overall_score": result.get("overall_score", 0.0),
        "num_benchmarks": result.get("num_benchmarks", 0),
        "benchmarks": {},
    }

    for benchmark_name, benchmark_result in result.get("benchmarks", {}).items():
        benchmark_analysis = {
            "score": benchmark_result.get("overall_score", 0.0),
            "total_samples": benchmark_result.get("total_samples", 0),
        }

        if "error" in benchmark_result:
            benchmark_analysis["error"] = benchmark_result["error"]
            analysis["benchmarks"][benchmark_name] = benchmark_analysis
            continue

        # NEW FORMAT: Ground truth annotations with match status
        unmatched_gt = benchmark_result.get("unmatched_ground_truth", [])
        unmatched_pred = benchmark_result.get("unmatched_predictions", [])
        detailed_results = benchmark_result.get("detailed_results", [])

        # Build ground_truth_annotations list
        ground_truth_annotations = []

        # Add matched ground truth items (from detailed_results)
        for detail in detailed_results:
            field_scores = detail.get("field_scores", {})
            field_values = detail.get("field_values", {})

            # Reconstruct the ground truth annotation from field_values
            gt_annotation = {k: v.get("ground_truth") for k, v in field_values.items()}

            # Calculate overall match score for this annotation
            scores_list = list(field_scores.values())
            overall_match_score = (
                sum(scores_list) / len(scores_list) if scores_list else 0.0
            )

            # Build detailed field comparison with match status
            field_comparison = {}
            for field, values in field_values.items():
                score = field_scores.get(field, 0.0)

                # Categorize match status based on score
                if score >= 0.95:
                    match_status = "match"
                elif score >= 0.5:
                    match_status = "partial_match"
                else:
                    match_status = "no_match"

                field_comparison[field] = {
                    "ground_truth": values.get("ground_truth"),
                    "prediction": values.get("prediction"),
                    "score": score,
                    "match_status": match_status,
                }

            annotation_entry = {
                "matched": True,
                "overall_match_score": overall_match_score,
                "annotation": gt_annotation,
                "field_comparison": field_comparison,
            }

            ground_truth_annotations.append(annotation_entry)

        # Add unmatched ground truth items
        for item in unmatched_gt:
            annotation_entry = {"matched": False, "annotation": item}
            ground_truth_annotations.append(annotation_entry)

        benchmark_analysis["ground_truth_annotations"] = {
            "count": len(ground_truth_annotations),
            "matched_count": len(detailed_results),
            "unmatched_count": len(unmatched_gt),
            "items": ground_truth_annotations,
        }

        # Extra predictions (not in ground truth)
        benchmark_analysis["extra_predictions"] = {
            "count": len(unmatched_pred),
            "items": [
                {
                    "variant": item.get("Variant/Haplotypes", "N/A"),
                    "gene": item.get("Gene", "N/A"),
                    "drug": item.get("Drug(s)", "N/A"),
                    "annotation": item,
                }
                for item in unmatched_pred
            ],
        }

        analysis["benchmarks"][benchmark_name] = benchmark_analysis

    # Save to JSON file
    output_file = output_dir / f"{pmcid}.json"
    with open(output_file, "w") as f:
        json.dump(analysis, f, indent=2)

    return output_file


def save_all_analyses(
    aggregate_results: Dict[str, Any],
    output_dir: Optional[Path] = None,
    verbose: bool = True,
) -> List[Path]:
    """
    Save detailed analysis for all files in aggregate results to JSON files.

    Args:
        aggregate_results: Results from run_all_benchmarks
        output_dir: Directory to save analysis JSON files (default: data/analysis)
        verbose: If True, print progress messages

    Returns:
        List of paths to saved JSON files
    """
    if output_dir is None:
        output_dir = Path("data/analysis")

    individual_results = aggregate_results.get("individual_results", [])

    saved_files = []
    result_iterator = tqdm(
        individual_results, desc="Saving analyses", disable=not verbose
    )

    for result in result_iterator:
        pmcid = result.get("pmcid", "Unknown")
        result_iterator.set_postfix(file=pmcid)
        output_file = save_analysis(result, output_dir=output_dir)
        if output_file:
            saved_files.append(output_file)

    if verbose:
        print(f"\nSaved {len(saved_files)} analysis files to {output_dir}")

    return saved_files


def save_analysis_summary(
    aggregate_results: Dict[str, Any],
    output_dir: Optional[Path] = None,
    verbose: bool = True,
) -> Path:
    """
    Save a timestamped summary of overall scores from a benchmark run.

    This creates a compact JSON file containing just the key metrics without
    all the detailed per-article analysis.

    Args:
        aggregate_results: Results from run_all_benchmarks
        output_dir: Directory to save summary (default: data/analysis_summaries)
        verbose: If True, print confirmation message

    Returns:
        Path to the saved summary file
    """
    if output_dir is None:
        output_dir = Path("data/analysis_summaries")

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate timestamp for filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Build summary with just the scores
    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_files": aggregate_results.get("total_files", 0),
        "missing_proposed_files": aggregate_results.get("missing_proposed_files", 0),
        "overall_statistics": {
            "mean_score": aggregate_results.get("overall_mean_score", 0.0),
            "min_score": aggregate_results.get("overall_min_score", 0.0),
            "max_score": aggregate_results.get("overall_max_score", 0.0),
        },
        "per_benchmark_statistics": aggregate_results.get("per_benchmark_stats", {}),
        "per_file_scores": [
            {
                "pmcid": result.get("pmcid"),
                "overall_score": result.get("overall_score", 0.0),
                "benchmark_scores": {
                    name: benchmark.get("overall_score", 0.0)
                    for name, benchmark in result.get("benchmarks", {}).items()
                    if "overall_score" in benchmark
                },
            }
            for result in aggregate_results.get("individual_results", [])
        ],
    }

    # Save to timestamped file
    output_file = output_dir / f"summary_{timestamp}.json"
    with open(output_file, "w") as f:
        json.dump(summary, f, indent=2)

    if verbose:
        print(f"\nAnalysis summary saved to: {output_file}")

    return output_file


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run benchmark evaluation on annotation files"
    )
    parser.add_argument(
        "--ground_truth_dir",
        type=Path,
        default=Path("data/benchmark_annotations"),
        help="Directory containing ground truth annotations",
    )
    parser.add_argument(
        "--proposed_dir",
        type=Path,
        default=Path("data/proposed_annotations"),
        help="Directory containing proposed annotations",
    )
    parser.add_argument(
        "--output_file",
        type=Path,
        default=Path("benchmark_results.json"),
        help="Output file for results",
    )
    parser.add_argument(
        "--analysis_dir",
        type=Path,
        default=Path("data/analysis"),
        help="Directory to save analysis JSON files (default: data/analysis)",
    )
    parser.add_argument(
        "--save_analysis",
        action="store_true",
        help="Save detailed analysis to JSON files in data/analysis/",
    )
    parser.add_argument(
        "--single_file",
        type=str,
        help="Run benchmark on a single file (provide PMCID, e.g., PMC5508045)",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")

    args = parser.parse_args()

    if args.single_file:
        # Run single file benchmark
        gt_file = args.ground_truth_dir / f"{args.single_file}.json"
        prop_file = args.proposed_dir / f"{args.single_file}.json"

        if not gt_file.exists():
            print(f"Error: Ground truth file not found: {gt_file}")
            exit(1)
        if not prop_file.exists():
            print(f"Error: Proposed file not found: {prop_file}")
            exit(1)

        result = run_single_benchmark(gt_file, prop_file, verbose=not args.quiet)

        if args.save_analysis:
            output_file = save_analysis(result, output_dir=args.analysis_dir)
            if output_file:
                print(f"\nAnalysis saved to: {output_file}")

        print(f"\nFinal Score: {result['overall_score']:.3f}")
    else:
        # Run all benchmarks
        results = run_all_benchmarks(
            args.ground_truth_dir,
            args.proposed_dir,
            output_file=args.output_file,
            verbose=not args.quiet,
        )

        if args.save_analysis:
            saved_files = save_all_analyses(
                results, output_dir=args.analysis_dir, verbose=not args.quiet
            )
            if saved_files and not args.quiet:
                print(
                    f"\nSaved {len(saved_files)} analysis files to {args.analysis_dir}"
                )
