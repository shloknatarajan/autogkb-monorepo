"""
Batch Judge Ask - Generate sentences for all variants in a PMCID at once.

(See batch_judge_ask.md for more details)

This script is similar to llm_judge_ask but batches all variants for a given PMCID
into a single LLM call instead of processing them one-by-one. This can be more
efficient and may produce more consistent results across variants.

(default judge model: claude-3-haiku-20240307)

Example Commands:

1. Run with default model (gpt-5) and prompt (v3) for one PMCID:
   python batch_judge_ask.py

2. Specify a different model and prompt, and process 1 PMCID:
   python batch_judge_ask.py --model gpt-4o-mini --prompt v3 --num-pmcids 1

3. Run with v4 prompt (includes sentence + explanation):
   python batch_judge_ask.py --model gpt-4o-mini --prompt v4 --num-pmcids 1

4. Run without automatic evaluation:
   python batch_judge_ask.py --no-eval

5. Specify a different judge model for evaluation:
   python batch_judge_ask.py --model claude-sonnet-4-20250514 --prompt v3 --judge-model claude-3-haiku-20240307

Prompts:
- v3: Batch dual format (rsID + genotype) - recommended for most uses
- v4: Batch dual format with explanation - includes brief evidence explanation
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path

import yaml
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

from shared.utils import call_llm

# Import sentence bench for evaluation
try:
    from benchmark.v2.sentence_bench import score_and_save
except ImportError:
    score_and_save = None

VARIANT_BENCH_PATH = ROOT / "data" / "benchmark_v2" / "variant_bench.jsonl"
PROMPTS_FILE = Path(__file__).parent / "prompts.yaml"
OUTPUTS_DIR = Path(__file__).parent / "outputs"
RESULTS_DIR = Path(__file__).parent / "results"


def load_prompts() -> dict:
    """Load prompt configurations from prompts.yaml."""
    logger.debug(f"Loading prompts from {PROMPTS_FILE}")
    with open(PROMPTS_FILE) as f:
        prompts = yaml.safe_load(f)
    logger.info(f"Loaded {len(prompts)} prompt(s)")
    return prompts


def get_n_pmcids_and_variants(n: int) -> list[tuple[str, list[str]]]:
    """Return the first N PMCIDs and their variant lists from variant_bench.jsonl."""
    logger.debug(f"Loading {n} PMCID(s) from {VARIANT_BENCH_PATH}")
    results = []
    with open(VARIANT_BENCH_PATH) as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            if not line.strip():
                continue
            rec = json.loads(line)
            results.append((rec["pmcid"], rec["variants"]))
    logger.info(f"Loaded {len(results)} PMCID(s) with variants")
    return results


def parse_batch_output(
    output: str, use_explanations: bool
) -> dict[str, list[str] | list[dict[str, str]]]:
    """Parse batch LLM output into a dict mapping variant -> sentence(s) or parsed dicts.

    Expected format for v3:
        VARIANT: rs9923231
        SENTENCE: Genotypes CT + TT of rs9923231 are associated with...

        VARIANT: rs1057910
        SENTENCE: Genotypes AC + CC of rs1057910 are associated with...

    Expected format for v4:
        VARIANT: rs9923231
        SENTENCE: Genotypes CT + TT of rs9923231 are associated with...
        EXPLANATION: A study of 1,015 patients found...

        VARIANT: rs1057910
        SENTENCE: Genotypes AC + CC of rs1057910 are associated with...
        EXPLANATION: The study demonstrated...
    """
    result: dict[str, list[str] | list[dict[str, str]]] = {}

    # Split output into blocks for each variant
    # Use a regex to find VARIANT: ... SENTENCE: ... [EXPLANATION: ...] patterns
    if use_explanations:
        # Pattern for v4: VARIANT, SENTENCE, and EXPLANATION
        pattern = r"VARIANT:\s*(.+?)\s*\n\s*SENTENCE:\s*(.+?)\s*\n\s*EXPLANATION:\s*(.+?)(?=\n\s*VARIANT:|$)"
        matches = re.findall(pattern, output, re.DOTALL | re.IGNORECASE)

        for match in matches:
            variant_id = match[0].strip()
            sentence = match[1].strip()
            explanation = match[2].strip()
            result[variant_id] = [{"sentence": sentence, "explanation": explanation}]
            logger.debug(f"Parsed variant {variant_id} with explanation")
    else:
        # Pattern for v3: VARIANT and SENTENCE only
        pattern = r"VARIANT:\s*(.+?)\s*\n\s*SENTENCE:\s*(.+?)(?=\n\s*VARIANT:|$)"
        matches = re.findall(pattern, output, re.DOTALL | re.IGNORECASE)

        for match in matches:
            variant_id = match[0].strip()
            sentence = match[1].strip()
            result[variant_id] = [sentence]
            logger.debug(f"Parsed variant {variant_id}")

    if not result:
        logger.warning("Failed to parse any variants from batch output")
        logger.debug(f"Output was: {output[:500]}...")

    return result


def process_pmcid(
    pmcid: str,
    variants: list[str],
    model: str,
    prompt_cfg: dict,
    prompt_name: str,
) -> dict[str, dict[str, list[str] | list[dict[str, str]]]]:
    """Process a single PMCID: generate sentences for all variants in batch.

    Returns:
        A dictionary with structure {pmcid: {variant: sentences}}
    """
    logger.info(
        f"Processing PMCID: {pmcid} with {len(variants)} variant(s) in batch mode"
    )

    # Get article text; reuse utils for markdown content
    try:
        from shared.utils import (
            get_methods_and_conclusions_text,
            get_markdown_text,
        )
    except Exception:
        get_methods_and_conclusions_text = None
        get_markdown_text = None

    article_text = ""
    if get_methods_and_conclusions_text is not None:
        article_text = get_methods_and_conclusions_text(pmcid)
    if not article_text and get_markdown_text is not None:
        article_text = get_markdown_text(pmcid)

    if not article_text:
        logger.warning(
            f"No article text found for {pmcid}. The model may return generic sentences."
        )

    logger.info(f"Variants: {', '.join(variants)}")

    # Determine if we're using v4 prompt (with explanations)
    use_explanations = prompt_name == "v4"

    # Format variants list for the prompt
    variants_list = "\n".join([f"- {variant}" for variant in variants])

    # Create the batch prompt
    user_prompt = prompt_cfg["user"].format(
        variants_list=variants_list, article_text=article_text
    )
    system_prompt = prompt_cfg["system"]

    # Call LLM once for all variants
    try:
        logger.debug(f"Making single LLM call for all {len(variants)} variants")
        output = call_llm(model, system_prompt, user_prompt)
    except Exception as e:
        output = ""
        logger.error(f"Error generating batch for {pmcid}: {e}")

    # Parse the batch output
    variant_results = parse_batch_output(output, use_explanations)

    # Build result structure matching llm_judge_ask format
    result: dict[str, dict[str, list[str] | list[dict[str, str]]]] = {pmcid: {}}

    # Ensure all requested variants are in the result
    for variant in variants:
        if variant in variant_results:
            result[pmcid][variant] = variant_results[variant]
            if use_explanations:
                preview = (
                    variant_results[variant][0]["sentence"]
                    if variant_results[variant]
                    else "<no output>"
                )
            else:
                preview = (
                    variant_results[variant][0]
                    if variant_results[variant]
                    else "<no output>"
                )
            logger.info(
                f"✓ {variant}: {preview[:90]}{'...' if len(preview) > 90 else ''}"
            )
        else:
            # Variant not found in parsed output
            if use_explanations:
                result[pmcid][variant] = [{"sentence": "", "explanation": ""}]
            else:
                result[pmcid][variant] = []
            logger.warning(f"✗ {variant}: not found in batch output")

    return result


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate association sentences for PMCID variants in batch mode and save JSON."
        )
    )
    parser.add_argument(
        "--model",
        default="gpt-5",
        help="Model name for litellm (default: gpt-5)",
    )
    parser.add_argument(
        "--prompt",
        default="v3",
        help="Prompt key from prompts.yaml (e.g., v3, v4)",
    )
    parser.add_argument(
        "--no-eval",
        action="store_true",
        help="Skip automatic evaluation after generation",
    )
    parser.add_argument(
        "--judge-model",
        default="claude-3-haiku-20240307",
        help="Model to use for evaluation judging (default: claude-3-haiku-20240307)",
    )
    parser.add_argument(
        "--num-pmcids",
        type=int,
        default=1,
        help="Number of PMCIDs to process (default: 1)",
    )
    args = parser.parse_args()

    prompts = load_prompts()
    if args.prompt not in prompts:
        raise KeyError(f"Prompt '{args.prompt}' not found in {PROMPTS_FILE}")
    prompt_cfg = prompts[args.prompt]

    # Get PMCIDs to process
    pmcids_and_variants = get_n_pmcids_and_variants(args.num_pmcids)

    logger.info(f"Prompt: {args.prompt} ({prompt_cfg.get('name', '')})")
    logger.info(f"Generation Model: {args.model}")
    logger.info(f"Judge Model: {args.judge_model}")
    logger.info(f"Processing {len(pmcids_and_variants)} PMCID(s) in batch mode")

    # Generate timestamp once for this run
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Collect results from all PMCIDs
    all_results: dict[str, dict[str, list[str] | list[dict[str, str]]]] = {}

    # Process each PMCID
    for pmcid, variants in pmcids_and_variants:
        pmcid_result = process_pmcid(
            pmcid=pmcid,
            variants=variants,
            model=args.model,
            prompt_cfg=prompt_cfg,
            prompt_name=args.prompt,
        )
        # Merge results into the combined dictionary
        all_results.update(pmcid_result)

    # Save all results to a single output file
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = args.model.replace("/", "_").replace(":", "_")
    out_path = OUTPUTS_DIR / f"{safe_model}_{args.prompt}_{timestamp}.json"

    logger.debug(f"Saving generated sentences to {out_path}")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    logger.success(
        f"Saved generated sentences for {len(all_results)} PMCID(s) to {out_path}"
    )

    # Evaluate generated sentences against ground truth
    if not args.no_eval and score_and_save is not None:
        logger.info("Evaluating sentences against ground truth")
        try:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            safe_judge_model = args.judge_model.replace("/", "_").replace(":", "_")
            eval_path = (
                RESULTS_DIR / f"sentence_scores_llm_{safe_judge_model}_{timestamp}.json"
            )

            logger.debug(f"Running evaluation with judge model: {args.judge_model}")
            eval_result = score_and_save(
                generated_sentences_path=out_path,
                method="llm",
                model=args.judge_model,
                output_path=eval_path,
            )

            logger.info("Evaluation Summary")
            logger.info(f"Overall Average Score: {eval_result.overall_avg_score:.3f}")
            logger.info(f"Number of PMCIDs: {eval_result.num_pmcids}")
            logger.info("Per-PMCID Scores:")
            for pmcid_result in eval_result.per_pmcid:
                num_scored = pmcid_result["num_variants_scored"]
                num_not_in_gt = pmcid_result["num_variants_not_in_ground_truth"]
                variants_info = f"{num_scored} variants"
                if num_not_in_gt > 0:
                    variants_info += f", {num_not_in_gt} not in ground truth"
                logger.info(
                    f"  {pmcid_result['pmcid']}: {pmcid_result['avg_score']:.3f} ({variants_info})"
                )

        except Exception as e:
            logger.warning(f"Evaluation failed: {e}")
            logger.info(
                "Generated sentences were saved successfully, but evaluation could not be completed."
            )
    elif args.no_eval:
        logger.info("Skipping evaluation (--no-eval flag set)")
    elif score_and_save is None:
        logger.info("Skipping evaluation (sentence_bench module not available)")

    logger.success(f"Completed batch processing {len(pmcids_and_variants)} PMCID(s)")


if __name__ == "__main__":
    main()
