from annotation.models import (
    AnnotationResult,
    Entity,
    EvidenceQuote,
    GroundedAssociation,
    Statistic,
)
from annotation.postprocess import merge_duplicate_associations


def association(**updates):
    values = {
        "variants": [
            Entity(name="rs1057910", id="PA166153959", url=None, mentions=["CYP2C9*3"])
        ],
        "gene": "CYP2C9",
        "genotypes": ["AC"],
        "drugs": [Entity(name="warfarin", id="PA451906", url=None, mentions=[])],
        "outcome": "Stable weekly dose",
        "direction": "decreased",
        "comparator": "AA",
        "population": ["Thai patients"],
        "statistics": [Statistic(name="p", value="0.01")],
        "evidence": [EvidenceQuote(source_id="S0001", quote="evidence one")],
    }
    values.update(updates)
    return GroundedAssociation(**values)


def test_exact_duplicate_is_merged_without_losing_details():
    duplicate = association(
        variants=[
            Entity(name="rs1057910", id="PA166153959", url=None, mentions=["1075A>C"])
        ],
        genotypes=["CC"],
        statistics=[Statistic(name="beta", value="-9.25")],
        evidence=[EvidenceQuote(source_id="S0002", quote="evidence two")],
    )
    merged = merge_duplicate_associations(
        AnnotationResult(associations=[association(), duplicate])
    )
    assert len(merged.associations) == 1
    record = merged.associations[0]
    assert record.genotypes == ["AC", "CC"]
    assert record.variants[0].mentions == ["CYP2C9*3", "1075A>C"]
    assert len(record.statistics) == 2
    assert len(record.evidence) == 2


def test_distinct_direction_is_not_merged():
    increased = association(direction="increased")
    merged = merge_duplicate_associations(
        AnnotationResult(associations=[association(), increased])
    )
    assert len(merged.associations) == 2
