"""Ranked lookup over the official ClinPGx phenotype vocabulary."""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel


CLINPGX_URL = "https://www.clinpgx.org"

_SEARCH_QUERY_ALIASES = {
    "adr": "adverse events",
    "adrs": "adverse events",
    "adverse drug reaction": "adverse events",
    "adverse drug reactions": "adverse events",
}


class PhenotypeSearchResult(BaseModel):
    """A ranked official phenotype candidate."""

    raw_input: str
    normalized_term: str
    id: str | None = None
    url: str | None = None
    source: str
    match_type: str
    score: float
    matched_term: str | None = None
    external_vocabulary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        try:
            return self.model_dump()
        except AttributeError:  # pragma: no cover - Pydantic v1 fallback
            return self.dict()


@dataclass(frozen=True)
class _PhenotypeRecord:
    id: str
    name: str
    aliases: tuple[str, ...]
    external_vocabulary: str


@dataclass(frozen=True)
class _PhenotypeAlias:
    record: _PhenotypeRecord
    text: str
    normalized: str
    kind: str


@dataclass(frozen=True)
class _PhenotypeIndex:
    aliases: tuple[_PhenotypeAlias, ...]
    exact: dict[str, tuple[_PhenotypeAlias, ...]]


def normalize_phenotype_text(text: str) -> str:
    """Normalize typography and word separators for phenotype comparison."""
    value = unicodedata.normalize("NFKC", str(text)).strip().casefold()
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _parse_aliases(value: str) -> tuple[str, ...]:
    if not value or not value.strip():
        return ()
    try:
        values = next(csv.reader([value], skipinitialspace=True))
    except csv.Error:
        values = [value]
    return tuple(dict.fromkeys(item.strip().strip('"') for item in values if item.strip()))


@lru_cache(maxsize=8)
def _load_index(table_path: str) -> _PhenotypeIndex:
    path = Path(table_path)
    if not path.exists():
        raise FileNotFoundError(f"ClinPGx phenotype table not found: {path}")
    aliases: list[_PhenotypeAlias] = []
    exact: dict[str, list[_PhenotypeAlias]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            identifier = (row.get("PharmGKB Accession Id") or "").strip()
            name = (row.get("Name") or "").strip()
            if not identifier or not name:
                continue
            record = _PhenotypeRecord(
                id=identifier,
                name=name,
                aliases=_parse_aliases(row.get("Alternate Names") or ""),
                external_vocabulary=(row.get("External Vocabulary") or "").strip(),
            )
            terms = [("canonical", record.name)]
            terms.extend(("alias", alias) for alias in record.aliases)
            for kind, term in terms:
                normalized = normalize_phenotype_text(term)
                if not normalized:
                    continue
                item = _PhenotypeAlias(record, term, normalized, kind)
                aliases.append(item)
                exact.setdefault(normalized, []).append(item)
    return _PhenotypeIndex(
        aliases=tuple(aliases),
        exact={key: tuple(values) for key, values in exact.items()},
    )


def clear_phenotype_index_cache() -> None:
    _load_index.cache_clear()


def _token_score(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _similarity(query: str, candidate: str) -> float:
    sequence = SequenceMatcher(None, query, candidate).ratio()
    token = _token_score(query, candidate)
    containment = min(len(query), len(candidate)) / max(len(query), len(candidate))
    query_contains = re.search(rf"\b{re.escape(candidate)}\b", query) is not None
    candidate_contains = re.search(rf"\b{re.escape(query)}\b", candidate) is not None
    if query_contains or candidate_contains:
        return max(sequence, 0.75 + 0.25 * containment)
    return max(sequence, token)


class PhenotypeLookup:
    """Search the bundled official ClinPGx phenotype table."""

    def __init__(self, data_dir: str | Path = Path("data")):
        self.table_path = Path(data_dir) / "term_lookup_info" / "phenotypes.tsv"

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.5,
    ) -> list[PhenotypeSearchResult]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        normalized = normalize_phenotype_text(query)
        if not normalized:
            return []
        normalized = _SEARCH_QUERY_ALIASES.get(normalized, normalized)
        index = _load_index(str(self.table_path.resolve()))
        best: dict[str, tuple[float, _PhenotypeAlias, str]] = {}

        exact = index.exact.get(normalized, ())
        if exact:
            for alias in exact:
                match_type = f"exact_{alias.kind}"
                current = best.get(alias.record.id)
                if current is None or alias.kind == "canonical":
                    best[alias.record.id] = (1.0, alias, match_type)
        else:
            # Short, unknown acronyms produce convincing but meaningless fuzzy
            # matches (for example, ADR -> DR). Only search these when they are
            # an exact official alias or one of the explicit expansions above.
            if len(normalized) <= 4 and " " not in normalized:
                return []
            for alias in index.aliases:
                score = _similarity(normalized, alias.normalized)
                if score < min_score:
                    continue
                current = best.get(alias.record.id)
                if current is None or score > current[0]:
                    best[alias.record.id] = (score, alias, f"fuzzy_{alias.kind}")

        ranked = sorted(
            best.values(),
            key=lambda item: (
                item[0],
                item[1].kind == "canonical",
                -len(item[1].record.name),
            ),
            reverse=True,
        )[:top_k]
        return [
            PhenotypeSearchResult(
                raw_input=query,
                normalized_term=alias.record.name,
                id=alias.record.id,
                url=f"{CLINPGX_URL}/phenotype/{alias.record.id}",
                source="clinpgx",
                match_type=match_type,
                score=round(score, 6),
                matched_term=alias.text,
                external_vocabulary=alias.record.external_vocabulary or None,
            )
            for score, alias, match_type in ranked
        ]


def search_phenotypes(
    query: str,
    *,
    data_dir: str | Path = Path("data"),
    top_k: int = 5,
    min_score: float = 0.5,
) -> list[PhenotypeSearchResult]:
    """Return ranked official candidates for a proposed phenotype string."""
    return PhenotypeLookup(data_dir=data_dir).search(
        query, top_k=top_k, min_score=min_score
    )


_FUNCTIONAL_PHENOTYPES = {
    "poor metabolizer": "Poor Metabolizer",
    "intermediate metabolizer": "Intermediate Metabolizer",
    "normal metabolizer": "Normal Metabolizer",
    "extensive metabolizer": "Normal Metabolizer",
    "rapid metabolizer": "Rapid Metabolizer",
    "ultrarapid metabolizer": "Ultrarapid Metabolizer",
    "ultra rapid metabolizer": "Ultrarapid Metabolizer",
    "indeterminate metabolizer": "Indeterminate Metabolizer",
    "poor function": "Poor Function",
    "decreased function": "Decreased Function",
    "normal function": "Normal Function",
    "increased function": "Increased Function",
    "no function": "No Function",
    "uncertain function": "Uncertain Function",
}

_FUNCTIONAL_ABBREVIATIONS = {
    "pm": "Poor Metabolizer",
    "im": "Intermediate Metabolizer",
    "nm": "Normal Metabolizer",
    "em": "Normal Metabolizer",
    "rm": "Rapid Metabolizer",
    "um": "Ultrarapid Metabolizer",
}

_FUNCTIONAL_DESCRIPTORS = {
    "poor": "Poor Metabolizer",
    "intermediate": "Intermediate Metabolizer",
    "normal": "Normal Metabolizer",
    "extensive": "Normal Metabolizer",
    "rapid": "Rapid Metabolizer",
    "ultrarapid": "Ultrarapid Metabolizer",
    "ultra rapid": "Ultrarapid Metabolizer",
    "indeterminate": "Indeterminate Metabolizer",
}


def normalize_functional_phenotype(text: str) -> PhenotypeSearchResult | None:
    """Normalize a CPIC-style metabolizer or function phenotype."""
    prepared = re.sub(r"\s+", " ", text.strip()).casefold()
    prepared = prepared.replace("metaboliser", "metabolizer")
    prepared = re.sub(r"\bmetabolizers\b", "metabolizer", prepared)
    modifier = ""
    for prefix in ("likely ", "possible "):
        if prepared.startswith(prefix):
            modifier = prefix.title()
            prepared = prepared[len(prefix) :]
            break
    parenthetical = re.fullmatch(r"(.+?)\s*\(([a-z]{2})\)", prepared)
    if parenthetical and parenthetical.group(2) in _FUNCTIONAL_ABBREVIATIONS:
        prepared = parenthetical.group(1).strip()
    normalized = _FUNCTIONAL_PHENOTYPES.get(prepared)
    if normalized is None:
        normalized = _FUNCTIONAL_ABBREVIATIONS.get(prepared)
    if normalized is None:
        normalized = _FUNCTIONAL_DESCRIPTORS.get(prepared)
    if normalized is None:
        return None
    return PhenotypeSearchResult(
        raw_input=text,
        normalized_term=f"{modifier}{normalized}",
        source="cpic",
        match_type="standardized_functional_phenotype",
        score=1.0,
    )
