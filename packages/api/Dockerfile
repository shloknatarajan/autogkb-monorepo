FROM python:3.12-slim

WORKDIR /app

# Install git (for pubmed-markdown git dep if needed) and uv
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Copy workspace packages needed by the API
COPY packages/shared/ ./packages/shared/
COPY packages/pipeline/ ./packages/pipeline/
COPY packages/api/ ./packages/api/

# Install packages in dependency order
RUN uv pip install --system --no-sources ./packages/shared ./packages/pipeline ./packages/api

# Create data directory for article cache
RUN mkdir -p /app/data/articles

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
