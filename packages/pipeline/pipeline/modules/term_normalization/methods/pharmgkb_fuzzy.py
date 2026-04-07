"""
PharmGKB fuzzy matching normalization.

Normalizes extracted variant names against PharmGKB's curated variant database
using fuzzy string matching via VariantLookup.
"""

from loguru import logger

from pipeline.modules.term_normalization.models import (
    NormalizationResult,
    VariantMapping,
)
from shared.term_normalization import VariantLookup

_variant_lookup = None


def _get_variant_lookup() -> VariantLookup:
    """Get or initialize the VariantLookup singleton."""
    global _variant_lookup
    if _variant_lookup is None:
        _variant_lookup = VariantLookup()
    return _variant_lookup


def pharmgkb_fuzzy_normalize(
    pmcid: str,
    variants: list[str],
    threshold: float = 0.8,
    min_score: float = 0.9,
    top_k: int = 3,
) -> NormalizationResult:
    """Normalize variants via PharmGKB fuzzy matching.

    For each variant, looks up the best match in PharmGKB. If the best match
    scores >= min_score, the normalized term is used; otherwise the original
    is kept.
    """
    variant_lookup = _get_variant_lookup()
    normalized_variants: list[str] = []
    mappings: list[VariantMapping] = []

    for variant in variants:
        try:
            results = variant_lookup.search(variant, threshold=threshold, top_k=top_k)
            if results and results[0].score >= min_score:
                norm = results[0].normalized_term
                normalized_variants.append(norm)
                mappings.append(
                    VariantMapping(
                        original=variant,
                        normalized=norm,
                        pharmgkb_id=results[0].id,
                        score=results[0].score,
                        changed=(variant != norm),
                    )
                )
            else:
                best_score = results[0].score if results else None
                normalized_variants.append(variant)
                mappings.append(
                    VariantMapping(
                        original=variant,
                        normalized=variant,
                        pharmgkb_id=None,
                        score=best_score,
                        changed=False,
                    )
                )
        except Exception as e:
            logger.debug(f"Normalization failed for '{variant}': {e}")
            normalized_variants.append(variant)
            mappings.append(
                VariantMapping(
                    original=variant,
                    normalized=variant,
                    pharmgkb_id=None,
                    score=None,
                    changed=False,
                )
            )

    # Deduplicate normalized variants while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for v in normalized_variants:
        if v not in seen:
            seen.add(v)
            deduped.append(v)

    return NormalizationResult(
        normalized_variants=deduped,
        mappings=mappings,
    )
