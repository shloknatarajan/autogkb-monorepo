"""Tests for the labels-only legacy triage backfill."""

import asyncio
from unittest.mock import call, patch

from src.triage_label_backfill import backfill_triage_labels, needs_label_backfill


def test_needs_label_backfill_distinguishes_valid_empty_labels():
    assert needs_label_backfill({"pmid": "1"}) is True
    assert (
        needs_label_backfill(
            {
                "pmid": "1",
                "paper_types": [],
                "paper_type_reasoning": "No matching paper type.",
                "pgx_information_types": [],
                "pgx_information_reasoning": "No PGx information.",
            }
        )
        is False
    )


def test_backfill_deduplicates_pmids_and_preserves_existing_fields():
    sessions = {
        "session-1": {
            "articles": [
                {
                    "pmid": "111",
                    "title": "Paper A",
                    "abstract": "Abstract A",
                    "decision": "dismissed",
                    "triage_score": 82,
                },
                {
                    "pmid": "222",
                    "title": "Paper B",
                    "abstract": "Abstract B",
                    "paper_types": ["methods"],
                },
            ]
        },
        "session-2": {
            "articles": [
                {
                    "pmid": "111",
                    "title": "Paper A",
                    "abstract": "Abstract A",
                },
                {
                    "pmid": "333",
                    "paper_types": [],
                    "paper_type_reasoning": "None.",
                    "pgx_information_types": [],
                    "pgx_information_reasoning": "None.",
                },
            ]
        },
    }

    def classify(pmid, _title, _abstract, _model):
        return {
            "paper_types": ["clinical"],
            "paper_type_reasoning": f"paper {pmid}",
            "pgx_information_types": ["efficacy"],
            "pgx_information_reasoning": f"pgx {pmid}",
        }

    with (
        patch(
            "src.triage_label_backfill.list_triage_sessions",
            return_value=[{"id": "session-1"}, {"id": "session-2"}],
        ),
        patch(
            "src.triage_label_backfill.get_triage_session",
            side_effect=lambda session_id: sessions[session_id],
        ),
        patch("src.triage_label_backfill.update_triage_session_articles") as update,
    ):
        stats = asyncio.run(
            backfill_triage_labels(model="test-model", concurrency=2, classify=classify)
        )

    assert stats == {
        "sessions_scanned": 2,
        "sessions_updated": 2,
        "article_rows_updated": 3,
        "unique_papers_classified": 2,
    }
    assert update.call_args_list == [
        call("session-1", sessions["session-1"]["articles"]),
        call("session-2", sessions["session-2"]["articles"]),
    ]
    first = sessions["session-1"]["articles"][0]
    assert first["decision"] == "dismissed"
    assert first["triage_score"] == 82
    assert first["paper_types"] == ["clinical"]
    partial = sessions["session-1"]["articles"][1]
    assert partial["paper_types"] == ["methods"]
    assert partial["pgx_information_types"] == ["efficacy"]
