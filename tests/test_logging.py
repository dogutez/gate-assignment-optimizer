import json
import logging
import sys

from app import worker
from app.logging_conf import JsonFormatter, current_job_id, setup_logging
from tests.helpers import run_worker_once, small_request


def json_lines(text: str) -> list[dict]:
    """Every non-empty stdout line must be valid JSON."""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_formatter_writes_one_json_object_with_extra_fields():
    record = logging.makeLogRecord(
        {"name": "app.test", "levelname": "INFO", "msg": "solve %s", "args": ("finished",)}
    )
    record.objective = 12.5
    entry = json.loads(JsonFormatter().format(record))
    assert entry["event"] == "solve finished"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.test"
    assert entry["objective"] == 12.5
    assert "time" in entry


def test_formatter_includes_traceback_on_one_line():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.makeLogRecord({"msg": "failed", "exc_info": sys.exc_info()})
    line = JsonFormatter().format(record)
    assert "\n" not in line  # newlines inside the traceback are escaped by json.dumps
    assert "ValueError: boom" in json.loads(line)["traceback"]


def test_context_job_id_is_added(capsys, restore_root_logger):
    setup_logging()
    log = logging.getLogger("app.test")
    token = current_job_id.set("job-123")
    log.info("inside a job")
    current_job_id.reset(token)
    log.info("outside a job")

    inside, outside = json_lines(capsys.readouterr().out)
    assert inside["job_id"] == "job-123"
    assert "job_id" not in outside


def test_job_lifecycle_events_carry_job_id(client, db_path, capsys, restore_root_logger):
    setup_logging()
    job_id = client.post("/jobs", json=small_request()).json()["job_id"]
    run_worker_once(db_path)

    entries = json_lines(capsys.readouterr().out)
    by_event = {e["event"]: e for e in entries if e.get("job_id") == job_id}
    assert {"job submitted", "job started", "model built", "solve finished", "job succeeded"} <= (
        set(by_event)
    )

    built = by_event["model built"]
    assert built["solver"] == "highs"
    assert built["n_variables"] > 0 and built["n_constraints"] > 0
    assert built["build_time_s"] >= 0

    finished = by_event["solve finished"]
    assert finished["solver"] == "highs" and finished["solver_version"]
    assert finished["status"] == "optimal"
    assert finished["objective"] > 0
    assert finished["mip_gap"] is not None and finished["solve_time_s"] >= 0


def test_failed_job_logs_exception_type_and_message(
    client, db_path, capsys, monkeypatch, restore_root_logger
):
    def broken_solve(*args, **kwargs):
        raise RuntimeError("solver crashed")

    monkeypatch.setattr(worker, "solve", broken_solve)
    setup_logging()
    job_id = client.post("/jobs", json=small_request()).json()["job_id"]
    run_worker_once(db_path)

    failed = [e for e in json_lines(capsys.readouterr().out) if e["event"] == "job failed"]
    assert len(failed) == 1
    assert failed[0]["job_id"] == job_id
    assert failed[0]["level"] == "ERROR"
    assert failed[0]["error_type"] == "RuntimeError"
    assert failed[0]["error"] == "solver crashed"
    assert "Traceback" in failed[0]["traceback"]
