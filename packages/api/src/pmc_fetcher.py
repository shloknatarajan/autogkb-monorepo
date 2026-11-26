"""Async-paced PMC full-text fetcher backed by ``pubmed-markdown>=0.3.0``.

``pubmed-markdown`` natively handles multi-tier PMC retrieval (NCBI E-utilities
``efetch`` JATS XML -> Europe PMC ``fullTextXML`` -> PMC Web HTML), JATS/HTML to
Markdown conversion, HTTP retries with backoff/``Retry-After``, AWS WAF/CAPTCHA
detection, and BioC supplementary material enrichment.

This module adds:
- Async concurrency and minimum-interval pacing across concurrent API requests
  and background jobs (e.g. "Submit all") to avoid tripping NCBI rate limits.
- Defense-in-depth Markdown validation (``validate_article_markdown`` /
  ``has_valid_article_markdown``) for freshly fetched, cached, and
  browser-supplied article text.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Callable, Optional

from loguru import logger
from pubmed_markdown import PubMedMarkdown

# ---------------------------------------------------------------------------
# Validation / CAPTCHA & WAF challenge detection
# ---------------------------------------------------------------------------

_INVALID_ARTICLE_MARKERS = (
    "checking your browser - recaptcha",
    "checking your browser – recaptcha",
    "verify you are human",
    "http 404 [not found]",
    "/pmc/articles//",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "please wait while we verify",
    "checking if the site connection is secure",
    "<title>403</title>",
    "403 forbidden",
    "captcha-delivery",
    "aws waf",
)

_DEFAULT_CONCURRENCY_NO_KEY = 3
_DEFAULT_CONCURRENCY_WITH_KEY = 8
_DEFAULT_MIN_INTERVAL_NO_KEY = 0.35
_DEFAULT_MIN_INTERVAL_WITH_KEY = 0.12

_fetch_semaphore: asyncio.Semaphore | None = None
_fetch_rate_lock: asyncio.Lock | None = None
_last_fetch_monotonic: float = 0.0
_bound_loop_id: int | None = None
_bound_concurrency: int | None = None

_downloader: PubMedMarkdown | None = None
_downloader_config: tuple[str | None, str | None, str] | None = None


def _get_pmc_max_concurrency() -> int:
    """Resolve max concurrent PMC fetches (env override or NCBI_API_KEY-aware default)."""
    raw = os.environ.get("PMC_FETCH_MAX_CONCURRENCY")
    if raw is not None and raw.strip():
        return max(1, int(raw))
    return (
        _DEFAULT_CONCURRENCY_WITH_KEY
        if os.environ.get("NCBI_API_KEY")
        else _DEFAULT_CONCURRENCY_NO_KEY
    )


def _get_pmc_min_interval_seconds() -> float:
    """Resolve minimum dispatch spacing (env override or NCBI_API_KEY-aware default)."""
    raw = os.environ.get("PMC_FETCH_MIN_INTERVAL_SEC")
    if raw is not None and raw.strip():
        return max(0.0, float(raw))
    return (
        _DEFAULT_MIN_INTERVAL_WITH_KEY
        if os.environ.get("NCBI_API_KEY")
        else _DEFAULT_MIN_INTERVAL_NO_KEY
    )


def get_pubmed_downloader() -> PubMedMarkdown:
    """Return a cached ``PubMedMarkdown`` instance matching current NCBI env config."""
    global _downloader, _downloader_config
    email = os.environ.get("NCBI_EMAIL") or None
    ncbi_key = os.environ.get("NCBI_API_KEY") or None
    source = os.environ.get("PMC_FETCH_SOURCE", "auto").strip() or "auto"
    config = (email, ncbi_key, source)
    if _downloader is None or _downloader_config != config:
        _downloader = PubMedMarkdown(
            email=email,
            ncbi_key=ncbi_key,
            source=source,
        )
        _downloader_config = config
    return _downloader


def _get_concurrency_primitives() -> tuple[asyncio.Semaphore, asyncio.Lock]:
    """Lazily bind semaphore/lock to the current running event loop and concurrency limit."""
    global _fetch_semaphore, _fetch_rate_lock, _bound_loop_id, _bound_concurrency
    loop_id = id(asyncio.get_running_loop())
    max_concurrency = _get_pmc_max_concurrency()
    if (
        _bound_loop_id != loop_id
        or _bound_concurrency != max_concurrency
        or _fetch_semaphore is None
        or _fetch_rate_lock is None
    ):
        _fetch_semaphore = asyncio.Semaphore(max_concurrency)
        _fetch_rate_lock = asyncio.Lock()
        _bound_loop_id = loop_id
        _bound_concurrency = max_concurrency
    return _fetch_semaphore, _fetch_rate_lock


async def _pace_ncbi_request() -> None:
    """Enforce a minimum spacing between outgoing PMC/NCBI fetch starts."""
    global _last_fetch_monotonic
    _, rate_lock = _get_concurrency_primitives()
    min_interval = _get_pmc_min_interval_seconds()
    async with rate_lock:
        now = time.monotonic()
        wait = min_interval - (now - _last_fetch_monotonic)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_fetch_monotonic = time.monotonic()


def validate_article_markdown(markdown: str | None, identifier: str) -> str:
    """Reject empty, implausibly short, or known CAPTCHA/WAF/error pages before analysis."""
    if not isinstance(markdown, str) or len(markdown.strip()) < 500:
        raise ValueError(f"Article content for {identifier} is missing or too short")

    preview = markdown[:5000].casefold()
    marker = next(
        (candidate for candidate in _INVALID_ARTICLE_MARKERS if candidate in preview),
        None,
    )
    if marker:
        raise ValueError(
            f"Invalid article content returned for {identifier}: detected {marker!r}"
        )
    return markdown


def has_valid_article_markdown(markdown: str | None, identifier: str) -> bool:
    """Return whether cached content is safe to reuse for pipeline analysis."""
    try:
        validate_article_markdown(markdown, identifier)
    except ValueError:
        return False
    return True


def _fetch_pmcid_markdown_sync(
    pmcid: str,
    *,
    include_supplements: bool = True,
    fetcher: Optional[Callable[[str, bool], Optional[str]]] = None,
) -> str:
    """Synchronous PMC full-text fetch via ``pubmed-markdown``. Raises ValueError on failure."""
    clean_pmcid = str(pmcid or "").strip().upper()
    if not re.fullmatch(r"PMC\d+", clean_pmcid):
        raise ValueError(f"Cannot fetch article content without a valid PMCID: {pmcid}")

    active_fetcher = fetcher or get_pubmed_downloader().pmcid_to_markdown

    try:
        raw_md = active_fetcher(clean_pmcid, include_supplements)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[pmc:{clean_pmcid}] pubmed_markdown fetch failed: {exc}")
        raise ValueError(
            f"Invalid article content or failed to fetch full text for {clean_pmcid} "
            f"(pubmed_markdown fetch error: {exc}). "
            "If the publisher blocks automated download, upload the PDF directly."
        ) from exc

    try:
        validated = validate_article_markdown(raw_md, clean_pmcid)
    except ValueError as exc:
        logger.warning(f"[pmc:{clean_pmcid}] pubmed_markdown output rejected: {exc}")
        raise ValueError(
            f"Invalid article content or failed to fetch full text for {clean_pmcid} "
            f"({exc}). If the publisher blocks automated download, upload the PDF directly."
        ) from exc

    logger.info(
        f"[pmc:{clean_pmcid}] Fetched full text via pubmed_markdown ({len(validated)} chars)"
    )
    return validated


async def fetch_pmcid_markdown(
    pmcid: str,
    *,
    include_supplements: bool = True,
    fetcher: Optional[Callable[[str, bool], Optional[str]]] = None,
) -> str:
    """Fetch and validate full-text Markdown for ``pmcid`` with concurrency pacing."""
    semaphore, _ = _get_concurrency_primitives()
    async with semaphore:
        await _pace_ncbi_request()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: _fetch_pmcid_markdown_sync(
                pmcid,
                include_supplements=include_supplements,
                fetcher=fetcher,
            ),
        )
