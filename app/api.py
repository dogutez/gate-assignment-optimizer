"""REST API. It never solves anything itself: it stores jobs and reads their state.

Run with:  uvicorn app.api:app --host 127.0.0.1 --port 8000
"""

import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status

from app import jobs
from app.schemas import JobInfo, JobRequest, JobResult, JobSubmitted


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


def find_job_or_404(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = jobs.get_job(conn, job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"job {job_id} not found")
    return row


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def submit_job(request: JobRequest, conn: Db, response: Response) -> JobSubmitted:
    # Invalid bodies never get here: FastAPI validates JobRequest and answers 422.
    job_id = jobs.create_job(conn, request)
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
