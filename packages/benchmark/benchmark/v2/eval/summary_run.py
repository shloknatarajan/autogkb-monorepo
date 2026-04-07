"""
Unified CLI runner for summary generation experiments.

Usage:
    # Generate summaries from a sentences file
    python -m src.modules.summary.run \\
      --method basic_summary \\
      --sentences-file outputs/.../sentences.json \\
      --model gpt-5 --prompt v1

    # With citations
    python -m src.modules.summary.run \\
      --method basic_summary \\
      --sentences-file outputs/.../sentences.json \\
      --citations-file outputs/.../citations.json

    # Max articles
    python -m src.modules.summary.run \\
      --method basic_summary \\
      --sentences-file outputs/.../sentences.json \\
      --max-articles 3
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from pipeline.modules.summary.models import (
    ArticleSummary,
    CitationInput,
    SentenceInput,
    SummaryGeneratorOutput,
)
from pipeline.modules.summary.summary_generator import SummaryGenerator
from benchmark.v2.eval.summary_eval import evaluate_from_file

OUTPUTS_DIR = Path(__file__).parent / "outputs"


def generate_run_name(method: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{method}_{timestamp}"


def generate_summaries(
    generator: SummaryGenerator,
    sentences_input: SentenceInput,
    sentences_file: str,
    citations_input: CitationInput | None,
    citations_file: str | None,
    run_name: str,
    model: str,
    prompt_version: str,
    max_articles: int | None = None,
) -> Path:
    """Run summary generation and save output to outputs/<run_name>/summary.json."""
    output_dir = OUTPUTS_DIR / run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    pmcids = list(sentences_input.sentences.keys())
    if max_articles:
        pmcids = pmcids[:max_articles]

    # Build citations lookup if available
    citations_data: dict[str, list[dict]] | None = None
    if citations_input is not None:
        citations_data = citations_input.citations

    print(f"\nRunning {generator.name} on {len(pmcids)} articles\n")

    all_summaries: list[ArticleSummary] = []

    for pmcid in pmcids:
        variant_sentences = sentences_input.sentences[pmcid]

        # Build variants_data: list of {variant, sentences: [str]}
        variants_data: list[dict] = []
        for variant, sents in variant_sentences.items():
            variants_data.append(
                {
                    "variant": variant,
                    "sentences": [s.sentence for s in sents],
                }
            )

        print(f"  {pmcid}: {len(variants_data)} variants")
        try:
            result = generator.generate(pmcid, variants_data, citations_data)
            all_summaries.append(result)
            print(f"    -> summary generated ({len(result.summary)} chars)")
        except Exception as e:
            print(f"    ! Error: {e}")

    output = SummaryGeneratorOutput(
        generator=generator.name,
        run_name=run_name,
        timestamp=datetime.now().isoformat(),
        source_sentences=sentences_file,
        source_citations=citations_file,
        model=model,
        prompt_version=prompt_version,
        summaries=all_summaries,
    )

    output_path = output_dir / "summary.json"
    with open(output_path, "w") as f:
        json.dump(output.model_dump(), f, indent=2)

    print(f"\nSummaries saved to {output_path}")

    # Print sample summary
    if all_summaries:
        print(f"\n{'=' * 60}")
        print(f"PMCID: {all_summaries[0].pmcid}")
        print(f"Variants: {', '.join(all_summaries[0].variants[:5])}")
        if len(all_summaries[0].variants) > 5:
            print(f"  ... and {len(all_summaries[0].variants) - 5} more")
        print(f"{'=' * 60}")
        print(all_summaries[0].summary)
        print(f"{'=' * 60}\n")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Run summary generation experiments")
    parser.add_argument(
        "--method",
        choices=["basic_summary"],
        help="Summary generation method to use",
    )
    parser.add_argument(
        "--sentences-file",
        type=str,
        default=None,
        help="Path to a sentences.json file (from sentence generation output)",
    )
    parser.add_argument(
        "--citations-file",
        type=str,
        default=None,
        help="Path to a citations.json file (from citation finding output, optional)",
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
        help="Prompt version (e.g., v1, v2)",
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
        help="Evaluate results against summary_bench. Pass a summary.json path "
        "to evaluate from file, or use with --method to generate and evaluate.",
    )

    args = parser.parse_args()

    # Mode 1: Evaluate from an existing summary file
    if isinstance(args.eval, str):
        summary_path = Path(args.eval)
        if not summary_path.exists():
            parser.error(f"Summary file not found: {summary_path}")
        evaluate_from_file(summary_path, judge_model=args.judge_model)
        return

    if args.method is None:
        parser.error("--method is required (or pass --eval <path> to evaluate)")
    if args.sentences_file is None:
        parser.error("--sentences-file is required for generation")

    sentences_path = Path(args.sentences_file)
    if not sentences_path.exists():
        parser.error(f"Sentences file not found: {sentences_path}")

    # Load and validate sentences input
    with open(sentences_path) as f:
        raw_data = json.load(f)
    sentences_input = SentenceInput(**raw_data)

    # Load citations input if provided
    citations_input = None
    if args.citations_file:
        citations_path = Path(args.citations_file)
        if not citations_path.exists():
            parser.error(f"Citations file not found: {citations_path}")
        with open(citations_path) as f:
            raw_citations = json.load(f)
        citations_input = CitationInput(**raw_citations)

    # Build kwargs for the generator
    kwargs = {}
    if args.model is not None:
        kwargs["model"] = args.model
    if args.prompt is not None:
        kwargs["prompt_version"] = args.prompt

    generator = SummaryGenerator(args.method, **kwargs)
    run_name = generate_run_name(args.method)

    summary_path = generate_summaries(
        generator=generator,
        sentences_input=sentences_input,
        sentences_file=args.sentences_file,
        citations_input=citations_input,
        citations_file=args.citations_file,
        run_name=run_name,
        model=args.model or "default",
        prompt_version=args.prompt or "default",
        max_articles=args.max_articles,
    )

    # Mode 3: Generate and evaluate
    if args.eval is True:
        evaluate_from_file(summary_path, judge_model=args.judge_model)


if __name__ == "__main__":
    main()
