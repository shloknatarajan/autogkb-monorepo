"""Tests for triage API endpoints."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from src.annotators import DEFAULT_ANNOTATION_METHOD
from src.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_db_init():
    with patch("src.main.init_db"):
        yield


def test_list_triage_sessions_returns_empty():
    """GET /triage/sessions returns 200 with empty list when no sessions exist."""
    with patch("src.main.list_triage_sessions", return_value=[]):
        response = client.get("/triage/sessions")
    assert response.status_code == 200
    assert response.json() == []


def test_get_triage_session_not_found():
    """GET /triage/sessions/{session_id} returns 404 when session does not exist."""
    with patch("src.main.get_triage_session", return_value=None):
        response = client.get("/triage/sessions/bad-id")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_triage_session_returns_session():
    """GET /triage/sessions/{session_id} returns 200 with the correct session data."""
    mock_session = {
        "id": "sess-123",
        "project_id": "proj-1",
        "project_name": "Test Project",
        "week_date": "2026-04-29",
        "status": "completed",
        "articles": [],
        "error": None,
        "created_at": "2026-04-29T00:00:00",
    }
    with patch("src.main.get_triage_session", return_value=mock_session):
        response = client.get("/triage/sessions/sess-123")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "sess-123"
    assert data["project_id"] == "proj-1"
    assert data["status"] == "completed"


def test_get_triage_session_defaults_new_labels_for_legacy_articles():
    legacy_article = {
        "pmid": "12345",
        "pmcid": None,
        "title": "Legacy paper",
        "abstract": None,
        "litsuggest_score": 0.9,
        "triage_score": 80,
        "triage_label": "relevant",
        "triage_tags": ["general_pharmacogenomics"],
        "reasoning": "Existing reasoning.",
        "decision": "pending",
        "job_id": None,
    }
    mock_session = {
        "id": "sess-legacy",
        "project_id": "proj-1",
        "project_name": "Test Project",
        "week_date": "2026-04-29",
        "status": "completed",
        "articles": [legacy_article],
        "error": None,
        "created_at": "2026-04-29T00:00:00",
    }
    with patch("src.main.get_triage_session", return_value=mock_session):
        response = client.get("/triage/sessions/sess-legacy")

    assert response.status_code == 200
    article = response.json()["articles"][0]
    assert article["publication_date"] is None
    assert article["paper_types"] == []
    assert article["paper_type_reasoning"] == ""
    assert article["pgx_information_types"] == []
    assert article["pgx_information_reasoning"] == ""
    assert article["drug_tags"] == []
    assert article["gene_tags"] == []
    assert article["phenotype_tags"] == []


def test_create_triage_sessions_backfills_missing_weeks_from_latest_four():
    weekly_jobs = [
        {
            "id": f"job-{day}",
            "name": f"Automatic Weekly Digest (Oct {day:02d} 2026 to Oct {day + 6:02d} 2026)",
        }
        for day in (18, 11, 4)
    ] + [
        {
            "id": "job-sep-27",
            "name": "Automatic Weekly Digest (Sep 27 2026 to Oct 03 2026)",
        },
        {
            "id": "job-sep-20",
            "name": "Automatic Weekly Digest (Sep 20 2026 to Sep 26 2026)",
        },
    ]

    def find_existing(_project_id, week_date):
        if week_date == "2026-10-18":
            return {"id": "existing-latest"}
        return None

    with (
        patch("src.main.list_weekly_jobs", return_value=weekly_jobs),
        patch("src.main.find_triage_session_by_week", side_effect=find_existing),
        patch(
            "src.main.create_triage_session",
            side_effect=["session-11", "session-04", "session-27"],
        ) as mock_create,
        patch("src.main.run_triage_backfill", new_callable=AsyncMock) as mock_backfill,
    ):
        response = client.post(
            "/triage/sessions",
            json={"project_id": "proj-1", "project_name": "General PGx"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "session_ids": ["session-11", "session-04", "session-27"],
        "latest_session_id": "existing-latest",
        "created_count": 3,
        "existing_count": 1,
        "regenerated_count": 0,
        "active_count": 0,
    }
    assert mock_create.call_count == 3
    mock_backfill.assert_awaited_once_with(
        "proj-1",
        [
            {"session_id": "session-11", "job_id": "job-11", "preserve_submitted": False},
            {"session_id": "session-04", "job_id": "job-4", "preserve_submitted": False},
            {"session_id": "session-27", "job_id": "job-sep-27", "preserve_submitted": False},
        ],
    )


def test_create_triage_sessions_can_regenerate_existing_weeks():
    weekly_jobs = [
        {
            "id": "job-18",
            "name": "Automatic Weekly Digest (Oct 18 2026 to Oct 24 2026)",
        },
        {
            "id": "job-11",
            "name": "Automatic Weekly Digest (Oct 11 2026 to Oct 17 2026)",
        },
    ]
    existing = {
        "2026-10-18": {"id": "session-18", "status": "completed"},
        "2026-10-11": {"id": "session-11", "status": "scoring"},
    }
    with (
        patch("src.main.list_weekly_jobs", return_value=weekly_jobs),
        patch(
            "src.main.find_triage_session_by_week",
            side_effect=lambda _project_id, week_date: existing[week_date],
        ),
        patch("src.main.update_triage_session_status") as mock_status,
        patch("src.main.run_triage_backfill", new_callable=AsyncMock) as mock_backfill,
    ):
        response = client.post(
            "/triage/sessions",
            json={
                "project_id": "proj-1",
                "project_name": "General PGx",
                "weeks": 13,
                "regenerate_existing": True,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "session_ids": ["session-18"],
        "latest_session_id": "session-18",
        "created_count": 0,
        "existing_count": 2,
        "regenerated_count": 1,
        "active_count": 1,
    }
    mock_status.assert_called_once_with("session-18", "pending")
    mock_backfill.assert_awaited_once_with(
        "proj-1",
        [
            {
                "session_id": "session-18",
                "job_id": "job-18",
                "preserve_submitted": True,
            }
        ],
    )


def test_regenerate_triage_session_uses_matching_week_and_preserves_submitted():
    mock_session = {
        "id": "sess-123",
        "project_id": "proj-1",
        "project_name": "Test Project",
        "week_date": "2026-04-19",
        "status": "completed",
        "articles": [],
        "error": None,
        "created_at": "2026-04-29T00:00:00",
    }
    weekly_jobs = [
        {
            "id": "newer-job",
            "name": "Automatic Weekly Digest (Apr 26 2026 to May 02 2026)",
        },
        {
            "id": "matching-job",
            "name": "Automatic Weekly Digest (Apr 19 2026 to Apr 25 2026)",
        },
    ]
    with (
        patch("src.main.get_triage_session", return_value=mock_session),
        patch("src.main.list_weekly_jobs", return_value=weekly_jobs),
        patch("src.main.update_triage_session_status") as mock_status,
        patch("src.main.run_triage_job") as mock_run,
    ):
        response = client.post("/triage/sessions/sess-123/regenerate")

    assert response.status_code == 200
    assert response.json() == {"session_id": "sess-123", "regenerating": True}
    mock_status.assert_called_once_with("sess-123", "pending")
    mock_run.assert_called_once_with(
        "sess-123", "proj-1", "matching-job", True
    )


def test_regenerate_triage_session_rejects_active_session():
    mock_session = {
        "id": "sess-123",
        "project_id": "proj-1",
        "week_date": "2026-04-19",
        "status": "scoring",
        "articles": [],
    }
    with patch("src.main.get_triage_session", return_value=mock_session):
        response = client.post("/triage/sessions/sess-123/regenerate")

    assert response.status_code == 409


def test_submit_all_only_submits_eligible_pending_articles():
    mock_session = {
        "id": "sess-123",
        "project_id": "proj-1",
        "status": "completed",
        "articles": [
            {"pmid": "11111", "pmcid": "PMC11111", "decision": "pending"},
            {"pmid": "22222", "pmcid": "PMC22222", "decision": "dismissed"},
            {"pmid": "33333", "pmcid": "PMC33333", "decision": "submitted"},
            {"pmid": "44444", "pmcid": None, "decision": "pending"},
        ],
    }
    mock_submit = AsyncMock(
        side_effect=[
            {"job_id": "job-1", "pmid": "11111"},
            HTTPException(status_code=409, detail="No full text"),
        ]
    )
    with (
        patch("src.main.get_triage_session", return_value=mock_session),
        patch("src.main._submit_triage_article", mock_submit),
    ):
        response = client.post("/triage/sessions/sess-123/submit-all")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "sess-123",
        "submitted": 1,
        "skipped_dismissed": 1,
        "skipped_submitted": 1,
        "skipped_no_full_text": 1,
    }
    assert mock_submit.await_count == 2
    assert [call.args[:2] for call in mock_submit.await_args_list] == [
        ("sess-123", "11111"),
        ("sess-123", "44444"),
    ]


def test_submit_all_reuses_completed_pdf_without_pmcid():
    session = {
        "id": "sess-123",
        "articles": [{"pmid": "42974700", "pmcid": None, "decision": "pending"}],
    }
    completed_pdf = {
        "id": "pdf-analysis",
        "status": "completed",
        "source": "pdf_upload",
        "markdown_content": "# Pharmacokinetics study\n" + ("Study results. " * 50),
    }
    with (
        patch("src.main.get_triage_session", return_value=session),
        patch("src.main.get_job_by_pmid", return_value=completed_pdf),
        patch("src.main.get_pmcid_from_pmid") as mock_lookup,
        patch("src.main.create_job") as mock_create,
        patch("src.main.update_triage_article_decision") as mock_decision,
    ):
        response = client.post("/triage/sessions/sess-123/submit-all")

    assert response.status_code == 200
    assert response.json()["submitted"] == 1
    assert response.json()["skipped_no_full_text"] == 0
    mock_lookup.assert_not_called()
    mock_create.assert_not_called()
    mock_decision.assert_called_once_with(
        "sess-123", "42974700", "submitted", job_id="pdf-analysis"
    )


def test_submit_triage_article_uses_stored_pmcid_without_lookup():
    session = {
        "id": "sess-123",
        "articles": [
            {"pmid": "42974700", "pmcid": "PMC13729000", "decision": "pending"}
        ],
    }
    with (
        patch("src.main.get_triage_session", return_value=session),
        patch("src.main.get_job_by_pmid", return_value=None),
        patch("src.main.get_pmcid_from_pmid") as mock_lookup,
        patch("src.main.create_job", return_value="analysis-1") as mock_create,
        patch("src.main.run_analysis_job", new_callable=AsyncMock) as mock_run,
        patch("src.main.update_triage_article_decision") as mock_decision,
    ):
        response = client.post(
            "/triage/sessions/sess-123/articles/42974700/submit"
        )

    assert response.status_code == 200
    mock_lookup.assert_not_called()
    mock_create.assert_called_once_with(
        "42974700", pmcid="PMC13729000", source="pmc", method=DEFAULT_ANNOTATION_METHOD
    )
    mock_run.assert_awaited_once_with(
        "analysis-1", "PMC13729000", pmid="42974700"
    )
    mock_decision.assert_called_once_with(
        "sess-123", "42974700", "submitted", job_id="analysis-1"
    )


def test_submit_triage_article_reuses_completed_pdf_without_pmcid():
    session = {
        "id": "sess-123",
        "articles": [{"pmid": "42974700", "pmcid": None, "decision": "pending"}],
    }
    completed_pdf = {
        "id": "pdf-analysis",
        "status": "completed",
        "source": "pdf_upload",
        "markdown_content": "# Pharmacokinetics study\n" + ("Study results. " * 50),
    }
    with (
        patch("src.main.get_triage_session", return_value=session),
        patch("src.main.get_job_by_pmid", return_value=completed_pdf),
        patch("src.main.get_pmcid_from_pmid") as mock_lookup,
        patch("src.main.create_job") as mock_create,
        patch("src.main.run_analysis_job", new_callable=AsyncMock) as mock_run,
        patch("src.main.update_triage_article_decision") as mock_decision,
    ):
        response = client.post(
            "/triage/sessions/sess-123/articles/42974700/submit"
        )

    assert response.status_code == 200
    assert response.json() == {"job_id": "pdf-analysis", "pmid": "42974700"}
    mock_lookup.assert_not_called()
    mock_create.assert_not_called()
    mock_run.assert_not_awaited()
    mock_decision.assert_called_once_with(
        "sess-123", "42974700", "submitted", job_id="pdf-analysis"
    )


def test_submit_triage_article_does_not_start_without_valid_pmcid():
    session = {
        "id": "sess-123",
        "articles": [{"pmid": "42974700", "pmcid": None, "decision": "pending"}],
    }
    with (
        patch("src.main.get_triage_session", return_value=session),
        patch("src.main.get_job_by_pmid", return_value=None),
        patch("src.main.get_pmcid_from_pmid", side_effect=RuntimeError("blocked")),
        patch("src.main.create_job") as mock_create,
        patch("src.main.run_analysis_job", new_callable=AsyncMock) as mock_run,
    ):
        response = client.post(
            "/triage/sessions/sess-123/articles/42974700/submit"
        )

    assert response.status_code == 409
    assert "pipeline was not started" in response.json()["detail"]
    mock_create.assert_not_called()
    mock_run.assert_not_awaited()


def test_submit_triage_article_replaces_poisoned_completed_job():
    session = {
        "id": "sess-123",
        "articles": [
            {"pmid": "42974700", "pmcid": "PMC13729000", "decision": "pending"}
        ],
    }
    poisoned = {
        "id": "bad-analysis",
        "status": "completed",
        "markdown_content": "# Checking your browser - reCAPTCHA\n" + ("x" * 600),
    }
    with (
        patch("src.main.get_triage_session", return_value=session),
        patch("src.main.get_job_by_pmid", return_value=poisoned),
        patch("src.main.create_job", return_value="replacement") as mock_create,
        patch("src.main.run_analysis_job", new_callable=AsyncMock) as mock_run,
        patch("src.main.update_triage_article_decision"),
    ):
        response = client.post(
            "/triage/sessions/sess-123/articles/42974700/submit"
        )

    assert response.status_code == 200
    mock_create.assert_called_once_with(
        "42974700", pmcid="PMC13729000", source="pmc", method=DEFAULT_ANNOTATION_METHOD
    )
    mock_run.assert_awaited_once_with(
        "replacement", "PMC13729000", pmid="42974700"
    )


def test_regenerate_refetches_poisoned_pmc_content():
    poisoned = {
        "id": "bad-analysis",
        "pmid": "42974700",
        "pmcid": None,
        "source": "pmc",
        "status": "completed",
        "markdown_content": "# Checking your browser - reCAPTCHA\n" + ("x" * 600),
    }
    with (
        patch("src.main.get_job_by_pmid", return_value=poisoned),
        patch(
            "src.main.get_pmcid_from_pmid",
            return_value={"42974700": "PMC13729000"},
        ),
        patch("src.main.create_job", return_value="replacement") as mock_create,
        patch("src.main.run_analysis_job", new_callable=AsyncMock) as mock_run,
        patch("src.main.run_reanalysis_job", new_callable=AsyncMock) as mock_reanalyze,
    ):
        response = client.post("/regenerate", json={"pmid": "42974700"})

    assert response.status_code == 200
    assert response.json()["pmcid"] == "PMC13729000"
    mock_create.assert_called_once_with(
        "42974700", pmcid="PMC13729000", source="pmc", method=DEFAULT_ANNOTATION_METHOD
    )
    mock_run.assert_awaited_once_with(
        "replacement", "PMC13729000", pmid="42974700", method=DEFAULT_ANNOTATION_METHOD
    )
    mock_reanalyze.assert_not_awaited()


def test_analyze_pmcid_replaces_poisoned_completed_job():
    poisoned = {
        "id": "bad-analysis",
        "pmid": "42974700",
        "pmcid": "PMC13729000",
        "source": "pmc",
        "status": "completed",
        "markdown_content": "# Checking your browser - reCAPTCHA\n" + ("x" * 600),
    }
    with (
        patch("src.main._get_pmid_from_pmcid_safe", return_value="42974700"),
        patch("src.main.get_job_by_pmid", return_value=poisoned),
        patch("src.main.create_job", return_value="replacement") as mock_create,
        patch("src.main.run_analysis_job", new_callable=AsyncMock) as mock_run,
    ):
        response = client.post("/analyze", json={"pmcid": "PMC13729000"})

    assert response.status_code == 200
    assert response.json()["job_id"] == "replacement"
    assert response.json()["status"] == "pending"
    mock_create.assert_called_once_with(
        "42974700", pmcid="PMC13729000", source="pmc", method=DEFAULT_ANNOTATION_METHOD
    )
    mock_run.assert_awaited_once_with(
        "replacement", "PMC13729000", pmid="42974700", method=DEFAULT_ANNOTATION_METHOD
    )


def test_update_article_decision_dismissed():
    """PATCH /triage/sessions/{session_id}/articles/{pmid} returns 200 for dismissed decision."""
    mock_session = {
        "id": "sess-1",
        "project_id": "proj-1",
        "project_name": "Test Project",
        "week_date": "2026-04-29",
        "status": "completed",
        "articles": [],
        "error": None,
        "created_at": "2026-04-29T00:00:00",
    }
    with (
        patch("src.main.get_triage_session", return_value=mock_session),
        patch("src.main.update_triage_article_decision") as mock_update,
    ):
        response = client.patch(
            "/triage/sessions/sess-1/articles/12345",
            json={"decision": "dismissed"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "dismissed"
    assert data["session_id"] == "sess-1"
    assert data["pmid"] == "12345"
    mock_update.assert_called_once_with("sess-1", "12345", "dismissed")


def test_update_article_decision_invalid():
    """PATCH /triage/sessions/{session_id}/articles/{pmid} returns 422 for invalid decision."""
    response = client.patch(
        "/triage/sessions/sess-1/articles/12345",
        json={"decision": "invalid"},
    )
    assert response.status_code == 422


def test_annotation_methods_lists_registered_methods_with_one_default():
    response = client.get("/annotation-methods")
    assert response.status_code == 200
    methods = {m["name"]: m for m in response.json()}
    assert {"grounded_v2", "sentences_v5"} <= set(methods)
    assert [m["name"] for m in methods.values() if m["default"]] == [
        DEFAULT_ANNOTATION_METHOD
    ]


def test_regenerate_rejects_unknown_method():
    with patch("src.main.create_job") as mock_create:
        response = client.post(
            "/regenerate", json={"pmid": "42974700", "method": "does_not_exist"}
        )
    assert response.status_code == 422
    assert "Available" in response.json()["detail"]
    mock_create.assert_not_called()


def test_regenerate_passes_requested_method_to_reanalysis():
    existing = {
        "id": "old",
        "pmid": "42974700",
        "pmcid": "PMC13729000",
        "source": "pdf_upload",
        "status": "completed",
        "markdown_content": "# Title\n\n" + ("Valid article text. " * 60),
    }
    with (
        patch("src.main.get_job_by_pmid", return_value=existing),
        patch("src.main.create_job", return_value="new-run"),
        patch("src.main.run_reanalysis_job", new_callable=AsyncMock) as mock_run,
    ):
        response = client.post(
            "/regenerate", json={"pmid": "42974700", "method": "sentences_v5"}
        )
    assert response.status_code == 200
    assert mock_run.await_args.kwargs["method"] == "sentences_v5"


def test_runs_endpoint_lists_every_run_with_metadata():
    runs = [
        {
            "id": "b",
            "status": "completed",
            "created_at": "2026-11-20",
            "updated_at": "2026-11-20",
            "generation_metadata": {"annotation_method": "grounded_v2"},
        },
        {
            "id": "a",
            "status": "completed",
            "created_at": "2026-11-19",
            "updated_at": "2026-11-19",
            "generation_metadata": {"sentence_generation_method": "batch_judge_ask"},
        },
    ]
    with patch("src.main.list_runs_by_pmid", return_value=runs):
        response = client.get("/jobs/pmid/42974700/runs")
    assert response.status_code == 200
    assert [r["job_id"] for r in response.json()] == ["b", "a"]
    assert response.json()[0]["generation_metadata"]["annotation_method"] == "grounded_v2"


def _completed_job(metadata):
    return {
        "id": "stored",
        "pmid": "42974700",
        "pmcid": "PMC13729000",
        "source": "pmc",
        "status": "completed",
        "markdown_content": "# Title\n\n" + ("Valid article text. " * 60),
        "annotation_data": {"result": {"associations": [{"variant_id": "rs1"}]}},
        "generation_metadata": metadata,
    }


def test_analyze_pmid_without_method_reuses_newest_run_of_any_method():
    stored = _completed_job({"annotation_method": "sentences_v5"})
    with (
        patch("src.main.get_pmcid_from_pmid", return_value={"42974700": "PMC13729000"}),
        patch("src.main.get_job_by_pmid", return_value=stored),
        patch("src.main.find_run_by_method") as mock_find,
        patch("src.main.create_job") as mock_create,
    ):
        response = client.post("/analyze/pmid", json={"pmid": "42974700"})

    assert response.json()["job_id"] == "stored"
    mock_find.assert_not_called()
    mock_create.assert_not_called()


@pytest.mark.parametrize("status", ["completed", "generating_sentences"])
def test_analyze_pmid_returns_existing_run_of_the_requested_method(status):
    """A retry while the first run is in flight must not start a second one."""
    stored = _completed_job({"annotation_method": "grounded_v2"})
    stored["status"] = status
    if status != "completed":
        stored["annotation_data"] = None
    with (
        patch("src.main.get_pmcid_from_pmid", return_value={"42974700": "PMC13729000"}),
        patch("src.main.get_job_by_pmid") as mock_newest,
        patch("src.main.find_run_by_method", return_value=stored) as mock_find,
        patch("src.main.create_job") as mock_create,
    ):
        response = client.post(
            "/analyze/pmid", json={"pmid": "42974700", "method": "grounded_v2"}
        )

    assert response.json()["job_id"] == "stored"
    assert response.json()["status"] == status
    mock_find.assert_called_once_with("grounded_v2", pmid="42974700", pmcid=None)
    mock_newest.assert_not_called()
    mock_create.assert_not_called()


def test_analyze_pmid_starts_and_tags_a_run_when_the_method_has_none():
    with (
        patch("src.main.get_pmcid_from_pmid", return_value={"42974700": "PMC13729000"}),
        patch("src.main.find_run_by_method", return_value=None),
        patch("src.main.create_job", return_value="new-run") as mock_create,
        patch("src.main.run_analysis_job", new_callable=AsyncMock),
    ):
        response = client.post(
            "/analyze/pmid", json={"pmid": "42974700", "method": "grounded_v2"}
        )

    assert response.json()["job_id"] == "new-run"
    assert mock_create.call_args.kwargs["method"] == "grounded_v2"


def test_analyze_pmcid_starts_a_new_run_for_a_different_method():
    with (
        patch("src.main._get_pmid_from_pmcid_safe", return_value="42974700"),
        patch("src.main.find_run_by_method", return_value=None) as mock_find,
        patch("src.main.create_job", return_value="new-run"),
        patch("src.main.run_analysis_job", new_callable=AsyncMock) as mock_run,
    ):
        response = client.post(
            "/analyze", json={"pmcid": "PMC13729000", "method": "grounded_v2"}
        )

    assert response.json()["job_id"] == "new-run"
    mock_find.assert_called_once_with(
        "grounded_v2", pmid="42974700", pmcid="PMC13729000"
    )
    assert mock_run.await_args.kwargs["method"] == "grounded_v2"


def test_analyze_pmcid_returns_in_flight_run_of_the_requested_method():
    stored = _completed_job({"annotation_method": "grounded_v2"})
    stored["status"] = "pending"
    with (
        patch("src.main._get_pmid_from_pmcid_safe", return_value="42974700"),
        patch("src.main.find_run_by_method", return_value=stored),
        patch("src.main.create_job") as mock_create,
    ):
        response = client.post(
            "/analyze", json={"pmcid": "PMC13729000", "method": "grounded_v2"}
        )

    assert response.json()["job_id"] == "stored"
    mock_create.assert_not_called()


def test_job_response_exposes_structured_associations():
    from src.database import _extract_annotation_data

    record = {"variants": [{"name": "rs1"}], "direction": "increased"}
    row = _extract_annotation_data(
        {
            "pmid": "1",
            "json_content": {
                "annotations": {"rs1": []},
                "annotation_citations": [],
                "annotation_data": {"structured_associations": [record]},
            },
        }
    )
    assert row["annotation_data"]["result"]["structured_associations"] == [record]
    assert row["annotation_data"]["result"]["variants"] == ["rs1"]


def test_unknown_default_method_fails_at_import():
    import importlib

    import src.annotators as annotators

    with patch.dict(os.environ, {"ANNOTATION_METHOD": "grounded_v3"}):
        with pytest.raises(RuntimeError, match="grounded_v3"):
            importlib.reload(annotators)
    importlib.reload(annotators)


def test_get_triage_session_marks_in_flight_pdf_upload_as_submitted():
    article = {
        "pmid": "43051034",
        "pmcid": None,
        "title": "PDF uploaded paper",
        "abstract": None,
        "litsuggest_score": 0.92,
        "triage_score": 85,
        "triage_label": "relevant",
        "reasoning": "PGx study.",
        "decision": "pending",
        "job_id": None,
    }
    mock_session = {
        "id": "sess-inflight",
        "project_id": "proj-1",
        "project_name": "Test Project",
        "week_date": "2026-11-20",
        "status": "completed",
        "articles": [article],
        "error": None,
        "created_at": "2026-11-20T00:00:00",
    }
    with (
        patch("src.main.get_triage_session", return_value=mock_session),
        patch(
            "src.main.get_latest_active_or_completed_jobs_for_pmids",
            return_value={
                "43051034": {
                    "id": "pdf-job-1",
                    "pmid": "43051034",
                    "source": "pdf_upload",
                    "status": "fetching_article",
                }
            },
        ),
    ):
        response = client.get("/triage/sessions/sess-inflight")

    assert response.status_code == 200
    enriched = response.json()["articles"][0]
    assert enriched["decision"] == "submitted"
    assert enriched["job_id"] == "pdf-job-1"
    assert enriched["job_source"] == "pdf_upload"


def test_submit_triage_article_rejects_in_flight_job_and_syncs_decision():
    session = {
        "id": "sess-123",
        "articles": [
            {"pmid": "43051034", "pmcid": "PMC1234567", "decision": "pending"}
        ],
    }
    with (
        patch("src.main.get_triage_session", return_value=session),
        patch(
            "src.main.get_job_by_pmid",
            return_value={"id": "pdf-job-1", "status": "fetching_article"},
        ),
        patch("src.main.update_triage_article_decision") as mock_update,
        patch("src.main.create_job") as mock_create,
        patch("src.main.run_analysis_job", new_callable=AsyncMock) as mock_run,
    ):
        response = client.post("/triage/sessions/sess-123/articles/43051034/submit")

    assert response.status_code == 409
    assert "already being processed" in response.json()["detail"]
    mock_update.assert_called_once_with(
        "sess-123", "43051034", "submitted", job_id="pdf-job-1"
    )
    mock_create.assert_not_called()
    mock_run.assert_not_awaited()


def test_submit_all_skips_in_flight_article_as_submitted():
    session = {
        "id": "sess-123",
        "articles": [
            {"pmid": "43051034", "pmcid": "PMC1234567", "decision": "pending"}
        ],
    }
    with (
        patch("src.main.get_triage_session", return_value=session),
        patch(
            "src.main.get_job_by_pmid",
            return_value={"id": "pdf-job-1", "status": "pending"},
        ),
        patch("src.main.update_triage_article_decision") as mock_update,
        patch("src.main.create_job") as mock_create,
    ):
        response = client.post("/triage/sessions/sess-123/submit-all")

    assert response.status_code == 200
    body = response.json()
    assert body["submitted"] == 0
    assert body["skipped_submitted"] == 1
    assert body["skipped_no_full_text"] == 0
    mock_update.assert_called_once_with(
        "sess-123", "43051034", "submitted", job_id="pdf-job-1"
    )
    mock_create.assert_not_called()


def test_regenerate_uploaded_pdfs_session_rescores_without_litsuggest():
    session = {
        "id": "sess-uploaded-nov",
        "project_id": "uploaded_pdfs",
        "project_name": "Uploaded PDFs",
        "week_date": "2026-11-01",
        "status": "completed",
        "articles": [{"pmid": "43051034", "decision": "submitted", "job_id": "job-1"}],
    }
    with (
        patch("src.main.get_triage_session", return_value=session),
        patch("src.main.update_triage_session_status") as mock_status,
        patch("src.main.list_weekly_jobs") as mock_litsuggest,
        patch(
            "src.main.run_uploaded_pdf_triage_rescore", new_callable=AsyncMock
        ) as mock_rescore,
    ):
        response = client.post("/triage/sessions/sess-uploaded-nov/regenerate")

    assert response.status_code == 200
    assert response.json() == {"session_id": "sess-uploaded-nov", "regenerating": True}
    mock_status.assert_called_once_with("sess-uploaded-nov", "pending")
    mock_litsuggest.assert_not_called()
    mock_rescore.assert_awaited_once_with("sess-uploaded-nov")


def test_update_triage_article_tags_resolves_and_persists():
    updated_article = {
        "pmid": "43051034",
        "pmcid": None,
        "title": "Sample Study",
        "abstract": "Abstract",
        "publication_date": "2026-11-10",
        "litsuggest_score": 0.9,
        "triage_score": 80,
        "triage_label": "relevant",
        "triage_tags": ["variant_annotations"],
        "paper_types": ["review"],
        "paper_type_reasoning": "",
        "pgx_information_types": ["dosage"],
        "pgx_information_reasoning": "",
        "drug_tags": [
            {
                "name": "warfarin",
                "id": "PA451906",
                "source": "pharmgkb",
                "url": "https://www.clinpgx.org/chemical/PA451906",
                "resolved": True,
                "mentions": ["warfarin"],
            }
        ],
        "gene_tags": [
            {
                "name": "CYP2C9",
                "id": "PA126",
                "source": "pharmgkb",
                "url": "https://www.clinpgx.org/gene/PA126",
                "resolved": True,
                "mentions": ["CYP2C9"],
                "ncbi_gene_id": "1559",
                "hgnc_id": "HGNC:2623",
            }
        ],
        "phenotype_tags": [
            {
                "name": "Poor Metabolizer",
                "id": None,
                "source": "cpic",
                "url": None,
                "resolved": True,
                "mentions": ["poor metabolizer"],
                "categories": [],
                "directions": [],
                "drugs": [],
                "genes": [],
            }
        ],
        "reasoning": "Original reasoning",
        "decision": "pending",
        "job_id": None,
    }
    with (
        patch(
            "src.main.resolve_manual_drug_tags",
            return_value=updated_article["drug_tags"],
        ) as mock_drugs,
        patch(
            "src.main.resolve_manual_gene_tags",
            return_value=updated_article["gene_tags"],
        ) as mock_genes,
        patch(
            "src.main.resolve_manual_phenotype_tags",
            return_value=updated_article["phenotype_tags"],
        ) as mock_phenotypes,
        patch(
            "src.main.update_triage_article_fields",
            return_value=updated_article,
        ) as mock_update,
        patch(
            "src.main.get_latest_active_or_completed_jobs_for_pmids",
            return_value={},
        ),
    ):
        response = client.patch(
            "/triage/sessions/sess-1/articles/43051034/tags",
            json={
                "paper_types": ["review"],
                "pgx_information_types": ["dosage"],
                "drug_tags": [{"name": "warfarin"}],
                "gene_tags": [{"name": "CYP2C9"}],
                "phenotype_tags": [{"name": "poor metabolizer"}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["triage_label"] == "relevant"
    assert body["paper_types"] == ["review"]
    assert body["drug_tags"][0]["id"] == "PA451906"
    mock_drugs.assert_called_once()
    mock_genes.assert_called_once()
    mock_phenotypes.assert_called_once()
    mock_update.assert_called_once()
    assert mock_update.call_args.kwargs == {
        "edit_action": "manual_edit",
        "reject_if_busy": True,
    }


def test_update_triage_article_tags_returns_409_when_regenerating():
    from src.database import TriageArticleBusyError

    with patch(
        "src.main.update_triage_article_fields",
        side_effect=TriageArticleBusyError("Tags are currently being regenerated"),
    ):
        response = client.patch(
            "/triage/sessions/sess-1/articles/43051034/tags",
            json={"paper_types": ["review"]},
        )

    assert response.status_code == 409
    assert "regenerated" in response.json()["detail"].lower()


def test_update_triage_article_tags_rejects_invalid_values():
    resp = client.patch(
        "/triage/sessions/sess-1/articles/43051034/tags",
        json={"pgx_information_types": ["bogus"]},
    )
    assert resp.status_code == 422

    resp2 = client.patch(
        "/triage/sessions/sess-1/articles/43051034/tags",
        json={"paper_types": ["unknown_type"]},
    )
    assert resp2.status_code == 422


def test_regenerate_triage_article_tags_starts_background_task_and_returns_immediately():
    from datetime import datetime, timezone

    existing_article = {
        "pmid": "43051034",
        "pmcid": "PMC1234567",
        "title": "Old Title",
        "abstract": "CYP2D6 warfarin abstract",
        "publication_date": "2026-11-10",
        "litsuggest_score": 0.85,
        "triage_score": 20,
        "triage_label": "not_relevant",
        "triage_tags": [],
        "paper_types": [],
        "paper_type_reasoning": "",
        "pgx_information_types": [],
        "pgx_information_reasoning": "",
        "drug_tags": [],
        "gene_tags": [],
        "phenotype_tags": [],
        "reasoning": "Old",
        "decision": "pending",
        "job_id": None,
    }
    in_flight_article = {
        **existing_article,
        "tags_regenerating": True,
        "tags_regenerating_started_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "tags_regeneration_error": None,
    }

    with (
        patch(
            "src.main.update_triage_article_fields",
            return_value=in_flight_article,
        ) as mock_update,
        patch(
            "src.main.run_triage_article_tag_regeneration",
            new_callable=AsyncMock,
        ) as mock_bg_regen,
        patch(
            "src.main.get_latest_active_or_completed_jobs_for_pmids",
            return_value={},
        ),
        patch(
            "src.main.get_article_original_tag_baseline",
            return_value=None,
        ),
    ):
        response = client.post(
            "/triage/sessions/sess-1/articles/43051034/tags/regenerate"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["pmid"] == "43051034"
    assert body["tags_regenerating"] is True
    assert body["tags_regeneration_error"] is None
    mock_update.assert_called_once()
    update_patch = mock_update.call_args.args[2]
    assert update_patch["tags_regenerating"] is True
    assert update_patch["tags_regeneration_error"] is None
    assert mock_update.call_args.kwargs == {"reject_if_busy": True}
    mock_bg_regen.assert_awaited_once_with("sess-1", "43051034")


def test_regenerate_triage_article_tags_rejects_already_regenerating():
    from src.database import TriageArticleBusyError

    with (
        patch(
            "src.main.update_triage_article_fields",
            side_effect=TriageArticleBusyError("Tags are currently being regenerated"),
        ),
        patch(
            "src.main.run_triage_article_tag_regeneration",
            new_callable=AsyncMock,
        ) as mock_bg_regen,
    ):
        response = client.post(
            "/triage/sessions/sess-1/articles/43051034/tags/regenerate"
        )

    assert response.status_code == 409
    mock_bg_regen.assert_not_awaited()


def test_revert_triage_article_tags_restores_original_from_provenance():
    reverted_article = {
        "pmid": "43051034",
        "pmcid": "PMC1234567",
        "title": "Sample Study",
        "abstract": "Abstract",
        "publication_date": "2026-11-10",
        "litsuggest_score": 0.9,
        "triage_score": 80,
        "triage_label": "relevant",
        "triage_tags": ["variant_annotations"],
        "paper_types": ["methods"],
        "paper_type_reasoning": "Methods paper",
        "pgx_information_types": ["pd"],
        "pgx_information_reasoning": "PD study",
        "drug_tags": [],
        "gene_tags": [],
        "phenotype_tags": [],
        "reasoning": "Original reasoning",
        "decision": "pending",
        "job_id": None,
    }
    with (
        patch(
            "src.main.revert_triage_article_tags",
            return_value=(reverted_article, True),
        ) as mock_revert,
        patch(
            "src.main.get_latest_active_or_completed_jobs_for_pmids",
            return_value={},
        ),
    ):
        response = client.post(
            "/triage/sessions/sess-1/articles/43051034/tags/revert"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["paper_types"] == ["methods"]
    assert body["pgx_information_types"] == ["pd"]
    assert body["can_revert_tags"] is False
    mock_revert.assert_called_once_with("sess-1", "43051034")


def test_revert_triage_article_tags_returns_409_without_history():
    with patch(
        "src.main.revert_triage_article_tags",
        return_value=({"pmid": "43051034"}, False),
    ):
        response = client.post(
            "/triage/sessions/sess-1/articles/43051034/tags/revert"
        )

    assert response.status_code == 409
    assert "history" in response.json()["detail"].lower()


def test_revert_triage_article_tags_returns_409_when_regenerating():
    from src.database import TriageArticleBusyError

    with patch(
        "src.main.revert_triage_article_tags",
        side_effect=TriageArticleBusyError("Tags are currently being regenerated"),
    ):
        response = client.post(
            "/triage/sessions/sess-1/articles/43051034/tags/revert"
        )

    assert response.status_code == 409
    assert "regenerated" in response.json()["detail"].lower()


def test_get_triage_session_exposes_can_revert_and_tags_regenerating():
    from datetime import datetime, timezone

    article = {
        "pmid": "43051034",
        "pmcid": None,
        "title": "Edited paper",
        "abstract": None,
        "litsuggest_score": 0.9,
        "triage_score": 80,
        "triage_label": "relevant",
        "paper_types": ["review"],
        "pgx_information_types": [],
        "drug_tags": [],
        "gene_tags": [],
        "phenotype_tags": [],
        "reasoning": "PGx study.",
        "decision": "pending",
        "job_id": None,
        "tags_regenerating": True,
        "tags_regenerating_started_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_session = {
        "id": "sess-prov",
        "project_id": "proj-1",
        "project_name": "Test Project",
        "week_date": "2026-11-20",
        "status": "completed",
        "articles": [article],
        "error": None,
        "created_at": "2026-11-20T00:00:00",
    }
    baseline = {
        "paper_types": ["methods"],
        "pgx_information_types": [],
        "drug_tags": [],
        "gene_tags": [],
        "phenotype_tags": [],
    }
    with (
        patch("src.main.get_triage_session", return_value=mock_session),
        patch(
            "src.main.get_latest_active_or_completed_jobs_for_pmids",
            return_value={},
        ),
        patch(
            "src.main.get_session_original_tag_baselines",
            return_value={"43051034": baseline},
        ),
    ):
        response = client.get("/triage/sessions/sess-prov")

    assert response.status_code == 200
    returned = response.json()["articles"][0]
    assert returned["can_revert_tags"] is True
    assert returned["tags_regenerating"] is True

