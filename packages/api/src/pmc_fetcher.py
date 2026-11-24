"""Resilient PMC full-text fetcher with API fallbacks, retries, and WAF/CAPTCHA detection.

Fetching PMC HTML directly from ``pmc.ncbi.nlm.nih.gov`` can trigger AWS WAF
reCAPTCHA challenges or HTTP 403 responses from cloud IPs and burst traffic (e.g.
"Submit all"). This module preserves ``pubmed_markdown``'s HTML-to-Markdown output
on normal requests while automatically recovering from CAPTCHAs/403s:

1. **PMC Web HTML (via ``pubmed_markdown`` — 1 primary attempt)** — preserves the
   exact ``PubMedHTMLToMarkdownConverter`` output whenever PMC HTML succeeds cleanly.
2. **NCBI E-utilities ``efetch`` (``db=pmc&retmode=xml``)** — CAPTCHA-free official
   programmatic API fallback; converts JATS XML to structured Markdown with retries
   on 429/5xx.
3. **Europe PMC ``fullTextXML``** — independent host serving JATS XML when NCBI
   ``efetch`` fails or omits full text.
4. **PMC Web HTML retry (with exponential backoff + jitter)** — final recovery path
   for publisher-restricted articles that disallow XML redistribution when the first
   HTML attempt hit a transient CAPTCHA/WAF challenge.

All network calls are paced through an asyncio semaphore and minimum-interval limiter
to prevent burst traffic from tripping NCBI rate limits.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
import xml.etree.ElementTree as ET
from typing import Callable, Optional

import requests
from loguru import logger
from pubmed_markdown.utils_bioc import format_supplement_as_markdown

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

_EUTILS_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_EUROPE_PMC_XML_URL = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
)

_PMC_MAX_CONCURRENCY = int(os.environ.get("PMC_FETCH_MAX_CONCURRENCY", "2"))
_PMC_MIN_INTERVAL_SECONDS = float(os.environ.get("PMC_FETCH_MIN_INTERVAL_SEC", "0.5"))
_EFETCH_RETRIES = 3
_EUROPE_PMC_RETRIES = 2
_HTML_FINAL_RETRIES = 2

_fetch_semaphore: asyncio.Semaphore | None = None
_fetch_rate_lock: asyncio.Lock | None = None
_last_fetch_monotonic: float = 0.0
_bound_loop_id: int | None = None


def _get_concurrency_primitives() -> tuple[asyncio.Semaphore, asyncio.Lock]:
    """Lazily bind semaphore/lock to the current running event loop."""
    global _fetch_semaphore, _fetch_rate_lock, _bound_loop_id
    loop_id = id(asyncio.get_running_loop())
    if _bound_loop_id != loop_id or _fetch_semaphore is None or _fetch_rate_lock is None:
        _fetch_semaphore = asyncio.Semaphore(max(1, _PMC_MAX_CONCURRENCY))
        _fetch_rate_lock = asyncio.Lock()
        _bound_loop_id = loop_id
    return _fetch_semaphore, _fetch_rate_lock


async def _pace_ncbi_request() -> None:
    """Enforce a minimum spacing between outgoing PMC/NCBI fetch starts."""
    global _last_fetch_monotonic
    _, rate_lock = _get_concurrency_primitives()
    async with rate_lock:
        now = time.monotonic()
        wait = _PMC_MIN_INTERVAL_SECONDS - (now - _last_fetch_monotonic)
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


# ---------------------------------------------------------------------------
# JATS XML -> Markdown converter
# ---------------------------------------------------------------------------


def _local_tag(tag: str) -> str:
    """Strip XML namespace prefix from an ElementTree tag name."""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _clean_inline(text: str) -> str:
    """Collapse internal whitespace for inline text."""
    return re.sub(r"\s+", " ", text or "").strip()


def _element_text(elem: ET.Element | None) -> str:
    """Extract normalized inline text from a JATS element, preserving inline spacing."""
    if elem is None:
        return ""
    parts: list[str] = []

    def _walk(node: ET.Element) -> None:
        tag = _local_tag(node.tag)
        if tag in ("table-wrap", "fig", "supplementary-material"):
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            _walk(child)
            if child.tail:
                parts.append(child.tail)

    _walk(elem)
    return _clean_inline("".join(parts))


def _format_table(table_elem: ET.Element) -> str:
    """Convert a JATS <table> element into a GFM pipe table."""
    rows: list[list[str]] = []
    header_row_count = 0

    thead = table_elem.find(".//thead")
    if thead is not None:
        for tr in thead.findall("tr"):
            cells = [
                _element_text(cell).replace("|", "\\|")
                for cell in tr
                if _local_tag(cell.tag) in ("th", "td")
            ]
            if cells:
                rows.append(cells)
                header_row_count += 1

    tbody = table_elem.find(".//tbody")
    body_rows = tbody.findall("tr") if tbody is not None else table_elem.findall("tr")
    for tr in body_rows:
        cells = [
            _element_text(cell).replace("|", "\\|")
            for cell in tr
            if _local_tag(cell.tag) in ("th", "td")
        ]
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    col_count = max(len(r) for r in rows)
    normalized = [r + [""] * (col_count - len(r)) for r in rows]

    lines: list[str] = []
    header = normalized[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    data_start = max(1, header_row_count)
    # If thead had multiple rows, emit any extra header rows right below the separator
    for extra_hdr in normalized[1:data_start]:
        lines.append("| " + " | ".join(extra_hdr) + " |")
    for row in normalized[data_start:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _format_table_wrap(tw: ET.Element) -> str:
    """Render a JATS <table-wrap> with label, caption, table body, and footnotes."""
    blocks: list[str] = []
    label = _element_text(tw.find("label"))
    caption_el = tw.find("caption")
    caption = _element_text(caption_el)
    heading = " — ".join(part for part in (label, caption) if part)
    if heading:
        blocks.append(f"**{heading}**")

    for table in tw.findall(".//table"):
        rendered = _format_table(table)
        if rendered:
            blocks.append(rendered)

    foot = tw.find("table-wrap-foot")
    if foot is not None:
        foot_text = _element_text(foot)
        if foot_text:
            blocks.append(f"*{foot_text}*")

    return "\n\n".join(blocks)


def _format_fig(fig: ET.Element) -> str:
    """Render a JATS <fig> label and caption."""
    label = _element_text(fig.find("label"))
    caption = _element_text(fig.find("caption"))
    if label and caption:
        return f"**{label}.** {caption}"
    if caption:
        return f"**Figure.** {caption}"
    return ""


def _render_block_children(parent: ET.Element, heading_level: int = 2) -> list[str]:
    """Recursively convert JATS body/section children to Markdown blocks."""
    blocks: list[str] = []
    for child in parent:
        tag = _local_tag(child.tag)
        if tag == "title":
            continue
        if tag == "sec":
            title = _element_text(child.find("title"))
            if title:
                hashes = "#" * min(heading_level, 5)
                blocks.append(f"{hashes} {title}")
            blocks.extend(_render_block_children(child, heading_level + 1))
        elif tag == "p":
            text = _element_text(child)
            if text:
                blocks.append(text)
            # Some JATS XML nests <table-wrap> or <fig> directly inside <p>
            for nested_tw in child.findall("table-wrap"):
                rendered_tw = _format_table_wrap(nested_tw)
                if rendered_tw:
                    blocks.append(rendered_tw)
            for nested_fig in child.findall("fig"):
                rendered_fig = _format_fig(nested_fig)
                if rendered_fig:
                    blocks.append(rendered_fig)
        elif tag == "list":
            items: list[str] = []
            for item in child.findall("list-item"):
                item_text = _element_text(item)
                if item_text:
                    items.append(f"- {item_text}")
            if items:
                blocks.append("\n".join(items))
        elif tag == "table-wrap":
            rendered = _format_table_wrap(child)
            if rendered:
                blocks.append(rendered)
        elif tag == "fig":
            rendered = _format_fig(child)
            if rendered:
                blocks.append(rendered)
        elif tag == "disp-quote":
            quote_text = _element_text(child)
            if quote_text:
                blocks.append(f"> {quote_text}")
        elif tag in ("boxed-text", "notes"):
            blocks.extend(_render_block_children(child, heading_level + 1))
    return blocks


def jats_xml_to_markdown(xml_text: str, pmcid: str) -> Optional[str]:
    """Convert a JATS PMC XML document to structured Markdown.

    Returns ``None`` if the XML cannot be parsed or does not contain a ``<body>``
    full-text section (e.g. publisher blocks XML redistribution).
    """
    if not xml_text or "<body" not in xml_text:
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning(f"Failed to parse JATS XML for {pmcid}: {exc}")
        return None

    article = root if _local_tag(root.tag) == "article" else root.find(".//article")
    if article is None:
        return None

    body = article.find("body")
    if body is None:
        return None

    body_blocks = _render_block_children(body, heading_level=2)
    if not body_blocks:
        return None

    # Metadata extraction
    front = article.find("front")
    meta = front.find("article-meta") if front is not None else None
    journal_meta = front.find("journal-meta") if front is not None else None

    title = (
        _element_text(meta.find(".//title-group/article-title"))
        if meta is not None
        else ""
    )
    journal = (
        _element_text(journal_meta.find(".//journal-title"))
        if journal_meta is not None
        else ""
    )

    pmid_val = ""
    doi_val = ""
    pmcid_val = pmcid.upper()
    if meta is not None:
        for aid in meta.findall("article-id"):
            id_type = aid.attrib.get("pub-id-type", "")
            val = _clean_inline(aid.text or "")
            if id_type == "pmid" and val:
                pmid_val = val
            elif id_type == "doi" and val:
                doi_val = val
            elif id_type in ("pmc", "pmcid") and val:
                pmcid_val = val if val.upper().startswith("PMC") else f"PMC{val}"

    authors: list[str] = []
    if meta is not None:
        for contrib in meta.findall(".//contrib-group/contrib"):
            if contrib.attrib.get("contrib-type") not in (None, "author"):
                continue
            name_el = contrib.find("name")
            if name_el is not None:
                given = _element_text(name_el.find("given-names"))
                surname = _element_text(name_el.find("surname"))
                full = " ".join(p for p in (given, surname) if p)
                if full:
                    authors.append(full)
            else:
                collab = _element_text(contrib.find("collab"))
                if collab:
                    authors.append(collab)

    parts: list[str] = []
    if title:
        parts.append(f"# {title}")

    meta_lines = [f"**PMCID:** {pmcid_val}"]
    if pmid_val:
        meta_lines.append(f"**PMID:** {pmid_val}")
    if journal:
        meta_lines.append(f"**Journal:** {journal}")
    if doi_val:
        meta_lines.append(f"**DOI:** {doi_val}")
    if authors:
        meta_lines.append(f"**Authors:** {', '.join(authors[:25])}")
    meta_lines.append(f"**URL:** https://pmc.ncbi.nlm.nih.gov/articles/{pmcid_val}/")
    parts.append("## Metadata\n" + "\n".join(meta_lines))

    # Abstract
    if meta is not None:
        abstract = meta.find("abstract")
        if abstract is not None:
            abs_blocks = _render_block_children(abstract, heading_level=3)
            if abs_blocks:
                parts.append("## Abstract\n\n" + "\n\n".join(abs_blocks))

    # Main body
    parts.extend(body_blocks)

    # Back matter: acknowledgments + references
    back = article.find("back")
    if back is not None:
        ack = back.find("ack")
        if ack is not None:
            ack_text = _element_text(ack)
            if ack_text:
                parts.append(f"## Acknowledgments\n\n{ack_text}")

        ref_list = back.find(".//ref-list")
        if ref_list is not None:
            ref_lines: list[str] = []
            for idx, ref in enumerate(ref_list.findall("ref"), start=1):
                ref_text = _element_text(ref)
                if ref_text:
                    ref_lines.append(f"{idx}. {ref_text}")
            if ref_lines:
                parts.append("## References\n\n" + "\n".join(ref_lines))

    markdown = "\n\n".join(filter(None, parts))
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip() + "\n"
    return markdown


# ---------------------------------------------------------------------------
# HTTP tier fetchers (synchronous, executed in worker thread)
# ---------------------------------------------------------------------------


def _sleep_backoff(attempt: int, retry_after: str | None = None, base: float = 1.5) -> None:
    """Sleep with exponential backoff + jitter (or honor Retry-After header)."""
    if retry_after:
        try:
            delay = min(float(retry_after), 30.0)
            if delay > 0:
                time.sleep(delay)
                return
        except ValueError:
            pass
    delay = min(base * (2**attempt) + random.uniform(0.1, 0.6), 20.0)
    time.sleep(delay)


def _fetch_efetch_jats_xml(pmcid: str, email: str, api_key: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch JATS XML via NCBI E-utilities efetch with retries.

    Returns ``(xml_text, reason_if_unavailable)``.
    """
    params: dict[str, str] = {"db": "pmc", "id": pmcid, "retmode": "xml"}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key

    headers = {
        "User-Agent": f"AutoGKB/1.0 ({email})" if email else "AutoGKB/1.0",
    }

    last_reason = "efetch returned no response"
    for attempt in range(_EFETCH_RETRIES):
        try:
            resp = requests.get(
                _EUTILS_EFETCH_URL, params=params, headers=headers, timeout=30
            )
            if resp.status_code == 200:
                text = resp.text
                if "does not allow downloading of the full text in xml form" in text.lower():
                    return None, "publisher disallows PMC XML download"
                if "<body" in text:
                    return text, None
                return None, "efetch XML contained no <body> full text"

            if resp.status_code in (429, 500, 502, 503, 504):
                last_reason = f"efetch HTTP {resp.status_code}"
                logger.warning(
                    f"[pmc:{pmcid}] {last_reason} on attempt {attempt + 1}/{_EFETCH_RETRIES}"
                )
                if attempt + 1 < _EFETCH_RETRIES:
                    _sleep_backoff(attempt, resp.headers.get("Retry-After"))
                    continue
                return None, last_reason

            return None, f"efetch HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_reason = f"efetch request error: {exc}"
            logger.warning(
                f"[pmc:{pmcid}] {last_reason} on attempt {attempt + 1}/{_EFETCH_RETRIES}"
            )
            if attempt + 1 < _EFETCH_RETRIES:
                _sleep_backoff(attempt)
                continue
            return None, last_reason

    return None, last_reason


def _fetch_europe_pmc_xml(pmcid: str, email: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch JATS XML from Europe PMC fullTextXML API with retries."""
    url = _EUROPE_PMC_XML_URL.format(pmcid=pmcid)
    headers = {
        "User-Agent": f"AutoGKB/1.0 ({email})" if email else "AutoGKB/1.0",
    }

    last_reason = "Europe PMC returned no response"
    for attempt in range(_EUROPE_PMC_RETRIES):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                if "<body" in resp.text:
                    return resp.text, None
                return None, "Europe PMC XML contained no <body> full text"
            if resp.status_code == 404:
                return None, "not available on Europe PMC"
            if resp.status_code in (429, 500, 502, 503, 504):
                last_reason = f"Europe PMC HTTP {resp.status_code}"
                if attempt + 1 < _EUROPE_PMC_RETRIES:
                    _sleep_backoff(attempt, resp.headers.get("Retry-After"))
                    continue
                return None, last_reason
            return None, f"Europe PMC HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_reason = f"Europe PMC connection error: {type(exc).__name__}"
            logger.debug(f"[pmc:{pmcid}] {last_reason}")
            return None, last_reason

    return None, last_reason


def _append_supplements(markdown: str, pmcid: str, include_supplements: bool) -> str:
    if not include_supplements:
        return markdown
    try:
        supplement = format_supplement_as_markdown(pmcid)
        if supplement:
            return markdown.rstrip() + "\n\n" + supplement + "\n"
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[pmc:{pmcid}] Supplement fetch skipped: {exc}")
    return markdown


def _fetch_pmcid_markdown_sync(
    pmcid: str,
    *,
    include_supplements: bool = True,
    html_fetcher: Optional[Callable[[str, bool], Optional[str]]] = None,
) -> str:
    """Synchronous multi-tier PMC full-text fetch. Raises ValueError on failure.

    Order:
    1. ``pubmed_markdown`` HTML (1 primary attempt — preserves canonical converter output)
    2. NCBI E-utilities ``efetch`` JATS XML (CAPTCHA-free API fallback)
    3. Europe PMC ``fullTextXML`` (independent JATS XML fallback)
    4. ``pubmed_markdown`` HTML retries with exponential backoff (for XML-blocked papers)
    """
    clean_pmcid = str(pmcid or "").strip().upper()
    if not re.fullmatch(r"PMC\d+", clean_pmcid):
        raise ValueError(f"Cannot fetch article content without a valid PMCID: {pmcid}")

    email = os.environ.get("NCBI_EMAIL", "")
    api_key = os.environ.get("NCBI_API_KEY", "")
    reasons: list[str] = []

    def _try_html(attempt_label: str) -> Optional[str]:
        if html_fetcher is None:
            return None
        try:
            raw_md = html_fetcher(clean_pmcid, include_supplements)
            validated = validate_article_markdown(raw_md, clean_pmcid)
            logger.info(
                f"[pmc:{clean_pmcid}] Fetched full text via pubmed_markdown HTML "
                f"({attempt_label}, {len(validated)} chars)"
            )
            return validated
        except ValueError as exc:
            msg = str(exc)
            logger.warning(
                f"[pmc:{clean_pmcid}] pubmed_markdown HTML ({attempt_label}) rejected: {msg}"
            )
            reasons.append(msg)
        except Exception as exc:  # noqa: BLE001
            msg = f"PMC HTML fetch error: {exc}"
            logger.warning(
                f"[pmc:{clean_pmcid}] pubmed_markdown HTML ({attempt_label}) failed: {exc}"
            )
            reasons.append(msg)
        return None

    # Tier 1: Primary pubmed_markdown HTML attempt
    primary_md = _try_html("primary")
    if primary_md is not None:
        return primary_md

    # Tier 2: NCBI E-utilities efetch JATS XML (CAPTCHA-free)
    xml_text, efetch_err = _fetch_efetch_jats_xml(clean_pmcid, email, api_key)
    if xml_text:
        md = jats_xml_to_markdown(xml_text, clean_pmcid)
        if md:
            md = _append_supplements(md, clean_pmcid, include_supplements)
            try:
                validated = validate_article_markdown(md, clean_pmcid)
                logger.info(
                    f"[pmc:{clean_pmcid}] Fetched full text via NCBI efetch XML fallback "
                    f"({len(validated)} chars)"
                )
                return validated
            except ValueError as exc:
                reasons.append(f"efetch XML invalid: {exc}")
        else:
            reasons.append("efetch XML could not be converted to markdown")
    elif efetch_err:
        reasons.append(efetch_err)

    # Tier 3: Europe PMC JATS XML (separate infrastructure, CAPTCHA-free)
    epmc_xml, epmc_err = _fetch_europe_pmc_xml(clean_pmcid, email)
    if epmc_xml:
        md = jats_xml_to_markdown(epmc_xml, clean_pmcid)
        if md:
            md = _append_supplements(md, clean_pmcid, include_supplements)
            try:
                validated = validate_article_markdown(md, clean_pmcid)
                logger.info(
                    f"[pmc:{clean_pmcid}] Fetched full text via Europe PMC XML fallback "
                    f"({len(validated)} chars)"
                )
                return validated
            except ValueError as exc:
                reasons.append(f"Europe PMC XML invalid: {exc}")
        else:
            reasons.append("Europe PMC XML could not be converted to markdown")
    elif epmc_err:
        reasons.append(epmc_err)

    # Tier 4: Final pubmed_markdown HTML retries with backoff (transient CAPTCHA recovery
    # for publisher-restricted papers without XML full text)
    if html_fetcher is not None:
        for attempt in range(_HTML_FINAL_RETRIES):
            _sleep_backoff(attempt, base=2.0)
            retried_md = _try_html(f"retry {attempt + 1}/{_HTML_FINAL_RETRIES}")
            if retried_md is not None:
                return retried_md

    summary = "; ".join(dict.fromkeys(reasons)) if reasons else "all PMC sources failed"
    raise ValueError(
        f"Invalid article content or failed to fetch full text for {clean_pmcid} "
        f"({summary}). If the publisher blocks automated download, upload the PDF directly."
    )


async def fetch_pmcid_markdown(
    pmcid: str,
    *,
    include_supplements: bool = True,
    html_fetcher: Optional[Callable[[str, bool], Optional[str]]] = None,
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
                html_fetcher=html_fetcher,
            ),
        )
