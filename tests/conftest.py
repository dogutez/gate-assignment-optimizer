"""Shared fixtures for API, worker and logging tests."""

import logging

import pytest
from fastapi.testclient import TestClient

from app import jobs
from app.api import app


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "jobs.db")
    conn = jobs.connect(path)
    jobs.init_db(conn)
    conn.close()
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    # The API reads jobs.DB_PATH on every request, so pointing it to a temporary
    # file is enough to isolate each test.
    monkeypatch.setattr(jobs, "DB_PATH", db_path)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def restore_root_logger():
    """setup_logging() changes the global root logger; undo that after the test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
