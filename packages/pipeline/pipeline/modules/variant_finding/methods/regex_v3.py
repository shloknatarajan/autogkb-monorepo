"""
Regex-based variant extraction v3.

Improvements over v2:
- Handles star alleles with space between gene and allele (e.g., "CYP2D6 *4")
- Handles standalone star alleles (*3, *4) by finding nearby gene context
- Handles NUDT15 star alleles
- Handles copy number variants (*1xN, *2xN)
- Handles diplotype patterns (*3/*3, *1/*4)
"""

import re

from shared.utils import get_markdown_text

PGX_GENES = [
    "CYP2D6",
    "CYP2C9",
    "CYP2C19",
    "CYP2B6",
    "CYP3A4",
    "CYP3A5",
    "CYP4F2",
    "CYP2A6",
    "CYP1A2",
    "UGT1A1",
    "UGT2B7",
    "UGT2B15",
    "NUDT15",
    "DPYD",
    "TPMT",
    "NAT1",
    "NAT2",
    "SLCO1B1",
    "SLCO1B3",
    "SLCO2B1",
    "ABCB1",
    "ABCG2",
    "VKORC1",
    "IFNL3",
    "IFNL4",
]


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
    allele_num = re.sub(r"[xX×].*$", "", allele_num)
    return f"{gene}*{allele_num}"


def extract_rsids(text: str) -> list[str]:
    """Extract rsID variants from text."""
    pattern = r"\brs\d{4,}\b"
    matches = re.findall(pattern, text, re.IGNORECASE)
    return [m.lower() for m in set(matches)]


def extract_star_alleles(text: str) -> list[str]:
    """Extract star allele variants from text.

    Handles:
    - Standard format: CYP2C9*3, UGT1A1*28
    - Space format: CYP2D6 *4, NUDT15 *3
    - Copy number: CYP2D6*1xN, *2xN
    """
    variants = []

    gene_pattern = "|".join(PGX_GENES)

    # Pattern 1: GENE*NUMBER format (standard)
    pattern1 = rf"\b({gene_pattern})\*(\d+[xX×]?[nN]?)\b"
    matches = re.findall(pattern1, text, re.IGNORECASE)
    for gene, allele in matches:
        normalized = normalize_star_allele(gene, allele)
        variants.append(normalized)

    # Pattern 2: GENE *NUMBER format (space between gene and asterisk)
    pattern2 = rf"\b({gene_pattern})\s+\*(\d+[xX×]?[nN]?)\b"
    matches = re.findall(pattern2, text, re.IGNORECASE)
    for gene, allele in matches:
        normalized = normalize_star_allele(gene, allele)
        variants.append(normalized)

    # Pattern 3: Standalone star alleles (*3, *4, etc.) - need gene context
    standalone_pattern = r"\*(\d{1,2})\b"

    # Find all gene mentions and their positions
    gene_mentions = []
    for gene in PGX_GENES:
        for match in re.finditer(rf"\b{gene}\b", text, re.IGNORECASE):
            gene_mentions.append((match.start(), match.end(), gene.upper()))

    # Pattern for diplotypes like *1×N/*2, *1/*10×N
    diplotype_pattern = r"\*(\d{1,2})[×xX]?[nN]?/\*(\d{1,2})[×xX]?[nN]?"
    for match in re.finditer(diplotype_pattern, text):
        allele1 = match.group(1)
        allele2 = match.group(2)
        diplotype_pos = match.start()

        nearest_gene = None
        min_distance = 800

        for gene_start, gene_end, gene_name in gene_mentions:
            if gene_end <= diplotype_pos:
                distance = diplotype_pos - gene_end
                if distance < min_distance:
                    min_distance = distance
                    nearest_gene = gene_name

        if nearest_gene:
            variants.append(f"{nearest_gene}*{allele1}")
            variants.append(f"{nearest_gene}*{allele2}")
            if "×" in match.group(0) or "x" in match.group(0).lower():
                variants.append(f"{nearest_gene}*{allele1}xN")
                variants.append(f"{nearest_gene}*{allele2}xN")

    # Find all standalone star alleles
    for match in re.finditer(standalone_pattern, text):
        allele_num = match.group(1)
        allele_pos = match.start()

        nearest_gene = None
        min_distance = 200

        for gene_start, gene_end, gene_name in gene_mentions:
            if gene_end <= allele_pos:
                distance = allele_pos - gene_end
                if distance < min_distance:
                    min_distance = distance
                    nearest_gene = gene_name

        if nearest_gene:
            normalized = normalize_star_allele(nearest_gene, allele_num)
            variants.append(normalized)

    # Pattern 4: Copy number variants with xN suffix
    xn_pattern = rf"\b({gene_pattern})\*(\d+)[xX×][nN]?\b"
    matches = re.findall(xn_pattern, text, re.IGNORECASE)
    for gene, allele in matches:
        normalized = normalize_star_allele(gene, allele)
        variants.append(normalized)
        variants.append(f"{gene.upper()}*{allele}xN")

    return list(set(variants))


def extract_hla_alleles(text: str) -> list[str]:
    """Extract HLA allele variants from text.

    Handles multiple formats:
    - HLA-B*58:01
    - HLA-B*5801
    - B*58:01
    - B*5801
    - HLA-B*38:(01/02) - parenthetical notation
    - B*39:(01/05/06/09)
    """
    variants = []

    hla_genes = r"(?:A|B|C|Cw|DRB1|DRB3|DRB4|DRB5|DQA1|DQB1|DPA1|DPB1)"

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

    # Parenthetical notation
    paren_pattern = rf"(?:HLA-)?({hla_genes})\*(\d{{2}}):?\(([/\d]+)\)"
    matches = re.findall(paren_pattern, text, re.IGNORECASE)
    for gene, field1, alleles_str in matches:
        gene = gene.upper()
        if gene == "CW":
            gene = "C"
        allele_nums = alleles_str.split("/")
        for allele_num in allele_nums:
            if allele_num.isdigit():
                variants.append(f"HLA-{gene}*{field1}:{allele_num}")

    return list(set(variants))


def extract_all_variants(text: str) -> list[str]:
    """Extract all variant types from text."""
    variants = []
    variants.extend(extract_rsids(text))
    variants.extend(extract_star_alleles(text))
    variants.extend(extract_hla_alleles(text))
    return list(set(variants))


def regex_v3_extract(pmcid: str) -> list[str]:
    text = get_markdown_text(pmcid)
    if not text:
        return []
    return extract_all_variants(text)
