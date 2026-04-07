"""Pydantic models for citation finding input/output validation."""

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


class Citation(BaseModel):
    """Citations found for one association."""

    variant: str
    sentence: str
    explanation: str = ""
    citations: list[str]


class CitationFinderOutput(BaseModel):
    """Full output of a citation finding run."""

    finder: str
    run_name: str
    timestamp: str
    source_sentences: str  # path to input sentences file
    model: str
    prompt_version: str
    citations: dict[str, list[Citation]]  # pmcid -> list of citations
