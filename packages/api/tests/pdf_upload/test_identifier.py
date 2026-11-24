"""Tests for automatic PDF PMID/PMCID resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pymupdf
import pytest

from src.pdf_upload.identifier import (
    ESEARCH_URL,
    ESUMMARY_URL,
    IDCONV_URL,
    _clean_doi,
    extract_pdf_signals,
    resolve_pdf_identifiers,
    verify_pmid_in_pubmed,
)


def _make_pdf(
    pages: list[list[tuple[tuple[float, float], str, float]]],
    *,
    metadata: dict[str, str] | None = None,
    xmp: str | None = None,
) -> bytes:
    """Build a valid in-memory PDF using pymupdf.

    Each entry in ``pages`` is a list of ``((x, y), text, fontsize)`` tuples.
    """
    doc = pymupdf.open()
    for items in pages:
        page = doc.new_page(width=612, height=792)
        for pos, text, fontsize in items:
            page.insert_text(pos, text, fontsize=fontsize)
    if metadata:
        doc.set_metadata(metadata)
    if xmp:
        doc.set_xml_metadata(xmp)
    data = doc.tobytes()
    doc.close()
    return data


@dataclass
class _FakeResponse:
    payload: dict[str, Any]
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


class _FakeNcbiSession:
    """Deterministic stub for NCBI esearch / esummary / idconv HTTP calls."""

    def __init__(
        self,
        *,
        esearch: dict[str, list[str]] | None = None,
        esummary: dict[str, dict[str, Any]] | None = None,
        idconv: dict[str, str] | None = None,
    ) -> None:
        self.esearch = esearch or {}
        self.esummary = esummary or {}
        self.idconv = idconv or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: int = 15):
        params = params or {}
        self.calls.append((url, params))
        if url == ESEARCH_URL:
            term = params.get("term", "")
            idlist = self.esearch.get(term, [])
            return _FakeResponse({"esearchresult": {"idlist": idlist}})
        if url == ESUMMARY_URL:
            ids = [p for p in str(params.get("id", "")).split(",") if p]
            result = {pid: self.esummary[pid] for pid in ids if pid in self.esummary}
            return _FakeResponse({"result": result})
        if url == IDCONV_URL:
            pmcid = params.get("ids", "")
            records = (
                [{"pmcid": pmcid, "pmid": self.idconv[pmcid]}]
                if pmcid in self.idconv
                else []
            )
            return _FakeResponse({"records": records})
        raise AssertionError(f"Unexpected URL requested: {url}")


def _summary_entry(
    pmid: str,
    title: str,
    *,
    pmcid: str | None = None,
    doi: str | None = None,
) -> dict[str, Any]:
    articleids: list[dict[str, str]] = [{"idtype": "pubmed", "value": pmid}]
    if pmcid:
        articleids.append({"idtype": "pmc", "value": pmcid})
    if doi:
        articleids.append({"idtype": "doi", "value": doi})
    return {"uid": pmid, "title": title, "articleids": articleids}


def test_clean_doi_preserves_balanced_parentheses_and_strips_trailing_punct():
    assert _clean_doi("10.1016/S0009-9236(24)00123-4.") == "10.1016/S0009-9236(24)00123-4"
    assert _clean_doi("10.1002/cpt.3123).") == "10.1002/cpt.3123"
    assert _clean_doi("10.1038/s41586-024-07123-x,") == "10.1038/s41586-024-07123-x"


def test_resolve_via_xmp_metadata_doi():
    doi = "10.1002/cpt.3123"
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        f'<prism:doi xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">{doi}</prism:doi>'
        "</x:xmpmeta>"
    )
    pdf_bytes = _make_pdf(
        [[((72, 100), "CYP2D6 Genotype and Codeine Analgesia in Pediatric Patients", 16)]],
        xmp=xmp,
    )
    session = _FakeNcbiSession(
        esearch={f'"{doi}"[DOI]': ["38234567"]},
        esummary={
            "38234567": _summary_entry(
                "38234567",
                "CYP2D6 Genotype and Codeine Analgesia in Pediatric Patients.",
                pmcid="PMC10987654",
                doi=doi,
            )
        },
    )

    result = resolve_pdf_identifiers(pdf_bytes, filename="paper.pdf", session=session)

    assert result.pmid == "38234567"
    assert result.pmcid == "PMC10987654"
    assert result.doi == doi
    assert result.title == "CYP2D6 Genotype and Codeine Analgesia in Pediatric Patients"
    assert result.match_method == "doi_metadata"
    assert result.confidence == "high"


def test_resolve_via_info_subject_doi():
    doi = "10.1016/S0009-9236(24)00123-4"
    pdf_bytes = _make_pdf(
        [[((72, 100), "Warfarin Dosing Guided by VKORC1 and CYP2C9 Genotypes", 15)]],
        metadata={"subject": f"Clin Pharmacol Ther. doi:{doi}"},
    )
    session = _FakeNcbiSession(
        esearch={f'"{doi}"[DOI]': ["39000111"]},
        esummary={
            "39000111": _summary_entry(
                "39000111",
                "Warfarin Dosing Guided by VKORC1 and CYP2C9 Genotypes.",
                doi=doi,
            )
        },
    )

    result = resolve_pdf_identifiers(pdf_bytes, session=session)

    assert result.pmid == "39000111"
    assert result.pmcid is None
    assert result.doi == doi
    assert result.match_method == "doi_metadata"
    assert result.confidence == "high"


def test_resolve_via_page1_text_linewrapped_doi():
    doi = "10.1038/s41586-024-07123-x"
    pdf_bytes = _make_pdf(
        [
            [
                ((72, 60), "https://doi.org/10.1038/", 9),
                ((72, 74), "s41586-024-07123-x", 9),
                ((72, 120), "Genome-Wide Pharmacogenomics of Statin Myopathy", 16),
            ]
        ]
    )
    session = _FakeNcbiSession(
        esearch={f'"{doi}"[DOI]': ["38555000"]},
        esummary={
            "38555000": _summary_entry(
                "38555000",
                "Genome-Wide Pharmacogenomics of Statin Myopathy.",
                pmcid="PMC11223344",
                doi=doi,
            )
        },
    )

    result = resolve_pdf_identifiers(pdf_bytes, session=session)

    assert result.pmid == "38555000"
    assert result.pmcid == "PMC11223344"
    assert result.match_method == "doi_text"
    assert result.confidence == "high"


def test_references_section_dois_are_ignored():
    pdf_bytes = _make_pdf(
        [
            [
                ((72, 80), "Novel Pharmacogenomic Findings Without Printed DOI", 16),
                ((72, 130), "Abstract", 11),
                ((72, 150), "This short letter studies SLCO1B1 variants in patients.", 10),
                ((72, 220), "References", 11),
                ((72, 240), "1. Smith et al. doi:10.1000/cited-paper-999", 9),
                ((72, 260), "2. Jones et al. PMID: 99999999 PMC8888888", 9),
            ]
        ]
    )

    signals = extract_pdf_signals(pdf_bytes)

    assert signals.text_dois == []
    assert signals.explicit_pmids == []
    assert signals.explicit_pmcids == []


def test_cover_page_triggers_page2_inspection():
    doi = "10.1111/cts.13500"
    pdf_bytes = _make_pdf(
        [
            [
                ((72, 100), "NIH Public Access", 14),
                ((72, 130), "Author Manuscript", 12),
                ((72, 160), "Downloaded from Europe PMC", 10),
            ],
            [
                ((72, 80), f"DOI: {doi}", 10),
                ((72, 120), "DPYD Variants and Fluoropyrimidine Toxicity in Oncology", 16),
            ],
        ]
    )
    session = _FakeNcbiSession(
        esearch={f'"{doi}"[DOI]': ["37123456"]},
        esummary={
            "37123456": _summary_entry(
                "37123456",
                "DPYD Variants and Fluoropyrimidine Toxicity in Oncology.",
                pmcid="PMC10123456",
                doi=doi,
            )
        },
    )

    result = resolve_pdf_identifiers(pdf_bytes, session=session)

    assert result.pmid == "37123456"
    assert result.pmcid == "PMC10123456"
    assert result.match_method == "doi_text"


def test_resolve_via_explicit_pmcid_with_title_verification():
    pdf_bytes = _make_pdf(
        [
            [
                ((72, 60), "Available in PMC as PMC9876543", 10),
                ((72, 110), "TPMT and NUDT15 Testing Before Thiopurine Therapy", 16),
                ((72, 160), "Abstract", 11),
                ((72, 180), "We evaluated clinical outcomes of preemptive genotyping.", 10),
            ]
        ]
    )
    session = _FakeNcbiSession(
        idconv={"PMC9876543": "36543210"},
        esummary={
            "36543210": _summary_entry(
                "36543210",
                "TPMT and NUDT15 Testing Before Thiopurine Therapy.",
                pmcid="PMC9876543",
            )
        },
    )

    result = resolve_pdf_identifiers(pdf_bytes, session=session)

    assert result.pmid == "36543210"
    assert result.pmcid == "PMC9876543"
    assert result.match_method == "pmcid_in_text"
    assert result.confidence == "high"


def test_mismatched_numeric_filename_is_not_falsely_accepted():
    pdf_bytes = _make_pdf(
        [
            [
                ((72, 100), "Pharmacogenetics of Clopidogrel and CYP2C19 Loss of Function", 16),
                ((72, 140), "Abstract", 11),
                ((72, 160), "Patients carrying CYP2C19*2 alleles had reduced response.", 10),
            ]
        ]
    )
    session = _FakeNcbiSession(
        esummary={
            "20240115": _summary_entry(
                "20240115",
                "Unrelated Botanical Study of Alpine Mosses in Norway.",
            )
        },
    )

    result = resolve_pdf_identifiers(
        pdf_bytes, filename="20240115.pdf", session=session
    )

    assert result.pmid is None
    assert result.match_method == "unresolved"


def test_resolve_via_layout_title_ignoring_junk_metadata_and_boilerplate():
    title = "CYP2C19 Genotype Guided Antiplatelet Therapy After Percutaneous Coronary Intervention"
    pdf_bytes = _make_pdf(
        [
            [
                ((72, 45), "ORIGINAL ARTICLE", 10),
                ((72, 80), "CYP2C19 Genotype Guided Antiplatelet Therapy After", 17),
                ((72, 102), "Percutaneous Coronary Intervention", 17),
                ((72, 135), "Jane Doe, John Smith", 11),
                ((72, 175), "Abstract", 11),
                ((72, 195), "Background: CYP2C19 loss-of-function alleles impair clopidogrel.", 10),
            ]
        ],
        metadata={"title": "Microsoft Word - final_manuscript_v3.docx"},
    )
    session = _FakeNcbiSession(
        esearch={f'"{title}"[Title]': ["38888777"]},
        esummary={
            "38888777": _summary_entry(
                "38888777",
                "<i>CYP2C19</i> Genotype-Guided Antiplatelet Therapy After Percutaneous Coronary Intervention.",
                pmcid="PMC11000222",
                doi="10.1056/NEJMoa1907096",
            )
        },
    )

    result = resolve_pdf_identifiers(pdf_bytes, session=session)

    assert result.pmid == "38888777"
    assert result.pmcid == "PMC11000222"
    assert result.match_method == "title_search"
    assert result.confidence == "high"


def test_title_search_rejects_low_similarity_candidate():
    title = "Association of HLA-B*57:01 With Abacavir Hypersensitivity"
    pdf_bytes = _make_pdf(
        [[((72, 90), title, 16), ((72, 140), "Abstract", 11), ((72, 160), "Study body text here.", 10)]]
    )
    session = _FakeNcbiSession(
        esearch={f'"{title}"[Title]': ["12345678"]},
        esummary={
            "12345678": _summary_entry(
                "12345678",
                "Completely Different Paper About HLA Typing in Organ Transplantation.",
            )
        },
    )

    result = resolve_pdf_identifiers(pdf_bytes, session=session)

    assert result.pmid is None
    assert result.match_method == "unresolved"
    assert result.title == title


def test_scanned_pdf_returns_unresolved_with_warning():
    pdf_bytes = _make_pdf([[]])
    session = _FakeNcbiSession()

    result = resolve_pdf_identifiers(pdf_bytes, filename="scan.pdf", session=session)

    assert result.pmid is None
    assert result.match_method == "unresolved"
    assert result.confidence == "none"
    assert any("scanned" in w.lower() for w in result.warnings)


def test_scanned_pdf_with_pmid_filename_returns_medium_confidence():
    pdf_bytes = _make_pdf([[]])
    session = _FakeNcbiSession(
        esummary={
            "38234567": _summary_entry(
                "38234567",
                "Scanned Article Title From PubMed.",
                pmcid="PMC10987654",
            )
        }
    )

    result = resolve_pdf_identifiers(
        pdf_bytes, filename="PMID_38234567.pdf", session=session
    )

    assert result.pmid == "38234567"
    assert result.pmcid == "PMC10987654"
    assert result.match_method == "pmid_in_text"
    assert result.confidence == "medium"
    assert len(result.warnings) == 1


def test_unresolved_preprint_doi_surfaces_doi_and_warning():
    doi = "10.1101/2026.01.15.123456"
    pdf_bytes = _make_pdf(
        [
            [
                ((72, 60), f"doi: {doi}", 10),
                ((72, 100), "Preprint Study on UGT1A1 Pharmacogenomics in Neonates", 16),
            ]
        ]
    )
    session = _FakeNcbiSession()

    result = resolve_pdf_identifiers(pdf_bytes, session=session)

    assert result.pmid is None
    assert result.doi == doi
    assert result.title == "Preprint Study on UGT1A1 Pharmacogenomics in Neonates"
    assert result.match_method == "unresolved"
    assert any(doi in w for w in result.warnings)


def test_corrupt_pdf_bytes_raise_value_error():
    with pytest.raises(ValueError, match="Could not read PDF file"):
        resolve_pdf_identifiers(b"definitely not a pdf")


def test_malformed_xmp_falls_back_to_regex_for_doi_and_title():
    doi = "10.1016/j.euroneuro.2026.113900"
    title = "Age and sex related differences on genotype stratified CYP450 mediated metabolism rate"
    # Includes an illegal XML tag starting with a digit (as seen in Elsevier PDFs)
    malformed_xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        "<6mIUqnInvalidTag/>"
        f"<pdfx:doi>{doi}</pdfx:doi>"
        f"<dc:title><rdf:Alt><rdf:li>{title}</rdf:li></rdf:Alt></dc:title>"
        "</x:xmpmeta>"
    )
    pdf_bytes = _make_pdf([[((72, 100), "Body text", 10)]], xmp=malformed_xmp)

    signals = extract_pdf_signals(pdf_bytes)

    assert signals.metadata_dois == [doi]
    assert signals.xmp_titles == [title]


def test_doi_resolution_retries_429_and_survives_esummary_failure(monkeypatch):
    doi = "10.1002/cpt.9999"
    pdf_bytes = _make_pdf(
        [[((72, 100), "CYP2D6 Pharmacogenetics Study in Clinical Practice", 16)]],
        metadata={"subject": f"doi:{doi}"},
    )
    monkeypatch.setattr("src.pdf_upload.identifier.time.sleep", lambda _: None)

    class _RateLimitedSession:
        def __init__(self) -> None:
            self.esearch_attempts = 0
            self.esummary_attempts = 0

        def get(self, url: str, params=None, timeout: int = 15):
            if url == ESEARCH_URL:
                self.esearch_attempts += 1
                if self.esearch_attempts == 1:
                    return _FakeResponse({}, status_code=429)
                return _FakeResponse({"esearchresult": {"idlist": ["43051034"]}})
            if url == ESUMMARY_URL:
                self.esummary_attempts += 1
                return _FakeResponse({}, status_code=429)
            raise AssertionError(f"Unexpected URL: {url}")

    session = _RateLimitedSession()
    result = resolve_pdf_identifiers(pdf_bytes, session=session)

    assert session.esearch_attempts == 2
    assert session.esummary_attempts == 3
    assert result.pmid == "43051034"
    assert result.doi == doi
    assert result.match_method == "doi_metadata"
    assert result.confidence == "high"
    assert result.title == "CYP2D6 Pharmacogenetics Study in Clinical Practice"


def test_verify_pmid_in_pubmed_found_and_not_found_and_outage(monkeypatch):
    monkeypatch.setattr("src.pdf_upload.identifier.time.sleep", lambda _: None)

    class _VerifySession:
        def get(self, url: str, params=None, timeout: int = 15):
            assert url == ESUMMARY_URL
            pmid = (params or {}).get("id")
            if pmid == "43051034":
                return _FakeResponse(
                    {
                        "result": {
                            "uids": ["43051034"],
                            "43051034": {
                                "title": "CYP2D6 Study.",
                                "articleids": [
                                    {"idtype": "pmc", "value": "PMC1234567"},
                                    {"idtype": "doi", "value": "10.1000/test"},
                                ],
                            },
                        }
                    }
                )
            if pmid == "999999999":
                return _FakeResponse(
                    {
                        "result": {
                            "uids": ["999999999"],
                            "999999999": {"error": "cannot get document summary"},
                        }
                    }
                )
            return _FakeResponse({}, status_code=503)

    session = _VerifySession()

    found = verify_pmid_in_pubmed("43051034", session=session)
    assert found.exists is True
    assert found.title == "CYP2D6 Study"
    assert found.pmcid == "PMC1234567"
    assert found.doi == "10.1000/test"

    missing = verify_pmid_in_pubmed("999999999", session=session)
    assert missing.exists is False
    assert missing.title is None

    outage = verify_pmid_in_pubmed("12345678", session=session)
    assert outage.exists is None
