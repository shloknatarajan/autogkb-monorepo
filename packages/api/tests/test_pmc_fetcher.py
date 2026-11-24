"""Tests for CAPTCHA-resilient PMC full-text fetching and JATS XML conversion."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.pmc_fetcher import (
    fetch_pmcid_markdown,
    jats_xml_to_markdown,
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


def test_jats_xml_to_markdown_renders_metadata_sections_tables_and_refs():
    md = jats_xml_to_markdown(_SAMPLE_JATS_XML, "PMC13729000")
    assert md is not None
    assert md.startswith("# Phase 1 Pharmacokinetics of CYP3A4 Inhibition")
    assert "**PMCID:** PMC13729000" in md
    assert "**PMID:** 42974700" in md
    assert "**Authors:** Jane Doe" in md
    assert "## Abstract" in md
    assert "## Introduction" in md
    assert "## Results" in md
    assert "| Genotype | AUC ratio |" in md
    assert "| *1/*2 | 2.10 |" in md
    assert "## References" in md
    assert "1. Smith A. CYP3A4 review. 2025." in md


def test_jats_xml_to_markdown_returns_none_without_body():
    assert jats_xml_to_markdown(_PUBLISHER_BLOCKED_XML, "PMC13717721") is None


def test_validate_article_markdown_catches_waf_and_cloudflare_challenges():
    for sample in (
        "# Checking your browser - reCAPTCHA\n" + ("x" * 600),
        "Attention Required! | Cloudflare\n" + ("x" * 600),
        "<html><title>403</title>403 Forbidden</html>" + ("x" * 600),
        "Please wait while we verify your request" + ("x" * 600),
    ):
        with pytest.raises(ValueError, match="Invalid article content"):
            validate_article_markdown(sample, "PMC13729000")


def test_fetch_pmcid_markdown_prefers_pubmed_markdown_html_when_valid():
    valid_md = "# Canonical HTML Conversion\n\n## Abstract\n" + ("Study text. " * 60)
    html_fetcher = MagicMock(return_value=valid_md)

    with patch("src.pmc_fetcher.requests.get") as mock_get:
        md = _run(
            fetch_pmcid_markdown(
                "PMC13729000", include_supplements=True, html_fetcher=html_fetcher
            )
        )

    assert md == valid_md
    html_fetcher.assert_called_once_with("PMC13729000", True)
    mock_get.assert_not_called()


def test_fetch_pmcid_markdown_falls_back_to_efetch_xml_on_captcha():
    captcha_md = "# Checking your browser - reCAPTCHA\n" + ("challenge " * 100)
    html_fetcher = MagicMock(return_value=captcha_md)
    ok_resp = MagicMock(status_code=200, text=_SAMPLE_JATS_XML, headers={})

    with (
        patch("src.pmc_fetcher.requests.get", return_value=ok_resp) as mock_get,
        patch("src.pmc_fetcher.format_supplement_as_markdown", return_value=None),
    ):
        md = _run(
            fetch_pmcid_markdown(
                "PMC13729000", include_supplements=True, html_fetcher=html_fetcher
            )
        )

    assert "Phase 1 Pharmacokinetics of CYP3A4 Inhibition" in md
    assert html_fetcher.call_count == 1
    assert mock_get.call_count == 1
    assert "efetch.fcgi" in mock_get.call_args[0][0]


def test_fetch_pmcid_markdown_retries_efetch_429_then_succeeds():
    html_fetcher = MagicMock(return_value=None)
    rate_limited = MagicMock(status_code=429, text="Too Many Requests", headers={})
    ok_resp = MagicMock(status_code=200, text=_SAMPLE_JATS_XML, headers={})

    with (
        patch(
            "src.pmc_fetcher.requests.get", side_effect=[rate_limited, ok_resp]
        ) as mock_get,
        patch("src.pmc_fetcher._sleep_backoff") as mock_sleep,
        patch("src.pmc_fetcher.format_supplement_as_markdown", return_value=None),
    ):
        md = _run(
            fetch_pmcid_markdown(
                "PMC13729000", include_supplements=False, html_fetcher=html_fetcher
            )
        )

    assert "## Results" in md
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once()


def test_fetch_pmcid_markdown_falls_back_to_europe_pmc_when_publisher_blocks_ncbi_xml():
    html_fetcher = MagicMock(return_value=None)
    blocked_resp = MagicMock(status_code=200, text=_PUBLISHER_BLOCKED_XML, headers={})
    epmc_resp = MagicMock(status_code=200, text=_SAMPLE_JATS_XML, headers={})

    with (
        patch("src.pmc_fetcher.requests.get", side_effect=[blocked_resp, epmc_resp]),
        patch("src.pmc_fetcher.format_supplement_as_markdown", return_value=None),
    ):
        md = _run(
            fetch_pmcid_markdown(
                "PMC13717721", include_supplements=False, html_fetcher=html_fetcher
            )
        )

    assert "Phase 1 Pharmacokinetics" in md
    assert html_fetcher.call_count == 1


def test_fetch_pmcid_markdown_retries_html_when_xml_unavailable_and_first_hit_captcha():
    blocked_resp = MagicMock(status_code=200, text=_PUBLISHER_BLOCKED_XML, headers={})
    epmc_404 = MagicMock(status_code=404, text="Not found", headers={})
    captcha_md = "# Checking your browser - reCAPTCHA\n" + ("challenge " * 100)
    valid_md = "# Valid Clinical Study\n\n## Abstract\n" + ("Pharmacogenomics data. " * 40)
    html_fetcher = MagicMock(side_effect=[captcha_md, valid_md])

    with (
        patch("src.pmc_fetcher.requests.get", side_effect=[blocked_resp, epmc_404]),
        patch("src.pmc_fetcher._sleep_backoff") as mock_sleep,
    ):
        md = _run(
            fetch_pmcid_markdown(
                "PMC13717721", include_supplements=False, html_fetcher=html_fetcher
            )
        )

    assert md == valid_md
    assert html_fetcher.call_count == 2
    mock_sleep.assert_called_once()


def test_fetch_pmcid_markdown_raises_descriptive_error_when_all_tiers_fail():
    blocked_resp = MagicMock(status_code=200, text=_PUBLISHER_BLOCKED_XML, headers={})
    epmc_404 = MagicMock(status_code=404, text="Not found", headers={})
    captcha_md = "# Checking your browser - reCAPTCHA\n" + ("challenge " * 100)
    html_fetcher = MagicMock(return_value=captcha_md)

    with (
        patch("src.pmc_fetcher.requests.get", side_effect=[blocked_resp, epmc_404]),
        patch("src.pmc_fetcher._sleep_backoff"),
        pytest.raises(ValueError, match="publisher disallows PMC XML download"),
    ):
        _run(
            fetch_pmcid_markdown(
                "PMC13717721", include_supplements=False, html_fetcher=html_fetcher
            )
        )

    # 1 primary attempt + 2 final retries
    assert html_fetcher.call_count == 3
