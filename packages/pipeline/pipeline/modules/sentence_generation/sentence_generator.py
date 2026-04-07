"""
Sentence generation: single class with function-based implementations.
"""

from functools import partial

from pipeline.modules.sentence_generation.models import GeneratedSentence


class SentenceGenerator:
    """Sentence generator that delegates to a plain generation function.

    The method name determines which function is used. Any extra kwargs
    are bound to the function via functools.partial.
    """

    METHODS: dict = {}  # populated lazily to avoid circular imports

    def __init__(self, method: str, **kwargs):
        self.name = method
        fn = self._resolve(method)
        self._generate_fn = partial(fn, **kwargs) if kwargs else fn

    def generate(
        self, pmcid: str, variants: list[str]
    ) -> dict[str, list[GeneratedSentence]]:
        """Generate sentences for the given article and variants.

        Returns:
            Dict mapping variant -> list of GeneratedSentence.
        """
        return self._generate_fn(pmcid, variants)

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
        from pipeline.modules.sentence_generation.methods.raw_sentence_ask import (
            raw_sentence_ask_generate,
        )
        from pipeline.modules.sentence_generation.methods.batch_judge_ask import (
            batch_judge_ask_generate,
        )
        from pipeline.modules.sentence_generation.methods.llm_judge_ask import (
            llm_judge_ask_generate,
        )

        cls.METHODS = {
            "raw_sentence_ask": raw_sentence_ask_generate,
            "batch_judge_ask": batch_judge_ask_generate,
            "llm_judge_ask": llm_judge_ask_generate,
        }
