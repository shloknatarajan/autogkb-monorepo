"""
FastAPI server for variant extraction.

Main entry point for the Variant Extractor API.
"""

import asyncio
import datetime
import json
import logging
import os
import re
import requests
import defusedxml.ElementTree as ET
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

import functools

from pubmed_markdown import PubMedMarkdown as _PubMedMarkdownClass
from pubmed_markdown.pmcid_from_pmid import get_pmcid_from_pmid

from pipeline.modules.variant_finding.utils import (
    extract_all_variants,
    get_variant_types,
)
from shared.utils import call_llm
from .annotators import describe_methods, resolve_method
from .database import (
    init_db,
    create_job,
    get_job,
    get_job_by_pmcid,
    get_job_by_pmid,
    get_latest_active_or_completed_jobs_for_pmids,
    is_job_in_flight,
    find_run_by_method,
    list_articles,
    list_runs_by_pmid,
    create_triage_session,
    find_triage_session_by_week,
    get_triage_session,
    list_triage_sessions,
    update_triage_article_decision,
    update_triage_session_status,
)
from .jobs import (
    has_valid_article_markdown,
    run_analysis_job,
    run_reanalysis_job,
    run_triage_backfill,
    run_triage_job,
    validate_article_markdown,
)
from .litsuggest_client import list_weekly_jobs
from .pdf_upload.router import router as pdf_upload_router
from .scoring import fetch_pubmed_abstract

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
app.include_router(pdf_upload_router)


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
    # Annotation method to run; the server default applies when omitted.
    method: Optional[str] = None

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
    # Annotation method to run; the server default applies when omitted.
    method: Optional[str] = None

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
    # Annotation method to run; the server default applies when omitted.
    method: Optional[str] = None

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
    updated_at: str | None = None
    # Which annotation method and model produced this run, and when.
    generation_metadata: dict | None = None


class AnnotationMethodResponse(BaseModel):
    name: str
    description: str
    model: str
    default: bool


class AnnotationRunResponse(BaseModel):
    job_id: str
    status: str
    created_at: str | None = None
    updated_at: str | None = None
    generation_metadata: dict | None = None


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


class CreateTriageSessionRequest(BaseModel):
    project_id: str
    project_name: str
    weeks: int = Field(default=4, ge=1, le=52)
    regenerate_existing: bool = False


class UpdateArticleDecisionRequest(BaseModel):
    decision: str


class DrugTagResponse(BaseModel):
    name: str
    id: str | None = None
    source: str | None = None
    url: str | None = None
    resolved: bool = False
    mentions: list[str] = Field(default_factory=list)


class GeneTagResponse(BaseModel):
    name: str
    id: str | None = None
    source: str | None = None
    url: str | None = None
    resolved: bool = False
    mentions: list[str] = Field(default_factory=list)
    ncbi_gene_id: str | None = None
    hgnc_id: str | None = None


class PhenotypeTagResponse(BaseModel):
    name: str
    id: str | None = None
    source: str | None = None
    url: str | None = None
    resolved: bool = False
    mentions: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    directions: list[str] = Field(default_factory=list)
    drugs: list[str] = Field(default_factory=list)
    genes: list[str] = Field(default_factory=list)


class TriageArticleResponse(BaseModel):
    pmid: str
    pmcid: str | None = None
    title: str | None = None
    abstract: str | None = None
    publication_date: str | None = None
    litsuggest_score: float
    triage_score: int
    triage_label: str
    triage_tags: list[str] = Field(default_factory=list)
    paper_types: list[str] = Field(default_factory=list)
    paper_type_reasoning: str = ""
    pgx_information_types: list[str] = Field(default_factory=list)
    pgx_information_reasoning: str = ""
    drug_tags: list[DrugTagResponse] = Field(default_factory=list)
    gene_tags: list[GeneTagResponse] = Field(default_factory=list)
    phenotype_tags: list[PhenotypeTagResponse] = Field(default_factory=list)
    reasoning: str
    decision: str
    job_id: str | None = None


class TriageSessionListItem(BaseModel):
    id: str
    project_id: str
    project_name: str
    week_date: str
    status: str
    article_count: int
    created_at: str


class TriageSessionResponse(BaseModel):
    id: str
    project_id: str
    project_name: str
    week_date: str
    status: str
    articles: list[TriageArticleResponse]
    error: str | None = None
    created_at: str


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
    try:
        return validate_article_markdown(markdown, pmid)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
    try:
        return validate_article_markdown(markdown, pmcid)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
        meta = await loop.run_in_executor(None, fetch_pubmed_abstract, pmid, ncbi_email)
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


def _existing_run(
    method: str | None, *, pmid: str | None, pmcid: str | None = None
) -> dict | None:
    """Find a stored run that may be returned instead of starting a new one.

    A caller that names a method gets the newest completed or in-flight run of
    that method, so a retry does not start a second run and a completed run of
    another method is never returned in its place. Without a named method the
    newest run of any method is used.
    """
    if method:
        return find_run_by_method(method, pmid=pmid, pmcid=pmcid)
    existing = get_job_by_pmid(pmid) if pmid else None
    if not existing and pmcid:
        existing = get_job_by_pmcid(pmcid)
    return existing


def _requested_method(name: str | None) -> str:
    """Validate a requested annotation method before a job is created."""
    try:
        return resolve_method(name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/annotation-methods", response_model=List[AnnotationMethodResponse])
async def list_annotation_methods():
    """List the annotation methods the server can run and which is the default."""
    return describe_methods()


@app.post("/analyze")
async def analyze_pmcid(input: AnalyzeRequest, background_tasks: BackgroundTasks):
    """Submit a PMCID for full pipeline analysis (background job)."""
    method = _requested_method(input.method)
    pmcid = input.pmcid.strip()
    if not pmcid.upper().startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    pmcid = pmcid.upper()

    loop = asyncio.get_running_loop()
    pmid_val = await loop.run_in_executor(None, _get_pmid_from_pmcid_safe, pmcid)

    if not input.force:
        existing = _existing_run(input.method, pmid=pmid_val, pmcid=pmcid)
        reusable = (
            existing
            and (
                existing.get("status") != "completed"
                or has_valid_article_markdown(
                    existing.get("markdown_content"),
                    existing.get("pmcid") or pmcid,
                )
            )
        )
        if reusable:
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
                updated_at=str(existing.get("updated_at", "")),
                generation_metadata=existing.get("generation_metadata"),
            )

    identifier = pmid_val or pmcid
    job_id = create_job(identifier, pmcid=pmcid, source="pmc", method=method)

    background_tasks.add_task(
        run_analysis_job, job_id, pmcid, pmid=pmid_val, method=method
    )

    return JobResponse(
        job_id=job_id, pmid=identifier, pmcid=pmcid, source="pmc", status="pending"
    )


@app.post("/analyze/pmid", response_model=JobResponse)
async def analyze_pmid_endpoint(
    input: AnalyzePmidRequest, background_tasks: BackgroundTasks
):
    """Submit a PubMed PMID for full pipeline analysis."""
    method = _requested_method(input.method)
    loop = asyncio.get_running_loop()

    try:
        pmcid_map = await loop.run_in_executor(None, get_pmcid_from_pmid, input.pmid)
        resolved_pmcid = str(pmcid_map.get(input.pmid) or "").strip().upper()
        pmcid = resolved_pmcid if re.fullmatch(r"PMC\d+", resolved_pmcid) else None
    except Exception as exc:
        logging.getLogger("uvicorn").warning(
            "PMCID lookup failed for PMID %s: %s", input.pmid, exc
        )
        pmcid = None

    if not input.force:
        existing = _existing_run(input.method, pmid=input.pmid)
        if existing:
            associations = (
                (existing.get("annotation_data") or {})
                .get("result", {})
                .get("associations")
            )
            # Only a named method can return a run that is still in flight.
            in_flight = bool(input.method) and existing.get("status") != "completed"
            if in_flight or (
                isinstance(associations, list)
                and associations
                and has_valid_article_markdown(
                    existing.get("markdown_content"),
                    existing.get("pmcid") or pmcid or input.pmid,
                )
            ):
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
                    updated_at=str(existing.get("updated_at", "")),
                    generation_metadata=existing.get("generation_metadata"),
                )

    if pmcid is None and not input.article_text:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No valid PubMed Central full text is available for PMID {input.pmid}, "
                "and no article text was supplied. The pipeline was not started."
            ),
        )

    download_id = pmcid or input.pmid
    job_id = create_job(
        input.pmid,
        pmcid=pmcid.upper() if pmcid else None,
        source="pmc",
        method=method,
    )

    article_text = None if pmcid else input.article_text
    background_tasks.add_task(
        run_analysis_job,
        job_id,
        download_id,
        article_text=article_text,
        pmid=input.pmid,
        method=method,
    )

    return JobResponse(
        job_id=job_id,
        pmid=input.pmid,
        pmcid=pmcid.upper() if pmcid else None,
        source="pmc",
        status="pending",
    )


@app.post("/regenerate", response_model=JobResponse)
async def regenerate_analysis(
    input: RegenerateRequest, background_tasks: BackgroundTasks
):
    """Re-run analysis using existing markdown for a PMID.

    Reuses valid markdown from the most recent completed job. Invalid cached
    PMC content is refetched before analysis; valid PMC and PDF-uploaded
    content reruns only variants, sentences, citations, and summary.
    """
    method = _requested_method(input.method)
    existing = get_job_by_pmid(input.pmid)
    if not existing or not existing.get("markdown_content"):
        raise HTTPException(
            status_code=404,
            detail=f"No existing markdown found for PMID {input.pmid}. "
            "Cannot regenerate without a prior analysis.",
        )

    markdown = existing["markdown_content"]
    stored_pmcid = str(existing.get("pmcid") or "").strip().upper()
    pmcid = stored_pmcid if re.fullmatch(r"PMC\d+", stored_pmcid) else None
    source = existing.get("source", "pmc")

    if source == "pmc" and not has_valid_article_markdown(
        markdown, pmcid or input.pmid
    ):
        if pmcid is None:
            loop = asyncio.get_running_loop()
            try:
                pmcid_map = await loop.run_in_executor(
                    None, get_pmcid_from_pmid, input.pmid
                )
                resolved_pmcid = str(
                    pmcid_map.get(input.pmid) or ""
                ).strip().upper()
                if re.fullmatch(r"PMC\d+", resolved_pmcid):
                    pmcid = resolved_pmcid
            except Exception as exc:
                logging.getLogger("uvicorn").warning(
                    "PMCID lookup failed while repairing PMID %s: %s",
                    input.pmid,
                    exc,
                )
        if pmcid is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"The cached article content for PMID {input.pmid} is invalid, "
                    "and a valid PMCID could not be resolved."
                ),
            )

        job_id = create_job(input.pmid, pmcid=pmcid, source="pmc", method=method)
        background_tasks.add_task(
            run_analysis_job, job_id, pmcid, pmid=input.pmid, method=method
        )
        return JobResponse(
            job_id=job_id,
            pmid=input.pmid,
            pmcid=pmcid,
            source="pmc",
            status="pending",
        )

    job_id = create_job(input.pmid, pmcid=pmcid, source=source, method=method)

    background_tasks.add_task(
        run_reanalysis_job, job_id, input.pmid, markdown, pmcid=pmcid, method=method
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
        updated_at=str(job.get("updated_at", "")),
        generation_metadata=job.get("generation_metadata"),
    )


@app.get("/jobs/pmid/{pmid}/runs", response_model=List[AnnotationRunResponse])
async def list_runs_for_pmid(pmid: str):
    """List every analysis run stored for a PMID, newest first."""
    pmid = pmid.strip()
    if not re.match(r"^\d{1,10}$", pmid):
        raise HTTPException(status_code=422, detail="PMID must be numeric")
    return [
        AnnotationRunResponse(
            job_id=str(run["id"]),
            status=run["status"],
            created_at=str(run.get("created_at", "")),
            updated_at=str(run.get("updated_at", "")),
            generation_metadata=run.get("generation_metadata"),
        )
        for run in list_runs_by_pmid(pmid)
    ]


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
        updated_at=str(job.get("updated_at", "")),
        generation_metadata=job.get("generation_metadata"),
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
        updated_at=str(job.get("updated_at", "")),
        generation_metadata=job.get("generation_metadata"),
    )


@app.post("/triage/sessions")
async def create_triage_session_endpoint(
    req: CreateTriageSessionRequest, background_tasks: BackgroundTasks
):
    """Create missing weekly sessions and optionally regenerate existing ones."""
    loop = asyncio.get_running_loop()
    jobs = await loop.run_in_executor(None, list_weekly_jobs, req.project_id)
    if not jobs:
        raise HTTPException(
            status_code=404, detail="No weekly jobs found for this project"
        )
    weekly_jobs = []
    for job in jobs:
        match = re.search(r"\((\w+ \d+ \d+) to", job.get("name", ""))
        if not match:
            continue
        try:
            week_date = datetime.datetime.strptime(
                match.group(1), "%b %d %Y"
            ).date().isoformat()
        except ValueError:
            continue
        weekly_jobs.append({**job, "week_date": week_date})

    weekly_jobs.sort(key=lambda job: job["week_date"], reverse=True)
    recent_jobs = weekly_jobs[: req.weeks]
    if not recent_jobs:
        raise HTTPException(status_code=404, detail="No weekly digest jobs found")

    created: list[dict] = []
    queued: list[dict] = []
    regenerated_count = 0
    active_count = 0
    existing_count = 0
    latest_session_id = None
    for job in recent_jobs:
        existing = await loop.run_in_executor(
            None, find_triage_session_by_week, req.project_id, job["week_date"]
        )
        if existing:
            session_id = str(existing["id"])
            existing_count += 1
            if req.regenerate_existing:
                if existing["status"] in {"pending", "scoring"}:
                    active_count += 1
                else:
                    update_triage_session_status(session_id, "pending")
                    queued.append(
                        {
                            "session_id": session_id,
                            "job_id": job["id"],
                            "preserve_submitted": True,
                        }
                    )
                    regenerated_count += 1
        else:
            session_id = create_triage_session(
                req.project_id, req.project_name, job["week_date"]
            )
            created.append({"session_id": session_id, "job_id": job["id"]})
            queued.append(
                {
                    "session_id": session_id,
                    "job_id": job["id"],
                    "preserve_submitted": False,
                }
            )
        if latest_session_id is None:
            latest_session_id = session_id

    if queued:
        background_tasks.add_task(run_triage_backfill, req.project_id, queued)

    return {
        "session_ids": [session["session_id"] for session in queued],
        "latest_session_id": latest_session_id,
        "created_count": len(created),
        "existing_count": existing_count,
        "regenerated_count": regenerated_count,
        "active_count": active_count,
    }


@app.get("/triage/sessions", response_model=list[TriageSessionListItem])
async def list_triage_sessions_endpoint():
    """List all triage sessions."""
    try:
        sessions = list_triage_sessions()
        return [
            TriageSessionListItem(
                id=str(s["id"]),
                project_id=s["project_id"],
                project_name=s["project_name"],
                week_date=s["week_date"],
                status=s["status"],
                article_count=s.get("article_count", 0),
                created_at=str(s.get("created_at", "")),
            )
            for s in sessions
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/triage/sessions/{session_id}", response_model=TriageSessionResponse)
async def get_triage_session_endpoint(session_id: str):
    """Get a triage session by ID."""
    session = get_triage_session(session_id)
    if not session:
        raise HTTPException(
            status_code=404, detail=f"Triage session {session_id} not found"
        )
    articles = session.get("articles") or []
    pmids = [str(a["pmid"]) for a in articles if a.get("pmid")]
    try:
        live_jobs = get_latest_active_or_completed_jobs_for_pmids(pmids)
    except RuntimeError:
        live_jobs = {}

    enriched_articles = []
    for article in articles:
        pmid = str(article.get("pmid") or "")
        live_job = live_jobs.get(pmid)
        if live_job and is_job_in_flight(live_job):
            enriched_articles.append(
                {**article, "decision": "submitted", "job_id": str(live_job["id"])}
            )
        elif (
            live_job
            and live_job.get("status") == "completed"
            and article.get("decision") == "pending"
            and has_valid_article_markdown(
                live_job.get("markdown_content"),
                live_job.get("pmcid") or article.get("pmcid") or pmid,
            )
        ):
            enriched_articles.append(
                {**article, "decision": "submitted", "job_id": str(live_job["id"])}
            )
        else:
            enriched_articles.append(article)

    return TriageSessionResponse(
        id=str(session["id"]),
        project_id=session["project_id"],
        project_name=session["project_name"],
        week_date=str(session["week_date"]),
        status=session["status"],
        articles=[TriageArticleResponse(**a) for a in enriched_articles],
        error=session.get("error"),
        created_at=str(session.get("created_at", "")),
    )


@app.post("/triage/sessions/{session_id}/regenerate")
async def regenerate_triage_session(
    session_id: str, background_tasks: BackgroundTasks
):
    """Re-fetch and re-score a session, restoring dismissed papers to pending."""
    session = get_triage_session(session_id)
    if not session:
        raise HTTPException(
            status_code=404, detail=f"Triage session {session_id} not found"
        )
    if session["status"] in {"pending", "scoring"}:
        raise HTTPException(status_code=409, detail="Session is already being generated")

    loop = asyncio.get_running_loop()
    jobs = await loop.run_in_executor(None, list_weekly_jobs, session["project_id"])
    matching_job = None
    for job in jobs:
        match = re.search(r"\((\w+ \d+ \d+) to", job.get("name", ""))
        if not match:
            continue
        try:
            job_week = datetime.datetime.strptime(
                match.group(1), "%b %d %Y"
            ).date().isoformat()
        except ValueError:
            continue
        if job_week == str(session["week_date"]):
            matching_job = job
            break

    if matching_job is None:
        raise HTTPException(
            status_code=404,
            detail="The LitSuggest weekly job for this session is no longer available",
        )

    update_triage_session_status(session_id, "pending")
    background_tasks.add_task(
        run_triage_job,
        session_id,
        session["project_id"],
        matching_job["id"],
        True,
    )
    return {"session_id": session_id, "regenerating": True}


@app.get("/triage/sessions/{session_id}/stream")
async def stream_triage_session(session_id: str):
    """Stream triage session status updates via Server-Sent Events."""

    async def event_generator():
        loop = asyncio.get_running_loop()
        heartbeat_counter = 0
        while True:
            session = await loop.run_in_executor(None, get_triage_session, session_id)
            if session is None:
                yield f"event: error\ndata: {json.dumps({'error': 'Session not found'})}\n\n"
                break
            articles = session.get("articles") or []
            payload = json.dumps(
                {
                    "session_id": str(session["id"]),
                    "status": session["status"],
                    "article_count": len(articles),
                    "error": session.get("error"),
                }
            )
            yield f"data: {payload}\n\n"
            if session["status"] in ("completed", "error"):
                break
            await asyncio.sleep(2)
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


def _find_in_flight_job(pmid: str, existing: dict | None = None) -> dict | None:
    """Return an active in-flight job for ``pmid`` if one exists."""
    try:
        live = get_latest_active_or_completed_jobs_for_pmids([pmid]).get(pmid)
        if live and is_job_in_flight(live):
            return live
    except RuntimeError:
        pass
    if existing and is_job_in_flight(existing):
        return existing
    return None


async def _submit_triage_article(
    session_id: str,
    pmid: str,
    article: dict,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Reuse or start analysis for one article already loaded from a session."""
    stored_pmcid = str(article.get("pmcid") or "").strip().upper()
    pmcid = stored_pmcid if re.fullmatch(r"PMC\d+", stored_pmcid) else None

    existing = get_job_by_pmid(pmid)
    in_flight = _find_in_flight_job(pmid, existing)
    if in_flight:
        job_id = str(in_flight["id"])
        update_triage_article_decision(
            session_id, pmid, "submitted", job_id=job_id
        )
        raise HTTPException(
            status_code=409,
            detail=f"PMID {pmid} is already being processed in the pipeline.",
        )

    if existing and existing.get("status") == "completed":
        existing_identifier = existing.get("pmcid") or pmcid or pmid
        if has_valid_article_markdown(
            existing.get("markdown_content"), existing_identifier
        ):
            job_id = str(existing["id"])
            update_triage_article_decision(
                session_id, pmid, "submitted", job_id=job_id
            )
            return {"job_id": job_id, "pmid": pmid}
        logging.getLogger("uvicorn").warning(
            "Ignoring completed job %s for PMID %s because its cached article content is invalid",
            existing.get("id"),
            pmid,
        )

    if pmcid is None:
        loop = asyncio.get_running_loop()
        try:
            pmcid_map = await loop.run_in_executor(None, get_pmcid_from_pmid, pmid)
            resolved_pmcid = str(pmcid_map.get(pmid) or "").strip().upper()
            if re.fullmatch(r"PMC\d+", resolved_pmcid):
                pmcid = resolved_pmcid
        except Exception as exc:
            logging.getLogger("uvicorn").warning(
                "PMCID lookup failed for triage PMID %s: %s", pmid, exc
            )

    if pmcid is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No valid PubMed Central full text is available for PMID {pmid}. "
                "The pipeline was not started."
            ),
        )

    job_id = create_job(
        pmid, pmcid=pmcid, source="pmc", method=_requested_method(None)
    )
    background_tasks.add_task(run_analysis_job, job_id, pmcid, pmid=pmid)

    update_triage_article_decision(session_id, pmid, "submitted", job_id=job_id)
    return {"job_id": job_id, "pmid": pmid}


@app.post("/triage/sessions/{session_id}/articles/{pmid}/submit")
async def submit_triage_article(
    session_id: str, pmid: str, background_tasks: BackgroundTasks
):
    """Submit a triage article to the full analysis pipeline."""
    pmid = pmid.strip()
    if not re.match(r"^\d{1,10}$", pmid):
        raise HTTPException(status_code=422, detail="PMID must be numeric")

    session = get_triage_session(session_id)
    if not session:
        raise HTTPException(
            status_code=404, detail=f"Triage session {session_id} not found"
        )

    article = next(
        (
            candidate
            for candidate in (session.get("articles") or [])
            if str(candidate.get("pmid")) == pmid
        ),
        None,
    )
    if article is None:
        raise HTTPException(
            status_code=404,
            detail=f"PMID {pmid} is not part of triage session {session_id}",
        )

    return await _submit_triage_article(session_id, pmid, article, background_tasks)


@app.post("/triage/sessions/{session_id}/submit-all")
async def submit_all_triage_articles(
    session_id: str, background_tasks: BackgroundTasks
):
    """Submit every eligible pending article in a triage session."""
    session = get_triage_session(session_id)
    if not session:
        raise HTTPException(
            status_code=404, detail=f"Triage session {session_id} not found"
        )

    submitted = 0
    skipped_dismissed = 0
    skipped_submitted = 0
    skipped_no_full_text = 0
    for article in session.get("articles") or []:
        decision = article.get("decision")
        if decision == "dismissed":
            skipped_dismissed += 1
            continue
        if decision == "submitted":
            skipped_submitted += 1
            continue

        try:
            await _submit_triage_article(
                session_id, str(article["pmid"]), article, background_tasks
            )
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            if "already being processed" in str(exc.detail):
                skipped_submitted += 1
            else:
                skipped_no_full_text += 1
            continue
        submitted += 1

    return {
        "session_id": session_id,
        "submitted": submitted,
        "skipped_dismissed": skipped_dismissed,
        "skipped_submitted": skipped_submitted,
        "skipped_no_full_text": skipped_no_full_text,
    }


@app.patch("/triage/sessions/{session_id}/articles/{pmid}")
async def update_triage_article_decision_endpoint(
    session_id: str, pmid: str, req: UpdateArticleDecisionRequest
):
    """Update the decision for a triage article."""
    allowed = {"pending", "dismissed"}
    if req.decision not in allowed:
        raise HTTPException(
            status_code=422, detail=f"decision must be one of: {allowed}"
        )

    session = get_triage_session(session_id)
    if not session:
        raise HTTPException(
            status_code=404, detail=f"Triage session {session_id} not found"
        )

    update_triage_article_decision(session_id, pmid, req.decision)
    return {"session_id": session_id, "pmid": pmid, "decision": req.decision}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
