"""Async-paced PMC full-text fetcher backed by ``pubmed-markdown>=0.4.0``.

``pubmed-markdown`` natively handles multi-tier PMC retrieval (NCBI E-utilities
``efetch`` JATS XML -> Europe PMC ``fullTextXML`` -> PMC Web HTML), JATS/HTML to
Markdown conversion, HTTP retries with backoff/``Retry-After``, host-aware rate
limiting, AWS WAF/CAPTCHA detection, BioC supplementary material enrichment,
``AsyncRateGate`` concurrency/interval gating, and structured ``FetchResult`` /
``TierError`` diagnostics.

This module provides:
- App-level lazy configuration for ``PubMedMarkdown`` and ``AsyncRateGate``
  (respecting ``NCBI_EMAIL``, ``NCBI_API_KEY``, ``PMC_FETCH_SOURCE``,
  ``PMC_FETCH_MAX_CONCURRENCY``, and ``PMC_FETCH_MIN_INTERVAL_SEC``).
- Defense-in-depth Markdown validation (``validate_article_markdown`` /
  ``has_valid_article_markdown``) for freshly fetched, cached, and
  browser-supplied article text.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Callable, Optional

from loguru import logger
from pubmed_markdown import AsyncRateGate, PubMedMarkdown

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

_downloader: PubMedMarkdown | None = None
_downloader_config: tuple[str | None, str | None, str] | None = None

_rate_gate: AsyncRateGate | None = None
_rate_gate_config: tuple[str | None, int | None, float | None] | None = None


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


def get_ncbi_rate_gate() -> AsyncRateGate:
    """Return a cached ``AsyncRateGate`` sized for NCBI quotas (with optional env overrides)."""
    global _rate_gate, _rate_gate_config
    ncbi_key = os.environ.get("NCBI_API_KEY") or None

    raw_concurrency = os.environ.get("PMC_FETCH_MAX_CONCURRENCY")
    concurrency = (
        max(1, int(raw_concurrency))
        if raw_concurrency is not None and raw_concurrency.strip()
        else None
    )

    raw_interval = os.environ.get("PMC_FETCH_MIN_INTERVAL_SEC")
    min_interval = (
        max(0.0, float(raw_interval))
        if raw_interval is not None and raw_interval.strip()
        else None
    )

    config = (ncbi_key, concurrency, min_interval)
    if _rate_gate is None or _rate_gate_config != config:
        _rate_gate = AsyncRateGate.for_ncbi(
            ncbi_key=ncbi_key,
            concurrency=concurrency,
            min_interval=min_interval,
        )
        _rate_gate_config = config
    return _rate_gate


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


async def fetch_pmcid_markdown(
    pmcid: str,
    *,
    include_supplements: bool = True,
    fetcher: Optional[Callable[[str, bool], Optional[str]]] = None,
) -> str:
    """Fetch and validate full-text Markdown for ``pmcid`` using ``pubmed-markdown``."""
    clean_pmcid = str(pmcid or "").strip().upper()
    if not re.fullmatch(r"PMC\d+", clean_pmcid):
        raise ValueError(f"Cannot fetch article content without a valid PMCID: {pmcid}")

    gate = get_ncbi_rate_gate()

    if fetcher is not None:
        try:
            async with gate:
                raw_md = await asyncio.to_thread(
                    fetcher, clean_pmcid, include_supplements
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[pmc:{clean_pmcid}] pubmed_markdown fetch failed: {exc}")
            raise ValueError(
                f"Invalid article content or failed to fetch full text for {clean_pmcid} "
                f"(pubmed_markdown fetch error: {exc}). "
                "If the publisher blocks automated download, upload the PDF directly."
            ) from exc
        source_label = "custom fetcher"
        error_summary: str | None = None
    else:
        try:
            result = await get_pubmed_downloader().afetch_pmcid(
                clean_pmcid,
                include_supplements=include_supplements,
                rate_gate=gate,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[pmc:{clean_pmcid}] pubmed_markdown fetch failed: {exc}")
            raise ValueError(
                f"Invalid article content or failed to fetch full text for {clean_pmcid} "
                f"(pubmed_markdown fetch error: {exc}). "
                "If the publisher blocks automated download, upload the PDF directly."
            ) from exc

        if not result.ok:
            summary = result.error_summary or "all PMC sources failed"
            logger.warning(
                f"[pmc:{clean_pmcid}] pubmed_markdown fetch failed: {summary}"
            )
            raise ValueError(
                f"Invalid article content or failed to fetch full text for {clean_pmcid} "
                f"({summary}). If the publisher blocks automated download, upload the PDF directly."
            )

        raw_md = result.markdown
        source_label = result.source or "pubmed_markdown"
        error_summary = result.error_summary

    try:
        validated = validate_article_markdown(raw_md, clean_pmcid)
    except ValueError as exc:
        detail = f"{exc}; {error_summary}" if error_summary else str(exc)
        logger.warning(f"[pmc:{clean_pmcid}] pubmed_markdown output rejected: {detail}")
        raise ValueError(
            f"Invalid article content or failed to fetch full text for {clean_pmcid} "
            f"({detail}). If the publisher blocks automated download, upload the PDF directly."
        ) from exc

    logger.info(
        f"[pmc:{clean_pmcid}] Fetched full text via {source_label} ({len(validated)} chars)"
    )
    return validated
