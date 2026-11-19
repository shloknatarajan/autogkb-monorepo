"""Tests for run_triage_job in src.jobs."""

import asyncio
from unittest.mock import ANY, call, patch

import pytest
import requests

from src.jobs import (
    extract_drug_tags,
    extract_gene_tags,
    run_analysis_job,
    run_triage_job,
    validate_article_markdown,
)

SESSION_ID = "sess-1"
PROJECT_ID = "proj-1"
JOB_ID = "job-1"

_PMID_ENTRIES = [
    {"pmid": "11111", "litsuggest_score": 0.99},
    {"pmid": "22222", "litsuggest_score": 0.95},
]

_ABSTRACTS = [
    {"title": "Title A", "abstract": "Abstract A", "publication_date": "2025-11-07"},
    {"title": "Title B", "abstract": "Abstract B", "publication_date": "2025-10-12"},
]

_SCORES = [
    {
        "score": 85,
        "label": "relevant",
        "tags": ["variant_annotations", "general_pharmacogenomics"],
        "reasoning": "Good association",
    },
    {"score": 20, "label": "not_relevant", "tags": [], "reasoning": "No association"},
]

_ADDITIONAL_LABELS = [
    {
        "paper_types": ["methods"],
        "paper_type_reasoning": "Validates a PGx assay.",
        "pgx_information_types": ["efficacy"],
        "pgx_information_reasoning": "Reports treatment response.",
    },
    {
        "paper_types": [],
        "paper_type_reasoning": "No matching type.",
        "pgx_information_types": [],
        "pgx_information_reasoning": "No PGx information.",
    },
]

_DRUG_TAGS = [
    [
        {
            "name": "warfarin",
            "id": "PA451906",
            "source": "pharmgkb",
            "url": "https://www.clinpgx.org/chemical/PA451906",
            "resolved": True,
            "mentions": ["warfarin"],
        }
    ],
    [],
]

_GENE_TAGS = [
    [
        {
            "name": "CYP2C9",
            "id": "PA125",
            "source": "pharmgkb",
            "url": "https://www.clinpgx.org/gene/PA125",
            "resolved": True,
            "mentions": ["CYP2C9"],
            "ncbi_gene_id": "1559",
            "hgnc_id": "HGNC:2623",
        }
    ],
    [],
]


@pytest.fixture(autouse=True)
def mock_existing_analysis_jobs():
    with patch("src.jobs.get_job_by_pmid", return_value=None):
        yield


@pytest.fixture(autouse=True)
def mock_additional_label_classification():
    with patch("src.jobs.classify_additional_labels", side_effect=_ADDITIONAL_LABELS):
        yield


@pytest.fixture(autouse=True)
def mock_drug_tag_extraction():
    with patch("src.jobs.extract_drug_tags", side_effect=_DRUG_TAGS):
        yield


@pytest.fixture(autouse=True)
def mock_gene_tag_extraction():
    with patch("src.jobs.extract_gene_tags", side_effect=_GENE_TAGS):
        yield


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def test_extract_drug_tags_uses_normalized_names_and_preserves_unresolved():
    normalized_mentions = {
        "Coumadin": {
            "matches": [
                {
                    "normalized_term": "warfarin",
                    "id": "PA451906",
                    "source": "pharmgkb",
                    "url": "https://www.clinpgx.org/chemical/PA451906",
                }
            ]
        },
        "warfarin": {
            "matches": [
                {
                    "normalized_term": "warfarin",
                    "id": "PA451906",
                    "source": "pharmgkb",
                    "url": "https://www.clinpgx.org/chemical/PA451906",
                }
            ]
        },
        "hydroxybupropion": {"matches": []},
    }
    with patch(
        "src.jobs.normalize_drugs_in_abstract",
        return_value=normalized_mentions,
    ):
        result = extract_drug_tags("Coumadin and hydroxybupropion were measured.")

    assert result == [
        {
            "name": "warfarin",
            "id": "PA451906",
            "source": "pharmgkb",
            "url": "https://www.clinpgx.org/chemical/PA451906",
            "resolved": True,
            "mentions": ["Coumadin", "warfarin"],
        },
        {
            "name": "hydroxybupropion",
            "id": None,
            "source": None,
            "url": None,
            "resolved": False,
            "mentions": ["hydroxybupropion"],
        },
    ]


def test_extract_gene_tags_uses_normalized_symbols_and_preserves_unresolved():
    normalized_mentions = {
        "genes": {
            "IL28B": {
                "matches": [
                    {
                        "normalized_term": "IFNL3",
                        "id": "PA134952671",
                        "source": "pharmgkb",
                        "url": "https://www.clinpgx.org/gene/PA134952671",
                        "ncbi_gene_id": "282617",
                        "hgnc_id": "HGNC:18365",
                    }
                ]
            },
            "IFNL3": {
                "matches": [
                    {
                        "normalized_term": "IFNL3",
                        "id": "PA134952671",
                        "source": "pharmgkb",
                        "url": "https://www.clinpgx.org/gene/PA134952671",
                        "ncbi_gene_id": "282617",
                        "hgnc_id": "HGNC:18365",
                    }
                ]
            },
            "candidate gene": {"matches": []},
        },
        "variants": {},
    }
    with patch(
        "src.jobs.normalize_genes_in_abstract",
        return_value=normalized_mentions,
    ):
        result = extract_gene_tags("IL28B (IFNL3) and candidate gene were studied.")

    assert result == [
        {
            "name": "IFNL3",
            "id": "PA134952671",
            "source": "pharmgkb",
            "url": "https://www.clinpgx.org/gene/PA134952671",
            "resolved": True,
            "mentions": ["IL28B", "IFNL3"],
            "ncbi_gene_id": "282617",
            "hgnc_id": "HGNC:18365",
        },
        {
            "name": "candidate gene",
            "id": None,
            "source": None,
            "url": None,
            "resolved": False,
            "mentions": ["candidate gene"],
            "ncbi_gene_id": None,
            "hgnc_id": None,
        },
    ]


def test_validate_article_markdown_rejects_ncbi_challenge_page():
    markdown = "# Checking your browser - reCAPTCHA\n" + ("challenge " * 100)

    with pytest.raises(ValueError, match="Invalid article content"):
        validate_article_markdown(markdown, "PMC13729000")


def test_validate_article_markdown_accepts_article_content():
    markdown = "# Pharmacokinetics study\n\n## Abstract\n" + ("Study results. " * 50)

    assert validate_article_markdown(markdown, "PMC13729000") == markdown


def test_run_analysis_job_fails_before_extraction_for_challenge_page():
    markdown = "# Checking your browser - reCAPTCHA\n" + ("challenge " * 100)
    with (
        patch("src.jobs._downloader.pmcid_to_markdown", return_value=markdown),
        patch("src.jobs.extract_all_variants") as mock_extract,
        patch("src.jobs.update_job") as mock_update,
    ):
        _run(run_analysis_job("analysis-1", "PMC13729000", pmid="42974700"))

    mock_extract.assert_not_called()
    assert mock_update.call_args_list[-1].kwargs["status"] == "failed"
    assert "Invalid article content" in mock_update.call_args_list[-1].kwargs["error"]


def test_run_analysis_job_does_not_download_a_raw_pmid_as_a_pmcid():
    with (
        patch("src.jobs._downloader.pmcid_to_markdown") as mock_download,
        patch("src.jobs.extract_all_variants") as mock_extract,
        patch("src.jobs.update_job") as mock_update,
    ):
        _run(run_analysis_job("analysis-1", "42974700", pmid="42974700"))

    mock_download.assert_not_called()
    mock_extract.assert_not_called()
    assert mock_update.call_args_list[-1].kwargs["status"] == "failed"
    assert "valid PMCID" in mock_update.call_args_list[-1].kwargs["error"]


class TestRunTriageJobHappyPath:
    def test_run_triage_job_happy_path(self):
        """Full happy-path: 2 PMIDs fetched, scored, and saved to DB."""
        with (
            patch("src.jobs.fetch_weekly_pmids", return_value=_PMID_ENTRIES),
            patch("src.jobs.fetch_pubmed_abstract", side_effect=_ABSTRACTS),
            patch("src.jobs.score_for_va", side_effect=_SCORES),
            patch("src.jobs.update_triage_session_articles") as mock_articles,
            patch("src.jobs.update_triage_session_status") as mock_status,
        ):
            _run(run_triage_job(SESSION_ID, PROJECT_ID, JOB_ID))

        # Final status call should be "completed"
        mock_status.assert_called_with(SESSION_ID, "completed")

        # update_triage_session_articles called once with 2 articles
        mock_articles.assert_called_once()
        _, article_list = (
            mock_articles.call_args.args
            if mock_articles.call_args.args
            else (None, mock_articles.call_args[0][1])
        )
        # Accept both positional and keyword arg styles
        call_args = mock_articles.call_args
        if call_args.args:
            saved_session_id, saved_articles = call_args.args
        else:
            saved_session_id = call_args.kwargs["session_id"]
            saved_articles = call_args.kwargs["articles"]

        assert saved_session_id == SESSION_ID
        assert len(saved_articles) == 2

        # Spot-check first article fields
        first = saved_articles[0]
        assert first["pmid"] == "11111"
        assert first["publication_date"] == "2025-11-07"
        assert first["litsuggest_score"] == 0.99
        assert first["triage_score"] == 85
        assert first["triage_label"] == "relevant"
        assert first["triage_tags"] == [
            "variant_annotations",
            "general_pharmacogenomics",
        ]
        assert first["paper_types"] == ["methods"]
        assert first["paper_type_reasoning"] == "Validates a PGx assay."
        assert first["pgx_information_types"] == ["efficacy"]
        assert first["pgx_information_reasoning"] == "Reports treatment response."
        assert first["drug_tags"] == _DRUG_TAGS[0]
        assert first["gene_tags"] == _GENE_TAGS[0]
        assert first["decision"] == "pending"
        assert first["job_id"] is None

    def test_regeneration_restores_dismissed_and_preserves_submitted(self):
        previous_articles = [
            {"pmid": "11111", "decision": "dismissed", "job_id": None},
            {"pmid": "22222", "decision": "submitted", "job_id": "analysis-2"},
        ]
        with (
            patch(
                "src.jobs.get_triage_session",
                return_value={"articles": previous_articles},
            ),
            patch("src.jobs.fetch_weekly_pmids", return_value=_PMID_ENTRIES),
            patch("src.jobs.fetch_pubmed_abstract", side_effect=_ABSTRACTS),
            patch("src.jobs.score_for_va", side_effect=_SCORES),
            patch("src.jobs.get_job_by_pmid", return_value=None),
            patch("src.jobs.update_triage_session_articles") as mock_articles,
            patch("src.jobs.update_triage_session_status"),
        ):
            _run(run_triage_job(SESSION_ID, PROJECT_ID, JOB_ID, True))

        saved_articles = mock_articles.call_args.args[1]
        assert saved_articles[0]["decision"] == "pending"
        assert saved_articles[0]["job_id"] is None
        assert saved_articles[1]["decision"] == "submitted"
        assert saved_articles[1]["job_id"] == "analysis-2"

    def test_run_triage_job_sets_scoring_status_first(self):
        """The very first status update must be 'scoring'."""
        with (
            patch("src.jobs.fetch_weekly_pmids", return_value=_PMID_ENTRIES),
            patch("src.jobs.fetch_pubmed_abstract", side_effect=_ABSTRACTS),
            patch("src.jobs.score_for_va", side_effect=_SCORES),
            patch("src.jobs.update_triage_session_articles"),
            patch("src.jobs.update_triage_session_status") as mock_status,
        ):
            _run(run_triage_job(SESSION_ID, PROJECT_ID, JOB_ID))

        first_call = mock_status.call_args_list[0]
        assert first_call == call(SESSION_ID, "scoring")

    def test_run_triage_job_handles_empty_pmid_list(self):
        """When LitSuggest returns no PMIDs the session should still complete."""
        with (
            patch("src.jobs.fetch_weekly_pmids", return_value=[]),
            patch("src.jobs.fetch_pubmed_abstract") as mock_abs,
            patch("src.jobs.score_for_va") as mock_score,
            patch("src.jobs.update_triage_session_articles") as mock_articles,
            patch("src.jobs.update_triage_session_status") as mock_status,
        ):
            _run(run_triage_job(SESSION_ID, PROJECT_ID, JOB_ID))

        # No abstract / score calls needed for empty list
        mock_abs.assert_not_called()
        mock_score.assert_not_called()

        # Articles saved as empty list
        mock_articles.assert_called_once()
        call_args = mock_articles.call_args
        if call_args.args:
            saved_articles = call_args.args[1]
        else:
            saved_articles = call_args.kwargs["articles"]
        assert saved_articles == []

        # Status ends at "completed"
        mock_status.assert_called_with(SESSION_ID, "completed")

    def test_run_triage_job_handles_fetch_error(self):
        """When fetch_weekly_pmids raises, status must be set to 'error'."""
        with (
            patch(
                "src.jobs.fetch_weekly_pmids",
                side_effect=requests.HTTPError("503 Service Unavailable"),
            ),
            patch("src.jobs.fetch_pubmed_abstract") as mock_abs,
            patch("src.jobs.score_for_va") as mock_score,
            patch("src.jobs.update_triage_session_articles") as mock_articles,
            patch("src.jobs.update_triage_session_status") as mock_status,
        ):
            _run(run_triage_job(SESSION_ID, PROJECT_ID, JOB_ID))

        # Should NOT reach abstract fetching or scoring
        mock_abs.assert_not_called()
        mock_score.assert_not_called()
        mock_articles.assert_not_called()

        # Error status recorded
        mock_status.assert_called_with(SESSION_ID, "error", error=ANY)
