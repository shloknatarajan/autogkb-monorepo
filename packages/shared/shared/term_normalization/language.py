"""Language guards shared by free-text term extractors."""

from __future__ import annotations

import unicodedata


def is_english_script_mention(mention: str) -> bool:
    """Return whether a mention uses scripts expected in English biomedical text.

    Latin letters cover ordinary English names. Greek letters are also allowed
    because established biomedical notation commonly includes them (for example,
    ``Δ9-THC`` and ``IFN-λ``). Digits, punctuation, and symbols are neutral.
    """
    for character in mention:
        if not character.isalpha():
            continue
        unicode_name = unicodedata.name(character, "")
        if "LATIN" not in unicode_name and "GREEK" not in unicode_name:
            return False
    return True
