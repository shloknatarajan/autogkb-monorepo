"""Pydantic models for the JSONL generation output schema."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GenerationStatus(str, Enum):
    in_progress = "in_progress"
    completed = "completed"
    error = "error"


class GenerationMetadata(BaseModel):
    config_name: str
    variant_extraction_method: str
    sentence_generation_method: str | None = None
    sentence_model: str | None = None
    citation_model: str | None = None
    summary_model: str | None = None
    elapsed_seconds: float
    git_sha: str
    stages_run: list[str]


class GenerationRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    pmid: str | None = None
    pmcid: str
    title: str | None = None
    text_content: str
    annotations: dict  # {variant_name: [{sentence, explanation}]}
    annotation_citations: list[dict]
    annotation_data: dict[str, Any] | None = None  # raw pipeline result JSON
    status: GenerationStatus = GenerationStatus.completed
    error: str | None = None
    generation_metadata: GenerationMetadata
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
