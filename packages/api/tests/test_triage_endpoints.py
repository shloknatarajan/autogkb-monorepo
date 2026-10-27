"""Tests for triage API endpoints."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

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
    }
    assert mock_create.call_count == 3
    mock_backfill.assert_awaited_once_with(
        "proj-1",
        [
            {"session_id": "session-11", "job_id": "job-11"},
            {"session_id": "session-04", "job_id": "job-4"},
            {"session_id": "session-27", "job_id": "job-sep-27"},
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
    mock_submit = AsyncMock(return_value={"job_id": "job-1", "pmid": "11111"})
    with (
        patch("src.main.get_triage_session", return_value=mock_session),
        patch("src.main.submit_triage_article", mock_submit),
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
    assert mock_submit.await_count == 1
    assert mock_submit.await_args.args[:2] == ("sess-123", "11111")


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
