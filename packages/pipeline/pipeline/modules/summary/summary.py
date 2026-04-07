"""
Summary Generation - Generate concise summaries of pharmacogenomic findings.

(See summary.md for more details)

This script takes article text, variants, associations, and citations to generate
summaries of the pharmacogenomic findings for each article.

Example Commands:

1. Run with default model (gpt-5) and prompt (v1) for one PMCID:
   python summary.py

2. Specify a different model and prompt, and process 3 PMCIDs:
   python summary.py --model gpt-4o --prompt v2 --num-pmcids 3

3. Process all PMCIDs:
   python summary.py --num-pmcids 0

4. Use citations from a previous run:
   python summary.py --citations-file outputs/citations_gpt-4o_v1_20240115.json

Prompts:
- v1: Basic summary - Background, Key Findings, Clinical Implications
- v2: Detailed variant-focused summary organized by genetic variant
"""

from __future__ import annotations

import argparse
import json
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

# Paths - summary.py is at src/modules/summary/, so 3 levels up to root
ROOT = Path(__file__).resolve().parents[3]

# Add repository root to Python path to enable imports
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import utils
from shared.utils import call_llm, get_markdown_text

SENTENCE_BENCH_PATH = ROOT / "data" / "benchmark_v2" / "sentence_bench.jsonl"
PROMPTS_FILE = Path(__file__).parent / "summary_prompts.yaml"
OUTPUTS_DIR = Path(__file__).parent / "outputs"


def load_prompts() -> dict:
    """Load prompt configurations from summary_prompts.yaml."""
    logger.debug(f"Loading prompts from {PROMPTS_FILE}")
    with open(PROMPTS_FILE) as f:
        prompts = yaml.safe_load(f)
    logger.info(f"Loaded {len(prompts)} prompt(s)")
    return prompts


def load_sentence_data(num_pmcids: int | None = None) -> dict[str, list[dict]]:
    """Load sentence data from sentence_bench.jsonl grouped by PMCID.

    Args:
        num_pmcids: Optional limit on number of PMCIDs to load. 0 means all.

    Returns:
        Dictionary mapping pmcid -> list of {variant, sentences} dicts.
    """
    logger.debug(f"Loading sentence data from {SENTENCE_BENCH_PATH}")
    pmcid_data: dict[str, list[dict]] = {}

    with open(SENTENCE_BENCH_PATH) as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            pmcid = rec["pmcid"]

            if pmcid not in pmcid_data:
                pmcid_data[pmcid] = []

            # Store variant and its sentences
            sentences = rec.get("sentences", [])
            # Handle both formats: sentences can be list of strings or list of dicts
            sentence_list = []
            for sent in sentences:
                if isinstance(sent, dict):
                    sentence_list.append(sent["sentence"])
                else:
                    sentence_list.append(sent)

            pmcid_data[pmcid].append(
                {
                    "variant": rec["variant"],
                    "sentences": sentence_list,
                }
            )

    # Limit to num_pmcids if specified (0 means all)
    if num_pmcids is not None and num_pmcids > 0:
        pmcid_data = dict(list(pmcid_data.items())[:num_pmcids])

    total_variants = sum(len(v) for v in pmcid_data.values())
    logger.info(f"Loaded {total_variants} variant(s) for {len(pmcid_data)} PMCID(s)")
    return pmcid_data


def load_citations(citations_file: Path | None) -> dict[str, list[dict]] | None:
    """Load citations from a previous citation generation run.

    Args:
        citations_file: Path to citations JSON file (optional)

    Returns:
        Dictionary mapping pmcid -> list of {variant, sentence, citations} dicts,
        or None if no file provided.
    """
    if citations_file is None:
        return None

    if not citations_file.exists():
        logger.warning(f"Citations file not found: {citations_file}")
        return None

    logger.debug(f"Loading citations from {citations_file}")
    with open(citations_file) as f:
        data = json.load(f)

    # Handle the output format from one_shot_citations.py
    citations = data.get("citations", data)
    logger.info(f"Loaded citations for {len(citations)} PMCID(s)")
    return citations


def format_associations(variants_data: list[dict]) -> str:
    """Format variant associations for the prompt.

    Args:
        variants_data: List of {variant, sentences} dicts

    Returns:
        Formatted string of associations
    """
    parts = []
    for i, v in enumerate(variants_data, 1):
        variant = v["variant"]
        sentences = v["sentences"]
        sentences_text = "\n  - ".join(sentences)
        parts.append(f"{i}. {variant}:\n  - {sentences_text}")

    return "\n\n".join(parts)


def format_citations(pmcid: str, citations_data: dict[str, list[dict]] | None) -> str:
    """Format citations for the prompt.

    Args:
        pmcid: PMCID to get citations for
        citations_data: Citations data from load_citations

    Returns:
        Formatted string of citations, or "No citations available"
    """
    if citations_data is None or pmcid not in citations_data:
        return "No citations available"

    pmcid_citations = citations_data[pmcid]
    parts = []
    for entry in pmcid_citations:
        variant = entry.get("variant", "Unknown")
        sentence = entry.get("sentence", "")
        citations = entry.get("citations", [])

        if citations:
            citations_text = "\n    - ".join(citations)
            parts.append(
                f"For '{variant}' ({sentence[:50]}...):\n    - {citations_text}"
            )

    if not parts:
        return "No citations available"

    return "\n\n".join(parts)


def generate_summary(
    pmcid: str,
    variants_data: list[dict],
    citations_data: dict[str, list[dict]] | None,
    model: str,
    prompt_cfg: dict,
) -> dict:
    """Generate a summary for a single PMCID.

    Args:
        pmcid: PMCID identifier
        variants_data: List of {variant, sentences} dicts for this PMCID
        citations_data: Citations data (optional)
        model: Model name for LLM
        prompt_cfg: Prompt configuration from summary_prompts.yaml

    Returns:
        Dictionary with pmcid, summary, and metadata
    """
    logger.info(f"Generating summary for PMCID: {pmcid}")

    # Get article text
    article_text = get_markdown_text(pmcid)
    if not article_text:
        logger.warning(f"No article text found for {pmcid}")
        article_text = "[Article text not available]"

    # Format associations and citations
    associations_text = format_associations(variants_data)
    citations_text = format_citations(pmcid, citations_data)

    # Create the prompt
    user_prompt = prompt_cfg["user"].format(
        article_text=article_text,
        associations=associations_text,
        citations=citations_text,
    )
    system_prompt = prompt_cfg["system"]

    # Call LLM
    try:
        logger.debug(f"Making LLM call for {pmcid}")
        summary = call_llm(model, system_prompt, user_prompt)
        logger.info(f"Generated summary for {pmcid} ({len(summary)} chars)")
    except Exception as e:
        summary = f"[Error generating summary: {e}]"
        logger.error(f"Error generating summary for {pmcid}: {e}")

    return {
        "pmcid": pmcid,
        "summary": summary,
        "num_variants": len(variants_data),
        "variants": [v["variant"] for v in variants_data],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate summaries of pharmacogenomic findings from articles."
    )
    parser.add_argument(
        "--model",
        default="gpt-5",
        help="Model name for litellm (default: gpt-5)",
    )
    parser.add_argument(
        "--prompt",
        default="v1",
        help="Prompt key from summary_prompts.yaml (e.g., v1, v2)",
    )
    parser.add_argument(
        "--num-pmcids",
        type=int,
        default=1,
        help="Number of PMCIDs to process (default: 1, 0 for all)",
    )
    parser.add_argument(
        "--citations-file",
        type=Path,
        default=None,
        help="Path to citations JSON file from one_shot_citations.py (optional)",
    )
    args = parser.parse_args()

    prompts = load_prompts()
    if args.prompt not in prompts:
        raise KeyError(f"Prompt '{args.prompt}' not found in {PROMPTS_FILE}")
    prompt_cfg = prompts[args.prompt]

    # Load data
    pmcid_data = load_sentence_data(args.num_pmcids)
    citations_data = load_citations(args.citations_file)

    logger.info(f"Prompt: {args.prompt} ({prompt_cfg.get('name', '')})")
    logger.info(f"Model: {args.model}")
    logger.info(f"Processing {len(pmcid_data)} PMCID(s)")
    if citations_data:
        logger.info(f"Using citations from: {args.citations_file}")

    # Generate timestamp for this run
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Process each PMCID and collect results
    summaries: list[dict] = []

    for pmcid, variants_data in pmcid_data.items():
        result = generate_summary(
            pmcid=pmcid,
            variants_data=variants_data,
            citations_data=citations_data,
            model=args.model,
            prompt_cfg=prompt_cfg,
        )
        summaries.append(result)

    # Save results
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = args.model.replace("/", "_").replace(":", "_")
    out_path = OUTPUTS_DIR / f"summary_{safe_model}_{args.prompt}_{timestamp}.json"

    # Build output with metadata
    output_data = {
        "metadata": {
            "model": args.model,
            "prompt_name": args.prompt,
            "prompt_description": prompt_cfg.get("name", ""),
            "timestamp": timestamp,
            "num_pmcids": len(summaries),
            "citations_file": str(args.citations_file) if args.citations_file else None,
        },
        "summaries": summaries,
    }

    logger.debug(f"Saving summaries to {out_path}")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    logger.success(f"Saved summaries for {len(summaries)} PMCID(s) to {out_path}")

    # Print a sample summary if any were generated
    if summaries:
        logger.info("Sample summary (first PMCID):")
        print(f"\n{'=' * 60}")
        print(f"PMCID: {summaries[0]['pmcid']}")
        print(f"Variants: {', '.join(summaries[0]['variants'][:5])}")
        if len(summaries[0]["variants"]) > 5:
            print(f"  ... and {len(summaries[0]['variants']) - 5} more")
        print(f"{'=' * 60}")
        print(summaries[0]["summary"])
        print(f"{'=' * 60}\n")

    logger.success(f"Completed processing {len(pmcid_data)} PMCID(s)")


if __name__ == "__main__":
    main()
