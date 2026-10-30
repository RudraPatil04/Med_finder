"""Short-lived cache for external API lookups so failures are not retried on every request."""

from backend.database import execute_non_query, execute_query

SUCCESS_TTL_DAYS = 30
EMPTY_TTL_DAYS = 1


def ensure_api_search_cache():
    execute_non_query(
        """CREATE TABLE IF NOT EXISTS api_search_cache (
            query_key TEXT PRIMARY KEY,
            searched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            result_count INTEGER NOT NULL DEFAULT 0
        )"""
    )
    columns = {row["name"] for row in execute_query("PRAGMA table_info(api_search_cache)")}
    if "result_count" not in columns:
        execute_non_query("ALTER TABLE api_search_cache ADD COLUMN result_count INTEGER NOT NULL DEFAULT 0")


def has_recent_api_search(query_key, success_ttl_days=None, empty_ttl_days=None):
    success_ttl = SUCCESS_TTL_DAYS if success_ttl_days is None else success_ttl_days
    empty_ttl = EMPTY_TTL_DAYS if empty_ttl_days is None else empty_ttl_days
    row = execute_query(
        "SELECT 1 FROM api_search_cache WHERE query_key = ? "
        "AND searched_at >= CASE WHEN result_count > 0 "
        "THEN datetime('now', ?) ELSE datetime('now', ?) END LIMIT 1",
        (query_key, f"-{success_ttl} days", f"-{empty_ttl} days"),
    )
    return bool(row)


def record_api_search(query_key, result_count):
    execute_non_query(
        "INSERT INTO api_search_cache(query_key, searched_at, result_count) "
        "VALUES (?, CURRENT_TIMESTAMP, ?) "
        "ON CONFLICT(query_key) DO UPDATE SET searched_at = CURRENT_TIMESTAMP, result_count = excluded.result_count",
        (query_key, result_count),
    )
