"""
Regex extraction (v5 alias).

Previously combined regex extraction with term normalization inline.
Normalization is now a separate pipeline stage (term_normalization).
This method is kept as an alias for regex_v5 for backward compatibility.
"""

from pipeline.modules.variant_finding.methods.regex_v5 import regex_v5_extract


def regex_term_norm_extract(pmcid: str) -> list[str]:
    return regex_v5_extract(pmcid)
