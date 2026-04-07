"""Shared utilities for sentence generation experiments."""

import re

import yaml

from shared.utils import get_markdown_text
from generation.modules.utils_bioc import fetch_bioc_supplement
from shared.term_normalization.snp_expansion import SNPExpander


# ============================================================================
# Singletons
# ============================================================================

_snp_expander: SNPExpander | None = None


def get_snp_expander() -> SNPExpander:
    """Get or initialize the SNP expander singleton."""
    global _snp_expander
    if _snp_expander is None:
        _snp_expander = SNPExpander()
        _snp_expander.load_or_build()
    return _snp_expander


# ============================================================================
# Text retrieval
# ============================================================================


def get_article_text(pmcid: str, use_supplements: bool = True) -> tuple[str, bool]:
    """Get article text, optionally with supplementary material.

    Returns:
        Tuple of (article_text, has_supplement).
    """
    article_text = get_markdown_text(pmcid)
    if not article_text:
        return "", False

    if use_supplements:
        supplement_text = fetch_bioc_supplement(pmcid)
        if supplement_text:
            combined = (
                article_text
                + "\n\n--- SUPPLEMENTARY MATERIAL ---\n\n"
                + supplement_text
            )
            return combined, True

    return article_text, False


# ============================================================================
# Variant context
# ============================================================================


def get_variant_context(variant: str) -> str:
    """Get additional context for a variant using SNP expander.

    For rsID variants, looks up alternative notations (gene + position).
    For star alleles and HLA alleles, returns empty string.

    Returns:
        Context string like "Also known as: CYP2B6 516G>T" or empty string.
    """
    expander = get_snp_expander()

    if variant.lower().startswith("rs"):
        alt_notations = []
        for (gene, notation), mapping in expander._mappings.items():
            if mapping.rsid.lower() == variant.lower():
                alt_notations.append(f"{gene} {notation}")
                if mapping.star_allele:
                    alt_notations.append(mapping.star_allele)

        for (gene, notation), mapping in expander._curated_mappings.items():
            if mapping.rsid.lower() == variant.lower():
                alt_notations.append(f"{gene} {notation}")

        if alt_notations:
            unique_notations = list(set(alt_notations))
            return f"Also known as: {', '.join(unique_notations)}"

    return ""


# ============================================================================
# Output parsing
# ============================================================================


def parse_batch_output(
    output: str, use_explanations: bool
) -> dict[str, list[dict[str, str]]]:
    """Parse batch LLM output into a dict mapping variant -> list of sentence dicts.

    Expected format for non-explanation prompts:
        VARIANT: rs9923231
        SENTENCE: Genotypes CT + TT of rs9923231 are associated with...

    Expected format for explanation prompts:
        VARIANT: rs9923231
        SENTENCE: Genotypes CT + TT of rs9923231 are associated with...
        EXPLANATION: A study of 1,015 patients found...
    """
    result: dict[str, list[dict[str, str]]] = {}

    if use_explanations:
        pattern = r"VARIANT:\s*(.+?)\s*\n\s*SENTENCE:\s*(.+?)\s*\n\s*EXPLANATION:\s*(.+?)(?=\n\s*VARIANT:|$)"
        matches = re.findall(pattern, output, re.DOTALL | re.IGNORECASE)
        for match in matches:
            variant_id = match[0].strip()
            sentence = match[1].strip()
            explanation = match[2].strip()
            entry = {"sentence": sentence, "explanation": explanation}
            if variant_id in result:
                result[variant_id].append(entry)
            else:
                result[variant_id] = [entry]
    else:
        pattern = r"VARIANT:\s*(.+?)\s*\n\s*SENTENCE:\s*(.+?)(?=\n\s*VARIANT:|$)"
        matches = re.findall(pattern, output, re.DOTALL | re.IGNORECASE)
        for match in matches:
            variant_id = match[0].strip()
            sentence = match[1].strip()
            entry = {"sentence": sentence, "explanation": ""}
            if variant_id in result:
                result[variant_id].append(entry)
            else:
                result[variant_id] = [entry]

    return result


def split_sentences(text: str) -> list[str]:
    """Split model output into a list of sentences.

    Handles either newline-separated or standard sentence punctuation.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) > 1:
        return lines
    parts = re.split(r"([.!?])\s+", text.strip())
    sentences: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        sentences.append((parts[i] + parts[i + 1]).strip())
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1].strip())
    return [s for s in sentences if s]


def parse_sentence_with_explanation(text: str) -> dict[str, str]:
    """Parse output in 'SENTENCE: ... EXPLANATION: ...' format.

    Returns a dict with 'sentence' and 'explanation' keys.
    """
    sentence_match = re.search(
        r"SENTENCE:\s*(.+?)(?=EXPLANATION:|$)", text, re.DOTALL | re.IGNORECASE
    )
    explanation_match = re.search(
        r"EXPLANATION:\s*(.+?)$", text, re.DOTALL | re.IGNORECASE
    )

    if sentence_match:
        sentence = sentence_match.group(1).strip()
        explanation = explanation_match.group(1).strip() if explanation_match else ""
        return {"sentence": sentence, "explanation": explanation}
    else:
        return {"sentence": text.strip(), "explanation": ""}


# ============================================================================
# Prompt loading
# ============================================================================


def load_prompts(prompts_file) -> dict:
    """Load prompts from a YAML file."""
    with open(prompts_file) as f:
        return yaml.safe_load(f)
