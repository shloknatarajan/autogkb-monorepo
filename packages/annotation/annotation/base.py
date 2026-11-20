"""Common interface for annotation methods.

Each method subclasses :class:`AnnotationExtractor`, sets ``name``, and implements
``extract``. Subclasses register themselves by name, so callers pick a method with
``get_extractor("grounded_v2")`` and never import a concrete class.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timezone
from typing import ClassVar

from annotation.models import StrictModel

# Called with a job status and a human-readable message as stages start.
ProgressCallback = Callable[[str, str], None]


class DisplayAssociation(StrictModel):
    """One finding in the shape every method can produce and the viewer renders."""

    variant: str
    sentence: str
    explanation: str
    citations: list[str]


class ExtractionOutput(StrictModel):
    associations: list[DisplayAssociation]
    # Method-specific records, kept verbatim for analysis and benchmarking.
    structured_associations: list[dict] = []


class AnnotationRun(StrictModel):
    """An extraction result with the provenance needed to compare runs."""

    method: str
    model: str
    started_at: str
    completed_at: str
    elapsed_seconds: float
    output: ExtractionOutput


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AnnotationExtractor(ABC):
    name: ClassVar[str]
    description: ClassVar[str] = ""
    default_model: ClassVar[str]

    _registry: ClassVar[dict[str, type["AnnotationExtractor"]]] = {}

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        name = cls.__dict__.get("name")
        if name is None:
            return
        existing = AnnotationExtractor._registry.get(name)
        if existing is not None and existing.__qualname__ != cls.__qualname__:
            raise ValueError(f"Annotation method {name!r} is already registered")
        AnnotationExtractor._registry[name] = cls

    def __init__(self, model: str | None = None) -> None:
        self.model = model or self.default_model

    @abstractmethod
    def extract(
        self, markdown: str, identifier: str, progress: ProgressCallback
    ) -> ExtractionOutput:
        """Return the associations found in one article."""

    def run(
        self,
        markdown: str,
        identifier: str,
        progress: ProgressCallback | None = None,
    ) -> AnnotationRun:
        started_at = _now()
        started = time.monotonic()
        output = self.extract(markdown, identifier, progress or (lambda *_: None))
        return AnnotationRun(
            method=self.name,
            model=self.model,
            started_at=started_at,
            completed_at=_now(),
            elapsed_seconds=round(time.monotonic() - started, 2),
            output=output,
        )


def available_methods() -> dict[str, type[AnnotationExtractor]]:
    return dict(AnnotationExtractor._registry)


def get_extractor(name: str, model: str | None = None) -> AnnotationExtractor:
    try:
        cls = AnnotationExtractor._registry[name]
    except KeyError:
        known = ", ".join(sorted(AnnotationExtractor._registry)) or "none"
        raise ValueError(
            f"Unknown annotation method {name!r}. Available: {known}"
        ) from None
    return cls(model=model)
