"""
Unified CLI runner for variant extraction experiments.

Usage:
    # Extract variants (saves to outputs/<run_name>/variants.json)
    python -m src.modules.variant_finding.run --method regex_v5
    python -m src.modules.variant_finding.run --method just_ask --model claude-opus-4-5-20251101 --prompt v3
    python -m src.modules.variant_finding.run --method regex_llm_filter --model gpt-4o --prompt v1 --max-articles 5

    # Evaluate from saved variants file (saves to results/<run_name>.json)
    python -m src.modules.variant_finding.run --eval outputs/regex_v5_20250601_120000/variants.json

    # Extract and evaluate in one step
    python -m src.modules.variant_finding.run --method regex_v5 --eval
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

from benchmark.v2.variant_bench import load_variant_bench_data
from generation.modules.variant_finding.variant_extractor import VariantExtractor
from benchmark.v2.eval.variant_eval import evaluate_from_file

OUTPUTS_DIR = Path(__file__).parent / "outputs"


def generate_run_name(method: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{method}_{timestamp}"


def extract_variants(extractor, pmcids: list[str], run_name: str) -> Path:
    """Run extraction and save intermediate variants to outputs/<run_name>/variants.json."""
    output_dir = OUTPUTS_DIR / run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nRunning {extractor.name} on {len(pmcids)} articles\n")

    variants = {}
    for pmcid in pmcids:
        try:
            extracted = extractor.get_variants(pmcid)
            variants[pmcid] = extracted
            print(f"  {pmcid}: found {len(extracted)} variants")
        except Exception as e:
            print(f"  ! {pmcid}: Error - {e}")

    output_path = output_dir / "variants.json"
    payload = {
        "extractor": extractor.name,
        "run_name": run_name,
        "timestamp": datetime.now().isoformat(),
        "variants": variants,
    }
    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"\nVariants saved to {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Run variant extraction experiments")
    parser.add_argument(
        "--method",
        choices=[
            "just_ask",
            "regex_v1",
            "regex_v2",
            "regex_v3",
            "regex_v4",
            "regex_v5",
            "regex_llm_filter",
            "regex_term_norm",
            "pubtator",
        ],
        help="Extraction method to use",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LLM model (for just_ask and regex_llm_filter)",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Prompt version (for just_ask and regex_llm_filter)",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Maximum number of articles to process",
    )
    parser.add_argument(
        "--eval",
        nargs="?",
        const=True,
        default=False,
        help="Evaluate results. Pass a variants.json path to evaluate from file, "
        "or use with --method to extract and evaluate in one step.",
    )

    args = parser.parse_args()

    # Mode 1: Evaluate from an existing variants file
    if isinstance(args.eval, str):
        variants_path = Path(args.eval)
        if not variants_path.exists():
            parser.error(f"Variants file not found: {variants_path}")
        evaluate_from_file(variants_path)
        return

    # For extraction modes, --method is required
    if args.method is None:
        parser.error(
            "--method is required for extraction (or pass --eval <path> to evaluate)"
        )

    # Build kwargs for the extractor
    kwargs = {}
    if args.model is not None:
        kwargs["model"] = args.model
    if args.prompt is not None:
        kwargs["prompt_version"] = args.prompt

    extractor = VariantExtractor(args.method, **kwargs)
    run_name = generate_run_name(args.method)

    benchmark_data = load_variant_bench_data()
    pmcids = list(benchmark_data.keys())
    if args.max_articles:
        pmcids = pmcids[: args.max_articles]

    # Mode 2: Extract only
    variants_path = extract_variants(extractor, pmcids, run_name)

    # Mode 3: Extract and evaluate
    if args.eval is True:
        evaluate_from_file(variants_path)


if __name__ == "__main__":
    main()
