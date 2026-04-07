"""
Unified CLI runner for sentence generation experiments.

Usage:
    # Generate sentences from a variants file
    python -m src.modules.sentence_generation.run \\
      --method batch_judge_ask \\
      --variants-file outputs/regex_v5_.../variants.json \\
      --model gpt-5 --prompt v3

    # Generate + evaluate
    python -m src.modules.sentence_generation.run \\
      --method batch_judge_ask \\
      --variants-file outputs/.../variants.json \\
      --eval

    # Evaluate from existing output
    python -m src.modules.sentence_generation.run \\
      --eval outputs/batch_judge_ask_.../sentences.json
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from generation.modules.sentence_generation.models import (
    GeneratedSentence,
    SentenceGenerationOutput,
    VariantInput,
)
from generation.modules.sentence_generation.sentence_generator import SentenceGenerator
from benchmark.v2.eval.sentence_eval import evaluate_from_file

OUTPUTS_DIR = Path(__file__).parent / "outputs"


def generate_run_name(method: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{method}_{timestamp}"


def generate_sentences(
    generator: SentenceGenerator,
    variants_input: VariantInput,
    variants_file: str,
    run_name: str,
    model: str,
    prompt_version: str,
    max_articles: int | None = None,
) -> Path:
    """Run generation and save output to outputs/<run_name>/sentences.json."""
    output_dir = OUTPUTS_DIR / run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    pmcids = list(variants_input.variants.keys())
    if max_articles:
        pmcids = pmcids[:max_articles]

    print(f"\nRunning {generator.name} on {len(pmcids)} articles\n")

    all_sentences: dict[str, dict[str, list[GeneratedSentence]]] = {}

    for pmcid in pmcids:
        variants = variants_input.variants[pmcid]
        print(f"  {pmcid}: {len(variants)} variants")
        try:
            result = generator.generate(pmcid, variants)
            all_sentences[pmcid] = result
            generated_count = sum(len(sents) for sents in result.values())
            print(f"    -> generated {generated_count} sentences")
        except Exception as e:
            print(f"    ! Error: {e}")

    output = SentenceGenerationOutput(
        generator=generator.name,
        run_name=run_name,
        timestamp=datetime.now().isoformat(),
        source_variants=variants_file,
        model=model,
        prompt_version=prompt_version,
        sentences={
            pmcid: {variant: sents for variant, sents in variant_results.items()}
            for pmcid, variant_results in all_sentences.items()
        },
    )

    output_path = output_dir / "sentences.json"
    with open(output_path, "w") as f:
        json.dump(output.model_dump(), f, indent=2)

    print(f"\nSentences saved to {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Run sentence generation experiments")
    parser.add_argument(
        "--method",
        choices=["raw_sentence_ask", "batch_judge_ask", "llm_judge_ask"],
        help="Generation method to use",
    )
    parser.add_argument(
        "--variants-file",
        type=str,
        default=None,
        help="Path to a variants.json file (from variant extraction output)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LLM model to use for generation",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Prompt version (e.g., v3, v4, v5)",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Maximum number of articles to process",
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default="claude-sonnet-4-20250514",
        help="Model to use for evaluation judging",
    )
    parser.add_argument(
        "--eval",
        nargs="?",
        const=True,
        default=False,
        help="Evaluate results. Pass a sentences.json path to evaluate from file, "
        "or use with --method to generate and evaluate in one step.",
    )

    args = parser.parse_args()

    # Mode 1: Evaluate from an existing sentences file
    if isinstance(args.eval, str):
        sentences_path = Path(args.eval)
        if not sentences_path.exists():
            parser.error(f"Sentences file not found: {sentences_path}")
        evaluate_from_file(sentences_path, judge_model=args.judge_model)
        return

    # For generation modes, --method and --variants-file are required
    if args.method is None:
        parser.error(
            "--method is required for generation (or pass --eval <path> to evaluate)"
        )
    if args.variants_file is None:
        parser.error("--variants-file is required for generation")

    variants_path = Path(args.variants_file)
    if not variants_path.exists():
        parser.error(f"Variants file not found: {variants_path}")

    # Load and validate variants input
    with open(variants_path) as f:
        raw_data = json.load(f)
    variants_input = VariantInput(**raw_data)

    # Build kwargs for the generator
    kwargs = {}
    if args.model is not None:
        kwargs["model"] = args.model
    if args.prompt is not None:
        kwargs["prompt_version"] = args.prompt

    generator = SentenceGenerator(args.method, **kwargs)
    run_name = generate_run_name(args.method)

    # Mode 2: Generate only
    sentences_path = generate_sentences(
        generator=generator,
        variants_input=variants_input,
        variants_file=args.variants_file,
        run_name=run_name,
        model=args.model or "default",
        prompt_version=args.prompt or "default",
        max_articles=args.max_articles,
    )

    # Mode 3: Generate and evaluate
    if args.eval is True:
        evaluate_from_file(sentences_path, judge_model=args.judge_model)


if __name__ == "__main__":
    main()
