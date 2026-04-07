# PDF Upload & PMID Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the system from PMCID-keyed to PMID-keyed article identification, and add PDF upload via Datalab API so users can upload their own papers.

**Architecture:** Two-track ingestion (PMC download vs PDF upload) sharing a common annotation pipeline. The database schema migrates from PMCID to PMID as the primary key. A new Datalab client handles PDF-to-markdown conversion. The frontend adds a second dialog for PDF upload alongside the existing PMC article flow.

**Tech Stack:** FastAPI, psycopg2, React 18/TypeScript, shadcn/ui, Datalab REST API, `requests` library (already a dependency).

---

## File Structure

```
packages/api/src/
  database.py      — MODIFY: new schema (pmid NOT NULL, source col), PMID-based queries
  datalab.py       — CREATE: Datalab API client (convert PDF + poll for result)
  jobs.py          — MODIFY: add pdf upload job function, refactor to use pmid
  main.py          — MODIFY: new /upload endpoint, refactored endpoints, /articles

packages/app/src/
  lib/api.ts                    — MODIFY: new uploadPdf(), getJobByPmid(), listArticles()
  components/AddArticleDialog.tsx  — MODIFY: minor text updates
  components/UploadPdfDialog.tsx   — CREATE: PDF upload dialog
  pages/Dashboard.tsx           — MODIFY: two buttons, use listArticles()
  pages/Viewer.tsx              — MODIFY: pmid route param
  hooks/useViewerData.ts        — MODIFY: fetch by pmid
  App.tsx                       — MODIFY: route /viewer/:pmid
  components/viewer/ViewerHeader.tsx — MODIFY: pmid prop naming
```

---

### Task 1: Database Schema Migration (PMID as primary key)

**Files:**
- Modify: `packages/api/src/database.py`

This task rewrites the schema to use PMID as the primary lookup key, adds `source` column, and creates PMID-based query functions.

- [ ] **Step 1: Update `_CREATE_TABLE_SQL` and `_MIGRATE_SQL`**

Replace the existing schema constants in `packages/api/src/database.py` (lines 87-115):

```python
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS annotation_jobs (
    id                  UUID        PRIMARY KEY,
    pmid                TEXT        NOT NULL,
    pmcid               TEXT,
    source              TEXT        NOT NULL DEFAULT 'pmc',
    status              TEXT        NOT NULL DEFAULT 'pending',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    progress            TEXT,
    markdown_content    TEXT,
    error               TEXT,
    title               TEXT,
    json_content        JSONB       DEFAULT '{}',
    generation_metadata JSONB       DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_annotation_jobs_pmid ON annotation_jobs(pmid);
CREATE INDEX IF NOT EXISTS idx_annotation_jobs_pmcid ON annotation_jobs(pmcid);
"""

_MIGRATE_SQL = """
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'pmc';
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS progress            TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS markdown_content    TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS error               TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS pmid                TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS pmcid               TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS title               TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS json_content        JSONB DEFAULT '{}';
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS generation_metadata JSONB DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_annotation_jobs_pmid ON annotation_jobs(pmid);
"""
```

- [ ] **Step 2: Update `create_job` to accept pmid and source**

Replace the `create_job` function (lines 187-211):

```python
def create_job(pmid: str, *, pmcid: str | None = None, source: str = "pmc") -> str:
    """Insert a new analysis job with status='pending'.

    Returns the new job's UUID as a string.
    """
    job_id = str(uuid.uuid4())
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO annotation_jobs (id, pmid, pmcid, source, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'pending', NOW(), NOW())
                    """,
                    (job_id, pmid, pmcid, source),
                )
            conn.commit()
            logger.debug(f"Created job {job_id} for pmid={pmid}, source={source}.")
            return job_id
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(f"create_job() failed for pmid={pmid}: {exc}")
            raise
```

- [ ] **Step 3: Add `get_job_by_pmid` function and update `_extract_annotation_data`**

Add this function after `get_job` (after line 306), and update `_extract_annotation_data` to use pmid:

```python
def get_job_by_pmid(pmid: str) -> dict | None:
    """Return the most recent job for a given pmid, or None.

    Prefers completed jobs; falls back to the latest job of any status.
    """
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                      FROM annotation_jobs
                     WHERE pmid = %s
                     ORDER BY
                       CASE status WHEN 'completed' THEN 0 ELSE 1 END,
                       created_at DESC
                     LIMIT 1
                    """,
                    (pmid,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return _extract_annotation_data(dict(row))
        except Exception as exc:
            logger.error(f"get_job_by_pmid() failed for pmid={pmid}: {exc}")
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()
```

In `_extract_annotation_data` (line 176), update the pmcid reference to also check pmid:

```python
                "pmcid": ann.get("pmcid", row.get("pmcid", row.get("pmid", ""))),
```

- [ ] **Step 4: Update `list_pmcids` to `list_articles`**

Replace `list_pmcids` (lines 344-369):

```python
def list_articles() -> list[dict]:
    """Return pmid, pmcid, source, and title for all completed jobs."""
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (pmid)
                           pmid,
                           pmcid,
                           source,
                           title,
                           json_content->'annotation_data'->>'summary' AS summary
                      FROM annotation_jobs
                     WHERE status = 'completed'
                     ORDER BY pmid, created_at DESC NULLS LAST
                    """
                )
                return [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            logger.error(f"list_articles() failed: {exc}")
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()
```

- [ ] **Step 5: Commit**

```bash
git add packages/api/src/database.py
git commit -m "refactor: migrate database schema from PMCID to PMID as primary key"
```

---

### Task 2: Datalab API Client

**Files:**
- Create: `packages/api/src/datalab.py`

- [ ] **Step 1: Create the Datalab client module**

Create `packages/api/src/datalab.py`:

```python
"""
Datalab API client for PDF-to-markdown conversion.

Uses the Datalab REST API (https://www.datalab.to/api/v1/) to convert
uploaded PDFs into markdown text.
"""

import os
import time

import requests
from loguru import logger

DATALAB_API_KEY: str | None = os.environ.get("DATALAB_API_KEY")
DATALAB_BASE_URL = "https://www.datalab.to/api/v1"
POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 300  # 5 minutes


def convert_pdf_to_markdown(pdf_bytes: bytes, filename: str = "upload.pdf") -> str:
    """Send a PDF to Datalab and return the converted markdown.

    Blocks until conversion is complete or timeout is reached.

    Args:
        pdf_bytes: Raw PDF file content.
        filename:  Original filename (sent to Datalab for metadata).

    Returns:
        The markdown string produced by Datalab.

    Raises:
        RuntimeError: If the API key is missing, conversion fails, or timeout.
    """
    if not DATALAB_API_KEY:
        raise RuntimeError("DATALAB_API_KEY environment variable is not set.")

    headers = {"X-API-Key": DATALAB_API_KEY}

    # Step 1: Submit the PDF for conversion
    logger.info(f"Submitting PDF ({len(pdf_bytes)} bytes) to Datalab for conversion")
    resp = requests.post(
        f"{DATALAB_BASE_URL}/convert",
        headers=headers,
        files={"file": (filename, pdf_bytes, "application/pdf")},
        data={"output_format": "markdown"},
        timeout=60,
    )
    resp.raise_for_status()
    submit_data = resp.json()

    if not submit_data.get("success", False):
        error = submit_data.get("error", "Unknown error")
        raise RuntimeError(f"Datalab conversion submission failed: {error}")

    request_check_url = submit_data.get("request_check_url")
    request_id = submit_data.get("request_id")
    if not request_check_url:
        raise RuntimeError("Datalab did not return a request_check_url")

    logger.info(f"Datalab conversion submitted, request_id={request_id}")

    # Step 2: Poll for the result
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)

        check_resp = requests.get(
            request_check_url,
            headers=headers,
            timeout=30,
        )
        check_resp.raise_for_status()
        result = check_resp.json()

        status = result.get("status", "")
        if status == "complete":
            markdown = result.get("markdown")
            if not markdown:
                raise RuntimeError("Datalab returned empty markdown")
            logger.info(
                f"Datalab conversion complete for request_id={request_id} "
                f"({len(markdown)} chars)"
            )
            return markdown

        if result.get("success") is False:
            error = result.get("error", "Unknown error")
            raise RuntimeError(f"Datalab conversion failed: {error}")

        logger.debug(f"Datalab conversion in progress (status={status})")

    raise RuntimeError(
        f"Datalab conversion timed out after {POLL_TIMEOUT_SECONDS}s "
        f"for request_id={request_id}"
    )
```

- [ ] **Step 2: Commit**

```bash
git add packages/api/src/datalab.py
git commit -m "feat: add Datalab API client for PDF-to-markdown conversion"
```

---

### Task 3: Backend Job Runner for PDF Upload

**Files:**
- Modify: `packages/api/src/jobs.py`

- [ ] **Step 1: Add the PDF upload job function**

Add this import at the top of `jobs.py` (after line 10, with the other imports):

```python
from src.datalab import convert_pdf_to_markdown
```

Then add a new function at the end of the file (after `run_analysis_job`):

```python
async def run_pdf_upload_job(
    job_id: str,
    pmid: str,
    pdf_bytes: bytes,
    filename: str = "upload.pdf",
) -> None:
    """Run the full analysis pipeline for a user-uploaded PDF.

    Args:
        job_id:    UUID of the annotation_jobs row to update.
        pmid:      PubMed ID provided by the user.
        pdf_bytes: Raw PDF file content.
        filename:  Original filename of the uploaded PDF.
    """
    loop = asyncio.get_running_loop()
    start_time = time.monotonic()

    try:
        # ------------------------------------------------------------------
        # Step 1 — Convert PDF to markdown via Datalab
        # ------------------------------------------------------------------
        update_job(
            job_id,
            status="fetching_article",
            progress="Converting PDF to markdown via Datalab...",
        )
        logger.info(f"[{job_id}] Converting PDF ({len(pdf_bytes)} bytes) via Datalab")

        markdown = await loop.run_in_executor(
            None, convert_pdf_to_markdown, pdf_bytes, filename
        )
        if not markdown:
            raise ValueError("Datalab returned empty markdown")

        logger.info(f"[{job_id}] PDF conversion complete ({len(markdown)} chars)")

        # ------------------------------------------------------------------
        # Step 2 — Persist markdown to disk
        # ------------------------------------------------------------------
        articles_dir: Path = API_ROOT / "data" / "articles"
        articles_dir.mkdir(parents=True, exist_ok=True)
        article_path: Path = articles_dir / f"{pmid}.md"
        article_path.write_text(markdown, encoding="utf-8")
        logger.info(f"[{job_id}] Saved markdown to {article_path}")

        pipeline_articles_dir: Path = PIPELINE_ROOT / "data" / "articles"
        pipeline_articles_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_articles_dir / f"{pmid}.md").write_text(markdown, encoding="utf-8")
        logger.info(
            f"[{job_id}] Cached markdown to pipeline path {pipeline_articles_dir}"
        )

        # ------------------------------------------------------------------
        # Step 3 — Extract variants
        # ------------------------------------------------------------------
        update_job(
            job_id,
            status="extracting_variants",
            progress="Extracting genetic variants...",
        )
        logger.info(f"[{job_id}] Extracting variants for {pmid}")

        variants: list[str] = await loop.run_in_executor(
            None, extract_all_variants, markdown
        )
        logger.info(f"[{job_id}] Found {len(variants)} variant(s): {variants}")

        # ------------------------------------------------------------------
        # Step 4 — Generate association sentences
        # ------------------------------------------------------------------
        update_job(
            job_id,
            status="generating_sentences",
            progress="Generating association sentences...",
        )
        logger.info(f"[{job_id}] Generating sentences for {pmid}")

        sentence_gen = SentenceGenerator(
            method="batch_judge_ask",
            model=PIPELINE_MODEL,
            prompt_version="v5",
        )
        sentences: dict = await loop.run_in_executor(
            None, sentence_gen.generate, pmid, variants
        )
        logger.info(f"[{job_id}] Sentence generation complete")

        # ------------------------------------------------------------------
        # Step 5 — Find citations
        # ------------------------------------------------------------------
        update_job(
            job_id,
            status="finding_citations",
            progress="Finding supporting citations...",
        )
        logger.info(f"[{job_id}] Finding citations for {pmid}")

        associations_input: list[dict] = [
            {
                "variant": v,
                "sentence": s.sentence,
                "explanation": s.explanation,
            }
            for v, sents in sentences.items()
            for s in sents
        ]

        citation_finder = CitationFinder(
            method="one_shot_citations",
            model=PIPELINE_MODEL,
            prompt_version="v2",
        )
        citations = await loop.run_in_executor(
            None, citation_finder.find_citations, pmid, associations_input
        )
        logger.info(
            f"[{job_id}] Citation finding complete, {len(citations)} citation(s)"
        )

        # ------------------------------------------------------------------
        # Step 6 — Generate summary
        # ------------------------------------------------------------------
        update_job(
            job_id,
            status="generating_summary",
            progress="Generating summary...",
        )
        logger.info(f"[{job_id}] Generating summary for {pmid}")

        variants_data: list[dict] = [
            {"variant": v, "sentences": [s.sentence for s in sents]}
            for v, sents in sentences.items()
        ]
        citations_data: dict = {pmid: [c.model_dump() for c in citations]}

        summary_gen = SummaryGenerator(
            method="basic_summary",
            model=PIPELINE_MODEL,
            prompt_version="v3",
        )
        summary = await loop.run_in_executor(
            None, summary_gen.generate, pmid, variants_data, citations_data
        )
        logger.info(f"[{job_id}] Summary generation complete")

        # ------------------------------------------------------------------
        # Step 7 — Persist completed result
        # ------------------------------------------------------------------
        summary_dump = summary.model_dump()
        title = _extract_title(markdown)
        elapsed = time.monotonic() - start_time

        update_job(
            job_id,
            status="completed",
            progress="",
            title=title,
            json_content={
                "annotations": {
                    v: [s.model_dump() for s in sents] for v, sents in sentences.items()
                },
                "annotation_citations": [c.model_dump() for c in citations],
                "annotation_data": {
                    "pmcid": pmid,
                    "summary": summary_dump.get("summary", ""),
                },
            },
            markdown_content=markdown,
            generation_metadata={
                "config_name": "api_pdf_upload",
                "variant_extraction_method": "regex_v5",
                "sentence_generation_method": "batch_judge_ask",
                "sentence_model": PIPELINE_MODEL,
                "citation_model": PIPELINE_MODEL,
                "summary_model": PIPELINE_MODEL,
                "elapsed_seconds": round(elapsed, 2),
                "git_sha": "unknown",
                "stages_run": ["datalab_convert", "variants", "sentences", "citations", "summary"],
            },
        )
        logger.info(
            f"[{job_id}] Job completed successfully for {pmid} ({len(citations)} citation(s))"
        )

    except Exception as exc:
        logger.exception(f"[{job_id}] Job failed for {pmid}: {exc}")
        try:
            update_job(
                job_id, status="failed", error=str(exc), progress="Analysis failed"
            )
        except Exception as db_exc:
            logger.error(f"[{job_id}] Also failed to record failure in DB: {db_exc}")
```

- [ ] **Step 2: Commit**

```bash
git add packages/api/src/jobs.py
git commit -m "feat: add PDF upload job runner using Datalab for conversion"
```

---

### Task 4: API Endpoints (upload, refactor analyze, /articles)

**Files:**
- Modify: `packages/api/src/main.py`

- [ ] **Step 1: Update imports and add new request/response models**

At the top of `main.py`, update the database import (line 26) and add the upload import:

```python
from .database import init_db, create_job, get_job, get_job_by_pmcid, get_job_by_pmid, list_articles
from .jobs import run_analysis_job, run_pdf_upload_job
```

Add `File, UploadFile` to the FastAPI import (line 13):

```python
from fastapi import FastAPI, HTTPException, BackgroundTasks, File, UploadFile, Form
```

Update `JobResponse` model (lines 113-122) to include `pmid` and `source`:

```python
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
```

- [ ] **Step 2: Add the `/upload` endpoint**

Add this endpoint after the existing `/analyze/pmid` endpoint (after line 333):

```python
@app.post("/upload", response_model=JobResponse)
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    pmid: str = Form(...),
):
    """Upload a PDF for full pipeline analysis via Datalab conversion.

    Accepts multipart form data with a PDF file and a PMID.
    """
    # Validate PMID format
    pmid = pmid.strip()
    if not re.match(r"^\d{1,10}$", pmid):
        raise HTTPException(status_code=422, detail="PMID must be numeric (e.g. 38234567)")

    # Check for existing job
    existing = get_job_by_pmid(pmid)
    if existing and existing["status"] == "completed":
        raise HTTPException(status_code=409, detail=f"An analysis already exists for PMID {pmid}")

    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are accepted")

    # Read file content
    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    # Create job and kick off background task
    job_id = create_job(pmid, source="pdf_upload")

    background_tasks.add_task(
        run_pdf_upload_job, job_id, pmid, pdf_bytes, file.filename
    )

    return JobResponse(job_id=job_id, pmid=pmid, source="pdf_upload", status="pending")
```

- [ ] **Step 3: Refactor `/analyze` endpoint to use PMID**

Update the `analyze_pmcid` function (lines 247-278). The endpoint still accepts a PMCID but now resolves to PMID internally:

```python
@app.post("/analyze")
async def analyze_pmcid(input: AnalyzeRequest, background_tasks: BackgroundTasks):
    """Submit a PMCID for full pipeline analysis (background job)."""
    pmcid = input.pmcid.strip()
    if not pmcid.upper().startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    pmcid = pmcid.upper()

    # Resolve PMCID to PMID
    loop = asyncio.get_running_loop()
    pmid_val = await loop.run_in_executor(None, _get_pmid_from_pmcid_safe, pmcid)

    if not input.force:
        # Check by PMID first if we have one, then by PMCID
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

    # Use PMID if resolved, otherwise use PMCID as identifier
    identifier = pmid_val or pmcid
    job_id = create_job(identifier, pmcid=pmcid, source="pmc")

    background_tasks.add_task(run_analysis_job, job_id, pmcid, pmid=pmid_val)

    return JobResponse(job_id=job_id, pmid=identifier, pmcid=pmcid, source="pmc", status="pending")
```

Add a helper function (before the endpoints, after the existing fetch functions). This mirrors the logic from `jobs.py` to avoid circular imports:

```python
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
```

Also add `import requests` to the imports at the top of `main.py`.

- [ ] **Step 4: Refactor `/analyze/pmid` endpoint**

Update the `analyze_pmid_endpoint` function (lines 281-333):

```python
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

    # The pipeline job still uses PMCID for download if available
    download_id = pmcid.upper() if pmcid else input.pmid
    job_id = create_job(input.pmid, pmcid=pmcid.upper() if pmcid else None, source="pmc")

    article_text = None if pmcid else input.article_text
    background_tasks.add_task(
        run_analysis_job, job_id, download_id, article_text=article_text, pmid=input.pmid
    )

    return JobResponse(job_id=job_id, pmid=input.pmid, pmcid=pmcid.upper() if pmcid else None, source="pmc", status="pending")
```

- [ ] **Step 5: Update remaining endpoints**

Update `get_job_by_pmid_endpoint` (lines 336-367):

```python
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
```

Update the `/pmcids` endpoint to `/articles` (lines 448-454):

```python
@app.get("/articles")
async def get_articles():
    """List all completed analyses with pmid, title, and summary."""
    try:
        return list_articles()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
```

Keep the old `/pmcids` endpoint as an alias for backwards compatibility:

```python
@app.get("/pmcids")
async def get_pmcids():
    """Legacy alias for /articles."""
    return await get_articles()
```

Update `get_job_status` (lines 457-472) and `stream_job_status` (lines 393-445) to include pmid in responses:

In `get_job_status`:
```python
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
```

In `stream_job_status`, update the payload (line 416-422):
```python
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
```

- [ ] **Step 6: Update `run_analysis_job` to accept pmid parameter**

In `packages/api/src/jobs.py`, update the `run_analysis_job` signature and the final step where it persists results. Change the function signature (line 69-73):

```python
async def run_analysis_job(
    job_id: str,
    pmcid: str,
    article_text: str | None = None,
    pmid: str | None = None,
) -> None:
```

In step 7 (line 236), remove the PMID lookup since we now have it. Replace lines 236-237:

```python
        title = _extract_title(markdown)
        # Use provided pmid, or look it up from pmcid
        if not pmid:
            pmid = await loop.run_in_executor(None, _get_pmid_from_pmcid, pmcid)
        elapsed = time.monotonic() - start_time
```

And update the `update_job` call to not pass pmid separately (it's already in the row from `create_job`).

- [ ] **Step 7: Commit**

```bash
git add packages/api/src/main.py packages/api/src/jobs.py
git commit -m "feat: add /upload endpoint, refactor API to use PMID as primary key"
```

---

### Task 5: Frontend API Client Updates

**Files:**
- Modify: `packages/app/src/lib/api.ts`

- [ ] **Step 1: Update types and API functions**

Replace the entire contents of `packages/app/src/lib/api.ts`:

```typescript
const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'https://autogkb-api.up.railway.app';

export interface JobResponse {
  job_id: string;
  pmid: string;
  pmcid: string | null;
  source: string;
  status: 'pending' | 'fetching_article' | 'extracting_variants' | 'generating_sentences' | 'finding_citations' | 'generating_summary' | 'completed' | 'failed';
  progress: string | null;
  annotation_data: Record<string, unknown> | null;
  markdown_content: string | null;
  error: string | null;
  created_at: string | null;
}

export const STATUS_LABELS: Record<string, string> = {
  pending: 'Queued...',
  fetching_article: 'Fetching article...',
  extracting_variants: 'Extracting genetic variants...',
  generating_sentences: 'Generating association sentences...',
  finding_citations: 'Finding supporting citations...',
  generating_summary: 'Generating summary...',
  completed: 'Analysis complete!',
  failed: 'Analysis failed',
};

export interface ArticleEntry {
  pmid: string;
  pmcid: string | null;
  source: string;
  title: string | null;
  summary: string | null;
}

export async function listArticles(): Promise<ArticleEntry[]> {
  const res = await fetch(`${API_URL}/articles`);
  if (!res.ok) return [];
  return res.json();
}

export async function analyzeArticle(pmcid: string, force = false): Promise<JobResponse> {
  const res = await fetch(`${API_URL}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pmcid, force }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function uploadPdf(file: File, pmid: string): Promise<JobResponse> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('pmid', pmid);

  const res = await fetch(`${API_URL}/upload`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function getJob(jobId: string): Promise<JobResponse> {
  const res = await fetch(`${API_URL}/jobs/${jobId}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function getJobByPmid(pmid: string): Promise<JobResponse | null> {
  const res = await fetch(`${API_URL}/jobs/pmid/${pmid}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

/** @deprecated Use getJobByPmid instead */
export async function getJobByPmcid(pmcid: string): Promise<JobResponse | null> {
  const res = await fetch(`${API_URL}/jobs/pmcid/${pmcid}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

/** @deprecated Use listArticles instead */
export async function listPmcids(): Promise<ArticleEntry[]> {
  return listArticles();
}
```

- [ ] **Step 2: Commit**

```bash
git add packages/app/src/lib/api.ts
git commit -m "feat: update frontend API client for PMID-based endpoints and PDF upload"
```

---

### Task 6: Upload PDF Dialog Component

**Files:**
- Create: `packages/app/src/components/UploadPdfDialog.tsx`

- [ ] **Step 1: Create the dialog component**

Create `packages/app/src/components/UploadPdfDialog.tsx`:

```tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { AlertCircle, Loader2, Upload } from 'lucide-react';
import { uploadPdf, getJob, STATUS_LABELS, type JobResponse } from '@/lib/api';

interface UploadPdfDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (pmid: string, jobData: JobResponse) => void;
}

type DialogState = 'idle' | 'loading' | 'error';

const UploadPdfDialog: React.FC<UploadPdfDialogProps> = ({
  open,
  onOpenChange,
  onSuccess,
}) => {
  const [pmid, setPmid] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [dialogState, setDialogState] = useState<DialogState>('idle');
  const [statusLabel, setStatusLabel] = useState('');
  const [progressText, setProgressText] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState('');
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const resetForm = useCallback(() => {
    stopPolling();
    setPmid('');
    setFile(null);
    setDialogState('idle');
    setStatusLabel('');
    setProgressText(null);
    setErrorMessage('');
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, [stopPolling]);

  const handleClose = useCallback(() => {
    resetForm();
    onOpenChange(false);
  }, [resetForm, onOpenChange]);

  useEffect(() => {
    if (!open) {
      stopPolling();
    }
    return () => {
      stopPolling();
    };
  }, [open, stopPolling]);

  const handleSubmit = async () => {
    const trimmedPmid = pmid.trim();
    if (!trimmedPmid || !file) return;

    setDialogState('loading');
    setStatusLabel('Uploading PDF...');
    setProgressText(null);
    setErrorMessage('');

    try {
      const job = await uploadPdf(file, trimmedPmid);
      const jobId = job.job_id;

      setStatusLabel(STATUS_LABELS[job.status] ?? job.status);
      setProgressText(job.progress);

      if (job.status === 'completed') {
        stopPolling();
        setDialogState('idle');
        onSuccess?.(trimmedPmid, job);
        handleClose();
        return;
      }
      if (job.status === 'failed') {
        stopPolling();
        setErrorMessage(job.error ?? 'An unknown error occurred.');
        setDialogState('error');
        return;
      }

      intervalRef.current = setInterval(async () => {
        try {
          const updated = await getJob(jobId);
          setStatusLabel(STATUS_LABELS[updated.status] ?? updated.status);
          setProgressText(updated.progress);

          if (updated.status === 'completed') {
            stopPolling();
            setDialogState('idle');
            onSuccess?.(trimmedPmid, updated);
            handleClose();
          } else if (updated.status === 'failed') {
            stopPolling();
            setErrorMessage(updated.error ?? 'An unknown error occurred.');
            setDialogState('error');
          }
        } catch (pollError) {
          stopPolling();
          setErrorMessage(
            pollError instanceof Error ? pollError.message : 'Failed to check job status.'
          );
          setDialogState('error');
        }
      }, 2000);
    } catch (submitError) {
      stopPolling();
      setErrorMessage(
        submitError instanceof Error ? submitError.message : 'Failed to upload PDF.'
      );
      setDialogState('error');
    }
  };

  const handleTryAgain = () => {
    setDialogState('idle');
    setErrorMessage('');
    setStatusLabel('');
    setProgressText(null);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0] ?? null;
    if (selected && !selected.name.toLowerCase().endsWith('.pdf')) {
      setErrorMessage('Please select a PDF file.');
      setDialogState('error');
      return;
    }
    setFile(selected);
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => {
      if (!isOpen) {
        handleClose();
      }
    }}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Upload PDF</DialogTitle>
          <DialogDescription>
            Upload a PDF article and provide its PubMed ID (PMID) to run the annotation pipeline.
            This process typically takes 1-3 minutes.
          </DialogDescription>
        </DialogHeader>

        {dialogState === 'idle' && (
          <>
            <div className="grid gap-4 py-4">
              <div className="grid gap-2">
                <Label htmlFor="pmid">PubMed ID (PMID)</Label>
                <Input
                  id="pmid"
                  placeholder="e.g., 38234567"
                  value={pmid}
                  onChange={(e) => setPmid(e.target.value)}
                />
                <p className="text-sm text-muted-foreground">
                  Enter the numeric PMID for this article
                </p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="pdf-file">PDF File</Label>
                <Input
                  id="pdf-file"
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf"
                  onChange={handleFileChange}
                />
                {file && (
                  <p className="text-sm text-muted-foreground">
                    Selected: {file.name} ({(file.size / 1024 / 1024).toFixed(1)} MB)
                  </p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleSubmit}
                disabled={!pmid.trim() || !file}
              >
                <Upload className="w-4 h-4 mr-2" />
                Upload & Analyze
              </Button>
            </DialogFooter>
          </>
        )}

        {dialogState === 'loading' && (
          <>
            <div className="flex flex-col items-center gap-4 py-8">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <div className="text-center">
                <p className="text-sm font-medium text-foreground">
                  {statusLabel}
                </p>
                {progressText && (
                  <p className="mt-1 text-sm text-muted-foreground">
                    {progressText}
                  </p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
            </DialogFooter>
          </>
        )}

        {dialogState === 'error' && (
          <>
            <div className="grid gap-4 py-4">
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{errorMessage}</AlertDescription>
              </Alert>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button type="button" onClick={handleTryAgain}>
                Try Again
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default UploadPdfDialog;
```

- [ ] **Step 2: Commit**

```bash
git add packages/app/src/components/UploadPdfDialog.tsx
git commit -m "feat: add UploadPdfDialog component for PDF upload workflow"
```

---

### Task 7: Dashboard — Two Buttons & PMID-based Navigation

**Files:**
- Modify: `packages/app/src/pages/Dashboard.tsx`

- [ ] **Step 1: Update Dashboard imports and state**

Update the imports at the top (lines 1-9):

```typescript
import React, { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import AddArticleDialog from '@/components/AddArticleDialog';
import UploadPdfDialog from '@/components/UploadPdfDialog';
import { toast } from 'sonner';
import type { JobResponse } from '@/lib/api';
import { listArticles } from '@/lib/api';
```

Update the Study interface and state (lines 11-24):

```typescript
interface Study {
  id: string;
  pmid: string;
  pmcid: string | null;
  source: string;
  title: string;
  description: string;
  numVariants: number | null;
  participants: number | null;
}

const Dashboard = () => {
  const navigate = useNavigate();
  const [searchTerm, setSearchTerm] = useState('');
  const [availableStudies, setAvailableStudies] = useState<Study[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [isUploadDialogOpen, setIsUploadDialogOpen] = useState(false);
```

- [ ] **Step 2: Update the data loading to use `listArticles`**

Replace the `useEffect` body (lines 26-97). Change the API call from `listPmcids` to `listArticles` and map entries with pmid:

```typescript
  useEffect(() => {
    const discoverAvailableStudies = async () => {
      setLoading(true);
      const studies: Study[] = [];

      try {
        const entries = await listArticles();
        for (const entry of entries) {
          let summary = entry.summary || '';
          let numVariants: number | null = null;

          if (summary.startsWith('{')) {
            try {
              const parsed = JSON.parse(summary);
              summary = parsed.summary || '';
              numVariants = parsed.num_variants ?? null;
            } catch { /* use raw summary */ }
          }

          studies.push({
            id: entry.pmid,
            pmid: entry.pmid,
            pmcid: entry.pmcid,
            source: entry.source,
            title: entry.title || entry.pmcid || entry.pmid,
            description: summary,
            numVariants,
            participants: null,
          });
        }
      } catch {
        // API unavailable — no fallback needed for fresh start
      }

      setAvailableStudies(studies);
      setLoading(false);
    };

    discoverAvailableStudies();
  }, []);
```

- [ ] **Step 3: Update handlers and navigation to use PMID**

Replace the click and success handlers (lines 109-116):

```typescript
  const handleStudyClick = (pmid: string) => {
    navigate(`/viewer/${pmid}`);
  };

  const handleArticleAdded = (pmid: string, jobData: JobResponse) => {
    toast.success(`Analysis complete for PMID ${pmid}!`);
    navigate(`/viewer/${pmid}`, { state: { dynamicData: jobData } });
  };

  const handlePdfUploaded = (pmid: string, jobData: JobResponse) => {
    toast.success(`PDF analysis complete for PMID ${pmid}!`);
    navigate(`/viewer/${pmid}`, { state: { dynamicData: jobData } });
  };
```

- [ ] **Step 4: Update the JSX — two buttons and PMID badges**

Replace the search/button area (lines 139-154):

```tsx
          <div className="max-w-2xl mx-auto space-y-4">
            <Input
              type="text"
              placeholder="Search by PMID, PMCID, or title..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full text-lg py-3 px-6"
            />
            <div className="flex gap-3 justify-center">
              <Button
                onClick={() => setIsAddDialogOpen(true)}
                size="lg"
              >
                Add PMC Article
              </Button>
              <Button
                onClick={() => setIsUploadDialogOpen(true)}
                size="lg"
                variant="outline"
              >
                Upload PDF
              </Button>
            </div>
          </div>
```

Update the card rendering (lines 157-191) to use `handleStudyClick` and show source badge:

```tsx
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredStudies.map((study) => (
            <Card 
              key={study.id}
              className="cursor-pointer hover:shadow-medium transition-bounce bg-card border-border hover:border-primary/20"
              onClick={() => handleStudyClick(study.pmid)}
            >
              <CardHeader>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <div className="px-3 py-1.5 bg-primary/10 text-primary text-xs font-medium rounded-full">
                      {study.pmcid || `PMID: ${study.pmid}`}
                    </div>
                    {study.source === 'pdf_upload' && (
                      <div className="px-2 py-1 bg-orange-100 text-orange-700 text-xs font-medium rounded-full">
                        PDF
                      </div>
                    )}
                  </div>
                  {study.numVariants != null && (
                    <div className="px-3 py-1.5 bg-accent text-accent-foreground text-xs font-medium rounded-full truncate">
                      {study.numVariants} Variant{study.numVariants !== 1 ? 's' : ''}
                    </div>
                  )}
                </div>
                <CardTitle className="text-lg leading-tight line-clamp-2">
                  {study.title}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <CardDescription className="mb-4 line-clamp-3">
                  {study.description}
                </CardDescription>
              </CardContent>
            </Card>
          ))}
        </div>
```

At the bottom, add the `UploadPdfDialog` alongside the existing `AddArticleDialog` (after line 235):

```tsx
      <AddArticleDialog
        open={isAddDialogOpen}
        onOpenChange={setIsAddDialogOpen}
        onSuccess={handleArticleAdded}
      />

      <UploadPdfDialog
        open={isUploadDialogOpen}
        onOpenChange={setIsUploadDialogOpen}
        onSuccess={handlePdfUploaded}
      />
```

- [ ] **Step 5: Update the search filter to include pmid**

Update `filteredStudies` (lines 99-107):

```typescript
  const filteredStudies = useMemo(() => {
    if (!searchTerm.trim()) return availableStudies;
    
    const term = searchTerm.toLowerCase();
    return availableStudies.filter(study => 
      study.pmid.toLowerCase().includes(term) ||
      (study.pmcid?.toLowerCase().includes(term) ?? false) ||
      study.title.toLowerCase().includes(term)
    );
  }, [searchTerm, availableStudies]);
```

- [ ] **Step 6: Commit**

```bash
git add packages/app/src/pages/Dashboard.tsx
git commit -m "feat: add Upload PDF button and switch Dashboard to PMID-based navigation"
```

---

### Task 8: Viewer & Router — PMID Route Param

**Files:**
- Modify: `packages/app/src/App.tsx`
- Modify: `packages/app/src/pages/Viewer.tsx`
- Modify: `packages/app/src/hooks/useViewerData.ts`
- Modify: `packages/app/src/components/viewer/ViewerHeader.tsx`

- [ ] **Step 1: Update App.tsx route**

Change the route param in `packages/app/src/App.tsx` (line 22):

```tsx
          <Route path="/viewer/:pmid" element={<Viewer />} />
```

- [ ] **Step 2: Update Viewer.tsx to use pmid**

In `packages/app/src/pages/Viewer.tsx`, update the param extraction (line 25):

```tsx
  const { pmid } = useParams<{ pmid: string }>();
```

Update all references from `pmcid` to `pmid` throughout the component:
- Line 27: `const { data, loading, error } = useViewerData(pmid);`
- Line 74: `{error || \`The requested PMID "${pmid}" could not be found.\`}`
- Line 86: `<ViewerHeader pmid={pmid || ''} />`

Remove the `AVAILABLE_PMCS` constant (lines 14-22) — it's unused legacy data.

- [ ] **Step 3: Update useViewerData.ts to fetch by PMID**

In `packages/app/src/hooks/useViewerData.ts`, update the import (line 3):

```typescript
import { type JobResponse, getJobByPmid } from '@/lib/api';
```

Rename the parameter from `pmcid` to `pmid` (line 13):

```typescript
export const useViewerData = (pmid: string | undefined) => {
```

Update the error message (line 22):

```typescript
        setError('No PMID provided');
```

Update the API fallback call (line 48):

```typescript
          jobData = await getJobByPmid(pmid);
```

Update error message at line 129:

```typescript
        setError(`Failed to load data for PMID: ${pmid}. Please ensure the article has been analyzed.`);
```

Update the dependency array (line 135):

```typescript
  }, [pmid, location.state]);
```

- [ ] **Step 4: Update ViewerHeader.tsx to use pmid**

In `packages/app/src/components/viewer/ViewerHeader.tsx`, rename the prop (lines 8-12):

```tsx
interface ViewerHeaderProps {
  pmid: string;
}

export const ViewerHeader: React.FC<ViewerHeaderProps> = ({ pmid }) => {
```

Update the regenerate handler (line 30) — the analyzeArticle call still takes a PMCID-like identifier, but the navigation should use pmid:

```tsx
  const handleRegenerate = async () => {
    setRegenerating(true);
    setStatusLabel(STATUS_LABELS['pending'] ?? 'Starting...');

    try {
      const job = await analyzeArticle(pmid, true);

      if (job.status === 'completed') {
        stopPolling();
        setRegenerating(false);
        navigate(`/viewer/${pmid}`, { state: { dynamicData: job } });
        return;
      }
      if (job.status === 'failed') {
        stopPolling();
        setRegenerating(false);
        toast.error(job.error ?? 'Regeneration failed.');
        return;
      }

      setStatusLabel(STATUS_LABELS[job.status] ?? job.status);

      intervalRef.current = setInterval(async () => {
        try {
          const updated = await getJob(job.job_id);
          setStatusLabel(STATUS_LABELS[updated.status] ?? updated.status);

          if (updated.status === 'completed') {
            stopPolling();
            setRegenerating(false);
            navigate(`/viewer/${pmid}`, { state: { dynamicData: updated } });
          } else if (updated.status === 'failed') {
            stopPolling();
            setRegenerating(false);
            toast.error(updated.error ?? 'Regeneration failed.');
          }
        } catch {
          stopPolling();
          setRegenerating(false);
          toast.error('Failed to check regeneration status.');
        }
      }, 2000);
    } catch (err) {
      stopPolling();
      setRegenerating(false);
      toast.error(err instanceof Error ? err.message : 'Failed to start regeneration.');
    }
  };
```

Update the header display (line 93):

```tsx
                <h1 className="text-lg font-bold !text-black dark:!text-white">{pmid}</h1>
```

- [ ] **Step 5: Commit**

```bash
git add packages/app/src/App.tsx packages/app/src/pages/Viewer.tsx packages/app/src/hooks/useViewerData.ts packages/app/src/components/viewer/ViewerHeader.tsx
git commit -m "feat: switch viewer and router to PMID-based routing"
```

---

### Task 9: Smoke Test & Verify

- [ ] **Step 1: Verify API starts without errors**

```bash
cd packages/api && python -c "from src.database import init_db; print('DB module loads OK')"
cd packages/api && python -c "from src.datalab import convert_pdf_to_markdown; print('Datalab module loads OK')"
cd packages/api && python -c "from src.main import app; print('API module loads OK')"
```

- [ ] **Step 2: Verify frontend compiles**

```bash
cd packages/app && npm run build
```

Expected: Build succeeds with no TypeScript errors.

- [ ] **Step 3: Commit any fixes if needed**

```bash
git add -A
git commit -m "fix: address build issues from PMID migration"
```
