"""Run score_for_va on a list of articles, with an append-only JSONL cache."""

from __future__ import annotations

import json
from pathlib import Path

from shared.triage_scoring import score_for_va

CACHE_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "litsuggest" / "scores_cache.jsonl"
)


def _key(pmid: str, model: str) -> str:
    return f"{pmid}:{model}"


def load_scores_cache(path: Path) -> dict[str, dict]:
    """Read the JSONL scores cache and return a {pmid:model: record} dict."""
    if not path.exists():
        return {}
    cache: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            record = json.loads(line)
            cache[_key(record["pmid"], record["model"])] = record
    return cache


def score_articles(
    articles: list[tuple[str, str | None, str | None]],
    model: str,
    cache_path: Path = CACHE_PATH,
    verbose: bool = True,
) -> dict[str, dict]:
    """Score each (pmid, title, abstract) tuple using score_for_va.

    Results keyed by "pmid:model" are appended to cache_path on first run.
    Subsequent calls skip already-cached entries, making reruns cheap.

    Returns a {pmid:model: score_record} dict.
    """
    cache = load_scores_cache(cache_path)
    to_score = [(pmid, t, a) for pmid, t, a in articles if _key(pmid, model) not in cache]

    if verbose and to_score:
        print(f"Scoring {len(to_score)} articles with {model} "
              f"({len(articles) - len(to_score)} already cached)…")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "a", encoding="utf-8") as fh:
        for i, (pmid, title, abstract) in enumerate(to_score):
            if verbose and (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(to_score)}")
            result = score_for_va(pmid, title, abstract, model)
            entry = {"pmid": pmid, "model": model, **result}
            fh.write(json.dumps(entry) + "\n")
            cache[_key(pmid, model)] = entry

    return cache
