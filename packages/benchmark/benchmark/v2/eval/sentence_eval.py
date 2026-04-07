"""
Evaluation wrapper for sentence generation experiments.

Wraps sentence_bench.score_and_save() with convenience functions.
"""

import json
from pathlib import Path

from benchmark.v2.sentence_bench import score_and_save, SentenceBenchResult


RESULTS_DIR = Path(__file__).parent / "results"


def evaluate_from_file(
    sentences_path: str | Path,
    judge_model: str = "claude-sonnet-4-20250514",
    save_results: bool = True,
) -> SentenceBenchResult:
    """Evaluate generated sentences from a saved output file.

    The file should contain a SentenceGenerationOutput-compatible JSON with
    a "sentences" key mapping pmcid -> variant -> [sentence dicts].

    Args:
        sentences_path: Path to a sentences.json output file.
        judge_model: LLM model to use for judging.
        save_results: Whether to save evaluation results.

    Returns:
        SentenceBenchResult with detailed scoring.
    """
    sentences_path = Path(sentences_path)

    with open(sentences_path) as f:
        data = json.load(f)

    # The output format wraps sentences under a "sentences" key with metadata.
    # sentence_bench expects the flat format: {pmcid: {variant: [sentences]}}
    if "sentences" in data:
        flat_sentences = data["sentences"]
        run_name = data.get("run_name", "unknown")
    else:
        # Assume it's already in flat format (legacy compatibility)
        flat_sentences = data
        run_name = "unknown"

    # Write a temporary flat file for score_and_save
    tmp_path = sentences_path.parent / f".eval_tmp_{sentences_path.name}"
    with open(tmp_path, "w") as f:
        json.dump(flat_sentences, f, indent=2)

    try:
        output_path = None
        if save_results:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            output_path = RESULTS_DIR / f"{run_name}_eval.json"

        result = score_and_save(
            generated_sentences_path=tmp_path,
            method="llm",
            model=judge_model,
            output_path=output_path,
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    return result
