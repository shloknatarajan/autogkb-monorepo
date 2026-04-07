# AutoGKB Monorepo Migration (Phase 1 + 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Combine three separate repos (autogkb-api, autogkb-app, autogkb-benchmark) into a single monorepo with clean package boundaries, UV workspace for Python dependency management, and proper local path dependencies.

**Architecture:** The monorepo has 4 Python packages (`packages/pipeline`, `packages/benchmark`, `packages/api`, `shared/`) linked via UV workspace, plus 1 JS app (`packages/app/`). `data/` lives at the root. The API and pipeline packages share code through workspace-local dependencies instead of git URL installs.

**Tech Stack:** UV (Python package management + workspaces), npm (JS), FastAPI, React/Vite/TypeScript, Pydantic, litellm

---

## File Structure

```
autogkb-monorepo/
├── pyproject.toml                    # Root: UV workspace definition only
├── .python-version                   # Pin Python version for UV
├── .gitignore                        # Combined gitignore
├── .env.example                      # Documented env vars
├── README.md                         # Monorepo overview
│
├── packages/
│   ├── pipeline/                     # Was: autogkb-benchmark/generation/
│   │   ├── pyproject.toml            # Installable package, depends on shared
│   │   └── pipeline/                 # Python package (renamed from generation/)
│   │       ├── __init__.py
│   │       ├── __main__.py
│   │       ├── pipeline.py
│   │       ├── models.py
│   │       ├── clean.py
│   │       ├── sync.py
│   │       ├── configs/
│   │       │   └── base_config.yaml
│   │       └── modules/
│   │           ├── utils.py
│   │           ├── utils_bioc.py
│   │           ├── variant_finding/
│   │           ├── term_normalization/
│   │           ├── sentence_generation/
│   │           ├── citations/
│   │           └── summary/
│   │
│   ├── benchmark/                    # Was: autogkb-benchmark/benchmark/
│   │   ├── pyproject.toml            # Depends on pipeline + shared
│   │   └── benchmark/               # Python package
│   │       ├── __init__.py
│   │       ├── v1/
│   │       ├── v2/
│   │       └── eval/
│   │
│   ├── api/                          # Was: autogkb-api/
│   │   ├── pyproject.toml            # Depends on pipeline + shared
│   │   ├── Dockerfile
│   │   ├── railway.json
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── database.py
│   │       └── jobs.py
│   │
│   └── app/                          # Was: autogkb-app/
│       ├── package.json
│       ├── tsconfig.json
│       ├── tsconfig.app.json
│       ├── tsconfig.node.json
│       ├── vite.config.ts
│       ├── tailwind.config.ts
│       ├── postcss.config.js
│       ├── eslint.config.js
│       ├── components.json
│       ├── vercel.json
│       ├── index.html
│       ├── public/
│       └── src/
│
├── shared/                           # Was: autogkb-benchmark/shared/
│   ├── pyproject.toml                # Installable package
│   └── shared/
│       ├── __init__.py
│       ├── utils.py
│       ├── data_setup/
│       └── term_normalization/
│
└── data/                             # Was: autogkb-benchmark/data/
    ├── articles/
    ├── annotations/
    ├── benchmark_v2/
    ├── generations/
    ├── generations.jsonl
    └── ...
```

---

## Task 1: Copy source repos into monorepo directory structure

**Files:**
- Create: `packages/pipeline/`, `packages/benchmark/`, `packages/api/`, `packages/app/`, `shared/`, `data/`

This task copies files without modifying any code. We preserve the original code to get a baseline that we'll fix imports on in subsequent tasks.

- [ ] **Step 1: Copy autogkb-benchmark/generation/ into packages/pipeline/pipeline/**

The `generation/` directory becomes the `pipeline` package. We rename the inner directory from `generation` to `pipeline` to match the new package name.

```bash
cd /Users/shloknatarajan/stanford/research/daneshjou/AutoGKB-Repos/prod/autogkb-monorepo

# Copy generation/ -> packages/pipeline/pipeline/
mkdir -p packages/pipeline
cp -R ../autogkb-benchmark/generation packages/pipeline/pipeline
```

- [ ] **Step 2: Copy autogkb-benchmark/benchmark/ into packages/benchmark/benchmark/**

```bash
mkdir -p packages/benchmark
cp -R ../autogkb-benchmark/benchmark packages/benchmark/benchmark
```

- [ ] **Step 3: Copy autogkb-benchmark/shared/ into shared/shared/**

```bash
mkdir -p shared
cp -R ../autogkb-benchmark/shared shared/shared
```

- [ ] **Step 4: Copy autogkb-benchmark/data/ into data/**

```bash
cp -R ../autogkb-benchmark/data data
```

- [ ] **Step 5: Copy autogkb-api/src/ into packages/api/src/ and supporting files**

```bash
mkdir -p packages/api
cp -R ../autogkb-api/src packages/api/src
cp ../autogkb-api/Dockerfile packages/api/Dockerfile
cp ../autogkb-api/railway.json packages/api/railway.json
```

- [ ] **Step 6: Copy autogkb-app/ into packages/app/ (excluding .git, node_modules, .pixi, dist, python_src, benchmarks)**

```bash
mkdir -p packages/app
rsync -a \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='.pixi' \
  --exclude='dist' \
  --exclude='python_src' \
  --exclude='benchmarks' \
  --exclude='data' \
  --exclude='notes' \
  --exclude='.claude' \
  --exclude='.env' \
  --exclude='pixi.toml' \
  --exclude='pixi.lock' \
  --exclude='bun.lockb' \
  ../autogkb-app/ packages/app/
```

- [ ] **Step 7: Remove __pycache__ directories from all copied files**

```bash
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
```

- [ ] **Step 8: Commit baseline copy**

```bash
git add -A
git commit -m "chore: copy source repos into monorepo directory structure

Copies autogkb-api, autogkb-app, and autogkb-benchmark into the target
monorepo layout without modifying any code:
- autogkb-benchmark/generation/ -> packages/pipeline/pipeline/
- autogkb-benchmark/benchmark/ -> packages/benchmark/benchmark/
- autogkb-benchmark/shared/ -> shared/shared/
- autogkb-benchmark/data/ -> data/
- autogkb-api/ -> packages/api/
- autogkb-app/ -> packages/app/"
```

---

## Task 2: Create pyproject.toml files for each Python package + UV workspace

**Files:**
- Create: `pyproject.toml` (root)
- Create: `.python-version`
- Create: `packages/pipeline/pyproject.toml`
- Create: `packages/benchmark/pyproject.toml`
- Create: `packages/api/pyproject.toml`
- Create: `shared/pyproject.toml`

- [ ] **Step 1: Create root pyproject.toml with UV workspace definition**

```toml
# pyproject.toml (root of monorepo)
[project]
name = "autogkb"
version = "0.1.0"
description = "AutoGKB monorepo — pharmacogenomics knowledge extraction"
requires-python = ">=3.11"

[tool.uv.workspace]
members = ["packages/pipeline", "packages/benchmark", "packages/api", "shared"]

[tool.uv]
dev-dependencies = [
    "ruff>=0.14.0",
    "pytest>=8.0.0",
]
```

- [ ] **Step 2: Create .python-version**

```
3.12
```

- [ ] **Step 3: Create shared/pyproject.toml**

This is the leaf dependency — no internal deps.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "autogkb-shared"
version = "0.1.0"
description = "Shared utilities for AutoGKB packages"
requires-python = ">=3.11"
dependencies = [
    "litellm>=1.72.0",
    "loguru>=0.7.0",
    "requests>=2.32.0",
    "pydantic>=2.0.0",
    "python-dotenv>=1.0.0",
    "tqdm>=4.67.0",
    "pubmed-markdown>=0.2.5",
]

[tool.hatch.build.targets.wheel]
packages = ["shared"]
```

- [ ] **Step 4: Create packages/pipeline/pyproject.toml**

Depends on `autogkb-shared`. The package is named `pipeline` internally but the distribution is `autogkb-pipeline`.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "autogkb-pipeline"
version = "0.1.0"
description = "AutoGKB annotation generation pipeline"
requires-python = ">=3.11"
dependencies = [
    "autogkb-shared",
    "pandas>=2.2.0",
    "pyyaml>=6.0",
    "biopython>=1.85",
    "beautifulsoup4>=4.13.0",
    "psycopg2-binary>=2.9.0",
    "sqlalchemy>=2.0.0",
]

[project.scripts]
generate = "pipeline.pipeline:main"
clean-generations = "pipeline.clean:main"

[tool.hatch.build.targets.wheel]
packages = ["pipeline"]
```

- [ ] **Step 5: Create packages/benchmark/pyproject.toml**

Depends on `autogkb-pipeline` (which transitively brings in `autogkb-shared`).

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "autogkb-benchmark"
version = "0.1.0"
description = "AutoGKB benchmark evaluation"
requires-python = ">=3.11"
dependencies = [
    "autogkb-pipeline",
    "autogkb-shared",
    "scikit-learn>=1.3.0",
    "polars>=1.37.0",
    "datasets>=3.6.0",
    "seaborn>=0.13.0",
    "numpy>=2.2.0",
    "termcolor>=3.1.0",
    "sentence-transformers>=3.0.0",
]

[tool.hatch.build.targets.wheel]
packages = ["benchmark"]
```

- [ ] **Step 6: Create packages/api/pyproject.toml**

Depends on `autogkb-pipeline` and `autogkb-shared`.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "autogkb-api"
version = "1.0.0"
description = "AutoGKB FastAPI backend"
requires-python = ">=3.11"
dependencies = [
    "autogkb-pipeline",
    "autogkb-shared",
    "fastapi>=0.100.0",
    "uvicorn>=0.23.0",
    "psycopg2-binary>=2.9.0",
]

[project.scripts]
api-dev = "uvicorn src.main:app --reload --port 8001"

[tool.hatch.build.targets.wheel]
packages = ["src"]
```

- [ ] **Step 7: Commit pyproject files**

```bash
git add pyproject.toml .python-version packages/*/pyproject.toml shared/pyproject.toml
git commit -m "chore: add pyproject.toml files and UV workspace config

Root workspace links: packages/pipeline, packages/benchmark, packages/api, shared.
Each package declares its own dependencies. Internal packages reference
each other by name — UV resolves them locally via the workspace."
```

---

## Task 3: Update shared/ imports — fix ROOT and DATA_DIR paths

**Files:**
- Modify: `shared/shared/utils.py`

The critical change: `ROOT` currently points one level up from `shared/utils.py` (the old benchmark repo root). In the monorepo, it needs to point to the monorepo root so `DATA_DIR = ROOT / "data"` still resolves correctly.

- [ ] **Step 1: Update ROOT calculation in shared/shared/utils.py**

Old code (line 9):
```python
ROOT = Path(__file__).resolve().parents[1]
```

New code — go up from `shared/shared/utils.py` to monorepo root (2 levels: `shared/` -> `shared/` -> monorepo root):
```python
ROOT = Path(__file__).resolve().parents[2]
```

The path is: `<monorepo>/shared/shared/utils.py` — `.parents[0]` = `shared/shared/`, `.parents[1]` = `shared/`, `.parents[2]` = monorepo root.

- [ ] **Step 2: Commit**

```bash
git add shared/shared/utils.py
git commit -m "fix: update ROOT path in shared/utils.py for monorepo layout

ROOT now resolves to the monorepo root (2 parents up from shared/shared/utils.py)
so that DATA_DIR = ROOT / 'data' correctly points to the top-level data/ directory."
```

---

## Task 4: Update pipeline package imports (generation -> pipeline, shared -> shared)

**Files:**
- Modify: All Python files under `packages/pipeline/pipeline/`

Every file in the old `generation/` package uses `from generation.X import Y` and `from shared.X import Y`. Now the package is named `pipeline`, so all `generation.` imports become `pipeline.` imports. `shared.` imports stay the same (the package is still named `shared`).

- [ ] **Step 1: Rename all `generation.` imports to `pipeline.` in the pipeline package**

Run a bulk find-and-replace across all `.py` files in `packages/pipeline/pipeline/`:

```bash
cd /Users/shloknatarajan/stanford/research/daneshjou/AutoGKB-Repos/prod/autogkb-monorepo

# Replace 'from generation.' with 'from pipeline.' in all pipeline .py files
find packages/pipeline/pipeline -name '*.py' -exec sed -i '' 's/from generation\./from pipeline./g' {} +
find packages/pipeline/pipeline -name '*.py' -exec sed -i '' 's/import generation\./import pipeline./g' {} +
```

- [ ] **Step 2: Update the __init__.py docstring**

In `packages/pipeline/pipeline/__init__.py`, update the docstring:

Old:
```python
"""Generation pipeline for pharmacogenomics knowledge extraction."""
```

New:
```python
"""AutoGKB annotation generation pipeline."""
```

- [ ] **Step 3: Update __main__.py**

In `packages/pipeline/pipeline/__main__.py`:

Old:
```python
from generation.pipeline import main
```

New:
```python
from pipeline.pipeline import main
```

- [ ] **Step 4: Update pipeline.py path constants**

In `packages/pipeline/pipeline/pipeline.py`, the `PIPELINE_DIR` and `CONFIGS_DIR` are relative to `__file__` so they still work. But `ROOT` and `DATA_DIR` are imported from `shared.utils` — verify these imports use `shared.utils`:

```python
from shared.utils import ROOT, DATA_DIR, get_markdown_text
```

This import is already correct (shared package name didn't change). No change needed.

- [ ] **Step 5: Verify no remaining `generation.` references in pipeline package**

```bash
grep -r "from generation\." packages/pipeline/pipeline/ --include="*.py"
grep -r "import generation\." packages/pipeline/pipeline/ --include="*.py"
# Should return no results
```

- [ ] **Step 6: Commit**

```bash
git add packages/pipeline/
git commit -m "refactor: rename generation -> pipeline in all pipeline package imports

Bulk rename from 'from generation.X' to 'from pipeline.X' across all
Python files in the pipeline package to match the new package name."
```

---

## Task 5: Update benchmark package imports

**Files:**
- Modify: All Python files under `packages/benchmark/benchmark/`

Benchmark code imports from both `generation` (now `pipeline`) and `shared`. Update all `generation.` references to `pipeline.`.

- [ ] **Step 1: Rename all `generation.` imports to `pipeline.` in benchmark package**

```bash
find packages/benchmark/benchmark -name '*.py' -exec sed -i '' 's/from generation\./from pipeline./g' {} +
find packages/benchmark/benchmark -name '*.py' -exec sed -i '' 's/import generation\./import pipeline./g' {} +
```

- [ ] **Step 2: Verify no remaining `generation.` references**

```bash
grep -r "from generation\." packages/benchmark/benchmark/ --include="*.py"
grep -r "import generation\." packages/benchmark/benchmark/ --include="*.py"
# Should return no results
```

- [ ] **Step 3: Commit**

```bash
git add packages/benchmark/
git commit -m "refactor: update benchmark imports from generation -> pipeline"
```

---

## Task 6: Update API package imports

**Files:**
- Modify: `packages/api/src/main.py`
- Modify: `packages/api/src/jobs.py`

The API imports from `generation.modules.*` and `shared.utils`. Change `generation.` to `pipeline.`.

- [ ] **Step 1: Update imports in packages/api/src/main.py**

Old (line 22):
```python
from generation.modules.variant_finding.utils import extract_all_variants, get_variant_types
```

New:
```python
from pipeline.modules.variant_finding.utils import extract_all_variants, get_variant_types
```

- [ ] **Step 2: Update imports in packages/api/src/jobs.py**

Old (lines 20-24):
```python
from shared.utils import ROOT as PIPELINE_ROOT
from generation.modules.variant_finding.utils import extract_all_variants
from generation.modules.sentence_generation.sentence_generator import SentenceGenerator
from generation.modules.citations.citation_finder import CitationFinder
from generation.modules.summary.summary_generator import SummaryGenerator
```

New:
```python
from shared.utils import ROOT as PIPELINE_ROOT
from pipeline.modules.variant_finding.utils import extract_all_variants
from pipeline.modules.sentence_generation.sentence_generator import SentenceGenerator
from pipeline.modules.citations.citation_finder import CitationFinder
from pipeline.modules.summary.summary_generator import SummaryGenerator
```

- [ ] **Step 3: Verify no remaining generation imports in api**

```bash
grep -r "from generation\." packages/api/ --include="*.py"
# Should return no results
```

- [ ] **Step 4: Commit**

```bash
git add packages/api/
git commit -m "refactor: update API imports from generation -> pipeline"
```

---

## Task 7: Update the API Dockerfile for monorepo layout

**Files:**
- Modify: `packages/api/Dockerfile`

The Dockerfile needs to install the workspace packages. Since Railway builds from the repo root (or we set the root directory), the Dockerfile must copy the relevant workspace packages and install them.

- [ ] **Step 1: Rewrite packages/api/Dockerfile**

The Dockerfile context will be the monorepo root (Railway's root directory set to `.`, with Dockerfile path `packages/api/Dockerfile`). This lets us COPY shared/, packages/pipeline/, and packages/api/ into the image.

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install git (for pubmed-markdown git dep if needed) and uv
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Copy workspace packages needed by the API
COPY shared/ ./shared/
COPY packages/pipeline/ ./packages/pipeline/
COPY packages/api/ ./packages/api/

# Install packages in dependency order
RUN uv pip install --system ./shared ./packages/pipeline ./packages/api

# Create data directory for article cache
RUN mkdir -p /app/data/articles

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Update packages/api/railway.json if needed**

The current `railway.json` references Dockerfile in the same directory. With the monorepo, Railway needs to know the Dockerfile path relative to the root. Update:

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {
    "dockerfilePath": "packages/api/Dockerfile"
  },
  "deploy": {
    "numReplicas": 1,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

Move this file to the repo root so Railway finds it:

```bash
mv packages/api/railway.json ./railway.json
```

- [ ] **Step 3: Commit**

```bash
git add packages/api/Dockerfile railway.json
git commit -m "fix: update Dockerfile and railway.json for monorepo layout

Dockerfile now copies shared/, packages/pipeline/, and packages/api/
from the monorepo root context and installs them with uv pip.
railway.json moved to repo root with updated dockerfilePath."
```

---

## Task 8: Create root .gitignore and .env.example

**Files:**
- Create: `.gitignore`
- Create: `.env.example`

- [ ] **Step 1: Create .gitignore**

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
*.pyd
*.egg-info/
dist/
build/
.eggs/

# Environments
.env
.envrc
.venv/
.pixi/
.ruff_cache/

# OS
.DS_Store
__MACOSX/

# Node
node_modules/
packages/app/dist/

# Data backups
data/backups/

# IDE
.idea/
.vscode/
*.swp

# UV
uv.lock

# Images (from benchmark)
*.png
```

- [ ] **Step 2: Create .env.example**

```bash
# PubMed API
NCBI_EMAIL=your-email@example.com

# LLM API keys (at least one required for pipeline)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-proj-...
GEMINI_API_KEY=AIzaSy...

# Pipeline model (default: gpt-4o)
PIPELINE_MODEL=gpt-4o

# PostgreSQL (Railway)
DATABASE_URL=postgresql://user:pass@host:port/dbname
DATABASE_PUBLIC_URL=postgresql://user:pass@host:port/dbname

# Frontend API URL
VITE_API_URL=https://autogkb-api.up.railway.app
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore .env.example
git commit -m "chore: add root .gitignore and .env.example

Single source of truth for environment variables across all packages."
```

---

## Task 9: Verify UV workspace resolves and packages import correctly

- [ ] **Step 1: Run uv sync from the monorepo root**

```bash
cd /Users/shloknatarajan/stanford/research/daneshjou/AutoGKB-Repos/prod/autogkb-monorepo
uv sync
```

This should resolve all workspace members and install them in editable mode. Fix any dependency resolution errors.

- [ ] **Step 2: Verify imports work**

```bash
uv run python -c "from shared.utils import ROOT, DATA_DIR; print(f'ROOT={ROOT}'); print(f'DATA_DIR={DATA_DIR}')"
uv run python -c "from pipeline.models import GenerationRecord; print('pipeline OK')"
uv run python -c "from pipeline.modules.variant_finding.utils import extract_all_variants; print('pipeline modules OK')"
```

- [ ] **Step 3: Verify API imports work**

```bash
uv run python -c "from src.main import app; print('API app OK')"
```

Note: This may fail if psycopg2 can't connect to DB — that's fine. We just need the import to succeed.

- [ ] **Step 4: Fix any import errors discovered in steps 2-3**

Address any remaining import issues. Common problems:
- Relative vs absolute imports within subpackages
- Missing `__init__.py` files
- Path resolution issues in modules that use `Path(__file__)`

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve import issues discovered during UV workspace verification"
```

---

## Task 10: Create a root README.md

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README.md**

```markdown
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
uv run uvicorn src.main:app --reload --port 8001
```

### Frontend (app)

```bash
cd packages/app
npm install
npm run dev
```

### Environment

Copy `.env.example` to `.env` and fill in your API keys and database URL.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add monorepo README with setup instructions"
```
