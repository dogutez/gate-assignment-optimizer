import threading
import time

from app import jobs, worker
from app.schemas import JobRequest
from tests.helpers import small_request


def test_worker_loop_runs_queued_job_and_stops_on_request(db_path):
    conn = jobs.connect(db_path)
    job_id = jobs.create_job(conn, JobRequest.model_validate(small_request()))

    stop = threading.Event()
    thread = threading.Thread(target=worker.run_forever, args=(db_path, 0.05, stop))
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while jobs.get_job(conn, job_id)["status"] != "succeeded":
            assert time.monotonic() < deadline, "job did not finish in time"
            time.sleep(0.05)
    finally:
        stop.set()  # what the SIGTERM handler does
        thread.join(timeout=5)
        conn.close()
    assert not thread.is_alive()
