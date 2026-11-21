"""Merging of records that state the same finding."""

import re
from collections.abc import Iterable

from annotation.methods.grounded_v2.entities import drug_key, variant_key
from annotation.methods.grounded_v2.models import AnnotationResult, Entity, GroundedAssociation


def _unique(items: Iterable, key=lambda item: item) -> list:
    seen = set()
    result = []
    for item in items:
        marker = key(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def _text_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _association_key(association: GroundedAssociation) -> tuple:
    return (
        tuple(sorted(variant_key(variant) for variant in association.variants)),
        tuple(sorted(drug_key(drug) for drug in association.drugs)),
        _text_key(association.outcome),
        association.direction,
        _text_key(association.comparator),
    )


def _merge_entities(current: list[Entity], incoming: list[Entity], key) -> list[Entity]:
    by_key = {key(entity): entity for entity in incoming}
    return [
        entity.model_copy(
            update={"mentions": _unique([*entity.mentions, *by_key[key(entity)].mentions])}
        )
        for entity in current
    ]


def merge_duplicate_associations(result: AnnotationResult) -> AnnotationResult:
    """Merge only records with the same normalized clinical claim."""
    merged: dict[tuple, GroundedAssociation] = {}
    for association in result.associations:
        key = _association_key(association)
        current = merged.get(key)
        if current is None:
            merged[key] = association
            continue
        merged[key] = current.model_copy(
            update={
                "variants": _merge_entities(
                    current.variants, association.variants, variant_key
                ),
                "drugs": _merge_entities(current.drugs, association.drugs, drug_key),
                "genotypes": _unique([*current.genotypes, *association.genotypes]),
                "population": _unique(
                    [*current.population, *association.population],
                    key=lambda value: value.casefold(),
                ),
                "statistics": _unique(
                    [*current.statistics, *association.statistics],
                    key=lambda item: (item.name.casefold(), item.value.casefold()),
                ),
                "evidence": _unique(
                    [*current.evidence, *association.evidence],
                    key=lambda item: (item.source_id, item.quote),
                ),
            }
        )
    return result.model_copy(update={"associations": list(merged.values())})
