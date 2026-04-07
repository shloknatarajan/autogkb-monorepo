"""
Citation Judge - Evaluate quality of citations for pharmacogenomic associations.

This script evaluates how well the found citations support the pharmacogenomic
association claims. It uses an LLM as a judge to score each citation set.

The evaluation is done in batches per PMCID, where all association sentences
for a given PMCID are evaluated together for consistency.
"""

from __future__ import annotations

import json
import re
import sys
import warnings
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger

# Suppress Pydantic serialization warnings from litellm
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic.main")

# Load environment (API keys, etc.)
load_dotenv()

# Paths
ROOT = Path(__file__).resolve().parents[4]

# Add repository root to Python path to enable imports
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import utils
from shared.utils import call_llm

# Judge prompt
JUDGE_SYSTEM_PROMPT = """You are an expert evaluator of scientific citations for pharmacogenomic claims.

Your task is to evaluate how well a set of citations from a scientific article support a given pharmacogenomic association claim.

You will score each citation set on a scale of 0-100 based on:
1. Relevance: Do the citations directly relate to the claimed association?
2. Support: Do the citations provide evidence for the specific claim?
3. Completeness: Do the citations together include all key information (statistical evidence, sample size, effect direction)?
4. Quality: Are the citations from appropriate sections (Results, Methods, Tables)?

Scoring guidelines:
- 90-100: Excellent - Citations strongly support the claim with statistical evidence and key details
- 70-89: Good - Citations support the claim with reasonable evidence
- 50-69: Fair - Citations relate to the claim but lack key supporting details
- 30-49: Poor - Citations are tangentially related but don't strongly support the claim
- 0-29: Very Poor - Citations are irrelevant or contradictory
"""

JUDGE_USER_PROMPT_TEMPLATE = """Evaluate the citation quality for the following pharmacogenomic associations from PMCID {pmcid}.

For each numbered association, I will provide:
1. The pharmacogenomic claim (association sentence)
2. The citations found to support this claim

Note: The same variant may appear multiple times with different association sentences. Please score EACH association separately based on how well the citations support that specific claim.

Please score each association's citation set on a 0-100 scale and provide a brief justification.

{associations_and_citations}

OUTPUT FORMAT:
For each association (using the same number from the input), provide:
ASSOCIATION: [number]
SCORE: [0-100]
JUSTIFICATION: [1-2 sentence explanation of the score]

Then a blank line before the next association.

Example:
ASSOCIATION: 1
SCORE: 85
JUSTIFICATION: Citations provide strong statistical evidence (p-values) and effect sizes. Table reference is appropriate. Missing explicit sample size but overall well-supported.

ASSOCIATION: 2
SCORE: 72
JUSTIFICATION: Citations support the general association but lack specific statistical significance values. Effect direction is clear.
"""


def load_citations(
    citations_path: Path,
) -> tuple[dict[str, list[dict]], dict[str, Any]]:
    """Load citation data from JSON file.

    Args:
        citations_path: Path to citations JSON file

    Returns:
        Tuple of (citations_dict, metadata_dict)
        - citations_dict: {pmcid: [{variant, sentence, explanation, citations}, ...]}
        - metadata_dict: metadata about the citation generation (model, prompt, etc.)
    """
    logger.debug(f"Loading citations from {citations_path}")
    with open(citations_path) as f:
        data = json.load(f)

    # Handle both old format (direct dict) and new format (with metadata)
    if "citations" in data and "metadata" in data:
        citations = data["citations"]
        metadata = data["metadata"]
        logger.info(f"Loaded citations for {len(citations)} PMCID(s) with metadata")
    else:
        # Old format - data is directly the citations dict
        citations = data
        metadata = {}
        logger.info(
            f"Loaded citations for {len(citations)} PMCID(s) (legacy format without metadata)"
        )

    return citations, metadata


def load_sentence_bench(sentence_bench_path: Path) -> dict[str, dict[str, dict]]:
    """Load sentence benchmark data grouped by PMCID and variant.

    Args:
        sentence_bench_path: Path to sentence_bench.jsonl

    Returns:
        Dictionary with structure {pmcid: {variant: {sentence, explanation}}}
    """
    logger.debug(f"Loading sentence benchmark from {sentence_bench_path}")
    pmcid_data: dict[str, dict[str, dict]] = {}

    with open(sentence_bench_path) as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            pmcid = rec["pmcid"]
            variant = rec["variant"]

            if pmcid not in pmcid_data:
                pmcid_data[pmcid] = {}

            # Handle both formats
            sentences = rec.get("sentences", [])
            if sentences and isinstance(sentences[0], dict):
                sentence = sentences[0]["sentence"]
                explanation = sentences[0].get("explanation", "")
            else:
                sentence = sentences[0] if sentences else ""
                explanation = ""

            pmcid_data[pmcid][variant] = {
                "sentence": sentence,
                "explanation": explanation,
            }

    logger.info(f"Loaded sentence data for {len(pmcid_data)} PMCID(s)")
    return pmcid_data


def parse_judge_output(output: str) -> dict[int, dict[str, Any]]:
    """Parse judge LLM output into association scores.

    Expected format:
        ASSOCIATION: 1
        SCORE: 85
        JUSTIFICATION: Citations provide strong evidence...

        ASSOCIATION: 2
        SCORE: 72
        JUSTIFICATION: Citations support the general association...
    """
    result: dict[int, dict[str, Any]] = {}

    # Split by ASSOCIATION: markers
    assoc_blocks = re.split(r"\n\s*ASSOCIATION:\s*", output)

    for block in assoc_blocks:
        if not block.strip():
            continue

        lines = block.strip().split("\n")
        if not lines:
            continue

        assoc_line = lines[0].strip()
        # Remove "ASSOCIATION:" prefix if present (happens for first association in output)
        if assoc_line.upper().startswith("ASSOCIATION:"):
            assoc_line = assoc_line[12:].strip()

        # Parse the association index
        try:
            assoc_idx = int(assoc_line)
        except ValueError:
            logger.warning(f"Could not parse association index: {assoc_line}")
            continue

        score = None
        justification = ""

        for line in lines[1:]:
            line = line.strip()
            if line.upper().startswith("SCORE:"):
                score_text = line.split(":", 1)[1].strip()
                try:
                    score = float(score_text)
                except ValueError:
                    logger.warning(f"Could not parse score: {score_text}")
            elif line.upper().startswith("JUSTIFICATION:"):
                justification = line.split(":", 1)[1].strip()
            elif justification:
                # Continue multi-line justification
                justification += " " + line

        if score is not None:
            result[assoc_idx] = {"score": score, "justification": justification.strip()}
            logger.debug(f"Parsed score {score} for association {assoc_idx}")

    if not result:
        logger.warning("Failed to parse any scores from judge output")
        logger.debug(f"Output was: {output[:500]}...")

    return result


def evaluate_pmcid(
    pmcid: str,
    associations: list[dict],
    judge_model: str,
) -> list[dict[str, Any]]:
    """Evaluate citations for a single PMCID.

    Args:
        pmcid: PMCID identifier
        associations: List of {variant, sentence, explanation, citations} dicts
        judge_model: Model name for judge LLM

    Returns:
        List of {variant, sentence, score, justification} dicts (one per association)
    """
    logger.info(
        f"Evaluating citations for PMCID: {pmcid} ({len(associations)} associations)"
    )

    # Format associations and citations for the prompt (1-indexed)
    associations_text_parts = []
    for i, assoc in enumerate(associations):
        variant = assoc["variant"]
        sentence = assoc.get("sentence", "")
        cites = assoc.get("citations", [])

        cite_text = "\n   ".join([f"{j + 1}. {c}" for j, c in enumerate(cites)])

        associations_text_parts.append(
            f"ASSOCIATION {i + 1}:\n"
            f"VARIANT: {variant}\n"
            f"CLAIM: {sentence}\n"
            f"CITATIONS:\n   {cite_text if cites else '(No citations found)'}"
        )

    associations_and_citations = "\n\n".join(associations_text_parts)

    # Create prompt
    user_prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
        pmcid=pmcid, associations_and_citations=associations_and_citations
    )

    # Call judge LLM
    try:
        logger.debug(f"Calling judge LLM for {len(associations)} association(s)")
        output = call_llm(judge_model, JUDGE_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        logger.error(f"Error calling judge for {pmcid}: {e}")
        # Return empty scores for all associations
        return [
            {
                "variant": assoc["variant"],
                "sentence": assoc.get("sentence", ""),
                "score": 0,
                "justification": "Error during evaluation",
            }
            for assoc in associations
        ]

    # Parse scores (returns {assoc_idx: {score, justification}})
    parsed_scores = parse_judge_output(output)

    # Build result list matching associations
    result: list[dict[str, Any]] = []
    for i, assoc in enumerate(associations):
        assoc_idx = i + 1  # 1-indexed
        score_info = parsed_scores.get(
            assoc_idx, {"score": 0, "justification": "No score provided by judge"}
        )

        if assoc_idx not in parsed_scores:
            logger.warning(
                f"Missing score for association {assoc_idx}, defaulting to 0"
            )

        result.append(
            {
                "variant": assoc["variant"],
                "sentence": assoc.get("sentence", ""),
                "score": score_info["score"],
                "justification": score_info["justification"],
            }
        )

    return result


def evaluate_citations(
    citations_path: Path,
    sentence_bench_path: Path,  # Kept for API compatibility but not used
    judge_model: str,
    output_path: Path,
) -> dict[str, Any]:
    """Evaluate all citations and save results.

    Args:
        citations_path: Path to citations JSON file
        sentence_bench_path: Path to sentence_bench.jsonl (kept for API compatibility, not used)
        judge_model: Model name for judge LLM
        output_path: Path to save evaluation results

    Returns:
        Dictionary with evaluation summary
    """
    # Load citation data (now includes sentences directly)
    citations_data, citation_metadata = load_citations(citations_path)

    # Evaluate each PMCID
    all_results: dict[str, list[dict[str, Any]]] = {}
    pmcid_summaries = []
    all_scores = []

    for pmcid, pmcid_associations in citations_data.items():
        if not pmcid_associations:
            logger.warning(f"No associations found for {pmcid}, skipping evaluation")
            continue

        # Evaluate this PMCID
        scores = evaluate_pmcid(pmcid, pmcid_associations, judge_model)

        all_results[pmcid] = scores

        # Calculate average for this PMCID
        assoc_scores = [s["score"] for s in scores]
        avg_score = sum(assoc_scores) / len(assoc_scores) if assoc_scores else 0
        all_scores.extend(assoc_scores)

        pmcid_summaries.append(
            {
                "pmcid": pmcid,
                "num_associations": len(scores),
                "avg_score": avg_score,
                "scores": scores,
            }
        )

        logger.info(f"✓ {pmcid}: avg score = {avg_score:.2f}")

    # Calculate overall average
    overall_avg = sum(all_scores) / len(all_scores) if all_scores else 0

    # Create summary result
    result = {
        "citation_metadata": citation_metadata,  # Include original citation generation metadata
        "judge_model": judge_model,  # Add judge model used
        "overall_avg_score": overall_avg,
        "num_pmcids": len(pmcid_summaries),
        "num_total_associations": len(all_scores),
        "per_pmcid": pmcid_summaries,
        "details": all_results,
    }

    # Save results
    logger.debug(f"Saving evaluation results to {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.success(f"Saved evaluation results to {output_path}")

    return result


def main():
    """Main entry point for standalone evaluation."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Evaluate citation quality for pharmacogenomic associations"
    )
    parser.add_argument(
        "--citations",
        required=True,
        type=Path,
        help="Path to citations JSON file",
    )
    parser.add_argument(
        "--sentence-bench",
        type=Path,
        default=ROOT / "data" / "benchmark_v2" / "sentence_bench.jsonl",
        help="Path to sentence_bench.jsonl (default: data/benchmark_v2/sentence_bench.jsonl)",
    )
    parser.add_argument(
        "--judge-model",
        default="claude-3-haiku-20240307",
        help="Model to use for judging (default: claude-3-haiku-20240307)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to save evaluation results JSON",
    )
    args = parser.parse_args()

    result = evaluate_citations(
        citations_path=args.citations,
        sentence_bench_path=args.sentence_bench,
        judge_model=args.judge_model,
        output_path=args.output,
    )

    logger.info("Evaluation Summary")
    logger.info(f"Overall Average Score: {result['overall_avg_score']:.3f}")
    logger.info(f"Number of PMCIDs: {result['num_pmcids']}")
    logger.info(f"Total Associations Evaluated: {result['num_total_associations']}")


if __name__ == "__main__":
    main()
