"""
SNP Notation Expansion Module

Expands informal SNP notations (like "516G>T") found in articles
to formal rsIDs (like "rs3745274") using PharmGKB data.

The problem: Articles often use shorthand like "CYP2B6 516G>T" but
databases use formal HGVS notation like "NM_000767.4:c.516G>T".
This module bridges that gap.
"""

import re
import json
import requests
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class SNPMapping:
    """Represents a mapping from informal notation to rsID."""

    gene: str
    notation: str  # e.g., "516G>T"
    rsid: str
    hgvs: str  # Full HGVS notation
    star_allele: Optional[str] = None  # e.g., "CYP2B6*9"


@dataclass
class SNPExpander:
    """
    Expands informal SNP notations to rsIDs.

    Usage:
        expander = SNPExpander()
        expander.load_or_build()  # Load from cache or build from PharmGKB

        # Lookup a single notation
        result = expander.lookup("CYP2B6", "516G>T")
        # Returns: SNPMapping(gene="CYP2B6", notation="516G>T", rsid="rs3745274", ...)

        # Expand notations in text
        text = "The CYP2B6 516G>T variant affects metabolism"
        expanded = expander.expand_text(text)
        # Returns: "The CYP2B6 516G>T (rs3745274) variant affects metabolism"
    """

    data_dir: Path = field(default_factory=lambda: Path("data"))
    cache_file: str = "snp_notation_mappings.json"

    # Key pharmacogenes to fetch from PharmGKB
    target_genes: list = field(
        default_factory=lambda: [
            "CYP2B6",
            "CYP2C9",
            "CYP2C19",
            "CYP2D6",
            "CYP3A4",
            "CYP3A5",
            "VKORC1",
            "TPMT",
            "DPYD",
            "UGT1A1",
            "SLCO1B1",
            "NUDT15",
        ]
    )

    # Mappings: (gene, normalized_notation) -> SNPMapping
    _mappings: dict = field(default_factory=dict)

    # Additional curated mappings for edge cases
    _curated_mappings: dict = field(default_factory=dict)

    def __post_init__(self):
        self._init_curated_mappings()

    def _init_curated_mappings(self):
        """Add manually curated mappings for known edge cases."""
        # These handle notations that don't follow standard HGVS patterns
        curated = [
            # VKORC1 promoter variant - often written without "c." prefix
            SNPMapping(
                gene="VKORC1",
                notation="-1639G>A",
                rsid="rs9923231",
                hgvs="NM_024006.5:c.-1639G>A",
            ),
            SNPMapping(
                gene="VKORC1",
                notation="1639G>A",
                rsid="rs9923231",
                hgvs="NM_024006.5:c.-1639G>A",
            ),
        ]
        for m in curated:
            key = (m.gene.upper(), self._normalize_notation(m.notation))
            self._curated_mappings[key] = m

    def _cache_path(self) -> Path:
        return self.data_dir / "term_lookup_info" / self.cache_file

    def _normalize_notation(self, notation: str) -> str:
        """
        Normalize a SNP notation to a canonical form for matching.

        Handles variations like:
        - "516G>T" vs "G516T" vs "516 G>T"
        - Case insensitivity
        """
        notation = notation.strip().upper()

        # Remove spaces around the arrow
        notation = re.sub(r"\s*>\s*", ">", notation)

        # Handle "G516T" format -> "516G>T"
        match = re.match(r"^([ACGT])(-?\d+)([ACGT])$", notation)
        if match:
            ref, pos, alt = match.groups()
            notation = f"{pos}{ref}>{alt}"

        return notation

    def _parse_hgvs_cds(self, hgvs: str) -> Optional[str]:
        """
        Extract simple notation from HGVS cDNA notation.

        Examples:
            "NM_000767.4:c.516G>T" -> "516G>T"
            "NM_024006.5:c.-1639G>A" -> "-1639G>A"
            "NM_000767.4:c.1459C>T" -> "1459C>T"
        """
        # Match coding sequence notation: c.POSITION REF>ALT
        match = re.search(r":c\.(-?\d+)([ACGT])>([ACGT])", hgvs, re.IGNORECASE)
        if match:
            pos, ref, alt = match.groups()
            return f"{pos}{ref.upper()}>{alt.upper()}"
        return None

    def _fetch_gene_haplotypes(self, gene_symbol: str) -> list:
        """Fetch all haplotypes for a gene from PharmGKB API."""
        url = f"https://api.pharmgkb.org/v1/data/haplotype?gene.symbol={gene_symbol}&view=max"
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get("data", [])
        except Exception as e:
            logger.warning(f"Failed to fetch haplotypes for {gene_symbol}: {e}")
        return []

    def _extract_mappings_from_haplotype(
        self, haplotype: dict, gene: str
    ) -> list[SNPMapping]:
        """Extract SNP mappings from a PharmGKB haplotype response."""
        mappings = []
        star_allele = haplotype.get("symbol", "")

        for allele in haplotype.get("alleles", []):
            variant = allele.get("variant", {})
            rsid = variant.get("symbol", "")

            if not rsid or not rsid.startswith("rs"):
                continue

            # Get synonyms which contain HGVS notations
            alt_names = variant.get("altNames", {})
            synonyms = alt_names.get("synonym", [])

            for synonym in synonyms:
                simple_notation = self._parse_hgvs_cds(synonym)
                if simple_notation:
                    mapping = SNPMapping(
                        gene=gene,
                        notation=simple_notation,
                        rsid=rsid,
                        hgvs=synonym,
                        star_allele=star_allele,
                    )
                    mappings.append(mapping)

        return mappings

    def build_from_pharmgkb(self, genes: Optional[list] = None) -> int:
        """
        Build mappings by fetching data from PharmGKB API.

        Args:
            genes: List of gene symbols to fetch. Defaults to target_genes.

        Returns:
            Number of mappings created.
        """
        genes = genes or self.target_genes
        total_mappings = 0

        for gene in genes:
            logger.info(f"Fetching haplotypes for {gene}...")
            haplotypes = self._fetch_gene_haplotypes(gene)

            for haplotype in haplotypes:
                mappings = self._extract_mappings_from_haplotype(haplotype, gene)
                for m in mappings:
                    key = (m.gene.upper(), self._normalize_notation(m.notation))
                    if key not in self._mappings:
                        self._mappings[key] = m
                        total_mappings += 1

        logger.info(f"Built {total_mappings} SNP notation mappings")
        return total_mappings

    def save_cache(self):
        """Save mappings to cache file."""
        cache_data = {
            "mappings": [
                {
                    "gene": m.gene,
                    "notation": m.notation,
                    "rsid": m.rsid,
                    "hgvs": m.hgvs,
                    "star_allele": m.star_allele,
                }
                for m in self._mappings.values()
            ]
        }
        self._cache_path().parent.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path(), "w") as f:
            json.dump(cache_data, f, indent=2)
        logger.info(f"Saved {len(self._mappings)} mappings to {self._cache_path()}")

    def load_cache(self) -> bool:
        """Load mappings from cache file. Returns True if successful."""
        if not self._cache_path().exists():
            return False

        try:
            with open(self._cache_path()) as f:
                cache_data = json.load(f)

            for item in cache_data.get("mappings", []):
                m = SNPMapping(
                    gene=item["gene"],
                    notation=item["notation"],
                    rsid=item["rsid"],
                    hgvs=item["hgvs"],
                    star_allele=item.get("star_allele"),
                )
                key = (m.gene.upper(), self._normalize_notation(m.notation))
                self._mappings[key] = m

            logger.info(f"Loaded {len(self._mappings)} mappings from cache")
            return True
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            return False

    def load_or_build(self, force_rebuild: bool = False) -> int:
        """
        Load mappings from cache, or build from PharmGKB if cache doesn't exist.

        Args:
            force_rebuild: If True, always rebuild from PharmGKB.

        Returns:
            Number of mappings loaded/built.
        """
        if not force_rebuild and self.load_cache():
            return len(self._mappings)

        count = self.build_from_pharmgkb()
        self.save_cache()
        return count

    def lookup(self, gene: str, notation: str) -> Optional[SNPMapping]:
        """
        Look up an rsID from a gene and informal notation.

        Args:
            gene: Gene symbol (e.g., "CYP2B6")
            notation: Informal SNP notation (e.g., "516G>T", "G516T")

        Returns:
            SNPMapping if found, None otherwise.
        """
        key = (gene.upper(), self._normalize_notation(notation))

        # Check curated mappings first (higher priority)
        if key in self._curated_mappings:
            return self._curated_mappings[key]

        return self._mappings.get(key)

    def expand_text(
        self, text: str, genes: Optional[list] = None, add_rsid: bool = True
    ) -> str:
        """
        Expand informal SNP notations in text to include rsIDs.

        Args:
            text: Input text containing SNP notations
            genes: List of genes to look for. Defaults to target_genes.
            add_rsid: If True, append rsID in parentheses. If False, replace notation.

        Returns:
            Text with expanded notations.

        Example:
            "CYP2B6 516G>T affects metabolism"
            ->
            "CYP2B6 516G>T (rs3745274) affects metabolism"
        """
        genes = genes or self.target_genes
        result = text

        # Build pattern for each gene
        for gene in genes:
            # Pattern: GENE followed by optional separator and SNP notation
            # Handles various formats:
            #   "CYP2B6 516G>T", "CYP2B6-G516T", "CYP2B6(516G>T)"
            #   "VKORC1-1639 G>A" (space before G>A)
            #   "CYP2B6 516 G>T" (spaces around position)
            pattern = rf"({gene})[\s\-\(\)]*(-?\d+)\s*([ACGT])\s*>\s*([ACGT])"

            def replacer(match):
                matched_gene = match.group(1)
                pos = match.group(2)
                ref = match.group(3)
                alt = match.group(4)
                notation = f"{pos}{ref}>{alt}"
                full_match = match.group(0)

                mapping = self.lookup(matched_gene, notation)
                if mapping:
                    if add_rsid:
                        return f"{full_match} ({mapping.rsid})"
                    else:
                        return f"{matched_gene} {mapping.rsid}"
                return full_match

            result = re.sub(pattern, replacer, result, flags=re.IGNORECASE)

            # Also handle reversed notation: "CYP2B6 G516T"
            pattern2 = rf"({gene})[\s\-\(\)]*([ACGT])(-?\d+)([ACGT])(?![>\d])"

            def replacer2(match):
                matched_gene = match.group(1)
                ref = match.group(2)
                pos = match.group(3)
                alt = match.group(4)
                notation = f"{pos}{ref}>{alt}"
                full_match = match.group(0)

                mapping = self.lookup(matched_gene, notation)
                if mapping:
                    if add_rsid:
                        return f"{full_match} ({mapping.rsid})"
                    else:
                        return f"{matched_gene} {mapping.rsid}"
                return full_match

            result = re.sub(pattern2, replacer2, result, flags=re.IGNORECASE)

        return result

    def get_all_rsids_for_gene(self, gene: str) -> list[str]:
        """Get all known rsIDs for a gene."""
        rsids = set()
        gene_upper = gene.upper()

        for (g, _), mapping in self._mappings.items():
            if g == gene_upper:
                rsids.add(mapping.rsid)

        for (g, _), mapping in self._curated_mappings.items():
            if g == gene_upper:
                rsids.add(mapping.rsid)

        return sorted(rsids)

    def stats(self) -> dict:
        """Get statistics about the loaded mappings."""
        genes = set()
        rsids = set()
        notations = set()

        for (gene, notation), mapping in self._mappings.items():
            genes.add(gene)
            rsids.add(mapping.rsid)
            notations.add(notation)

        return {
            "total_mappings": len(self._mappings),
            "unique_genes": len(genes),
            "unique_rsids": len(rsids),
            "unique_notations": len(notations),
            "genes": sorted(genes),
        }


# Convenience function
def create_expander(data_dir: Optional[Path] = None) -> SNPExpander:
    """Create and initialize an SNPExpander."""
    expander = SNPExpander(data_dir=data_dir or Path("data"))
    expander.load_or_build()
    return expander


if __name__ == "__main__":
    # Demo/test script
    expander = SNPExpander()
    expander.load_or_build(force_rebuild=True)

    print("\n=== SNP Expander Stats ===")
    print(expander.stats())

    print("\n=== Test Lookups ===")
    test_cases = [
        ("CYP2B6", "516G>T"),
        ("CYP2B6", "G516T"),
        ("CYP2B6", "983T>C"),
        ("VKORC1", "-1639G>A"),
        ("CYP2C19", "681G>A"),
    ]

    for gene, notation in test_cases:
        result = expander.lookup(gene, notation)
        if result:
            print(f"  {gene} {notation} -> {result.rsid} (via {result.star_allele})")
        else:
            print(f"  {gene} {notation} -> NOT FOUND")

    print("\n=== Text Expansion Test ===")
    test_text = "The CYP2B6 516G>T polymorphism and VKORC1-1639G>A variant affect drug response."
    expanded = expander.expand_text(test_text)
    print(f"  Input:  {test_text}")
    print(f"  Output: {expanded}")
