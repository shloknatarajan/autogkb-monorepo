"""Tests for triage API endpoints."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

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
