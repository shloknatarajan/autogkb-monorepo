"""
Citation finding: single class with function-based implementations.
"""

from functools import partial

from pipeline.modules.citations.models import Citation


class CitationFinder:
    """Citation finder that delegates to a plain finding function.

    The method name determines which function is used. Any extra kwargs
    are bound to the function via functools.partial.
    """

    METHODS: dict = {}  # populated lazily to avoid circular imports

    def __init__(self, method: str, **kwargs):
        self.name = method
        fn = self._resolve(method)
        self._find_fn = partial(fn, **kwargs) if kwargs else fn

    def find_citations(self, pmcid: str, associations: list[dict]) -> list[Citation]:
        """Find citations for all associations in one article.

        Args:
            pmcid: Article PMC identifier.
            associations: List of {variant, sentence, explanation} dicts.

        Returns:
            List of Citation objects.
        """
        return self._find_fn(pmcid, associations)

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
        from pipeline.modules.citations.methods.one_shot_citations import (
            one_shot_citations_find,
        )

        cls.METHODS = {
            "one_shot_citations": one_shot_citations_find,
        }
