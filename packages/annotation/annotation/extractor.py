"""One-call extraction of structured associations and their evidence."""

import json
import re
from pathlib import Path

from loguru import logger

from annotation.config import load_environment
from annotation.models import AnnotationResult, EvidenceQuote
from annotation.postprocess import merge_duplicate_associations, normalize_variants
from shared.utils import call_llm


SYSTEM_PROMPT = """You extract pharmacogenomic associations from scientific articles.

Return atomic facts rather than polished prose. Discover genetic variants that the
paper itself studies and distinguish them from variants mentioned only as background.
Every association and variant classification must be supported by an exact quote from
the supplied source blocks. Never infer a genotype, comparator, population, direction,
statistic, drug, or outcome. Extract separate records for distinct outcomes or effect
directions. Use the variant notation found in the source; leave variant_aliases empty
and variant_id/variant_url null because application code performs normalization. Use
null or an empty array when a field is not explicitly reported.
"""


def number_source_blocks(text: str) -> tuple[str, dict[str, str]]:
    """Add stable source IDs to non-empty Markdown blocks."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    sources = {f"S{i:04d}": block for i, block in enumerate(blocks, 1)}
    numbered = "\n\n".join(
        f"[SOURCE {source_id}]\n{block}" for source_id, block in sources.items()
    )
    return numbered, sources


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def quote_is_supported(source: str, quote: str) -> bool:
    """Permit whitespace normalization, but reject paraphrased evidence."""
    normalized_quote = _normalize_space(quote)
    return bool(normalized_quote) and normalized_quote in _normalize_space(source)


def _valid_evidence(
    evidence: list[EvidenceQuote], sources: dict[str, str]
) -> list[EvidenceQuote]:
    return [
        item
        for item in evidence
        if item.source_id in sources
        and quote_is_supported(sources[item.source_id], item.quote)
    ]


def validate_evidence(result: AnnotationResult, sources: dict[str, str]) -> AnnotationResult:
    """Remove records that have no exact quote in their claimed source block."""
    associations = []
    for association in result.associations:
        evidence = _valid_evidence(association.evidence, sources)
        if evidence:
            associations.append(association.model_copy(update={"evidence": evidence}))
        else:
            logger.warning(
                "Discarding unsupported association for {}", association.variant
            )

    mentions = []
    for mention in result.variant_mentions:
        evidence = _valid_evidence(mention.evidence, sources)
        if evidence:
            mentions.append(mention.model_copy(update={"evidence": evidence}))

    return result.model_copy(
        update={"associations": associations, "variant_mentions": mentions}
    )


def annotate_text(text: str, *, model: str = "gpt-6.2-sol") -> AnnotationResult:
    """Extract all evidence-grounded PGx annotations from article text."""
    load_environment()
    if not text.strip():
        return AnnotationResult(associations=[], variant_mentions=[])

    numbered_text, sources = number_source_blocks(text)
    prompt = f"""Extract every pharmacogenomic association studied by this paper.
Also record genetic variant mentions that are background-only or unclear.
Copy each evidence quote exactly and use the enclosing SOURCE ID.

Article sources:
{numbered_text}
"""
    output = call_llm(
        model,
        SYSTEM_PROMPT,
        prompt,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "grounded_pharmacogenomic_annotations",
                "strict": True,
                "schema": AnnotationResult.model_json_schema(),
            },
        },
    )
    result = AnnotationResult.model_validate(json.loads(output))
    result = validate_evidence(result, sources)
    data_dir = Path(__file__).resolve().parents[3] / "data"
    result = normalize_variants(result, sources, data_dir=data_dir)
    return merge_duplicate_associations(result)
