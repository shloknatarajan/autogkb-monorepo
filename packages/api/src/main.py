"""
FastAPI server for variant extraction.

Main entry point for the Variant Extractor API.
"""

import asyncio
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
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
from shared.utils import call_llm
from .database import (
    init_db,
    create_job,
    get_job,
    get_job_by_pmcid,
    get_job_by_pmid,
    list_articles,
)
from .jobs import run_analysis_job, run_pdf_upload_job, run_reanalysis_job

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


class RegenerateRequest(BaseModel):
    pmid: str
    force: bool = True

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


class ScoreRequest(BaseModel):
    pmids: List[str]

    @field_validator("pmids")
    @classmethod
    def validate_pmids(cls, v: List[str]) -> List[str]:
        cleaned = [p.strip() for p in v if p.strip()]
        if len(cleaned) > 100:
            raise ValueError("Maximum 100 PMIDs per request")
        return cleaned


class ScoredPaper(BaseModel):
    pmid: str
    title: str | None = None
    abstract: str | None = None
    score: int
    reasoning: str
    error: str | None = None


_SCORING_SYSTEM = (
    "You are a pharmacogenomics expert. Given a paper's title and abstract, "
    "score its relevance to pharmacogenomics (PGx) research on a scale of 0-100, where:\n"
    "- 0-30: Not PGx (general genomics, unrelated disease area, basic biology with no drug relevance)\n"
    "- 31-60: Tangentially related (mentions genetics or drugs but not drug-gene interactions)\n"
    "- 61-100: Directly PGx (drug metabolism, genetic variants affecting drug response/efficacy/toxicity, "
    "pharmacokinetics, pharmacodynamics, or PGx guidelines)\n\n"
    'Respond with ONLY valid JSON, no markdown: {"score": <integer 0-100>, "reasoning": "<1-2 sentences>"}'
)


def _fetch_pubmed_abstract(pmid: str, ncbi_email: str) -> dict:
    """Fetch title and abstract from NCBI E-utilities efetch (XML)."""
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "xml", "email": ncbi_email},
            timeout=15,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        title_el = root.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else None
        abstract_parts = root.findall(".//AbstractText")
        abstract = (
            " ".join(
                "".join(el.itertext()).strip()
                for el in abstract_parts
                if "".join(el.itertext()).strip()
            )
            or None
        )
        return {"title": title, "abstract": abstract, "error": None}
    except Exception as e:
        return {"title": None, "abstract": None, "error": str(e)}


def _score_paper_sync(
    pmid: str, title: str | None, abstract: str | None, model: str
) -> dict:
    """Score a paper for PGx relevance using LLM. Returns score + reasoning."""
    content = f"Title: {title or 'N/A'}\n\nAbstract: {abstract or 'N/A'}"
    try:
        response = call_llm(model, _SCORING_SYSTEM, content)
        cleaned = (
            response.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        parsed = json.loads(cleaned)
        return {
            "score": max(0, min(100, int(parsed["score"]))),
            "reasoning": str(parsed["reasoning"]),
        }
    except Exception as e:
        logging.getLogger("uvicorn").warning(f"LLM scoring failed for PMID {pmid}: {e}")
        return {"score": 0, "reasoning": f"Scoring failed: {e}"}


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
            pmid = records[0].get("pmid")
            return str(pmid) if pmid is not None else None
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


@app.post("/papers/score", response_model=List[ScoredPaper])
async def score_papers(req: ScoreRequest):
    """Score a list of PMIDs for pharmacogenomics (PGx) relevance using an LLM.

    Fetches title + abstract from NCBI for each PMID, then scores each paper
    0-100 for PGx relevance. Results are returned sorted by score descending.
    """
    model = os.environ.get("PIPELINE_MODEL", "gpt-4o")
    ncbi_email = os.environ.get("NCBI_EMAIL", "")
    loop = asyncio.get_event_loop()

    async def score_one(pmid: str) -> ScoredPaper:
        meta = await loop.run_in_executor(
            None, _fetch_pubmed_abstract, pmid, ncbi_email
        )
        if meta["error"] and not meta["title"] and not meta["abstract"]:
            return ScoredPaper(
                pmid=pmid,
                score=0,
                reasoning="Failed to fetch paper metadata.",
                error=meta["error"],
            )
        scored = await loop.run_in_executor(
            None, _score_paper_sync, pmid, meta["title"], meta["abstract"], model
        )
        return ScoredPaper(
            pmid=pmid,
            title=meta["title"],
            abstract=meta["abstract"],
            score=scored["score"],
            reasoning=scored["reasoning"],
        )

    results = await asyncio.gather(*[score_one(pmid) for pmid in req.pmids])
    return sorted(results, key=lambda p: p.score, reverse=True)


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

    return JobResponse(
        job_id=job_id, pmid=identifier, pmcid=pmcid, source="pmc", status="pending"
    )


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
    job_id = create_job(
        input.pmid, pmcid=pmcid.upper() if pmcid else None, source="pmc"
    )

    article_text = None if pmcid else input.article_text
    background_tasks.add_task(
        run_analysis_job,
        job_id,
        download_id,
        article_text=article_text,
        pmid=input.pmid,
    )

    return JobResponse(
        job_id=job_id,
        pmid=input.pmid,
        pmcid=pmcid.upper() if pmcid else None,
        source="pmc",
        status="pending",
    )


@app.post("/upload", response_model=JobResponse)
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

    # Attempt PMID -> PMCID conversion to use open access version if available
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
        # Open access version available — use PMC download pipeline instead
        job_id = create_job(pmid, pmcid=pmcid, source="pmc")
        background_tasks.add_task(run_analysis_job, job_id, pmcid, pmid=pmid)
        return JobResponse(
            job_id=job_id, pmid=pmid, pmcid=pmcid, source="pmc", status="pending"
        )

    # No PMCID found — proceed with PDF upload pipeline
    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    job_id = create_job(pmid, source="pdf_upload")
    background_tasks.add_task(
        run_pdf_upload_job, job_id, pmid, pdf_bytes, file.filename
    )
    return JobResponse(job_id=job_id, pmid=pmid, source="pdf_upload", status="pending")


@app.post("/regenerate", response_model=JobResponse)
async def regenerate_analysis(
    input: RegenerateRequest, background_tasks: BackgroundTasks
):
    """Re-run analysis using existing markdown for a PMID.

    Fetches the most recent completed job's markdown_content from the DB
    and runs only the analysis pipeline (variants, sentences, citations,
    summary). Works for both PMC and PDF-uploaded articles.
    """
    existing = get_job_by_pmid(input.pmid)
    if not existing or not existing.get("markdown_content"):
        raise HTTPException(
            status_code=404,
            detail=f"No existing markdown found for PMID {input.pmid}. "
            "Cannot regenerate without a prior analysis.",
        )

    markdown = existing["markdown_content"]
    pmcid = existing.get("pmcid")
    source = existing.get("source", "pmc")

    job_id = create_job(input.pmid, pmcid=pmcid, source=source)

    background_tasks.add_task(
        run_reanalysis_job, job_id, input.pmid, markdown, pmcid=pmcid
    )

    return JobResponse(
        job_id=job_id,
        pmid=input.pmid,
        pmcid=pmcid,
        source=source,
        status="pending",
    )


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
