"""Prometheus metrics in the plain-text exposition format, without a client library.

Job metrics are read from the SQLite table at scrape time, so they include the
worker's work even though the worker is a separate process without an HTTP port.
HTTP request counts live in the API process (reset when it restarts, which
Prometheus handles for counters).
"""

import sqlite3
from collections import Counter
from datetime import UTC, datetime

from app.schemas import JobStatus

PREFIX = "gate_optimizer"
JOB_STATUSES: tuple[JobStatus, ...] = ("queued", "running", "succeeded", "failed")

# (method, route template, status code) -> number of requests.
# The route template ("/jobs/{job_id}") is used, not the URL, so every job id
# does not become a separate time series.
http_requests: Counter[tuple[str, str, int]] = Counter()


def job_lines(conn: sqlite3.Connection, now: datetime) -> list[str]:
    counts = dict.fromkeys(JOB_STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
        counts[row["status"]] = row["n"]

    oldest = conn.execute("SELECT MIN(created_at) FROM jobs WHERE status = 'queued'").fetchone()[0]
    oldest_age = (now - datetime.fromisoformat(oldest)).total_seconds() if oldest else 0.0

    solves = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(solve_time_s), 0) FROM jobs "
        "WHERE status = 'succeeded' AND solve_time_s IS NOT NULL"
    ).fetchone()

    lines = [
        f"# HELP {PREFIX}_jobs Jobs currently in each status.",
        f"# TYPE {PREFIX}_jobs gauge",
    ]
    lines += [f'{PREFIX}_jobs{{status="{s}"}} {n}' for s, n in counts.items()]
    lines += [
        f"# HELP {PREFIX}_oldest_queued_job_age_seconds Wait time of the oldest queued job "
        "(0 if the queue is empty). Keeps growing if no worker is running.",
        f"# TYPE {PREFIX}_oldest_queued_job_age_seconds gauge",
        f"{PREFIX}_oldest_queued_job_age_seconds {oldest_age:.3f}",
        f"# HELP {PREFIX}_solve_seconds Solver time of succeeded jobs.",
        f"# TYPE {PREFIX}_solve_seconds summary",
        f"{PREFIX}_solve_seconds_count {solves[0]}",
        f"{PREFIX}_solve_seconds_sum {solves[1]:.3f}",
    ]
    return lines


def http_lines() -> list[str]:
    lines = [
        f"# HELP {PREFIX}_http_requests_total HTTP requests handled by the API.",
        f"# TYPE {PREFIX}_http_requests_total counter",
    ]
    for (method, path, code), n in sorted(http_requests.items()):
        lines.append(
            f'{PREFIX}_http_requests_total{{method="{method}",path="{path}",code="{code}"}} {n}'
        )
    return lines


def render(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return "\n".join(job_lines(conn, now) + http_lines()) + "\n"
