"""Deterministic gene and variant candidate extraction from free text."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypedDict

from .gene_search import GeneLookup
from .variant_search import VariantLookup


_RSID_RE = re.compile(r"(?<![A-Za-z0-9])rs\d{4,}(?![A-Za-z0-9])", re.IGNORECASE)
_ALLELE_RE = re.compile(
    r"(?<![A-Za-z0-9-])(?:HLA-)?[A-Z][A-Z0-9-]{0,14}\s*[∗★*]\s*"
    r"\d+[A-Z]?(?::\d+){0,2}(?:[xX×][nN\d]+)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_SNP_RE = re.compile(
    r"(?<![A-Za-z0-9-])[A-Z][A-Z0-9-]{1,14}[\s\-(]+"
    r"(?:[-−–]?\d+(?:[+-]\d+)?\s*[ACGT]\s*>\s*[ACGT]|"
    r"[ACGT]-?\d+[ACGT])\)?",
    re.IGNORECASE,
)


class NormalizedGeneticEntity(TypedDict, total=False):
    normalized_term: str
    id: str
    url: str
    score: float
    matched_term: str | None
    match_type: str
    source: str
    entity_type: str
    ncbi_gene_id: str | None
    hgnc_id: str | None


class GeneticMention(TypedDict):
    count: int
    spans: list[list[int]]
    ambiguous: bool
    matches: list[NormalizedGeneticEntity]


class GeneticTextResult(TypedDict):
    genes: dict[str, GeneticMention]
    variants: dict[str, GeneticMention]


def literal_spans(text: str, mention: str) -> list[tuple[str, int, int]]:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(mention)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return [
        (match.group(), match.start(), match.end()) for match in pattern.finditer(text)
    ]


def serialize_results(results: list[Any]) -> list[NormalizedGeneticEntity]:
    serialized: list[NormalizedGeneticEntity] = []
    for result in results:
        entry: NormalizedGeneticEntity = {
            "normalized_term": result.normalized_term,
            "id": result.id,
            "url": result.url,
            "score": result.score,
            "matched_term": result.matched_term,
            "match_type": result.match_type,
            "source": result.source,
            "entity_type": getattr(result, "entity_type", "gene"),
        }
        if hasattr(result, "ncbi_gene_id"):
            entry["ncbi_gene_id"] = result.ncbi_gene_id
        if hasattr(result, "hgnc_id"):
            entry["hgnc_id"] = result.hgnc_id
        serialized.append(entry)
    return serialized


def add_mention(
    mentions: dict[str, GeneticMention],
    original: str,
    start: int,
    end: int,
    matches: list[NormalizedGeneticEntity],
    *,
    ambiguous: bool,
) -> None:
    entry = mentions.get(original)
    if entry is None:
        entry = {
            "count": 0,
            "spans": [],
            "ambiguous": ambiguous,
            "matches": matches,
        }
        mentions[original] = entry
    span = [start, end]
    if span not in entry["spans"]:
        entry["count"] += 1
        entry["spans"].append(span)


def _variant_candidates(text: str) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for pattern in (_RSID_RE, _ALLELE_RE, _SNP_RE):
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if span in seen:
                continue
            seen.add(span)
            found.append((match.group(), *span))
    return sorted(found, key=lambda item: (item[1], item[2]))


def normalize_genes_and_variants_in_text(
    text: str,
    *,
    gene_lookup: GeneLookup | None = None,
    variant_lookup: VariantLookup | None = None,
    data_dir: str | Path = Path("data"),
    max_candidates: int = 5,
    use_api_fallback: bool = False,
) -> GeneticTextResult:
    """Extract high-recall genetic candidates and ground available identifiers."""
    if max_candidates < 1:
        raise ValueError("max_candidates must be at least 1")
    if not text.strip():
        return {"genes": {}, "variants": {}}

    base_dir = Path(data_dir)
    genes = gene_lookup or GeneLookup(data_dir=base_dir)
    variants = variant_lookup or VariantLookup(data_dir=base_dir)
    result: GeneticTextResult = {"genes": {}, "variants": {}}

    for candidate in genes.symbol_candidates(text):
        matches = genes.search(candidate, top_k=max_candidates)
        serialized = serialize_results(matches)
        for original, start, end in literal_spans(text, candidate):
            add_mention(
                result["genes"],
                original,
                start,
                end,
                serialized,
                ambiguous=any(match.ambiguous for match in matches),
            )

    for original, start, end in _variant_candidates(text):
        matches = variants.search(
            original,
            top_k=max_candidates,
            use_api_fallback=use_api_fallback,
        )
        add_mention(
            result["variants"],
            original,
            start,
            end,
            serialize_results(matches),
            ambiguous=any(match.ambiguous for match in matches),
        )

    return result
