"""
Basic Summary - Generate concise summaries of pharmacogenomic findings.

Generates a structured summary from article text, variant associations,
and optional citations.
"""

from pathlib import Path

from loguru import logger

from shared.utils import call_llm, get_markdown_text
from generation.modules.summary.models import ArticleSummary
from generation.modules.summary.utils import (
    load_prompts,
    format_associations,
    format_citations,
)

PROMPTS_FILE = Path(__file__).parent / "prompts" / "basic_summary.yaml"


def basic_summary_generate(
    pmcid: str,
    variants_data: list[dict],
    citations_data: dict[str, list[dict]] | None = None,
    *,
    model: str = "gpt-5",
    prompt_version: str = "v1",
) -> ArticleSummary:
    """Generate a summary for a single article.

    Args:
        pmcid: Article PMC identifier.
        variants_data: List of {variant, sentences: [str]} dicts.
        citations_data: Optional citations dict (pmcid -> list of citation dicts).
        model: LLM model to use.
        prompt_version: Which prompt version from prompts.yaml.

    Returns:
        ArticleSummary object.
    """
    prompts = load_prompts(PROMPTS_FILE)
    prompt_config = prompts[prompt_version]

    # Get article text
    article_text = get_markdown_text(pmcid)
    if not article_text:
        logger.warning(f"No article text found for {pmcid}")
        article_text = "[Article text not available]"

    # Format associations and citations
    associations_text = format_associations(variants_data)
    citations_text = format_citations(pmcid, citations_data)

    # Create the prompt
    user_prompt = prompt_config["user"].format(
        article_text=article_text,
        associations=associations_text,
        citations=citations_text,
    )
    system_prompt = prompt_config["system"]

    # Call LLM
    try:
        logger.debug(f"Making LLM call for {pmcid}")
        summary = call_llm(model, system_prompt, user_prompt)
        logger.info(f"Generated summary for {pmcid} ({len(summary)} chars)")
    except Exception as e:
        summary = f"[Error generating summary: {e}]"
        logger.error(f"Error generating summary for {pmcid}: {e}")

    return ArticleSummary(
        pmcid=pmcid,
        summary=summary,
        num_variants=len(variants_data),
        variants=[v["variant"] for v in variants_data],
    )
