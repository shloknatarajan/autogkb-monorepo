"""Tests for run_triage_job in src.jobs."""

import asyncio
import os
from pathlib import Path
from unittest.mock import ANY, AsyncMock, call, patch

import pytest
import requests

from annotation import AnnotationExtractor, DisplayAssociation, ExtractionOutput
from src.summary import SUMMARY_MODEL, generate_summary
from src.jobs import (
    _annotate_and_store,
    extract_drug_tags,
    extract_gene_tags,
    extract_phenotype_tags,
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

_PHENOTYPE_TAGS = [
    [
        {
            "name": "Hemorrhage",
            "id": "PA444147",
            "source": "clinpgx",
            "url": "https://www.clinpgx.org/phenotype/PA444147",
            "resolved": True,
            "mentions": ["increased bleeding risk"],
            "categories": ["toxicity"],
            "directions": ["increased"],
            "drugs": ["warfarin"],
            "genes": ["CYP2C9"],
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


@pytest.fixture(autouse=True)
def mock_phenotype_tag_extraction():
    with patch("src.jobs.extract_phenotype_tags", side_effect=_PHENOTYPE_TAGS):
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


def test_extract_phenotype_tags_collapses_resolved_and_preserves_raw_outcomes():
    frames = [
        {
            "mention": "increased risk of severe neutropenia",
            "core_phenotype": "neutropenia",
            "category": "toxicity",
            "direction": "increased",
            "drugs": [{"name": "irinotecan"}],
            "genes": [{"name": "UGT1A1"}],
            "normalized_term": "Neutropenia",
            "id": "PA445667",
            "source": "clinpgx",
            "url": "https://www.clinpgx.org/phenotype/PA445667",
            "resolved": True,
        },
        {
            "mention": "severe neutropenia",
            "core_phenotype": "neutropenia",
            "category": "toxicity",
            "direction": "not_stated",
            "drugs": [{"name": "irinotecan"}],
            "genes": [{"name": "UGT1A1"}],
            "normalized_term": "Neutropenia",
            "id": "PA445667",
            "source": "clinpgx",
            "url": "https://www.clinpgx.org/phenotype/PA445667",
            "resolved": True,
        },
        {
            "mention": "lower attained dose",
            "core_phenotype": "attained dose",
            "category": "dosage",
            "direction": "decreased",
            "drugs": [{"name": "metoprolol"}],
            "genes": [{"name": "CYP2D6"}],
            "normalized_term": None,
            "id": None,
            "source": None,
            "url": None,
            "resolved": False,
        },
    ]
    with patch("src.jobs.normalize_phenotypes_in_abstract", return_value=frames):
        result = extract_phenotype_tags(
            "UGT1A1 was associated with neutropenia and lower attained dose.",
            _DRUG_TAGS[0],
            _GENE_TAGS[0],
        )

    assert result == [
        {
            "name": "Neutropenia",
            "id": "PA445667",
            "source": "clinpgx",
            "url": "https://www.clinpgx.org/phenotype/PA445667",
            "resolved": True,
            "mentions": [
                "increased risk of severe neutropenia",
                "severe neutropenia",
            ],
            "categories": ["toxicity"],
            "directions": ["increased", "not_stated"],
            "drugs": ["irinotecan"],
            "genes": ["UGT1A1"],
        },
        {
            "name": "attained dose",
            "id": None,
            "source": None,
            "url": None,
            "resolved": False,
            "mentions": ["lower attained dose"],
            "categories": ["dosage"],
            "directions": ["decreased"],
            "drugs": ["metoprolol"],
            "genes": ["CYP2D6"],
        },
    ]


def test_tag_extractors_use_explicit_runtime_data_directory():
    with (
        patch.dict(os.environ, {"AUTOGKB_DATA_DIR": "/app/data"}),
        patch(
            "src.jobs.normalize_drugs_in_abstract",
            return_value={},
        ) as normalize_drugs,
        patch(
            "src.jobs.normalize_genes_in_abstract",
            return_value={"genes": {}, "variants": {}},
        ) as normalize_genes,
        patch(
            "src.jobs.normalize_phenotypes_in_abstract",
            return_value=[],
        ) as normalize_phenotypes,
    ):
        assert extract_drug_tags("warfarin") == []
        assert extract_gene_tags("CYP2C9") == []
        assert extract_phenotype_tags("bleeding") == []

    assert normalize_drugs.call_args.kwargs["data_dir"] == Path("/app/data")
    assert normalize_genes.call_args.kwargs["data_dir"] == Path("/app/data")
    assert normalize_phenotypes.call_args.kwargs["data_dir"] == Path("/app/data")


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
        patch(
            "src.jobs.fetch_pmcid_markdown",
            new_callable=AsyncMock,
            return_value=markdown,
        ),
        patch("src.jobs.get_extractor") as mock_extract,
        patch("src.jobs.update_job") as mock_update,
    ):
        _run(run_analysis_job("analysis-1", "PMC13729000", pmid="42974700"))

    mock_extract.assert_not_called()
    assert mock_update.call_args_list[-1].kwargs["status"] == "failed"
    assert "Invalid article content" in mock_update.call_args_list[-1].kwargs["error"]


def test_run_analysis_job_does_not_download_a_raw_pmid_as_a_pmcid():
    with (
        patch("src.jobs.fetch_pmcid_markdown", new_callable=AsyncMock) as mock_download,
        patch("src.jobs.get_extractor") as mock_extract,
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
        assert first["phenotype_tags"] == _PHENOTYPE_TAGS[0]
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


class _StubExtractor(AnnotationExtractor):
    name = "stub_method"
    default_model = "stub-model"

    def extract(self, markdown, identifier, progress):
        progress("generating_sentences", "working")
        return ExtractionOutput(
            associations=[
                DisplayAssociation(
                    variant="rs1",
                    sentence="rs1 is associated with response.",
                    explanation="p: 0.01",
                    citations=["a quote"],
                )
            ],
            variants=["rs1", "rs2"],
            structured_associations=[{"variants": [{"name": "rs1"}]}],
        )


def test_annotate_and_store_records_method_model_and_timestamps():
    with (
        patch("src.jobs.update_job") as mock_update,
        patch("src.jobs.generate_summary", return_value="A summary.") as mock_summary,
    ):
        count = _run(
            _annotate_and_store(
                "job-1",
                "PMC1",
                "# Title\n\nBody",
                config_name="api",
                method="stub_method",
                start_time=0.0,
                pmid="123",
            )
        )

    assert count == 1
    statuses = [c.kwargs["status"] for c in mock_update.call_args_list]
    assert statuses == ["generating_sentences", "generating_summary", "completed"]
    final = mock_update.call_args_list[-1].kwargs
    content = final["json_content"]
    # rs2 was examined but produced no sentence; it must still be listed.
    assert content["annotations"] == {
        "rs1": [{"sentence": "rs1 is associated with response.", "explanation": "p: 0.01"}],
        "rs2": [],
    }
    assert content["annotation_citations"][0]["citations"] == ["a quote"]
    assert content["annotation_data"]["summary"] == "A summary."
    assert content["annotation_data"]["structured_associations"] == [
        {"variants": [{"name": "rs1"}]}
    ]
    metadata = final["generation_metadata"]
    assert metadata["annotation_method"] == "stub_method"
    assert metadata["annotation_model"] == "stub-model"
    assert metadata["started_at"] <= metadata["completed_at"]
    assert metadata["summary_model"] == SUMMARY_MODEL
    assert metadata["summary_prompt_version"] == "v3"
    mock_summary.assert_called_once_with("PMC1", content["annotation_citations"])


def test_generate_summary_groups_sentences_and_uses_summary_model():
    associations = [
        {"variant": "rs1", "sentence": "First.", "explanation": "", "citations": []},
        {"variant": "rs1", "sentence": "Second.", "explanation": "", "citations": []},
        {"variant": "rs2", "sentence": "Third.", "explanation": "", "citations": []},
    ]
    with patch("src.summary.SummaryGenerator") as mock_generator:
        mock_generator.return_value.generate.return_value.model_dump.return_value = {
            "summary": "Text."
        }
        assert generate_summary("PMC1", associations) == "Text."

    mock_generator.assert_called_once_with(
        method="basic_summary", model=SUMMARY_MODEL, prompt_version="v3"
    )
    identifier, variants_data, citations_data = (
        mock_generator.return_value.generate.call_args.args
    )
    assert variants_data == [
        {"variant": "rs1", "sentences": ["First.", "Second."]},
        {"variant": "rs2", "sentences": ["Third."]},
    ]
    assert citations_data == {"PMC1": associations}


def test_annotate_and_store_rejects_unknown_method():
    with patch("src.jobs.update_job"), pytest.raises(ValueError, match="Available"):
        _run(
            _annotate_and_store(
                "job-1", "PMC1", "text", config_name="api", method="nope", start_time=0.0
            )
        )


class _MultiVariantExtractor(AnnotationExtractor):
    name = "stub_multi_variant"
    default_model = "stub-model"

    def extract(self, markdown, identifier, progress):
        return ExtractionOutput(
            associations=[
                DisplayAssociation(
                    variant="CYP2C9*2, rs1799853",
                    variants=["CYP2C9*2", "rs1799853"],
                    sentence="Both are associated with dose.",
                    explanation="",
                    citations=[],
                )
            ]
        )


def test_annotate_and_store_keys_by_single_variant_and_keeps_prior_stages():
    with (
        patch("src.jobs.update_job") as mock_update,
        patch("src.jobs.generate_summary", return_value=""),
    ):
        _run(
            _annotate_and_store(
                "job-1",
                "123",
                "# Title\n\nBody",
                config_name="api_pdf_upload",
                method="stub_multi_variant",
                start_time=0.0,
                prior_stages=("datalab_convert",),
            )
        )

    final = mock_update.call_args_list[-1].kwargs
    entry = [{"sentence": "Both are associated with dose.", "explanation": ""}]
    assert final["json_content"]["annotations"] == {
        "CYP2C9*2": entry,
        "rs1799853": entry,
    }
    assert final["generation_metadata"]["stages_run"] == [
        "datalab_convert",
        "annotation",
        "summary",
    ]


def test_run_uploaded_pdf_triage_scoring_upserts_scored_article():
    from src.jobs import run_uploaded_pdf_triage_scoring

    with (
        patch(
            "src.jobs.fetch_pubmed_abstract",
            return_value={
                "pmid": "43051034",
                "pmcid": "PMC1111111",
                "title": "CYP2D6 Study",
                "abstract": "CYP2D6 variants affect warfarin response.",
                "publication_date": "2026-11-10",
            },
        ),
        patch(
            "src.jobs.score_for_va",
            return_value={
                "score": 88,
                "label": "relevant",
                "tags": ["variant_annotations"],
                "reasoning": "Strong PGx association.",
            },
        ),
        patch(
            "src.jobs.classify_additional_labels",
            return_value={
                "paper_types": ["review"],
                "paper_type_reasoning": "Systematic review.",
                "pgx_information_types": ["metabolism_pk"],
                "pgx_information_reasoning": "Metabolism study.",
            },
        ),
        patch("src.jobs.extract_drug_tags", return_value=[]),
        patch("src.jobs.extract_gene_tags", return_value=[]),
        patch("src.jobs.extract_phenotype_tags", return_value=[]),
        patch("src.jobs.upsert_uploaded_pdf_triage_article") as mock_upsert,
    ):
        _run(
            run_uploaded_pdf_triage_scoring(
                "2026-11-01",
                "43051034",
                "job-abc",
                pmcid=None,
                title="Fallback Title",
            )
        )

    mock_upsert.assert_called_once()
    month_arg, article_arg = mock_upsert.call_args.args
    assert month_arg == "2026-11-01"
    assert article_arg["pmid"] == "43051034"
    assert article_arg["pmcid"] == "PMC1111111"
    assert article_arg["title"] == "CYP2D6 Study"
    assert article_arg["triage_label"] == "relevant"
    assert article_arg["triage_score"] == 88
    assert article_arg["decision"] == "submitted"
    assert article_arg["job_id"] == "job-abc"


def test_resolve_manual_tags_and_regenerate_single_article():
    from src.jobs import (
        regenerate_triage_article_tags,
        resolve_manual_drug_tags,
        resolve_manual_gene_tags,
        resolve_manual_phenotype_tags,
    )

    phenotypes = resolve_manual_phenotype_tags(
        [{"name": "poor metabolizer"}, {"name": "Custom Unmapped Outcome"}]
    )
    assert phenotypes[0]["name"] == "Poor Metabolizer"
    assert phenotypes[0]["source"] == "cpic"
    assert phenotypes[0]["resolved"] is True
    assert phenotypes[1]["name"] == "Custom Unmapped Outcome"
    assert phenotypes[1]["resolved"] is False

    # Preserves already-resolved tags without dropping metadata and deduplicates
    drugs = resolve_manual_drug_tags(
        [
            {
                "name": "warfarin",
                "id": "PA451906",
                "source": "pharmgkb",
                "url": "https://www.clinpgx.org/chemical/PA451906",
                "resolved": True,
                "mentions": ["warfarin"],
            },
            {
                "name": "warfarin",
                "id": "PA451906",
                "source": "pharmgkb",
                "resolved": True,
            },
        ]
    )
    assert len(drugs) == 1
    assert drugs[0]["id"] == "PA451906"

    genes = resolve_manual_gene_tags(
        [{"name": "UNKNOWN_FAKE_GENE_XYZ"}]
    )
    assert genes == [
        {
            "name": "UNKNOWN_FAKE_GENE_XYZ",
            "id": None,
            "source": None,
            "url": None,
            "resolved": False,
            "mentions": ["UNKNOWN_FAKE_GENE_XYZ"],
            "ncbi_gene_id": None,
            "hgnc_id": None,
        }
    ]

    with (
        patch("src.jobs.score_for_va") as mock_score,
        patch(
            "src.jobs.classify_additional_labels",
            return_value={
                "paper_types": ["methods"],
                "paper_type_reasoning": "Methods paper",
                "pgx_information_types": ["toxicity"],
                "pgx_information_reasoning": "Toxicity",
            },
        ),
        patch("src.jobs.extract_drug_tags", return_value=[]),
        patch("src.jobs.extract_gene_tags", return_value=[]),
        patch("src.jobs.extract_phenotype_tags", return_value=[]),
    ):
        patch_data = _run(
            regenerate_triage_article_tags(
                {
                    "pmid": "43051034",
                    "pmcid": "PMC1234567",
                    "title": "Existing Title",
                    "abstract": "Existing abstract text",
                    "publication_date": "2026-11-10",
                }
            )
        )

    mock_score.assert_not_called()
    assert "triage_label" not in patch_data
    assert "triage_tags" not in patch_data
    assert patch_data["paper_types"] == ["methods"]
    assert patch_data["pgx_information_types"] == ["toxicity"]


def test_run_triage_article_tag_regeneration_updates_article_and_clears_flag():
    from src.jobs import run_triage_article_tag_regeneration

    article = {
        "pmid": "43051034",
        "title": "Existing Title",
        "abstract": "Existing abstract text",
    }
    regenerated_patch = {
        "pmcid": None,
        "title": "Existing Title",
        "abstract": "Existing abstract text",
        "publication_date": None,
        "paper_types": ["review"],
        "paper_type_reasoning": "Review",
        "pgx_information_types": ["dosage"],
        "pgx_information_reasoning": "Dosage",
        "drug_tags": [],
        "gene_tags": [],
        "phenotype_tags": [],
    }
    with (
        patch(
            "src.jobs.get_triage_session",
            return_value={"id": "sess-1", "articles": [article]},
        ),
        patch(
            "src.jobs.regenerate_triage_article_tags",
            new_callable=AsyncMock,
            return_value=regenerated_patch,
        ) as mock_regen,
        patch("src.jobs.update_triage_article_fields") as mock_update,
    ):
        _run(run_triage_article_tag_regeneration("sess-1", "43051034"))

    mock_regen.assert_awaited_once_with(article)
    mock_update.assert_called_once_with(
        "sess-1",
        "43051034",
        {
            **regenerated_patch,
            "tags_regenerating": False,
            "tags_regenerating_started_at": None,
            "tags_regeneration_error": None,
        },
        edit_action="regenerate",
    )


def test_run_triage_article_tag_regeneration_records_error_on_failure():
    from src.jobs import run_triage_article_tag_regeneration

    article = {"pmid": "43051034", "title": "Existing Title", "abstract": "Text"}
    with (
        patch(
            "src.jobs.get_triage_session",
            return_value={"id": "sess-1", "articles": [article]},
        ),
        patch(
            "src.jobs.regenerate_triage_article_tags",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM timeout"),
        ),
        patch("src.jobs.update_triage_article_fields") as mock_update,
    ):
        _run(run_triage_article_tag_regeneration("sess-1", "43051034"))

    mock_update.assert_called_once_with(
        "sess-1",
        "43051034",
        {
            "tags_regenerating": False,
            "tags_regenerating_started_at": None,
            "tags_regeneration_error": "LLM timeout",
        },
    )

