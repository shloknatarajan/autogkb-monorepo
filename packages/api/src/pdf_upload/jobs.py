"""Background workflow for uploaded PDFs."""

import asyncio
import os
from pathlib import Path
import time
import weakref

from loguru import logger
from shared.utils import ROOT as PIPELINE_ROOT
from src.database import update_job
from src.jobs import _annotate_and_store

from .datalab import convert_pdf_to_markdown

API_ROOT = Path(__file__).resolve().parents[2]
MAX_CONCURRENT_PDF_JOBS = max(
    1, int(os.environ.get("MAX_CONCURRENT_PDF_JOBS", "3"))
)

_LOOP_SEMAPHORES: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()


def _get_pdf_job_semaphore() -> asyncio.Semaphore:
    """Return a bounded concurrency semaphore scoped to the running event loop."""
    loop = asyncio.get_running_loop()
    sem = _LOOP_SEMAPHORES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENT_PDF_JOBS)
        _LOOP_SEMAPHORES[loop] = sem
    return sem


async def run_pdf_upload_job(
    job_id: str,
    pmid: str,
    pdf_source: bytes | Path,
    filename: str = "upload.pdf",
    method: str | None = None,
) -> None:
    """Convert an uploaded PDF and run the shared annotation pipeline.

    Accepts either raw ``bytes`` or a ``Path`` to a staged temporary PDF file
    (which is deleted in ``finally`` once conversion finishes or fails).
    """
    loop = asyncio.get_running_loop()
    temp_path = pdf_source if isinstance(pdf_source, Path) else None
    semaphore = _get_pdf_job_semaphore()

    try:
        if semaphore.locked():
            update_job(
                job_id,
                status="pending",
                progress="Queued for PDF conversion...",
            )

        async with semaphore:
            start_time = time.monotonic()
            pdf_bytes = (
                temp_path.read_bytes() if temp_path is not None else pdf_source
            )
            if not pdf_bytes:
                raise ValueError("Uploaded PDF file is empty")

            update_job(
                job_id,
                status="fetching_article",
                progress="Converting PDF to markdown via Datalab...",
            )
            logger.info(
                f"[{job_id}] Converting PDF ({len(pdf_bytes)} bytes) via Datalab"
            )

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
            (pipeline_articles_dir / f"{pmid}.md").write_text(
                markdown, encoding="utf-8"
            )
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
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    f"[{job_id}] Failed to remove staged temp PDF {temp_path}: {exc}"
                )
