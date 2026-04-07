"""Pydantic models for sentence generation input/output validation."""

from pydantic import BaseModel


class VariantInput(BaseModel):
    """Validated input from a variant finding output file."""

    extractor: str
    run_name: str
    timestamp: str
    variants: dict[str, list[str]]  # pmcid -> variant list


class GeneratedSentence(BaseModel):
    """A single generated sentence, optionally with explanation."""

    sentence: str
    explanation: str = ""


class SentenceGenerationOutput(BaseModel):
    """Full output of a sentence generation run."""

    generator: str
    run_name: str
    timestamp: str
    source_variants: str  # path to the input variants file
    model: str
    prompt_version: str
    sentences: dict[str, dict[str, list[GeneratedSentence]]]
    # pmcid -> variant -> sentences
