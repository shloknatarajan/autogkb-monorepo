"""Canonical gene-symbol normalization against the local ClinPGx gene table."""

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


CLINPGX_GENE_URL = "https://www.clinpgx.org/gene"
_SYMBOL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9-])[A-Za-z][A-Za-z0-9-]{1,19}(?![A-Za-z0-9-])"
)


class GeneSearchResult(BaseModel):
    """A grounded mapping to a ClinPGx gene record."""

    raw_input: str
    id: str
    normalized_term: str
    url: str
    score: float
    matched_term: str | None = None
    match_type: str = "unknown"
    ambiguous: bool = False
    source: str = "pharmgkb"
    ncbi_gene_id: str | None = None
    hgnc_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        try:
            return self.model_dump()
        except AttributeError:  # pragma: no cover - Pydantic v1 fallback
            return self.dict()


@dataclass(frozen=True)
class _GeneRecord:
    id: str
    symbol: str
    name: str
    alternate_names: tuple[str, ...]
    alternate_symbols: tuple[str, ...]
    ncbi_gene_id: str | None
    hgnc_id: str | None


@dataclass(frozen=True)
class _GeneAlias:
    record: _GeneRecord
    text: str
    normalized: str
    kind: str
    priority: int


@dataclass(frozen=True)
class _GeneIndex:
    aliases: tuple[_GeneAlias, ...]
    exact: dict[str, tuple[_GeneAlias, ...]]
    symbols: frozenset[str]


def normalize_gene_text(text: str) -> str:
    """Normalize a gene symbol, name, or alias for comparison."""
    normalized = unicodedata.normalize("NFKD", str(text)).casefold()
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^a-z0-9-]+", " ", normalized)
    return " ".join(normalized.split())


def _split_values(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parts = next(csv.reader([value], skipinitialspace=True))
    return tuple(part.strip() for part in parts if part.strip())


@lru_cache(maxsize=8)
def _load_gene_index(path_string: str, modified_ns: int) -> _GeneIndex:
    del modified_ns
    aliases: list[_GeneAlias] = []
    exact: dict[str, list[_GeneAlias]] = {}
    symbols: set[str] = set()

    with Path(path_string).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            identifier = (row.get("PharmGKB Accession Id") or "").strip()
            symbol = (row.get("Symbol") or "").strip()
            if not identifier or not symbol:
                continue
            record = _GeneRecord(
                id=identifier,
                symbol=symbol,
                name=(row.get("Name") or "").strip(),
                alternate_names=_split_values(row.get("Alternate Names")),
                alternate_symbols=_split_values(row.get("Alternate Symbols")),
                ncbi_gene_id=(row.get("NCBI Gene ID") or "").strip() or None,
                hgnc_id=(row.get("HGNC ID") or "").strip() or None,
            )
            values = [
                (record.symbol, "symbol", 0),
                *((value, "alternate_symbol", 1) for value in record.alternate_symbols),
                (record.name, "name", 2),
                *((value, "alternate_name", 3) for value in record.alternate_names),
            ]
            seen: set[str] = set()
            for text, kind, priority in values:
                normalized = normalize_gene_text(text)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                alias = _GeneAlias(record, text, normalized, kind, priority)
                aliases.append(alias)
                exact.setdefault(normalized, []).append(alias)
            symbols.add(record.symbol.upper())
            symbols.update(value.upper() for value in record.alternate_symbols)

    return _GeneIndex(
        aliases=tuple(aliases),
        exact={key: tuple(value) for key, value in exact.items()},
        symbols=frozenset(symbols),
    )


def clear_gene_index_cache() -> None:
    _load_gene_index.cache_clear()


def _index_for(path: Path) -> _GeneIndex:
    resolved = path.resolve()
    return _load_gene_index(str(resolved), resolved.stat().st_mtime_ns)


class GeneLookup(BaseModel):
    """Resolve official gene symbols, aliases, and names to ClinPGx records."""

    data_dir: Path = Path("data")

    def _data_path(self) -> Path:
        return self.data_dir / "term_lookup_info" / "genes.tsv"

    def search(
        self,
        gene: str,
        threshold: float = 0.85,
        top_k: int = 1,
        ambiguity_margin: float = 0.03,
    ) -> list[GeneSearchResult]:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if ambiguity_margin < 0:
            raise ValueError("ambiguity_margin cannot be negative")

        query = normalize_gene_text(gene)
        if not query:
            return []
        index = _index_for(self._data_path())
        best_by_id: dict[str, tuple[_GeneAlias, float]] = {}

        exact = index.exact.get(query, ())
        if exact:
            for alias in exact:
                current = best_by_id.get(alias.record.id)
                if current is None or alias.priority < current[0].priority:
                    best_by_id[alias.record.id] = (alias, 1.0)
        elif len(query) >= 3:
            effective_threshold = max(threshold, 0.9) if len(query) < 6 else threshold
            for alias in index.aliases:
                if alias.normalized[0] != query[0]:
                    continue
                if abs(len(alias.normalized) - len(query)) > max(3, len(query) // 2):
                    continue
                score = SequenceMatcher(None, query, alias.normalized).ratio()
                if score < effective_threshold:
                    continue
                current = best_by_id.get(alias.record.id)
                if current is None or (score, -alias.priority) > (
                    current[1],
                    -current[0].priority,
                ):
                    best_by_id[alias.record.id] = (alias, score)

        ranked = sorted(
            best_by_id.values(),
            key=lambda item: (
                -item[1],
                item[0].priority,
                item[0].record.symbol.casefold(),
            ),
        )
        ambiguous = (
            len(ranked) > 1 and ranked[0][1] - ranked[1][1] <= ambiguity_margin
        )
        return [
            GeneSearchResult(
                raw_input=gene,
                id=alias.record.id,
                normalized_term=alias.record.symbol,
                url=f"{CLINPGX_GENE_URL}/{alias.record.id}",
                score=score,
                matched_term=alias.text,
                match_type=(
                    f"exact_{alias.kind}" if score == 1 else f"fuzzy_{alias.kind}"
                ),
                ambiguous=ambiguous,
                ncbi_gene_id=alias.record.ncbi_gene_id,
                hgnc_id=alias.record.hgnc_id,
            )
            for alias, score in ranked[:top_k]
        ]

    def symbol_candidates(self, text: str) -> list[str]:
        """Return uppercase official/alternate symbols literally present in text."""
        index = _index_for(self._data_path())
        candidates: list[str] = []
        for match in _SYMBOL_TOKEN_RE.finditer(text):
            token = match.group()
            if token != token.upper() or len(token) < 3:
                continue
            if token in index.symbols and token not in candidates:
                candidates.append(token)
        return candidates
