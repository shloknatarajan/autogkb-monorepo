from annotation.methods.grounded_v2.entities import EntityResolver, EntityTable

SOURCES = {
    "S0001": "Patients on Coumadin were genotyped for VKORC1 -1639G>A (rs9923231).",
    "S0002": "Carriers of CYP2C9 *3 needed less warfarin than CYP2C9*1 homozygotes.",
}


def test_resolver_canonicalizes_variants_and_drugs():
    resolver = EntityResolver()
    assert resolver.variant("VKORC1 -1639G>A").name == "rs9923231"
    assert resolver.variant("CYP2C9 *3").name == "CYP2C9*3"
    assert resolver.variant("HLA-B*5701").name == "HLA-B*57:01"
    assert resolver.drug("Coumadin").name == "warfarin"
    unknown = resolver.variant("CYP2D6 poor metabolizer")
    assert unknown.id is None and unknown.name == "CYP2D6 poor metabolizer"


def test_table_groups_notations_and_lists_sources():
    table = EntityTable(SOURCES)
    by_name = {entity.name: ref for ref, entity in table.variants.items()}
    assert set(by_name) == {"rs9923231", "CYP2C9*3", "CYP2C9*1"}
    vkorc1 = table.variants[by_name["rs9923231"]]
    assert len(vkorc1.mentions) == 2
    assert [entity.name for entity in table.drugs.values()] == ["warfarin"]
    rendered = table.render()
    assert f"{by_name['rs9923231']} | rs9923231 | written as:" in rendered
    assert "| in S0001, S0002" in rendered


def test_table_resolves_references_and_unlisted_text():
    table = EntityTable(SOURCES)
    ref = next(ref for ref, entity in table.variants.items() if entity.id)
    resolved = table.resolve_variants([ref, "V99", "CYP2D6 poor metabolizer", ref])
    assert [entity.name for entity in resolved] == [
        "rs9923231",
        "CYP2D6 poor metabolizer",
    ]
    assert table.resolve_drugs(["Coumadin"])[0].id == table.drugs["D1"].id


def test_decorated_references_resolve_and_misplaced_ones_are_dropped():
    table = EntityTable(SOURCES)
    ref = next(ref for ref, entity in table.variants.items() if entity.id)
    number = ref[1:]
    for written in (f"v{number}", f"[V{number}]", f"V{number} (rs9923231)"):
        assert [e.name for e in table.resolve_variants([written])] == ["rs9923231"]
    # A drug reference is not a variant, and must not become a variant name.
    assert table.resolve_variants(["D1"]) == []
    # A protein change that merely starts like a reference is left as text.
    assert [e.name for e in table.resolve_variants(["V600E"])] == ["V600E"]


def test_source_listing_matches_whole_tokens_only():
    table = EntityTable(
        {
            "S0001": "We genotyped rs1045 in all patients.",
            "S0002": "ABCB1 rs1045642 was also typed.",
        }
    )
    rendered = table.render()
    short = next(line for line in rendered.splitlines() if "| rs1045 |" in line)
    assert short.endswith("| in S0001")
