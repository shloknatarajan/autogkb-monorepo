"""Fetch PubMed abstracts for a list of PMIDs, with an append-only JSONL cache."""

from __future__ import annotations

import json
import time
from pathlib import Path

from shared.triage_scoring import fetch_pubmed_abstract

CACHE_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "litsuggest" / "abstracts_cache.jsonl"
)


def load_cache(path: Path) -> dict[str, dict]:
    """Read the JSONL cache and return a {pmid: record} dict."""
    if not path.exists():
        return {}
    cache: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            record = json.loads(line)
            cache[record["pmid"]] = record
    return cache


def fetch_abstracts(
    pmids: list[str],
    ncbi_email: str,
    cache_path: Path = CACHE_PATH,
    delay: float = 0.4,
    verbose: bool = True,
) -> dict[str, dict]:
    """Return a {pmid: abstract_record} dict for all pmids.

    Already-cached PMIDs are skipped. New results are appended to cache_path.
    delay (seconds) is applied between NCBI requests to respect rate limits.
    """
    cache = load_cache(cache_path)
    to_fetch = [p for p in pmids if p not in cache]

    if verbose and to_fetch:
        print(f"Fetching {len(to_fetch)} abstracts "
              f"({len(pmids) - len(to_fetch)} already cached)…")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "a", encoding="utf-8") as fh:
        for i, pmid in enumerate(to_fetch):
            if verbose and (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(to_fetch)}")
            result = fetch_pubmed_abstract(pmid, ncbi_email)
            entry = {"pmid": pmid, **result}
            fh.write(json.dumps(entry) + "\n")
            cache[pmid] = entry
            if i < len(to_fetch) - 1:
                time.sleep(delay)

    return cache
