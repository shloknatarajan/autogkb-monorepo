"""
Batch Judge Ask - Generate sentences for all variants in a PMCID in one LLM call.

Batches all variants together into a single prompt, parses structured output.
"""

from pathlib import Path

from loguru import logger

from shared.utils import call_llm, get_methods_and_results_text, get_markdown_text
from pipeline.modules.sentence_generation.models import GeneratedSentence
from pipeline.modules.sentence_generation.utils import (
    load_prompts,
    parse_batch_output,
)

PROMPTS_FILE = Path(__file__).parent / "prompts" / "batch_judge_ask.yaml"


def batch_judge_ask_generate(
    pmcid: str,
    variants: list[str],
    *,
    model: str = "gpt-5",
    prompt_version: str = "v3",
) -> dict[str, list[GeneratedSentence]]:
    """Generate sentences for all variants in a single LLM call.

    Args:
        pmcid: Article PMC identifier.
        variants: List of variant strings to generate sentences for.
        model: LLM model to use.
        prompt_version: Which prompt version from prompts.yaml.

    Returns:
        Dict mapping variant -> list of GeneratedSentence.
    """
    prompts = load_prompts(PROMPTS_FILE)
    prompt_config = prompts[prompt_version]

    article_text = get_methods_and_results_text(pmcid)
    if not article_text:
        article_text = get_markdown_text(pmcid)
    if not article_text:
        logger.warning(f"No article text found for {pmcid}")
        return {v: [] for v in variants}

    use_explanations = prompt_version in ("v4", "v5", "v6")

    variants_list = "\n".join([f"- {variant}" for variant in variants])
    user_prompt = prompt_config["user"].format(
        variants_list=variants_list, article_text=article_text
    )
    system_prompt = prompt_config["system"]

    try:
        output = call_llm(model, system_prompt, user_prompt)
    except Exception as e:
        logger.error(f"Error generating batch for {pmcid}: {e}")
        output = ""

    parsed = parse_batch_output(output, use_explanations) if output else {}

    result: dict[str, list[GeneratedSentence]] = {}
    for variant in variants:
        if variant in parsed:
            result[variant] = [GeneratedSentence(**entry) for entry in parsed[variant]]
        else:
            result[variant] = []
            logger.warning(f"  {variant}: not found in batch output")

    return result
