"""Background worker: takes queued jobs from SQLite, solves them, stores the result.

Run with:  python -m app.worker
"""

import argparse
import logging
import signal
import sqlite3
import threading

from app import jobs
from app.logging_conf import current_job_id, setup_logging
from app.model import compute_kpis, solve
from app.schemas import GateAssignment, JobRequest, JobResult

# Fixed name: under "python -m app.worker" __name__ would be "__main__".
logger = logging.getLogger("app.worker")


def process_one(conn: sqlite3.Connection) -> bool:
    """Run the oldest queued job. Returns False if there was nothing to do."""
    row = jobs.claim_next_job(conn)
    if row is None:
        return False

    # Every log line written while this job runs (also inside app.model) gets its job_id.
    token = current_job_id.set(row["id"])
    try:
        run_job(conn, row)
    finally:
        current_job_id.reset(token)
    return True


def run_job(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    job_id = row["id"]
    logger.info(
        "job started",
        extra={
            "instance_name": row["instance_name"],
            "n_flights": row["n_flights"],
            "n_gates": row["n_gates"],
        },
    )
    try:
        request = JobRequest.model_validate_json(row["request_json"])
        options = request.options
        result = solve(request.instance, options.time_limit_s, options.mip_gap)

        if result.status == "no_solution":
            # Only possible if the time limit hits before HiGHS finds any solution.
            error = (
                f"no feasible solution found within the time limit of {options.time_limit_s} s "
                f"(solver termination: {result.termination})"
            )
            jobs.mark_failed(
                conn,
                job_id,
                error,
                solver_status=result.status,
                termination=result.termination,
                solve_time_s=result.solve_time_s,
            )
            logger.warning("job failed", extra={"error_type": "NoSolution", "error": error})
            return

        kpis = compute_kpis(request.instance, result.assignment)
        job_result = JobResult(
            job_id=job_id,
            assignments=[
                GateAssignment(flight_id=f, gate_id=g) for f, g in result.assignment.items()
            ],
            kpis=kpis,
        )
        jobs.mark_succeeded(
            conn,
            job_id,
            solver_status=result.status,
            termination=result.termination,
            objective=result.objective,
            mip_gap=result.mip_gap,
            solve_time_s=result.solve_time_s,
            result=job_result,
        )
        logger.info(
            "job succeeded",
            extra={"apron_count": kpis.apron_count, "solver_status": result.status},
        )
    except Exception as exc:
        # Any bug or bad data must fail this job, not crash the worker.
        jobs.mark_failed(conn, job_id, f"{type(exc).__name__}: {exc}")
        logger.exception("job failed", extra={"error_type": type(exc).__name__, "error": str(exc)})


def run_forever(db_path: str, poll_interval_s: float, stop: threading.Event) -> None:
    """Poll for jobs until `stop` is set.

    The stop flag is only checked between jobs: a running HiGHS solve cannot be
    interrupted from Python. If the process is killed mid-solve, the job stays
    'running' and is requeued when the worker starts again.
    """
    conn = jobs.connect(db_path)
    jobs.init_db(conn)
    requeued = jobs.requeue_running_jobs(conn)
    if requeued:
        logger.warning("requeued interrupted jobs", extra={"count": requeued})
    logger.info("worker started", extra={"db_path": db_path, "poll_interval_s": poll_interval_s})

    while not stop.is_set():
        found = process_one(conn)
        if not found:
            # Queue empty: wait before asking again. Returns early if stop is set.
            stop.wait(poll_interval_s)
    conn.close()
    logger.info("worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate assignment solve worker.")
    parser.add_argument("--db", default=jobs.DB_PATH, help="SQLite file")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="seconds")
    args = parser.parse_args()

    setup_logging()
    stop = threading.Event()

    def request_stop(signum, frame):
        logger.info("stop requested", extra={"signal": signal.Signals(signum).name})
        stop.set()

    # SIGTERM is what `docker stop` sends; SIGINT is Ctrl+C.
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run_forever(args.db, args.poll_interval, stop)


if __name__ == "__main__":
    main()
