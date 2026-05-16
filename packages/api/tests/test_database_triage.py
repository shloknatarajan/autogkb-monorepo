import datetime
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

import src.database as db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(fetchone_return=None, fetchall_return=None):
    """Build a mock psycopg2 connection with a configurable cursor."""
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_return
    cur.fetchall.return_value = fetchall_return or []

    # Support `with conn.cursor(...) as cur:` usage
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.closed = 0
    return conn, cur


@contextmanager
def _mock_get_conn(conn):
    yield conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_triage_session_returns_uuid():
    import uuid

    session_uuid = uuid.uuid4()
    conn, cur = _make_conn(fetchone_return=(str(session_uuid),))

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        result = db.create_triage_session("proj1", "My Project", "2026-04-28")

    assert result == str(session_uuid)
    # Confirm it's a valid UUID (no exception raised)
    uuid.UUID(result)
    conn.commit.assert_called_once()


def test_get_triage_session_returns_none_for_missing():
    conn, cur = _make_conn(fetchone_return=None)

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        result = db.get_triage_session("nonexistent-id")

    assert result is None


def test_get_triage_session_returns_dict_when_found():
    row = MagicMock()
    row.__iter__ = MagicMock(
        return_value=iter(
            [
                ("id", "abc-123"),
                ("project_id", "proj1"),
                ("articles", [{"pmid": "12345"}]),
            ]
        )
    )
    # RealDictCursor rows behave like dicts; simulate with a plain dict
    row_dict = {"id": "abc-123", "project_id": "proj1", "articles": [{"pmid": "12345"}]}
    conn, cur = _make_conn(fetchone_return=row_dict)
    # Make cursor() return a context manager that yields cur
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    cur.fetchone.return_value = row_dict

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        result = db.get_triage_session("abc-123")

    assert result is not None
    assert result["project_id"] == "proj1"
    assert result["articles"] == [{"pmid": "12345"}]


def test_list_triage_sessions_returns_list():
    row = {
        "id": "abc-123",
        "project_id": "proj1",
        "project_name": "My Project",
        "week_date": datetime.date(2026, 4, 28),
        "status": "pending",
        "created_at": datetime.datetime(2026, 4, 28, 12, 0, 0),
        "article_count": 3,
    }
    conn, cur = _make_conn(fetchall_return=[row])

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        results = db.list_triage_sessions()

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["article_count"] == 3
    # week_date must be coerced to string
    assert results[0]["week_date"] == "2026-04-28"


def test_update_triage_session_status_executes_update():
    conn, cur = _make_conn()

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        db.update_triage_session_status("sess-1", "running")

    cur.execute.assert_called_once()
    call_args = cur.execute.call_args[0]
    assert "UPDATE triage_sessions" in call_args[0]
    conn.commit.assert_called_once()


def test_update_triage_session_status_with_error_field():
    conn, cur = _make_conn()

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        db.update_triage_session_status("sess-1", "failed", error="Something went wrong")

    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "error" in sql
    assert "Something went wrong" in params


def test_update_triage_session_articles_executes_update():
    conn, cur = _make_conn()
    articles = [{"pmid": "111", "decision": "include"}]

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        db.update_triage_session_articles("sess-1", articles)

    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "UPDATE triage_sessions" in sql
    assert "articles" in sql
    # The articles should be JSON-serialized
    assert json.loads(params[0]) == articles
    conn.commit.assert_called_once()


def test_update_triage_article_decision_executes_jsonb_update():
    conn, cur = _make_conn()

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        db.update_triage_article_decision("sess-1", "99999", "include", job_id="job-42")

    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "jsonb_agg" in sql
    assert params[0] == "99999"
    patch_obj = json.loads(params[1])
    assert patch_obj["decision"] == "include"
    assert patch_obj["job_id"] == "job-42"
    assert params[2] == "sess-1"
    conn.commit.assert_called_once()


def test_update_triage_article_decision_no_job_id():
    conn, cur = _make_conn()

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        db.update_triage_article_decision("sess-1", "99999", "exclude")

    cur.execute.assert_called_once()
    _, params = cur.execute.call_args[0]
    patch_obj = json.loads(params[1])
    assert patch_obj["job_id"] is None
