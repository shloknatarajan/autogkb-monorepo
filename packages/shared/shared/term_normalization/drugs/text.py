"""Extract and normalize known drug mentions from free text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from .search import DrugLookup, _index_for, normalize_drug_text


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_HARD_BOUNDARY_RE = re.compile(r"[.!?;:\n]")
_ABBREVIATION_AFTER_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)?\s*(?:mg|mcg|ug|g|ml|units?)\s*)?"
    r"\(\s*([A-Z][A-Z0-9-]{1,11})\s*\)"
)
class NormalizedDrug(TypedDict):
    normalized_term: str
    id: str
    url: str
    score: float
    matched_term: str | None
    match_type: str
    source: str


class DrugMention(TypedDict):
    count: int
    spans: list[list[int]]
    ambiguous: bool
    matches: list[NormalizedDrug]


@dataclass(frozen=True)
class _TextToken:
    normalized: str
    start: int
    end: int


@lru_cache(maxsize=16)
def _alias_sequences(
    path_string: str,
    modified_ns: int,
    min_single_token_length: int,
    include_trade_names: bool,
) -> dict[str, tuple[tuple[str, ...], ...]]:
    del modified_ns  # Including it in the key invalidates changed source data.
    index = _index_for(Path(path_string))
    by_first_token: dict[str, set[tuple[str, ...]]] = {}

    for normalized_alias, aliases in index.exact.items():
        if not include_trade_names and all(alias.kind == "trade" for alias in aliases):
            continue
        tokens = tuple(normalized_alias.split())
        if not tokens:
            continue
        if all(token.isdigit() for token in tokens):
            continue
        if len(tokens) == 1:
            if len(tokens[0]) < min_single_token_length or tokens[0].isdigit():
                continue
        by_first_token.setdefault(tokens[0], set()).add(tokens)

    return {
        first: tuple(
            sorted(
                sequences,
                key=lambda sequence: (
                    -len(sequence),
                    -sum(map(len, sequence)),
                    sequence,
                ),
            )
        )
        for first, sequences in by_first_token.items()
    }


def _tokens_from_text(text: str) -> list[_TextToken]:
    tokens: list[_TextToken] = []
    for match in _TOKEN_RE.finditer(text):
        normalized = normalize_drug_text(match.group())
        if normalized:
            tokens.append(_TextToken(normalized, match.start(), match.end()))
    return tokens


def _crosses_hard_boundary(
    text: str,
    tokens: list[_TextToken],
    start: int,
    length: int,
) -> bool:
    for index in range(start, start + length - 1):
        gap = text[tokens[index].end : tokens[index + 1].start]
        if _HARD_BOUNDARY_RE.search(gap):
            return True
    return False


def _serialized_matches(results) -> list[NormalizedDrug]:
    return [
        {
            "normalized_term": result.normalized_term,
            "id": result.id,
            "url": result.url,
            "score": result.score,
            "matched_term": result.matched_term,
            "match_type": result.match_type,
            "source": result.source,
        }
        for result in results
    ]


def _add_mention(
    mentions: dict[str, DrugMention],
    original: str,
    start: int,
    end: int,
    matches: list[NormalizedDrug],
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


def _add_contextual_abbreviations(
    text: str,
    mentions: dict[str, DrugMention],
) -> None:
    definitions: dict[str, DrugMention] = {}

    for entry in list(mentions.values()):
        for _, end in entry["spans"]:
            match = _ABBREVIATION_AFTER_RE.match(text[end : end + 40])
            if not match:
                continue
            abbreviation = match.group(1)
            alpha_prefix = re.match(r"([A-Z][A-Z-]*?)(?:\d+)?$", abbreviation)
            if not alpha_prefix or len(alpha_prefix.group(1).replace("-", "")) < 2:
                continue
            definitions[alpha_prefix.group(1)] = entry

    for prefix, source in definitions.items():
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(prefix)}\d*(?![A-Za-z0-9])")
        for match in pattern.finditer(text):
            original = match.group()
            contextual_matches = [
                {
                    **candidate,
                    "matched_term": original,
                    "match_type": "contextual_abbreviation",
                }
                for candidate in source["matches"]
            ]
            _add_mention(
                mentions,
                original,
                match.start(),
                match.end(),
                contextual_matches,
                ambiguous=source["ambiguous"],
            )


def normalize_drugs_in_text(
    text: str,
    *,
    lookup: DrugLookup | None = None,
    data_dir: str | Path = Path("data"),
    max_candidates: int = 5,
    min_single_token_length: int = 4,
    include_trade_names: bool = True,
    include_contextual_abbreviations: bool = True,
) -> dict[str, DrugMention]:
    """Map known drug mentions in free text to canonical terms and IDs.

    The returned dictionary is keyed by the original substring exactly as it
    appeared in ``text``. Repeated mentions share one entry with occurrence
    counts and character spans. Extraction uses exact known aliases and then
    delegates resolution to :class:`DrugLookup`; it intentionally does not run
    fuzzy matching across arbitrary prose, which would create false positives.
    """
    if max_candidates < 1:
        raise ValueError("max_candidates must be at least 1")
    if min_single_token_length < 1:
        raise ValueError("min_single_token_length must be at least 1")
    if not text.strip():
        return {}

    drug_lookup = lookup or DrugLookup(data_dir=Path(data_dir))
    data_path = drug_lookup._data_path()
    resolved_path = data_path.resolve()
    sequences = _alias_sequences(
        str(resolved_path),
        resolved_path.stat().st_mtime_ns,
        min_single_token_length,
        include_trade_names,
    )
    tokens = _tokens_from_text(text)
    mentions: dict[str, DrugMention] = {}

    index = 0
    while index < len(tokens):
        matched_length = 0
        for sequence in sequences.get(tokens[index].normalized, ()):
            end = index + len(sequence)
            if end > len(tokens):
                continue
            if tuple(token.normalized for token in tokens[index:end]) != sequence:
                continue
            if _crosses_hard_boundary(text, tokens, index, len(sequence)):
                continue
            matched_length = len(sequence)
            break

        if not matched_length:
            index += 1
            continue

        start_offset = tokens[index].start
        end_offset = tokens[index + matched_length - 1].end
        original = text[start_offset:end_offset]
        results = drug_lookup.clinpgx_lookup(original, top_k=max_candidates)
        if not results:
            index += matched_length
            continue

        _add_mention(
            mentions,
            original,
            start_offset,
            end_offset,
            _serialized_matches(results),
            ambiguous=any(result.ambiguous for result in results),
        )
        index += matched_length

    if include_contextual_abbreviations:
        _add_contextual_abbreviations(text, mentions)

    return mentions
