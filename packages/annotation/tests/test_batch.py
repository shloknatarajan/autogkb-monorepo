from pathlib import Path

from annotation.batch import _is_complete


def test_is_complete_rejects_missing_and_invalid_outputs(tmp_path: Path):
    assert not _is_complete(tmp_path / "missing.json")
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    assert not _is_complete(invalid)


def test_is_complete_accepts_annotation_result(tmp_path: Path):
    output = tmp_path / "complete.json"
    output.write_text(
        '{"associations": [], "variant_mentions": []}', encoding="utf-8"
    )
    assert _is_complete(output)
