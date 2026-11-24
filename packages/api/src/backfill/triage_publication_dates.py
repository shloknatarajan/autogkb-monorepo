"""Backfill PubMed publication dates on legacy triage rows without rescoring."""

import os
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

import requests
from loguru import logger
from shared.triage_scoring import _parse_pubmed_publication_date

from ..database import (
    get_triage_session,
    list_triage_sessions,
    update_triage_session_articles,
)


PublicationDateFetcher = Callable[[list[str], str, int], dict[str, str | None]]


def needs_publication_date_backfill(article: dict) -> bool:
    """Return whether a stored article has no usable publication date."""
    value = article.get("publication_date")
    return not isinstance(value, str) or not value.strip()


def fetch_pubmed_publication_dates(
    pmids: list[str],
    ncbi_email: str,
    batch_size: int = 200,
) -> dict[str, str | None]:
    """Fetch publication dates for PMIDs using batched PubMed XML requests."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    dates: dict[str, str | None] = {}
    api_key = os.environ.get("NCBI_API_KEY")
    for start in range(0, len(pmids), batch_size):
        batch = pmids[start : start + batch_size]
        data = {
            "db": "pubmed",
            "id": ",".join(batch),
            "retmode": "xml",
            "email": ncbi_email,
            "tool": "AutoGKB",
        }
        if api_key:
            data["api_key"] = api_key
        response = requests.post(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            data=data,
            timeout=60,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        for article in root.findall(".//PubmedArticle"):
            pmid = (article.findtext(".//MedlineCitation/PMID") or "").strip()
            if pmid:
                dates[pmid] = _parse_pubmed_publication_date(article)
        logger.info(
            "Fetched PubMed dates for batch {}-{} of {} PMIDs",
            start + 1,
            min(start + len(batch), len(pmids)),
            len(pmids),
        )
    return dates


def backfill_triage_publication_dates(
    *,
    ncbi_email: str | None = None,
    apply: bool = False,
    batch_size: int = 200,
    fetch_dates: PublicationDateFetcher | None = None,
) -> dict[str, int | bool]:
    """Inventory or fill missing publication dates while preserving all other fields."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if apply and not ncbi_email:
        raise ValueError("NCBI_EMAIL is required when applying the backfill")

    loaded_sessions: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []
    unique_pmids: set[str] = set()
    stats: dict[str, int | bool] = {
        "dry_run": not apply,
        "sessions_scanned": 0,
        "sessions_needing_updates": 0,
        "sessions_updated": 0,
        "article_rows_needing_updates": 0,
        "article_rows_updated": 0,
        "unique_pmids_to_fetch": 0,
        "unique_dates_found": 0,
        "unique_dates_not_found": 0,
        "article_rows_without_pmids": 0,
    }

    for summary in list_triage_sessions():
        session_id = str(summary["id"])
        session = get_triage_session(session_id)
        if session is None:
            continue
        stats["sessions_scanned"] += 1
        articles: list[dict[str, Any]] = session.get("articles") or []
        pending = [
            article for article in articles if needs_publication_date_backfill(article)
        ]
        loaded_sessions.append((session_id, articles, pending))
        if not pending:
            continue
        stats["sessions_needing_updates"] += 1
        stats["article_rows_needing_updates"] += len(pending)
        for article in pending:
            pmid = str(article.get("pmid") or "").strip()
            if pmid:
                unique_pmids.add(pmid)
            else:
                stats["article_rows_without_pmids"] += 1

    stats["unique_pmids_to_fetch"] = len(unique_pmids)
    if not apply:
        return stats

    fetcher = fetch_dates or fetch_pubmed_publication_dates
    ordered_pmids = sorted(unique_pmids)
    dates = fetcher(ordered_pmids, ncbi_email or "", batch_size)
    stats["unique_dates_found"] = sum(bool(value) for value in dates.values())
    stats["unique_dates_not_found"] = len(unique_pmids) - stats["unique_dates_found"]

    for session_id, articles, pending in loaded_sessions:
        updated_rows = 0
        for article in pending:
            pmid = str(article.get("pmid") or "").strip()
            publication_date = dates.get(pmid)
            if publication_date:
                article["publication_date"] = publication_date
                updated_rows += 1
        if updated_rows == 0:
            continue
        update_triage_session_articles(session_id, articles)
        stats["sessions_updated"] += 1
        stats["article_rows_updated"] += updated_rows
        logger.info(
            "Backfilled publication dates for {} article row(s) in triage session {}",
            updated_rows,
            session_id,
        )

    return stats
