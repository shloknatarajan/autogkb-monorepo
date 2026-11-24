"""HTTP routes for uploading PDFs for analysis."""

import asyncio
import logging
from pathlib import Path
import re
import tempfile

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from pubmed_markdown.pmcid_from_pmid import get_pmcid_from_pmid

from src.annotators import resolve_method
from src.database import (
    create_job,
    get_job_by_pmcid,
    get_job_by_pmid,
    get_latest_active_or_completed_job_for_pmcid,
    get_latest_active_or_completed_jobs_for_pmids,
    is_job_in_flight,
    sync_triage_sessions_for_pmid,
)

from .identifier import (
    PdfIdentifierResult,
    PmidVerificationResult,
    resolve_pdf_identifiers,
    verify_pmid_in_pubmed,
)
from .jobs import run_pdf_upload_job

router = APIRouter(tags=["pdf-upload"])

_TEMP_UPLOAD_DIR = Path(tempfile.gettempdir()) / "autogkb_pdf_uploads"


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


def _stage_pdf_temp_file(job_id: str, pdf_bytes: bytes) -> Path:
    """Write uploaded PDF bytes to temp disk so queued jobs don't hold RAM."""
    _TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    staged_path = _TEMP_UPLOAD_DIR / f"{job_id}.pdf"
    staged_path.write_bytes(pdf_bytes)
    return staged_path


def _lookup_existing_job_for_pmid(
    pmid: str, pmcid: str | None = None
) -> dict | None:
    """Return an active in-flight (preferred) or completed job for pmid/pmcid."""
    pmid_job: dict | None = None
    try:
        active_map = get_latest_active_or_completed_jobs_for_pmids([pmid])
        pmid_job = active_map.get(pmid)
    except RuntimeError:
        pass
    if pmid_job is None:
        try:
            pmid_job = get_job_by_pmid(pmid)
        except RuntimeError:
            pmid_job = None

    if pmid_job and is_job_in_flight(pmid_job):
        return pmid_job

    pmcid_job: dict | None = None
    if pmcid:
        clean_pmcid = pmcid.strip().upper()
        if clean_pmcid:
            try:
                pmcid_job = get_latest_active_or_completed_job_for_pmcid(
                    clean_pmcid
                )
            except RuntimeError:
                pmcid_job = None
            if pmcid_job is None:
                try:
                    pmcid_job = get_job_by_pmcid(clean_pmcid)
                except RuntimeError:
                    pmcid_job = None

    if pmcid_job and is_job_in_flight(pmcid_job):
        return pmcid_job
    if pmid_job and pmid_job.get("status") == "completed":
        return pmid_job
    if pmcid_job and pmcid_job.get("status") == "completed":
        return pmcid_job
    return pmid_job or pmcid_job


def _raise_if_existing_job(
    existing: dict | None, pmid: str, force: str
) -> None:
    """Reject in-flight jobs unconditionally and completed jobs unless forced."""
    if not existing:
        return
    if is_job_in_flight(existing):
        raise HTTPException(
            status_code=409,
            detail=f"PMID {pmid} is already being processed in the pipeline",
        )
    if existing.get("status") == "completed" and force.lower() not in (
        "true",
        "1",
    ):
        raise HTTPException(
            status_code=409,
            detail=f"An analysis already exists for PMID {pmid}",
        )


def _sync_triage_safe(pmid: str, job_id: str) -> None:
    """Best-effort sync so triage sessions reflect newly uploaded articles."""
    try:
        sync_triage_sessions_for_pmid(pmid, job_id)
    except Exception as exc:
        logging.getLogger("uvicorn").debug(
            "Triage sync skipped for PMID %s: %s", pmid, exc
        )


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

        existing = _lookup_existing_job_for_pmid(result.pmid, pmcid=result.pmcid)
        if existing and (
            existing.get("status") == "completed" or is_job_in_flight(existing)
        ):
            result.existing_job_id = str(existing["id"])
            result.existing_job_status = str(existing["status"])

    return result


@router.get("/upload/verify-pmid/{pmid}", response_model=PmidVerificationResult)
async def verify_uploaded_pmid(pmid: str):
    """Check whether a manually entered PMID exists in PubMed and/or the DB."""
    clean_pmid = pmid.strip()
    if not re.match(r"^\d{1,10}$", clean_pmid):
        raise HTTPException(
            status_code=422, detail="PMID must be numeric (e.g. 38234567)"
        )

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, verify_pmid_in_pubmed, clean_pmid)

    existing = _lookup_existing_job_for_pmid(clean_pmid, pmcid=result.pmcid)
    if existing and (
        existing.get("status") == "completed" or is_job_in_flight(existing)
    ):
        result.existing_job_id = str(existing["id"])
        result.existing_job_status = str(existing["status"])

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

    existing = _lookup_existing_job_for_pmid(pmid, pmcid=resolved_pmcid)
    _raise_if_existing_job(existing, pmid, force)

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

        if resolved_pmcid:
            pmcid_existing = _lookup_existing_job_for_pmid(
                pmid, pmcid=resolved_pmcid
            )
            _raise_if_existing_job(pmcid_existing, pmid, force)

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
        _sync_triage_safe(pmid, job_id)
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
    _sync_triage_safe(pmid, job_id)
    staged_path = _stage_pdf_temp_file(job_id, pdf_bytes)
    background_tasks.add_task(
        run_pdf_upload_job, job_id, pmid, staged_path, file.filename
    )
    return PdfUploadJobResponse(
        job_id=job_id, pmid=pmid, source="pdf_upload", status="pending"
    )
