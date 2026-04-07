"""
FastAPI server for variant extraction.

Main entry point for the Variant Extractor API.
"""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, HTTPException, BackgroundTasks
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
from .database import init_db, create_job, get_job, get_job_by_pmcid, list_pmcids
from .jobs import run_analysis_job

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
    pmcid: str
    status: str
    progress: Optional[str] = None
    annotation_data: Optional[dict] = None
    markdown_content: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None


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
    # 1. Normalize PMCID: if it doesn't start with "PMC", prepend "PMC"
    pmcid = input.pmcid.strip()
    if not pmcid.upper().startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    pmcid = pmcid.upper()  # normalize to uppercase

    # 2. Check if a completed job already exists for this PMCID (return cached result)
    #    Skip the cache when force=True so the pipeline re-runs from scratch.
    if not input.force:
        existing = get_job_by_pmcid(pmcid)
        if existing:
            return JobResponse(
                job_id=str(existing["id"]),
                pmcid=pmcid,
                status=existing["status"],
                progress=existing.get("progress"),
                annotation_data=existing.get("annotation_data"),
                markdown_content=existing.get("markdown_content"),
                error=existing.get("error"),
                created_at=str(existing.get("created_at", "")),
            )

    # 3. Create a new job
    job_id = create_job(pmcid)

    # 4. Kick off background task
    background_tasks.add_task(run_analysis_job, job_id, pmcid)

    return JobResponse(job_id=job_id, pmcid=pmcid, status="pending")


@app.post("/analyze/pmid", response_model=JobResponse)
async def analyze_pmid_endpoint(
    input: AnalyzePmidRequest, background_tasks: BackgroundTasks
):
    """Submit a PubMed PMID for full pipeline analysis.

    Tries to resolve the PMID to a PMCID via NCBI's ID Converter API.
    - If a PMCID is found, the article is downloaded from PubMed Central and
      the job is stored under that PMCID (same path as POST /analyze).
    - If no PMCID exists (non-PMC article), the article text scraped by the
      browser extension is used and the job is stored under the raw PMID.
    """
    loop = asyncio.get_running_loop()

    # 1. Try PMID → PMCID resolution (result is cached by pubmed_markdown)
    pmcid_map = await loop.run_in_executor(None, get_pmcid_from_pmid, input.pmid)
    pmcid: Optional[str] = pmcid_map.get(input.pmid)

    # identifier used in DB and viewer URL: resolved PMCID or raw PMID
    identifier = pmcid.upper() if pmcid else input.pmid

    # 2. Cache check — skip if force=True or no real associations stored
    if not input.force:
        existing = get_job_by_pmcid(identifier)
        if existing:
            associations = (
                (existing.get("annotation_data") or {})
                .get("result", {})
                .get("associations")
            )
            if isinstance(associations, list) and associations:
                return JobResponse(
                    job_id=str(existing["id"]),
                    pmcid=identifier,
                    status=existing["status"],
                    progress=existing.get("progress"),
                    annotation_data=existing.get("annotation_data"),
                    markdown_content=existing.get("markdown_content"),
                    error=existing.get("error"),
                    created_at=str(existing.get("created_at", "")),
                )

    # 3. Create job and kick off background task
    job_id = create_job(identifier)

    # If PMCID resolved: let the job download from PMC (article_text=None).
    # If not: pass the browser-scraped article text so the job skips download.
    article_text = None if pmcid else input.article_text
    background_tasks.add_task(
        run_analysis_job, job_id, identifier, article_text=article_text
    )

    return JobResponse(job_id=job_id, pmcid=identifier, status="pending")


@app.get("/jobs/pmid/{pmid}", response_model=JobResponse)
async def get_job_by_pmid_endpoint(pmid: str):
    """Check whether a PMID has an existing completed analysis.

    Tries PMID → PMCID resolution first so that articles analyzed via their
    PMCID are found even when queried by PMID.
    """
    pmid = pmid.strip()
    if not re.match(r"^\d{1,10}$", pmid):
        raise HTTPException(status_code=422, detail="PMID must be numeric")

    loop = asyncio.get_running_loop()
    pmcid_map = await loop.run_in_executor(None, get_pmcid_from_pmid, pmid)
    pmcid: Optional[str] = pmcid_map.get(pmid)

    # Check by resolved PMCID first, then by raw PMID
    identifier = pmcid.upper() if pmcid else pmid
    job = get_job_by_pmcid(identifier)
    if not job:
        raise HTTPException(
            status_code=404, detail=f"No completed analysis found for PMID {pmid}"
        )
    return JobResponse(
        job_id=str(job["id"]),
        pmcid=job["pmcid"],
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
        pmcid=job["pmcid"],
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
    ``job_id``, ``pmcid``, ``status``, ``progress``, ``error``.
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
                    "pmcid": job["pmcid"],
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


@app.get("/pmcids")
async def get_pmcids():
    """List pmcid and title for all completed analyses."""
    try:
        return list_pmcids()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(job_id: str):
    """Get the status and result of an analysis job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobResponse(
        job_id=str(job["id"]),
        pmcid=job["pmcid"],
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
