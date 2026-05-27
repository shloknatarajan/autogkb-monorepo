import textwrap
from pathlib import Path

import pytest

from benchmark.litsuggest_eval.loader import GroundTruthRecord, load_ground_truth


@pytest.fixture
def tmp_tsv(tmp_path: Path) -> Path:
    content = textwrap.dedent("""\
        curationstate\tpmid\ttype\ttitle\tjobname\tscore
        Curated\t12345678\t1\tCYP2D6 and codeine response\tWeekly Digest\t0.999
        Not Relevant\t87654321\t1\tA general surgery study\tWeekly Digest\t0.910
        Relevant\t11111111\t1\tUGT1A1 and irinotecan toxicity\tWeekly Digest\t0.980
        Relevant Association\t22222222\t1\tCFTR modulator response\tWeekly Digest\t0.950
        Not Curated\t33333333\t1\tCYP2C19 population study\tWeekly Digest\t0.920
    """)
    p = tmp_path / "test.tsv"
    p.write_text(content, encoding="utf-8")
    return p


def test_loads_all_valid_records(tmp_tsv: Path) -> None:
    records = load_ground_truth(tmp_tsv)
    assert len(records) == 5


def test_record_fields_curated(tmp_tsv: Path) -> None:
    records = load_ground_truth(tmp_tsv)
    curated = next(r for r in records if r.curation_state == "Curated")
    assert curated.pmid == "12345678"
    assert curated.title == "CYP2D6 and codeine response"
    assert abs(curated.litsuggest_score - 0.999) < 1e-6


def test_all_five_states_accepted(tmp_tsv: Path) -> None:
    records = load_ground_truth(tmp_tsv)
    states = {r.curation_state for r in records}
    assert states == {"Curated", "Not Relevant", "Relevant", "Relevant Association", "Not Curated"}


def test_returns_frozen_dataclass(tmp_tsv: Path) -> None:
    records = load_ground_truth(tmp_tsv)
    assert isinstance(records[0], GroundTruthRecord)
    with pytest.raises((AttributeError, TypeError)):
        records[0].pmid = "changed"  # type: ignore[misc]
