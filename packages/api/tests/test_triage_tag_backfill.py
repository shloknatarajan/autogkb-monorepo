"""Tests for the tags-only legacy triage backfill."""

import asyncio
from unittest.mock import call, patch

from src.backfill.triage_tags import backfill_triage_tags, needs_tag_backfill


def test_needs_tag_backfill_distinguishes_valid_empty_tags():
    assert needs_tag_backfill({"pmid": "1"}) is True
    assert (
        needs_tag_backfill({"drug_tags": [], "gene_tags": [], "phenotype_tags": []})
        is False
    )


def test_dry_run_reports_work_without_extracting_or_writing():
    sessions = {
        "session-1": {
            "articles": [
                {"pmid": "111", "abstract": "CYP2C9 and warfarin."},
                {"pmid": "222", "abstract": None, "drug_tags": []},
                {
                    "pmid": "333",
                    "drug_tags": [],
                    "gene_tags": [],
                    "phenotype_tags": [],
                },
            ]
        }
    }

    def drug_extractor(_text, _model):
        return [{"name": "warfarin"}]

    def gene_extractor(_text, _model):
        return [{"name": "CYP2C9"}]

    def phenotype_extractor(_text, _drugs, _genes, _model):
        return [{"name": "Hemorrhage"}]

    with (
        patch(
            "src.backfill.triage_tags.list_triage_sessions",
            return_value=[{"id": "session-1"}],
        ),
        patch(
            "src.backfill.triage_tags.get_triage_session",
            side_effect=lambda session_id: sessions[session_id],
        ),
        patch("src.backfill.triage_tags.update_triage_session_articles") as update,
    ):
        stats = asyncio.run(
            backfill_triage_tags(
                model="test-model",
                drug_extractor=drug_extractor,
                gene_extractor=gene_extractor,
                phenotype_extractor=phenotype_extractor,
            )
        )

    assert stats == {
        "dry_run": True,
        "sessions_scanned": 1,
        "sessions_needing_updates": 1,
        "sessions_updated": 0,
        "article_rows_needing_updates": 2,
        "article_rows_updated": 0,
        "unique_papers_needing_tags": 1,
        "drug_extractions_needed": 1,
        "gene_extractions_needed": 1,
        "phenotype_extractions_needed": 1,
        "articles_without_abstracts": 1,
    }
    update.assert_not_called()


def test_apply_deduplicates_papers_and_preserves_existing_fields():
    sessions = {
        "session-1": {
            "articles": [
                {
                    "pmid": "111",
                    "abstract": "CYP2C9 and warfarin.",
                    "decision": "dismissed",
                    "triage_score": 82,
                },
                {
                    "pmid": "222",
                    "abstract": "No abstract tags.",
                    "drug_tags": [{"name": "existing"}],
                },
            ]
        },
        "session-2": {
            "articles": [
                {"pmid": "111", "abstract": "CYP2C9 and warfarin."},
                {"pmid": "333", "abstract": None},
            ]
        },
    }
    calls = {"drugs": 0, "genes": 0, "phenotypes": 0}

    def drugs(_text, _model):
        calls["drugs"] += 1
        return [{"name": "warfarin"}]

    def genes(_text, _model):
        calls["genes"] += 1
        return [{"name": "CYP2C9"}]

    def phenotypes(_text, drug_tags, gene_tags, _model):
        calls["phenotypes"] += 1
        assert drug_tags is not None
        assert gene_tags is not None
        return [{"name": "Hemorrhage"}]

    with (
        patch(
            "src.backfill.triage_tags.list_triage_sessions",
            return_value=[{"id": "session-1"}, {"id": "session-2"}],
        ),
        patch(
            "src.backfill.triage_tags.get_triage_session",
            side_effect=lambda session_id: sessions[session_id],
        ),
        patch("src.backfill.triage_tags.update_triage_session_articles") as update,
    ):
        stats = asyncio.run(
            backfill_triage_tags(
                model="test-model",
                apply=True,
                concurrency=2,
                drug_extractor=drugs,
                gene_extractor=genes,
                phenotype_extractor=phenotypes,
            )
        )

    assert stats["sessions_updated"] == 2
    assert stats["article_rows_updated"] == 4
    assert stats["unique_papers_needing_tags"] == 2
    assert stats["drug_extractions_needed"] == 1
    assert stats["gene_extractions_needed"] == 2
    assert stats["phenotype_extractions_needed"] == 2
    assert calls == {"drugs": 1, "genes": 2, "phenotypes": 2}
    assert update.call_args_list == [
        call("session-1", sessions["session-1"]["articles"]),
        call("session-2", sessions["session-2"]["articles"]),
    ]
    first = sessions["session-1"]["articles"][0]
    assert first["decision"] == "dismissed"
    assert first["triage_score"] == 82
    assert first["drug_tags"] == [{"name": "warfarin"}]
    assert first["gene_tags"] == [{"name": "CYP2C9"}]
    assert first["phenotype_tags"] == [{"name": "Hemorrhage"}]
    partial = sessions["session-1"]["articles"][1]
    assert partial["drug_tags"] == [{"name": "existing"}]
    assert partial["gene_tags"] == [{"name": "CYP2C9"}]
    assert partial["phenotype_tags"] == [{"name": "Hemorrhage"}]
    no_abstract = sessions["session-2"]["articles"][1]
    assert no_abstract["drug_tags"] == []
    assert no_abstract["gene_tags"] == []
    assert no_abstract["phenotype_tags"] == []


def test_backfill_validates_limits():
    for kwargs in ({"concurrency": 0}, {"session_batch_size": 0}):
        try:
            asyncio.run(backfill_triage_tags(model="test", **kwargs))
        except ValueError:
            pass
        else:
            raise AssertionError("Expected ValueError")
