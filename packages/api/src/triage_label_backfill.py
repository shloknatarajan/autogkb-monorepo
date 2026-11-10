"""Backfill additive paper-type and PGx-information labels on legacy triage rows."""

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger

from .database import (
    get_triage_session,
    list_triage_sessions,
    update_triage_session_articles,
)
from .scoring import classify_additional_labels


LABEL_FIELDS = (
    "paper_types",
    "paper_type_reasoning",
    "pgx_information_types",
    "pgx_information_reasoning",
)


def needs_label_backfill(article: dict) -> bool:
    """Return whether a stored article predates one or more additive label fields."""
    return any(field not in article for field in LABEL_FIELDS)


async def backfill_triage_labels(
    *,
    model: str,
    concurrency: int = 4,
    session_batch_size: int = 5,
    classify: Callable[[str, str | None, str | None, str], dict]
    | None = None,
) -> dict[str, int]:
    """Classify legacy triage articles and persist labels without changing other data.

    PMIDs repeated across weekly sessions are classified once per run. Each session is
    saved as soon as all of its missing labels are ready, providing a natural checkpoint
    if a long-running backfill is interrupted.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if session_batch_size < 1:
        raise ValueError("session_batch_size must be at least 1")

    classifier = classify or classify_additional_labels
    session_summaries = list_triage_sessions()
    semaphore = asyncio.Semaphore(concurrency)
    result_cache: dict[str, asyncio.Task] = {}
    stats = {
        "sessions_scanned": 0,
        "sessions_updated": 0,
        "article_rows_updated": 0,
        "unique_papers_classified": 0,
    }

    async def classify_once(article: dict) -> dict:
        pmid = str(article.get("pmid") or "").strip()
        if not pmid:
            return {
                "paper_types": [],
                "paper_type_reasoning": "No PMID was stored for this article.",
                "pgx_information_types": [],
                "pgx_information_reasoning": "No PMID was stored for this article.",
            }
        cached = result_cache.get(pmid)
        if cached is not None:
            return await cached

        async def run() -> dict:
            async with semaphore:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None,
                    classifier,
                    pmid,
                    article.get("title"),
                    article.get("abstract"),
                    model,
                )

        task = asyncio.create_task(run())
        result_cache[pmid] = task
        stats["unique_papers_classified"] += 1
        return await task

    for batch_start in range(0, len(session_summaries), session_batch_size):
        loaded_sessions: list[tuple[str, list[dict[str, Any]], list[dict]]] = []
        batch_pending: list[dict] = []
        for summary in session_summaries[
            batch_start : batch_start + session_batch_size
        ]:
            session_id = str(summary["id"])
            session = get_triage_session(session_id)
            if session is None:
                continue
            stats["sessions_scanned"] += 1
            articles: list[dict[str, Any]] = session.get("articles") or []
            pending = [
                article for article in articles if needs_label_backfill(article)
            ]
            loaded_sessions.append((session_id, articles, pending))
            batch_pending.extend(pending)

        labels = await asyncio.gather(
            *(classify_once(article) for article in batch_pending)
        )
        for article, label_result in zip(batch_pending, labels):
            # Preserve any label fields that may already exist on partially migrated rows.
            for field in LABEL_FIELDS:
                if field not in article:
                    article[field] = label_result.get(
                        field, [] if field.endswith("types") else ""
                    )

        for session_id, articles, pending in loaded_sessions:
            if not pending:
                continue
            update_triage_session_articles(session_id, articles)
            stats["sessions_updated"] += 1
            stats["article_rows_updated"] += len(pending)
            logger.info(
                "Backfilled labels for {} article row(s) in triage session {}",
                len(pending),
                session_id,
            )

    return stats
