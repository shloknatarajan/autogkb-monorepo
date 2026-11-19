"""Backfill normalized entity tags on legacy triage rows without rescoring papers."""

import asyncio
import hashlib
from collections.abc import Callable
from typing import Any

from loguru import logger

from .database import (
    get_triage_session,
    list_triage_sessions,
    update_triage_session_articles,
)
from .jobs import extract_drug_tags, extract_gene_tags, extract_phenotype_tags


TAG_FIELDS = ("drug_tags", "gene_tags", "phenotype_tags")
TagExtractor = Callable[[str | None, str], list[dict]]
PhenotypeTagExtractor = Callable[
    [str | None, list[dict] | None, list[dict] | None, str], list[dict]
]


def needs_tag_backfill(article: dict) -> bool:
    """Return whether a stored article predates one or more tag fields."""
    return any(field not in article for field in TAG_FIELDS)


def _paper_key(article: dict) -> str | None:
    abstract = article.get("abstract")
    if not isinstance(abstract, str) or not abstract.strip():
        return None
    digest = hashlib.sha256(abstract.strip().encode()).hexdigest()
    pmid = str(article.get("pmid") or "").strip()
    return f"{pmid}:{digest}" if pmid else digest


async def backfill_triage_tags(
    *,
    model: str,
    apply: bool = False,
    concurrency: int = 4,
    session_batch_size: int = 5,
    drug_extractor: TagExtractor | None = None,
    gene_extractor: TagExtractor | None = None,
    phenotype_extractor: PhenotypeTagExtractor | None = None,
) -> dict[str, int | bool]:
    """Inventory or backfill missing triage tags while preserving stored fields.

    Repeated papers with the same abstract are extracted once per tag type. Sessions
    are checkpointed in small batches so an interrupted apply run can be resumed.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if session_batch_size < 1:
        raise ValueError("session_batch_size must be at least 1")

    extract_drugs = drug_extractor or extract_drug_tags
    extract_genes = gene_extractor or extract_gene_tags
    extract_phenotypes = phenotype_extractor or extract_phenotype_tags
    session_summaries = list_triage_sessions()
    loaded_sessions: list[tuple[str, list[dict[str, Any]], list[dict]]] = []
    drug_keys: set[str] = set()
    gene_keys: set[str] = set()
    phenotype_keys: set[str] = set()
    unique_keys: set[str] = set()
    stats: dict[str, int | bool] = {
        "dry_run": not apply,
        "sessions_scanned": 0,
        "sessions_needing_updates": 0,
        "sessions_updated": 0,
        "article_rows_needing_updates": 0,
        "article_rows_updated": 0,
        "unique_papers_needing_tags": 0,
        "drug_extractions_needed": 0,
        "gene_extractions_needed": 0,
        "phenotype_extractions_needed": 0,
        "articles_without_abstracts": 0,
    }

    for summary in session_summaries:
        session_id = str(summary["id"])
        session = get_triage_session(session_id)
        if session is None:
            continue
        stats["sessions_scanned"] += 1
        articles: list[dict[str, Any]] = session.get("articles") or []
        pending = [article for article in articles if needs_tag_backfill(article)]
        loaded_sessions.append((session_id, articles, pending))
        if not pending:
            continue
        stats["sessions_needing_updates"] += 1
        stats["article_rows_needing_updates"] += len(pending)
        for article in pending:
            key = _paper_key(article)
            if key is None:
                stats["articles_without_abstracts"] += 1
                continue
            unique_keys.add(key)
            if "drug_tags" not in article:
                drug_keys.add(key)
            if "gene_tags" not in article:
                gene_keys.add(key)
            if "phenotype_tags" not in article:
                phenotype_keys.add(key)

    stats["unique_papers_needing_tags"] = len(unique_keys)
    stats["drug_extractions_needed"] = len(drug_keys)
    stats["gene_extractions_needed"] = len(gene_keys)
    stats["phenotype_extractions_needed"] = len(phenotype_keys)
    if not apply:
        return stats

    semaphore = asyncio.Semaphore(concurrency)
    drug_cache: dict[str, asyncio.Task[list[dict]]] = {}
    gene_cache: dict[str, asyncio.Task[list[dict]]] = {}
    phenotype_cache: dict[str, asyncio.Task[list[dict]]] = {}

    async def extract_once(
        cache: dict[str, asyncio.Task[list[dict]]],
        key: str,
        extractor: TagExtractor,
        abstract: str,
    ) -> list[dict]:
        cached = cache.get(key)
        if cached is not None:
            return await cached

        async def run() -> list[dict]:
            async with semaphore:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, extractor, abstract, model)

        task = asyncio.create_task(run())
        cache[key] = task
        return await task

    async def tags_for(
        article: dict,
    ) -> tuple[list[dict] | None, list[dict] | None, list[dict] | None]:
        abstract = article.get("abstract")
        if not isinstance(abstract, str) or not abstract.strip():
            return (
                [] if "drug_tags" not in article else None,
                [] if "gene_tags" not in article else None,
                [] if "phenotype_tags" not in article else None,
            )
        key = _paper_key(article)
        assert key is not None
        drug_task = (
            extract_once(drug_cache, key, extract_drugs, abstract)
            if "drug_tags" not in article
            else None
        )
        gene_task = (
            extract_once(gene_cache, key, extract_genes, abstract)
            if "gene_tags" not in article
            else None
        )
        drug_tags, gene_tags = await asyncio.gather(
            drug_task or asyncio.sleep(0, result=None),
            gene_task or asyncio.sleep(0, result=None),
        )
        phenotype_tags = None
        if "phenotype_tags" not in article:
            cached = phenotype_cache.get(key)
            if cached is None:

                async def run_phenotypes() -> list[dict]:
                    async with semaphore:
                        loop = asyncio.get_running_loop()
                        return await loop.run_in_executor(
                            None,
                            extract_phenotypes,
                            abstract,
                            drug_tags
                            if drug_tags is not None
                            else article.get("drug_tags", []),
                            gene_tags
                            if gene_tags is not None
                            else article.get("gene_tags", []),
                            model,
                        )

                cached = asyncio.create_task(run_phenotypes())
                phenotype_cache[key] = cached
            phenotype_tags = await cached
        return drug_tags, gene_tags, phenotype_tags

    for batch_start in range(0, len(loaded_sessions), session_batch_size):
        batch = loaded_sessions[batch_start : batch_start + session_batch_size]
        batch_pending = [article for _, _, pending in batch for article in pending]
        results = await asyncio.gather(
            *(tags_for(article) for article in batch_pending)
        )
        for article, (drug_tags, gene_tags, phenotype_tags) in zip(
            batch_pending, results
        ):
            if "drug_tags" not in article:
                article["drug_tags"] = drug_tags or []
            if "gene_tags" not in article:
                article["gene_tags"] = gene_tags or []
            if "phenotype_tags" not in article:
                article["phenotype_tags"] = phenotype_tags or []

        for session_id, articles, pending in batch:
            if not pending:
                continue
            update_triage_session_articles(session_id, articles)
            stats["sessions_updated"] += 1
            stats["article_rows_updated"] += len(pending)
            logger.info(
                "Backfilled tags for {} article row(s) in triage session {}",
                len(pending),
                session_id,
            )

    return stats
