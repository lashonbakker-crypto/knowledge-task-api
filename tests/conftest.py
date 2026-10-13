from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from knowledge_task_api.api import create_app
from knowledge_task_api.config import Settings
from knowledge_task_api.store import Store


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return Store(tmp_path / "knowledge.sqlite3", clock=clock, lease_seconds=10)


@pytest.fixture
def client(tmp_path, store):
    settings = Settings.demo(tmp_path / "knowledge.sqlite3", worker_enabled=False)
    app = create_app(settings, store=store)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def headers():
    return {"Authorization": "Bearer demo-alice-local-only", "Idempotency-Key": "document-request-01"}
