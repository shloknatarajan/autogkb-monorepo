"""Load previously scored articles from the triage_sessions PostgreSQL table."""

from __future__ import annotations

import os

import psycopg2
import psycopg2.extras


def load_scores_from_db(database_url: str | None = None) -> dict[str, dict]:
    """Return {pmid: article_dict} for all completed triage sessions.

    Deduplication: when a PMID appears in multiple sessions, the score from
    the most recent session (latest created_at) is kept.
    """
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL must be set (--database-url or env var)")

    conn = psycopg2.connect(url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # ASC order so later rows overwrite earlier ones → most recent session wins
            cur.execute(
                "SELECT articles FROM triage_sessions"
                " WHERE status = 'completed'"
                " ORDER BY created_at ASC"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    scores: dict[str, dict] = {}
    for row in rows:
        for article in row["articles"] or []:
            pmid = article.get("pmid")
            if pmid and article.get("triage_label"):
                scores[pmid] = dict(article)

    return scores
