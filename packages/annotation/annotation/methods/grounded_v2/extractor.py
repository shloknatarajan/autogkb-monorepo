"""One-call extraction of structured associations and their evidence."""

import json
import re

from loguru import logger

from annotation.config import load_environment
from annotation.methods.grounded_v2.entities import EntityResolver, EntityTable
from annotation.methods.grounded_v2.models import (
    AnnotationResult,
    EvidenceQuote,
    ExtractionResponse,
    GroundedAssociation,
)
from annotation.methods.grounded_v2.postprocess import merge_duplicate_associations
from shared.utils import call_llm


DEFAULT_MODEL = "gpt-6-astra"

SYSTEM_PROMPT = """You extract pharmacogenomic variant annotations from a scientific article for a
curated knowledge base in the style of PharmGKB/ClinPGx. A curator reviews every
record you return, so aim for the records a careful curator would write: one per
distinct finding of this paper, not one per sentence, table row, or statistical test.

What one record is
- One finding: a variant (or group of alleles) that affects, or was tested and shown
  not to affect, one outcome of one drug therapy, in one direction.
- The same finding reported several ways is still one record. Unadjusted and adjusted
  analyses, different genetic models, subgroup repeats that agree, and a table row
  plus the prose describing it all belong together: put every reported statistic in
  `statistics` and every supporting quote in `evidence`.
- Genotypes of one variant that share a direction against the same comparator are one
  record; list them in `genotypes`. Opposite directions, different outcomes, and
  different drugs are separate records.
- Star alleles or haplotypes of one gene that the paper analyses together, as
  genotype groups or metabolizer groups, are one record that lists all of them.
- HLA alleles are different: each HLA allele is its own record, even when the paper
  tests many of them in one table or reports them in one sentence.
- Include negative results for variants the paper tested (`no_association`).

What to leave out
- Variants the paper only mentions as background or cites from other studies.
- How much dose or response variance a variant or model explains (R2, contribution
  to variability), unless that is the only result reported for the variant.
- Labels that do not identify a variant, such as "risk alleles", "SNPs assessed",
  "genetic variants", or polygenic scores.

Naming variants and drugs
Before the article you are given tables of the variants and drugs that a lookup tool
recognised in it, each with a reference such as V3 or D1, the ways the article writes
it, and the source blocks where it appears.
- In `variants` and `drugs`, give the reference whenever the entity is in the table.
- The tables are over-inclusive: some rows are ordinary words that resemble a drug
  name, or variants that are only background. Ignore rows that are not relevant.
- When the article identifies two rows as the same variant, for example a star allele
  and its rsID, list both references.
- For anything not in the table, write it as the article does. Write metabolizer
  phenotypes as "<GENE> <phenotype>", for example "CYP2D6 poor metabolizer".
- `drugs` is empty when the finding does not involve a drug.

Other fields
- `outcome`: a short noun phrase for what was measured, without analysis qualifiers
  ("stable warfarin dose", not "stable dose in the multivariate model").
- `direction`: the effect of the listed genotypes or alleles on the outcome relative
  to `comparator`.
- Do not infer a genotype, comparator, population, statistic, drug, or outcome the
  article does not state; use null or an empty list.

Evidence
Every record needs at least one quote copied character for character from a single
source block, with that block's SOURCE ID. Quotes are checked verbatim and a record
with no matching quote is discarded, so copy rather than paraphrase and keep each
quote short: a sentence or a table row.
"""


def number_source_blocks(text: str) -> tuple[str, dict[str, str]]:
    """Add stable source IDs to non-empty Markdown blocks."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    sources = {f"S{i:04d}": block for i, block in enumerate(blocks, 1)}
    numbered = "\n\n".join(
        f"[SOURCE {source_id}]\n{block}" for source_id, block in sources.items()
    )
    return numbered, sources


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def quote_is_supported(source: str, quote: str) -> bool:
    """Permit whitespace normalization, but reject paraphrased evidence."""
    normalized_quote = _normalize_space(quote)
    return bool(normalized_quote) and normalized_quote in _normalize_space(source)


def _source_id(value: str) -> str:
    """Accept "SOURCE S0012", "[S12]", or "12" for the block labelled S0012."""
    digits = re.search(r"\d+", value)
    return f"S{int(digits.group()):04d}" if digits else value


def _valid_evidence(
    evidence: list[EvidenceQuote], sources: dict[str, str]
) -> list[EvidenceQuote]:
    valid = []
    for item in evidence:
        source_id = _source_id(item.source_id)
        if source_id in sources and quote_is_supported(sources[source_id], item.quote):
            valid.append(item.model_copy(update={"source_id": source_id}))
    return valid


def ground_associations(
    response: ExtractionResponse, sources: dict[str, str], table: EntityTable
) -> AnnotationResult:
    """Keep records with a verbatim quote and attach the entities they reference."""
    associations = []
    for extracted in response.associations:
        evidence = _valid_evidence(extracted.evidence, sources)
        variants = table.resolve_variants(extracted.variants)
        if not evidence or not variants:
            logger.warning(
                "Discarding association for {}: {}",
                extracted.variants,
                "no verbatim quote" if not evidence else "no usable variant",
            )
            continue
        associations.append(
            GroundedAssociation(
                **extracted.model_dump(exclude={"variants", "drugs", "evidence"}),
                variants=variants,
                drugs=table.resolve_drugs(extracted.drugs),
                evidence=evidence,
            )
        )
    return AnnotationResult(associations=associations)


def annotate_text(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    resolver: EntityResolver | None = None,
) -> AnnotationResult:
    """Extract evidence-grounded PGx annotations from article text."""
    load_environment()
    if not text.strip():
        return AnnotationResult(associations=[])

    numbered_text, sources = number_source_blocks(text)
    table = EntityTable(sources, resolver)
    prompt = f"""{table.render()}

Article sources:
{numbered_text}
"""
    output = call_llm(
        model,
        SYSTEM_PROMPT,
        prompt,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "pharmacogenomic_annotations",
                "strict": True,
                "schema": ExtractionResponse.model_json_schema(),
            },
        },
    )
    response = ExtractionResponse.model_validate(json.loads(output))
    return merge_duplicate_associations(ground_associations(response, sources, table))
