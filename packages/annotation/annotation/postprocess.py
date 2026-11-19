"""Term normalization and conservative association deduplication."""

import re
from collections.abc import Iterable
from pathlib import Path

from annotation.models import AnnotationResult, GroundedAssociation
from shared.term_normalization.genes import (
    VariantLookup,
    classify_variant_term,
    normalize_genes_and_variants_in_text,
)


_RSID_RE = re.compile(r"\brs\d{4,}\b", re.IGNORECASE)
_SUBSTITUTION_RE = re.compile(r"[-−–]?\d+(?:[+-]\d+)?\s*[ACGT]\s*>\s*[ACGT]", re.I)
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _unique(items: Iterable, key=lambda item: item) -> list:
    seen = set()
    result = []
    for item in items:
        marker = key(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def _lookup_rsid(lookup: VariantLookup, rsid: str):
    matches = lookup.search(rsid, top_k=1, use_api_fallback=False)
    if matches and not matches[0].ambiguous:
        return matches[0]
    return None


def _co_mentioned_rsid(
    association: GroundedAssociation,
    source_blocks: dict[str, str],
    lookup: VariantLookup,
):
    """Resolve a source alias through an explicitly co-mentioned rsID."""
    if not association.gene:
        return None
    candidates = set()

    # Tables and method descriptions commonly place cDNA notation and rsID on
    # the same line. This is stronger than general paragraph co-occurrence.
    compact_variant = re.sub(r"[\s*]", "", association.variant).casefold()
    for block in source_blocks.values():
        for line in block.splitlines():
            compact_line = re.sub(r"[\s*]", "", line).casefold()
            if compact_variant not in compact_line:
                continue
            line_matches = {
                match.normalized_term
                for rsid in _RSID_RE.findall(line)
                if (match := _lookup_rsid(lookup, rsid)) is not None
            }
            if len(line_matches) == 1:
                candidates.update(line_matches)
    if len(candidates) == 1:
        return _lookup_rsid(lookup, next(iter(candidates)))

    if "*" not in association.variant:
        return None

    candidates.clear()
    for block in source_blocks.values():
        if not re.search(rf"\b{re.escape(association.gene)}\b", block, re.IGNORECASE):
            continue
        allele_number = re.search(r"\*(\d+)", association.variant)
        if allele_number and not re.search(rf"\*{allele_number.group(1)}\b", block):
            continue
        normalized = normalize_genes_and_variants_in_text(
            block,
            variant_lookup=lookup,
            data_dir=lookup.data_dir,
            use_api_fallback=False,
        )
        for original_mention, mention in normalized["variants"].items():
            if not re.search(
                rf"\b{re.escape(association.gene)}\b", original_mention, re.IGNORECASE
            ):
                continue
            for match in mention["matches"]:
                if match.get("match_type") == "shorthand_to_rsid":
                    candidates.add(match["normalized_term"])
        # Some papers put the gene/star allele before a parenthesized bare
        # substitution, e.g. "CYP2C9*3 (1075A>C; rs1057910)". Route that
        # notation through VariantLookup as well rather than maintaining a map.
        for notation in _SUBSTITUTION_RE.findall(block):
            matches = lookup.search(
                f"{association.gene} {notation}",
                threshold=0.9,
                top_k=1,
                use_api_fallback=False,
            )
            if matches and not matches[0].ambiguous:
                candidates.add(matches[0].normalized_term)
    if len(candidates) != 1:
        return None
    return _lookup_rsid(lookup, next(iter(candidates)))


def normalize_variants(
    result: AnnotationResult,
    source_blocks: dict[str, str],
    *,
    data_dir: str | Path,
) -> AnnotationResult:
    """Canonicalize variants with shared term-normalization tools."""
    lookup = VariantLookup(data_dir=Path(data_dir))
    normalized = []
    resolution_cache = {}
    for association in result.associations:
        original = association.variant
        cache_key = (original.casefold(), (association.gene or "").casefold())
        if cache_key in resolution_cache:
            match = resolution_cache[cache_key]
        else:
            match = None
            direct = []
            queries = [original]
            if association.gene:
                queries.append(f"{association.gene} {original}")
            for query in queries:
                if classify_variant_term(query) == "variant":
                    continue
                direct = lookup.search(
                    query, threshold=0.9, top_k=1, use_api_fallback=False
                )
                if direct:
                    break
            if direct and direct[0].score >= 0.9 and not direct[0].ambiguous:
                match = direct[0]
            else:
                embedded = _unique(
                    rsid.lower() for rsid in _RSID_RE.findall(original)
                )
                if len(embedded) == 1:
                    match = _lookup_rsid(lookup, embedded[0])
                else:
                    match = _co_mentioned_rsid(association, source_blocks, lookup)
            resolution_cache[cache_key] = match

        if match:
            aliases = _unique(
                [*association.variant_aliases, original], key=lambda value: value.casefold()
            )
            aliases = [
                alias for alias in aliases if alias.casefold() != match.normalized_term.casefold()
            ]
            association = association.model_copy(
                update={
                    "variant": match.normalized_term,
                    "variant_aliases": aliases,
                    "variant_id": match.id,
                    "variant_url": match.url,
                }
            )
        normalized.append(association)
    return result.model_copy(update={"associations": normalized})


def _text_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _association_key(association: GroundedAssociation) -> tuple[str, ...]:
    return (
        _text_key(association.variant),
        _text_key(association.drug),
        _text_key(association.outcome),
        association.direction,
        _text_key(association.comparator),
    )


def merge_duplicate_associations(result: AnnotationResult) -> AnnotationResult:
    """Merge only records with the same normalized clinical claim."""
    merged: dict[tuple[str, ...], GroundedAssociation] = {}
    order: list[tuple[str, ...]] = []
    for association in result.associations:
        key = _association_key(association)
        current = merged.get(key)
        if current is None:
            merged[key] = association
            order.append(key)
            continue

        confidence = max(
            (current.confidence, association.confidence),
            key=lambda value: _CONFIDENCE_RANK[value],
        )
        merged[key] = current.model_copy(
            update={
                "variant_aliases": _unique(
                    [*current.variant_aliases, *association.variant_aliases],
                    key=lambda value: value.casefold(),
                ),
                "genotypes": _unique([*current.genotypes, *association.genotypes]),
                "population": _unique(
                    [*current.population, *association.population],
                    key=lambda value: value.casefold(),
                ),
                "statistics": _unique(
                    [*current.statistics, *association.statistics],
                    key=lambda item: (item.name.casefold(), item.value.casefold()),
                ),
                "evidence": _unique(
                    [*current.evidence, *association.evidence],
                    key=lambda item: (item.source_id, item.quote),
                ),
                "confidence": confidence,
            }
        )
    return result.model_copy(update={"associations": [merged[key] for key in order]})
