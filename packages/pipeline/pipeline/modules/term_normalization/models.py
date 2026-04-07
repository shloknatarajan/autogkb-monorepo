from pydantic import BaseModel


class VariantMapping(BaseModel):
    original: str
    normalized: str
    pharmgkb_id: str | None = None
    score: float | None = None
    changed: bool  # True if normalization changed the term


class NormalizationResult(BaseModel):
    normalized_variants: list[str]
    mappings: list[VariantMapping]
