"""Gene-associated variant, allele, and SNP normalization."""

from .gene_search import (
    GeneLookup,
    GeneSearchResult,
    clear_gene_index_cache,
    normalize_gene_text,
)
from .llm_text import normalize_genes_in_abstract
from .snp_expansion import SNPExpander, SNPMapping, create_expander
from .text import (
    GeneticMention,
    GeneticTextResult,
    NormalizedGeneticEntity,
    normalize_genes_and_variants_in_text,
)
from .variant_search import (
    VariantLookup,
    VariantSearchResult,
    classify_variant_term,
    clear_variant_index_cache,
    normalize_hla_allele,
    normalize_star_allele,
    normalize_variant_text,
    parse_snp_shorthand,
)

__all__ = [
    "SNPExpander",
    "SNPMapping",
    "GeneLookup",
    "GeneSearchResult",
    "GeneticMention",
    "GeneticTextResult",
    "NormalizedGeneticEntity",
    "VariantLookup",
    "VariantSearchResult",
    "classify_variant_term",
    "clear_gene_index_cache",
    "clear_variant_index_cache",
    "create_expander",
    "normalize_hla_allele",
    "normalize_gene_text",
    "normalize_genes_and_variants_in_text",
    "normalize_genes_in_abstract",
    "normalize_star_allele",
    "normalize_variant_text",
    "parse_snp_shorthand",
]
