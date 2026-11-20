import json
from unittest.mock import patch

import pytest

from annotation.base import (
    AnnotationExtractor,
    DisplayAssociation,
    ExtractionOutput,
    available_methods,
    get_extractor,
)
from annotation.grounded import GroundedExtractor


def test_subclasses_register_by_name_and_run_records_provenance():
    class EchoExtractor(AnnotationExtractor):
        name = "echo_test"
        default_model = "none"

        def extract(self, markdown, identifier, progress):
            progress("generating_sentences", "working")
            return ExtractionOutput(
                associations=[
                    DisplayAssociation(
                        variant="rs1", sentence=markdown, explanation="", citations=[]
                    )
                ]
            )

    assert available_methods()["echo_test"] is EchoExtractor
    stages = []
    run = get_extractor("echo_test", model="custom").run(
        "text", "PMC1", lambda status, message: stages.append(status)
    )
    assert (run.method, run.model) == ("echo_test", "custom")
    assert run.started_at <= run.completed_at
    assert stages == ["generating_sentences"]
    assert run.output.associations[0].sentence == "text"


def test_unknown_method_lists_available_ones():
    with pytest.raises(ValueError, match="grounded_v2"):
        get_extractor("missing")


def test_grounded_extractor_maps_records_to_display_shape():
    response = {
        "associations": [
            {
                "variants": ["V1"],
                "gene": "VKORC1",
                "genotypes": ["AA"],
                "drugs": ["D1"],
                "outcome": "warfarin dose",
                "direction": "decreased",
                "comparator": "GG",
                "population": [],
                "statistics": [{"name": "p", "value": "0.01"}],
                "evidence": [
                    {
                        "source_id": "S0001",
                        "quote": "rs9923231 AA carriers needed less Coumadin.",
                    }
                ],
            }
        ]
    }
    with patch("annotation.extractor.call_llm", return_value=json.dumps(response)):
        run = GroundedExtractor().run(
            "rs9923231 AA carriers needed less Coumadin.", "PMC1"
        )

    association = run.output.associations[0]
    assert association.variant == "rs9923231"
    assert association.sentence.startswith("Genotype AA of rs9923231")
    assert association.explanation == "p: 0.01"
    assert association.citations == ["rs9923231 AA carriers needed less Coumadin."]
    assert run.output.structured_associations[0]["drugs"][0]["name"] == "warfarin"
    assert run.method == "grounded_v2"
