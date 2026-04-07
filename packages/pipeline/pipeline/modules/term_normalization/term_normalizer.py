"""
Term normalization: single class with function-based implementations.
"""

from functools import partial

from pipeline.modules.term_normalization.models import NormalizationResult


class TermNormalizer:
    """Term normalizer that delegates to a normalization function.

    The method name determines which function is used. Any extra kwargs
    are bound to the function via functools.partial.
    """

    METHODS: dict = {}  # populated lazily to avoid circular imports

    def __init__(self, method: str, **kwargs):
        self.name = method
        fn = self._resolve(method)
        self._normalize_fn = partial(fn, **kwargs) if kwargs else fn

    def normalize(self, pmcid: str, variants: list[str]) -> NormalizationResult:
        return self._normalize_fn(pmcid, variants)

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
        from pipeline.modules.term_normalization.methods.pharmgkb_fuzzy import (
            pharmgkb_fuzzy_normalize,
        )

        cls.METHODS = {
            "pharmgkb_fuzzy": pharmgkb_fuzzy_normalize,
        }
