"""
Shared utilities for variant extraction experiments.

Contains all regex-based extraction functions, normalization helpers,
and common utilities used across multiple extraction approaches.
"""

import json
import re
from pathlib import Path

import yaml

from shared.utils import get_markdown_text, get_methods_and_results_text
from generation.modules.utils_bioc import fetch_bioc_supplement
from shared.term_normalization.snp_expansion import SNPExpander


# ============================================================================
# Constants
# ============================================================================

# Gene families with star allele nomenclature
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


# ============================================================================
# Singletons
# ============================================================================

_snp_expander = None


def get_snp_expander() -> SNPExpander:
    """Get or initialize the SNP expander singleton."""
    global _snp_expander
    if _snp_expander is None:
        _snp_expander = SNPExpander()
        _snp_expander.load_or_build()
    return _snp_expander


# ============================================================================
# Text retrieval
# ============================================================================


def get_combined_text(pmcid: str) -> tuple[str, str | None]:
    """
    Get combined article + supplement text for extraction.

    Returns:
        Tuple of (combined_text, supplement_text_or_none)
    """
    article_text = get_markdown_text(pmcid)
    supplement_text = fetch_bioc_supplement(pmcid)

    if supplement_text:
        combined_text = (
            article_text + "\n\n--- SUPPLEMENTARY MATERIAL ---\n\n" + supplement_text
        )
    else:
        combined_text = article_text

    return combined_text, supplement_text


# ============================================================================
# Normalization helpers
# ============================================================================


def normalize_hla(variant: str) -> str:
    """Normalize HLA allele format to HLA-X*XX:XX format."""
    variant = variant.upper()

    # Already normalized
    if re.match(r"HLA-[A-Z]+\d*\*\d+:\d+", variant):
        return variant

    # Handle formats like B*5801 -> HLA-B*58:01
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
    # Remove trailing x/X for copy number variants but keep xN format
    allele_num = re.sub(r"[xX×].*$", "", allele_num)
    return f"{gene}*{allele_num}"


# ============================================================================
# Extraction functions
# ============================================================================


def _rejoin_split_rsids(text: str) -> str:
    """Rejoin rsIDs split by spaces from PDF table cell parsing.

    BioC supplement text from PDFs can split rsIDs at table cell boundaries,
    e.g. "rs7692 58 rs28371 696" should be "rs769258 rs28371696".

    Only rejoins when the trailing digits are followed by another rsID or
    an uppercase word (column header), avoiding false positives in prose
    like "rs12345 was found in 3 patients".
    """
    return re.sub(
        r"(rs\d{4,})\s+(\d{1,4})(?=\s+(?:rs\d|[A-Z])|\s*$)",
        r"\1\2",
        text,
    )


def extract_rsids(text: str) -> list[str]:
    """Extract rsID variants from text."""
    text = _rejoin_split_rsids(text)
    pattern = r"\brs\d{4,}\b"
    matches = re.findall(pattern, text, re.IGNORECASE)
    return [m.lower() for m in set(matches)]


def extract_snp_notations(text: str) -> list[str]:
    """
    Extract rsIDs from informal SNP notations in text.

    Handles patterns like:
    - CYP2B6 516G>T -> rs3745274
    - CYP2B6-G516T -> rs3745274
    - VKORC1-1639 G>A -> rs9923231

    Returns:
        List of rsIDs derived from SNP notations found in text.
    """
    expander = get_snp_expander()
    rsids = []

    pgx_genes = expander.target_genes

    for gene in pgx_genes:
        # Pattern 1: GENE followed by position and substitution
        pattern1 = rf"\b({gene})[\s\-\(\)]*(-?\d+)\s*([ACGT])\s*>\s*([ACGT])"
        for match in re.finditer(pattern1, text, re.IGNORECASE):
            matched_gene = match.group(1)
            pos = match.group(2)
            ref = match.group(3)
            alt = match.group(4)
            notation = f"{pos}{ref.upper()}>{alt.upper()}"

            mapping = expander.lookup(matched_gene, notation)
            if mapping:
                rsids.append(mapping.rsid.lower())

        # Pattern 2: Reversed notation GENE G516T
        pattern2 = rf"\b({gene})[\s\-\(\)]*([ACGT])(-?\d+)([ACGT])(?![>\d])"
        for match in re.finditer(pattern2, text, re.IGNORECASE):
            matched_gene = match.group(1)
            ref = match.group(2)
            pos = match.group(3)
            alt = match.group(4)
            notation = f"{pos}{ref.upper()}>{alt.upper()}"

            mapping = expander.lookup(matched_gene, notation)
            if mapping:
                rsids.append(mapping.rsid.lower())

    return list(set(rsids))


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

    # Pattern for diplotypes like *1xN/*2, *1/*10xN
    diplotype_pattern = r"\*(\d{1,2})[×xX]?[nN]?/\*(\d{1,2})[×xX]?[nN]?"
    for match in re.finditer(diplotype_pattern, text):
        allele1 = match.group(1)
        allele2 = match.group(2)
        diplotype_pos = match.start()

        # Find the nearest gene mention within 800 characters before
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

        # Find the nearest gene mention within 200 characters before
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

    # Parenthetical notation: HLA-B*38:(01/02) or B*39:(01/05/06/09)
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
    variants.extend(extract_snp_notations(text))
    variants.extend(extract_star_alleles(text))
    variants.extend(extract_hla_alleles(text))
    return list(set(variants))


def filter_studied_variants(pmcid: str, variants: list[str]) -> list[str]:
    """Filter variants to only those appearing in Methods or Results sections.

    Variants that only appear in Discussion/Introduction (not directly studied)
    are excluded. Variants found in supplementary materials are kept.

    Args:
        pmcid: Article PMC identifier.
        variants: Full list of extracted variants.

    Returns:
        Filtered list containing only variants found in Methods, Results,
        or supplementary materials.
    """
    methods_results_text = get_methods_and_results_text(pmcid)
    supplement_text = fetch_bioc_supplement(pmcid) or ""

    # Combine Methods+Results with supplements for the filter check
    studied_text = methods_results_text
    if supplement_text:
        studied_text += "\n\n" + supplement_text

    if not studied_text:
        # If we can't extract sections, return all variants as fallback
        return variants

    studied_variants = extract_all_variants(studied_text)
    studied_set = {v.lower() for v in studied_variants}

    return [v for v in variants if v.lower() in studied_set]


def get_variant_types(variants: list[str]) -> dict:
    """Categorize variants by type into rsids, star_alleles, hla_alleles, other."""
    result: dict[str, list[str]] = {
        "rsids": [],
        "star_alleles": [],
        "hla_alleles": [],
        "other": [],
    }
    pgx_genes_upper = [g.upper() for g in PGX_GENES]
    for v in variants:
        v_upper = v.upper()
        if v_upper.startswith("RS") and v_upper[2:].isdigit():
            result["rsids"].append(v)
        elif v_upper.startswith("HLA-"):
            result["hla_alleles"].append(v)
        elif "*" in v and any(g in v_upper for g in pgx_genes_upper):
            result["star_alleles"].append(v)
        else:
            result["other"].append(v)
    return result


# ============================================================================
# LLM response parsing
# ============================================================================


def extract_json_array(text: str) -> list[str]:
    """
    Extract JSON array from LLM response.

    Handles various formats:
    - Pure JSON array: ["rs9923231", "CYP2C9*2"]
    - JSON in markdown code block: ```json\\n["rs9923231"]\\n```
    - JSON with explanation text before/after
    """
    # First try to extract from code blocks
    code_block_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if code_block_match:
        json_str = code_block_match.group(1)
    else:
        # Try to find JSON array anywhere in the text
        json_match = re.search(r"\[.*?\]", text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            return []

    try:
        result = json.loads(json_str)
        if isinstance(result, list):
            return [str(v).strip() for v in result]
        return []
    except json.JSONDecodeError:
        return []


# ============================================================================
# Prompt loading
# ============================================================================


def load_prompts(prompts_file: Path) -> dict:
    """Load prompts from a YAML file."""
    with open(prompts_file) as f:
        return yaml.safe_load(f)
