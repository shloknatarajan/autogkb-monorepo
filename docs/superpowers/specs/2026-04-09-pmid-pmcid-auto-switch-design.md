# PMID-to-PMCID Auto-Switch on PDF Upload

## Problem

When a user uploads a PDF with a PMID, the system always uses the Datalab PDF-to-markdown conversion pipeline. If the paper is available as open access on PubMed Central, the PMC download pipeline produces better quality markdown without depending on an external conversion service. The system should automatically detect this and use the better path.

## Design

### Backend: `/upload` endpoint (`packages/api/src/main.py`)

Modify the upload handler to attempt PMID-to-PMCID conversion before processing the PDF:

1. After PMID validation (`re.match` check) but **before** `await file.read()`, call `get_pmcid_from_pmid(pmid)` in a thread executor (it's a blocking HTTP call to NCBI).
2. **If PMCID is found:**
   - Do not read the uploaded PDF bytes.
   - Create the job with `source="pmc"` and `pmcid` populated.
   - Queue `run_analysis_job(job_id, pmcid, pmid=pmid)` — the standard PMC download pipeline.
   - Return `JobResponse` with `source="pmc"`, `pmcid` set, `status="pending"`.
3. **If PMCID is not found** (returns `None`, or lookup fails/times out):
   - Proceed with existing behavior: read PDF bytes, create job with `source="pdf_upload"`, queue `run_pdf_upload_job`.

The PMCID lookup must be wrapped in a try/except so that network failures fall through silently to the PDF upload path.

### Frontend: `UploadPdfDialog.tsx`

After calling `uploadPdf()` and receiving the `JobResponse`:

1. Check if `job.source === "pmc"` and `job.pmcid` is non-null.
2. If true, set a local state flag (e.g., `openAccessDetected` + store the `pmcid` string).
3. In the `loading` dialog state, render a persistent info `Alert` above the spinner:
   - Uses the existing shadcn `Alert` component (non-destructive variant).
   - Text: "Open access version found (PMC{id}) — using the published version for better quality."
   - Styled with a blue/green info aesthetic (not the red destructive variant).

### API Client (`lib/api.ts`)

No changes. `JobResponse` already includes `source` and `pmcid` fields.

### Edge Cases

| Scenario | Behavior |
|----------|----------|
| PMCID lookup fails or times out | Fall through to PDF upload path silently |
| PMCID found but PMC download later fails | Normal error handling — job status becomes `failed` |
| Existing completed analysis for this PMID | Existing 409 check runs first (before PMCID lookup) |
| PMCID found but empty/invalid | Treat as not found, fall through to PDF upload |

### Files Modified

- `packages/api/src/main.py` — `/upload` endpoint
- `packages/app/src/components/UploadPdfDialog.tsx` — loading state UI
