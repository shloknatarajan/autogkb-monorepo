"""HTTP routes for uploading PDFs for analysis."""

import asyncio
import logging
import re

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from pubmed_markdown.pmcid_from_pmid import get_pmcid_from_pmid

from src.annotators import resolve_method
from src.database import create_job, get_job_by_pmid

from .jobs import run_pdf_upload_job

router = APIRouter(tags=["pdf-upload"])


class PdfUploadJobResponse(BaseModel):
    """Initial job state returned after accepting an upload."""

    job_id: str
    pmid: str
    pmcid: str | None = None
    source: str = "pdf_upload"
    status: str
    progress: str | None = None
    annotation_data: dict | None = None
    markdown_content: str | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    generation_metadata: dict | None = None


def _default_method() -> str:
    """Resolve the configured default before persisting the new job."""
    try:
        return resolve_method(None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/upload", response_model=PdfUploadJobResponse)
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    pmid: str = Form(...),
    force: str = Form(""),
):
    """Upload a PDF for full pipeline analysis via Datalab conversion.

    If the PMID has a corresponding PMCID on PubMed Central, the open-access
    version is used instead of the uploaded PDF for better quality markdown.
    """
    pmid = pmid.strip()
    if not re.match(r"^\d{1,10}$", pmid):
        raise HTTPException(
            status_code=422, detail="PMID must be numeric (e.g. 38234567)"
        )

    existing = get_job_by_pmid(pmid)
    if (
        existing
        and existing["status"] == "completed"
        and force.lower() not in ("true", "1")
    ):
        raise HTTPException(
            status_code=409, detail=f"An analysis already exists for PMID {pmid}"
        )

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are accepted")

    loop = asyncio.get_running_loop()
    try:
        pmcid_map = await loop.run_in_executor(None, get_pmcid_from_pmid, pmid)
        pmcid: str | None = pmcid_map.get(pmid)
        if pmcid:
            pmcid = pmcid.upper()
    except Exception:
        logging.getLogger("uvicorn").debug(
            "PMCID lookup failed for PMID %s, falling back to PDF upload", pmid
        )
        pmcid = None

    if pmcid:
        from src.jobs import run_analysis_job

        job_id = create_job(pmid, pmcid=pmcid, source="pmc", method=_default_method())
        background_tasks.add_task(run_analysis_job, job_id, pmcid, pmid=pmid)
        return PdfUploadJobResponse(
            job_id=job_id, pmid=pmid, pmcid=pmcid, source="pmc", status="pending"
        )

    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    job_id = create_job(pmid, source="pdf_upload", method=_default_method())
    background_tasks.add_task(
        run_pdf_upload_job, job_id, pmid, pdf_bytes, file.filename
    )
    return PdfUploadJobResponse(
        job_id=job_id, pmid=pmid, source="pdf_upload", status="pending"
    )
