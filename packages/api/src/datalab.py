"""
Datalab API client for PDF-to-markdown conversion.

Uses the Datalab REST API (https://www.datalab.to/api/v1/) to convert
uploaded PDFs into markdown text.
"""

import os
import time

import requests
from loguru import logger

DATALAB_API_KEY: str | None = os.environ.get("DATALAB_API_KEY")
DATALAB_BASE_URL = "https://www.datalab.to/api/v1"
POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 300  # 5 minutes


def convert_pdf_to_markdown(pdf_bytes: bytes, filename: str = "upload.pdf") -> str:
    """Send a PDF to Datalab and return the converted markdown.

    Blocks until conversion is complete or timeout is reached.

    Args:
        pdf_bytes: Raw PDF file content.
        filename:  Original filename (sent to Datalab for metadata).

    Returns:
        The markdown string produced by Datalab.

    Raises:
        RuntimeError: If the API key is missing, conversion fails, or timeout.
    """
    if not DATALAB_API_KEY:
        raise RuntimeError("DATALAB_API_KEY environment variable is not set.")

    headers = {"X-API-Key": DATALAB_API_KEY}

    # Step 1: Submit the PDF for conversion
    logger.info(f"Submitting PDF ({len(pdf_bytes)} bytes) to Datalab for conversion")
    resp = requests.post(
        f"{DATALAB_BASE_URL}/convert",
        headers=headers,
        files={"file": (filename, pdf_bytes, "application/pdf")},
        data={"output_format": "markdown"},
        timeout=60,
    )
    resp.raise_for_status()
    submit_data = resp.json()

    if not submit_data.get("success", False):
        error = submit_data.get("error", "Unknown error")
        raise RuntimeError(f"Datalab conversion submission failed: {error}")

    request_check_url = submit_data.get("request_check_url")
    request_id = submit_data.get("request_id")
    if not request_check_url:
        raise RuntimeError("Datalab did not return a request_check_url")

    logger.info(f"Datalab conversion submitted, request_id={request_id}")

    # Step 2: Poll for the result
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)

        check_resp = requests.get(
            request_check_url,
            headers=headers,
            timeout=30,
        )
        check_resp.raise_for_status()
        result = check_resp.json()

        status = result.get("status", "")
        if status == "complete":
            markdown = result.get("markdown")
            if not markdown:
                raise RuntimeError("Datalab returned empty markdown")
            logger.info(
                f"Datalab conversion complete for request_id={request_id} "
                f"({len(markdown)} chars)"
            )
            return markdown

        if result.get("success") is False:
            error = result.get("error", "Unknown error")
            raise RuntimeError(f"Datalab conversion failed: {error}")

        logger.debug(f"Datalab conversion in progress (status={status})")

    raise RuntimeError(
        f"Datalab conversion timed out after {POLL_TIMEOUT_SECONDS}s "
        f"for request_id={request_id}"
    )
