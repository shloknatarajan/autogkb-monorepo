"""
LLM Judge Ask - Per-variant sentence generation with simpler prompt.

Processes variants one-by-one, supports both plain sentences and
sentence + explanation formats.
"""

from pathlib import Path

from loguru import logger

from shared.utils import call_llm, get_methods_and_conclusions_text, get_markdown_text
from generation.modules.sentence_generation.models import GeneratedSentence
from generation.modules.sentence_generation.utils import (
    load_prompts,
    parse_sentence_with_explanation,
    split_sentences,
)

PROMPTS_FILE = Path(__file__).parent / "prompts" / "llm_judge_ask.yaml"


def llm_judge_ask_generate(
    pmcid: str,
    variants: list[str],
    *,
    model: str = "gpt-5",
    prompt_version: str = "v3",
) -> dict[str, list[GeneratedSentence]]:
    """Generate sentences per-variant with a simpler prompt.

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

    article_text = get_methods_and_conclusions_text(pmcid)
    if not article_text:
        article_text = get_markdown_text(pmcid)
    if not article_text:
        logger.warning(f"No article text found for {pmcid}")
        return {v: [] for v in variants}

    use_explanations = prompt_version == "v4"

    result: dict[str, list[GeneratedSentence]] = {}

    for variant in variants:
        user_prompt = prompt_config["user"].format(
            variant=variant, article_text=article_text
        )
        system_prompt = prompt_config["system"]

        try:
            output = call_llm(model, system_prompt, user_prompt)
        except Exception as e:
            logger.error(f"Error generating for {pmcid}/{variant}: {e}")
            output = ""

        if not output:
            result[variant] = []
            continue

        if use_explanations:
            parsed = parse_sentence_with_explanation(output)
            result[variant] = [GeneratedSentence(**parsed)]
        else:
            sentences = split_sentences(output)
            result[variant] = [GeneratedSentence(sentence=s) for s in sentences]

        preview = result[variant][0].sentence[:90] if result[variant] else "<no output>"
        logger.info(f"  {variant}: {preview}")

    return result
