"""
Functions:
- (proposed_variants: list[str], true_variants: list[str]) -> VariantBenchResult
- (proposed_variants: list[str], pmcid: str) -> VariantBenchResult. Get's the true variants using the pmcid from data/benchmark_v2/variant_bench.jsonl
- main function that tests this using the variant_bench.jsonl file for reference and some dummy proposed variants

Currently uses exact matching, but could be extended to use fuzzy matching or other similarity metrics.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VariantBenchResult:
    pmcid: str
    title: str
    match_rate: float
    misses: list[str]
    matches: list[str]
    extras: list[str]


def load_pmcid_title(pmcid: str) -> str:
    """Load the title of an article given its PMCID.

    Args:
        pmcid: PMCID of the article

    Returns:
        Title of the article
    """
    data_path = (
        Path(__file__).parent.parent.parent
        / "data"
        / "benchmark_v2"
        / "variant_bench.jsonl"
    )
    with open(data_path) as f:
        for line in f:
            record = json.loads(line)
            if record["pmcid"] == pmcid:
                return record["article_title"]
    return ""


def load_variant_bench_data() -> dict[str, list[str]]:
    """Load the variant benchmark data from the jsonl file.

    Returns:
        dict mapping pmcid to list of true variants
    """
    data_path = (
        Path(__file__).parent.parent.parent
        / "data"
        / "benchmark_v2"
        / "variant_bench.jsonl"
    )
    pmcid_to_variants: dict[str, list[str]] = {}

    with open(data_path) as f:
        for line in f:
            record = json.loads(line)
            pmcid_to_variants[record["pmcid"]] = record["variants"]

    return pmcid_to_variants


def score_variants(
    proposed_variants: list[str],
    true_variants: list[str],
    pmcid: str = "",
    title: str = "",
) -> VariantBenchResult:
    """Score proposed variants against true variants.

    Args:
        proposed_variants: List of variant identifiers proposed by the model
        true_variants: List of true variant identifiers (ground truth)
        pmcid: Optional PMCID for the result
        title: Optional title of the article

    Returns:
        VariantBenchResult with match statistics
    """
    proposed_set = {variant.strip().lower() for variant in proposed_variants}
    true_set = {variant.strip().lower() for variant in true_variants}

    matches = list(proposed_set & true_set)
    mismatches = list(proposed_set - true_set)
    missed_variants = list(true_set - proposed_set)

    if len(true_set) > 0:
        match_rate = len(matches) / len(true_set)
    else:
        match_rate = 1.0 if len(proposed_set) == 0 else 0.0

    return VariantBenchResult(
        pmcid=pmcid,
        title=title,
        match_rate=match_rate,
        misses=missed_variants,
        matches=matches,
        extras=mismatches,
    )


def score_variants_by_pmcid(
    proposed_variants: list[str],
    pmcid: str,
) -> VariantBenchResult:
    """Score proposed variants against true variants looked up by PMCID.

    Args:
        proposed_variants: List of variant identifiers proposed by the model
        pmcid: PMCID to look up true variants from variant_bench.jsonl

    Returns:
        VariantBenchResult with match statistics

    Raises:
        KeyError: If the pmcid is not found in the benchmark data
    """
    data = load_variant_bench_data()

    if pmcid not in data:
        raise KeyError(f"PMCID {pmcid} not found in variant benchmark data")

    true_variants = data[pmcid]
    title = load_pmcid_title(pmcid)
    return score_variants(proposed_variants, true_variants, pmcid, title)


def score_annotation(proposed_annotation_path: str) -> VariantBenchResult:
    """Score a proposed annotation file against the benchmark data.

    Args:
        proposed_annotation_path: Path to the proposed annotation JSON file
            (e.g., data/proposed_annotations/PMC384715.json)

    Returns:
        VariantBenchResult with match statistics

    Raises:
        KeyError: If the pmcid from the annotation is not found in the benchmark data
    """
    from benchmark_v2.field_extractor import fields_from_file

    # Extract pmcid and variants from the proposed annotation
    extracted = fields_from_file(proposed_annotation_path, "variant")
    pmcid = extracted.pmcid
    proposed_variants = extracted.fields

    # Score against the true variants from benchmark data
    return score_variants_by_pmcid(proposed_variants, pmcid)


def score_all_annotations(
    annotations_dir: str | Path = "data/proposed_annotations",
    output_path: str | Path | None = None,
    run_name: str | None = None,
) -> dict:
    """Score all annotations in a directory against the benchmark data.

    Args:
        annotations_dir: Path to the directory containing proposed annotation JSON files.
            Defaults to "data/proposed_annotations".
        output_path: Path to save the results JSON file. If None, saves to
            "data/benchmark_results/variant_bench_results.json".
        run_name: Name for this run. Defaults to the annotations_dir path string.

    Returns:
        dict with the following structure:
        {
            "timestamp": "",
            "run_name": "",
            "total_match_rate": 0.0,
            "per_annotation_scores": [
                {
                    "pmcid": "PMC5508045",
                    "title": "",
                    "match_rate": 0.0,
                    "matches": [],
                    "misses": [],
                    "extras": []
                }
            ]
        }
    """
    from datetime import datetime

    annotations_dir = Path(annotations_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if output_path is None:
        output_path = (
            Path(__file__).parent.parent.parent
            / "data"
            / "benchmark_v2"
            / "variant_bench_results"
            / f"annotation_variants_{timestamp}.json"
        )
    else:
        output_path = Path(output_path)

    if run_name is None:
        run_name = str(annotations_dir)

    # Score each annotation file
    per_annotation_scores = []
    for annotation_file in sorted(annotations_dir.glob("*.json")):
        try:
            result = score_annotation(str(annotation_file))
            per_annotation_scores.append(
                {
                    "pmcid": result.pmcid,
                    "title": result.title,
                    "match_rate": result.match_rate,
                    "matches": result.matches,
                    "misses": result.misses,
                    "extras": result.extras,
                }
            )
        except KeyError as e:
            print(f"Warning: Skipping {annotation_file.name} - {e}")
            continue

    # Calculate total match rate as average across all annotations
    if per_annotation_scores:
        total_match_rate = sum(s["match_rate"] for s in per_annotation_scores) / len(
            per_annotation_scores
        )
    else:
        total_match_rate = 0.0

    results = {
        "timestamp": datetime.now().isoformat(),
        "run_name": run_name,
        "total_match_rate": total_match_rate,
        "per_annotation_scores": per_annotation_scores,
    }

    # Ensure output directory exists and save results
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Results saved to {output_path}")
    print(f"Total match rate: {total_match_rate:.2%}")
    print(f"Scored {len(per_annotation_scores)} annotations")

    return results


def score_generated_variants(
    generated_variants_path: str | Path,
    run_name: str | None = None,
) -> dict:
    """Score generated variants from a JSON file against the benchmark data.

    Args:
        generated_variants_path: Path to the JSON file containing generated variants.
            Expected format: {pmcid: [...], ...}
            e.g., "data/benchmark_v2/generated_variants/example.json"
        run_name: Name for this run. Defaults to the generated_variants_path filename.

    Returns:
        dict with the following structure:
        {
            "timestamp": "",
            "run_name": "",
            "source_file": "",
            "total_match_rate": 0.0,
            "per_pmcid_scores": [
                {
                    "pmcid": "PMC5508045",
                    "title": "",
                    "match_rate": 0.0,
                    "matches": [],
                    "misses": [],
                    "extras": []
                }
            ]
        }
    """
    from datetime import datetime

    generated_variants_path = Path(generated_variants_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Load generated variants from the JSON file
    with open(generated_variants_path) as f:
        generated_data = json.load(f)

    # Use run_name from file if available, otherwise use filename stem
    if run_name is None:
        run_name = generated_data.get("run_name", generated_variants_path.stem)

    output_path = (
        Path(__file__).parent.parent.parent
        / "data"
        / "benchmark_v2"
        / "variant_bench_results"
        / f"{run_name}_variants_{timestamp}.json"
    )

    # Score each PMCID entry (skip non-PMCID keys like run_name)
    per_pmcid_scores = []
    for pmcid, variants in generated_data.items():
        if not pmcid.startswith("PMC"):
            continue
        proposed_variants = variants if isinstance(variants, list) else []
        try:
            result = score_variants_by_pmcid(proposed_variants, pmcid)
            per_pmcid_scores.append(
                {
                    "pmcid": result.pmcid,
                    "title": result.title,
                    "match_rate": result.match_rate,
                    "matches": result.matches,
                    "misses": result.misses,
                    "extras": result.extras,
                }
            )
        except KeyError as e:
            print(f"Warning: Skipping {pmcid} - {e}")
            continue

    # Calculate total match rate as average across all PMCIDs
    if per_pmcid_scores:
        total_match_rate = sum(s["match_rate"] for s in per_pmcid_scores) / len(
            per_pmcid_scores
        )
    else:
        total_match_rate = 0.0

    results = {
        "timestamp": datetime.now().isoformat(),
        "run_name": run_name,
        "source_file": str(generated_variants_path),
        "total_match_rate": total_match_rate,
        "per_pmcid_scores": per_pmcid_scores,
    }

    # Ensure output directory exists and save results
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Results saved to {output_path}")
    print(f"Total match rate: {total_match_rate:.2%}")
    print(f"Scored {len(per_pmcid_scores)} PMCIDs")

    return results


def main():
    """Test the variant scoring functions using the benchmark data."""
    data = load_variant_bench_data()

    # Get the first entry from the benchmark data for testing
    test_pmcid = "PMC5508045"
    true_variants = data[test_pmcid]

    print(f"Testing with PMCID: {test_pmcid}")
    print(f"True variants: {true_variants}")

    # Test case 1: Perfect match
    print("\n--- Test 1: Perfect match ---")
    proposed_variants = true_variants
    result = score_variants(proposed_variants, true_variants, test_pmcid)
    print(f"Proposed: {proposed_variants}")
    print(f"Match rate: {result.match_rate:.2%}")
    print(f"Matches: {result.matches}")
    print(f"Extras: {result.extras}")
    print(f"Misses: {result.misses}")

    # Test case 2: Partial match with some correct, some wrong, some missing
    print("\n--- Test 2: Partial match ---")
    proposed = ["rs9923231", "rs887829", "rs12345678"]  # 2 correct, 1 wrong
    result = score_variants(proposed, true_variants, test_pmcid)
    print(f"Proposed: {proposed}")
    print(f"Match rate: {result.match_rate:.2%}")
    print(f"Matches: {result.matches}")
    print(f"Extras: {result.extras}")
    print(f"Misses: {result.misses}")

    # Test case 3: Using score_variants_by_pmcid
    print("\n--- Test 3: Score by PMCID ---")
    proposed = ["rs9923231", "rs1057910", "rs2108622", "rs887829"]
    result = score_variants_by_pmcid(proposed, test_pmcid)
    print(f"Proposed: {proposed}")
    print(f"Match rate: {result.match_rate:.2%}")
    print(f"Matches: {result.matches}")
    print(f"Extras: {result.extras}")
    print(f"Misses: {result.misses}")

    # Test case 4: No matches
    print("\n--- Test 4: No matches ---")
    proposed = ["rs00000000", "rs11111111"]
    result = score_variants_by_pmcid(proposed, test_pmcid)
    print(f"Proposed: {proposed}")
    print(f"Match rate: {result.match_rate:.2%}")
    print(f"Matches: {result.matches}")
    print(f"Extras: {result.extras}")
    print(f"Misses: {result.misses}")

    # Test case 5: Score generated variants from JSON file
    print("\n--- Test 5: Score generated variants from JSON file ---")
    generated_variants_path = (
        Path(__file__).parent.parent.parent
        / "data"
        / "benchmark_v2"
        / "generated_variants"
        / "example.json"
    )
    print(f"Generated variants file: {generated_variants_path}")
    results = score_generated_variants(generated_variants_path)
    print("\nSummary:")
    print(f"Total match rate: {results['total_match_rate']:.2%}")
    print(f"Number of PMCIDs scored: {len(results['per_pmcid_scores'])}")
    for score in results["per_pmcid_scores"]:
        print(f"\n  {score['pmcid']}: {score['title'][:50]}...")
        print(f"    Match rate: {score['match_rate']:.2%}")
        print(f"    Matches: {score['matches']}")
        print(f"    Extras: {score['extras']}")
        print(f"    Misses: {score['misses']}")


if __name__ == "__main__":
    main()
