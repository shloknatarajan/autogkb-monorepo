"""
PostgreSQL database module for autogkb-api.

Provides a synchronous psycopg2 connection pool and helper functions for
managing annotation_jobs records. The table is created automatically on import
via init_db().
"""

import contextlib
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
            }
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return row


def create_job(pmid: str, *, pmcid: str | None = None, source: str = "pmc") -> str:
    """Insert a new analysis job with status='pending'.

    Returns the new job's UUID as a string.
    """
    job_id = str(uuid.uuid4())
    with _get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO annotation_jobs (id, pmid, pmcid, source, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'pending', NOW(), NOW())
                    """,
                    (job_id, pmid, pmcid, source),
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


def list_articles() -> list[dict]:
    """Return pmid, pmcid, source, and title for all completed jobs."""
    with _get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (pmid)
                           pmid,
                           pmcid,
                           source,
                           title,
                           json_content->'annotation_data'->>'summary' AS summary
                      FROM annotation_jobs
                     WHERE status = 'completed'
                     ORDER BY pmid, created_at DESC NULLS LAST
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
            logger.debug(f"Created triage session {session_id} for project={project_id}.")
            return session_id
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(f"create_triage_session() failed for project_id={project_id}: {exc}")
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
            logger.error(f"get_triage_session() failed for session_id={session_id}: {exc}")
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


def update_triage_session_status(session_id: str, status: str, error: str | None = None) -> None:
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
            logger.error(f"update_triage_session_status() failed for session_id={session_id}: {exc}")
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
            logger.error(f"update_triage_session_articles() failed for session_id={session_id}: {exc}")
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
            logger.debug(f"Updated article decision pmid={pmid} in session {session_id}.")
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            logger.error(
                f"update_triage_article_decision() failed for session_id={session_id}, pmid={pmid}: {exc}"
            )
            raise
