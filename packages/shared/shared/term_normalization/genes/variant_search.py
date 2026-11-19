"""Deterministic normalization for variants, haplotypes, and HLA alleles."""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from loguru import logger
from pydantic import BaseModel


CLINPGX_URL = "https://www.clinpgx.org"
PHARMGKB_API_URL = "https://api.clinpgx.org/v1/data"

_RSID_RE = re.compile(r"rs\d+", re.IGNORECASE)
_HLA_GENES = {
    "A", "B", "C", "CW", "DPA1", "DPB1", "DQA1", "DQB1",
    "DRA", "DRB1", "DRB3", "DRB4", "DRB5",
}
_HLA_RE = re.compile(
    r"(?:HLA-)?(?P<gene>[A-Z]+\d*)\s*\*\s*"
    r"(?P<field1>\d{2})(?::?(?P<field2>\d{2}))?"
    r"(?::(?P<field3>\d{2}))?",
    re.IGNORECASE,
)
_STAR_RE = re.compile(
    r"(?P<gene>[A-Z][A-Z0-9]+)\s*\*\s*(?P<allele>\d+[A-Z]?)"
    r"(?P<copy>[xX×]\s*[nN\d]+)?",
    re.IGNORECASE,
)
_SNP_FORWARD_RE = re.compile(
    r"(?P<gene>[A-Z][A-Z0-9]+)[\s(]*"
    r"(?P<position>-?\d+(?:[+-]\d+)?)\s*"
    r"(?P<ref>[ACGT])\s*>\s*(?P<alt>[ACGT])\)?",
    re.IGNORECASE,
)
_SNP_REVERSED_RE = re.compile(
    r"(?P<gene>[A-Z][A-Z0-9]+)[\s\-(]+"
    r"(?P<ref>[ACGT])(?P<position>-?\d+)(?P<alt>[ACGT])\)?",
    re.IGNORECASE,
)


class VariantSearchResult(BaseModel):
    """A grounded match to a ClinPGx variant or haplotype record."""

    raw_input: str
    id: str
    normalized_term: str
    url: str
    score: float
    matched_term: str | None = None
    match_type: str = "unknown"
    entity_type: str = "variant"
    ambiguous: bool = False
    source: str = "pharmgkb"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for Pydantic v1 or v2."""
        try:
            return self.model_dump()
        except AttributeError:  # pragma: no cover - Pydantic v1 fallback
            return self.dict()


@dataclass(frozen=True)
class _VariantRecord:
    id: str
    name: str
    synonyms: tuple[str, ...]


@dataclass(frozen=True)
class _VariantAlias:
    record: _VariantRecord
    text: str
    normalized: str
    kind: str


@dataclass(frozen=True)
class _VariantIndex:
    aliases: tuple[_VariantAlias, ...]
    exact: dict[str, tuple[_VariantAlias, ...]]


def normalize_variant_text(text: str) -> str:
    """Normalize punctuation, spacing, and case for variant comparison."""
    normalized = unicodedata.normalize("NFKC", str(text)).strip()
    normalized = normalized.replace("×", "x").replace("∗", "*").replace("★", "*")
    normalized = normalized.replace("−", "-").replace("–", "-")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*([*:>=/])\s*", r"\1", normalized)
    return normalized.casefold()


def normalize_hla_allele(text: str) -> str | None:
    """Return canonical HLA notation when ``text`` is an HLA allele."""
    prepared = text.strip().replace("∗", "*").replace("★", "*")
    match = _HLA_RE.fullmatch(prepared)
    if not match:
        return None
    gene = match.group("gene").upper()
    if not prepared.upper().startswith("HLA-") and gene not in _HLA_GENES:
        return None
    if gene == "CW":
        gene = "C"
    fields = [match.group("field1")]
    fields.extend(
        field
        for field in (match.group("field2"), match.group("field3"))
        if field
    )
    return f"HLA-{gene}*{':'.join(fields)}"


def normalize_star_allele(text: str) -> str | None:
    """Return canonical star-allele notation when ``text`` is one."""
    prepared = text.strip().replace("∗", "*").replace("★", "*")
    match = _STAR_RE.fullmatch(prepared)
    if not match or prepared.upper().startswith("HLA-"):
        return None
    copy = match.group("copy") or ""
    copy = re.sub(r"\s+", "", copy).replace("X", "x").replace("n", "N")
    return f"{match.group('gene').upper()}*{match.group('allele')}{copy}"


def classify_variant_term(text: str) -> str:
    """Classify a term so it can be routed to the correct lookup strategy."""
    stripped = text.strip()
    if _RSID_RE.fullmatch(stripped):
        return "rsid"
    if normalize_hla_allele(stripped):
        return "hla"
    if normalize_star_allele(stripped):
        return "star_allele"
    if parse_snp_shorthand(stripped):
        return "snp_shorthand"
    return "variant"


def parse_snp_shorthand(text: str) -> tuple[str, str] | None:
    """Parse gene-relative substitution notation into ``(gene, notation)``."""
    prepared = text.strip().replace("−", "-").replace("–", "-")
    prepared = re.sub(r"-\s+(?=\d)", "-", prepared)
    for pattern in (_SNP_FORWARD_RE, _SNP_REVERSED_RE):
        match = pattern.fullmatch(prepared)
        if match:
            return (
                match.group("gene").upper(),
                f"{match.group('position')}{match.group('ref').upper()}>"
                f"{match.group('alt').upper()}",
            )
    return None


def _split_synonyms(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


@lru_cache(maxsize=8)
def _load_variant_index(path_string: str, modified_ns: int) -> _VariantIndex:
    """Load the local ClinPGx table once per path and file revision."""
    del modified_ns
    aliases: list[_VariantAlias] = []
    exact: dict[str, list[_VariantAlias]] = {}

    with Path(path_string).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            identifier = (row.get("Variant ID") or "").strip()
            name = (row.get("Variant Name") or "").strip()
            if not identifier or not name:
                continue
            record = _VariantRecord(
                id=identifier,
                name=name,
                synonyms=_split_synonyms(row.get("Synonyms")),
            )
            seen: set[str] = set()
            values = [(name, "canonical")]
            values.extend((synonym, "synonym") for synonym in record.synonyms)
            for value, kind in values:
                normalized = normalize_variant_text(value)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                alias = _VariantAlias(record, value, normalized, kind)
                aliases.append(alias)
                exact.setdefault(normalized, []).append(alias)

    return _VariantIndex(
        aliases=tuple(aliases),
        exact={key: tuple(value) for key, value in exact.items()},
    )


def clear_variant_index_cache() -> None:
    """Clear the cached local variant table, primarily for tests."""
    _load_variant_index.cache_clear()
    _load_snp_mapping_index.cache_clear()


def _index_for(path: Path) -> _VariantIndex:
    resolved = path.resolve()
    return _load_variant_index(str(resolved), resolved.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _load_snp_mapping_index(
    path_string: str,
    modified_ns: int,
) -> dict[tuple[str, str], str]:
    """Load the checked-in gene-relative notation map."""
    del modified_ns
    import json

    try:
        payload = json.loads(Path(path_string).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    mappings: dict[tuple[str, str], str] = {}
    for item in payload.get("mappings", []):
        if not isinstance(item, dict):
            continue
        gene = item.get("gene")
        notation = item.get("notation")
        rsid = item.get("rsid")
        if all(isinstance(value, str) for value in (gene, notation, rsid)):
            mappings[(gene.upper(), normalize_variant_text(notation))] = rsid.lower()
    return mappings


def _api_records(endpoint: str, symbol: str) -> list[dict[str, Any]]:
    """Fetch a guarded symbol response from PharmGKB."""
    try:
        response = requests.get(
            f"{PHARMGKB_API_URL}/{endpoint}",
            params={"symbol": symbol},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        logger.warning(f"PharmGKB {endpoint} lookup failed for '{symbol}': {error}")
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    return [record for record in payload["data"] if isinstance(record, dict)]


def _api_symbol_search(
    raw_input: str,
    symbol: str,
    *,
    endpoint: str,
    entity_type: str,
    threshold: float,
    top_k: int,
) -> list[VariantSearchResult]:
    candidates: list[tuple[dict[str, Any], float]] = []
    query = normalize_variant_text(symbol)
    for record in _api_records(endpoint, symbol):
        record_symbol = record.get("symbol")
        identifier = record.get("id")
        if not isinstance(record_symbol, str) or not identifier:
            continue
        score = SequenceMatcher(
            None, query, normalize_variant_text(record_symbol)
        ).ratio()
        if score == 1 and score >= threshold:
            candidates.append((record, score))

    candidates.sort(key=lambda item: (-item[1], str(item[0].get("symbol"))))
    ambiguous = (
        len(candidates) > 1 and candidates[0][1] - candidates[1][1] <= 0.02
    )
    return [
        VariantSearchResult(
            raw_input=raw_input,
            id=str(record["id"]),
            normalized_term=str(record["symbol"]),
            url=f"{CLINPGX_URL}/{endpoint}/{record['id']}",
            score=score,
            matched_term=str(record["symbol"]),
            match_type="exact_api",
            entity_type=entity_type,
            ambiguous=ambiguous,
        )
        for record, score in candidates[:top_k]
    ]


def pgkb_star_allele_search(
    star_allele: str,
    threshold: float = 0.8,
    top_k: int = 1,
) -> list[VariantSearchResult]:
    """Look up a canonical star allele through the haplotype endpoint."""
    canonical = normalize_star_allele(star_allele)
    if canonical is None:
        return []
    return _api_symbol_search(
        star_allele,
        canonical,
        endpoint="haplotype",
        entity_type="haplotype",
        threshold=threshold,
        top_k=top_k,
    )


def pgkb_hla_allele_search(
    hla_allele: str,
    threshold: float = 0.8,
    top_k: int = 1,
) -> list[VariantSearchResult]:
    """Look up a canonical HLA allele through the haplotype endpoint."""
    canonical = normalize_hla_allele(hla_allele)
    if canonical is None:
        return []
    return _api_symbol_search(
        hla_allele,
        canonical,
        endpoint="haplotype",
        entity_type="hla_allele",
        threshold=threshold,
        top_k=top_k,
    )


def pgkb_rsid_search(
    rsid: str,
    threshold: float = 0.8,
    top_k: int = 1,
) -> list[VariantSearchResult]:
    """Look up an rsID through the variant endpoint."""
    canonical = rsid.strip().lower()
    if not _RSID_RE.fullmatch(canonical):
        return []
    return _api_symbol_search(
        rsid,
        canonical,
        endpoint="variant",
        entity_type="variant",
        threshold=threshold,
        top_k=top_k,
    )


class VariantLookup(BaseModel):
    """Resolve variant-like terms with notation-aware internal routing."""

    data_dir: Path = Path("data")

    def _data_path(self) -> Path:
        return self.data_dir / "term_lookup_info" / "variants.tsv"

    def _snp_mapping_path(self) -> Path:
        return self.data_dir / "term_lookup_info" / "snp_notation_mappings.json"

    def _shorthand_rsid(self, variant: str) -> str | None:
        parsed = parse_snp_shorthand(variant)
        path = self._snp_mapping_path()
        if parsed is None or not path.exists():
            return None
        resolved = path.resolve()
        mappings = _load_snp_mapping_index(
            str(resolved),
            resolved.stat().st_mtime_ns,
        )
        gene, notation = parsed
        normalized = normalize_variant_text(notation)
        result = mappings.get((gene, normalized))
        if result is not None:
            return result
        alternate = normalized[1:] if normalized.startswith("-") else f"-{normalized}"
        return mappings.get((gene, alternate))

    def _local_search(
        self,
        variant: str,
        *,
        threshold: float,
        top_k: int,
        ambiguity_margin: float,
    ) -> list[VariantSearchResult]:
        index = _index_for(self._data_path())
        query = normalize_variant_text(variant)
        if not query:
            return []

        exact_by_id: dict[str, _VariantAlias] = {}
        for alias in index.exact.get(query, ()):
            current = exact_by_id.get(alias.record.id)
            if current is None or alias.kind == "canonical":
                exact_by_id[alias.record.id] = alias
        if exact_by_id:
            ranked = [(alias, 1.0) for alias in exact_by_id.values()]
        else:
            if len(query) < 5:
                return []
            effective_threshold = max(threshold, 0.9) if len(query) < 8 else threshold
            best_by_id: dict[str, tuple[_VariantAlias, float]] = {}
            for alias in index.aliases:
                score = SequenceMatcher(None, query, alias.normalized).ratio()
                if score < effective_threshold:
                    continue
                current = best_by_id.get(alias.record.id)
                if current is None or score > current[1]:
                    best_by_id[alias.record.id] = (alias, score)
            ranked = list(best_by_id.values())

        ranked.sort(
            key=lambda item: (
                -item[1],
                item[0].kind != "canonical",
                item[0].record.name.casefold(),
            )
        )
        ambiguous = (
            len(ranked) > 1 and ranked[0][1] - ranked[1][1] <= ambiguity_margin
        )
        return [
            VariantSearchResult(
                raw_input=variant,
                id=alias.record.id,
                normalized_term=alias.record.name,
                url=f"{CLINPGX_URL}/variant/{alias.record.id}",
                score=score,
                matched_term=alias.text,
                match_type=(
                    f"exact_{alias.kind}" if score == 1 else f"fuzzy_{alias.kind}"
                ),
                entity_type="variant",
                ambiguous=ambiguous,
            )
            for alias, score in ranked[:top_k]
        ]

    def search(
        self,
        variant: str,
        threshold: float = 0.8,
        top_k: int = 1,
        ambiguity_margin: float = 0.02,
        use_api_fallback: bool = True,
    ) -> list[VariantSearchResult]:
        """Detect notation and route it to the appropriate lookup strategy."""
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if ambiguity_margin < 0:
            raise ValueError("ambiguity_margin cannot be negative")
        if not variant.strip():
            return []

        entity_type = classify_variant_term(variant)
        if entity_type == "hla":
            return (
                pgkb_hla_allele_search(variant, threshold=threshold, top_k=top_k)
                if use_api_fallback
                else []
            )
        if entity_type == "star_allele":
            return (
                pgkb_star_allele_search(variant, threshold=threshold, top_k=top_k)
                if use_api_fallback
                else []
            )
        if entity_type == "snp_shorthand":
            rsid = self._shorthand_rsid(variant)
            if rsid is None:
                return []
            results = self._local_search(
                rsid,
                threshold=threshold,
                top_k=top_k,
                ambiguity_margin=ambiguity_margin,
            )
            return [
                VariantSearchResult(
                    raw_input=variant,
                    id=result.id,
                    normalized_term=result.normalized_term,
                    url=result.url,
                    score=1.0,
                    matched_term=variant,
                    match_type="shorthand_to_rsid",
                    entity_type="variant",
                    ambiguous=result.ambiguous,
                    source=result.source,
                )
                for result in results
            ]

        local = self._local_search(
            variant,
            threshold=threshold,
            top_k=top_k,
            ambiguity_margin=ambiguity_margin,
        )
        if local or not use_api_fallback or entity_type != "rsid":
            return local
        return pgkb_rsid_search(variant, threshold=threshold, top_k=top_k)

    def rsid_lookup(
        self,
        rsid: str,
        threshold: float = 0.8,
        top_k: int = 1,
    ) -> list[VariantSearchResult]:
        """Backwards-compatible explicit rsID lookup."""
        return self.search(rsid, threshold=threshold, top_k=top_k)

    def star_lookup(
        self,
        star_allele: str,
        threshold: float = 0.8,
        top_k: int = 1,
    ) -> list[VariantSearchResult]:
        """Backwards-compatible explicit haplotype lookup."""
        return pgkb_star_allele_search(
            star_allele,
            threshold=threshold,
            top_k=top_k,
        )
