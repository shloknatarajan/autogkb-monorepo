"""Sync generations.jsonl with Railway Postgres (annotation_jobs table).

Push (local → DB):
    python -m generation.sync push              # upsert local records into DB
    python -m generation.sync push --override   # replace all DB rows with local data

Pull (DB → local):
    python -m generation.sync pull              # upsert DB records into local JSONL
    python -m generation.sync pull --override   # replace local JSONL with DB data
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from sqlalchemy import create_engine, text

from generation.models import GenerationRecord, GenerationStatus
from generation.pipeline import GENERATIONS_JSONL, _save_generation_file

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
TABLE = "annotation_jobs"


def _engine():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set in environment")
    return create_engine(DATABASE_URL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_local_records() -> list[GenerationRecord]:
    """Read all records from the local generations.jsonl."""
    if not GENERATIONS_JSONL.exists():
        return []
    records = []
    for line_no, line in enumerate(
        GENERATIONS_JSONL.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            records.append(GenerationRecord.model_validate(json.loads(line)))
        except json.JSONDecodeError as e:
            snippet = line.strip()
            if len(snippet) > 400:
                snippet = snippet[:400] + "…"
            raise RuntimeError(
                "Invalid JSON in local generations file "
                f"({GENERATIONS_JSONL}) at line {line_no}: {e}. "
                f"Line snippet: {snippet}"
            ) from e
    return records


def _write_local_records(records: list[GenerationRecord]) -> None:
    """Overwrite the local generations.jsonl with the given records."""
    GENERATIONS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(GENERATIONS_JSONL, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(rec.model_dump_json() + "\n")


def _record_to_row(rec: GenerationRecord) -> dict:
    """Convert a GenerationRecord to a dict matching DB columns."""
    json_content = {
        "annotations": rec.annotations,
        "annotation_citations": rec.annotation_citations,
        "annotation_data": rec.annotation_data,
    }
    return {
        "id": rec.id,
        "pmid": rec.pmid,
        "pmcid": rec.pmcid,
        "title": rec.title,
        "status": rec.status.value,
        "markdown_content": rec.text_content,
        "json_content": json.dumps(json_content),
        "generation_metadata": json.dumps(rec.generation_metadata.model_dump()),
        "error": rec.error,
        "created_at": rec.timestamp,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _row_to_record(row: dict) -> GenerationRecord:
    """Convert a DB row dict to a GenerationRecord."""
    json_content = row.get("json_content") or {}
    if isinstance(json_content, str):
        json_content = json.loads(json_content)

    annotations = json_content.get("annotations") or {}
    annotation_citations = json_content.get("annotation_citations") or []
    annotation_data = json_content.get("annotation_data")

    generation_metadata = row.get("generation_metadata")
    if isinstance(generation_metadata, str):
        generation_metadata = json.loads(generation_metadata)

    # If generation_metadata is missing (old DB rows), build a minimal one
    if not generation_metadata:
        generation_metadata = {
            "config_name": "unknown",
            "variant_extraction_method": "unknown",
            "elapsed_seconds": 0.0,
            "git_sha": "unknown",
            "stages_run": [],
        }

    return GenerationRecord(
        id=str(row["id"]),
        pmid=row.get("pmid"),
        pmcid=row["pmcid"],
        title=row.get("title"),
        text_content=row.get("markdown_content") or "",
        annotations=annotations,
        annotation_citations=annotation_citations,
        annotation_data=annotation_data,
        status=GenerationStatus(row.get("status", "completed")),
        error=row.get("error"),
        generation_metadata=generation_metadata,
        timestamp=str(row.get("created_at") or datetime.now(timezone.utc).isoformat()),
    )


# ---------------------------------------------------------------------------
# Push (local → DB)
# ---------------------------------------------------------------------------

_UPSERT_SQL = text(
    f"""
    INSERT INTO {TABLE}
        (id, pmid, pmcid, title, status, markdown_content,
         json_content, generation_metadata, error, created_at, updated_at)
    VALUES
        (CAST(:id AS uuid), :pmid, :pmcid, :title, :status, :markdown_content,
         CAST(:json_content AS jsonb), CAST(:generation_metadata AS jsonb),
         :error, CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz))
    ON CONFLICT (id) DO UPDATE SET
        pmid = EXCLUDED.pmid,
        pmcid = EXCLUDED.pmcid,
        title = EXCLUDED.title,
        status = EXCLUDED.status,
        markdown_content = EXCLUDED.markdown_content,
        json_content = EXCLUDED.json_content,
        generation_metadata = EXCLUDED.generation_metadata,
        error = EXCLUDED.error,
        updated_at = EXCLUDED.updated_at
"""
)


def push(override: bool = False) -> None:
    """Push local generations.jsonl records to the DB."""
    records = _load_local_records()
    if not records:
        logger.warning("No local records to push")
        return

    engine = _engine()
    with engine.begin() as conn:
        if override:
            conn.execute(text(f"DELETE FROM {TABLE}"))
            logger.info(f"Cleared all rows from {TABLE}")

        for rec in records:
            row = _record_to_row(rec)
            conn.execute(_UPSERT_SQL, row)

    logger.success(
        f"Pushed {len(records)} record(s) to {TABLE}"
        + (" (override)" if override else "")
    )


# ---------------------------------------------------------------------------
# Pull (DB → local)
# ---------------------------------------------------------------------------


def pull(override: bool = False) -> None:
    """Pull records from DB into local generations.jsonl."""
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM {TABLE}")).mappings().all()

    if not rows:
        logger.warning("No rows in DB to pull")
        return

    db_records = [_row_to_record(dict(r)) for r in rows]
    logger.info(f"Fetched {len(db_records)} record(s) from DB")

    if override:
        _write_local_records(db_records)
        new_records = db_records
        logger.success(f"Overwrote local JSONL with {len(db_records)} record(s)")
    else:
        # Merge: DB records win on conflict (by id)
        local_records = _load_local_records()
        local_by_id = {r.id: r for r in local_records}
        new_records = [rec for rec in db_records if rec.id not in local_by_id]
        for rec in db_records:
            local_by_id[rec.id] = rec
        merged = list(local_by_id.values())
        _write_local_records(merged)
        logger.success(
            f"Merged {len(db_records)} DB record(s) into local JSONL "
            f"({len(merged)} total)"
        )

    # Generate markdown files for new/updated entries
    md_count = 0
    for rec in new_records:
        if rec.annotation_data:
            _save_generation_file(rec)
            md_count += 1
    if md_count:
        logger.info(f"Generated {md_count} markdown file(s) in data/generations/")


# ---------------------------------------------------------------------------
# Backup (DB → timestamped file)
# ---------------------------------------------------------------------------

BACKUPS_DIR = Path("data/backups")


def backup() -> None:
    """Dump all DB rows to a timestamped JSONL file in data/backups/."""
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM {TABLE}")).mappings().all()

    if not rows:
        logger.warning("No rows in DB to back up")
        return

    db_records = [_row_to_record(dict(r)) for r in rows]
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = BACKUPS_DIR / f"generations_{ts}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for rec in db_records:
            f.write(rec.model_dump_json() + "\n")
    logger.success(f"Backed up {len(db_records)} record(s) to {path}")


# ---------------------------------------------------------------------------
# Migrate (restructure DB schema)
# ---------------------------------------------------------------------------

_MIGRATE_STMTS = [
    # Consolidate old columns into json_content
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS json_content JSONB",
    f"""UPDATE {TABLE}
SET json_content = jsonb_build_object(
    'annotations', COALESCE(annotations, '{{}}'::jsonb),
    'annotation_citations', COALESCE(annotation_citations, '[]'::jsonb),
    'annotation_data', annotation_data
)
WHERE json_content IS NULL""",
    # Drop legacy columns
    f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS annotations",
    f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS annotation_citations",
    f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS annotation_data",
    f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS progress",
    # Recreate table with correct column order
    f"""CREATE TABLE {TABLE}_new AS
    SELECT id, pmid, pmcid, title, status, markdown_content,
           json_content, generation_metadata, error, created_at, updated_at
    FROM {TABLE}""",
    f"DROP TABLE {TABLE}",
    f"ALTER TABLE {TABLE}_new RENAME TO {TABLE}",
    f"ALTER TABLE {TABLE} ALTER COLUMN id SET NOT NULL",
    f"ALTER TABLE {TABLE} ADD PRIMARY KEY (id)",
]


def migrate() -> None:
    """Migrate DB schema: consolidate columns, drop legacy, reorder to match local."""
    engine = _engine()
    with engine.begin() as conn:
        for stmt in _MIGRATE_STMTS:
            conn.execute(text(stmt))
    logger.success("Migration complete: schema now matches local column order")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Sync generations.jsonl ↔ Railway DB")
    parser.add_argument(
        "direction",
        choices=["push", "pull", "backup", "migrate"],
        help="push (local→DB), pull (DB→local), backup (DB→timestamped file), or migrate",
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Replace all data in the target (DB for push, local for pull)",
    )
    args = parser.parse_args()

    if args.direction == "push":
        push(override=args.override)
    elif args.direction == "pull":
        pull(override=args.override)
    elif args.direction == "backup":
        backup()
    else:
        migrate()


if __name__ == "__main__":
    main()
