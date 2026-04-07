"""
Raw Sentence Ask - Per-variant LLM sentence generation with supplements + SNP context.

Generates sentences one variant at a time, using combined article + supplement text
and variant context enrichment from SNP expander.
"""

from pathlib import Path

from loguru import logger

from shared.utils import call_llm
from generation.modules.sentence_generation.models import GeneratedSentence
from generation.modules.sentence_generation.utils import (
    get_article_text,
    get_variant_context,
    load_prompts,
    split_sentences,
)

PROMPTS_FILE = Path(__file__).parent / "prompts" / "raw_sentence_ask.yaml"


def raw_sentence_ask_generate(
    pmcid: str,
    variants: list[str],
    *,
    model: str = "claude-sonnet-4-20250514",
    prompt_version: str = "v3",
    use_supplements: bool = True,
    use_variant_context: bool = True,
) -> dict[str, list[GeneratedSentence]]:
    """Generate sentences per-variant with supplement text + SNP context enrichment.

    Args:
        pmcid: Article PMC identifier.
        variants: List of variant strings to generate sentences for.
        model: LLM model to use.
        prompt_version: Which prompt version from prompts.yaml.
        use_supplements: Whether to include supplementary material text.
        use_variant_context: Whether to include variant alternative notations.

    Returns:
        Dict mapping variant -> list of GeneratedSentence.
    """
    prompts = load_prompts(PROMPTS_FILE)
    prompt_config = prompts[prompt_version]

    article_text, has_supplement = get_article_text(
        pmcid, use_supplements=use_supplements
    )
    if not article_text:
        logger.warning(f"No article text found for {pmcid}")
        return {v: [] for v in variants}

    if has_supplement:
        logger.debug(f"{pmcid}: using supplement text")

    result: dict[str, list[GeneratedSentence]] = {}

    for variant in variants:
        variant_context = ""
        if use_variant_context:
            variant_context = get_variant_context(variant)

        # Format prompt with variant context if available
        if variant_context and "{variant_context}" in prompt_config["user"]:
            user_prompt = prompt_config["user"].format(
                variant=variant,
                variant_context=variant_context,
                article_text=article_text,
            )
        else:
            user_prompt = prompt_config["user"].format(
                variant=variant,
                article_text=article_text,
            )
            if variant_context:
                user_prompt = user_prompt.replace(
                    f'variant "{variant}"',
                    f'variant "{variant}" ({variant_context})',
                )

        system_prompt = prompt_config["system"]

        try:
            output = call_llm(model, system_prompt, user_prompt)
        except Exception as e:
            logger.error(f"Error generating for {pmcid}/{variant}: {e}")
            output = ""

        if output:
            sentences = split_sentences(output)
            result[variant] = [GeneratedSentence(sentence=s) for s in sentences]
        else:
            result[variant] = []

        preview = result[variant][0].sentence[:90] if result[variant] else "<no output>"
        logger.info(f"  {variant}: {preview}")

    return result
