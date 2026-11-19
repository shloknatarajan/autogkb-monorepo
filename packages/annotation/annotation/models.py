"""Strict output contract for evidence-grounded annotations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceQuote(StrictModel):
    source_id: str
    quote: str


class Statistic(StrictModel):
    name: str
    value: str


class GroundedAssociation(StrictModel):
    variant: str
    variant_aliases: list[str]
    variant_id: str | None
    variant_url: str | None
    gene: str | None
    genotypes: list[str]
    drug: str | None
    outcome: str
    direction: Literal["increased", "decreased", "no_association", "other"]
    comparator: str | None
    population: list[str]
    statistics: list[Statistic]
    evidence: list[EvidenceQuote]
    confidence: Literal["high", "medium", "low"]

    def render_sentence(self) -> str:
        """Render display prose from atomic facts."""
        if self.genotypes:
            noun = "Genotype" if len(self.genotypes) == 1 else "Genotypes"
            subject = f"{noun} {' + '.join(self.genotypes)} of {self.variant}"
            verb = "is" if len(self.genotypes) == 1 else "are"
        else:
            subject = self.variant
            verb = "is"

        if self.direction == "no_association":
            predicate = f"{verb} not associated with {self.outcome}"
        elif self.direction == "other":
            predicate = f"{verb} associated with {self.outcome}"
        else:
            predicate = f"{verb} associated with {self.direction} {self.outcome}"

        parts = [subject, predicate]
        if self.drug:
            parts.append(f"with {self.drug}")
        if self.population:
            parts.append(f"in people with {', '.join(self.population)}")
        if self.comparator:
            parts.append(f"compared with {self.comparator}")
        sentence = " ".join(parts).strip()
        return sentence[0].upper() + sentence[1:] + "."


class VariantMention(StrictModel):
    variant: str
    status: Literal["studied", "background_only", "unclear", "false_positive"]
    reason: str
    evidence: list[EvidenceQuote]


class AnnotationResult(StrictModel):
    associations: list[GroundedAssociation]
    variant_mentions: list[VariantMention]
