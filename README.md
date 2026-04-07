# AutoGKB

Pharmacogenomics knowledge extraction pipeline, evaluation benchmark, and web application.

## Structure

| Package | Description | Location |
|---------|------------|----------|
| **pipeline** | Annotation generation pipeline (variants, sentences, citations, summaries) | `packages/pipeline/` |
| **benchmark** | Evaluation of pipeline output against ground truth | `packages/benchmark/` |
| **api** | FastAPI backend serving the pipeline | `packages/api/` |
| **app** | React/TypeScript frontend | `packages/app/` |
| **shared** | Common Python utilities (LLM calls, markdown parsing, term lookup) | `shared/` |

## Setup

### Python packages (pipeline, benchmark, api, shared)

```bash
# Install uv if you haven't
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all workspace packages in editable mode
uv sync

# Run the pipeline
uv run generate --pmcids PMC10275785

# Run the API
cd packages/api && uv run uvicorn src.main:app --reload --port 8001
```

### Frontend (app)

```bash
cd packages/app
npm install
npm run dev
```

### Environment

Copy `.env.example` to `.env` and fill in your API keys and database URL.
