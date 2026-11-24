import datetime
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

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
        db.update_triage_session_status(
            "sess-1", "failed", error="Something went wrong"
        )

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


def test_update_triage_article_fields_records_tag_edit_history():
    existing_article = {
        "pmid": "43051034",
        "paper_types": ["methods"],
        "pgx_information_types": ["pd"],
        "drug_tags": [{"name": "warfarin", "id": "PA451906"}],
        "gene_tags": [],
        "phenotype_tags": [],
    }
    conn, cur = _make_conn(fetchone_return={"articles": [existing_article]})

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        updated = db.update_triage_article_fields(
            "sess-1",
            "43051034",
            {
                "paper_types": ["review"],
                "drug_tags": [{"name": "clopidogrel", "id": "PA449053"}],
            },
            edit_action="manual_edit",
            edited_by="curator@example.org",
        )

    assert updated is not None
    assert updated["paper_types"] == ["review"]
    executed_sql = [call[0][0] for call in cur.execute.call_args_list]
    assert any("pg_advisory_xact_lock" in sql for sql in executed_sql)
    assert any("UPDATE triage_sessions SET articles" in sql for sql in executed_sql)
    insert_calls = [
        call for call in cur.execute.call_args_list
        if "INSERT INTO triage_tag_edits" in call[0][0]
    ]
    assert len(insert_calls) == 1
    _, params = insert_calls[0][0]
    assert params[0] == "sess-1"
    assert params[1] == "43051034"
    assert params[2] == "manual_edit"
    assert json.loads(params[3])["paper_types"] == ["methods"]
    assert json.loads(params[3])["drug_tags"] == [{"name": "warfarin", "id": "PA451906"}]
    assert json.loads(params[4])["paper_types"] == ["review"]
    assert json.loads(params[4])["drug_tags"] == [
        {"name": "clopidogrel", "id": "PA449053"}
    ]
    assert params[5] == "curator@example.org"
    conn.commit.assert_called_once()


def test_update_triage_article_fields_skips_history_when_tags_unchanged():
    existing_article = {
        "pmid": "43051034",
        "paper_types": ["methods"],
        "pgx_information_types": ["pd"],
        "drug_tags": [],
        "gene_tags": [],
        "phenotype_tags": [],
    }
    conn, cur = _make_conn(fetchone_return={"articles": [existing_article]})

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        updated = db.update_triage_article_fields(
            "sess-1",
            "43051034",
            {"paper_types": ["methods"], "pgx_information_types": ["pd"]},
            edit_action="manual_edit",
        )

    assert updated is not None
    insert_calls = [
        call for call in cur.execute.call_args_list
        if "INSERT INTO triage_tag_edits" in call[0][0]
    ]
    assert insert_calls == []
    conn.commit.assert_called_once()


def test_update_triage_article_fields_records_regenerate_even_when_unchanged():
    existing_article = {
        "pmid": "43051034",
        "paper_types": ["methods"],
        "pgx_information_types": ["pd"],
        "drug_tags": [],
        "gene_tags": [],
        "phenotype_tags": [],
    }
    conn, cur = _make_conn(fetchone_return={"articles": [existing_article]})

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        updated = db.update_triage_article_fields(
            "sess-1",
            "43051034",
            {"paper_types": ["methods"], "pgx_information_types": ["pd"]},
            edit_action="regenerate",
        )

    assert updated is not None
    insert_calls = [
        call for call in cur.execute.call_args_list
        if "INSERT INTO triage_tag_edits" in call[0][0]
    ]
    assert len(insert_calls) == 1
    assert insert_calls[0][0][1][2] == "regenerate"


def test_compute_original_tag_baselines_tracks_initial_and_post_regenerate():
    original = {
        "paper_types": ["methods"],
        "pgx_information_types": ["pd"],
        "drug_tags": [{"name": "warfarin"}],
        "gene_tags": [],
        "phenotype_tags": [],
    }
    manual_1 = {**original, "paper_types": ["review"]}
    manual_2 = {**original, "paper_types": ["implementation"]}
    regenerated = {**original, "paper_types": ["allele_function"]}
    manual_3 = {**regenerated, "pgx_information_types": ["toxicity"]}

    baselines = db._compute_original_tag_baselines(
        [
            {"pmid": "111", "action": "manual_edit", "previous_tags": original, "new_tags": manual_1},
            {"pmid": "111", "action": "manual_edit", "previous_tags": manual_1, "new_tags": manual_2},
            {"pmid": "222", "action": "manual_edit", "previous_tags": original, "new_tags": manual_1},
            {"pmid": "222", "action": "regenerate", "previous_tags": manual_1, "new_tags": regenerated},
            {"pmid": "222", "action": "manual_edit", "previous_tags": regenerated, "new_tags": manual_3},
        ]
    )

    assert baselines["111"] == original
    assert baselines["222"] == regenerated
    assert db.article_can_revert_tags({"pmid": "111", **manual_2}, baselines["111"]) is True
    assert db.article_can_revert_tags({"pmid": "111", **original}, baselines["111"]) is False
    assert db.article_can_revert_tags({"pmid": "222", **manual_3}, baselines["222"]) is True
    assert db.article_can_revert_tags({"pmid": "222", **regenerated}, baselines["222"]) is False


def test_revert_triage_article_tags_restores_original_and_records_audit_row():
    original_tags = {
        "paper_types": ["methods"],
        "pgx_information_types": ["pd"],
        "drug_tags": [{"name": "warfarin", "id": "PA451906"}],
        "gene_tags": [],
        "phenotype_tags": [],
    }
    edited_article = {
        "pmid": "43051034",
        "title": "Study",
        "paper_types": ["review"],
        "pgx_information_types": ["dosage"],
        "drug_tags": [{"name": "clopidogrel", "id": "PA449053"}],
        "gene_tags": [],
        "phenotype_tags": [],
    }
    conn, cur = _make_conn(
        fetchone_return={"articles": [edited_article]},
        fetchall_return=[
            {
                "pmid": "43051034",
                "action": "manual_edit",
                "previous_tags": original_tags,
                "new_tags": db._extract_triage_tag_snapshot(edited_article),
            }
        ],
    )

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        reverted, had_history = db.revert_triage_article_tags("sess-1", "43051034")

    assert had_history is True
    assert reverted is not None
    assert reverted["paper_types"] == ["methods"]
    assert reverted["pgx_information_types"] == ["pd"]
    assert reverted["drug_tags"] == [{"name": "warfarin", "id": "PA451906"}]
    insert_calls = [
        call for call in cur.execute.call_args_list
        if "INSERT INTO triage_tag_edits" in call[0][0]
    ]
    assert len(insert_calls) == 1
    _, params = insert_calls[0][0]
    assert params[2] == "revert"
    assert json.loads(params[3])["paper_types"] == ["review"]
    assert json.loads(params[4])["paper_types"] == ["methods"]
    conn.commit.assert_called_once()


def test_revert_triage_article_tags_returns_false_without_history():
    existing_article = {
        "pmid": "43051034",
        "paper_types": ["methods"],
        "pgx_information_types": ["pd"],
        "drug_tags": [],
        "gene_tags": [],
        "phenotype_tags": [],
    }
    conn, cur = _make_conn(
        fetchone_return={"articles": [existing_article]},
        fetchall_return=[],
    )

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        reverted, had_history = db.revert_triage_article_tags("sess-1", "43051034")

    assert reverted == existing_article
    assert had_history is False
    conn.commit.assert_not_called()


def test_revert_triage_article_tags_rejects_in_flight_regeneration():
    import pytest

    existing_article = {
        "pmid": "43051034",
        "tags_regenerating": True,
        "tags_regenerating_started_at": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
    }
    conn, _cur = _make_conn(fetchone_return={"articles": [existing_article]})

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        with pytest.raises(db.TriageArticleBusyError):
            db.revert_triage_article_tags("sess-1", "43051034")


def test_is_tag_regeneration_in_flight_expires_stale_flags():
    fresh = {
        "tags_regenerating": True,
        "tags_regenerating_started_at": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
    }
    stale = {
        "tags_regenerating": True,
        "tags_regenerating_started_at": (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=20)
        ).isoformat(),
    }
    assert db.is_tag_regeneration_in_flight(fresh) is True
    assert db.is_tag_regeneration_in_flight(stale) is False
    assert db.is_tag_regeneration_in_flight({"tags_regenerating": False}) is False


def test_update_triage_article_fields_rejects_busy_article_when_requested():
    import pytest

    existing_article = {
        "pmid": "43051034",
        "tags_regenerating": True,
        "tags_regenerating_started_at": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
    }
    conn, _cur = _make_conn(fetchone_return={"articles": [existing_article]})

    with patch("src.database._get_conn", return_value=_mock_get_conn(conn)):
        with pytest.raises(db.TriageArticleBusyError):
            db.update_triage_article_fields(
                "sess-1",
                "43051034",
                {"paper_types": ["review"]},
                edit_action="manual_edit",
                reject_if_busy=True,
            )
    conn.commit.assert_not_called()

