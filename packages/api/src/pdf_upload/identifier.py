"""Automatic PMID/PMCID resolution from uploaded PDF bytes.

Extracts bibliographic signals locally from PDF metadata (XMP and Info dict)
and front-matter pages (page 1, plus page 2 when page 1 looks like a cover
sheet), then resolves them against NCBI PubMed E-utilities without requiring
Datalab conversion or LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import html
import os
import re
import time
from typing import Literal
import unicodedata

import defusedxml.ElementTree as ET
from loguru import logger
from pydantic import BaseModel, Field
import pymupdf
import requests

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

NCBI_TIMEOUT_SECONDS = 15
TITLE_SIMILARITY_THRESHOLD = 0.90
TITLE_HIGH_CONFIDENCE_THRESHOLD = 0.96
TEXT_OVERLAP_THRESHOLD = 0.65

MatchMethod = Literal[
    "doi_metadata",
    "doi_text",
    "pmcid_in_text",
    "pmid_in_text",
    "title_search",
    "unresolved",
]
Confidence = Literal["high", "medium", "none"]


class PdfIdentifierResult(BaseModel):
    """Resolved PubMed identifiers and provenance for an uploaded PDF."""

    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    title: str | None = None
    match_method: MatchMethod = "unresolved"
    confidence: Confidence = "none"
    existing_job_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


@dataclass
class PdfSignals:
    """Raw bibliographic signals extracted locally from a PDF."""

    metadata_dois: list[str] = field(default_factory=list)
    text_dois: list[str] = field(default_factory=list)
    xmp_titles: list[str] = field(default_factory=list)
    layout_titles: list[str] = field(default_factory=list)
    info_titles: list[str] = field(default_factory=list)
    front_matter_text: str = ""
    explicit_pmcids: list[str] = field(default_factory=list)
    explicit_pmids: list[str] = field(default_factory=list)
    filename_pmcid: str | None = None
    filename_pmid: str | None = None
    page_count: int = 0
    is_scanned: bool = False


@dataclass
class PubMedRecord:
    """Canonical article metadata returned by NCBI E-utilities."""

    pmid: str
    pmcid: str | None = None
    doi: str | None = None
    title: str | None = None


_DOI_LINEBREAK_RE = re.compile(r"(10\.\d{4,9}/)\s*\r?\n\s*([^\s]+)")
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
_PMCID_RE = re.compile(r"\b(PMC\d{5,9})\b", re.IGNORECASE)
_PMID_TEXT_RE = re.compile(
    r"\b(?:PMID|PubMed\s*ID)\s*[:#]?\s*(\d{5,9})\b", re.IGNORECASE
)
_FILENAME_PMCID_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(PMC\d{5,9})(?:$|[^A-Za-z0-9])", re.IGNORECASE
)
_FILENAME_PMID_RE = re.compile(r"^(?:pmid[_\s-]?)?(\d{6,9})$", re.IGNORECASE)
_REFERENCES_HEADING_RE = re.compile(
    r"(?im)^\s*(?:\d+\.?\s*)?(?:references|bibliography|works\s+cited)\s*$"
)
_ABSTRACT_HEADING_RE = re.compile(r"(?i)^(?:structured\s+)?abstract\b")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_XMP_TITLE_RE = re.compile(r"<dc:title\b[^>]*>(.*?)</dc:title>", re.IGNORECASE | re.DOTALL)

_NCBI_RETRY_STATUSES = {429, 503}
_NCBI_RETRY_DELAYS = (0.4, 0.8)

_COVER_PAGE_MARKERS = (
    "downloaded from",
    "author manuscript",
    "nih public access",
    "hhs public access",
    "europe pmc",
    "this is a pdf file of an unedited manuscript",
    "accepted manuscript",
)

_JUNK_TITLE_SUBSTRINGS = (
    "untitled",
    "microsoft word",
    "powerpoint",
    "acrobat distiller",
    "latex",
)

_JUNK_TITLE_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".indd",
    ".tex",
    ".qxd",
    ".dvi",
    ".ps",
    ".rtf",
)

_BOILERPLATE_LINES = {
    "original article",
    "original research",
    "research article",
    "review",
    "review article",
    "systematic review",
    "brief report",
    "short communication",
    "case report",
    "letter to the editor",
    "correspondence",
    "editorial",
    "commentary",
    "perspective",
    "clinical trial",
    "open access",
    "article",
    "research",
    "full length article",
    "rapid communication",
    "pharmacogenomics",
    "contents lists available at sciencedirect",
    "journal homepage",
}


def _dedupe_preserve_order(
    items: list[str], *, case_insensitive: bool = False
) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold() if case_insensitive else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _clean_doi(raw: str) -> str | None:
    """Normalize a raw DOI candidate and strip trailing punctuation safely."""
    doi = raw.strip()
    while doi and doi[-1] in ".,;:!?\"'":
        doi = doi[:-1]
    # Preserve balanced parentheses (e.g. Elsevier PIIs inside DOIs) while
    # stripping unmatched closing brackets from surrounding prose.
    for open_ch, close_ch in (("(", ")"), ("[", "]"), ("{", "}")):
        while doi.endswith(close_ch) and doi.count(close_ch) > doi.count(open_ch):
            doi = doi[:-1]
    while doi and doi[-1] in ".,;:!?\"'":
        doi = doi[:-1]
    if "/" not in doi:
        return None
    suffix = doi.split("/", 1)[1]
    if len(suffix) < 2:
        return None
    return doi


def _extract_dois(text: str) -> list[str]:
    """Extract and clean DOI candidates from text."""
    if not text:
        return []
    joined = _DOI_LINEBREAK_RE.sub(r"\1\2", text)
    found: list[str] = []
    for match in _DOI_RE.finditer(joined):
        cleaned = _clean_doi(match.group(1))
        if cleaned:
            found.append(cleaned)
    return _dedupe_preserve_order(found, case_insensitive=True)


def _strip_references(text: str) -> str:
    """Drop anything from a References/Bibliography heading onward."""
    match = _REFERENCES_HEADING_RE.search(text)
    return text[: match.start()] if match else text


def _clean_title_candidate(text: str) -> str | None:
    """Return a normalized title candidate, or None if it looks like junk."""
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = cleaned.strip("-–—|: ")
    if len(cleaned) < 15 or len(cleaned) > 350:
        return None
    words = cleaned.split()
    if len(words) < 3:
        return None
    lower = cleaned.casefold()
    if lower in _BOILERPLATE_LINES:
        return None
    if any(marker in lower for marker in _JUNK_TITLE_SUBSTRINGS):
        return None
    if any(lower.endswith(ext) for ext in _JUNK_TITLE_EXTENSIONS):
        return None
    if lower.startswith(("doi:", "http://", "https://", "10.")):
        return None
    if "@" in cleaned:
        return None
    if re.fullmatch(r"[A-Z0-9._-]+", cleaned):
        return None
    return cleaned


def _parse_xmp_metadata(xml_text: str) -> tuple[list[str], list[str]]:
    """Extract DOIs and titles from an embedded PDF XMP XML packet."""
    if not xml_text or not xml_text.strip():
        return [], []

    dois: list[str] = []
    titles: list[str] = []

    try:
        root = ET.fromstring(xml_text)
        for elem in root.iter():
            local_name = elem.tag.rsplit("}", 1)[-1].lower()
            if local_name in ("doi", "identifier"):
                text = " ".join(part.strip() for part in elem.itertext() if part.strip())
                dois.extend(_extract_dois(text))
            elif local_name == "title":
                text = " ".join(part.strip() for part in elem.itertext() if part.strip())
                candidate = _clean_title_candidate(text)
                if candidate:
                    titles.append(candidate)
    except ET.ParseError:
        logger.debug("Failed to parse PDF XMP XML; falling back to regex scan")
        for match in _XMP_TITLE_RE.finditer(xml_text):
            raw_title = html.unescape(_HTML_TAG_RE.sub(" ", match.group(1)))
            candidate = _clean_title_candidate(raw_title)
            if candidate:
                titles.append(candidate)

    # Always scan raw XMP string as a fallback for attributes (e.g. prism:doi="...")
    dois.extend(_extract_dois(xml_text))

    return (
        _dedupe_preserve_order(dois, case_insensitive=True),
        _dedupe_preserve_order(titles, case_insensitive=True),
    )


def _extract_layout_titles(page: pymupdf.Page) -> list[str]:
    """Extract likely article titles from page 1 using font size and position."""
    page_dict = page.get_text("dict")
    page_height = float(page_dict.get("height") or page.rect.height or 792.0)

    # First locate an Abstract heading (if present) so we stay above it.
    max_y = page_height * 0.55
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        block_text = " ".join(
            span.get("text", "").strip()
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if span.get("text", "").strip()
        ).strip()
        y0 = float(block.get("bbox", (0, 0, 0, 0))[1])
        if y0 > page_height * 0.12 and _ABSTRACT_HEADING_RE.match(block_text):
            max_y = min(max_y, y0)
            break

    # Collect styled lines in the upper region.
    styled_lines: list[tuple[float, float, float, str]] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            bbox = line.get("bbox", (0, 0, 0, 0))
            y0, y1 = float(bbox[1]), float(bbox[3])
            if y0 >= max_y:
                continue
            spans = [
                span
                for span in line.get("spans", [])
                if span.get("text", "").strip()
            ]
            if not spans:
                continue
            text = " ".join(span["text"].strip() for span in spans)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            if text.casefold() in _BOILERPLATE_LINES:
                continue
            if _DOI_RE.search(text) or "@" in text or text.casefold().startswith("http"):
                continue
            # Weighted average font size by character count to ignore superscripts.
            total_chars = sum(len(s["text"].strip()) for s in spans)
            weighted_size = (
                sum(float(s.get("size", 0.0)) * len(s["text"].strip()) for s in spans)
                / total_chars
                if total_chars
                else 0.0
            )
            styled_lines.append((y0, y1, weighted_size, text))

    if not styled_lines:
        return []

    styled_lines.sort(key=lambda item: item[0])

    # Merge consecutive lines with matching font size and small vertical gap.
    # Publisher fonts frequently have slightly overlapping ascender/descender
    # bounding boxes (e.g. y0 - prev_y1 around -0.4 * font_size).
    groups: list[tuple[float, float, list[str]]] = []
    for y0, y1, size, text in styled_lines:
        if groups:
            prev_size, prev_y1, prev_texts = groups[-1]
            vertical_gap = y0 - prev_y1
            if (
                abs(size - prev_size) <= 0.8
                and -max(size, prev_size) * 0.6 <= vertical_gap <= max(size * 1.8, 18.0)
            ):
                prev_texts.append(text)
                groups[-1] = (max(prev_size, size), max(prev_y1, y1), prev_texts)
                continue
        groups.append((size, y1, [text]))

    scored: list[tuple[float, float, str]] = []
    for size, y1, lines in groups:
        merged = " ".join(lines)
        candidate = _clean_title_candidate(merged)
        if candidate:
            scored.append((size, y1, candidate))

    # Prefer the largest font size; break ties by appearing earlier on the page.
    scored.sort(key=lambda item: (-round(item[0], 1), item[1]))
    return _dedupe_preserve_order(
        [candidate for _, _, candidate in scored[:3]], case_insensitive=True
    )


def _looks_like_cover_page(page1_text: str) -> bool:
    stripped = page1_text.strip()
    if len(stripped) < 1200:
        return True
    lower = stripped.casefold()
    return any(marker in lower for marker in _COVER_PAGE_MARKERS)


def extract_pdf_signals(
    pdf_bytes: bytes, filename: str | None = None
) -> PdfSignals:
    """Extract local bibliographic signals from PDF bytes without network calls.

    Raises:
        ValueError: If ``pdf_bytes`` is empty, corrupted, or encrypted.
    """
    if not pdf_bytes:
        raise ValueError("PDF file is empty")

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Could not read PDF file: {exc}") from exc

    try:
        if doc.is_encrypted:
            raise ValueError("PDF file is encrypted and cannot be read")
        page_count = len(doc)
        if page_count == 0:
            raise ValueError("PDF file has no pages")

        xmp_dois, xmp_titles = _parse_xmp_metadata(doc.get_xml_metadata() or "")

        info = doc.metadata or {}
        info_dois: list[str] = []
        for key in ("subject", "keywords", "title"):
            info_dois.extend(_extract_dois(str(info.get(key) or "")))

        info_titles: list[str] = []
        raw_info_title = _clean_title_candidate(str(info.get("title") or ""))
        if raw_info_title:
            info_titles.append(raw_info_title)

        page1 = doc[0]
        page1_text = page1.get_text("text") or ""
        layout_titles = _extract_layout_titles(page1)

        front_pages = [page1_text]
        include_page2 = page_count > 1 and _looks_like_cover_page(page1_text)
        if include_page2:
            page2 = doc[1]
            page2_text = page2.get_text("text") or ""
            front_pages.append(page2_text)
            layout_titles.extend(_extract_layout_titles(page2))

        combined_front = "\n".join(front_pages)
        front_matter = _strip_references(combined_front)
        is_scanned = len(combined_front.strip()) < 100

        text_dois = _extract_dois(front_matter)
        explicit_pmcids = _dedupe_preserve_order(
            [m.group(1).upper() for m in _PMCID_RE.finditer(front_matter)]
        )
        explicit_pmids = _dedupe_preserve_order(
            [m.group(1) for m in _PMID_TEXT_RE.finditer(front_matter)]
        )

        filename_pmcid: str | None = None
        filename_pmid: str | None = None
        if filename:
            stem = os.path.splitext(os.path.basename(filename))[0].strip()
            pmcid_match = _FILENAME_PMCID_RE.search(stem)
            if pmcid_match:
                filename_pmcid = pmcid_match.group(1).upper()
            else:
                pmid_match = _FILENAME_PMID_RE.match(stem)
                if pmid_match:
                    filename_pmid = pmid_match.group(1)

        return PdfSignals(
            metadata_dois=_dedupe_preserve_order(
                [*xmp_dois, *info_dois], case_insensitive=True
            ),
            text_dois=text_dois,
            xmp_titles=xmp_titles,
            layout_titles=_dedupe_preserve_order(
                layout_titles, case_insensitive=True
            ),
            info_titles=info_titles,
            front_matter_text=front_matter,
            explicit_pmcids=explicit_pmcids,
            explicit_pmids=explicit_pmids,
            filename_pmcid=filename_pmcid,
            filename_pmid=filename_pmid,
            page_count=page_count,
            is_scanned=is_scanned,
        )
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Text normalization & matching helpers
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Normalize text for robust title comparison across PDF ligatures/accents."""
    without_tags = _HTML_TAG_RE.sub("", text)
    decomposed = unicodedata.normalize("NFKD", without_tags)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold()
    alnum = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", alnum).strip()


def _title_similarity(candidate: str, pubmed_title: str) -> float:
    """Return normalized similarity ratio in [0.0, 1.0] between two titles."""
    norm_a = _normalize_text(candidate)
    norm_b = _normalize_text(pubmed_title)
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0
    return SequenceMatcher(None, norm_a, norm_b).ratio()


def _title_overlaps_front_matter(pubmed_title: str | None, front_matter: str) -> bool:
    """Check whether most significant words of a PubMed title appear on page 1."""
    if not pubmed_title or not front_matter.strip():
        return False
    title_tokens = [tok for tok in _normalize_text(pubmed_title).split() if len(tok) >= 4]
    if not title_tokens:
        title_tokens = _normalize_text(pubmed_title).split()
    if not title_tokens:
        return False
    front_tokens = set(_normalize_text(front_matter).split())
    matched = sum(1 for tok in title_tokens if tok in front_tokens)
    return (matched / len(title_tokens)) >= TEXT_OVERLAP_THRESHOLD


# ---------------------------------------------------------------------------
# NCBI E-utilities client
# ---------------------------------------------------------------------------


def _ncbi_base_params() -> dict[str, str]:
    params: dict[str, str] = {"tool": "autogkb", "retmode": "json"}
    email = os.environ.get("NCBI_EMAIL", "").strip()
    if email:
        params["email"] = email
    api_key = os.environ.get("NCBI_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    return params


def _extract_pmcid_from_articleids(articleids: list[dict]) -> str | None:
    for entry in articleids:
        idtype = str(entry.get("idtype") or "").casefold()
        value = str(entry.get("value") or "")
        if idtype in ("pmc", "pmcid"):
            match = _PMCID_RE.search(value)
            if match:
                return match.group(1).upper()
    return None


def _extract_doi_from_articleids(articleids: list[dict]) -> str | None:
    for entry in articleids:
        if str(entry.get("idtype") or "").casefold() == "doi":
            cleaned = _clean_doi(str(entry.get("value") or ""))
            if cleaned:
                return cleaned
    return None


def _clean_pubmed_title(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = _HTML_TAG_RE.sub("", raw).strip()
    cleaned = cleaned.rstrip(".")
    return cleaned or None


def _ncbi_get(
    url: str,
    params: dict[str, str],
    *,
    session: requests.Session | None = None,
) -> requests.Response:
    """Issue a GET request to NCBI with brief backoff on transient 429/503s."""
    client = session or requests
    for attempt in range(len(_NCBI_RETRY_DELAYS) + 1):
        resp = client.get(url, params=params, timeout=NCBI_TIMEOUT_SECONDS)
        status_code = getattr(resp, "status_code", 200)
        if (
            status_code in _NCBI_RETRY_STATUSES
            and attempt < len(_NCBI_RETRY_DELAYS)
        ):
            time.sleep(_NCBI_RETRY_DELAYS[attempt])
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Exhausted retries for {url}")


def _esearch_pubmed(
    term: str,
    *,
    retmax: int = 5,
    session: requests.Session | None = None,
) -> list[str]:
    params = {
        **_ncbi_base_params(),
        "db": "pubmed",
        "term": term,
        "retmax": str(retmax),
    }
    try:
        resp = _ncbi_get(ESEARCH_URL, params, session=session)
        data = resp.json()
        idlist = data.get("esearchresult", {}).get("idlist", [])
        return [str(pid).strip() for pid in idlist if str(pid).strip().isdigit()]
    except Exception as exc:
        logger.warning(f"PubMed esearch failed for term={term!r}: {exc}")
        return []


def _esummary_pubmed(
    pmids: list[str],
    *,
    session: requests.Session | None = None,
) -> dict[str, PubMedRecord]:
    unique_pmids = _dedupe_preserve_order([p.strip() for p in pmids if p.strip()])
    if not unique_pmids:
        return {}
    params = {
        **_ncbi_base_params(),
        "db": "pubmed",
        "id": ",".join(unique_pmids),
    }
    try:
        resp = _ncbi_get(ESUMMARY_URL, params, session=session)
        result = resp.json().get("result", {})
    except Exception as exc:
        logger.warning(f"PubMed esummary failed for pmids={unique_pmids}: {exc}")
        return {}

    records: dict[str, PubMedRecord] = {}
    for pmid in unique_pmids:
        entry = result.get(pmid)
        if not isinstance(entry, dict) or entry.get("error"):
            continue
        articleids = entry.get("articleids") or []
        records[pmid] = PubMedRecord(
            pmid=pmid,
            pmcid=_extract_pmcid_from_articleids(articleids),
            doi=_extract_doi_from_articleids(articleids),
            title=_clean_pubmed_title(entry.get("title")),
        )
    return records


def _lookup_pmid_from_pmcid(
    pmcid: str,
    *,
    session: requests.Session | None = None,
) -> str | None:
    params: dict[str, str] = {"ids": pmcid, "format": "json", "tool": "autogkb"}
    email = os.environ.get("NCBI_EMAIL", "").strip()
    if email:
        params["email"] = email
    try:
        resp = _ncbi_get(IDCONV_URL, params, session=session)
        records = resp.json().get("records", [])
        if records:
            pmid = records[0].get("pmid")
            if pmid is not None and str(pmid).strip().isdigit():
                return str(pmid).strip()
    except Exception as exc:
        logger.warning(f"NCBI idconv failed for {pmcid}: {exc}")

    # Fallback to PubMed esearch by PMCID
    pmids = _esearch_pubmed(f"{pmcid}[PMC]", retmax=1, session=session)
    return pmids[0] if pmids else None


def _resolve_via_dois(
    dois: list[str],
    *,
    match_method: Literal["doi_metadata", "doi_text"],
    signals: PdfSignals,
    tried_dois: set[str],
    session: requests.Session | None,
) -> PdfIdentifierResult | None:
    fallback_title = next(
        iter([*signals.xmp_titles, *signals.layout_titles, *signals.info_titles]),
        None,
    )
    for doi in dois:
        key = doi.casefold()
        if key in tried_dois:
            continue
        tried_dois.add(key)

        pmids = _esearch_pubmed(f'"{doi}"[DOI]', retmax=5, session=session)
        if not pmids:
            continue

        records = _esummary_pubmed(pmids, session=session)
        if len(records) == 1:
            rec = next(iter(records.values()))
            return PdfIdentifierResult(
                pmid=rec.pmid,
                pmcid=rec.pmcid,
                doi=rec.doi or doi,
                title=rec.title,
                match_method=match_method,
                confidence="high",
            )

        if len(pmids) == 1 and not records:
            # esearch uniquely matched the DOI even though esummary failed
            return PdfIdentifierResult(
                pmid=pmids[0],
                pmcid=None,
                doi=doi,
                title=fallback_title,
                match_method=match_method,
                confidence="high",
            )

        if len(records) > 1 and not signals.is_scanned:
            matching = [
                rec
                for rec in records.values()
                if _title_overlaps_front_matter(rec.title, signals.front_matter_text)
            ]
            if len(matching) == 1:
                rec = matching[0]
                return PdfIdentifierResult(
                    pmid=rec.pmid,
                    pmcid=rec.pmcid,
                    doi=rec.doi or doi,
                    title=rec.title,
                    match_method=match_method,
                    confidence="high",
                )
    return None


def _resolve_via_explicit_ids(
    signals: PdfSignals,
    *,
    session: requests.Session | None,
) -> PdfIdentifierResult | None:
    pmcid_candidates: list[tuple[str, bool]] = [
        (pmcid, False) for pmcid in signals.explicit_pmcids
    ]
    if signals.filename_pmcid and signals.filename_pmcid not in signals.explicit_pmcids:
        pmcid_candidates.append((signals.filename_pmcid, True))

    for pmcid, from_filename in pmcid_candidates:
        pmid = _lookup_pmid_from_pmcid(pmcid, session=session)
        if not pmid:
            continue
        records = _esummary_pubmed([pmid], session=session)
        rec = records.get(pmid)
        if not rec:
            continue
        if not signals.is_scanned:
            if not _title_overlaps_front_matter(rec.title, signals.front_matter_text):
                continue
            return PdfIdentifierResult(
                pmid=rec.pmid,
                pmcid=rec.pmcid or pmcid,
                doi=rec.doi,
                title=rec.title,
                match_method="pmcid_in_text",
                confidence="high",
            )
        if from_filename:
            return PdfIdentifierResult(
                pmid=rec.pmid,
                pmcid=rec.pmcid or pmcid,
                doi=rec.doi,
                title=rec.title,
                match_method="pmcid_in_text",
                confidence="medium",
                warnings=[
                    "PDF has no selectable text; match was inferred from the filename without verifying page text."
                ],
            )

    pmid_candidates: list[tuple[str, bool]] = [
        (pmid, False) for pmid in signals.explicit_pmids
    ]
    if signals.filename_pmid and signals.filename_pmid not in signals.explicit_pmids:
        pmid_candidates.append((signals.filename_pmid, True))

    for pmid, from_filename in pmid_candidates:
        records = _esummary_pubmed([pmid], session=session)
        rec = records.get(pmid)
        if not rec:
            continue
        if not signals.is_scanned:
            if not _title_overlaps_front_matter(rec.title, signals.front_matter_text):
                continue
            return PdfIdentifierResult(
                pmid=rec.pmid,
                pmcid=rec.pmcid,
                doi=rec.doi,
                title=rec.title,
                match_method="pmid_in_text",
                confidence="high",
            )
        if from_filename:
            return PdfIdentifierResult(
                pmid=rec.pmid,
                pmcid=rec.pmcid,
                doi=rec.doi,
                title=rec.title,
                match_method="pmid_in_text",
                confidence="medium",
                warnings=[
                    "PDF has no selectable text; match was inferred from the filename without verifying page text."
                ],
            )

    return None


def _resolve_via_titles(
    candidate_titles: list[str],
    *,
    session: requests.Session | None,
) -> PdfIdentifierResult | None:
    for candidate in candidate_titles:
        # Try exact phrase search first, then normalized keyword title search.
        pmids = _esearch_pubmed(f'"{candidate}"[Title]', retmax=5, session=session)
        if not pmids:
            normalized = _normalize_text(candidate)
            if normalized:
                pmids = _esearch_pubmed(
                    f"{normalized}[Title]", retmax=5, session=session
                )
        if not pmids:
            continue

        records = _esummary_pubmed(pmids, session=session)
        scored: list[tuple[float, PubMedRecord]] = []
        for rec in records.values():
            if not rec.title:
                continue
            score = _title_similarity(candidate, rec.title)
            scored.append((score, rec))
        if not scored:
            continue

        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_rec = scored[0]
        if best_score < TITLE_SIMILARITY_THRESHOLD:
            continue

        # Reject ambiguous ties where two different papers score almost identically.
        if len(scored) > 1 and (best_score - scored[1][0]) < 0.02 and scored[1][0] >= TITLE_SIMILARITY_THRESHOLD:
            continue

        confidence: Confidence = (
            "high" if best_score >= TITLE_HIGH_CONFIDENCE_THRESHOLD else "medium"
        )
        return PdfIdentifierResult(
            pmid=best_rec.pmid,
            pmcid=best_rec.pmcid,
            doi=best_rec.doi,
            title=best_rec.title,
            match_method="title_search",
            confidence=confidence,
        )

    return None


def resolve_pdf_identifiers(
    pdf_bytes: bytes,
    filename: str | None = None,
    *,
    session: requests.Session | None = None,
) -> PdfIdentifierResult:
    """Resolve a PDF's PMID and PMCID via local extraction + NCBI E-utilities.

    Resolution waterfall:
        1. DOI in PDF XMP / Info metadata -> PubMed ``[DOI]`` search
        2. DOI in page-1/front-matter text -> PubMed ``[DOI]`` search
        3. Explicit PMCID / PMID in front matter or filename (title-verified)
        4. Candidate title (XMP, page-1 layout, Info dict) -> PubMed ``[Title]``
           search with normalized similarity verification

    Args:
        pdf_bytes: Raw PDF bytes.
        filename:  Optional original filename (used as a low-priority hint).
        session:   Optional ``requests.Session`` for HTTP calls/testing.

    Returns:
        ``PdfIdentifierResult`` describing the matched identifiers (or
        ``match_method="unresolved"`` with extracted fallback metadata/warnings).

    Raises:
        ValueError: If ``pdf_bytes`` cannot be parsed as a valid PDF.
    """
    signals = extract_pdf_signals(pdf_bytes, filename=filename)
    tried_dois: set[str] = set()

    # Tier 1a: DOI from XMP / PDF Info metadata
    result = _resolve_via_dois(
        signals.metadata_dois,
        match_method="doi_metadata",
        signals=signals,
        tried_dois=tried_dois,
        session=session,
    )
    if result:
        return result

    # Tier 1b: DOI from front-matter text
    result = _resolve_via_dois(
        signals.text_dois,
        match_method="doi_text",
        signals=signals,
        tried_dois=tried_dois,
        session=session,
    )
    if result:
        return result

    # Tier 2: Explicit PMCID or PMID in front matter or filename
    result = _resolve_via_explicit_ids(signals, session=session)
    if result:
        return result

    # Tier 3: Candidate title search (XMP -> layout -> Info dict)
    candidate_titles = _dedupe_preserve_order(
        [*signals.xmp_titles, *signals.layout_titles, *signals.info_titles],
        case_insensitive=True,
    )
    result = _resolve_via_titles(candidate_titles, session=session)
    if result:
        return result

    # Unresolved: return best extracted DOI/title along with actionable warnings
    warnings: list[str] = []
    if signals.is_scanned:
        warnings.append(
            "No selectable text found in PDF (possibly scanned); enter the PMID manually."
        )
    all_dois = _dedupe_preserve_order(
        [*signals.metadata_dois, *signals.text_dois], case_insensitive=True
    )
    first_doi = all_dois[0] if all_dois else None
    if first_doi:
        warnings.append(
            f"Found DOI {first_doi} in PDF, but no matching PubMed record was found."
        )
    first_title = candidate_titles[0] if candidate_titles else None
    return PdfIdentifierResult(
        doi=first_doi,
        title=first_title,
        match_method="unresolved",
        confidence="none",
        warnings=warnings,
    )
