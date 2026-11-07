"""SQLite job store shared by the API (writes jobs) and the worker (runs them).

One row per job. The API inserts rows with status 'queued'; the worker moves
them to 'running' and then to 'succeeded' or 'failed'.
"""

import os
import sqlite3
import uuid
from datetime import UTC, datetime

from app.schemas import JobInfo, JobRequest, JobResult

# Path of the SQLite file, shared by API and worker. Tests point it to a temporary file.
DB_PATH = os.environ.get("JOBS_DB_PATH", "jobs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL,      -- queued / running / succeeded / failed
    instance_name TEXT NOT NULL,
    n_flights     INTEGER NOT NULL,
    n_gates       INTEGER NOT NULL,
    request_json  TEXT NOT NULL,      -- JobRequest: instance + options
    created_at    TEXT NOT NULL,      -- ISO 8601, UTC
    started_at    TEXT,
    finished_at   TEXT,
    solver_status TEXT,               -- optimal / feasible / no_solution
    termination   TEXT,
    objective     REAL,
    mip_gap       REAL,
    solve_time_s  REAL,
    result_json   TEXT,               -- JobResult, only for succeeded jobs
    error         TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs (status, created_at);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)  # wait up to 10 s if the db is locked
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["status"]
    # WAL lets the API read while the worker writes (two processes, one file).
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def create_job(conn: sqlite3.Connection, request: JobRequest) -> str:
    job_id = uuid.uuid4().hex
    with conn:  # commits on success, rolls back on error
        conn.execute(
            "INSERT INTO jobs (id, status, instance_name, n_flights, n_gates, request_json, "
            "created_at) VALUES (?, 'queued', ?, ?, ?, ?, ?)",
            (
                job_id,
                request.instance.name,
                len(request.instance.flights),
                len(request.instance.gates),
                request.model_dump_json(),
                now(),
            ),
        )
    return job_id


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
    ).fetchall()


def claim_next_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Mark the oldest queued job as running and return it, or None if the queue is empty.

    A single UPDATE ... RETURNING statement is atomic, so even if two workers
    run at the same time they can never claim the same job.
    """
    with conn:
        return conn.execute(
            """
            UPDATE jobs SET status = 'running', started_at = ?
            WHERE id = (
                SELECT id FROM jobs WHERE status = 'queued'
                ORDER BY created_at, rowid LIMIT 1
            )
            RETURNING *
            """,
            (now(),),
        ).fetchone()


def requeue_running_jobs(conn: sqlite3.Connection) -> int:
    """Put jobs left in 'running' back in the queue (e.g. the worker was killed mid-solve).

    Only safe with a single worker, which is the v1 setup: call it once at worker start.
    """
    with conn:
        cursor = conn.execute(
            "UPDATE jobs SET status = 'queued', started_at = NULL WHERE status = 'running'"
        )
    return cursor.rowcount


def mark_succeeded(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    solver_status: str,
    termination: str,
    objective: float,
    mip_gap: float | None,
    solve_time_s: float,
    result: JobResult,
) -> None:
    with conn:
        conn.execute(
            "UPDATE jobs SET status = 'succeeded', finished_at = ?, solver_status = ?, "
            "termination = ?, objective = ?, mip_gap = ?, solve_time_s = ?, result_json = ? "
            "WHERE id = ?",
            (
                now(),
                solver_status,
                termination,
                objective,
                mip_gap,
                solve_time_s,
                result.model_dump_json(),
                job_id,
            ),
        )


def mark_failed(
    conn: sqlite3.Connection,
    job_id: str,
    error: str,
    *,
    solver_status: str | None = None,
    termination: str | None = None,
    solve_time_s: float | None = None,
) -> None:
    with conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', finished_at = ?, error = ?, solver_status = ?, "
            "termination = ?, solve_time_s = ? WHERE id = ?",
            (now(), error, solver_status, termination, solve_time_s, job_id),
        )


def to_job_info(row: sqlite3.Row) -> JobInfo:
    return JobInfo(
        job_id=row["id"],
        status=row["status"],
        instance_name=row["instance_name"],
        n_flights=row["n_flights"],
        n_gates=row["n_gates"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        solver_status=row["solver_status"],
        termination=row["termination"],
        objective=row["objective"],
        mip_gap=row["mip_gap"],
        solve_time_s=row["solve_time_s"],
        error=row["error"],
    )


def to_job_result(row: sqlite3.Row) -> JobResult:
    return JobResult.model_validate_json(row["result_json"])
