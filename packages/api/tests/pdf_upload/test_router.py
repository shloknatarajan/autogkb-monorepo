"""Tests for PDF upload HTTP routing."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_upload_pdf_starts_pdf_job_when_no_pmcid_exists():
    with (
        patch("src.pdf_upload.router.get_job_by_pmid", return_value=None),
        patch("src.pdf_upload.router.get_pmcid_from_pmid", return_value={}),
        patch("src.pdf_upload.router.resolve_method", return_value="test-method"),
        patch("src.pdf_upload.router.create_job", return_value="job-123") as create,
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
    run_job.assert_awaited_once_with(
        "job-123", "38234567", b"%PDF-1.7 content", "article.pdf"
    )


def test_upload_pdf_rejects_non_pdf_file():
    with patch("src.pdf_upload.router.get_job_by_pmid", return_value=None):
        response = client.post(
            "/upload",
            data={"pmid": "38234567"},
            files={"file": ("article.txt", b"not a pdf", "text/plain")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "Only PDF files are accepted"
