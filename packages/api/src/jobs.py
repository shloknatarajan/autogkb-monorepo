"""
Background job runner for autogkb-api.

Provides run_analysis_job: async background task that runs the full analysis
pipeline for a single PMCID and persists results in the generation/sync.py
json_content schema.
"""

import asyncio
import os
import re
import time
from pathlib import Path

import requests
from loguru import logger
from pubmed_markdown import PubMedMarkdown as _PubMedMarkdownClass

from src.database import update_job
from shared.utils import ROOT as PIPELINE_ROOT
from pipeline.modules.variant_finding.utils import extract_all_variants
from pipeline.modules.sentence_generation.sentence_generator import SentenceGenerator
from pipeline.modules.citations.citation_finder import CitationFinder
from pipeline.modules.summary.summary_generator import SummaryGenerator

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------

PIPELINE_MODEL: str = os.environ.get("PIPELINE_MODEL", "gpt-4o")
_downloader = _PubMedMarkdownClass()

# Root of the repository (two levels above this file: src/ -> autogkb-api/)
API_ROOT: Path = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_title(markdown: str) -> str:
    """Return the first # heading from the markdown, or empty string."""
    m = re.search(r"^# (.+)$", markdown, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _get_pmid_from_pmcid(pmcid: str) -> str | None:
    """Look up the PMID for a PMCID via NCBI's idconv API."""
    try:
        resp = requests.get(
            "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
            params={"ids": pmcid, "format": "json"},
            timeout=10,
        )
        records = resp.json().get("records", [])
        if records:
            return records[0].get("pmid")
    except Exception as exc:
        logger.warning(f"PMID lookup failed for {pmcid}: {exc}")
    return None


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------


async def run_analysis_job(
    job_id: str,
    pmcid: str,
    article_text: str | None = None,
) -> None:
    """Run the full analysis pipeline for a PMCID (or PMID) as a background task.

    Args:
        job_id:       UUID of the annotation_jobs row to update.
        pmcid:        Identifier used for this job — either a PubMed Central ID
                      (e.g. 'PMC1234567') or a raw PMID when no PMC version exists.
        article_text: Pre-fetched article text (e.g. scraped by the browser extension
                      for non-PMC articles).  When provided, the download step is
                      skipped and this text is used directly.
    """
    loop = asyncio.get_running_loop()
    start_time = time.monotonic()

    try:
        # ------------------------------------------------------------------
        # Step 1 — Obtain article markdown
        # ------------------------------------------------------------------
        if article_text:
            # Browser-scraped text provided — skip network download
            markdown = article_text
            update_job(job_id, status="fetching_article", progress="Using article text from browser...")
            logger.info(f"[{job_id}] Using pre-fetched article text ({len(markdown)} chars) for {pmcid}")
        else:
            update_job(job_id, status="fetching_article", progress="Fetching article from PubMed...")
            logger.info(f"[{job_id}] Fetching markdown for {pmcid}")
            markdown = await loop.run_in_executor(
                None, _downloader.pmcid_to_markdown, pmcid
            )
            if not markdown:
                raise ValueError(f"No markdown content returned for {pmcid}")

        # ------------------------------------------------------------------
        # Step 2 — Persist markdown to disk
        # ------------------------------------------------------------------
        articles_dir: Path = API_ROOT / "data" / "articles"
        articles_dir.mkdir(parents=True, exist_ok=True)
        article_path: Path = articles_dir / f"{pmcid}.md"
        article_path.write_text(markdown, encoding="utf-8")
        logger.info(f"[{job_id}] Saved markdown to {article_path}")

        # All pipeline steps (sentence generation, citation finding, etc.) read
        # article text from the autogkb_pipeline package's own data/articles/
        # directory via get_markdown_text(). Write the downloaded article there
        # so every step finds it without needing to re-download.
        pipeline_articles_dir: Path = PIPELINE_ROOT / "data" / "articles"
        pipeline_articles_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_articles_dir / f"{pmcid}.md").write_text(markdown, encoding="utf-8")
        logger.info(f"[{job_id}] Cached markdown to pipeline path {pipeline_articles_dir}")

        # ------------------------------------------------------------------
        # Step 3 — Extract variants
        # ------------------------------------------------------------------
        update_job(job_id, status="extracting_variants", progress="Extracting genetic variants...")
        logger.info(f"[{job_id}] Extracting variants for {pmcid}")

        # Use the markdown already downloaded in Step 1 rather than letting
        # VariantExtractor.get_variants() re-read from the autogkb-benchmark
        # disk cache (a separate repo path that won't have this PMCID).
        variants: list[str] = await loop.run_in_executor(
            None, extract_all_variants, markdown
        )
        logger.info(f"[{job_id}] Found {len(variants)} variant(s): {variants}")

        # ------------------------------------------------------------------
        # Step 4 — Generate association sentences
        # ------------------------------------------------------------------
        update_job(
            job_id,
            status="generating_sentences",
            progress="Generating association sentences...",
        )
        logger.info(f"[{job_id}] Generating sentences for {pmcid}")

        sentence_gen = SentenceGenerator(
            method="batch_judge_ask",
            model=PIPELINE_MODEL,
            prompt_version="v5",
        )
        sentences: dict = await loop.run_in_executor(
            None, sentence_gen.generate, pmcid, variants
        )
        logger.info(f"[{job_id}] Sentence generation complete")

        # ------------------------------------------------------------------
        # Step 5 — Find citations
        # ------------------------------------------------------------------
        update_job(job_id, status="finding_citations", progress="Finding supporting citations...")
        logger.info(f"[{job_id}] Finding citations for {pmcid}")

        associations_input: list[dict] = [
            {
                "variant": v,
                "sentence": s.sentence,
                "explanation": s.explanation,
            }
            for v, sents in sentences.items()
            for s in sents
        ]

        citation_finder = CitationFinder(
            method="one_shot_citations",
            model=PIPELINE_MODEL,
            prompt_version="v2",
        )
        citations = await loop.run_in_executor(
            None, citation_finder.find_citations, pmcid, associations_input
        )
        logger.info(f"[{job_id}] Citation finding complete, {len(citations)} citation(s)")

        # ------------------------------------------------------------------
        # Step 6 — Generate summary
        # ------------------------------------------------------------------
        update_job(
            job_id,
            status="generating_summary",
            progress="Generating summary...",
        )
        logger.info(f"[{job_id}] Generating summary for {pmcid}")

        variants_data: list[dict] = [
            {"variant": v, "sentences": [s.sentence for s in sents]}
            for v, sents in sentences.items()
        ]
        citations_data: dict = {pmcid: [c.model_dump() for c in citations]}

        summary_gen = SummaryGenerator(
            method="basic_summary",
            model=PIPELINE_MODEL,
            prompt_version="v3",
        )
        summary = await loop.run_in_executor(
            None, summary_gen.generate, pmcid, variants_data, citations_data
        )
        logger.info(f"[{job_id}] Summary generation complete")

        # ------------------------------------------------------------------
        # Step 7 — Persist completed result in generation/sync.py schema
        # ------------------------------------------------------------------
        summary_dump = summary.model_dump()
        title = _extract_title(markdown)
        pmid = await loop.run_in_executor(None, _get_pmid_from_pmcid, pmcid)
        elapsed = time.monotonic() - start_time

        update_job(
            job_id,
            status="completed",
            progress="",
            pmid=pmid,
            title=title,
            json_content={
                "annotations": {
                    v: [s.model_dump() for s in sents]
                    for v, sents in sentences.items()
                },
                "annotation_citations": [c.model_dump() for c in citations],
                "annotation_data": {
                    "pmcid": pmcid,
                    "summary": summary_dump.get("summary", ""),
                },
            },
            markdown_content=markdown,
            generation_metadata={
                "config_name": "api",
                "variant_extraction_method": "regex_v5",
                "sentence_generation_method": "batch_judge_ask",
                "sentence_model": PIPELINE_MODEL,
                "citation_model": PIPELINE_MODEL,
                "summary_model": PIPELINE_MODEL,
                "elapsed_seconds": round(elapsed, 2),
                "git_sha": "unknown",
                "stages_run": ["variants", "sentences", "citations", "summary"],
            },
        )
        logger.info(f"[{job_id}] Job completed successfully for {pmcid} ({len(citations)} citation(s))")

    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[{job_id}] Job failed for {pmcid}: {exc}")
        try:
            update_job(job_id, status="failed", error=str(exc), progress="Analysis failed")
        except Exception as db_exc:
            logger.error(f"[{job_id}] Also failed to record failure in DB: {db_exc}")
