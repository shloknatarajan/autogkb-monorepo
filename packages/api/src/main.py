"""
FastAPI server for variant extraction.

Main entry point for the Variant Extractor API.
"""

import asyncio
import json
import re
import requests
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, HTTPException, BackgroundTasks, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

import functools

from pubmed_markdown import PubMedMarkdown as _PubMedMarkdownClass
from pubmed_markdown.pmcid_from_pmid import get_pmcid_from_pmid

from pipeline.modules.variant_finding.utils import (
    extract_all_variants,
    get_variant_types,
)
from .database import init_db, create_job, get_job, get_job_by_pmcid, get_job_by_pmid, list_articles
from .jobs import run_analysis_job, run_pdf_upload_job

_converter = _PubMedMarkdownClass()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception as exc:
        import logging

        logging.getLogger("uvicorn").warning(
            f"Database unavailable at startup (analysis endpoints will fail): {exc}"
        )
    yield


app = FastAPI(
    title="AutoGKB API",
    description="Extract genetic variants from PubMed articles or text. Part of the AutoGKB project.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TextInput(BaseModel):
    text: str
    include_metadata: bool = True


class PmidInput(BaseModel):
    pmid: str
    include_supplements: bool = False


class PmcidInput(BaseModel):
    pmcid: str
    include_supplements: bool = False


class ExtractionResult(BaseModel):
    success: bool
    variants: List[str]
    metadata: Optional[dict] = None


class AnalyzeRequest(BaseModel):
    pmcid: str
    force: bool = False

    @field_validator("pmcid")
    @classmethod
    def validate_pmcid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("PMCID cannot be empty")
        if not re.match(r"^(?:PMC)?\d{4,10}$", v, re.IGNORECASE):
            raise ValueError(
                "Invalid PMCID format. Expected PMC followed by digits (e.g., PMC5508045)"
            )
        return v


class AnalyzePmidRequest(BaseModel):
    pmid: str
    article_text: Optional[str] = None  # article text scraped by the extension
    force: bool = False

    @field_validator("pmid")
    @classmethod
    def validate_pmid(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^\d{1,10}$", v):
            raise ValueError("PMID must be numeric (e.g. 38234567)")
        return v


class JobResponse(BaseModel):
    job_id: str
    pmid: str
    pmcid: str | None = None
    source: str = "pmc"
    status: str
    progress: str | None = None
    annotation_data: dict | None = None
    markdown_content: str | None = None
    error: str | None = None
    created_at: str | None = None


async def fetch_pubmed_text(pmid: str, include_supplements: bool = False) -> str:
    """Fetch article text for a PMID using the pubmed_markdown library."""
    loop = asyncio.get_event_loop()
    markdown = await loop.run_in_executor(
        None, functools.partial(_converter.pmid_to_markdown, pmid, include_supplements)
    )
    if markdown is None:
        raise HTTPException(
            status_code=404,
            detail=f"No PMCID found or failed to fetch article for PMID {pmid}",
        )
    return markdown


async def fetch_pmcid_text(pmcid: str, include_supplements: bool = False) -> str:
    """Fetch article text for a PMCID using the pubmed_markdown library."""
    if not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    loop = asyncio.get_event_loop()
    markdown = await loop.run_in_executor(
        None,
        functools.partial(_converter.pmcid_to_markdown, pmcid, include_supplements),
    )
    if markdown is None:
        raise HTTPException(
            status_code=404,
            detail=f"Failed to fetch article {pmcid} from PubMed Central",
        )
    return markdown


def _get_pmid_from_pmcid_safe(pmcid: str) -> str | None:
    """Look up PMID from PMCID using NCBI's idconv API. Returns None on failure."""
    try:
        resp = requests.get(
            "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
            params={"ids": pmcid, "format": "json"},
            timeout=10,
        )
        records = resp.json().get("records", [])
        if records:
            return records[0].get("pmid")
    except Exception:
        pass
    return None


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "AutoGKB API",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/variant-extract/text", response_model=ExtractionResult)
async def extract_from_text(input: TextInput):
    """Extract variants from raw text."""
    try:
        variants = extract_all_variants(input.text)
        variant_types = get_variant_types(variants)

        result = {
            "success": True,
            "variants": sorted(variants),
        }

        if input.include_metadata:
            result["metadata"] = {
                "source": "text",
                "variant_count": len(variants),
                "variant_types": variant_types,
            }

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/variant-extract/pmid", response_model=ExtractionResult)
async def extract_from_pmid(input: PmidInput):
    """Extract variants from PubMed PMID."""
    try:
        text = await fetch_pubmed_text(input.pmid, input.include_supplements)
        variants = extract_all_variants(text)
        variant_types = get_variant_types(variants)

        result = {
            "success": True,
            "variants": sorted(variants),
            "metadata": {
                "source": "pmid",
                "pmid": input.pmid,
                "variant_count": len(variants),
                "variant_types": variant_types,
            },
        }

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/variant-extract/pmcid", response_model=ExtractionResult)
async def extract_from_pmcid(input: PmcidInput):
    """Extract variants from PubMed Central PMCID."""
    try:
        text = await fetch_pmcid_text(input.pmcid, input.include_supplements)
        variants = extract_all_variants(text)
        variant_types = get_variant_types(variants)

        result = {
            "success": True,
            "variants": sorted(variants),
            "metadata": {
                "source": "pmcid",
                "pmcid": input.pmcid,
                "variant_count": len(variants),
                "variant_types": variant_types,
            },
        }

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze")
async def analyze_pmcid(input: AnalyzeRequest, background_tasks: BackgroundTasks):
    """Submit a PMCID for full pipeline analysis (background job)."""
    pmcid = input.pmcid.strip()
    if not pmcid.upper().startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    pmcid = pmcid.upper()

    loop = asyncio.get_running_loop()
    pmid_val = await loop.run_in_executor(None, _get_pmid_from_pmcid_safe, pmcid)

    if not input.force:
        existing = None
        if pmid_val:
            existing = get_job_by_pmid(pmid_val)
        if not existing:
            existing = get_job_by_pmcid(pmcid)
        if existing:
            return JobResponse(
                job_id=str(existing["id"]),
                pmid=existing.get("pmid") or pmid_val or "",
                pmcid=existing.get("pmcid") or pmcid,
                source=existing.get("source", "pmc"),
                status=existing["status"],
                progress=existing.get("progress"),
                annotation_data=existing.get("annotation_data"),
                markdown_content=existing.get("markdown_content"),
                error=existing.get("error"),
                created_at=str(existing.get("created_at", "")),
            )

    identifier = pmid_val or pmcid
    job_id = create_job(identifier, pmcid=pmcid, source="pmc")

    background_tasks.add_task(run_analysis_job, job_id, pmcid, pmid=pmid_val)

    return JobResponse(job_id=job_id, pmid=identifier, pmcid=pmcid, source="pmc", status="pending")


@app.post("/analyze/pmid", response_model=JobResponse)
async def analyze_pmid_endpoint(
    input: AnalyzePmidRequest, background_tasks: BackgroundTasks
):
    """Submit a PubMed PMID for full pipeline analysis."""
    loop = asyncio.get_running_loop()

    pmcid_map = await loop.run_in_executor(None, get_pmcid_from_pmid, input.pmid)
    pmcid: str | None = pmcid_map.get(input.pmid)

    if not input.force:
        existing = get_job_by_pmid(input.pmid)
        if existing:
            associations = (
                (existing.get("annotation_data") or {})
                .get("result", {})
                .get("associations")
            )
            if isinstance(associations, list) and associations:
                return JobResponse(
                    job_id=str(existing["id"]),
                    pmid=input.pmid,
                    pmcid=existing.get("pmcid") or (pmcid.upper() if pmcid else None),
                    source=existing.get("source", "pmc"),
                    status=existing["status"],
                    progress=existing.get("progress"),
                    annotation_data=existing.get("annotation_data"),
                    markdown_content=existing.get("markdown_content"),
                    error=existing.get("error"),
                    created_at=str(existing.get("created_at", "")),
                )

    download_id = pmcid.upper() if pmcid else input.pmid
    job_id = create_job(input.pmid, pmcid=pmcid.upper() if pmcid else None, source="pmc")

    article_text = None if pmcid else input.article_text
    background_tasks.add_task(
        run_analysis_job, job_id, download_id, article_text=article_text, pmid=input.pmid
    )

    return JobResponse(job_id=job_id, pmid=input.pmid, pmcid=pmcid.upper() if pmcid else None, source="pmc", status="pending")


@app.post("/upload", response_model=JobResponse)
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    pmid: str = Form(...),
):
    """Upload a PDF for full pipeline analysis via Datalab conversion."""
    pmid = pmid.strip()
    if not re.match(r"^\d{1,10}$", pmid):
        raise HTTPException(status_code=422, detail="PMID must be numeric (e.g. 38234567)")

    existing = get_job_by_pmid(pmid)
    if existing and existing["status"] == "completed":
        raise HTTPException(status_code=409, detail=f"An analysis already exists for PMID {pmid}")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are accepted")

    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    job_id = create_job(pmid, source="pdf_upload")

    background_tasks.add_task(
        run_pdf_upload_job, job_id, pmid, pdf_bytes, file.filename
    )

    return JobResponse(job_id=job_id, pmid=pmid, source="pdf_upload", status="pending")


@app.get("/jobs/pmid/{pmid}", response_model=JobResponse)
async def get_job_by_pmid_endpoint(pmid: str):
    """Get the most recent analysis job for a PMID."""
    pmid = pmid.strip()
    if not re.match(r"^\d{1,10}$", pmid):
        raise HTTPException(status_code=422, detail="PMID must be numeric")

    job = get_job_by_pmid(pmid)
    if not job:
        raise HTTPException(
            status_code=404, detail=f"No analysis found for PMID {pmid}"
        )
    return JobResponse(
        job_id=str(job["id"]),
        pmid=job.get("pmid") or pmid,
        pmcid=job.get("pmcid"),
        source=job.get("source", "pmc"),
        status=job["status"],
        progress=job.get("progress"),
        annotation_data=job.get("annotation_data"),
        markdown_content=job.get("markdown_content"),
        error=job.get("error"),
        created_at=str(job.get("created_at", "")),
    )


@app.get("/jobs/pmcid/{pmcid}", response_model=JobResponse)
async def get_job_by_pmcid_endpoint(pmcid: str):
    """Get the most recent completed analysis job for a PMCID."""
    # Normalize PMCID
    pmcid = pmcid.strip().upper()
    if not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"

    job = get_job_by_pmcid(pmcid)
    if not job:
        raise HTTPException(status_code=404, detail=f"No analysis found for {pmcid}")
    return JobResponse(
        job_id=str(job["id"]),
        pmid=job.get("pmid") or job.get("pmcid", ""),
        pmcid=job.get("pmcid"),
        source=job.get("source", "pmc"),
        status=job["status"],
        progress=job.get("progress"),
        annotation_data=job.get("annotation_data"),
        markdown_content=job.get("markdown_content"),
        error=job.get("error"),
        created_at=str(job.get("created_at", "")),
    )


@app.get("/jobs/{job_id}/stream")
async def stream_job_status(job_id: str):
    """Stream analysis job status updates via Server-Sent Events.

    Yields one SSE ``data:`` event every 2 seconds until the job reaches a
    terminal state (``completed`` or ``failed``).  Clients should connect with::

        fetch(`/jobs/{job_id}/stream`, { headers: { Accept: 'text/event-stream' } })

    Each event payload is a JSON object with keys:
    ``job_id``, ``pmid``, ``pmcid``, ``status``, ``progress``, ``error``.
    """

    async def event_generator():
        loop = asyncio.get_running_loop()
        heartbeat_counter = 0
        while True:
            job = await loop.run_in_executor(None, get_job, job_id)

            if job is None:
                yield f"event: error\ndata: {json.dumps({'error': 'Job not found'})}\n\n"
                break

            payload = json.dumps(
                {
                    "job_id": str(job["id"]),
                    "pmid": job.get("pmid") or job.get("pmcid", ""),
                    "pmcid": job.get("pmcid"),
                    "status": job["status"],
                    "progress": job.get("progress"),
                    "error": job.get("error"),
                }
            )
            yield f"data: {payload}\n\n"

            if job["status"] in ("completed", "failed"):
                break

            await asyncio.sleep(2)

            # Send a keep-alive comment every ~10 s to prevent proxy timeouts
            heartbeat_counter += 1
            if heartbeat_counter % 5 == 0:
                yield ": heartbeat\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/articles")
async def get_articles():
    """List all completed analyses with pmid, title, and summary."""
    try:
        return list_articles()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/pmcids")
async def get_pmcids():
    """Legacy alias for /articles."""
    return await get_articles()


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(job_id: str):
    """Get the status and result of an analysis job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobResponse(
        job_id=str(job["id"]),
        pmid=job.get("pmid") or job.get("pmcid", ""),
        pmcid=job.get("pmcid"),
        source=job.get("source", "pmc"),
        status=job["status"],
        progress=job.get("progress"),
        annotation_data=job.get("annotation_data"),
        markdown_content=job.get("markdown_content"),
        error=job.get("error"),
        created_at=str(job.get("created_at", "")),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
