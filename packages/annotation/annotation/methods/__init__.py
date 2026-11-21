"""Annotation methods, one module or subpackage per method.

Importing this package registers every method with ``annotation.base``. To add
one, create a module here with an ``AnnotationExtractor`` subclass and import
it below.
"""

from annotation.methods.grounded_v2 import GroundedExtractor
from annotation.methods.sentences_v5 import SentencePipelineExtractor

__all__ = ["GroundedExtractor", "SentencePipelineExtractor"]
