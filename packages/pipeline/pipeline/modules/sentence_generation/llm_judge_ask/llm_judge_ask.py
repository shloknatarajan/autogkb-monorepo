"""
LLM Judge Ask - Generate sentences for a single article's variants and save output.

(See llm_judge_ask.md for more details)

This script automates the process of generating sentences using an LLM for given
PMCID variants, saving the output, and optionally evaluating the generated
sentences against ground truth.

Example Commands:

1. Run with default model (gpt-5) and prompt (v3) for one PMCID:
   python llm_judge_ask.py

2. Specify a different model and prompt, and process 1 PMCID:
   python llm_judge_ask.py --model gpt-4o-mini --prompt v3 --num-pmcids 1

3. Run with v4 prompt (includes sentence + explanation):
   python llm_judge_ask.py --model gpt-4o-mini --prompt v4 --num-pmcids 1

4. Run without automatic evaluation:
   python llm_judge_ask.py --no-eval

5. Specify a different judge model for evaluation:
   python llm_judge_ask.py --model claude-sonnet-4-20250514 --prompt v1 --judge-model claude-3-haiku-2024030

Prompts:
- v3: Dual format (rsID + genotype) - recommended for most uses
- v4: Dual format with explanation - includes brief evidence explanation (not evaluated by sentence_bench)
- v1, v2: Kept for reference
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

# Import centralized LLM utilities
from shared.utils import call_llm

# Import sentence bench for evaluation
try:
    from benchmark.v2.sentence_bench import score_and_save
except ImportError:
    score_and_save = None

VARIANT_BENCH_PATH = ROOT / "data" / "benchmark_v2" / "variant_bench.jsonl"
PROMPTS_FILE = "prompts.yaml"
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


def split_sentences(text: str) -> list[str]:
    """Split model output into a list of sentences.

    Handles either newline-separated or standard sentence punctuation.
    """
    # If output has newlines, treat each non-empty line as a sentence
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) > 1:
        return lines
    # Otherwise, split by sentence-ending punctuation.
    # Keep the delimiter by using a regex split with capture then rejoin.
    parts = re.split(r"([.!?])\s+", text.strip())
    sentences: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        sentences.append((parts[i] + parts[i + 1]).strip())
    # If there is a trailing fragment without punctuation
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1].strip())
    return [s for s in sentences if s]


def parse_sentence_with_explanation(text: str) -> dict[str, str]:
    """Parse output in 'SENTENCE: ... EXPLANATION: ...' format.

    Returns a dict with 'sentence' and 'explanation' keys.
    If format is not matched, treats entire text as sentence with empty explanation.
    """
    # Try to match the SENTENCE: ... EXPLANATION: ... format
    sentence_match = re.search(
        r"SENTENCE:\s*(.+?)(?=EXPLANATION:|$)", text, re.DOTALL | re.IGNORECASE
    )
    explanation_match = re.search(
        r"EXPLANATION:\s*(.+?)$", text, re.DOTALL | re.IGNORECASE
    )

    if sentence_match:
        sentence = sentence_match.group(1).strip()
        explanation = explanation_match.group(1).strip() if explanation_match else ""
        return {"sentence": sentence, "explanation": explanation}
    else:
        # Fallback: treat entire text as sentence if format not matched
        logger.warning(
            "Could not parse SENTENCE/EXPLANATION format, treating as plain sentence"
        )
        return {"sentence": text.strip(), "explanation": ""}


def process_pmcid(
    pmcid: str,
    variants: list[str],
    model: str,
    prompt_cfg: dict,
    prompt_name: str,
    judge_model: str,
    no_eval: bool,
) -> None:
    """Process a single PMCID: generate sentences and optionally evaluate."""
    logger.info(f"Processing PMCID: {pmcid} with {len(variants)} variant(s)")

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

    result: dict[str, dict[str, list[str] | list[dict[str, str]]]] = {pmcid: {}}

    for variant in variants:
        logger.debug(f"Processing variant: {variant}")
        user_prompt = prompt_cfg["user"].format(
            variant=variant, article_text=article_text
        )
        system_prompt = prompt_cfg["system"]

        try:
            output = call_llm(model, system_prompt, user_prompt)
        except Exception as e:
            output = ""
            logger.error(f"Error generating for {pmcid}/{variant}: {e}")

        if use_explanations:
            # For v4: parse sentence + explanation
            parsed = (
                parse_sentence_with_explanation(output)
                if output
                else {"sentence": "", "explanation": ""}
            )
            result[pmcid][variant] = [parsed]  # Store as list of dicts for consistency
            preview = parsed["sentence"] if parsed["sentence"] else "<no output>"
        else:
            # For v1-v3: use original sentence splitting
            sentences = split_sentences(output) if output else []
            result[pmcid][variant] = sentences
            preview = sentences[0] if sentences else "<no output>"

        logger.info(f"✓ {variant}: {preview[:90]}{'...' if len(preview) > 90 else ''}")

    # Save output file
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "_").replace(":", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUTS_DIR / f"{safe_model}_{prompt_name}_{timestamp}.json"

    logger.debug(f"Saving generated sentences to {out_path}")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    logger.success(f"Saved generated sentences to {out_path}")

    # Evaluate generated sentences against ground truth
    if not no_eval and score_and_save is not None:
        logger.info("Evaluating sentences against ground truth")
        try:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            safe_judge_model = judge_model.replace("/", "_").replace(":", "_")
            eval_path = (
                RESULTS_DIR / f"sentence_scores_llm_{safe_judge_model}_{timestamp}.json"
            )

            logger.debug(f"Running evaluation with judge model: {judge_model}")
            eval_result = score_and_save(
                generated_sentences_path=out_path,
                method="llm",
                model=judge_model,
                output_path=eval_path,
            )

            logger.info("Evaluation Summary")
            logger.info(f"Overall Average Score: {eval_result.overall_avg_score:.3f}")
            logger.info(f"Number of PMCIDs: {eval_result.num_pmcids}")
            logger.info("Per-PMCID Scores:")
            for pmcid_result in eval_result.per_pmcid:
                logger.info(
                    f"  {pmcid_result['pmcid']}: {pmcid_result['avg_score']:.3f} ({pmcid_result['num_variants']} variants)"
                )

        except Exception as e:
            logger.warning(f"Evaluation failed: {e}")
            logger.info(
                "Generated sentences were saved successfully, but evaluation could not be completed."
            )
    elif no_eval:
        logger.info("Skipping evaluation (--no-eval flag set)")
    elif score_and_save is None:
        logger.info("Skipping evaluation (sentence_bench module not available)")


def main():
    parser = argparse.ArgumentParser(
        description=("Generate association sentences for PMCID variants and save JSON.")
    )
    parser.add_argument(
        "--model",
        default="gpt-5",
        help="Model name for litellm (default: gpt-5)",
    )
    parser.add_argument(
        "--prompt",
        default="v3",
        help="Prompt key from prompts.yaml (e.g., v1, v2, v3)",
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
    logger.info(f"Processing {len(pmcids_and_variants)} PMCID(s)")

    # Process each PMCID
    for pmcid, variants in pmcids_and_variants:
        process_pmcid(
            pmcid=pmcid,
            variants=variants,
            model=args.model,
            prompt_cfg=prompt_cfg,
            prompt_name=args.prompt,
            judge_model=args.judge_model,
            no_eval=args.no_eval,
        )

    logger.success(f"Completed processing {len(pmcids_and_variants)} PMCID(s)")


if __name__ == "__main__":
    main()
