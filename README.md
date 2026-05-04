# AutoGKB

End-to-end pharmacogenomics (PGx) knowledge extraction: pipeline, evaluation, API, and web app — all in one monorepo.

AutoGKB ingests PubMed/PMC articles, extracts genetic variants and PGx associations, grounds them with citations, and summarizes results. Outputs are stored as JSONL and human-readable markdown for inspection and downstream use.

## Monorepo Structure

- **`packages/pipeline`**: End-to-end annotation generation (variants → normalization → sentences → citations → summary)
- **`packages/benchmark`**: Evaluation utilities and scripts for comparing outputs to ground truth
- **`packages/api`**: FastAPI backend for variant extraction and full analysis jobs
- **`packages/app`**: React + Vite frontend for browsing articles and annotations
- **`shared`**: Shared Python utilities (LLM calls, markdown/text helpers, term lookup)
- **`data`**: Local data cache (articles, generations, benchmarks, etc.)

The Python workspace is managed with `uv` and configured in the root `pyproject.toml`.

## Quick Start

### 1) Configure environment

Copy `.env.example` to `.env` and set the relevant keys.

- `NCBI_EMAIL`: Required for NCBI tools courtesy policy when downloading/looking up PMC content
- One of: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` for LLM steps
- `PIPELINE_MODEL`: Default LLM (e.g., `gpt-4o`) for API background jobs
- Optional DB: `DATABASE_URL` for API job persistence (PostgreSQL)
- Frontend dev: `VITE_API_URL` to point the app at your API

### 2) Install Python workspace

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# From repo root, install all workspace packages
uv sync
```

### 3) Run the pipeline (CLI)

The pipeline persists results to `data/generations.jsonl` and writes per-article markdown to `data/generations/`.

Examples:

```bash
# Process a specific PMCID
uv run generate --pmcids PMC10275785

# Process one or more PMID(s) — resolves to PMCID(s) and auto-downloads markdown
uv run generate --pmid 32948745 38234567

# Run a subset of stages
uv run generate --pmcids PMC10275785 --stages variants,sentences

# Re-run for all PMCIDs already present in data/generations.jsonl (pulls latest from DB if configured)
uv run generate --regenerate-all

# Use a custom config
uv run generate --config packages/pipeline/pipeline/configs/base_config.yaml
```

Key options (see `packages/pipeline/pipeline/pipeline.py`):

- `--pmid ...` or `--pmcids ...`: Choose inputs
- `--stages variants,term_normalization,sentences,citations,summary`: Select stages
- `--variants-file path/to/variants.json`: Reuse variants from a prior run
- `--config path/to/config.yaml`: Override methods/models per stage

Outputs (by default):

- `data/generations.jsonl`: One GenerationRecord per line with metadata and annotations
- `data/generations/<timestamp>_<PMCID>.md`: Human-readable annotation markdown
- `data/articles/<PMCID>.md`: Cached article markdown from PMC

Maintenance:

```bash
# Inspect and (optionally) clean incomplete/invalid records in generations.jsonl
uv run clean-generations            # dry-run
uv run clean-generations --apply    # rewrite file
```

### 4) Run the API (FastAPI)

The API provides:
- Variant extraction endpoints for raw text, PMID, or PMCID
- Background analysis jobs that run the full sentence/citation/summary flow and persist results

Run locally:

```bash
cd packages/api
uv run uvicorn src.main:app --reload --port 8001
```

Environment notes:
- Without `DATABASE_URL`, endpoints relying on persistence will error; variant extraction endpoints still work.
- Background jobs use `PIPELINE_MODEL` (default `gpt-4o`) and the LLM key(s) you provide.

Selected endpoints:

- `GET /health`: Health check
- `POST /variant-extract/text` → `{ text, include_metadata? }`
- `POST /variant-extract/pmid` → `{ pmid, include_supplements? }`
- `POST /variant-extract/pmcid` → `{ pmcid, include_supplements? }`
- `POST /analyze` → `{ pmcid, force? }` submits a background job
- `POST /analyze/pmid` → `{ pmid, article_text?, force? }` with PMID→PMCID fallback
- `GET /jobs/pmcid/{pmcid}` and `GET /jobs/pmid/{pmid}`: Fetch latest job
- `GET /jobs/{job_id}/stream`: Server‑sent events stream of job status
- `GET /pmcids`: List analyzed PMCIDs with titles and summaries

Docker (Railway): `packages/api/Dockerfile` builds the API with uv; `railway.json` holds default deploy settings.

### 5) Frontend (React app)

```bash
cd packages/app
npm install
npm run dev
```

- Open `http://localhost:5173`
- Set `VITE_API_URL` in `.env` to point the app at your running API
- The app UI lets you browse articles and view the markdown + structured annotations

### 6) Benchmark and Evaluation

Ground-truth data and helper scripts live under `packages/benchmark` and `data/benchmark_v2/`.

Evaluate a pipeline run directory containing variants/sentences/citations/summaries:

```bash
uv run python -m benchmark.eval.eval_pipeline \
  --input path/to/run_dir \
  --stages variants,sentences,citations,summary \
  --judge-model claude-sonnet-4-20250514   # optional override
```

The evaluator writes an aggregate JSON to `<run_dir>/eval_results/aggregate.json` and logs a short summary.

## Configuration and Methods

Pipeline configuration lives in YAML (see `packages/pipeline/pipeline/configs/base_config.yaml`). Each stage selects a method and, when applicable, a model and prompt version. Highlights:

- Variant extraction methods: `regex_v1`…`regex_v5`, `regex_llm_filter`, `regex_term_norm`, `pubtator`, `pgxmine`, `just_ask`
- Sentence generation: `raw_sentence_ask`, `batch_judge_ask`, `llm_judge_ask`
- Citation finding: `one_shot_citations`
- Summary generation: `basic_summary`

Term normalization (enabled by default) can be tuned via thresholds and top‑k; see `term_normalization` in the config.

## Data Layout

- `data/articles/`: Cached PMC markdown (auto-fetched on demand)
- `data/generations.jsonl`: Append‑only JSONL of GenerationRecord objects
- `data/generations/`: Per‑article rendered markdown snapshots
- `data/benchmark_v2/*.jsonl`: Benchmark datasets (variants, sentences, summaries)
- `data/term_lookup_info/`: TSV/JSON caches for term lookup and SNP notation expansion

## Requirements

- Python 3.11+
- `uv` for workspace management
- Node 18+ for the frontend
- PostgreSQL (optional, for API job persistence)

## Tips and Troubleshooting

- If article markdown is missing, the pipeline/API will attempt to download it automatically via `pubmed_markdown`.
- For PMID inputs, the pipeline resolves to PMCIDs and caches a `data/pmcid_mapping.json` file.
- LLM calls are routed via `litellm`; model names can be specified with or without provider prefixes -- see `packages/shared/shared/utils.py` for normalization logic.
- Use `uv run clean-generations --apply` if `data/generations.jsonl` accumulates invalid/incomplete lines.

## Development

Workspace management is via `uv` with all packages declared in the root `pyproject.toml`.

- Lint: `uv run ruff check .`
- Tests: `uv run pytest` (where present)
- Formatting: project follows existing styles; avoid unrelated refactors

## License

This repository is for research use. See individual package metadata for additional details.
