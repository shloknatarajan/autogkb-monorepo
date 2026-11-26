"""Download a single PMC article as markdown into data/articles/."""

from pathlib import Path

from loguru import logger

from shared.utils import DATA_DIR


def _resolve_to_pmcid(identifier: str) -> str:
    """If *identifier* looks like a plain PMID, convert it to a PMCID.

    Returns a PMCID string (``PMC…``) or raises ``ValueError``.
    """
    if identifier.upper().startswith("PMC"):
        return identifier  # already a PMCID

    # Treat as a PMID — try conversion
    from shared.data_setup.pmcid_converter import PMIDConverter

    converter = PMIDConverter()
    mapping = converter.convert([identifier], show_progress=False)
    pmcid = mapping.get(identifier)
    if not pmcid:
        raise ValueError(
            f"PMID {identifier} could not be converted to a PMCID "
            "(article may not be in PubMed Central)"
        )
    logger.info(f"Resolved PMID {identifier} → {pmcid}")
    return pmcid


def download_article(identifier: str, data_dir: Path = DATA_DIR) -> Path:
    """Fetch a PMC article as markdown using ``pubmed-markdown`` (XML-first with HTML fallback).

    *identifier* can be a PMCID (``PMC…``) **or** a plain PMID.
    If a PMID is given it is first converted via the NCBI ID Converter API.

    Saves to ``data_dir/articles/{pmcid}.md`` and returns the path.
    Skips if the file already exists.

    Raises:
        ValueError: If a PMID cannot be mapped to a PMCID.
        RuntimeError: If fetching or converting the article fails for the resolved PMCID.
    """
    from pubmed_markdown import PubMedMarkdown

    pmcid = _resolve_to_pmcid(identifier)

    articles_dir = data_dir / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    md_path = articles_dir / f"{pmcid}.md"

    if md_path.exists():
        logger.debug(f"Article already exists: {md_path}")
        return md_path

    logger.info(f"Downloading article {pmcid}...")
    downloader = PubMedMarkdown()
    markdown = downloader.pmcid_to_markdown(pmcid, include_supplements=False)
    if not markdown:
        raise RuntimeError(f"Failed to fetch article markdown for {pmcid}")

    md_path.write_text(markdown, encoding="utf-8")
    logger.info(f"Saved {pmcid} → {md_path}")
    return md_path


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Download a PMC article as markdown. Accepts a PMCID or PMID."
    )
    parser.add_argument(
        "identifier",
        help="PMCID (e.g. PMC10275785) or PMID (e.g. 32948745)",
    )
    args = parser.parse_args()

    try:
        path = download_article(args.identifier)
        print(f"Article saved to {path}")
    except (ValueError, RuntimeError) as e:
        logger.error(str(e))
        sys.exit(1)
