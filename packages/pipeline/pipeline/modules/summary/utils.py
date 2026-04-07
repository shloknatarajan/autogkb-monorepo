"""Shared utilities for summary generation experiments."""

import yaml


def format_associations(variants_data: list[dict]) -> str:
    """Format variant associations for the prompt.

    Args:
        variants_data: List of {variant, sentences: [str]} dicts.

    Returns:
        Formatted string of associations.
    """
    parts = []
    for i, v in enumerate(variants_data, 1):
        variant = v["variant"]
        sentences = v["sentences"]
        sentences_text = "\n  - ".join(sentences)
        parts.append(f"{i}. {variant}:\n  - {sentences_text}")

    return "\n\n".join(parts)


def format_citations(pmcid: str, citations_data: dict[str, list[dict]] | None) -> str:
    """Format citations for the prompt.

    Args:
        pmcid: PMCID to get citations for.
        citations_data: Citations data keyed by pmcid.

    Returns:
        Formatted string of citations, or "No citations available".
    """
    if citations_data is None or pmcid not in citations_data:
        return "No citations available"

    pmcid_citations = citations_data[pmcid]
    parts = []
    for entry in pmcid_citations:
        variant = entry.get("variant", "Unknown")
        sentence = entry.get("sentence", "")
        citations = entry.get("citations", [])

        if citations:
            citations_text = "\n    - ".join(citations)
            parts.append(
                f"For '{variant}' ({sentence[:50]}...):\n    - {citations_text}"
            )

    if not parts:
        return "No citations available"

    return "\n\n".join(parts)


def load_prompts(prompts_file) -> dict:
    """Load prompts from a YAML file."""
    with open(prompts_file) as f:
        return yaml.safe_load(f)
