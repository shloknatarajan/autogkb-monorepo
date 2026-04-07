"""
Variant extraction: single class with function-based implementations.
"""

from functools import partial


class VariantExtractor:
    """Variant extractor that delegates to a plain extraction function.

    The method name determines which function is used. Any extra kwargs
    are bound to the function via functools.partial.
    """

    METHODS: dict = {}  # populated lazily to avoid circular imports

    def __init__(self, method: str, **kwargs):
        self.name = method
        fn = self._resolve(method)
        self._extract_fn = partial(fn, **kwargs) if kwargs else fn

    def get_variants(self, pmcid: str) -> list[str]:
        return self._extract_fn(pmcid)

    @classmethod
    def _resolve(cls, method: str):
        if not cls.METHODS:
            cls._load_methods()
        if method not in cls.METHODS:
            available = ", ".join(cls.METHODS.keys())
            raise ValueError(f"Unknown method '{method}'. Available: {available}")
        return cls.METHODS[method]

    @classmethod
    def _load_methods(cls):
        from generation.modules.variant_finding.methods.just_ask import just_ask_extract
        from generation.modules.variant_finding.methods.pubtator import pubtator_extract
        from generation.modules.variant_finding.methods.pxgmine import pgxmine_extract
        from generation.modules.variant_finding.methods.regex_llm_filter import (
            regex_llm_filter_extract,
        )
        from generation.modules.variant_finding.methods.regex_term_norm import (
            regex_term_norm_extract,
        )
        from generation.modules.variant_finding.methods.regex_v1 import regex_v1_extract
        from generation.modules.variant_finding.methods.regex_v2 import regex_v2_extract
        from generation.modules.variant_finding.methods.regex_v3 import regex_v3_extract
        from generation.modules.variant_finding.methods.regex_v4 import regex_v4_extract
        from generation.modules.variant_finding.methods.regex_v5 import regex_v5_extract

        cls.METHODS = {
            "just_ask": just_ask_extract,
            "regex_v1": regex_v1_extract,
            "regex_v2": regex_v2_extract,
            "regex_v3": regex_v3_extract,
            "regex_v4": regex_v4_extract,
            "regex_v5": regex_v5_extract,
            "regex_llm_filter": regex_llm_filter_extract,
            "regex_term_norm": regex_term_norm_extract,
            "pubtator": pubtator_extract,
            "pgxmine": pgxmine_extract,
        }
