"""
PostgreSQL database module for autogkb-api.

Provides a synchronous psycopg2 connection pool and helper functions for
managing annotation_jobs records. The table is created automatically on import
via init_db().
"""

import contextlib
from datetime import datetime, timedelta, timezone
import json
import os
import uuid

import psycopg2
import psycopg2.pool
import psycopg2.extras
from loguru import logger

# ---------------------------------------------------------------------------
# Connection pool setup
# ---------------------------------------------------------------------------

_DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

pool: psycopg2.pool.ThreadedConnectionPool | None = None

if _DATABASE_URL is None:
    logger.warning(
        "DATABASE_URL environment variable is not set. "
        "Database functionality will be unavailable."
    )
else:
    try:
        pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=_DATABASE_URL,
        )
        logger.info("PostgreSQL connection pool created (min=1, max=5).")
    except Exception as exc:  # pragma: no cover
        logger.error(f"Failed to create PostgreSQL connection pool: {exc}")
        pool = None


def _require_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Raise a clear RuntimeError if the pool was never initialised."""
    if pool is None:
        raise RuntimeError(
            "DATABASE_URL not configured. "
            "Set the DATABASE_URL environment variable before starting the server."
        )
    return pool


@contextlib.contextmanager
def _get_conn():
    """Yield a live connection from the pool.

    Proactively replaces connections killed by Railway's idle timeout
    (SSL SYSCALL error / EOF) so callers never see a dead connection.
    On exit, evicts broken connections instead of returning them to the pool.
    """
    p = _require_pool()
    conn = p.getconn()
    try:
        # Evict connections that the server closed while they were idle.
        if conn.closed != 0:
            p.putconn(conn, close=True)
            conn = p.getconn()
        yield conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # Connection died mid-operation — discard it so the pool stays healthy.
        with contextlib.suppress(Exception):
            p.putconn(conn, close=True)
        conn = None
        raise
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                p.putconn(conn, close=conn.closed != 0)


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS annotation_jobs (
    id                  UUID        PRIMARY KEY,
    pmid                TEXT        NOT NULL,
    pmcid               TEXT,
    source              TEXT        NOT NULL DEFAULT 'pmc',
    status              TEXT        NOT NULL DEFAULT 'pending',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    progress            TEXT,
    markdown_content    TEXT,
    error               TEXT,
    title               TEXT,
    json_content        JSONB       DEFAULT '{}',
    generation_metadata JSONB       DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_annotation_jobs_pmid ON annotation_jobs(pmid);
CREATE INDEX IF NOT EXISTS idx_annotation_jobs_pmcid ON annotation_jobs(pmcid);
"""

# Ensure API-created rows can coexist with generation/sync.py rows.
# Safe to run repeatedly (all statements are idempotent).
_MIGRATE_SQL = """
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'pmc';
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS progress            TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS markdown_content    TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS error               TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS pmid                TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS pmcid               TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS title               TEXT;
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS json_content        JSONB DEFAULT '{}';
ALTER TABLE annotation_jobs ADD COLUMN IF NOT EXISTS generation_metadata JSONB DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_annotation_jobs_pmid ON annotation_jobs(pmid);
"""

_CREATE_TRIAGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS triage_sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  TEXT        NOT NULL,
    project_name TEXT       NOT NULL,
    week_date   DATE        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'pending',
    articles    JSONB       NOT NULL DEFAULT '[]',
    error       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_triage_sessions_project_id ON triage_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_triage_sessions_week_date ON triage_sessions(week_date);
"""

_CREATE_TRIAGE_TAG_EDITS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS triage_tag_edits (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID        NOT NULL REFERENCES triage_sessions(id) ON DELETE CASCADE,
    pmid          TEXT        NOT NULL,
    action        TEXT        NOT NULL,
    previous_tags JSONB       NOT NULL,
    new_tags      JSONB       NOT NULL,
    edited_by     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_triage_tag_edits_session_pmid
    ON triage_tag_edits(session_id, pmid, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_triage_tag_edits_pmid
    ON triage_tag_edits(pmid, created_at DESC);
"""


def init_db() -> None:
    """Create the annotation_jobs table and index if they do not already exist.

    Idempotent — safe to call on every startup.
    Raises RuntimeError if DATABASE_URL is not configured.
    """
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE_SQL)
                cur.execute(_MIGRATE_SQL)
                cur.execute(_CREATE_TRIAGE_TABLE_SQL)
                cur.execute(_CREATE_TRIAGE_TAG_EDITS_TABLE_SQL)
            conn.commit()
            logger.info("Database schema initialised (annotation_jobs table ready).")
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(f"init_db() failed: {exc}")
            raise


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _extract_annotation_data(row: dict) -> dict:
    """Build annotation_data from json_content (generation/sync.py schema).

    json_content = {
        "annotations":          {variant: [{"sentence": ..., "explanation": ...}]},
        "annotation_citations": [{"variant": ..., "sentence": ..., "explanation": ..., "citations": [...]}],
        "annotation_data":      {"pmcid": ..., "summary": ..., ...},
    }

    Populates row["annotation_data"] with {"result": {"pmcid", "variants", "associations", "summary"}}
    so the extension can read job.annotation_data.result.associations.
    """
    if not row.get("json_content"):
        return row
    try:
        jc = (
            json.loads(row["json_content"])
            if isinstance(row["json_content"], str)
            else row["json_content"]
        )
        ann = jc.get("annotation_data") or {}
        citations = jc.get("annotation_citations") or []
        associations = [
            {
                "variant_id": c.get("variant", ""),
                "sentence": c.get("sentence", ""),
                "explanation": c.get("explanation", ""),
                "citations": c.get("citations", []),
            }
            for c in citations
        ]
        row["annotation_data"] = {
            "result": {
                "pmcid": ann.get("pmcid", row.get("pmcid", row.get("pmid", ""))),
                "variants": list((jc.get("annotations") or {}).keys()),
                "associations": associations,
                "summary": ann.get("summary", ""),
                # Empty for methods that only produce display sentences.
                "structured_associations": ann.get("structured_associations")
                or [],
            }
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return row


def create_job(
    pmid: str,
    *,
    pmcid: str | None = None,
    source: str = "pmc",
    method: str | None = None,
    title: str | None = None,
) -> str:
    """Insert a new analysis job with status='pending'.

    ``method`` is recorded straight away so a run that is still in flight can be
    found by the annotation method it will use.

    Returns the new job's UUID as a string.
    """
    job_id = str(uuid.uuid4())
    metadata = {"annotation_method": method} if method else {}
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO annotation_jobs
                        (id, pmid, pmcid, source, status, title, generation_metadata,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'pending', %s, %s, NOW(), NOW())
                    """,
                    (job_id, pmid, pmcid, source, title, json.dumps(metadata)),
                )
            conn.commit()
            logger.debug(f"Created job {job_id} for pmid={pmid}, source={source}.")
            return job_id
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(f"create_job() failed for pmid={pmid}: {exc}")
            raise


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: str | None = None,
    pmid: str | None = None,
    title: str | None = None,
    markdown_content: str | None = None,
    json_content: dict | None = None,
    generation_metadata: dict | None = None,
    error: str | None = None,
) -> None:
    """Update fields on an existing analysis job.

    Only fields explicitly passed (non-None) are written.  updated_at is
    always refreshed to NOW().

    Raises RuntimeError if DATABASE_URL is not configured.
    Raises ValueError if no fields are provided to update.
    """
    _require_pool()

    # Build the SET clause dynamically — only include provided fields.
    set_clauses: list[str] = ["updated_at = NOW()"]
    params: list = []

    if status is not None:
        set_clauses.append("status = %s")
        params.append(status)
    if progress is not None:
        set_clauses.append("progress = %s")
        params.append(progress)
    if pmid is not None:
        set_clauses.append("pmid = %s")
        params.append(pmid)
    if title is not None:
        set_clauses.append("title = %s")
        params.append(title)
    if markdown_content is not None:
        set_clauses.append("markdown_content = %s")
        params.append(markdown_content)
    if json_content is not None:
        set_clauses.append("json_content = %s")
        params.append(json.dumps(json_content))
    if generation_metadata is not None:
        set_clauses.append("generation_metadata = %s")
        params.append(json.dumps(generation_metadata))
    if error is not None:
        set_clauses.append("error = %s")
        params.append(error)
    elif status != "error":
        set_clauses.append("error = NULL")

    if len(set_clauses) == 1:
        # Only updated_at would be set — no real update requested.
        raise ValueError("update_job() called with no fields to update.")

    params.append(job_id)
    sql = f"UPDATE annotation_jobs SET {', '.join(set_clauses)} WHERE id = %s"

    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
            logger.debug(f"Updated job {job_id}: {set_clauses}.")
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(f"update_job() failed for job_id={job_id}: {exc}")
            raise


def get_job(job_id: str) -> dict | None:
    """Return the analysis job row as a dict, or None if not found.

    Raises RuntimeError if DATABASE_URL is not configured.
    """
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM annotation_jobs WHERE id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return _extract_annotation_data(dict(row))
        except Exception as exc:
            logger.error(f"get_job() failed for job_id={job_id}: {exc}")
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()  # close implicit read transaction


def get_job_by_pmid(pmid: str) -> dict | None:
    """Return the most recent job for a given pmid, or None.

    Prefers completed jobs; falls back to the latest job of any status.
    """
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                      FROM annotation_jobs
                     WHERE pmid = %s
                     ORDER BY
                       CASE status WHEN 'completed' THEN 0 ELSE 1 END,
                       created_at DESC
                     LIMIT 1
                    """,
                    (pmid,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return _extract_annotation_data(dict(row))
        except Exception as exc:
            logger.error(f"get_job_by_pmid() failed for pmid={pmid}: {exc}")
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def get_job_by_pmcid(pmcid: str) -> dict | None:
    """Return the most recent job for a given pmcid, or None.

    Prefers completed jobs; falls back to the latest job of any status so
    callers can see in-progress or failed jobs rather than a 404.

    Raises RuntimeError if DATABASE_URL is not configured.
    """
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                      FROM annotation_jobs
                     WHERE pmcid = %s
                     ORDER BY
                       CASE status WHEN 'completed' THEN 0 ELSE 1 END,
                       created_at DESC
                     LIMIT 1
                    """,
                    (pmcid,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return _extract_annotation_data(dict(row))
        except Exception as exc:
            logger.error(f"get_job_by_pmcid() failed for pmcid={pmcid}: {exc}")
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()  # close implicit read transaction


# A run that has not been touched for this long is treated as abandoned (for
# example after a restart) rather than as still in flight.
_IN_FLIGHT_WINDOW = "30 minutes"
_IN_FLIGHT_MAX_AGE = timedelta(minutes=30)


def is_job_in_flight(job: dict | None) -> bool:
    """Return True when ``job`` represents an active, non-abandoned in-flight run."""
    if not job:
        return False
    status = job.get("status")
    if not status or status in ("completed", "failed", "error"):
        return False
    updated_at = job.get("updated_at")
    if updated_at is None:
        return True
    if isinstance(updated_at, str):
        try:
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            return True
    if isinstance(updated_at, datetime):
        now = (
            datetime.now(timezone.utc)
            if updated_at.tzinfo is not None
            else datetime.now()
        )
        return (now - updated_at) <= _IN_FLIGHT_MAX_AGE
    return True


def get_latest_active_or_completed_jobs_for_pmids(
    pmids: list[str],
) -> dict[str, dict]:
    """Return the active in-flight (preferred) or latest completed job per PMID."""
    unique_pmids = list(dict.fromkeys(str(p).strip() for p in pmids if str(p).strip()))
    if not unique_pmids:
        return {}
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT DISTINCT ON (pmid)
                           id,
                           pmid,
                           pmcid,
                           source,
                           status,
                           progress,
                           markdown_content,
                           created_at,
                           updated_at
                      FROM annotation_jobs
                     WHERE pmid = ANY(%s)
                       AND (
                             status = 'completed'
                             OR (
                                 status NOT IN ('failed', 'error')
                                 AND updated_at > NOW() - INTERVAL '{_IN_FLIGHT_WINDOW}'
                             )
                           )
                     ORDER BY
                           pmid,
                           CASE WHEN status != 'completed' THEN 0 ELSE 1 END,
                           created_at DESC NULLS LAST
                    """,
                    (unique_pmids,),
                )
                return {str(row["pmid"]): dict(row) for row in cur.fetchall()}
        except Exception as exc:
            logger.error(
                f"get_latest_active_or_completed_jobs_for_pmids() failed: {exc}"
            )
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def get_latest_active_or_completed_job_for_pmcid(pmcid: str) -> dict | None:
    """Return the active in-flight (preferred) or latest completed job for a PMCID."""
    clean_pmcid = str(pmcid).strip().upper()
    if not clean_pmcid:
        return None
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT id,
                           pmid,
                           pmcid,
                           source,
                           status,
                           progress,
                           markdown_content,
                           created_at,
                           updated_at
                      FROM annotation_jobs
                     WHERE UPPER(pmcid) = %s
                       AND (
                             status = 'completed'
                             OR (
                                 status NOT IN ('failed', 'error')
                                 AND updated_at > NOW() - INTERVAL '{_IN_FLIGHT_WINDOW}'
                             )
                           )
                     ORDER BY
                           CASE WHEN status != 'completed' THEN 0 ELSE 1 END,
                           created_at DESC NULLS LAST
                     LIMIT 1
                    """,
                    (clean_pmcid,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as exc:
            logger.error(
                f"get_latest_active_or_completed_job_for_pmcid() failed for pmcid={clean_pmcid}: {exc}"
            )
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def find_run_by_method(
    method: str, *, pmid: str | None = None, pmcid: str | None = None
) -> dict | None:
    """Return the newest completed or in-flight run of ``method`` for an article.

    Runs stored before methods were named carry only
    ``sentence_generation_method`` and count as ``sentences_v5``.
    """
    if not pmid and not pmcid:
        return None
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT *
                      FROM annotation_jobs
                     WHERE (pmid = %s OR pmcid = %s)
                       AND (
                             generation_metadata->>'annotation_method' = %s
                             OR (
                                 %s = 'sentences_v5'
                                 AND generation_metadata->>'annotation_method' IS NULL
                                 AND generation_metadata->>'sentence_generation_method'
                                     IS NOT NULL
                             )
                           )
                       AND (
                             status = 'completed'
                             OR (
                                 status NOT IN ('failed', 'error')
                                 AND updated_at > NOW() - INTERVAL '{_IN_FLIGHT_WINDOW}'
                             )
                           )
                     ORDER BY created_at DESC
                     LIMIT 1
                    """,
                    (pmid, pmcid, method, method),
                )
                row = cur.fetchone()
            return _extract_annotation_data(dict(row)) if row else None
        except Exception as exc:
            logger.error(
                f"find_run_by_method() failed for pmid={pmid}, pmcid={pmcid}: {exc}"
            )
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def list_runs_by_pmid(pmid: str) -> list[dict]:
    """Return every job stored for a pmid, newest first, without article bodies."""
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, status, created_at, updated_at, generation_metadata
                      FROM annotation_jobs
                     WHERE pmid = %s
                     ORDER BY created_at DESC
                    """,
                    (pmid,),
                )
                return [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            logger.error(f"list_runs_by_pmid() failed for pmid={pmid}: {exc}")
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def list_articles() -> list[dict]:
    """Return active in-flight and completed articles, with in-flight jobs first."""
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT job_id,
                           pmid,
                           pmcid,
                           source,
                           status,
                           progress,
                           title,
                           summary
                      FROM (
                            SELECT DISTINCT ON (pmid)
                                   id::text AS job_id,
                                   pmid,
                                   pmcid,
                                   source,
                                   status,
                                   progress,
                                   title,
                                   json_content->'annotation_data'->>'summary' AS summary,
                                   created_at
                              FROM annotation_jobs
                             WHERE status = 'completed'
                                OR (
                                    status NOT IN ('failed', 'error')
                                    AND updated_at > NOW() - INTERVAL '{_IN_FLIGHT_WINDOW}'
                                )
                             ORDER BY
                                   pmid,
                                   CASE WHEN status != 'completed' THEN 0 ELSE 1 END,
                                   created_at DESC NULLS LAST
                           ) latest
                     ORDER BY
                           CASE WHEN status != 'completed' THEN 0 ELSE 1 END,
                           CASE WHEN status != 'completed' THEN created_at END DESC NULLS LAST,
                           pmid
                    """
                )
                return [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            logger.error(f"list_articles() failed: {exc}")
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


# ---------------------------------------------------------------------------
# Triage session helpers
# ---------------------------------------------------------------------------

UPLOADED_PDFS_PROJECT_ID = "uploaded_pdfs"
UPLOADED_PDFS_PROJECT_NAME = "Uploaded PDFs"


def create_triage_session(project_id: str, project_name: str, week_date: str) -> str:
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO triage_sessions (project_id, project_name, week_date, status, articles)
                    VALUES (%s, %s, %s, 'pending', '[]')
                    RETURNING id
                    """,
                    (project_id, project_name, week_date),
                )
                session_id = str(cur.fetchone()[0])
            conn.commit()
            logger.debug(
                f"Created triage session {session_id} for project={project_id}."
            )
            return session_id
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(
                f"create_triage_session() failed for project_id={project_id}: {exc}"
            )
            raise


def get_triage_session(session_id: str) -> dict | None:
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM triage_sessions WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            return dict(row)
        except Exception as exc:
            logger.error(
                f"get_triage_session() failed for session_id={session_id}: {exc}"
            )
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def find_triage_session_by_week(project_id: str, week_date: str) -> dict | None:
    """Return the existing session for (project_id, week_date), or None."""
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM triage_sessions WHERE project_id = %s AND week_date = %s LIMIT 1",
                    (project_id, week_date),
                )
                row = cur.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            logger.error(f"find_triage_session_by_week() failed: {exc}")
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def list_triage_sessions() -> list[dict]:
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, project_id, project_name, week_date, status, created_at,
                           jsonb_array_length(articles) AS article_count
                      FROM triage_sessions
                     ORDER BY created_at DESC
                    """
                )
                rows = cur.fetchall()
            result = []
            for row in rows:
                r = dict(row)
                r["week_date"] = str(r["week_date"])
                result.append(r)
            return result
        except Exception as exc:
            logger.error(f"list_triage_sessions() failed: {exc}")
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def update_triage_session_status(
    session_id: str, status: str, error: str | None = None
) -> None:
    set_clauses = ["status = %s"]
    params: list = [status]
    if error is not None:
        set_clauses.append("error = %s")
        params.append(error)
    params.append(session_id)
    sql = f"UPDATE triage_sessions SET {', '.join(set_clauses)} WHERE id = %s"
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
            logger.debug(f"Updated triage session {session_id} status={status}.")
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(
                f"update_triage_session_status() failed for session_id={session_id}: {exc}"
            )
            raise


def update_triage_session_articles(session_id: str, articles: list[dict]) -> None:
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE triage_sessions SET articles = %s WHERE id = %s",
                    (json.dumps(articles), session_id),
                )
            conn.commit()
            logger.debug(f"Updated articles for triage session {session_id}.")
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(
                f"update_triage_session_articles() failed for session_id={session_id}: {exc}"
            )
            raise


def update_triage_article_decision(
    session_id: str, pmid: str, decision: str, job_id: str | None = None
) -> None:
    patch = json.dumps({"decision": decision, "job_id": job_id})
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE triage_sessions
                       SET articles = (
                           SELECT jsonb_agg(
                               CASE WHEN elem->>'pmid' = %s
                                    THEN elem || %s::jsonb
                                    ELSE elem
                               END
                           )
                           FROM jsonb_array_elements(articles) AS elem
                       )
                     WHERE id = %s
                    """,
                    (pmid, patch, session_id),
                )
            conn.commit()
            logger.debug(
                f"Updated article decision pmid={pmid} in session {session_id}."
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(
                f"update_triage_article_decision() failed for session_id={session_id}, pmid={pmid}: {exc}"
            )
            raise


_TAG_REGEN_MAX_AGE = timedelta(minutes=10)


class TriageArticleBusyError(Exception):
    """Raised when a triage article cannot be modified while tags are regenerating."""


def is_tag_regeneration_in_flight(article: dict | None) -> bool:
    """Return True when single-article tag regeneration is actively in flight."""
    if not article or not article.get("tags_regenerating"):
        return False
    started_at = article.get("tags_regenerating_started_at")
    if not started_at:
        return True
    if isinstance(started_at, str):
        try:
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError:
            return True
    if isinstance(started_at, datetime):
        now = (
            datetime.now(timezone.utc)
            if started_at.tzinfo is not None
            else datetime.now()
        )
        return (now - started_at) <= _TAG_REGEN_MAX_AGE
    return True


def _parse_jsonb_dict(value: object) -> dict:
    """Return a dict from a JSONB value that may be a dict or JSON string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def _extract_triage_tag_snapshot(article: dict) -> dict:
    """Return a normalized snapshot of the editable triage tag fields."""
    return {
        "paper_types": list(article.get("paper_types") or []),
        "pgx_information_types": list(article.get("pgx_information_types") or []),
        "drug_tags": list(article.get("drug_tags") or []),
        "gene_tags": list(article.get("gene_tags") or []),
        "phenotype_tags": list(article.get("phenotype_tags") or []),
    }


def _compute_original_tag_baselines(rows: list[dict]) -> dict[str, dict]:
    """Derive each PMID's AI-generated baseline tag snapshot from chronological edits.

    Before any single-article regeneration, the earliest edit row's ``previous_tags``
    holds the original AI-generated annotations. After a ``regenerate`` action,
    its ``new_tags`` becomes the latest AI-generated baseline.
    """
    baselines: dict[str, dict] = {}
    for row in rows:
        pmid_key = str(row.get("pmid") or "").strip()
        if not pmid_key:
            continue
        if row.get("action") == "regenerate":
            baselines[pmid_key] = _extract_triage_tag_snapshot(
                _parse_jsonb_dict(row.get("new_tags"))
            )
        elif pmid_key not in baselines:
            baselines[pmid_key] = _extract_triage_tag_snapshot(
                _parse_jsonb_dict(row.get("previous_tags"))
            )
    return baselines


def article_can_revert_tags(article: dict, baseline: dict | None) -> bool:
    """Return True when ``article`` currently differs from its saved AI baseline."""
    if baseline is None:
        return False
    return _extract_triage_tag_snapshot(article) != baseline


def _is_valid_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def get_session_original_tag_baselines(session_id: str) -> dict[str, dict]:
    """Return ``{pmid: baseline_tag_snapshot}`` for articles with tag edit provenance."""
    if not _is_valid_uuid(session_id):
        return {}
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT pmid, action, previous_tags, new_tags
                      FROM triage_tag_edits
                     WHERE session_id = %s
                     ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                rows = [dict(row) for row in cur.fetchall()]
            return _compute_original_tag_baselines(rows)
        except Exception as exc:
            logger.error(
                f"get_session_original_tag_baselines() failed for session_id={session_id}: {exc}"
            )
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def get_article_original_tag_baseline(session_id: str, pmid: str) -> dict | None:
    """Return the saved AI-generated baseline tag snapshot for one article, or None."""
    if not _is_valid_uuid(session_id):
        return None
    clean_pmid = str(pmid).strip()
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT pmid, action, previous_tags, new_tags
                      FROM triage_tag_edits
                     WHERE session_id = %s AND pmid = %s
                     ORDER BY created_at ASC
                    """,
                    (session_id, clean_pmid),
                )
                rows = [dict(row) for row in cur.fetchall()]
            return _compute_original_tag_baselines(rows).get(clean_pmid)
        except Exception as exc:
            logger.error(
                f"get_article_original_tag_baseline() failed for session_id={session_id}, pmid={clean_pmid}: {exc}"
            )
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()


def revert_triage_article_tags(
    session_id: str,
    pmid: str,
    *,
    edited_by: str | None = None,
) -> tuple[dict | None, bool]:
    """Restore an article's tags to its saved original AI-generated snapshot.

    Returns ``(updated_article, had_history)``. Raises ``TriageArticleBusyError``
    if a background tag regeneration is currently in flight for this article.
    """
    clean_pmid = str(pmid).strip()
    lock_key = f"triage_session:{session_id}"
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
                cur.execute(
                    "SELECT articles FROM triage_sessions WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None, False

                articles = list(row["articles"] or [])
                target_idx: int | None = None
                previous_article: dict | None = None
                for idx, existing in enumerate(articles):
                    if str(existing.get("pmid") or "") == clean_pmid:
                        target_idx = idx
                        previous_article = dict(existing)
                        break

                if target_idx is None or previous_article is None:
                    return None, False

                if is_tag_regeneration_in_flight(previous_article):
                    raise TriageArticleBusyError(
                        "Tags are currently being regenerated for this article"
                    )

                cur.execute(
                    """
                    SELECT pmid, action, previous_tags, new_tags
                      FROM triage_tag_edits
                     WHERE session_id = %s AND pmid = %s
                     ORDER BY created_at ASC
                    """,
                    (session_id, clean_pmid),
                )
                edit_rows = [dict(edit_row) for edit_row in cur.fetchall()]
                baseline = _compute_original_tag_baselines(edit_rows).get(clean_pmid)
                if baseline is None:
                    return previous_article, False

                updated_article = {**previous_article, **baseline}
                articles[target_idx] = updated_article

                cur.execute(
                    "UPDATE triage_sessions SET articles = %s WHERE id = %s",
                    (json.dumps(articles), session_id),
                )

                previous_tags = _extract_triage_tag_snapshot(previous_article)
                new_tags = _extract_triage_tag_snapshot(updated_article)
                if previous_tags != new_tags:
                    cur.execute(
                        """
                        INSERT INTO triage_tag_edits
                            (session_id, pmid, action, previous_tags, new_tags, edited_by)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            session_id,
                            clean_pmid,
                            "revert",
                            json.dumps(previous_tags),
                            json.dumps(new_tags),
                            edited_by,
                        ),
                    )
            conn.commit()
            logger.debug(
                f"Reverted tags for pmid={clean_pmid} in session {session_id}."
            )
            return updated_article, True
        except TriageArticleBusyError:
            with contextlib.suppress(Exception):
                conn.rollback()
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(
                f"revert_triage_article_tags() failed for session_id={session_id}, pmid={clean_pmid}: {exc}"
            )
            raise


def update_triage_article_fields(
    session_id: str,
    pmid: str,
    patch: dict,
    *,
    edit_action: str | None = None,
    edited_by: str | None = None,
) -> dict | None:
    """Merge ``patch`` into the matching article inside a triage session.

    Uses a transaction-scoped advisory lock per session to avoid lost updates
    from concurrent tag edits or regenerations. When ``edit_action`` is
    provided and the tag snapshot changes (or when ``edit_action == "regenerate"``),
    records a before/after audit row in ``triage_tag_edits`` within the same
    transaction. Returns the updated article dict, or ``None`` if the session
    or PMID does not exist.
    """
    clean_pmid = str(pmid).strip()
    lock_key = f"triage_session:{session_id}"
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
                cur.execute(
                    "SELECT articles FROM triage_sessions WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None

                articles = list(row["articles"] or [])
                previous_article: dict | None = None
                updated_article: dict | None = None
                for idx, existing in enumerate(articles):
                    if str(existing.get("pmid") or "") == clean_pmid:
                        previous_article = dict(existing)
                        updated_article = {**previous_article, **patch}
                        articles[idx] = updated_article
                        break

                if updated_article is None or previous_article is None:
                    return None

                cur.execute(
                    "UPDATE triage_sessions SET articles = %s WHERE id = %s",
                    (json.dumps(articles), session_id),
                )

                if edit_action is not None:
                    previous_tags = _extract_triage_tag_snapshot(previous_article)
                    new_tags = _extract_triage_tag_snapshot(updated_article)
                    if previous_tags != new_tags or edit_action == "regenerate":
                        cur.execute(
                            """
                            INSERT INTO triage_tag_edits
                                (session_id, pmid, action, previous_tags, new_tags, edited_by)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                session_id,
                                clean_pmid,
                                edit_action,
                                json.dumps(previous_tags),
                                json.dumps(new_tags),
                                edited_by,
                            ),
                        )
            conn.commit()
            logger.debug(
                f"Updated article fields for pmid={clean_pmid} in session {session_id}."
            )
            return updated_article
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(
                f"update_triage_article_fields() failed for session_id={session_id}, pmid={clean_pmid}: {exc}"
            )
            raise


def sync_triage_sessions_for_pmid(pmid: str, job_id: str) -> None:
    """Mark any matching triage articles for ``pmid`` as submitted with ``job_id``."""
    patch = json.dumps({"decision": "submitted", "job_id": job_id})
    target = json.dumps([{"pmid": pmid}])
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE triage_sessions
                       SET articles = (
                           SELECT jsonb_agg(
                               CASE WHEN elem->>'pmid' = %s
                                    THEN elem || %s::jsonb
                                    ELSE elem
                               END
                           )
                           FROM jsonb_array_elements(articles) AS elem
                       )
                     WHERE articles @> %s::jsonb
                    """,
                    (pmid, patch, target),
                )
            conn.commit()
            logger.debug(
                f"Synced triage sessions for pmid={pmid} with job_id={job_id}."
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(
                f"sync_triage_sessions_for_pmid() failed for pmid={pmid}: {exc}"
            )
            raise


def upsert_uploaded_pdf_triage_article(month_date: str, article: dict) -> str:
    """Upsert an uploaded PDF article into the monthly 'Uploaded PDFs' session.

    Uses a transaction-scoped PostgreSQL advisory lock keyed by ``month_date``
    so concurrent bulk uploads and background triage-scoring completions never
    create duplicate monthly sessions or clobber each other's JSONB updates.
    """
    pmid = str(article.get("pmid") or "").strip()
    if not pmid:
        raise ValueError("article must include a non-empty pmid")

    lock_key = f"{UPLOADED_PDFS_PROJECT_ID}:{month_date}"
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
                cur.execute(
                    """
                    SELECT id, articles
                      FROM triage_sessions
                     WHERE project_id = %s AND week_date = %s
                     ORDER BY created_at ASC
                     LIMIT 1
                    """,
                    (UPLOADED_PDFS_PROJECT_ID, month_date),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        """
                        INSERT INTO triage_sessions
                            (project_id, project_name, week_date, status, articles)
                        VALUES (%s, %s, %s, 'completed', '[]')
                        RETURNING id, articles
                        """,
                        (
                            UPLOADED_PDFS_PROJECT_ID,
                            UPLOADED_PDFS_PROJECT_NAME,
                            month_date,
                        ),
                    )
                    row = cur.fetchone()

                session_id = str(row["id"])
                articles = list(row["articles"] or [])

                existing_idx = next(
                    (
                        idx
                        for idx, existing in enumerate(articles)
                        if str(existing.get("pmid") or "") == pmid
                    ),
                    None,
                )

                if existing_idx is not None:
                    existing = dict(articles[existing_idx])
                    if (
                        article.get("triage_label") == "scoring"
                        and existing.get("triage_label")
                        in ("relevant", "borderline", "not_relevant")
                    ):
                        # Re-uploading an already scored paper: keep its score/tags
                        # while updating job linkage and any newly resolved fields.
                        merged = {
                            **existing,
                            "decision": "submitted",
                            "job_id": article.get("job_id") or existing.get("job_id"),
                            "pmcid": article.get("pmcid") or existing.get("pmcid"),
                            "title": existing.get("title") or article.get("title"),
                        }
                    else:
                        merged = {
                            **existing,
                            **article,
                            "decision": "submitted",
                            "job_id": article.get("job_id") or existing.get("job_id"),
                            "pmcid": article.get("pmcid") or existing.get("pmcid"),
                            "title": article.get("title") or existing.get("title"),
                        }
                    articles[existing_idx] = merged
                else:
                    articles.insert(0, article)

                cur.execute(
                    "UPDATE triage_sessions SET articles = %s, status = 'completed' WHERE id = %s",
                    (json.dumps(articles), session_id),
                )
            conn.commit()
            return session_id
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(
                f"upsert_uploaded_pdf_triage_article() failed for month={month_date}, pmid={pmid}: {exc}"
            )
            raise
