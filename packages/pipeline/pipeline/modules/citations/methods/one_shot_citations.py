"""
One Shot Citations - Find supporting citations for pharmacogenomic associations.

Finds 3-5 supporting sentences from the source article for each association
in a single LLM call per article.
"""

from pathlib import Path

from loguru import logger

from shared.utils import call_llm, get_markdown_text
from pipeline.modules.citations.models import Citation
from pipeline.modules.citations.utils import load_prompts, parse_citation_output

PROMPTS_FILE = Path(__file__).parent / "prompts" / "one_shot_citations.yaml"


def one_shot_citations_find(
    pmcid: str,
    associations: list[dict],
    *,
    model: str = "gpt-4o",
    prompt_version: str = "v1",
) -> list[Citation]:
    """Find citations for all associations in a single LLM call.

    Args:
        pmcid: Article PMC identifier.
        associations: List of {variant, sentence, explanation} dicts.
        model: LLM model to use.
        prompt_version: Which prompt version from prompts.yaml.

    Returns:
        List of Citation objects.
    """
    prompts = load_prompts(PROMPTS_FILE)
    prompt_config = prompts[prompt_version]

    # Get article text
    article_text = get_markdown_text(pmcid)
    if not article_text:
        logger.warning(f"No article text found for {pmcid}")
        return [
            Citation(
                variant=a["variant"],
                sentence=a["sentence"],
                explanation=a.get("explanation", ""),
                citations=[],
            )
            for a in associations
        ]

    # Format associations for the prompt with numbered indices (1-indexed)
    if prompt_version == "v2":
        # Include explanations
        associations_text = "\n\n".join(
            [
                f"ASSOCIATION {i + 1}:\n- Variant: {a['variant']}\n- Sentence: {a['sentence']}\n- Explanation: {a.get('explanation', '')}"
                for i, a in enumerate(associations)
            ]
        )
    else:
        associations_text = "\n\n".join(
            [
                f"ASSOCIATION {i + 1}:\n- Variant: {a['variant']}\n- Sentence: {a['sentence']}"
                for i, a in enumerate(associations)
            ]
        )

    # Create the prompt
    user_prompt = prompt_config["user"].format(
        associations=associations_text, article_text=article_text
    )
    system_prompt = prompt_config["system"]

    # Call LLM
    try:
        logger.debug(f"Making LLM call for {len(associations)} association(s)")
        output = call_llm(model, system_prompt, user_prompt)
    except Exception as e:
        logger.error(f"Error generating citations for {pmcid}: {e}")
        output = ""

    # Parse the output (returns {association_idx: [citations]})
    assoc_citations = parse_citation_output(output) if output else {}

    # Build result list
    results: list[Citation] = []
    for i, assoc in enumerate(associations):
        assoc_idx = i + 1  # 1-indexed
        citations = assoc_citations.get(assoc_idx, [])

        results.append(
            Citation(
                variant=assoc["variant"],
                sentence=assoc["sentence"],
                explanation=assoc.get("explanation", ""),
                citations=citations,
            )
        )

        if citations:
            logger.info(
                f"  Association {assoc_idx} ({assoc['variant']}): {len(citations)} citation(s)"
            )
        else:
            logger.warning(
                f"  Association {assoc_idx} ({assoc['variant']}): no citations found"
            )

    return results
