"""
Regex-based variant extraction (v5).

Uses pattern matching for rsIDs, star alleles, HLA alleles, and SNP notation
expansion, with BioC supplementary material integration.
"""

from generation.modules.variant_finding.utils import (
    extract_all_variants,
    get_combined_text,
)


def regex_v5_extract(pmcid: str) -> list[str]:
    combined_text, _ = get_combined_text(pmcid)
    if not combined_text:
        return []
    return extract_all_variants(combined_text)
