"""Tests for PDF upload HTTP routing."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.main import app
from src.pdf_upload.identifier import PdfIdentifierResult, PmidVerificationResult

client = TestClient(app)


def test_upload_pdf_starts_pdf_job_when_no_pmcid_exists(tmp_path):
    staged_file = tmp_path / "job-123.pdf"

    def fake_stage(job_id: str, pdf_bytes: bytes) -> Path:
        assert job_id == "job-123"
        staged_file.write_bytes(pdf_bytes)
        return staged_file

    with (
        patch("src.pdf_upload.router.get_job_by_pmid", return_value=None),
        patch("src.pdf_upload.router.get_pmcid_from_pmid", return_value={}),
        patch("src.pdf_upload.router.resolve_method", return_value="test-method"),
        patch("src.pdf_upload.router.create_job", return_value="job-123") as create,
        patch("src.pdf_upload.router._sync_triage_safe") as sync_triage,
        patch(
            "src.pdf_upload.router._stage_pdf_temp_file", side_effect=fake_stage
        ),
        patch(
            "src.pdf_upload.router.run_pdf_upload_job", new_callable=AsyncMock
        ) as run_job,
    ):
        response = client.post(
            "/upload",
            data={"pmid": "38234567"},
            files={"file": ("article.pdf", b"%PDF-1.7 content", "application/pdf")},
        )

    assert response.status_code == 200
    assert response.json()["source"] == "pdf_upload"
    create.assert_called_once_with(
        "38234567", source="pdf_upload", method="test-method"
    )
    sync_triage.assert_called_once_with("38234567", "job-123")
    run_job.assert_awaited_once_with(
        "job-123", "38234567", staged_file, "article.pdf"
    )
    assert staged_file.read_bytes() == b"%PDF-1.7 content"


def test_upload_pdf_rejects_non_pdf_file():
    with patch("src.pdf_upload.router.get_job_by_pmid", return_value=None):
        response = client.post(
            "/upload",
            data={"pmid": "38234567"},
            files={"file": ("article.txt", b"not a pdf", "text/plain")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "Only PDF files are accepted"


def test_upload_pdf_rejects_in_flight_job_for_same_pmid():
    with patch(
        "src.pdf_upload.router._lookup_existing_job_for_pmid",
        return_value={"id": "job-active", "status": "fetching_article"},
    ):
        response = client.post(
            "/upload",
            data={"pmid": "38234567", "force": "true"},
            files={"file": ("article.pdf", b"%PDF-1.7 content", "application/pdf")},
        )

    assert response.status_code == 409
    assert "already being processed" in response.json()["detail"]


def test_upload_pdf_uses_explicit_pmcid_and_skips_lookup():
    with (
        patch("src.pdf_upload.router.get_job_by_pmid", return_value=None),
        patch("src.pdf_upload.router.get_pmcid_from_pmid") as lookup_pmcid,
        patch("src.pdf_upload.router.resolve_method", return_value="test-method"),
        patch("src.pdf_upload.router.create_job", return_value="job-456") as create,
        patch("src.pdf_upload.router._sync_triage_safe") as sync_triage,
        patch("src.jobs.run_analysis_job", new_callable=AsyncMock) as run_pmc,
    ):
        response = client.post(
            "/upload",
            data={"pmid": "38234567", "pmcid": "pmc10987654"},
            files={"file": ("article.pdf", b"%PDF-1.7 content", "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "pmc"
    assert body["pmcid"] == "PMC10987654"
    lookup_pmcid.assert_not_called()
    create.assert_called_once_with(
        "38234567", pmcid="PMC10987654", source="pmc", method="test-method"
    )
    sync_triage.assert_called_once_with("38234567", "job-456")
    run_pmc.assert_awaited_once_with("job-456", "PMC10987654", pmid="38234567")


def test_upload_pdf_rejects_invalid_pmcid():
    response = client.post(
        "/upload",
        data={"pmid": "38234567", "pmcid": "NOT_A_PMCID"},
        files={"file": ("article.pdf", b"%PDF-1.7 content", "application/pdf")},
    )
    assert response.status_code == 422
    assert "PMCID" in response.json()["detail"]


def test_upload_pdf_forwards_optional_title_to_create_job(tmp_path):
    staged_file = tmp_path / "job-789.pdf"
    with (
        patch("src.pdf_upload.router.get_job_by_pmid", return_value=None),
        patch("src.pdf_upload.router.get_pmcid_from_pmid", return_value={}),
        patch("src.pdf_upload.router.resolve_method", return_value="test-method"),
        patch("src.pdf_upload.router.create_job", return_value="job-789") as create,
        patch("src.pdf_upload.router._sync_triage_safe"),
        patch(
            "src.pdf_upload.router._stage_pdf_temp_file", return_value=staged_file
        ),
        patch("src.pdf_upload.router.run_pdf_upload_job", new_callable=AsyncMock),
    ):
        response = client.post(
            "/upload",
            data={"pmid": "38234567", "title": "CYP2D6 Genotype Study"},
            files={"file": ("article.pdf", b"%PDF-1.7 content", "application/pdf")},
        )

    assert response.status_code == 200
    create.assert_called_once_with(
        "38234567",
        source="pdf_upload",
        method="test-method",
        title="CYP2D6 Genotype Study",
    )


def test_resolve_uploaded_pdf_returns_identifiers_and_existing_job():
    resolved = PdfIdentifierResult(
        pmid="38234567",
        pmcid=None,
        doi="10.1002/cpt.3123",
        title="Sample PGx Article",
        match_method="doi_metadata",
        confidence="high",
    )
    with (
        patch(
            "src.pdf_upload.router.resolve_pdf_identifiers", return_value=resolved
        ) as resolve_fn,
        patch(
            "src.pdf_upload.router.get_pmcid_from_pmid",
            return_value={"38234567": "pmc10987654"},
        ),
        patch(
            "src.pdf_upload.router.get_job_by_pmid",
            return_value={"id": "existing-job-99", "status": "completed"},
        ),
    ):
        response = client.post(
            "/upload/resolve",
            files={"file": ("article.pdf", b"%PDF-1.7 content", "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["pmid"] == "38234567"
    assert body["pmcid"] == "PMC10987654"
    assert body["match_method"] == "doi_metadata"
    assert body["confidence"] == "high"
    assert body["existing_job_id"] == "existing-job-99"
    assert body["existing_job_status"] == "completed"
    resolve_fn.assert_called_once_with(b"%PDF-1.7 content", "article.pdf")


def test_resolve_uploaded_pdf_reports_in_flight_job_status():
    resolved = PdfIdentifierResult(
        pmid="38234567",
        pmcid="PMC10987654",
        doi="10.1002/cpt.3123",
        title="Sample PGx Article",
        match_method="doi_metadata",
        confidence="high",
    )
    with (
        patch("src.pdf_upload.router.resolve_pdf_identifiers", return_value=resolved),
        patch(
            "src.pdf_upload.router._lookup_existing_job_for_pmid",
            return_value={"id": "inflight-job-1", "status": "fetching_article"},
        ),
    ):
        response = client.post(
            "/upload/resolve",
            files={"file": ("article.pdf", b"%PDF-1.7 content", "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["existing_job_id"] == "inflight-job-1"
    assert body["existing_job_status"] == "fetching_article"


def test_resolve_uploaded_pdf_rejects_non_pdf_and_empty_and_corrupt():
    non_pdf = client.post(
        "/upload/resolve",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert non_pdf.status_code == 422
    assert non_pdf.json()["detail"] == "Only PDF files are accepted"

    empty_pdf = client.post(
        "/upload/resolve",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert empty_pdf.status_code == 422
    assert empty_pdf.json()["detail"] == "Uploaded file is empty"

    corrupt_pdf = client.post(
        "/upload/resolve",
        files={"file": ("corrupt.pdf", b"not real pdf bytes", "application/pdf")},
    )
    assert corrupt_pdf.status_code == 422
    assert "Could not read PDF file" in corrupt_pdf.json()["detail"]


def test_verify_uploaded_pmid_returns_pubmed_and_existing_job_status():
    with (
        patch(
            "src.pdf_upload.router.verify_pmid_in_pubmed",
            return_value=PmidVerificationResult(
                pmid="43051034",
                exists=True,
                title="Verified Study",
                pmcid="PMC9876543",
            ),
        ),
        patch(
            "src.pdf_upload.router._lookup_existing_job_for_pmid",
            return_value={"id": "job-running", "status": "annotating"},
        ),
    ):
        resp = client.get("/upload/verify-pmid/43051034")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["title"] == "Verified Study"
    assert body["pmcid"] == "PMC9876543"
    assert body["existing_job_id"] == "job-running"
    assert body["existing_job_status"] == "annotating"


def test_verify_uploaded_pmid_not_found_and_invalid_format():
    with (
        patch(
            "src.pdf_upload.router.verify_pmid_in_pubmed",
            return_value=PmidVerificationResult(pmid="999999999", exists=False),
        ),
        patch(
            "src.pdf_upload.router._lookup_existing_job_for_pmid",
            return_value=None,
        ),
    ):
        missing = client.get("/upload/verify-pmid/999999999")

    assert missing.status_code == 200
    assert missing.json()["exists"] is False

    invalid = client.get("/upload/verify-pmid/not-a-pmid")
    assert invalid.status_code == 422


def test_resolve_and_upload_detect_pmcid_only_existing_job():
    resolved = PdfIdentifierResult(
        pmid="38234567",
        pmcid="PMC10987654",
        doi="10.1002/cpt.3123",
        title="Sample PGx Article",
        match_method="doi_metadata",
        confidence="high",
    )
    pmcid_only_job = {"id": "pmcid-only-job-1", "status": "completed"}

    with (
        patch("src.pdf_upload.router.resolve_pdf_identifiers", return_value=resolved),
        patch(
            "src.pdf_upload.router.get_latest_active_or_completed_jobs_for_pmids",
            return_value={},
        ),
        patch("src.pdf_upload.router.get_job_by_pmid", return_value=None),
        patch(
            "src.pdf_upload.router.get_latest_active_or_completed_job_for_pmcid",
            return_value=pmcid_only_job,
        ),
    ):
        resolve_resp = client.post(
            "/upload/resolve",
            files={"file": ("article.pdf", b"%PDF-1.7 content", "application/pdf")},
        )

    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["existing_job_id"] == "pmcid-only-job-1"
    assert resolve_resp.json()["existing_job_status"] == "completed"

    # Uploading with only pmid (no pmcid form field) still catches PMCID-only job
    # once get_pmcid_from_pmid resolves the PMCID.
    with (
        patch(
            "src.pdf_upload.router.get_latest_active_or_completed_jobs_for_pmids",
            return_value={},
        ),
        patch("src.pdf_upload.router.get_job_by_pmid", return_value=None),
        patch(
            "src.pdf_upload.router.get_pmcid_from_pmid",
            return_value={"38234567": "PMC10987654"},
        ),
        patch(
            "src.pdf_upload.router.get_latest_active_or_completed_job_for_pmcid",
            return_value={"id": "pmcid-only-active", "status": "annotating"},
        ),
    ):
        upload_resp = client.post(
            "/upload",
            data={"pmid": "38234567"},
            files={"file": ("article.pdf", b"%PDF-1.7 content", "application/pdf")},
        )

    assert upload_resp.status_code == 409
    assert "already being processed" in upload_resp.json()["detail"]
