"""``grounded_v2``: one LLM call over the article with prefilled entity lookups.

- ``method``: the ``AnnotationExtractor`` subclass
- ``extractor``: prompt, model call, and evidence-quote validation
- ``entities``: entity table and variant/drug resolution
- ``postprocess``: merging of duplicate records
- ``models``: record schemas
"""

from annotation.methods.grounded_v2.extractor import DEFAULT_MODEL, annotate_text
from annotation.methods.grounded_v2.method import GroundedExtractor
from annotation.methods.grounded_v2.models import AnnotationResult, GroundedAssociation

__all__ = [
    "DEFAULT_MODEL",
    "AnnotationResult",
    "GroundedAssociation",
    "GroundedExtractor",
    "annotate_text",
]
