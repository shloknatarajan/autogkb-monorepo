"""
Goal:
Given a set of ground truth sentences and a set of generated sentences, use an LLM judge to evaluate the similarity between them
and score the results. It goes variant-by-variant and produces a 0-1 score per variant (evaluating all the sentences for that variant
against all of the ground truths for that variant).
It should also produce a summary of the evaluation.
We do not care about exact wording, only whether the associations are generally correct (variant, direction, effect, phenotype, drug, comparison).

Notes:
- Use litellm for API calls
- Use load_dotenv() for API keys
- Ground truth sentences are stored in sentence_bench.jsonl
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables for API keys
load_dotenv()

from shared.utils import call_llm


@dataclass
class VariantSentenceScore:
    """Score for a single variant's sentences."""

    variant: str
    ground_truth: list[str]
    generated: list[str]
    score: float | None  # None if variant not in ground truth
    critique: str


@dataclass
class SentenceBenchResult:
    """Overall result for sentence benchmarking across multiple PMCIDs."""

    timestamp: str
    method: str
    judge_model: str | None
    source_file: str
    overall_avg_score: float
    num_pmcids: int
    per_pmcid: list[
        dict
    ]  # Each dict contains: pmcid, avg_score, num_variants_scored, num_variants_not_in_ground_truth, per_variant


def load_sentence_bench_data() -> dict[str, dict[str, list[str]]]:
    """Load the sentence benchmark data from the jsonl file.

    Returns:
        dict mapping pmcid -> variant -> list of ground truth sentences
    """
    data_path = (
        Path(__file__).parent.parent.parent
        / "data"
        / "benchmark_v2"
        / "sentence_bench.jsonl"
    )

    pmcid_data: dict[str, dict[str, list[str]]] = {}

    with open(data_path) as f:
        for line in f:
            record = json.loads(line)
            pmcid = record["pmcid"]
            variant = record["variant"]
            sentences = record["sentences"]

            if pmcid not in pmcid_data:
                pmcid_data[pmcid] = {}

            pmcid_data[pmcid][variant] = sentences

    return pmcid_data


def llm_judge_score(
    ground_truth_sentences: list[str],
    generated_sentences: list[str],
    variant: str,
    model: str = "claude-sonnet-4-20250514",
) -> tuple[float, str]:
    """Use an LLM to judge the similarity between ground truth and generated sentences.

    Args:
        ground_truth_sentences: List of ground truth sentences for this variant
        generated_sentences: List of generated sentences for this variant
        variant: The variant name being evaluated
        model: LLM model to use for judging

    Returns:
        Tuple of (score from 0-1, explanation)
    """
    prompt = f"""You are evaluating whether generated pharmacogenomic sentences capture the same associations as ground truth sentences.

Variant: {variant}

Ground Truth Sentences:
{chr(10).join(f"{i + 1}. {s}" for i, s in enumerate(ground_truth_sentences))}

Generated Sentences:
{chr(10).join(f"{i + 1}. {s}" for i, s in enumerate(generated_sentences))}

Evaluate whether the generated sentences capture the same pharmacogenomic associations as the ground truth. Focus on:
- Variant/genotype mentioned
- Direction of association (increased/decreased/not associated)
- Effect type (dose, risk, likelihood, etc.)
- Phenotype/condition
- Drug mentioned
- Comparison groups

Provide a similarity score from 0 to 1:
- 1.0: Perfect match - all key associations are captured correctly
- 0.7-0.9: Most associations captured, minor differences in specificity or wording
- 0.4-0.6: Some associations captured but missing key details or has inaccuracies.
- 0.1-0.3: Associations are mostly incorrect or contradictory. Opposite/incorrect associations should be here.
- 0.0: Completely incorrect or contradictory associations

Don't be generous with your scoring. Missing drug, phenotype, or key genotype information is a major issue
that should lower your score. You can also return a score that is specific to 2 decimal places.

Provide your response in this exact JSON format:
{{"score": <float between 0 and 1>, "explanation": "<brief explanation of your scoring>"}}"""

    try:
        content = call_llm(
            model=model,
            system_prompt="",
            user_prompt=prompt,
            temperature=0,
        )
        # Parse JSON response
        result = json.loads(content)
        score = float(result["score"])
        explanation = result["explanation"]

        return score, explanation

    except Exception as e:
        print(f"Error in LLM judge for variant {variant}: {e}")
        # Return a middle score as fallback
        return 0.5, f"Error during evaluation: {str(e)}"


def score_variant_sentences(
    variant: str,
    ground_truth: list[str],
    generated: list[str],
    model: str = "claude-sonnet-4-20250514",
) -> VariantSentenceScore:
    """Score generated sentences for a single variant against ground truth.

    Args:
        variant: Variant identifier
        ground_truth: List of ground truth sentences
        generated: List of generated sentences
        model: LLM model to use for judging

    Returns:
        VariantSentenceScore with detailed metrics
    """
    # Get LLM judge score
    llm_score, critique = llm_judge_score(ground_truth, generated, variant, model)

    return VariantSentenceScore(
        variant=variant,
        ground_truth=ground_truth,
        generated=generated,
        score=llm_score,
        critique=critique,
    )


def extract_sentences_from_generated(
    generated_data: list[str] | list[dict[str, str]],
) -> list[str]:
    """Extract sentences from generated data, handling both old and new formats.

    Args:
        generated_data: Either list of strings (old format) or list of dicts with 'sentence' key (new format)

    Returns:
        List of sentence strings
    """
    if not generated_data:
        return []

    # Check if first element is a dict (new format with explanations)
    if isinstance(generated_data[0], dict):
        return [item.get("sentence", "") for item in generated_data]
    else:
        # Old format: list of strings
        return generated_data


def score_generated_sentences(
    generated_sentences_path: str | Path,
    method: str = "llm",
    model: str = "claude-sonnet-4-20250514",
) -> SentenceBenchResult:
    """Score generated sentences from a JSON file against ground truth.

    Processes all PMCIDs found in the generated sentences file.

    Args:
        generated_sentences_path: Path to JSON file with generated sentences.
            Expected format: {pmcid: {variant: [sentences], ...}, ...}
            OR {pmcid: {variant: [{"sentence": ..., "explanation": ...}], ...}, ...}
        method: Method name for this evaluation
        model: LLM model to use for judging

    Returns:
        SentenceBenchResult with detailed scoring for all PMCIDs
    """
    generated_sentences_path = Path(generated_sentences_path)

    # Load generated sentences
    with open(generated_sentences_path) as f:
        generated_data = json.load(f)

    # Load ground truth data
    ground_truth_data = load_sentence_bench_data()

    # Get all PMCIDs from generated data
    pmcids = [k for k in generated_data.keys() if k.startswith("PMC")]

    if not pmcids:
        raise ValueError("No PMCIDs found in generated sentences file")

    # Process each PMCID
    per_pmcid_results = []
    total_score_across_pmcids = 0.0
    num_pmcids_scored = 0

    for pmcid in pmcids:
        if pmcid not in ground_truth_data:
            print(f"Warning: PMCID {pmcid} not found in ground truth data, skipping")
            continue

        # Get sentences for this PMCID
        generated_variants = generated_data[pmcid]
        ground_truth_variants = ground_truth_data[pmcid]

        # Score each variant for this PMCID
        per_variant_scores = []
        total_score = 0.0
        num_variants_scored = 0

        # First, score variants that are in ground truth
        for variant, gt_sentences in ground_truth_variants.items():
            gen_data = generated_variants.get(variant, [])
            # Extract sentences only (ignore explanations for evaluation)
            gen_sentences = extract_sentences_from_generated(gen_data)

            variant_score = score_variant_sentences(
                variant, gt_sentences, gen_sentences, model
            )

            per_variant_scores.append(
                {
                    "variant": variant,
                    "ground_truth": variant_score.ground_truth,
                    "generated": variant_score.generated,
                    "score": variant_score.score,
                    "critique": variant_score.critique,
                }
            )

            total_score += variant_score.score
            num_variants_scored += 1

        # Include extra variants in generated that aren't in ground truth
        # These get null scores and aren't counted in the average
        generated_extras = [
            v for v in generated_variants.keys() if v not in ground_truth_variants
        ]

        for variant in generated_extras:
            gen_data = generated_variants[variant]
            gen_sentences = extract_sentences_from_generated(gen_data)

            per_variant_scores.append(
                {
                    "variant": variant,
                    "ground_truth": None,
                    "generated": gen_sentences,
                    "score": None,
                    "critique": "Variant not found in ground truth - not scored",
                }
            )

        # Calculate average score for this PMCID (only counting scored variants)
        pmcid_avg_score = (
            total_score / num_variants_scored if num_variants_scored > 0 else 0.0
        )

        # Add to per_pmcid results
        per_pmcid_results.append(
            {
                "pmcid": pmcid,
                "avg_score": round(pmcid_avg_score, 3),
                "num_variants_scored": num_variants_scored,
                "num_variants_not_in_ground_truth": len(generated_extras),
                "per_variant": per_variant_scores,
            }
        )

        total_score_across_pmcids += pmcid_avg_score
        num_pmcids_scored += 1

    # Calculate overall average score across all PMCIDs
    overall_avg_score = (
        total_score_across_pmcids / num_pmcids_scored if num_pmcids_scored > 0 else 0.0
    )

    # Create result
    return SentenceBenchResult(
        timestamp=datetime.now().isoformat(),
        method=method,
        judge_model=model,
        source_file=str(generated_sentences_path),
        overall_avg_score=round(overall_avg_score, 3),
        num_pmcids=num_pmcids_scored,
        per_pmcid=per_pmcid_results,
    )


def score_and_save(
    generated_sentences_path: str | Path,
    method: str = "llm",
    model: str = "claude-sonnet-4-20250514",
    output_path: str | Path | None = None,
) -> SentenceBenchResult:
    """Score generated sentences and save results to a JSON file.

    Processes all PMCIDs found in the generated sentences file.

    Args:
        generated_sentences_path: Path to JSON file with generated sentences
        method: Method name for this evaluation
        model: LLM model to use for judging
        output_path: Path to save results. If None, auto-generates name.

    Returns:
        SentenceBenchResult
    """
    result = score_generated_sentences(generated_sentences_path, method, model)

    # Generate output path if not provided
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_safe = model.replace("/", "_").replace(":", "_") if model else "none"
        output_path = (
            Path(__file__).parent.parent.parent
            / "data"
            / "benchmark_v2"
            / "sentence_bench_results"
            / f"sentence_scores_{method}_{model_safe}_{timestamp}.json"
        )
    else:
        output_path = Path(output_path)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save results
    result_dict = {
        "timestamp": result.timestamp,
        "method": result.method,
        "judge_model": result.judge_model,
        "source_file": result.source_file,
        "overall_avg_score": result.overall_avg_score,
        "num_pmcids": result.num_pmcids,
        "per_pmcid": result.per_pmcid,
    }

    with open(output_path, "w") as f:
        json.dump(result_dict, f, indent=2)

    print(f"Results saved to {output_path}")
    print("\nOverall Statistics:")
    print(f"  Overall Average Score: {result.overall_avg_score:.3f}")
    print(f"  Number of PMCIDs: {result.num_pmcids}")
    print("\nPer-PMCID Scores:")
    for pmcid_result in result.per_pmcid:
        num_scored = pmcid_result["num_variants_scored"]
        num_not_in_gt = pmcid_result["num_variants_not_in_ground_truth"]
        variants_info = f"{num_scored} variants"
        if num_not_in_gt > 0:
            variants_info += f", {num_not_in_gt} not in ground truth"
        print(
            f"  {pmcid_result['pmcid']}: {pmcid_result['avg_score']:.3f} ({variants_info})"
        )

    return result


def main():
    """Test the sentence scoring functions."""
    # Example usage
    generated_file = (
        Path(__file__).parent.parent.parent
        / "src"
        / "experiments"
        / "sentence_generation"
        / "llm_judge_ask"
        / "outputs"
        / "openai_gpt-4o-mini_v1_20260119_223926.json"
    )

    print(f"Scoring sentences from: {generated_file}")

    # Score all PMCIDs in file
    result = score_and_save(
        generated_sentences_path=generated_file,
        method="llm",
        model="gpt-4o-mini",
    )

    # Print detailed results for each PMCID
    print("\n=== Detailed Results ===")
    for pmcid_result in result.per_pmcid:
        print(f"\n{pmcid_result['pmcid']} (Avg: {pmcid_result['avg_score']:.3f})")
        for variant_result in pmcid_result["per_variant"]:
            score = variant_result["score"]
            if score is not None:
                print(f"  {variant_result['variant']}: {score:.3f}")
            else:
                print(f"  {variant_result['variant']}: N/A (not in ground truth)")


if __name__ == "__main__":
    main()
