from annotation.models import (
    AnnotationResult,
    EvidenceQuote,
    GroundedAssociation,
    Statistic,
)
from annotation.postprocess import merge_duplicate_associations, normalize_variants


def association(**updates):
    values = {
        "variant": "VKORC1 −1639G>A",
        "variant_aliases": [],
        "variant_id": None,
        "variant_url": None,
        "gene": "VKORC1",
        "genotypes": ["GA"],
        "drug": "warfarin",
        "outcome": "stable weekly dose",
        "direction": "decreased",
        "comparator": "GG",
        "population": ["Thai patients"],
        "statistics": [Statistic(name="p", value="<0.001")],
        "evidence": [EvidenceQuote(source_id="S0001", quote="evidence one")],
        "confidence": "high",
    }
    values.update(updates)
    return GroundedAssociation(**values)


def result(*associations):
    return AnnotationResult(associations=list(associations), variant_mentions=[])


def test_shared_term_normalization_resolves_snp_shorthand():
    normalized = normalize_variants(
        result(association()),
        {"S0001": "VKORC1 −1639G>A was tested."},
        data_dir="data",
    )
    item = normalized.associations[0]
    assert item.variant == "rs9923231"
    assert item.variant_aliases == ["VKORC1 −1639G>A"]
    assert item.variant_id == "PA166155091"


def test_shared_term_normalization_combines_gene_with_bare_notation():
    bare = association(variant="−1639G > A", gene="VKORC1")
    normalized = normalize_variants(
        result(bare),
        {"S0001": "VKORC1 −1639G > A was tested."},
        data_dir="data",
    )
    assert normalized.associations[0].variant == "rs9923231"


def test_star_allele_uses_unambiguous_gene_rsid_co_mention():
    item = association(variant="CYP2C9*3", gene="CYP2C9")
    normalized = normalize_variants(
        result(item),
        {"S0001": "CYP2C9*3 (1075A>C; rs1057910) was genotyped."},
        data_dir="data",
    )
    assert normalized.associations[0].variant == "rs1057910"
    assert normalized.associations[0].variant_aliases == ["CYP2C9*3"]


def test_cdna_alias_uses_same_line_rsid_co_mention():
    item = association(variant="c.521T>C", gene="SLCO1B1")
    normalized = normalize_variants(
        result(item),
        {"S0001": "| SLCO1B1 | rs4149056 | c.521T>C | p.Val174Ala |"},
        data_dir="data",
    )
    assert normalized.associations[0].variant == "rs4149056"
    assert normalized.associations[0].variant_aliases == ["c.521T>C"]


def test_duplicate_claims_merge_genotypes_statistics_and_evidence():
    second = association(
        genotypes=["AA"],
        statistics=[Statistic(name="beta", value="-18.1")],
        evidence=[EvidenceQuote(source_id="S0002", quote="evidence two")],
    )
    merged = merge_duplicate_associations(result(association(), second))
    assert len(merged.associations) == 1
    assert merged.associations[0].genotypes == ["GA", "AA"]
    assert len(merged.associations[0].statistics) == 2
    assert len(merged.associations[0].evidence) == 2


def test_distinct_direction_is_not_merged():
    increased = association(direction="increased")
    merged = merge_duplicate_associations(result(association(), increased))
    assert len(merged.associations) == 2
