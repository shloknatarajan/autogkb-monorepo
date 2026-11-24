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
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
import weakref

import requests
from loguru import logger
from pubmed_markdown import PubMedMarkdown as _PubMedMarkdownClass

from src.database import (
    get_triage_session,
    is_job_in_flight,
    update_job,
    update_triage_session_status,
    update_triage_session_articles,
    upsert_uploaded_pdf_triage_article,
    get_job_by_pmid,
)
from src.litsuggest_client import fetch_weekly_pmids
from src.scoring import classify_additional_labels, fetch_pubmed_abstract, score_for_va
from shared.term_normalization.drugs import normalize_drugs_in_abstract
from shared.term_normalization.genes import normalize_genes_in_abstract
from shared.term_normalization.phenotypes import normalize_phenotypes_in_abstract
from shared.utils import DATA_DIR, ROOT as PIPELINE_ROOT
from src.annotators import get_extractor, resolve_method
from src.summary import (
    SUMMARY_METHOD,
    SUMMARY_MODEL,
    SUMMARY_PROMPT_VERSION,
    generate_summary,
)

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------

TRIAGE_MODEL: str = os.environ.get("TRIAGE_MODEL", "gpt-6-astra")

_INVALID_ARTICLE_MARKERS = (
    "checking your browser - recaptcha",
    "checking your browser – recaptcha",
    "http 404 [not found]",
    "verify you are human",
    "/pmc/articles//",
)


def _term_lookup_data_dir() -> Path:
    """Resolve lookup data explicitly when the shared package is installed."""
    return Path(os.environ.get("AUTOGKB_DATA_DIR", DATA_DIR))


def validate_article_markdown(markdown: str | None, identifier: str) -> str:
    """Reject empty, implausibly short, or known error pages before analysis."""
    if not isinstance(markdown, str) or len(markdown.strip()) < 500:
        raise ValueError(f"Article content for {identifier} is missing or too short")

    preview = markdown[:5000].casefold()
    marker = next(
        (candidate for candidate in _INVALID_ARTICLE_MARKERS if candidate in preview),
        None,
    )
    if marker:
        raise ValueError(
            f"Invalid article content returned for {identifier}: detected {marker!r}"
        )
    return markdown


def has_valid_article_markdown(markdown: str | None, identifier: str) -> bool:
    """Return whether cached content is safe to reuse for pipeline analysis."""
    try:
        validate_article_markdown(markdown, identifier)
    except ValueError:
        return False
    return True


_downloader = _PubMedMarkdownClass()

# Root of the repository (two levels above this file: src/ -> autogkb-api/)
API_ROOT: Path = Path(__file__).resolve().parent.parent


def extract_drug_tags(abstract: str | None, model: str = TRIAGE_MODEL) -> list[dict]:
    """Return display-ready drug tags grounded by abstract normalization."""
    if not abstract or not abstract.strip():
        return []

    mentions = normalize_drugs_in_abstract(
        abstract,
        model=model,
        data_dir=_term_lookup_data_dir(),
    )
    tags: OrderedDict[tuple[str, str], dict] = OrderedDict()

    for mention, details in mentions.items():
        matches = details.get("matches", [])
        top_match = matches[0] if matches else None
        name = top_match["normalized_term"] if top_match else mention
        source = top_match.get("source") if top_match else None
        identifier = top_match.get("id") if top_match else None
        key = (source or "unresolved", identifier or name.casefold())
        tag = tags.setdefault(
            key,
            {
                "name": name,
                "id": identifier,
                "source": source,
                "url": top_match.get("url") if top_match else None,
                "resolved": top_match is not None,
                "mentions": [],
            },
        )
        if mention not in tag["mentions"]:
            tag["mentions"].append(mention)

    return list(tags.values())


def extract_gene_tags(abstract: str | None, model: str = TRIAGE_MODEL) -> list[dict]:
    """Return display-ready gene tags grounded by abstract normalization."""
    if not abstract or not abstract.strip():
        return []

    normalized = normalize_genes_in_abstract(
        abstract,
        model=model,
        data_dir=_term_lookup_data_dir(),
    )
    tags: OrderedDict[tuple[str, str], dict] = OrderedDict()

    for mention, details in normalized["genes"].items():
        matches = details.get("matches", [])
        top_match = matches[0] if matches else None
        name = top_match["normalized_term"] if top_match else mention
        source = top_match.get("source") if top_match else None
        identifier = top_match.get("id") if top_match else None
        key = (source or "unresolved", identifier or name.casefold())
        tag = tags.setdefault(
            key,
            {
                "name": name,
                "id": identifier,
                "source": source,
                "url": top_match.get("url") if top_match else None,
                "resolved": top_match is not None,
                "mentions": [],
                "ncbi_gene_id": (top_match.get("ncbi_gene_id") if top_match else None),
                "hgnc_id": top_match.get("hgnc_id") if top_match else None,
            },
        )
        if mention not in tag["mentions"]:
            tag["mentions"].append(mention)

    return list(tags.values())


def extract_phenotype_tags(
    abstract: str | None,
    drug_tags: list[dict] | None = None,
    gene_tags: list[dict] | None = None,
    model: str = TRIAGE_MODEL,
) -> list[dict]:
    """Return compact phenotype tags while preserving contextual raw mentions."""
    if not abstract or not abstract.strip():
        return []

    frames = normalize_phenotypes_in_abstract(
        abstract,
        drug_tags=drug_tags or [],
        gene_tags=gene_tags or [],
        model=model,
        data_dir=_term_lookup_data_dir(),
    )
    tags: OrderedDict[tuple[str, str, tuple[str, ...]], dict] = OrderedDict()

    for frame in frames:
        name = frame.get("normalized_term") or frame.get("core_phenotype")
        if not isinstance(name, str) or not name.strip():
            continue
        source = frame.get("source")
        identifier = frame.get("id")
        gene_names = tuple(
            sorted(
                {
                    str(gene.get("name"))
                    for gene in frame.get("genes", [])
                    if gene.get("name")
                }
            )
        )
        key = (
            source or "unresolved",
            identifier or name.casefold(),
            gene_names if source == "cpic" else (),
        )
        tag = tags.setdefault(
            key,
            {
                "name": name,
                "id": identifier,
                "source": source,
                "url": frame.get("url"),
                "resolved": bool(frame.get("resolved")),
                "mentions": [],
                "categories": [],
                "directions": [],
                "drugs": [],
                "genes": [],
            },
        )
        mention = frame.get("mention")
        if isinstance(mention, str) and mention not in tag["mentions"]:
            tag["mentions"].append(mention)
        category = frame.get("category")
        if isinstance(category, str) and category not in tag["categories"]:
            tag["categories"].append(category)
        direction = frame.get("direction")
        if isinstance(direction, str) and direction not in tag["directions"]:
            tag["directions"].append(direction)
        for association_field in ("drugs", "genes"):
            for association in frame.get(association_field, []):
                association_name = association.get("name")
                if (
                    isinstance(association_name, str)
                    and association_name not in tag[association_field]
                ):
                    tag[association_field].append(association_name)

    return list(tags.values())


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


async def _annotate_and_store(
    job_id: str,
    identifier: str,
    markdown: str,
    *,
    config_name: str,
    method: str | None,
    start_time: float,
    pmid: str | None = None,
    prior_stages: tuple[str, ...] = (),
) -> int:
    """Run one annotation method, then the summary step, and persist the job.

    Every method returns the same display shape, so the stored ``json_content``
    and the viewer are independent of which method ran. The method name, model,
    and timestamps go into ``generation_metadata``.
    """
    loop = asyncio.get_running_loop()
    extractor = get_extractor(resolve_method(method))
    logger.info(f"[{job_id}] Annotating {identifier} with {extractor.name}")

    def progress(status: str, message: str) -> None:
        update_job(job_id, status=status, progress=message)

    run = await loop.run_in_executor(
        None, extractor.run, markdown, identifier, progress
    )
    associations = [a.model_dump() for a in run.output.associations]
    # Seed with every examined variant so one without sentences still appears.
    by_variant: OrderedDict[str, list[dict]] = OrderedDict(
        (variant, []) for variant in run.output.variants
    )
    for association in associations:
        # A record naming several variants is listed under each of them, so the
        # keys stay single variants whichever method ran.
        for variant in association["variants"] or [association["variant"]]:
            by_variant.setdefault(variant, []).append(
                {
                    "sentence": association["sentence"],
                    "explanation": association["explanation"],
                }
            )

    update_job(
        job_id, status="generating_summary", progress="Generating summary..."
    )
    summary_started = time.monotonic()
    summary = await loop.run_in_executor(
        None, generate_summary, identifier, associations
    )
    summary_elapsed = round(time.monotonic() - summary_started, 2)

    update_job(
        job_id,
        status="completed",
        progress="",
        pmid=pmid,
        title=_extract_title(markdown),
        json_content={
            "annotations": by_variant,
            "annotation_citations": associations,
            "annotation_data": {
                "pmcid": identifier,
                "summary": summary,
                "structured_associations": run.output.structured_associations,
            },
        },
        markdown_content=markdown,
        generation_metadata={
            "config_name": config_name,
            "annotation_method": run.method,
            "annotation_model": run.model,
            "summary_method": SUMMARY_METHOD,
            "summary_prompt_version": SUMMARY_PROMPT_VERSION,
            "summary_model": SUMMARY_MODEL,
            "summary_elapsed_seconds": summary_elapsed,
            "started_at": run.started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "annotation_elapsed_seconds": run.elapsed_seconds,
            "elapsed_seconds": round(time.monotonic() - start_time, 2),
            "git_sha": "unknown",
            "stages_run": [*prior_stages, "annotation", "summary"],
        },
    )
    return len(associations)


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------


async def run_analysis_job(
    job_id: str,
    pmcid: str,
    article_text: str | None = None,
    pmid: str | None = None,
    method: str | None = None,
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
            update_job(
                job_id,
                status="fetching_article",
                progress="Using article text from browser...",
            )
            logger.info(
                f"[{job_id}] Using pre-fetched article text ({len(markdown)} chars) for {pmcid}"
            )
        else:
            if not re.fullmatch(r"PMC\d+", pmcid.upper()):
                raise ValueError(
                    f"Cannot fetch article content without a valid PMCID: {pmcid}"
                )
            update_job(
                job_id,
                status="fetching_article",
                progress="Fetching article from PubMed...",
            )
            logger.info(f"[{job_id}] Fetching markdown for {pmcid}")
            markdown = await loop.run_in_executor(
                None, _downloader.pmcid_to_markdown, pmcid
            )
        markdown = validate_article_markdown(markdown, pmcid)

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
        logger.info(
            f"[{job_id}] Cached markdown to pipeline path {pipeline_articles_dir}"
        )

        if not pmid:
            pmid = await loop.run_in_executor(None, _get_pmid_from_pmcid, pmcid)
        count = await _annotate_and_store(
            job_id,
            pmcid,
            markdown,
            config_name="api",
            method=method,
            start_time=start_time,
            pmid=pmid,
        )
        logger.info(
            f"[{job_id}] Job completed successfully for {pmcid} ({count} association(s))"
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[{job_id}] Job failed for {pmcid}: {exc}")
        try:
            update_job(
                job_id, status="failed", error=str(exc), progress="Analysis failed"
            )
        except Exception as db_exc:
            logger.error(f"[{job_id}] Also failed to record failure in DB: {db_exc}")


async def run_reanalysis_job(
    job_id: str,
    pmid: str,
    markdown: str,
    pmcid: str | None = None,
    method: str | None = None,
) -> None:
    """Re-run only the analysis pipeline using existing markdown.

    Skips markdown fetching/conversion. Used by the regenerate button
    so PDF-uploaded articles don't need re-conversion.
    """
    start_time = time.monotonic()
    identifier = pmcid or pmid

    try:
        markdown = validate_article_markdown(markdown, identifier)

        # Persist markdown to disk so pipeline modules can read it
        articles_dir: Path = API_ROOT / "data" / "articles"
        articles_dir.mkdir(parents=True, exist_ok=True)
        (articles_dir / f"{identifier}.md").write_text(markdown, encoding="utf-8")

        pipeline_articles_dir: Path = PIPELINE_ROOT / "data" / "articles"
        pipeline_articles_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_articles_dir / f"{identifier}.md").write_text(
            markdown, encoding="utf-8"
        )

        count = await _annotate_and_store(
            job_id,
            identifier,
            markdown,
            config_name="api_reanalysis",
            method=method,
            start_time=start_time,
            pmid=pmid,
        )
        logger.info(
            f"[{job_id}] Reanalysis completed successfully for {identifier} "
            f"({count} association(s))"
        )

    except Exception as exc:
        logger.exception(f"[{job_id}] Reanalysis failed for {identifier}: {exc}")
        try:
            update_job(
                job_id, status="failed", error=str(exc), progress="Analysis failed"
            )
        except Exception as db_exc:
            logger.error(f"[{job_id}] Also failed to record failure in DB: {db_exc}")


async def run_triage_job(
    session_id: str,
    project_id: str,
    job_id: str,
    preserve_submitted: bool = False,
) -> None:
    """Score a batch of PMIDs from a LitSuggest weekly digest job.

    Fetches PMIDs from LitSuggest, retrieves their titles/abstracts from
    PubMed, scores each for variant-association relevance, and persists the
    results to the triage session row in the database.

    Args:
        session_id:  UUID of the triage_sessions row to update.
        project_id:  LitSuggest project identifier.
        job_id:      LitSuggest digest job ID (NOT the triage session ID).
    """
    loop = asyncio.get_running_loop()

    try:
        # Preserve submitted work across regeneration. Dismissed and pending
        # articles intentionally return to pending after they are re-scored.
        existing_session = (
            get_triage_session(session_id) if preserve_submitted else None
        )
        existing_articles = {
            article["pmid"]: article
            for article in (existing_session or {}).get("articles", [])
        }

        # ------------------------------------------------------------------
        # Step 1 — Mark session as scoring
        # ------------------------------------------------------------------
        update_triage_session_status(session_id, "scoring")
        logger.info(
            f"[triage:{session_id}] Starting triage for project={project_id} job={job_id}"
        )

        # ------------------------------------------------------------------
        # Step 2 — Fetch PMIDs from LitSuggest
        # ------------------------------------------------------------------
        pmid_entries: list[dict] = await loop.run_in_executor(
            None, fetch_weekly_pmids, project_id, job_id
        )
        logger.info(
            f"[triage:{session_id}] Fetched {len(pmid_entries)} PMID(s) from LitSuggest"
        )

        # ------------------------------------------------------------------
        # Step 3 — Fetch title + abstract for each PMID sequentially
        # NCBI allows 3 req/s without an API key; sequential fetching with a
        # 400 ms gap stays safely under that limit and avoids the race where
        # all concurrent slots fire at t=0 and saturate the rate limit.
        # ------------------------------------------------------------------
        ncbi_email = os.environ.get("NCBI_EMAIL", "")
        abstracts: list[dict] = []
        for entry in pmid_entries:
            result = await loop.run_in_executor(
                None, fetch_pubmed_abstract, entry["pmid"], ncbi_email
            )
            abstracts.append(result)
            await asyncio.sleep(0.4)
        logger.info(
            f"[triage:{session_id}] Fetched abstracts for {len(abstracts)} article(s)"
        )

        # ------------------------------------------------------------------
        # Step 4 — Score each article for variant-association relevance concurrently
        # ------------------------------------------------------------------
        score_tasks = [
            loop.run_in_executor(
                None,
                score_for_va,
                entry["pmid"],
                abs_result.get("title"),
                abs_result.get("abstract"),
                TRIAGE_MODEL,
            )
            for entry, abs_result in zip(pmid_entries, abstracts)
        ]
        additional_label_tasks = [
            loop.run_in_executor(
                None,
                classify_additional_labels,
                entry["pmid"],
                abs_result.get("title"),
                abs_result.get("abstract"),
                TRIAGE_MODEL,
            )
            for entry, abs_result in zip(pmid_entries, abstracts)
        ]
        drug_tag_tasks = [
            loop.run_in_executor(
                None,
                extract_drug_tags,
                abs_result.get("abstract"),
                TRIAGE_MODEL,
            )
            for abs_result in abstracts
        ]
        gene_tag_tasks = [
            loop.run_in_executor(
                None,
                extract_gene_tags,
                abs_result.get("abstract"),
                TRIAGE_MODEL,
            )
            for abs_result in abstracts
        ]
        scores: list[dict] = await asyncio.gather(*score_tasks)
        additional_labels: list[dict] = await asyncio.gather(*additional_label_tasks)
        drug_tags: list[list[dict]] = await asyncio.gather(*drug_tag_tasks)
        gene_tags: list[list[dict]] = await asyncio.gather(*gene_tag_tasks)
        phenotype_tag_tasks = [
            loop.run_in_executor(
                None,
                extract_phenotype_tags,
                abs_result.get("abstract"),
                article_drug_tags,
                article_gene_tags,
                TRIAGE_MODEL,
            )
            for abs_result, article_drug_tags, article_gene_tags in zip(
                abstracts, drug_tags, gene_tags
            )
        ]
        phenotype_tags: list[list[dict]] = await asyncio.gather(*phenotype_tag_tasks)
        logger.info(
            f"[triage:{session_id}] Scoring complete for {len(scores)} article(s)"
        )

        # ------------------------------------------------------------------
        # Step 5 — Build article list and save to DB
        # ------------------------------------------------------------------
        articles = []
        for (
            entry,
            abs_result,
            score_result,
            label_result,
            article_drug_tags,
            article_gene_tags,
            article_phenotype_tags,
        ) in zip(
            pmid_entries,
            abstracts,
            scores,
            additional_labels,
            drug_tags,
            gene_tags,
            phenotype_tags,
        ):
            previous = existing_articles.get(entry["pmid"], {})
            existing_job = get_job_by_pmid(entry["pmid"])
            already_analyzed = (
                existing_job is not None and existing_job.get("status") == "completed"
            )
            already_in_flight = is_job_in_flight(existing_job)
            was_submitted = previous.get("decision") == "submitted"
            submitted_job_id = (
                str(existing_job["id"])
                if already_in_flight
                else previous.get("job_id")
                if was_submitted
                else str(existing_job["id"])
                if already_analyzed
                else None
            )
            articles.append(
                {
                    "pmid": entry["pmid"],
                    "pmcid": abs_result.get("pmcid"),
                    "title": abs_result.get("title"),
                    "abstract": abs_result.get("abstract"),
                    "publication_date": abs_result.get("publication_date"),
                    "litsuggest_score": entry["litsuggest_score"],
                    "triage_score": score_result["score"],
                    "triage_label": score_result["label"],
                    "triage_tags": score_result.get("tags", []),
                    "paper_types": label_result.get("paper_types", []),
                    "paper_type_reasoning": label_result.get(
                        "paper_type_reasoning", ""
                    ),
                    "pgx_information_types": label_result.get(
                        "pgx_information_types", []
                    ),
                    "pgx_information_reasoning": label_result.get(
                        "pgx_information_reasoning", ""
                    ),
                    "drug_tags": article_drug_tags,
                    "gene_tags": article_gene_tags,
                    "phenotype_tags": article_phenotype_tags,
                    "reasoning": score_result["reasoning"],
                    "decision": "submitted"
                    if was_submitted or already_analyzed or already_in_flight
                    else "pending",
                    "job_id": submitted_job_id,
                }
            )
        update_triage_session_articles(session_id, articles)
        logger.info(f"[triage:{session_id}] Saved {len(articles)} article(s) to DB")

        # ------------------------------------------------------------------
        # Step 6 — Mark session as completed
        # ------------------------------------------------------------------
        update_triage_session_status(session_id, "completed")
        logger.info(f"[triage:{session_id}] Triage job completed successfully")

    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[triage:{session_id}] Failed: {exc}")
        try:
            update_triage_session_status(session_id, "error", error=str(exc))
        except Exception as db_exc:
            logger.error(f"[triage:{session_id}] Also failed to record error: {db_exc}")


async def run_triage_backfill(project_id: str, sessions: list[dict]) -> None:
    """Score newly created weekly sessions sequentially.

    Sequential execution keeps PubMed abstract requests within the rate limit even
    when several missing weeks are created by one frontend action.
    """
    for session in sessions:
        await run_triage_job(
            session["session_id"],
            project_id,
            session["job_id"],
            session.get("preserve_submitted", False),
        )


_UPLOADED_TRIAGE_SEMAPHORES: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()
MAX_CONCURRENT_UPLOADED_TRIAGE_SCORES = max(
    1, int(os.environ.get("MAX_CONCURRENT_UPLOADED_TRIAGE_SCORES", "3"))
)


def _get_uploaded_triage_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _UPLOADED_TRIAGE_SEMAPHORES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENT_UPLOADED_TRIAGE_SCORES)
        _UPLOADED_TRIAGE_SEMAPHORES[loop] = sem
    return sem


async def _score_single_uploaded_pdf_article(
    pmid: str,
    job_id: str | None,
    *,
    fallback_pmcid: str | None = None,
    fallback_title: str | None = None,
) -> dict:
    """Fetch PubMed metadata and run all triage classifiers for one uploaded PDF."""
    loop = asyncio.get_running_loop()
    ncbi_email = os.environ.get("NCBI_EMAIL", "")
    abs_result = await loop.run_in_executor(
        None, fetch_pubmed_abstract, pmid, ncbi_email
    )
    title = abs_result.get("title") or fallback_title
    abstract = abs_result.get("abstract")
    pmcid = abs_result.get("pmcid") or fallback_pmcid

    score_result, label_result, article_drug_tags, article_gene_tags = (
        await asyncio.gather(
            loop.run_in_executor(
                None, score_for_va, pmid, title, abstract, TRIAGE_MODEL
            ),
            loop.run_in_executor(
                None,
                classify_additional_labels,
                pmid,
                title,
                abstract,
                TRIAGE_MODEL,
            ),
            loop.run_in_executor(
                None, extract_drug_tags, abstract, TRIAGE_MODEL
            ),
            loop.run_in_executor(
                None, extract_gene_tags, abstract, TRIAGE_MODEL
            ),
        )
    )
    article_phenotype_tags = await loop.run_in_executor(
        None,
        extract_phenotype_tags,
        abstract,
        article_drug_tags,
        article_gene_tags,
        TRIAGE_MODEL,
    )

    return {
        "pmid": pmid,
        "pmcid": pmcid,
        "title": title,
        "abstract": abstract,
        "publication_date": abs_result.get("publication_date"),
        "litsuggest_score": 0.0,
        "triage_score": score_result["score"],
        "triage_label": score_result["label"],
        "triage_tags": score_result.get("tags", []),
        "paper_types": label_result.get("paper_types", []),
        "paper_type_reasoning": label_result.get("paper_type_reasoning", ""),
        "pgx_information_types": label_result.get("pgx_information_types", []),
        "pgx_information_reasoning": label_result.get(
            "pgx_information_reasoning", ""
        ),
        "drug_tags": article_drug_tags,
        "gene_tags": article_gene_tags,
        "phenotype_tags": article_phenotype_tags,
        "reasoning": score_result["reasoning"],
        "decision": "submitted",
        "job_id": job_id,
    }


async def run_uploaded_pdf_triage_scoring(
    month_date: str,
    pmid: str,
    job_id: str,
    *,
    pmcid: str | None = None,
    title: str | None = None,
) -> None:
    """Triage-score an uploaded PDF in the background and update its monthly session."""
    semaphore = _get_uploaded_triage_semaphore()
    try:
        async with semaphore:
            scored = await _score_single_uploaded_pdf_article(
                pmid,
                job_id,
                fallback_pmcid=pmcid,
                fallback_title=title,
            )
            upsert_uploaded_pdf_triage_article(month_date, scored)
            logger.info(
                f"[triage:uploaded_pdfs:{month_date}] Scored PMID {pmid} "
                f"({scored['triage_label']}, score={scored['triage_score']})"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[triage:uploaded_pdfs:{month_date}] Failed to score PMID {pmid}: {exc}"
        )
        try:
            upsert_uploaded_pdf_triage_article(
                month_date,
                {
                    "pmid": pmid,
                    "pmcid": pmcid,
                    "title": title,
                    "abstract": None,
                    "publication_date": None,
                    "litsuggest_score": 0.0,
                    "triage_score": 0,
                    "triage_label": "borderline",
                    "triage_tags": [],
                    "paper_types": [],
                    "paper_type_reasoning": "",
                    "pgx_information_types": [],
                    "pgx_information_reasoning": "",
                    "drug_tags": [],
                    "gene_tags": [],
                    "phenotype_tags": [],
                    "reasoning": "Automatic triage scoring was unavailable for this upload.",
                    "decision": "submitted",
                    "job_id": job_id,
                },
            )
        except Exception as db_exc:  # noqa: BLE001
            logger.error(
                f"[triage:uploaded_pdfs:{month_date}] Also failed to persist fallback for {pmid}: {db_exc}"
            )


async def run_uploaded_pdf_triage_rescore(session_id: str) -> None:
    """Re-score every article currently stored in an 'Uploaded PDFs' monthly session."""
    try:
        session = get_triage_session(session_id)
        if not session:
            return
        update_triage_session_status(session_id, "scoring")
        existing_articles = list(session.get("articles") or [])
        rescored: list[dict] = []
        for existing in existing_articles:
            pmid = str(existing.get("pmid") or "").strip()
            if not pmid:
                continue
            scored = await _score_single_uploaded_pdf_article(
                pmid,
                existing.get("job_id"),
                fallback_pmcid=existing.get("pmcid"),
                fallback_title=existing.get("title"),
            )
            scored["decision"] = existing.get("decision") or "submitted"
            rescored.append(scored)
            await asyncio.sleep(0.4)
        update_triage_session_articles(session_id, rescored)
        update_triage_session_status(session_id, "completed")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[triage:{session_id}] Uploaded PDFs rescore failed: {exc}")
        try:
            update_triage_session_status(session_id, "error", error=str(exc))
        except Exception as db_exc:  # noqa: BLE001
            logger.error(f"[triage:{session_id}] Also failed to record error: {db_exc}")
