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


def test_annotate_text_discards_unsupported_records():
    response = {
        "associations": [
            {
                "variant": "rs12345",
                "variant_aliases": [],
                "variant_id": None,
                "variant_url": None,
                "gene": "GENE1",
                "genotypes": ["AA"],
                "drug": "exampledrug",
                "outcome": "toxicity",
                "direction": "increased",
                "comparator": "GG",
                "population": [],
                "statistics": [{"name": "p", "value": "0.01"}],
                "evidence": [
                    {
                        "source_id": "S0001",
                        "quote": "AA was associated with increased toxicity.",
                    }
                ],
                "confidence": "high",
            },
            {
                "variant": "rs99999",
                "variant_aliases": [],
                "variant_id": None,
                "variant_url": None,
                "gene": None,
                "genotypes": [],
                "drug": None,
                "outcome": "response",
                "direction": "other",
                "comparator": None,
                "population": [],
                "statistics": [],
                "evidence": [{"source_id": "S0001", "quote": "invented quote"}],
                "confidence": "low",
            },
        ],
        "variant_mentions": [],
    }
    with patch("annotation.extractor.call_llm", return_value=json.dumps(response)):
        result = annotate_text("AA was associated with increased toxicity.")

    assert [association.variant for association in result.associations] == ["rs12345"]
    assert result.associations[0].render_sentence().startswith("Genotype AA")
