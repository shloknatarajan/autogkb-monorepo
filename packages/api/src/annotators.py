"""Annotation method selection for the API.

The methods themselves live in ``annotation.methods``; importing ``annotation``
registers them. ``ANNOTATION_METHOD`` selects the default and a request may
name another one.
"""

import os

from annotation import available_methods, get_extractor

DEFAULT_ANNOTATION_METHOD: str = os.environ.get("ANNOTATION_METHOD", "grounded_v2")

if DEFAULT_ANNOTATION_METHOD not in available_methods():
    # Fail at startup rather than on every request that omits a method.
    raise RuntimeError(
        f"ANNOTATION_METHOD={DEFAULT_ANNOTATION_METHOD!r} is not a registered "
        f"annotation method. Available: {', '.join(sorted(available_methods()))}"
    )


def resolve_method(name: str | None) -> str:
    """Return a registered method name, defaulting to the configured one."""
    method = name or DEFAULT_ANNOTATION_METHOD
    if method not in available_methods():
        known = ", ".join(sorted(available_methods()))
        raise ValueError(f"Unknown annotation method {method!r}. Available: {known}")
    return method


def describe_methods() -> list[dict]:
    return [
        {
            "name": name,
            "description": cls.description,
            "model": cls.default_model,
            "default": name == DEFAULT_ANNOTATION_METHOD,
        }
        for name, cls in sorted(available_methods().items())
    ]


__all__ = [
    "DEFAULT_ANNOTATION_METHOD",
    "describe_methods",
    "get_extractor",
    "resolve_method",
]
