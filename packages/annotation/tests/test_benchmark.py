from annotation.benchmark import (
    _ground_truth_direction,
    association_matches,
    evaluate_article,
    load_generated,
    load_ground_truth,
)
from annotation.entities import EntityResolver


def record(variants, drugs, direction):
    return {"variants": set(variants), "drugs": set(drugs), "direction": direction}


def test_association_match_uses_variant_drug_and_direction():
    expected = record(["rs123"], ["warfarin"], "decreased")
    assert association_matches(expected, record(["rs123"], ["warfarin"], "decreased"))
    assert not association_matches(
        expected, record(["rs123"], ["warfarin"], "increased")
    )
    assert not association_matches(
        expected, record(["rs123"], ["clopidogrel"], "decreased")
    )


def test_association_match_needs_one_shared_allele_and_drug():
    expected = record(["cyp2d6*1", "cyp2d6*4"], ["a", "b"], None)
    assert association_matches(expected, record(["cyp2d6*4"], ["b"], "other"))
    assert not association_matches(expected, record(["cyp2d6*10"], ["b"], "other"))


def test_evaluate_article_reports_rough_metrics():
    expected = [
        record(["rs1"], ["drug-a"], "increased"),
        record(["rs2"], ["drug-b"], None),
    ]
    generated = [
        record(["rs1"], ["drug-a"], "increased"),
        record(["rs3"], ["drug-c"], "decreased"),
    ]
    result = evaluate_article(expected, generated)
    assert result["matched_associations"] == 1
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["variant_recall"] == 0.5


def test_reference_and_generated_records_share_canonical_keys(tmp_path):
    resolver = EntityResolver()
    reference = tmp_path / "reference.json"
    reference.write_text(
        '{"var_drug_ann": [{"Variant/Haplotypes": "rs9923231", "Drug(s)": "warfarin",'
        ' "Direction of effect": "decreased"},'
        ' {"Variant/Haplotypes": "CYP2C9*1, CYP2C9*3", "Drug(s)": "warfarin"}]}',
        encoding="utf-8",
    )
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        '{"associations": [{"variant": "VKORC1 -1639G>A", "variant_aliases": [],'
        ' "drug": "Coumadin", "direction": "decreased"}]}',
        encoding="utf-8",
    )
    current = tmp_path / "current.json"
    current.write_text(
        '{"associations": [{"variants": [{"name": "CYP2C9*3", "id": null, "url": null,'
        ' "mentions": ["CYP2C9 *3"]}], "drugs": [{"name": "warfarin",'
        ' "id": "PA451906", "url": null, "mentions": []}], "direction": "decreased"}]}',
        encoding="utf-8",
    )
    expected = load_ground_truth(reference, resolver)
    assert association_matches(expected[0], load_generated(legacy, resolver)[0])
    assert association_matches(expected[1], load_generated(current, resolver)[0])


def test_not_associated_overrides_direction_word():
    annotation = {
        "Is/Is Not associated": "Not associated with",
        "Direction of effect": "increased",
    }
    assert _ground_truth_direction(annotation) == "no_association"
    annotation["Is/Is Not associated"] = "Associated with"
    assert _ground_truth_direction(annotation) == "increased"
