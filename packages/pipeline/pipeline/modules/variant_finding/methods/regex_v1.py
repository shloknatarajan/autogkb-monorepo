"""
Regex-based variant extraction v1.

Basic patterns on methods/conclusions text only:
- rsIDs (rs9923231)
- Star alleles with CYP prefix only (CYP2C9*3)
- HLA alleles (HLA-B*58:01)
"""

import re

from shared.utils import get_methods_and_conclusions_text


def extract_rsids(text: str) -> list[str]:
    """Extract rsID variants from text."""
    pattern = r"\brs\d{4,}\b"
    matches = re.findall(pattern, text, re.IGNORECASE)
    return list(set(matches))


def extract_star_alleles(text: str) -> list[str]:
    """Extract star allele variants (e.g., CYP2C9*3) from text."""
    pattern = r"\b(CYP\w+)\*(\d+)\b"
    matches = re.findall(pattern, text)
    variants = [f"{gene}*{number}" for gene, number in matches]
    return list(set(variants))


def extract_hla_alleles(text: str) -> list[str]:
    """Extract HLA allele variants from text."""
    pattern = r"\bHLA-[A-Z]+\d*\*\d+:\d+\b"
    matches = re.findall(pattern, text)
    return list(set(matches))


def extract_all_variants(text: str) -> list[str]:
    """Extract all variant types from text."""
    variants = []
    variants.extend(extract_rsids(text))
    variants.extend(extract_star_alleles(text))
    variants.extend(extract_hla_alleles(text))
    return list(set(variants))


def regex_v1_extract(pmcid: str) -> list[str]:
    text = get_methods_and_conclusions_text(pmcid)
    if not text:
        return []
    return extract_all_variants(text)
