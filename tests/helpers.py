import json

from app import jobs, worker
from data.generate import INSTANCES_DIR


def run_worker_once(db_path: str) -> bool:
    conn = jobs.connect(db_path)
    try:
        return worker.process_one(conn)
    finally:
        conn.close()


def small_request(**options) -> dict:
    instance = json.loads((INSTANCES_DIR / "small.json").read_text())
    return {"instance": instance, "options": options}
