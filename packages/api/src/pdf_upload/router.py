"""HTTP routes for uploading PDFs for analysis."""

import asyncio
import logging
import re

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from pubmed_markdown.pmcid_from_pmid import get_pmcid_from_pmid

from src.annotators import resolve_method
from src.database import create_job, get_job_by_pmid

from .identifier import PdfIdentifierResult, resolve_pdf_identifiers
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


@router.post("/upload/resolve", response_model=PdfIdentifierResult)
async def resolve_uploaded_pdf(file: UploadFile = File(...)):
    """Extract and resolve a PDF's PMID/PMCID before running Datalab."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are accepted")

    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, resolve_pdf_identifiers, pdf_bytes, file.filename
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result.pmid:
        if not result.pmcid:
            try:
                pmcid_map = await loop.run_in_executor(
                    None, get_pmcid_from_pmid, result.pmid
                )
                resolved_pmcid = str(pmcid_map.get(result.pmid) or "").strip().upper()
                if re.fullmatch(r"PMC\d+", resolved_pmcid):
                    result.pmcid = resolved_pmcid
            except Exception:
                logging.getLogger("uvicorn").debug(
                    "PMCID lookup failed for resolved PMID %s", result.pmid
                )

        try:
            existing = get_job_by_pmid(result.pmid)
        except RuntimeError:
            existing = None
        if existing and existing.get("status") == "completed":
            result.existing_job_id = str(existing["id"])

    return result


@router.post("/upload", response_model=PdfUploadJobResponse)
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    pmid: str = Form(...),
    pmcid: str = Form(""),
    title: str = Form(""),
    force: str = Form(""),
):
    """Upload a PDF for full pipeline analysis via Datalab conversion.

    If a PMCID is provided (or found on PubMed Central for the PMID), the
    open-access version is used instead of the uploaded PDF for better quality
    markdown.
    """
    pmid = pmid.strip()
    if not re.match(r"^\d{1,10}$", pmid):
        raise HTTPException(
            status_code=422, detail="PMID must be numeric (e.g. 38234567)"
        )

    provided_pmcid = pmcid.strip()
    resolved_pmcid: str | None = None
    if provided_pmcid:
        if not re.fullmatch(r"(?:PMC)?\d{1,10}", provided_pmcid, re.IGNORECASE):
            raise HTTPException(
                status_code=422,
                detail="Invalid PMCID format (expected e.g. PMC10987654)",
            )
        normalized = provided_pmcid.upper()
        resolved_pmcid = (
            normalized if normalized.startswith("PMC") else f"PMC{normalized}"
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

    if resolved_pmcid is None:
        loop = asyncio.get_running_loop()
        try:
            pmcid_map = await loop.run_in_executor(None, get_pmcid_from_pmid, pmid)
            found = pmcid_map.get(pmid)
            if found:
                resolved_pmcid = found.upper()
        except Exception:
            logging.getLogger("uvicorn").debug(
                "PMCID lookup failed for PMID %s, falling back to PDF upload", pmid
            )
            resolved_pmcid = None

    clean_title = title.strip() or None
    title_kwarg = {"title": clean_title} if clean_title else {}

    if resolved_pmcid:
        from src.jobs import run_analysis_job

        job_id = create_job(
            pmid,
            pmcid=resolved_pmcid,
            source="pmc",
            method=_default_method(),
            **title_kwarg,
        )
        background_tasks.add_task(run_analysis_job, job_id, resolved_pmcid, pmid=pmid)
        return PdfUploadJobResponse(
            job_id=job_id,
            pmid=pmid,
            pmcid=resolved_pmcid,
            source="pmc",
            status="pending",
        )

    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    job_id = create_job(
        pmid, source="pdf_upload", method=_default_method(), **title_kwarg
    )
    background_tasks.add_task(
        run_pdf_upload_job, job_id, pmid, pdf_bytes, file.filename
    )
    return PdfUploadJobResponse(
        job_id=job_id, pmid=pmid, source="pdf_upload", status="pending"
    )
