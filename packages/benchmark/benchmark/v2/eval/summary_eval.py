"""
Evaluation wrapper for summary generation experiments.

Evaluates generated summaries against structured ground truth from summary_bench.jsonl.
Uses an LLM judge to check whether the summary covers the key facts.
"""

import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from loguru import logger

from shared.utils import call_llm

RESULTS_DIR = Path(__file__).parent / "results"
SUMMARY_BENCH_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "benchmark_v2"
    / "summary_bench.jsonl"
)

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator of pharmacogenomic research summaries.

Your task is to evaluate whether a generated summary accurately covers the key pharmacogenomic findings from a research article, given a structured checklist of ground truth facts."""

JUDGE_USER_PROMPT_TEMPLATE = """Evaluate the following generated summary for article {pmcid} ("{title}").

GROUND TRUTH FACTS (structured checklist):
{facts_checklist}

GENERATED SUMMARY:
{summary}

INSTRUCTIONS:
For each ground truth association, determine if the summary:
1. Mentions the correct variant and gene
2. States the correct direction of effect (increased/decreased/not associated)
3. Identifies the correct drug(s)
4. Mentions the phenotype/condition
5. Includes statistical evidence (p-values, effect sizes) when available

Score the summary on a 0-1 scale:
- 1.0: All key associations are accurately covered with supporting evidence
- 0.7-0.9: Most associations covered, minor omissions
- 0.4-0.6: Some associations covered but missing key details
- 0.1-0.3: Few associations covered or significant inaccuracies
- 0.0: No relevant content or completely wrong

Respond in this exact JSON format:
{{"score": <float 0-1>, "associations_covered": <int>, "associations_total": <int>, "explanation": "<brief explanation>", "per_association": [<list of {{"variant": "str", "covered": true/false, "notes": "str"}}>]}}"""


def load_summary_bench() -> dict[str, dict]:
    """Load summary_bench.jsonl -> {pmcid: record}."""
    data = {}
    with open(SUMMARY_BENCH_PATH) as f:
        for line in f:
            rec = json.loads(line)
            data[rec["pmcid"]] = rec
    return data


def format_facts_checklist(bench_record: dict) -> str:
    """Format a summary bench record into a readable facts checklist."""
    parts = []
    for i, assoc in enumerate(bench_record["associations"], 1):
        lines = [f"Association {i}:"]
        lines.append(
            f"  Variant: {assoc['variant']} (Gene: {assoc.get('gene', 'N/A')})"
        )
        lines.append(f"  Drug(s): {assoc.get('drug', 'N/A')}")
        lines.append(f"  Association: {assoc.get('is_associated', 'N/A')}")
        lines.append(f"  Direction: {assoc.get('direction', 'N/A')}")
        lines.append(f"  Category: {assoc.get('phenotype_category', 'N/A')}")
        lines.append(f"  Significance: {assoc.get('significance', 'N/A')}")

        if assoc.get("alleles"):
            lines.append(
                f"  Alleles: {assoc['alleles']} vs {assoc.get('comparison', 'N/A')}"
            )
        if assoc.get("population"):
            lines.append(f"  Population: {assoc['population']}")
        if assoc.get("phenotype"):
            lines.append(f"  Phenotype: {assoc['phenotype']}")

        # Study parameters
        for sp in assoc.get("study_parameters", []):
            sp_parts = []
            if sp.get("p_value"):
                sp_parts.append(f"p {sp['p_value']}")
            if sp.get("study_cases"):
                sp_parts.append(f"n={int(sp['study_cases'])}")
            if sp.get("ratio_stat"):
                sp_parts.append(f"{sp.get('ratio_stat_type', 'OR')}={sp['ratio_stat']}")
            if sp_parts:
                lines.append(f"  Evidence: {', '.join(sp_parts)}")

        lines.append(f"  Ground truth sentence: {assoc.get('sentence', 'N/A')}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def evaluate_summary(
    pmcid: str,
    summary: str,
    bench_record: dict,
    judge_model: str,
) -> dict[str, Any]:
    """Evaluate a single summary against ground truth facts.

    Args:
        pmcid: Article identifier.
        summary: Generated summary text.
        bench_record: Ground truth record from summary_bench.
        judge_model: Model to use for judging.

    Returns:
        Dict with score, explanation, and per-association details.
    """
    facts_checklist = format_facts_checklist(bench_record)

    user_prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
        pmcid=pmcid,
        title=bench_record.get("title", ""),
        facts_checklist=facts_checklist,
        summary=summary,
    )

    try:
        output = call_llm(judge_model, JUDGE_SYSTEM_PROMPT, user_prompt, temperature=0)
        result = json.loads(output)
        return {
            "pmcid": pmcid,
            "score": float(result["score"]),
            "associations_covered": result.get("associations_covered", 0),
            "associations_total": result.get("associations_total", 0),
            "explanation": result.get("explanation", ""),
            "per_association": result.get("per_association", []),
        }
    except Exception as e:
        logger.error(f"Error evaluating summary for {pmcid}: {e}")
        return {
            "pmcid": pmcid,
            "score": 0.0,
            "associations_covered": 0,
            "associations_total": len(bench_record.get("associations", [])),
            "explanation": f"Error during evaluation: {e}",
            "per_association": [],
        }


def evaluate_from_file(
    summaries_path: str | Path,
    judge_model: str = "claude-sonnet-4-20250514",
    save_results: bool = True,
) -> dict[str, Any]:
    """Evaluate summaries from a saved output file against summary_bench.

    Args:
        summaries_path: Path to a summary.json output file.
        judge_model: LLM model to use for judging.
        save_results: Whether to save evaluation results.

    Returns:
        Dictionary with evaluation summary.
    """
    summaries_path = Path(summaries_path)

    with open(summaries_path) as f:
        data = json.load(f)

    summaries = data.get("summaries", [])
    run_name = data.get("run_name", "unknown")

    bench_data = load_summary_bench()

    all_results = []
    all_scores = []

    for summary_entry in summaries:
        pmcid = summary_entry["pmcid"]
        summary_text = summary_entry["summary"]

        if pmcid not in bench_data:
            logger.warning(f"No ground truth for {pmcid}, skipping")
            continue

        result = evaluate_summary(pmcid, summary_text, bench_data[pmcid], judge_model)
        all_results.append(result)
        all_scores.append(result["score"])
        logger.info(
            f"  {pmcid}: {result['score']:.2f} ({result['associations_covered']}/{result['associations_total']} associations)"
        )

    overall_avg = sum(all_scores) / len(all_scores) if all_scores else 0.0

    output = {
        "judge_model": judge_model,
        "source_file": str(summaries_path),
        "overall_avg_score": overall_avg,
        "num_articles": len(all_results),
        "per_article": all_results,
    }

    if save_results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = RESULTS_DIR / f"{run_name}_eval.json"
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
        logger.info(f"Saved evaluation results to {output_path}")

    return output
