import re
from collections import Counter
from datetime import datetime, timedelta

import pytest

from app import jobs, metrics
from app.schemas import JobRequest
from tests.helpers import run_worker_once, small_request

# name{labels} value  (or name value)
SAMPLE_LINE = re.compile(r'^[a-z_]+(\{[a-z_]+="[^"]*"(,[a-z_]+="[^"]*")*\})? [0-9.e+-]+$')


@pytest.fixture(autouse=True)
def fresh_http_counter(monkeypatch):
    # The counter is global to the API process; give each test its own.
    monkeypatch.setattr(metrics, "http_requests", Counter())


def samples(text: str) -> dict[str, float]:
    """Parse the exposition text into {"name{labels}": value}, checking every line."""
    values = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        assert SAMPLE_LINE.match(line), f"not a valid sample line: {line!r}"
        key, value = line.rsplit(" ", 1)
        values[key] = float(value)
    return values


def test_metrics_on_empty_store(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    values = samples(response.text)
    for status in ("queued", "running", "succeeded", "failed"):
        assert values[f'gate_optimizer_jobs{{status="{status}"}}'] == 0
    assert values["gate_optimizer_oldest_queued_job_age_seconds"] == 0
    assert values["gate_optimizer_solve_seconds_count"] == 0


def test_metrics_follow_the_job_lifecycle(client, db_path):
    job_id = client.post("/jobs", json=small_request(time_limit_s=20)).json()["job_id"]
    values = samples(client.get("/metrics").text)
    assert values['gate_optimizer_jobs{status="queued"}'] == 1

    run_worker_once(db_path)
    values = samples(client.get("/metrics").text)
    assert values['gate_optimizer_jobs{status="queued"}'] == 0
    assert values['gate_optimizer_jobs{status="succeeded"}'] == 1
    assert values["gate_optimizer_solve_seconds_count"] == 1
    assert values["gate_optimizer_solve_seconds_sum"] > 0
    assert client.get(f"/jobs/{job_id}").json()["status"] == "succeeded"


def test_oldest_queued_job_age(db_path):
    conn = jobs.connect(db_path)
    jobs.create_job(conn, JobRequest.model_validate(small_request()))
    created = datetime.fromisoformat(conn.execute("SELECT created_at FROM jobs").fetchone()[0])

    values = samples(metrics.render(conn, now=created + timedelta(seconds=120)))
    conn.close()
    assert values["gate_optimizer_oldest_queued_job_age_seconds"] == pytest.approx(120)


def test_http_requests_are_counted_per_route_not_per_url(client):
    client.get("/jobs/abc")
    client.get("/jobs/def")
    client.get("/health")

    values = samples(client.get("/metrics").text)
    key = 'gate_optimizer_http_requests_total{method="GET",path="/jobs/{job_id}",code="404"}'
    assert values[key] == 2
    assert values['gate_optimizer_http_requests_total{method="GET",path="/health",code="200"}'] == 1
    assert not any("abc" in k for k in values)
