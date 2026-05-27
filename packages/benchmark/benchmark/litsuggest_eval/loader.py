from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "litsuggest" / "LitSuggest_in_ClinPGx.tsv"
)

_VALID_STATES = frozenset(
    {"Curated", "Relevant", "Relevant Association", "Not Curated", "Not Relevant"}
)


@dataclass(frozen=True)
class GroundTruthRecord:
    pmid: str
    curation_state: str
    title: str
    litsuggest_score: float


def load_ground_truth(path: Path = GROUND_TRUTH_PATH) -> list[GroundTruthRecord]:
    """Parse the PharmGKB LitSuggest ground truth TSV.

    Skips the header and any rows whose curationstate is not one of the five
    recognised labels.
    """
    records: list[GroundTruthRecord] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            if row.get("curationstate") not in _VALID_STATES:
                continue
            try:
                records.append(
                    GroundTruthRecord(
                        pmid=row["pmid"],
                        curation_state=row["curationstate"],
                        title=row["title"],
                        litsuggest_score=float(row["score"]),
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
    return records
