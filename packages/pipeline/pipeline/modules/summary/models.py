"""Pydantic models for summary generation input/output validation."""

from pydantic import BaseModel

from generation.modules.sentence_generation.models import GeneratedSentence


class SentenceInput(BaseModel):
    """Validated input from a sentence generation output file."""

    generator: str
    run_name: str
    timestamp: str
    source_variants: str
    model: str
    prompt_version: str
    sentences: dict[str, dict[str, list[GeneratedSentence]]]
    # pmcid -> variant -> sentences


class CitationInput(BaseModel):
    """Validated input from a citations output file."""

    finder: str
    run_name: str
    timestamp: str
    source_sentences: str
    model: str
    prompt_version: str
    citations: dict[str, list[dict]]  # pmcid -> list of citation dicts


class ArticleSummary(BaseModel):
    """Summary for one article."""

    pmcid: str
    summary: str
    num_variants: int
    variants: list[str]


class SummaryGeneratorOutput(BaseModel):
    """Full output of a summary generation run."""

    generator: str
    run_name: str
    timestamp: str
    source_sentences: str
    source_citations: str | None
    model: str
    prompt_version: str
    summaries: list[ArticleSummary]
