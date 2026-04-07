"""
Unified CLI runner for citation finding experiments.

Usage:
    # Find citations from a sentences file
    python -m src.modules.citations.run \\
      --method one_shot_citations \\
      --sentences-file outputs/.../sentences.json \\
      --model gpt-4o --prompt v1

    # Find + evaluate
    python -m src.modules.citations.run \\
      --method one_shot_citations \\
      --sentences-file outputs/.../sentences.json \\
      --eval

    # Evaluate from existing output
    python -m src.modules.citations.run \\
      --eval outputs/.../citations.json
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from pipeline.modules.citations.models import (
    Citation,
    CitationFinderOutput,
    SentenceInput,
)
from pipeline.modules.citations.citation_finder import CitationFinder
from benchmark.v2.eval.citation_eval import evaluate_from_file

OUTPUTS_DIR = Path(__file__).parent / "outputs"


def generate_run_name(method: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{method}_{timestamp}"


def find_citations(
    finder: CitationFinder,
    sentences_input: SentenceInput,
    sentences_file: str,
    run_name: str,
    model: str,
    prompt_version: str,
    max_articles: int | None = None,
) -> Path:
    """Run citation finding and save output to outputs/<run_name>/citations.json."""
    output_dir = OUTPUTS_DIR / run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    pmcids = list(sentences_input.sentences.keys())
    if max_articles:
        pmcids = pmcids[:max_articles]

    print(f"\nRunning {finder.name} on {len(pmcids)} articles\n")

    all_citations: dict[str, list[Citation]] = {}

    for pmcid in pmcids:
        variant_sentences = sentences_input.sentences[pmcid]

        # Flatten variant -> sentences into a list of associations
        associations: list[dict] = []
        for variant, sents in variant_sentences.items():
            for sent in sents:
                associations.append(
                    {
                        "variant": variant,
                        "sentence": sent.sentence,
                        "explanation": sent.explanation,
                    }
                )

        print(f"  {pmcid}: {len(associations)} associations")
        try:
            result = finder.find_citations(pmcid, associations)
            all_citations[pmcid] = result
            cited_count = sum(1 for c in result if c.citations)
            print(f"    -> {cited_count}/{len(result)} associations have citations")
        except Exception as e:
            print(f"    ! Error: {e}")

    output = CitationFinderOutput(
        finder=finder.name,
        run_name=run_name,
        timestamp=datetime.now().isoformat(),
        source_sentences=sentences_file,
        model=model,
        prompt_version=prompt_version,
        citations={pmcid: cites for pmcid, cites in all_citations.items()},
    )

    output_path = output_dir / "citations.json"
    with open(output_path, "w") as f:
        json.dump(output.model_dump(), f, indent=2)

    print(f"\nCitations saved to {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Run citation finding experiments")
    parser.add_argument(
        "--method",
        choices=["one_shot_citations"],
        help="Citation finding method to use",
    )
    parser.add_argument(
        "--sentences-file",
        type=str,
        default=None,
        help="Path to a sentences.json file (from sentence generation output)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LLM model to use for citation finding",
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
        help="Evaluate results. Pass a citations.json path to evaluate from file, "
        "or use with --method to find and evaluate in one step.",
    )

    args = parser.parse_args()

    # Mode 1: Evaluate from an existing citations file
    if isinstance(args.eval, str):
        citations_path = Path(args.eval)
        if not citations_path.exists():
            parser.error(f"Citations file not found: {citations_path}")
        evaluate_from_file(citations_path, judge_model=args.judge_model)
        return

    # For finding modes, --method and --sentences-file are required
    if args.method is None:
        parser.error(
            "--method is required for finding (or pass --eval <path> to evaluate)"
        )
    if args.sentences_file is None:
        parser.error("--sentences-file is required for finding")

    sentences_path = Path(args.sentences_file)
    if not sentences_path.exists():
        parser.error(f"Sentences file not found: {sentences_path}")

    # Load and validate sentences input
    with open(sentences_path) as f:
        raw_data = json.load(f)
    sentences_input = SentenceInput(**raw_data)

    # Build kwargs for the finder
    kwargs = {}
    if args.model is not None:
        kwargs["model"] = args.model
    if args.prompt is not None:
        kwargs["prompt_version"] = args.prompt

    finder = CitationFinder(args.method, **kwargs)
    run_name = generate_run_name(args.method)

    # Mode 2: Find only
    citations_path = find_citations(
        finder=finder,
        sentences_input=sentences_input,
        sentences_file=args.sentences_file,
        run_name=run_name,
        model=args.model or "default",
        prompt_version=args.prompt or "default",
        max_articles=args.max_articles,
    )

    # Mode 3: Find and evaluate
    if args.eval is True:
        evaluate_from_file(citations_path, judge_model=args.judge_model)


if __name__ == "__main__":
    main()
