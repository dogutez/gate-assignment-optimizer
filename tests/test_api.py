import json

import pytest

from app import jobs, worker
from app.model import SolveResult
from app.schemas import APRON, Instance, JobRequest
from data.generate import INSTANCES_DIR
from tests.helpers import run_worker_once, small_request


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_submit_poll_result_happy_path(client, db_path):
    response = client.post("/jobs", json=small_request(time_limit_s=20))
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert response.headers["Location"] == f"/jobs/{job_id}"

    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "queued"
    assert status["n_flights"] == 20
    # No result before the worker has run.
    assert client.get(f"/jobs/{job_id}/result").status_code == 409

    assert run_worker_once(db_path) is True
    assert run_worker_once(db_path) is False  # queue is now empty

    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "succeeded"
    assert status["solver_status"] == "optimal"
    assert status["objective"] > 0
    assert status["started_at"] is not None and status["finished_at"] is not None

    result = client.get(f"/jobs/{job_id}/result").json()
    assert len(result["assignments"]) == 20
    assert result["kpis"]["n_flights"] == 20
    assert {a["gate_id"] for a in result["assignments"]} <= {f"G{k:02d}" for k in range(1, 11)} | {
        APRON
    }


def test_jobs_run_oldest_first_and_list_newest_first(client, db_path):
    first = client.post("/jobs", json=small_request()).json()["job_id"]
    second = client.post("/jobs", json=small_request()).json()["job_id"]

    run_worker_once(db_path)
    assert client.get(f"/jobs/{first}").json()["status"] == "succeeded"
    assert client.get(f"/jobs/{second}").json()["status"] == "queued"

    listed = [j["job_id"] for j in client.get("/jobs").json()]
    assert listed == [second, first]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda b: b["instance"]["flights"][0].update(departure=0), "departure"),
        (lambda b: b["instance"]["flights"][0].update(aircraft_size="XL"), "aircraft_size"),
        (lambda b: b["instance"]["gates"].append(b["instance"]["gates"][0]), "duplicate gate"),
        (lambda b: b["options"].update(time_limit_s=-1), "time_limit_s"),
        (lambda b: b["options"].update(solver="glpk"), "solver"),
        (lambda b: b["options"].update(threads=0), "threads"),
        (lambda b: b.update(unexpected=1), "unexpected"),
    ],
)
def test_invalid_input_returns_422(client, change, message):
    body = small_request()
    change(body)
    response = client.post("/jobs", json=body)
    assert response.status_code == 422
    assert message in json.dumps(response.json()["detail"])


def test_unknown_job_returns_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404
    assert client.get("/jobs/does-not-exist/result").status_code == 404


def test_solver_exception_marks_job_failed(client, db_path, monkeypatch):
    def broken_solve(*args, **kwargs):
        raise RuntimeError("solver crashed")

    monkeypatch.setattr(worker, "solve", broken_solve)
    job_id = client.post("/jobs", json=small_request()).json()["job_id"]
    run_worker_once(db_path)

    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "failed"
    assert status["error"] == "RuntimeError: solver crashed"
    result = client.get(f"/jobs/{job_id}/result")
    assert result.status_code == 409
    assert "solver crashed" in result.json()["detail"]


def test_no_solution_within_time_limit_marks_job_failed(client, db_path, monkeypatch):
    def no_solution(*args, **kwargs):
        return SolveResult(
            solver="highs",
            solver_version="1.15.1",
            status="no_solution",
            termination="maxTimeLimit",
            objective=None,
            best_bound=None,
            mip_gap=None,
            assignment={},
            nodes=0,
            n_variables=1,
            n_constraints=1,
            build_time_s=0.0,
            solve_time_s=5.0,
        )

    monkeypatch.setattr(worker, "solve", no_solution)
    job_id = client.post("/jobs", json=small_request(time_limit_s=5)).json()["job_id"]
    run_worker_once(db_path)

    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "failed"
    assert status["solver_status"] == "no_solution"
    assert "time limit of 5.0 s" in status["error"]


def test_interrupted_running_job_is_requeued(db_path):
    conn = jobs.connect(db_path)
    instance = Instance.model_validate_json((INSTANCES_DIR / "small.json").read_text())
    job_id = jobs.create_job(conn, JobRequest(instance=instance))
    jobs.claim_next_job(conn)
    assert jobs.get_job(conn, job_id)["status"] == "running"

    assert jobs.requeue_running_jobs(conn) == 1
    assert jobs.get_job(conn, job_id)["status"] == "queued"
    conn.close()
