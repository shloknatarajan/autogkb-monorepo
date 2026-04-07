"""
PGxMine-based variant extraction.

Downloads the PGxMine sentences TSV from Zenodo and looks up variants
by PMID for a given article. PGxMine is a text-mining tool that extracts
pharmacogenomic associations from biomedical literature.

Source: https://github.com/jakelever/pgxmine
Data: https://zenodo.org/records/6617348
"""

import json
import re

import pandas as pd
from loguru import logger

from shared.utils import ROOT

PGXMINE_ZENODO_URL = (
    "https://zenodo.org/api/records/6617348/files/pgxmine_sentences.tsv/content"
)
PGXMINE_DATA_DIR = ROOT / "data" / "cache" / "pgxmine"
PGXMINE_SENTENCES_PATH = PGXMINE_DATA_DIR / "pgxmine_sentences.tsv"

_pgxmine_df: pd.DataFrame | None = None
_pmid_mapping: dict[str, str] | None = None


def _get_pmid_mapping() -> dict[str, str]:
    """Get or initialize the PMCID-to-PMID mapping singleton."""
    global _pmid_mapping
    if _pmid_mapping is None:
        data_path = ROOT / "data" / "benchmark_v2" / "variant_bench.jsonl"
        _pmid_mapping = {}
        with open(data_path) as f:
            for line in f:
                record = json.loads(line)
                _pmid_mapping[record["pmcid"]] = record["pmid"]
    return _pmid_mapping


def _download_pgxmine_data() -> None:
    """Download PGxMine sentences TSV from Zenodo if not already cached."""
    if PGXMINE_SENTENCES_PATH.exists():
        return

    import requests

    PGXMINE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading PGxMine sentences data from Zenodo...")
    response = requests.get(PGXMINE_ZENODO_URL, stream=True, timeout=120)
    response.raise_for_status()

    with open(PGXMINE_SENTENCES_PATH, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info(f"PGxMine data saved to {PGXMINE_SENTENCES_PATH}")


def _get_pgxmine_df() -> pd.DataFrame:
    """Get or initialize the PGxMine DataFrame singleton."""
    global _pgxmine_df
    if _pgxmine_df is None:
        _download_pgxmine_data()
        logger.info("Loading PGxMine sentences data...")
        _pgxmine_df = pd.read_csv(PGXMINE_SENTENCES_PATH, sep="\t")
        # Ensure pmid column is string for consistent matching
        _pgxmine_df["pmid"] = _pgxmine_df["pmid"].astype(str)
        logger.info(f"Loaded {len(_pgxmine_df)} PGxMine sentence records")
    return _pgxmine_df


def _normalize_variant(
    variant_id: str, variant_type: str, gene_names: str
) -> str | None:
    """Normalize a PGxMine variant to match the benchmark format.

    Returns None if the variant can't be meaningfully normalized.
    """
    if not variant_id or pd.isna(variant_id):
        return None

    variant_id = str(variant_id).strip()

    # rsIDs: lowercase
    if variant_type == "rs_snp" or variant_id.lower().startswith("rs"):
        return variant_id.lower()

    # Star alleles: GENE*NUMBER format
    if variant_type == "star_allele":
        # variant_id may already be in the right format, e.g. "CYP2D6*4"
        if "*" in variant_id:
            # Normalize: uppercase gene, strip copy number suffixes
            match = re.match(r"([A-Za-z0-9]+)\*(\d+)", variant_id)
            if match:
                gene = match.group(1).upper()
                allele = match.group(2)
                return f"{gene}*{allele}"
            return variant_id

        # If variant_id is just the allele number, pair with gene_names
        if gene_names and not pd.isna(gene_names):
            gene = str(gene_names).split(";")[0].strip().upper()
            if variant_id.isdigit():
                return f"{gene}*{variant_id}"

        return None

    # HLA alleles
    if "HLA" in variant_id.upper() or (
        gene_names and not pd.isna(gene_names) and "HLA" in str(gene_names).upper()
    ):
        variant_id = variant_id.upper()
        # Normalize HLA format: HLA-X*XX:XX
        match = re.match(r"(?:HLA-)?([A-Z]+\d*)\*(\d{2,})(?::(\d{2}))?", variant_id)
        if match:
            gene = match.group(1)
            f1 = match.group(2)
            f2 = match.group(3)
            if f2:
                return f"HLA-{gene}*{f1}:{f2}"
            elif len(f1) >= 4:
                return f"HLA-{gene}*{f1[:2]}:{f1[2:4]}"
            else:
                return f"HLA-{gene}*{f1}"

    return None


def pgxmine_extract(pmcid: str, min_score: float = 0.75) -> list[str]:
    """Extract pharmacogenomic variants for an article using PGxMine data.

    Looks up the article's PMID in the PGxMine sentences dataset and returns
    all variants mentioned with a prediction confidence above the threshold.

    Args:
        pmcid: PubMed Central ID of the article.
        min_score: Minimum prediction confidence score (default 0.75,
                   the sentences file is already filtered at this threshold).

    Returns:
        List of normalized variant identifiers.
    """
    pmid_mapping = _get_pmid_mapping()
    pmid = pmid_mapping.get(pmcid)
    if not pmid:
        logger.warning(f"No PMID found for {pmcid}")
        return []

    df = _get_pgxmine_df()
    matches = df[df["pmid"] == str(pmid)]

    if matches.empty:
        logger.info(f"No PGxMine entries found for {pmcid} (PMID {pmid})")
        return []

    # Apply score filter
    if "score" in matches.columns:
        matches = matches[matches["score"] >= min_score]

    variants = set()
    for _, row in matches.iterrows():
        variant_id = row.get("variant_id", "")
        variant_type = row.get("variant_type", "")
        gene_names = row.get("gene_names", "")

        normalized = _normalize_variant(variant_id, variant_type, gene_names)
        if normalized:
            variants.add(normalized)

    logger.info(
        f"PGxMine found {len(variants)} unique variants for {pmcid} "
        f"(PMID {pmid}, {len(matches)} sentence matches)"
    )
    return list(variants)
