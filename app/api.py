"""REST API. It never solves anything itself: it stores jobs and reads their state.

Run with:  python -m app.api --host 127.0.0.1 --port 8000
"""

import argparse
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app import jobs, metrics
from app.logging_conf import setup_logging
from app.schemas import JobInfo, JobRequest, JobResult, JobSubmitted

# Fixed name: under "python -m app.api" __name__ would be "__main__".
logger = logging.getLogger("app.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the table on startup so the API works before the worker has ever run.
    conn = jobs.connect(jobs.DB_PATH)
    jobs.init_db(conn)
    conn.close()
    yield


app = FastAPI(
    title="Gate Assignment Optimizer",
    description="Submit a gate assignment instance as a background MIP solve job.",
    version="0.1.0",
    lifespan=lifespan,
)


def get_db() -> Iterator[sqlite3.Connection]:
    """One SQLite connection per request, closed when the request is done."""
    conn = jobs.connect(jobs.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


Db = Annotated[sqlite3.Connection, Depends(get_db)]


@app.middleware("http")
async def count_requests(request: Request, call_next):
    response = await call_next(request)
    # After routing, the scope holds the matched route, e.g. "/jobs/{job_id}".
    route = request.scope.get("route")
    path = route.path if route else "unmatched"
    metrics.http_requests[request.method, path, response.status_code] += 1
    return response


def find_job_or_404(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = jobs.get_job(conn, job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"job {job_id} not found")
    return row


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
def get_metrics(conn: Db) -> PlainTextResponse:
    """Prometheus scrape endpoint: job counts, queue age, solve time, HTTP requests."""
    return PlainTextResponse(metrics.render(conn), media_type="text/plain; version=0.0.4")


@app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def submit_job(request: JobRequest, conn: Db, response: Response) -> JobSubmitted:
    # Invalid bodies never get here: FastAPI validates JobRequest and answers 422.
    job_id = jobs.create_job(conn, request)
    logger.info(
        "job submitted",
        extra={
            "job_id": job_id,
            "instance_name": request.instance.name,
            "n_flights": len(request.instance.flights),
            "n_gates": len(request.instance.gates),
            "time_limit_s": request.options.time_limit_s,
            "mip_gap": request.options.mip_gap,
        },
    )
    response.headers["Location"] = f"/jobs/{job_id}"
    return JobSubmitted(job_id=job_id, status="queued")


@app.get("/jobs")
def list_jobs(conn: Db, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[JobInfo]:
    return [jobs.to_job_info(row) for row in jobs.list_jobs(conn, limit)]


@app.get("/jobs/{job_id}")
def get_job(job_id: str, conn: Db) -> JobInfo:
    return jobs.to_job_info(find_job_or_404(conn, job_id))


@app.get("/jobs/{job_id}/result")
def get_result(job_id: str, conn: Db) -> JobResult:
    row = find_job_or_404(conn, job_id)
    if row["status"] == "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"job failed: {row['error']}")
    if row["status"] != "succeeded":
        raise HTTPException(status.HTTP_409_CONFLICT, f"job is {row['status']}, no result yet")
    return jobs.to_job_result(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate assignment API server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    setup_logging()
    # log_config=None: uvicorn keeps its loggers unconfigured, so its lines (startup,
    # access log) go through our JSON handler too.
    uvicorn.run(app, host=args.host, port=args.port, log_config=None)


if __name__ == "__main__":
    main()
