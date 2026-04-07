# PDF Upload & PMID Migration Design

## Overview

Convert the system from PMCID-keyed to PMID-keyed article identification, and add a new PDF upload workflow that uses Datalab for PDF-to-markdown conversion. Uploaded PDFs go through the same annotation pipeline as PMC-sourced articles.

## Database Changes

### `annotation_jobs` table (start fresh)

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PRIMARY KEY | unchanged |
| pmid | TEXT NOT NULL | **new primary lookup key**, indexed |
| pmcid | TEXT | optional, populated when source is PMC |
| source | TEXT NOT NULL | `pmc` or `pdf_upload` |
| status | TEXT | default `pending` |
| created_at | TIMESTAMPTZ | unchanged |
| updated_at | TIMESTAMPTZ | unchanged |
| progress | TEXT | unchanged |
| markdown_content | TEXT | stores full article markdown (from PMC or Datalab) |
| error | TEXT | unchanged |
| title | TEXT | unchanged |
| json_content | JSONB | unchanged |
| generation_metadata | JSONB | unchanged |

Key changes:
- `pmid` becomes NOT NULL and indexed (replaces `pmcid` as the primary lookup)
- `pmcid` becomes optional (nullable)
- New `source` column to distinguish article origin
- Remove the old `pmid` column's nullable status

### Migration strategy

Start fresh. Drop existing data. Recreate the table with the new schema on startup (existing `_initialize_schema` pattern).

## API Changes

### New endpoint: `POST /upload`

Accepts multipart form data:
- `file`: PDF binary (required)
- `pmid`: PubMed ID string (required)

Flow:
1. Validate PMID format and that no existing job exists for this PMID
2. Create `annotation_jobs` row with `source='pdf_upload'`, status `pending`
3. In background job:
   a. Set status to `fetching_article`
   b. POST PDF to Datalab `https://www.datalab.to/api/v1/convert` with `X-API-Key` header, `output_format=markdown`
   c. Poll `GET /api/v1/convert/{request_id}` until status is `complete`
   d. Save returned markdown to `markdown_content` in DB
   e. Continue with existing annotation pipeline (variant extraction, sentences, citations, summary)
4. Return `{ job_id, status }` immediately (same pattern as `/analyze`)

### Refactored endpoints

| Old | New | Notes |
|-----|-----|-------|
| `POST /analyze` (takes pmcid) | `POST /analyze` (takes pmcid, resolves to pmid internally) | Keep for PMC workflow, set `source='pmc'` |
| `POST /analyze/pmid` | `POST /analyze/pmid` | Keep, set `source='pmc'` |
| `GET /jobs/pmcid/{pmcid}` | `GET /jobs/pmid/{pmid}` | Primary lookup by PMID |
| `GET /jobs/{job_id}` | `GET /jobs/{job_id}` | unchanged |
| `GET /jobs/{job_id}/stream` | `GET /jobs/{job_id}/stream` | unchanged |
| `GET /pmcids` | `GET /articles` | Returns all completed articles (both sources) |

Keep `GET /jobs/pmcid/{pmcid}` as a secondary lookup for backwards compatibility, but primary flow uses PMID.

### Datalab integration details

- **Endpoint**: `POST https://www.datalab.to/api/v1/convert`
- **Auth**: `X-API-Key: {DATALAB_API_KEY}` header
- **Request**: Multipart form with `file` (PDF binary) and `output_format=markdown`
- **Response**: Returns `request_id` and `request_check_url`
- **Polling**: `GET https://www.datalab.to/api/v1/convert/{request_id}` with same auth header
- **Result**: `status=complete` with `markdown` field containing the converted text
- **Limits**: 200MB max file size, 400 req/min rate limit

## Frontend Changes

### Dashboard (`Dashboard.tsx`)

Current "Add Article" button splits into two buttons:
- **"Add PMC Article"** - opens existing `AddArticleDialog` (refactored to use PMID internally)
- **"Upload PDF"** - opens new `UploadPdfDialog`

### New component: `UploadPdfDialog`

- Modal dialog with:
  - PMID text input (required)
  - File picker for PDF (accept `.pdf` only)
  - Upload button (disabled until both fields filled)
  - Progress display (reuses same job status polling as `AddArticleDialog`)
- On submit: POST multipart to `/upload` endpoint
- On complete: Navigate to viewer with the article data

### Refactored `AddArticleDialog`

- Update to work with PMID as the primary identifier
- Internal flow: user enters PMCID or PMID, API resolves and uses PMID as key

### Route changes

- `/viewer/{pmcid}` becomes `/viewer/{pmid}`
- Update `useViewerData` hook to fetch by PMID
- Update `api.ts` client to use new endpoint paths

### API client (`api.ts`)

- Add `uploadPdf(file: File, pmid: string)` method
- Update `getJobByPmcid` to `getJobByPmid`
- Update `getPmcids` to `getArticles`

## File structure for new/modified files

```
packages/api/src/
  main.py          # new /upload endpoint, refactored /analyze endpoints, /articles
  database.py      # updated schema, new PMID-based queries
  jobs.py          # new upload_and_process_pdf job function
  datalab.py       # NEW: Datalab API client (convert + poll)

packages/app/src/
  pages/Dashboard.tsx           # two buttons instead of one
  pages/Viewer.tsx              # pmid in route params
  components/AddArticleDialog.tsx  # refactored for PMID
  components/UploadPdfDialog.tsx   # NEW: PDF upload dialog
  hooks/useViewerData.ts        # fetch by PMID
  lib/api.ts                    # new/updated API methods
  App.tsx                       # route param change
```

## Error handling

- Datalab conversion failure: set job status to `failed` with error message from Datalab
- Invalid PDF: Datalab returns error, propagate to user
- PMID already exists: return 409 Conflict
- File too large: validate on frontend (reasonable limit, e.g. 50MB) and let Datalab enforce its 200MB limit
- Datalab polling timeout: fail after reasonable timeout (e.g., 5 minutes)
