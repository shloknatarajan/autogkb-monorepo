"""
Evaluation Pipeline for Pharmacogenomics Knowledge Extraction

Evaluates pipeline run directories by delegating to experiment eval modules:
- Variant evaluation via variant_finding.eval
- Sentence evaluation via sentence_generation.eval
- Citation evaluation via citations.eval
- Summary evaluation via summary.eval

Input: A pipeline run directory (outputs/<run_name>/)
Output: Aggregate results saved to <run_dir>/eval_results/aggregate.json

Example Commands:

1. Evaluate a run directory:
   python -m src.eval_pipeline.eval_pipeline --input outputs/base_config_20240101/

2. Evaluate specific stages:
   python -m src.eval_pipeline.eval_pipeline --input outputs/base_config_20240101/ --stages variants,sentences

3. Use a different judge model:
   python -m src.eval_pipeline.eval_pipeline --input outputs/base_config_20240101/ --judge-model gpt-4o
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# Paths
EVAL_PIPELINE_DIR = Path(__file__).resolve().parent
ROOT = EVAL_PIPELINE_DIR.parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.v2.eval import variant_eval
from benchmark.v2.eval import sentence_eval
from benchmark.v2.eval import citation_eval
from benchmark.v2.eval import summary_eval

CONFIGS_DIR = EVAL_PIPELINE_DIR / "configs"
CONFIG_FILE = CONFIGS_DIR / "default_config.yaml"


# =============================================================================
# CONFIGURATION
# =============================================================================


def load_config(config_path: Path = CONFIG_FILE) -> dict:
    """Load evaluation configuration from YAML file."""
    logger.debug(f"Loading config from {config_path}")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    logger.info(
        f"Loaded eval config: {config.get('config', {}).get('name', 'unknown')}"
    )
    return config


# =============================================================================
# COLLECTION HELPERS
# =============================================================================


def _collect_sentences(sentences_dir: Path) -> dict:
    """Read per-PMCID sentence files and assemble into the format
    expected by sentence_eval.evaluate_from_file().

    Expected input format per file: {variant: [{sentence, explanation}, ...]}
    Output format: {"sentences": {pmcid: {variant: [{sentence, explanation}]}}}
    """
    sentences: dict[str, dict] = {}
    if not sentences_dir.is_dir():
        return {"sentences": sentences}

    for f in sorted(sentences_dir.glob("*.json")):
        pmcid = f.stem
        with open(f) as fh:
            sentences[pmcid] = json.load(fh)

    return {"sentences": sentences, "run_name": "pipeline_run"}


def _collect_citations(citations_dir: Path) -> dict:
    """Read per-PMCID citation files and assemble into the format
    expected by citation_eval.evaluate_from_file().

    Expected input format per file: [{variant, sentence, explanation, citations}, ...]
    Output format: {"citations": {pmcid: [citation_dicts]}}
    """
    citations: dict[str, list] = {}
    if not citations_dir.is_dir():
        return {"citations": citations}

    for f in sorted(citations_dir.glob("*.json")):
        pmcid = f.stem
        with open(f) as fh:
            citations[pmcid] = json.load(fh)

    return {"citations": citations, "run_name": "pipeline_run"}


def _collect_summaries(summaries_dir: Path) -> dict:
    """Read per-PMCID summary files and assemble into the format
    expected by summary_eval.evaluate_from_file().

    Expected input format per file: {pmcid, summary, num_variants, variants}
    Output format: {"summaries": [{pmcid, summary, ...}, ...]}
    """
    summaries: list[dict] = []
    if not summaries_dir.is_dir():
        return {"summaries": summaries}

    for f in sorted(summaries_dir.glob("*.json")):
        with open(f) as fh:
            summaries.append(json.load(fh))

    return {"summaries": summaries, "run_name": "pipeline_run"}


# =============================================================================
# EVALUATION
# =============================================================================


def evaluate_run(
    run_dir: Path,
    config: dict,
    stages: set[str],
    judge_model: str = "claude-sonnet-4-20250514",
) -> dict[str, Any]:
    """Evaluate a pipeline run directory.

    Args:
        run_dir: Path to the pipeline run directory.
        config: Pipeline or eval configuration dict.
        stages: Set of stages to evaluate.
        judge_model: LLM model for judging sentences/citations/summaries.

    Returns:
        Dict with evaluation results per stage.
    """
    eval_dir = run_dir / "eval_results"
    eval_dir.mkdir(exist_ok=True)

    results: dict[str, Any] = {}

    # Variant evaluation
    if "variants" in stages:
        variants_path = run_dir / "variants.json"
        if variants_path.exists():
            logger.info("Evaluating variants...")
            results["variants"] = variant_eval.evaluate_from_file(
                variants_path, save_results=False
            )
        else:
            logger.warning("No variants.json found, skipping variant evaluation")

    # Sentence evaluation
    if "sentences" in stages:
        sentences_dir = run_dir / "sentences"
        if sentences_dir.is_dir() and any(sentences_dir.glob("*.json")):
            logger.info("Evaluating sentences...")
            sentences_data = _collect_sentences(sentences_dir)
            # Write temporary file for evaluate_from_file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, dir=eval_dir
            ) as tmp:
                json.dump(sentences_data, tmp, indent=2)
                tmp_path = Path(tmp.name)
            try:
                results["sentences"] = _bench_result_to_dict(
                    sentence_eval.evaluate_from_file(
                        tmp_path, judge_model=judge_model, save_results=False
                    )
                )
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            logger.warning("No sentence files found, skipping sentence evaluation")

    # Citation evaluation
    if "citations" in stages:
        citations_dir = run_dir / "citations"
        if citations_dir.is_dir() and any(citations_dir.glob("*.json")):
            logger.info("Evaluating citations...")
            citations_data = _collect_citations(citations_dir)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, dir=eval_dir
            ) as tmp:
                json.dump(citations_data, tmp, indent=2)
                tmp_path = Path(tmp.name)
            try:
                results["citations"] = citation_eval.evaluate_from_file(
                    tmp_path, judge_model=judge_model, save_results=False
                )
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            logger.warning("No citation files found, skipping citation evaluation")

    # Summary evaluation
    if "summary" in stages:
        summaries_dir = run_dir / "summaries"
        if summaries_dir.is_dir() and any(summaries_dir.glob("*.json")):
            logger.info("Evaluating summaries...")
            summaries_data = _collect_summaries(summaries_dir)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, dir=eval_dir
            ) as tmp:
                json.dump(summaries_data, tmp, indent=2)
                tmp_path = Path(tmp.name)
            try:
                results["summaries"] = summary_eval.evaluate_from_file(
                    tmp_path, judge_model=judge_model, save_results=False
                )
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            logger.warning("No summary files found, skipping summary evaluation")

    # Save aggregate results
    aggregate_path = eval_dir / "aggregate.json"
    with open(aggregate_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    logger.success(f"Evaluation complete! Results saved to: {aggregate_path}")
    _print_summary(results)

    return results


def _bench_result_to_dict(result: Any) -> dict:
    """Convert a SentenceBenchResult (or similar) to a JSON-serializable dict."""
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "__dict__"):
        return {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
    return {"result": str(result)}


def _print_summary(results: dict) -> None:
    """Print a human-readable evaluation summary."""
    logger.info("\nEvaluation Summary:")

    if "variants" in results:
        vr = results["variants"]
        logger.info(
            f"  Variants - Avg Recall: {vr.get('avg_recall', 0):.1%}, "
            f"Avg Precision: {vr.get('avg_precision', 0):.1%}"
        )

    if "sentences" in results:
        sr = results["sentences"]
        avg = sr.get("average_score") or sr.get("avg_score", 0)
        logger.info(f"  Sentences - Avg Score: {avg}")

    if "citations" in results:
        cr = results["citations"]
        logger.info(
            f"  Citations - Combined: {cr.get('overall_combined_score', 0):.3f}, "
            f"Grounding: {cr.get('overall_grounding_rate', 0):.3f}"
        )

    if "summaries" in results:
        smr = results["summaries"]
        logger.info(f"  Summaries - Avg Score: {smr.get('overall_avg_score', 0):.3f}")


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate pipeline outputs against ground truth."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to pipeline run directory (e.g., outputs/base_config_20240101/)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_FILE,
        help=f"Path to eval config YAML file (default: {CONFIG_FILE})",
    )
    parser.add_argument(
        "--stages",
        default="variants,sentences",
        help="Comma-separated list of stages to evaluate (default: variants,sentences)",
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default=None,
        help="Override judge model for sentence/citation/summary evaluation",
    )
    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Parse stages
    stages = set(s.strip() for s in args.stages.split(","))
    valid_stages = {"variants", "sentences", "citations", "summary"}
    invalid_stages = stages - valid_stages
    if invalid_stages:
        logger.error(f"Invalid stages: {invalid_stages}. Valid: {valid_stages}")
        sys.exit(1)

    # Determine judge model
    judge_model = args.judge_model or config.get("sentence_evaluation", {}).get(
        "judge_model", "claude-sonnet-4-20250514"
    )

    logger.info("Evaluation Configuration:")
    logger.info(f"  Input directory: {args.input}")
    logger.info(f"  Stages: {sorted(stages)}")
    logger.info(f"  Judge model: {judge_model}")

    # Run evaluation
    evaluate_run(args.input, config, stages, judge_model=judge_model)


if __name__ == "__main__":
    main()
