"""Remember searches that found nothing, so missing medicines can be added later.

Each zero-result search on /api/search is counted in `search_misses`, keyed by
the normalized query. Nothing here may ever break a search: every write is
best-effort and errors are swallowed.

Read the list with:  python -m etl.check_coverage --misses
"""

from datetime import datetime, timezone

from backend import database
from backend.medicine_data import normalize_medication_text

_DDL = """
CREATE TABLE IF NOT EXISTS search_misses (
    query_norm TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    miss_count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
)
"""

MAX_QUERY_LENGTH = 80


def _key(query):
    return normalize_medication_text(query)


def record_miss(query):
    """Count one zero-result search. Returns True if it was stored."""
    query = " ".join((query or "").split())[:MAX_QUERY_LENGTH]
    key = _key(query)
    if len(key) < 3:
        return False
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        connection = database.get_connection()
        try:
            connection.execute(_DDL)
            connection.execute(
                "INSERT INTO search_misses (query_norm, query, miss_count, first_seen, last_seen) "
                "VALUES (?, ?, 1, ?, ?) "
                "ON CONFLICT(query_norm) DO UPDATE SET miss_count = miss_count + 1, "
                "query = excluded.query, last_seen = excluded.last_seen",
                (key, query, now, now),
            )
            connection.commit()
        finally:
            connection.close()
        return True
    except Exception as error:  # never let logging break a search
        print(f"Could not record search miss: {error}")
        return False


def forget_miss(query):
    """Drop a query from the list (for example after the medicine was added)."""
    try:
        connection = database.get_connection()
        try:
            connection.execute(_DDL)
            connection.execute("DELETE FROM search_misses WHERE query_norm = ?", (_key(query),))
            connection.commit()
        finally:
            connection.close()
    except Exception as error:
        print(f"Could not clear search miss: {error}")


def list_misses(limit=200):
    """Most-missed searches first: [{query, miss_count, first_seen, last_seen}]."""
    try:
        connection = database.get_connection()
        try:
            connection.execute(_DDL)
            rows = connection.execute(
                "SELECT query, miss_count, first_seen, last_seen FROM search_misses "
                "ORDER BY miss_count DESC, last_seen DESC LIMIT ?", (int(limit),)
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]
    except Exception as error:
        print(f"Could not read search misses: {error}")
        return []
