"""Background worker: takes queued jobs from SQLite, solves them, stores the result.

Run with:  python -m app.worker
"""

import argparse
import logging
import sqlite3
import time

from app import jobs
from app.model import compute_kpis, solve
from app.schemas import GateAssignment, JobRequest, JobResult

logger = logging.getLogger(__name__)


def process_one(conn: sqlite3.Connection) -> bool:
    """Run the oldest queued job. Returns False if there was nothing to do."""
    row = jobs.claim_next_job(conn)
    if row is None:
        return False

    job_id = row["id"]
    logger.info("job started", extra={"job_id": job_id})
    try:
        request = JobRequest.model_validate_json(row["request_json"])
        options = request.options
        result = solve(request.instance, options.time_limit_s, options.mip_gap)

        if result.status == "no_solution":
            # Only possible if the time limit hits before HiGHS finds any solution.
            jobs.mark_failed(
                conn,
                job_id,
                f"no feasible solution found within the time limit of {options.time_limit_s} s "
                f"(solver termination: {result.termination})",
                solver_status=result.status,
                termination=result.termination,
                solve_time_s=result.solve_time_s,
            )
            logger.warning("job failed: no solution", extra={"job_id": job_id})
            return True

        job_result = JobResult(
            job_id=job_id,
            assignments=[
                GateAssignment(flight_id=f, gate_id=g) for f, g in result.assignment.items()
            ],
            kpis=compute_kpis(request.instance, result.assignment),
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
        logger.info("job succeeded", extra={"job_id": job_id})
    except Exception as exc:
        # Any bug or bad data must fail this job, not crash the worker.
        jobs.mark_failed(conn, job_id, f"{type(exc).__name__}: {exc}")
        logger.exception("job failed", extra={"job_id": job_id})
    return True


def run_forever(db_path: str, poll_interval_s: float) -> None:
    conn = jobs.connect(db_path)
    jobs.init_db(conn)
    requeued = jobs.requeue_running_jobs(conn)
    if requeued:
        logger.warning("requeued %d interrupted job(s)", requeued)
    logger.info("worker started, polling %s", db_path)

    while True:
        found = process_one(conn)
        if not found:
            time.sleep(poll_interval_s)  # queue empty: wait before asking again


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate assignment solve worker.")
    parser.add_argument("--db", default=jobs.DB_PATH, help="SQLite file")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="seconds")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)  # replaced by JSON logging in a later phase
    try:
        run_forever(args.db, args.poll_interval)
    except KeyboardInterrupt:
        logger.info("worker stopped")


if __name__ == "__main__":
    main()
