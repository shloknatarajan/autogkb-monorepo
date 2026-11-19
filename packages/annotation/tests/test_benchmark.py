from annotation.benchmark import association_matches, evaluate_article


def test_association_match_uses_variant_drug_and_direction():
    expected = {"variant": "rs123", "drug": "warfarin", "direction": "decreased"}
    assert association_matches(
        expected,
        {"variant": "RS123", "drug": "warfarin", "direction": "decreased"},
    )
    assert not association_matches(
        expected,
        {"variant": "rs123", "drug": "warfarin", "direction": "increased"},
    )


def test_association_match_accepts_preserved_variant_alias():
    expected = {"variant": "cyp2c9*3", "drug": "warfarin", "direction": "decreased"}
    generated = {
        "variant": "rs1057910",
        "variant_aliases": ["CYP2C9*3"],
        "drug": "warfarin",
        "direction": "decreased",
    }
    assert association_matches(expected, generated)


def test_evaluate_article_reports_rough_metrics():
    expected = [
        {"variant": "rs1", "drug": "drug-a", "direction": "increased"},
        {"variant": "rs2", "drug": "drug-b", "direction": None},
    ]
    generated = [
        {"variant": "rs1", "drug": "drug-a", "direction": "increased"},
        {"variant": "rs3", "drug": "drug-c", "direction": "decreased"},
    ]
    result = evaluate_article(expected, generated)
    assert result["matched_associations"] == 1
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["variant_recall"] == 0.5
