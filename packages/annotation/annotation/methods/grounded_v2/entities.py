"""Variant and drug resolution shared by the extraction prompt and the benchmark.

Lookups run before the LLM call: the entities recognised in an article are listed
in the prompt with short references, and the model cites those references instead
of free text. The benchmark resolves reference annotations with the same functions
so that both sides are compared on the same canonical keys.
"""

import logging
import os
import re
from pathlib import Path

from annotation.methods.grounded_v2.models import Entity
from shared.term_normalization.drugs import DrugLookup
from shared.term_normalization.drugs.text import normalize_drugs_in_text
from shared.term_normalization.genes import (
    VariantLookup,
    classify_variant_term,
    normalize_genes_and_variants_in_text,
    normalize_hla_allele,
    normalize_star_allele,
    normalize_variant_text,
)

# The repository layout does not survive installation into a container, so
# deployments point at the lookup tables explicitly.
DATA_DIR = Path(
    os.environ.get("AUTOGKB_DATA_DIR") or Path(__file__).resolve().parents[5] / "data"
)
_MATCH_THRESHOLD = 0.9
_MAX_LISTED_SOURCES = 6
# A table reference, tolerating the ways a model decorates one: "v3", "[V3]",
# "V3 (rs1045642)". The reference must be the whole value or be followed by
# punctuation, so names such as "V600E" are left alone.
_REF_RE = re.compile(r"[\[(]?([VD])0*(\d+)[\])]?(?:\s*[(\[:|].*)?", re.IGNORECASE)
logger = logging.getLogger(__name__)


def _clean(term: str) -> str:
    return " ".join(term.split())


class EntityResolver:
    """Resolve variant and drug terms to canonical entities, with caching."""

    def __init__(self, data_dir: str | Path = DATA_DIR):
        self.data_dir = Path(data_dir)
        self.variant_lookup = VariantLookup(data_dir=self.data_dir)
        self.drug_lookup = DrugLookup(data_dir=self.data_dir)
        self._variants: dict[str, Entity] = {}
        self._drugs: dict[str, Entity] = {}

    def variant(self, term: str) -> Entity:
        term = _clean(term)
        cached = self._variants.get(term)
        if cached is None:
            # Only rsIDs and gene-relative substitutions have local records. Free
            # text ("CYP2D6 poor metabolizer") would fall through to a slow fuzzy
            # scan whose near-misses are not safe to accept for identifiers.
            matches = (
                self.variant_lookup.search(term, top_k=1, use_api_fallback=False)
                if classify_variant_term(term) in {"rsid", "snp_shorthand"}
                else []
            )
            if matches and matches[0].score == 1.0 and not matches[0].ambiguous:
                cached = Entity(
                    name=matches[0].normalized_term,
                    id=matches[0].id,
                    url=matches[0].url,
                    mentions=[term],
                )
            else:
                # Star and HLA alleles are absent from the local variant table, so
                # canonical notation is the best available identity for them.
                name = normalize_hla_allele(term) or normalize_star_allele(term)
                if not name and re.fullmatch(r"rs\d+", term, re.IGNORECASE):
                    name = term.lower()
                cached = Entity(name=name or term, id=None, url=None, mentions=[term])
            self._variants[term] = cached
        return cached.model_copy(deep=True)

    def drug(self, term: str) -> Entity:
        term = _clean(term)
        cached = self._drugs.get(term)
        if cached is None:
            matches = self.drug_lookup.clinpgx_lookup(
                term, threshold=_MATCH_THRESHOLD, top_k=1
            )
            if (
                matches
                and matches[0].score >= _MATCH_THRESHOLD
                and not matches[0].ambiguous
            ):
                cached = Entity(
                    name=matches[0].normalized_term,
                    id=matches[0].id,
                    url=matches[0].url,
                    mentions=[term],
                )
            else:
                cached = Entity(name=term, id=None, url=None, mentions=[term])
            self._drugs[term] = cached
        return cached.model_copy(deep=True)


def variant_key(entity: Entity) -> str:
    return entity.id or normalize_variant_text(entity.name)


def drug_key(entity: Entity) -> str:
    return entity.id or entity.name.casefold()


def _add(entities: dict[str, Entity], key: str, entity: Entity) -> None:
    current = entities.get(key)
    if current is None:
        entities[key] = entity
        return
    for mention in entity.mentions:
        if mention not in current.mentions:
            current.mentions.append(mention)


class EntityTable:
    """Entities recognised in one article, addressable by prompt reference."""

    def __init__(
        self, sources: dict[str, str], resolver: EntityResolver | None = None
    ):
        self.resolver = resolver or EntityResolver()
        self.sources = sources
        self._blocks = {
            source_id: " ".join(block.split()) for source_id, block in sources.items()
        }
        text = "\n\n".join(sources.values())

        variants: dict[str, Entity] = {}
        found = normalize_genes_and_variants_in_text(
            text,
            variant_lookup=self.resolver.variant_lookup,
            data_dir=self.resolver.data_dir,
            use_api_fallback=False,
        )
        for mention in found["variants"]:
            entity = self.resolver.variant(mention)
            _add(variants, variant_key(entity), entity)

        drugs: dict[str, Entity] = {}
        for mention, details in normalize_drugs_in_text(
            text, lookup=self.resolver.drug_lookup, data_dir=self.resolver.data_dir
        ).items():
            if details["ambiguous"] or not details["matches"]:
                continue
            match = details["matches"][0]
            entity = Entity(
                name=match["normalized_term"],
                id=match["id"],
                url=match["url"],
                mentions=[_clean(mention)],
            )
            _add(drugs, drug_key(entity), entity)

        self.variants = {f"V{i}": e for i, e in enumerate(variants.values(), 1)}
        self.drugs = {f"D{i}": e for i, e in enumerate(drugs.values(), 1)}

    def _source_ids(self, entity: Entity) -> str:
        # Whole-token matching: "rs1045" must not be listed for a block that
        # only contains "rs1045642".
        pattern = re.compile(
            "|".join(
                rf"(?<![A-Za-z0-9]){re.escape(mention)}(?![A-Za-z0-9])"
                for mention in entity.mentions
            ),
            re.IGNORECASE,
        )
        hits = [
            source_id
            for source_id, block in self._blocks.items()
            if pattern.search(block)
        ]
        listed = ", ".join(hits[:_MAX_LISTED_SOURCES])
        extra = len(hits) - _MAX_LISTED_SOURCES
        return f"{listed} (+{extra} more)" if extra > 0 else listed

    def _rows(self, entities: dict[str, Entity]) -> str:
        if not entities:
            return "(none recognised)"
        return "\n".join(
            f"{ref} | {entity.name} | written as: "
            + ", ".join(f'"{mention}"' for mention in entity.mentions)
            + f" | in {self._source_ids(entity)}"
            for ref, entity in entities.items()
        )

    def render(self) -> str:
        return (
            "Variants recognised in the article:\n"
            f"{self._rows(self.variants)}\n\n"
            "Drugs recognised in the article:\n"
            f"{self._rows(self.drugs)}"
        )

    def _resolve(self, values, table, resolve_text) -> list[Entity]:
        resolved: dict[str, Entity] = {}
        for value in values:
            value = _clean(value)
            if not value:
                continue
            reference = _REF_RE.fullmatch(value)
            if reference:
                # An unknown reference, or a drug reference in the variant list,
                # is a model error and not an entity name.
                ref = f"{reference.group(1).upper()}{reference.group(2)}"
                entity = table.get(ref)
                if entity is None:
                    logger.warning("Dropped unusable table reference %r", value)
                    continue
                entity = entity.model_copy(deep=True)
            else:
                entity = resolve_text(value)
            resolved.setdefault(entity.id or entity.name.casefold(), entity)
        return list(resolved.values())

    def resolve_variants(self, values: list[str]) -> list[Entity]:
        return self._resolve(values, self.variants, self.resolver.variant)

    def resolve_drugs(self, values: list[str]) -> list[Entity]:
        return self._resolve(values, self.drugs, self.resolver.drug)
