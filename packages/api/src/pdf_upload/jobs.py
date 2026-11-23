"""Background workflow for uploaded PDFs."""

import asyncio
import time
from pathlib import Path

from loguru import logger
from shared.utils import ROOT as PIPELINE_ROOT
from src.database import update_job
from src.jobs import _annotate_and_store

from .datalab import convert_pdf_to_markdown

API_ROOT = Path(__file__).resolve().parents[2]


async def run_pdf_upload_job(
    job_id: str,
    pmid: str,
    pdf_bytes: bytes,
    filename: str = "upload.pdf",
    method: str | None = None,
) -> None:
    """Convert an uploaded PDF and run the shared annotation pipeline."""
    loop = asyncio.get_running_loop()
    start_time = time.monotonic()

    try:
        update_job(
            job_id,
            status="fetching_article",
            progress="Converting PDF to markdown via Datalab...",
        )
        logger.info(f"[{job_id}] Converting PDF ({len(pdf_bytes)} bytes) via Datalab")

        markdown = await loop.run_in_executor(
            None, convert_pdf_to_markdown, pdf_bytes, filename
        )
        if not markdown:
            raise ValueError("Datalab returned empty markdown")

        logger.info(f"[{job_id}] PDF conversion complete ({len(markdown)} chars)")

        articles_dir = API_ROOT / "data" / "articles"
        articles_dir.mkdir(parents=True, exist_ok=True)
        article_path = articles_dir / f"{pmid}.md"
        article_path.write_text(markdown, encoding="utf-8")
        logger.info(f"[{job_id}] Saved markdown to {article_path}")

        pipeline_articles_dir = PIPELINE_ROOT / "data" / "articles"
        pipeline_articles_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_articles_dir / f"{pmid}.md").write_text(markdown, encoding="utf-8")
        logger.info(
            f"[{job_id}] Cached markdown to pipeline path {pipeline_articles_dir}"
        )

        count = await _annotate_and_store(
            job_id,
            pmid,
            markdown,
            config_name="api_pdf_upload",
            prior_stages=("datalab_convert",),
            method=method,
            start_time=start_time,
        )
        logger.info(
            f"[{job_id}] Job completed successfully for {pmid} ({count} association(s))"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[{job_id}] Job failed for {pmid}: {exc}")
        try:
            update_job(
                job_id, status="failed", error=str(exc), progress="Analysis failed"
            )
        except Exception as db_exc:  # noqa: BLE001
            logger.error(f"[{job_id}] Also failed to record failure in DB: {db_exc}")
