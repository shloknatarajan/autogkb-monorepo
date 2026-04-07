"""
Regex-based variant extraction v2.

Improvements over v1:
- Uses full article text instead of just methods/conclusions
- Handles HLA alleles without HLA- prefix (e.g., B*5801 -> HLA-B*58:01)
- Handles more gene star allele formats (UGT, NUDT, CYP4F2, etc.)
- Normalizes variants for comparison
"""

import re

from shared.utils import get_markdown_text


def normalize_hla(variant: str) -> str:
    """Normalize HLA allele format to HLA-X*XX:XX format."""
    variant = variant.upper()

    if re.match(r"HLA-[A-Z]+\d*\*\d+:\d+", variant):
        return variant

    match = re.match(r"(?:HLA-)?([A-Z]+\d*)\*(\d{2,})(\d{2})?", variant)
    if match:
        gene = match.group(1)
        field1 = match.group(2)
        field2 = match.group(3)

        if len(field1) == 4 and field2 is None:
            field1, field2 = field1[:2], field1[2:]
        elif len(field1) == 2 and field2:
            pass
        elif len(field1) > 2 and field2 is None:
            field2 = field1[2:]
            field1 = field1[:2]

        if field2:
            return f"HLA-{gene}*{field1}:{field2}"
        else:
            return f"HLA-{gene}*{field1}"

    return variant


def normalize_star_allele(gene: str, allele_num: str) -> str:
    """Normalize star allele format."""
    gene = gene.upper()
    allele_num = re.sub(r"[xX].*$", "", allele_num)
    return f"{gene}*{allele_num}"


def extract_rsids(text: str) -> list[str]:
    """Extract rsID variants from text."""
    pattern = r"\brs\d{4,}\b"
    matches = re.findall(pattern, text, re.IGNORECASE)
    return [m.lower() for m in set(matches)]


def extract_star_alleles(text: str) -> list[str]:
    """Extract star allele variants from text.

    Handles genes like: CYP2C9, CYP2D6, CYP3A4, CYP4F2, UGT1A1, NUDT15, etc.
    """
    variants = []

    gene_pattern = (
        r"\b(CYP\w+|UGT\w+|NUDT\d+|DPYD|TPMT|NAT\d*|SLCO\w+|ABCB\d+)\*(\d+[xX]?[nN]?)\b"
    )
    matches = re.findall(gene_pattern, text, re.IGNORECASE)
    for gene, allele in matches:
        normalized = normalize_star_allele(gene, allele)
        variants.append(normalized)

    return list(set(variants))


def extract_hla_alleles(text: str) -> list[str]:
    """Extract HLA allele variants from text.

    Handles multiple formats:
    - HLA-B*58:01
    - HLA-B*5801
    - B*58:01
    - B*5801
    """
    variants = []

    # With HLA- prefix
    pattern1 = r"\bHLA-([A-Z]+\d*)\*(\d{2,}):?(\d{2})?\b"
    matches = re.findall(pattern1, text, re.IGNORECASE)
    for gene, f1, f2 in matches:
        if f2:
            variants.append(f"HLA-{gene.upper()}*{f1}:{f2}")
        elif len(f1) >= 4:
            variants.append(f"HLA-{gene.upper()}*{f1[:2]}:{f1[2:4]}")
        else:
            variants.append(f"HLA-{gene.upper()}*{f1}")

    # Without HLA- prefix
    hla_genes = r"(?:A|B|C|Cw|DRB1|DRB3|DRB4|DRB5|DQA1|DQB1|DPA1|DPB1)"
    pattern2 = rf"\b({hla_genes})\*(\d{{2,}})(?::(\d{{2}}))?\b"
    matches = re.findall(pattern2, text, re.IGNORECASE)
    for gene, f1, f2 in matches:
        gene = gene.upper()
        if gene == "CW":
            gene = "C"
        if f2:
            variants.append(f"HLA-{gene}*{f1}:{f2}")
        elif len(f1) >= 4:
            variants.append(f"HLA-{gene}*{f1[:2]}:{f1[2:4]}")
        else:
            variants.append(f"HLA-{gene}*{f1}")

    return list(set(variants))


def extract_all_variants(text: str) -> list[str]:
    """Extract all variant types from text."""
    variants = []
    variants.extend(extract_rsids(text))
    variants.extend(extract_star_alleles(text))
    variants.extend(extract_hla_alleles(text))
    return list(set(variants))


def regex_v2_extract(pmcid: str) -> list[str]:
    text = get_markdown_text(pmcid)
    if not text:
        return []
    return extract_all_variants(text)
