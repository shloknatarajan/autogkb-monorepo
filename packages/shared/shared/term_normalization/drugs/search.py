"""Deterministic drug-name normalization against the local ClinPGx table."""

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


CLINPGX_CHEMICAL_URL = "https://www.clinpgx.org/chemical"

_STRENGTH_RE = re.compile(
    r"(?<!\w)\d+(?:\.\d+)?\s*"
    r"(?:mg|mcg|ug|g|kg|ml|l|units?|iu|meq|mmol|%)"
    r"(?:\s*/\s*(?:ml|l|kg|dose|tablet|capsule))?(?=\s|$|[^\w])",
    re.IGNORECASE,
)
_FORMULATION_RE = re.compile(
    r"\b(?:"
    r"extended[- ]release|immediate[- ]release|delayed[- ]release|"
    r"sustained[- ]release|controlled[- ]release|modified[- ]release|"
    r"oral|intravenous|intramuscular|subcutaneous|topical|transdermal|"
    r"ophthalmic|otic|nasal|rectal|sublingual|buccal|inhaled|"
    r"tablets?|capsules?|caplets?|injections?|solutions?|suspensions?|"
    r"syrups?|elixirs?|creams?|ointments?|gels?|patches?|sprays?|"
    r"drops?|powders?|granules?|suppositor(?:y|ies)|"
    r"xr|er|sr|cr|dr|ir|iv|im"
    r")\b",
    re.IGNORECASE,
)


class DrugSearchResult(BaseModel):
    """A candidate mapping from input text to a ClinPGx chemical."""

    raw_input: str
    id: str
    normalized_term: str
    url: str
    score: float
    matched_term: str | None = None
    match_type: str = "unknown"
    ambiguous: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for Pydantic v1 or v2."""
        try:
            return self.model_dump()
        except AttributeError:  # pragma: no cover - Pydantic v1 fallback
            return self.dict()


@dataclass(frozen=True)
class _DrugRecord:
    id: str
    name: str
    generic_names: tuple[str, ...]
    trade_names: tuple[str, ...]
    rxnorm_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Alias:
    record: _DrugRecord
    text: str
    normalized: str
    kind: str
    priority: int


@dataclass(frozen=True)
class _DrugIndex:
    aliases: tuple[_Alias, ...]
    exact: dict[str, tuple[_Alias, ...]]
    rxnorm: dict[str, tuple[_DrugRecord, ...]]


def normalize_drug_text(text: str, *, strip_formulation: bool = False) -> str:
    """Normalize drug text for comparison while retaining salts and ingredients."""
    normalized = unicodedata.normalize("NFKD", str(text)).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace("µ", "u").replace("μ", "u")
    normalized = re.sub(r"[®™©]", "", normalized)
    if strip_formulation:
        normalized = _STRENGTH_RE.sub(" ", normalized)
        normalized = _FORMULATION_RE.sub(" ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def _query_forms(text: str) -> tuple[str, ...]:
    candidates = [
        normalize_drug_text(text),
        normalize_drug_text(text, strip_formulation=True),
    ]

    # Also evaluate parenthetical aliases, e.g. "warfarin (Coumadin)".
    for parenthetical in re.findall(r"[\(\[]([^\)\]]+)[\)\]]", text):
        candidates.extend(
            [
                normalize_drug_text(parenthetical),
                normalize_drug_text(parenthetical, strip_formulation=True),
            ]
        )
    without_parenthetical = re.sub(r"[\(\[][^\)\]]+[\)\]]", " ", text)
    candidates.append(normalize_drug_text(without_parenthetical))

    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _split_values(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip().strip('"') for part in value.split(",") if part.strip())


def _split_rxnorm_ids(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(dict.fromkeys(re.findall(r"\d+", value)))


@lru_cache(maxsize=8)
def _load_drug_index(path_string: str, modified_ns: int) -> _DrugIndex:
    """Load and index a drug table once per path and file revision."""
    del modified_ns  # Including this in the cache key invalidates changed files.
    records: list[_DrugRecord] = []

    with Path(path_string).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            drug_id = (row.get("PharmGKB Accession Id") or "").strip()
            name = (row.get("Name") or "").strip()
            if not drug_id or not name:
                continue
            records.append(
                _DrugRecord(
                    id=drug_id,
                    name=name,
                    generic_names=_split_values(row.get("Generic Names")),
                    trade_names=_split_values(row.get("Trade Names")),
                    rxnorm_ids=_split_rxnorm_ids(row.get("RxNorm Identifiers")),
                )
            )

    aliases: list[_Alias] = []
    exact: dict[str, list[_Alias]] = {}
    rxnorm: dict[str, list[_DrugRecord]] = {}

    for record in records:
        values = [
            (record.name, "canonical", 0),
            *((value, "generic", 1) for value in record.generic_names),
            *((value, "trade", 2) for value in record.trade_names),
        ]
        seen_aliases: set[tuple[str, str]] = set()
        for text, kind, priority in values:
            normalized = normalize_drug_text(text)
            key = (normalized, record.id)
            if not normalized or key in seen_aliases:
                continue
            seen_aliases.add(key)
            alias = _Alias(record, text, normalized, kind, priority)
            aliases.append(alias)
            exact.setdefault(normalized, []).append(alias)

        for rxcui in record.rxnorm_ids:
            rxnorm.setdefault(rxcui, []).append(record)

    return _DrugIndex(
        aliases=tuple(aliases),
        exact={key: tuple(value) for key, value in exact.items()},
        rxnorm={key: tuple(value) for key, value in rxnorm.items()},
    )


def clear_drug_index_cache() -> None:
    """Clear cached tables, primarily for tests and refreshed source data."""
    _load_drug_index.cache_clear()


def _index_for(path: Path) -> _DrugIndex:
    resolved = path.resolve()
    return _load_drug_index(str(resolved), resolved.stat().st_mtime_ns)


def _similarity(query: str, candidate: str) -> float:
    direct = SequenceMatcher(None, query, candidate).ratio()
    query_tokens = " ".join(sorted(query.split()))
    candidate_tokens = " ".join(sorted(candidate.split()))
    token_sorted = SequenceMatcher(None, query_tokens, candidate_tokens).ratio()
    return max(direct, token_sorted)


def _best_aliases(
    index: _DrugIndex,
    query_forms: tuple[str, ...],
    threshold: float,
) -> list[tuple[_Alias, float, str]]:
    # Exact canonical and alias matches always take precedence over fuzzy ones.
    exact_by_id: dict[str, tuple[_Alias, float, str]] = {}
    for query in query_forms:
        for alias in index.exact.get(query, ()):
            current = exact_by_id.get(alias.record.id)
            candidate = (alias, 1.0, f"exact_{alias.kind}")
            if current is None or alias.priority < current[0].priority:
                exact_by_id[alias.record.id] = candidate
    if exact_by_id:
        return sorted(
            exact_by_id.values(),
            key=lambda item: (item[0].priority, item[0].record.name.casefold()),
        )

    # Short fuzzy strings create too many false positives. Acronyms still work
    # through the exact alias index above.
    fuzzy_queries = tuple(query for query in query_forms if len(query) >= 4)
    if not fuzzy_queries:
        return []

    effective_threshold = (
        max(threshold, 0.9) if max(map(len, fuzzy_queries)) < 6 else threshold
    )
    best_by_id: dict[str, tuple[_Alias, float, str]] = {}

    for alias in index.aliases:
        score = max(_similarity(query, alias.normalized) for query in fuzzy_queries)
        if score < effective_threshold:
            continue
        current = best_by_id.get(alias.record.id)
        if current is None or (score, -alias.priority) > (
            current[1],
            -current[0].priority,
        ):
            best_by_id[alias.record.id] = (
                alias,
                score,
                f"fuzzy_{alias.kind}",
            )

    return sorted(
        best_by_id.values(),
        key=lambda item: (
            -item[1],
            item[0].priority,
            item[0].record.name.casefold(),
        ),
    )


def _to_result(
    raw_input: str,
    candidate: tuple[_Alias, float, str],
    *,
    ambiguous: bool,
) -> DrugSearchResult:
    alias, score, match_type = candidate
    return DrugSearchResult(
        raw_input=raw_input,
        id=alias.record.id,
        normalized_term=alias.record.name,
        url=f"{CLINPGX_CHEMICAL_URL}/{alias.record.id}",
        score=score,
        matched_term=alias.text,
        match_type=match_type,
        ambiguous=ambiguous,
    )


def get_first_rxnorm_candidate(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first candidate explicitly sourced from RxNorm."""
    candidates = data.get("approximateGroup", {}).get("candidate", [])
    return next(
        (candidate for candidate in candidates if candidate.get("source") == "RXNORM"),
        None,
    )


def rxnorm_search(drug_name: str) -> DrugSearchResult | None:
    """Use RxNorm only as a guarded fallback for locally unresolved text."""
    try:
        response = requests.get(
            "https://rxnav.nlm.nih.gov/REST/approximateTerm.json",
            params={"term": drug_name, "maxEntries": 1},
            timeout=5,
        )
        response.raise_for_status()
        candidate = get_first_rxnorm_candidate(response.json())
    except (requests.RequestException, ValueError) as error:
        logger.warning(f"RxNorm lookup failed for '{drug_name}': {error}")
        return None

    if not candidate or not candidate.get("rxcui") or not candidate.get("name"):
        return None

    name = str(candidate["name"])
    return DrugSearchResult(
        raw_input=drug_name,
        id=f"RXN{candidate['rxcui']}",
        normalized_term=name,
        url=f"https://rxnav.nlm.nih.gov/REST/rxcui/{candidate['rxcui']}",
        score=_similarity(normalize_drug_text(drug_name), normalize_drug_text(name)),
        matched_term=name,
        match_type="rxnorm",
    )


class DrugLookup(BaseModel):
    """Resolve drug text to canonical ClinPGx names and PharmGKB IDs."""

    data_dir: Path = Path("data")
    raw_input: str = ""

    def _data_path(self) -> Path:
        return self.data_dir / "term_lookup_info" / "drugs.tsv"

    def clinpgx_lookup(
        self,
        drug_name: str,
        threshold: float = 0.8,
        top_k: int = 1,
        ambiguity_margin: float = 0.03,
    ) -> list[DrugSearchResult]:
        """Search canonical, generic, and trade names in the local table."""
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if ambiguity_margin < 0:
            raise ValueError("ambiguity_margin cannot be negative")

        query_forms = _query_forms(drug_name)
        if not query_forms:
            return []

        candidates = _best_aliases(
            _index_for(self._data_path()), query_forms, threshold
        )
        ambiguous = (
            len(candidates) > 1
            and candidates[0][1] - candidates[1][1] <= ambiguity_margin
        )
        return [
            _to_result(drug_name, candidate, ambiguous=ambiguous)
            for candidate in candidates[:top_k]
        ]

    def rxcui_to_pa_id(
        self,
        rxcui: str,
        *,
        raw_input: str | None = None,
    ) -> list[DrugSearchResult]:
        """Map an RxCUI to ClinPGx records using exact identifier tokens."""
        normalized_rxcui = rxcui.upper().removeprefix("RXN").strip()
        if not normalized_rxcui.isdigit():
            return []

        records = _index_for(self._data_path()).rxnorm.get(normalized_rxcui, ())
        ambiguous = len(records) > 1
        return [
            DrugSearchResult(
                raw_input=raw_input or rxcui,
                id=record.id,
                normalized_term=record.name,
                url=f"{CLINPGX_CHEMICAL_URL}/{record.id}",
                score=1.0,
                matched_term=normalized_rxcui,
                match_type="exact_rxnorm",
                ambiguous=ambiguous,
            )
            for record in records
        ]

    def rxnorm_lookup(self, drug_name: str) -> list[DrugSearchResult]:
        """Resolve through RxNorm, then map its RxCUI exactly to ClinPGx."""
        rxnorm_result = rxnorm_search(drug_name)
        if not rxnorm_result:
            return []
        return self.rxcui_to_pa_id(rxnorm_result.id, raw_input=drug_name)

    def search(
        self,
        drug_name: str,
        threshold: float = 0.8,
        top_k: int = 1,
        ambiguity_margin: float = 0.03,
        use_rxnorm_fallback: bool = True,
    ) -> list[DrugSearchResult]:
        """Return ranked local candidates, optionally falling back to RxNorm."""
        self.raw_input = drug_name
        results = self.clinpgx_lookup(
            drug_name,
            threshold=threshold,
            top_k=top_k,
            ambiguity_margin=ambiguity_margin,
        )
        if results or not use_rxnorm_fallback or not drug_name.strip():
            return results

        logger.warning(f"No local ClinPGx match for '{drug_name}', trying RxNorm")
        return self.rxnorm_lookup(drug_name)[:top_k]
