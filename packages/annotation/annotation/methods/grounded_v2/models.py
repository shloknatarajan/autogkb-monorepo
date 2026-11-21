"""Strict contracts for the LLM response and the stored annotations."""

from typing import Literal

from annotation.base import StrictModel

Direction = Literal["increased", "decreased", "no_association", "other"]


class EvidenceQuote(StrictModel):
    source_id: str
    quote: str


class Statistic(StrictModel):
    name: str
    value: str


class ExtractedAssociation(StrictModel):
    """One finding as the model returns it.

    ``variants`` and ``drugs`` hold entity-table references (``V3``, ``D1``) or,
    for entities the table does not list, the article's own wording.
    """

    variants: list[str]
    gene: str | None
    genotypes: list[str]
    drugs: list[str]
    outcome: str
    direction: Direction
    comparator: str | None
    population: list[str]
    statistics: list[Statistic]
    evidence: list[EvidenceQuote]


class ExtractionResponse(StrictModel):
    associations: list[ExtractedAssociation]


class Entity(StrictModel):
    """A variant or drug; ``id`` is null when no lookup record matched."""

    name: str
    id: str | None
    url: str | None
    mentions: list[str]


class GroundedAssociation(StrictModel):
    variants: list[Entity]
    gene: str | None
    genotypes: list[str]
    drugs: list[Entity]
    outcome: str
    direction: Direction
    comparator: str | None
    population: list[str]
    statistics: list[Statistic]
    evidence: list[EvidenceQuote]

    def render_sentence(self) -> str:
        """Render display prose from atomic facts."""
        names = ", ".join(variant.name for variant in self.variants)
        if self.genotypes:
            noun = "Genotype" if len(self.genotypes) == 1 else "Genotypes"
            subject = f"{noun} {' + '.join(self.genotypes)} of {names}"
            verb = "is" if len(self.genotypes) == 1 else "are"
        else:
            subject = names
            verb = "is" if len(self.variants) == 1 else "are"

        if self.direction == "no_association":
            predicate = f"{verb} not associated with {self.outcome}"
        elif self.direction == "other":
            predicate = f"{verb} associated with {self.outcome}"
        else:
            predicate = f"{verb} associated with {self.direction} {self.outcome}"

        parts = [subject, predicate]
        if self.drugs:
            names = ", ".join(drug.name for drug in self.drugs)
            parts.append(f"when treated with {names}")
        if self.population:
            parts.append(f"in {', '.join(self.population)}")
        if self.comparator:
            parts.append(f"compared with {self.comparator}")
        sentence = " ".join(parts).strip()
        return sentence[0].upper() + sentence[1:] + "."


class AnnotationResult(StrictModel):
    associations: list[GroundedAssociation]
