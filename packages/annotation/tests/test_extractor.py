import json
import os
from unittest.mock import patch

from annotation.config import load_environment
from annotation.extractor import annotate_text, number_source_blocks, quote_is_supported


def test_number_source_blocks_assigns_stable_ids():
    numbered, sources = number_source_blocks("First block.\n\nSecond block.")
    assert sources == {"S0001": "First block.", "S0002": "Second block."}
    assert "[SOURCE S0002]\nSecond block." in numbered


def test_quote_validation_normalizes_whitespace_only():
    assert quote_is_supported("A result\nwas significant.", "A result was significant.")
    assert not quote_is_supported("A result was significant.", "The result was strong.")


def test_load_environment_preserves_explicit_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("ANNOTATION_TEST_KEY=from-file\n", encoding="utf-8")
    with patch("annotation.config.Path.is_file", return_value=False), patch(
        "annotation.config.find_dotenv", return_value=str(env_path)
    ), patch.dict(os.environ, {"ANNOTATION_TEST_KEY": "explicit"}, clear=False):
        load_environment()
        assert os.environ["ANNOTATION_TEST_KEY"] == "explicit"


def extracted(**updates):
    values = {
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
            {"source_id": "S0001", "quote": "rs9923231 AA carriers needed less Coumadin."}
        ],
    }
    values.update(updates)
    return values


ARTICLE = "rs9923231 AA carriers needed less Coumadin."


def test_annotate_text_prefills_entities_and_resolves_references():
    response = {"associations": [extracted()]}
    with patch(
        "annotation.extractor.call_llm", return_value=json.dumps(response)
    ) as call:
        result = annotate_text(ARTICLE)

    prompt = call.call_args.args[2]
    assert "V1 | rs9923231 |" in prompt
    assert 'D1 | warfarin | written as: "Coumadin"' in prompt
    record = result.associations[0]
    assert record.variants[0].id == "PA166155091"
    assert record.drugs[0].name == "warfarin"
    assert record.render_sentence().startswith("Genotype AA of rs9923231")


def test_annotate_text_discards_unsupported_records():
    response = {
        "associations": [
            extracted(),
            extracted(
                outcome="response",
                evidence=[{"source_id": "S0001", "quote": "invented quote"}],
            ),
            extracted(outcome="bleeding", variants=["V42"]),
        ]
    }
    with patch("annotation.extractor.call_llm", return_value=json.dumps(response)):
        result = annotate_text(ARTICLE)

    assert [record.outcome for record in result.associations] == ["warfarin dose"]


def test_evidence_source_id_formatting_is_normalised():
    response = {
        "associations": [
            extracted(
                evidence=[{"source_id": "SOURCE S1", "quote": ARTICLE}],
            )
        ]
    }
    with patch("annotation.extractor.call_llm", return_value=json.dumps(response)):
        result = annotate_text(ARTICLE)

    assert result.associations[0].evidence[0].source_id == "S0001"
