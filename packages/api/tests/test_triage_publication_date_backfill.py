"""Tests for the publication-date-only legacy triage backfill."""

from unittest.mock import call, patch

from src.backfill.triage_publication_dates import (
    backfill_triage_publication_dates,
    needs_publication_date_backfill,
)


def test_needs_publication_date_backfill_distinguishes_valid_dates():
    assert needs_publication_date_backfill({"pmid": "1"}) is True
    assert needs_publication_date_backfill({"publication_date": None}) is True
    assert needs_publication_date_backfill({"publication_date": ""}) is True
    assert needs_publication_date_backfill({"publication_date": "2026-11-07"}) is False


def test_dry_run_reports_work_without_fetching_or_writing():
    sessions = {
        "session-1": {
            "articles": [
                {"pmid": "111", "publication_date": None},
                {"pmid": "111"},
                {"pmid": "222", "publication_date": "2025-01-02"},
                {"pmid": None},
            ]
        }
    }

    with (
        patch(
            "src.backfill.triage_publication_dates.list_triage_sessions",
            return_value=[{"id": "session-1"}],
        ),
        patch(
            "src.backfill.triage_publication_dates.get_triage_session",
            side_effect=lambda session_id: sessions[session_id],
        ),
        patch(
            "src.backfill.triage_publication_dates.update_triage_session_articles"
        ) as update,
    ):
        stats = backfill_triage_publication_dates()

    assert stats == {
        "dry_run": True,
        "sessions_scanned": 1,
        "sessions_needing_updates": 1,
        "sessions_updated": 0,
        "article_rows_needing_updates": 3,
        "article_rows_updated": 0,
        "unique_pmids_to_fetch": 1,
        "unique_dates_found": 0,
        "unique_dates_not_found": 0,
        "article_rows_without_pmids": 1,
    }
    update.assert_not_called()


def test_apply_deduplicates_pmids_and_preserves_existing_fields():
    sessions = {
        "session-1": {
            "articles": [
                {
                    "pmid": "111",
                    "publication_date": None,
                    "decision": "dismissed",
                    "triage_score": 82,
                },
                {"pmid": "222", "publication_date": "2024-03-04"},
            ]
        },
        "session-2": {
            "articles": [
                {"pmid": "111"},
                {"pmid": "333", "publication_date": None},
                {"pmid": None, "publication_date": None},
            ]
        },
    }
    fetch_calls = []

    def fetch_dates(pmids, email, batch_size):
        fetch_calls.append((pmids, email, batch_size))
        return {"111": "2025-11-07", "333": None}

    with (
        patch(
            "src.backfill.triage_publication_dates.list_triage_sessions",
            return_value=[{"id": "session-1"}, {"id": "session-2"}],
        ),
        patch(
            "src.backfill.triage_publication_dates.get_triage_session",
            side_effect=lambda session_id: sessions[session_id],
        ),
        patch(
            "src.backfill.triage_publication_dates.update_triage_session_articles"
        ) as update,
    ):
        stats = backfill_triage_publication_dates(
            ncbi_email="curator@example.org",
            apply=True,
            batch_size=100,
            fetch_dates=fetch_dates,
        )

    assert fetch_calls == [(["111", "333"], "curator@example.org", 100)]
    assert stats["sessions_updated"] == 2
    assert stats["article_rows_updated"] == 2
    assert stats["unique_pmids_to_fetch"] == 2
    assert stats["unique_dates_found"] == 1
    assert stats["unique_dates_not_found"] == 1
    assert update.call_args_list == [
        call("session-1", sessions["session-1"]["articles"]),
        call("session-2", sessions["session-2"]["articles"]),
    ]
    first = sessions["session-1"]["articles"][0]
    assert first["publication_date"] == "2025-11-07"
    assert first["decision"] == "dismissed"
    assert first["triage_score"] == 82
    assert sessions["session-1"]["articles"][1]["publication_date"] == "2024-03-04"
    assert sessions["session-2"]["articles"][1]["publication_date"] is None


def test_backfill_validates_arguments():
    for kwargs in (
        {"batch_size": 0},
        {"apply": True, "ncbi_email": None},
    ):
        try:
            backfill_triage_publication_dates(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected ValueError")
