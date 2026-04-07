"""
Summary generation: single class with function-based implementations.
"""

from functools import partial

from pipeline.modules.summary.models import ArticleSummary


class SummaryGenerator:
    """Summary generator that delegates to a plain generation function.

    The method name determines which function is used. Any extra kwargs
    are bound to the function via functools.partial.
    """

    METHODS: dict = {}  # populated lazily to avoid circular imports

    def __init__(self, method: str, **kwargs):
        self.name = method
        fn = self._resolve(method)
        self._generate_fn = partial(fn, **kwargs) if kwargs else fn

    def generate(
        self,
        pmcid: str,
        variants_data: list[dict],
        citations_data: dict[str, list[dict]] | None = None,
    ) -> ArticleSummary:
        """Generate summary for one article.

        Args:
            pmcid: Article PMC identifier.
            variants_data: List of {variant, sentences: [str]} dicts.
            citations_data: Optional full citations dict (pmcid -> list of citation dicts).

        Returns:
            ArticleSummary object.
        """
        return self._generate_fn(pmcid, variants_data, citations_data)

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
        from pipeline.modules.summary.methods.basic_summary import (
            basic_summary_generate,
        )

        cls.METHODS = {
            "basic_summary": basic_summary_generate,
        }
