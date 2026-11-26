"""Tests for pubmed-markdown>=0.3.0 integration in pmc_fetcher."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src import pmc_fetcher
from src.pmc_fetcher import (
    fetch_pmcid_markdown,
    get_pubmed_downloader,
    has_valid_article_markdown,
    validate_article_markdown,
)


def _run(coro):
    return asyncio.run(coro)


_SAMPLE_JATS_XML = (
    """<?xml version="1.0"?>
<pmc-articleset>
  <article article-type="research-article">
    <front>
      <journal-meta>
        <journal-title-group>
          <journal-title>Clinical and Translational Science</journal-title>
        </journal-title-group>
      </journal-meta>
      <article-meta>
        <article-id pub-id-type="pmid">42974700</article-id>
        <article-id pub-id-type="pmcid">PMC13729000</article-id>
        <article-id pub-id-type="doi">10.1111/cts.70751</article-id>
        <title-group>
          <article-title>Phase 1 Pharmacokinetics of <italic>CYP3A4</italic> Inhibition</article-title>
        </title-group>
        <contrib-group>
          <contrib contrib-type="author">
            <name><surname>Doe</surname><given-names>Jane</given-names></name>
          </contrib>
        </contrib-group>
        <abstract>
          <p>We evaluated CYP3A4-mediated drug-drug interactions in healthy volunteers across multiple cohorts.</p>
        </abstract>
      </article-meta>
    </front>
    <body>
      <sec>
        <title>Introduction</title>
        <p>Pharmacogenetic variation in CYP3A4 and CYP2C19 influences systemic drug exposure and clinical response in treated patients. """
    + ("Detailed background text. " * 25)
    + """</p>
      </sec>
      <sec>
        <title>Results</title>
        <p>Co-administration increased AUC by 2.1-fold (p &lt; 0.001).</p>
        <table-wrap>
          <label>Table 1</label>
          <caption><p>Pharmacokinetic summary</p></caption>
          <table>
            <thead>
              <tr><th>Genotype</th><th>AUC ratio</th></tr>
            </thead>
            <tbody>
              <tr><td>*1/*1</td><td>1.00</td></tr>
              <tr><td>*1/*2</td><td>2.10</td></tr>
            </tbody>
          </table>
          <table-wrap-foot><p>Values are geometric mean ratios.</p></table-wrap-foot>
        </table-wrap>
      </sec>
    </body>
    <back>
      <ref-list>
        <ref><mixed-citation>Smith A. CYP3A4 review. 2025.</mixed-citation></ref>
      </ref-list>
    </back>
  </article>
</pmc-articleset>
"""
)

_PUBLISHER_BLOCKED_XML = """<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <!--The publisher of this article does not allow downloading of the full text in XML form.-->
    <front><article-meta><title-group><article-title>Blocked Paper</article-title></title-group></article-meta></front>
  </article>
</pmc-articleset>
"""


def test_validate_article_markdown_catches_waf_and_cloudflare_challenges():
    for sample in (
        "# Checking your browser - reCAPTCHA\n" + ("x" * 600),
        "Attention Required! | Cloudflare\n" + ("x" * 600),
        "<html><title>403</title>403 Forbidden</html>" + ("x" * 600),
        "Please wait while we verify your request" + ("x" * 600),
    ):
        with pytest.raises(ValueError, match="Invalid article content"):
            validate_article_markdown(sample, "PMC13729000")
        assert has_valid_article_markdown(sample, "PMC13729000") is False


def test_validate_article_markdown_rejects_missing_or_short_text():
    for sample in (None, "", "short text"):
        with pytest.raises(ValueError, match="missing or too short"):
            validate_article_markdown(sample, "PMC13729000")
        assert has_valid_article_markdown(sample, "PMC13729000") is False


def test_get_pubmed_downloader_respects_env_and_caches(monkeypatch):
    monkeypatch.setattr(pmc_fetcher, "_downloader", None)
    monkeypatch.setattr(pmc_fetcher, "_downloader_config", None)
    monkeypatch.setenv("NCBI_EMAIL", "test@example.edu")
    monkeypatch.setenv("NCBI_API_KEY", "secret-key")
    monkeypatch.setenv("PMC_FETCH_SOURCE", "auto")

    first = get_pubmed_downloader()
    second = get_pubmed_downloader()
    assert first is second
    assert first.email == "test@example.edu"
    assert first.ncbi_key == "secret-key"
    assert first.source == "auto"

    monkeypatch.setenv("PMC_FETCH_SOURCE", "html_first")
    third = get_pubmed_downloader()
    assert third is not first
    assert third.source == "html_first"


def test_fetch_pmcid_markdown_delegates_to_fetcher():
    valid_md = "# Canonical Conversion\n\n## Abstract\n" + ("Study text. " * 60)
    fetcher = MagicMock(return_value=valid_md)

    md = _run(
        fetch_pmcid_markdown(
            " pmc13729000 ", include_supplements=True, fetcher=fetcher
        )
    )

    assert md == valid_md
    fetcher.assert_called_once_with("PMC13729000", True)


def test_fetch_pmcid_markdown_uses_pubmed_markdown_xml_first(monkeypatch):
    monkeypatch.setattr(pmc_fetcher, "_downloader", None)
    monkeypatch.setattr(pmc_fetcher, "_downloader_config", None)
    monkeypatch.setenv("NCBI_EMAIL", "test@example.edu")
    monkeypatch.delenv("PMC_FETCH_SOURCE", raising=False)

    ok_resp = MagicMock(status_code=200, text=_SAMPLE_JATS_XML, headers={})
    ok_resp.raise_for_status = MagicMock()

    with (
        patch("pubmed_markdown.xml_from_pmcid.requests.get", return_value=ok_resp) as mock_get,
        patch("pubmed_markdown.core.format_supplement_as_markdown", return_value=None),
    ):
        md = _run(fetch_pmcid_markdown("PMC13729000", include_supplements=True))

    assert "Phase 1 Pharmacokinetics of" in md
    assert "CYP3A4" in md
    assert "| Genotype | AUC ratio |" in md
    assert mock_get.call_count == 1
    assert "efetch.fcgi" in mock_get.call_args[0][0]


def test_fetch_pmcid_markdown_falls_back_to_europe_pmc_via_pubmed_markdown(monkeypatch):
    monkeypatch.setattr(pmc_fetcher, "_downloader", None)
    monkeypatch.setattr(pmc_fetcher, "_downloader_config", None)
    monkeypatch.setenv("NCBI_EMAIL", "test@example.edu")
    monkeypatch.delenv("PMC_FETCH_SOURCE", raising=False)

    blocked_resp = MagicMock(status_code=200, text=_PUBLISHER_BLOCKED_XML, headers={})
    blocked_resp.raise_for_status = MagicMock()
    epmc_resp = MagicMock(status_code=200, text=_SAMPLE_JATS_XML, headers={})
    epmc_resp.raise_for_status = MagicMock()

    with (
        patch(
            "pubmed_markdown.xml_from_pmcid.requests.get",
            side_effect=[blocked_resp, epmc_resp],
        ) as mock_get,
        patch("pubmed_markdown.core.format_supplement_as_markdown", return_value=None),
    ):
        md = _run(fetch_pmcid_markdown("PMC13717721", include_supplements=False))

    assert "Phase 1 Pharmacokinetics" in md
    assert mock_get.call_count == 2
    assert "europepmc" in mock_get.call_args_list[1][0][0]


def test_fetch_pmcid_markdown_rejects_invalid_pmcid():
    fetcher = MagicMock()
    with pytest.raises(ValueError, match="valid PMCID"):
        _run(fetch_pmcid_markdown("42974700", fetcher=fetcher))
    fetcher.assert_not_called()


def test_fetch_pmcid_markdown_raises_descriptive_error_when_fetch_fails():
    fetcher = MagicMock(return_value=None)
    with pytest.raises(ValueError, match="upload the PDF directly"):
        _run(fetch_pmcid_markdown("PMC13717721", include_supplements=False, fetcher=fetcher))


def test_fetch_pmcid_markdown_rejects_waf_markdown_from_fetcher():
    captcha_md = "# Checking your browser - reCAPTCHA\n" + ("challenge " * 100)
    fetcher = MagicMock(return_value=captcha_md)
    with pytest.raises(ValueError, match="Invalid article content"):
        _run(fetch_pmcid_markdown("PMC13717721", include_supplements=False, fetcher=fetcher))


def test_pacing_defaults_scale_with_ncbi_api_key_and_respect_overrides(monkeypatch):
    monkeypatch.delenv("PMC_FETCH_MAX_CONCURRENCY", raising=False)
    monkeypatch.delenv("PMC_FETCH_MIN_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("NCBI_API_KEY", raising=False)

    assert pmc_fetcher._get_pmc_max_concurrency() == 3
    assert pmc_fetcher._get_pmc_min_interval_seconds() == pytest.approx(0.35)

    monkeypatch.setenv("NCBI_API_KEY", "test-key")
    assert pmc_fetcher._get_pmc_max_concurrency() == 8
    assert pmc_fetcher._get_pmc_min_interval_seconds() == pytest.approx(0.12)

    monkeypatch.setenv("PMC_FETCH_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("PMC_FETCH_MIN_INTERVAL_SEC", "0.25")
    assert pmc_fetcher._get_pmc_max_concurrency() == 4
    assert pmc_fetcher._get_pmc_min_interval_seconds() == pytest.approx(0.25)


def test_fetch_pubmed_text_falls_back_directly_to_abstract_without_duplicate_idconv(
    monkeypatch,
):
    from src import main as api_main

    monkeypatch.setattr(pmc_fetcher, "_downloader", None)
    monkeypatch.setattr(pmc_fetcher, "_downloader_config", None)
    monkeypatch.setenv("NCBI_EMAIL", "test@example.edu")
    monkeypatch.setenv("NCBI_API_KEY", "secret-key")

    abstract_md = "# Abstract Only Study\n\n## Abstract\n" + ("Abstract text. " * 60)

    with (
        patch("src.main.get_pmcid_from_pmid", return_value={"12345678": None}) as mock_idconv,
        patch(
            "src.main.get_abstract_markdown_from_pmid", return_value=abstract_md
        ) as mock_abstract,
    ):
        md = _run(api_main.fetch_pubmed_text("12345678", include_supplements=False))

    assert md == abstract_md
    mock_idconv.assert_called_once_with("12345678", email="test@example.edu")
    mock_abstract.assert_called_once_with(
        "12345678", email="test@example.edu", ncbi_key="secret-key"
    )
