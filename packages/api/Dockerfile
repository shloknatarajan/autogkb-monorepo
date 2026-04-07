FROM python:3.14-slim

WORKDIR /app

# Install git (required for git+ dependencies) and uv
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Copy requirements and install
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

# Copy application
COPY src/ ./src/

RUN mkdir -p /app/data/articles

# Expose port
EXPOSE 8000

# Run the server
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
