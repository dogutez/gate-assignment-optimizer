"""JSON logging to stdout: one JSON object per line, Docker-friendly.

Example line:
    {"time": "...", "level": "INFO", "logger": "app.model", "event": "model built",
     "job_id": "3f2c...", "n_variables": 8476, "n_constraints": 4580, "build_time_s": 0.23}

Extra fields come from logger.info("event", extra={...}). The job id does not have to
be passed around: the worker stores it in `current_job_id` while it runs a job, and
JobIdFilter adds it to every log line written during that time (also from app.model).
"""

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# Set by the worker while a job is running; None otherwise.
current_job_id: ContextVar[str | None] = ContextVar("current_job_id", default=None)

# Attributes every LogRecord has; anything else on a record came from `extra`.
STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
# Uvicorn adds a copy of each message with terminal colors; not useful in JSON.
SKIPPED_ATTRS = {"color_message"}


class JobIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        job_id = current_job_id.get()
        if job_id is not None and not hasattr(record, "job_id"):
            record.job_id = job_id
        return True  # never drop records, only enrich them


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in STANDARD_ATTRS and key not in SKIPPED_ATTRS:
                entry[key] = value
        if record.exc_info:
            entry["traceback"] = self.formatException(record.exc_info)
        # default=str: values json can't encode (e.g. datetime) are written as text.
        return json.dumps(entry, default=str)


def setup_logging(level: str | None = None) -> None:
    """Send all logs (ours and uvicorn's) as JSON lines to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(JobIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))
