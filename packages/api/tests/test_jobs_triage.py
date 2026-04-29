"""Tests for run_triage_job in src.jobs."""

import asyncio
from unittest.mock import ANY, call, patch

import pytest
import requests

from src.jobs import run_triage_job

SESSION_ID = "sess-1"
PROJECT_ID = "proj-1"
JOB_ID = "job-1"

_PMID_ENTRIES = [
    {"pmid": "11111", "litsuggest_score": 0.99},
    {"pmid": "22222", "litsuggest_score": 0.95},
]

_ABSTRACTS = [
    {"title": "Title A", "abstract": "Abstract A"},
    {"title": "Title B", "abstract": "Abstract B"},
]

_SCORES = [
    {"score": 85, "label": "relevant", "reasoning": "Good association"},
    {"score": 20, "label": "not_relevant", "reasoning": "No association"},
]


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


class TestRunTriageJobHappyPath:
    def test_run_triage_job_happy_path(self):
        """Full happy-path: 2 PMIDs fetched, scored, and saved to DB."""
        with (
            patch("src.jobs.fetch_weekly_pmids", return_value=_PMID_ENTRIES) as mock_fetch,
            patch("src.jobs.fetch_pubmed_abstract", side_effect=_ABSTRACTS) as mock_abs,
            patch("src.jobs.score_for_va", side_effect=_SCORES) as mock_score,
            patch("src.jobs.update_triage_session_articles") as mock_articles,
            patch("src.jobs.update_triage_session_status") as mock_status,
        ):
            _run(run_triage_job(SESSION_ID, PROJECT_ID, JOB_ID))

        # Final status call should be "completed"
        mock_status.assert_called_with(SESSION_ID, "completed")

        # update_triage_session_articles called once with 2 articles
        mock_articles.assert_called_once()
        _, article_list = mock_articles.call_args.args if mock_articles.call_args.args else (None, mock_articles.call_args[0][1])
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
        assert first["litsuggest_score"] == 0.99
        assert first["triage_score"] == 85
        assert first["triage_label"] == "relevant"
        assert first["decision"] == "pending"
        assert first["job_id"] is None

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
