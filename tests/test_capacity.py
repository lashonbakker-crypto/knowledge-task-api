from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from knowledge_task_api.api import create_app
from knowledge_task_api.config import Settings
from knowledge_task_api.store import Conflict, QueueFull, Store

PAYLOAD = {"title": "Capacity", "text": "Bound pending work.", "max_attempts": 3}


def enqueue(store, key, tenant="alice"):
    return store.enqueue(tenant, key, PAYLOAD, "capacity-request")


def test_concurrent_admission_respects_both_limits(tmp_path):
    database = tmp_path / "queue.sqlite3"
    # Independent connections/Store instances contend for the same database.
    stores = [Store(database, max_pending_per_tenant=3, max_pending_total=5) for _ in range(8)]
    ready = Barrier(8)

    def submit(number):
        ready.wait(timeout=10)
        try:
            return enqueue(stores[number % 8], f"request-{number:02}", "alice" if number % 2 else "bob")
        except QueueFull:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(24)))
    assert sum(result is not None for result in results) == 5
    assert stores[0].statistics()["tasks"] == {"queued": 5}
    assert max(stores[0].statistics(tenant)["tasks"]["queued"] for tenant in ("alice", "bob")) <= 3
    with stores[0].connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 5


def test_full_queue_preserves_replay_conflict_and_rejected_key(tmp_path):
    store = Store(tmp_path / "queue.sqlite3", max_pending_per_tenant=1, max_pending_total=1)
    first, replay = enqueue(store, "original-request")
    assert not replay
    assert enqueue(store, "original-request") == (first, True)
    with pytest.raises(Conflict):
        store.enqueue("alice", "original-request", {**PAYLOAD, "text": "changed"}, "trace")
    with pytest.raises(QueueFull):
        enqueue(store, "rejected-request", "bob")
    store.cancel("alice", first["id"])
    accepted, replay = enqueue(store, "rejected-request", "bob")
    assert not replay and accepted["state"] == "queued"


@pytest.mark.parametrize("ending", ["complete", "fail", "timeout", "cancel"])
def test_terminal_attempt_releases_capacity(tmp_path, ending):
    store = Store(tmp_path / "queue.sqlite3", max_pending_per_tenant=1, max_pending_total=1)
    task, _ = enqueue(store, "first-request")
    lease = store.claim()
    with pytest.raises(QueueFull):
        enqueue(store, "next-request")
    if ending == "cancel":
        store.cancel("alice", task["id"])
    elif ending == "fail":
        assert store.fail(lease, retryable=False)
    else:
        assert getattr(store, ending)(lease)
    assert enqueue(store, "next-request")[0]["state"] == "queued"


def test_scheduled_retry_still_occupies_capacity_after_reopen(tmp_path):
    database = tmp_path / "queue.sqlite3"
    store = Store(database, max_pending_per_tenant=1)
    enqueue(store, "first-request")
    assert store.fail(store.claim(), retryable=True)
    reopened = Store(database, max_pending_per_tenant=1)
    with pytest.raises(QueueFull):
        enqueue(reopened, "next-request")


def test_http_capacity_settings_replay_privacy_and_retry_hint(tmp_path):
    settings = replace(Settings.demo(tmp_path / "queue.sqlite3", worker_enabled=False),
                       max_pending_per_tenant=2, max_pending_total=3)
    app = create_app(settings)
    with TestClient(app) as client:
        def submit(tenant, key, payload=PAYLOAD):
            token = "demo-alice-local-only" if tenant == "alice" else "demo-bob-local-only-1"
            return client.post("/v1/documents", json=payload,
                               headers={"Authorization": "Bearer " + token, "Idempotency-Key": key})

        first = submit("alice", "original-request")
        assert first.status_code == 202
        assert submit("alice", "second-request").status_code == 202
        limited = submit("alice", "another-request")
        assert limited.status_code == 429 and limited.headers["Retry-After"] == "1"
        assert limited.json() == {"detail": "pending task capacity reached; retry later"}
        assert submit("bob", "original-request").status_code == 202
        global_limit = submit("bob", "another-request")
        assert global_limit.status_code == 429 and global_limit.json() == limited.json()
        replay = submit("alice", "original-request")
        assert replay.status_code == 202 and replay.json()["id"] == first.json()["id"]
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert submit("alice", "original-request", {**PAYLOAD, "text": "changed"}).status_code == 409
        assert client.post("/v1/documents", json=PAYLOAD,
                           headers={"Idempotency-Key": "another-request"}).status_code == 401


@pytest.mark.parametrize("field", ["max_pending_per_tenant", "max_pending_total"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
def test_invalid_capacity_is_rejected_before_database_creation(tmp_path, field, value):
    database = tmp_path / "invalid.sqlite3"
    with pytest.raises(ValueError, match="positive integers"):
        Store(database, **{field: value})
    with pytest.raises(ValueError, match="positive integers"):
        replace(Settings.demo(database), **{field: value})
    assert not database.exists()


def test_capacity_environment_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("KTA_KEYS_JSON", '{"alice":{"key":"demo-alice-local-only","role":"user"}}')
    monkeypatch.setenv("KTA_DATABASE", str(tmp_path / "queue.sqlite3"))
    monkeypatch.setenv("KTA_MAX_PENDING_PER_TENANT", "2")
    monkeypatch.setenv("KTA_MAX_PENDING_TOTAL", "3")
    settings = Settings.from_env()
    assert (settings.max_pending_per_tenant, settings.max_pending_total) == (2, 3)
    app = create_app(settings)
    assert (app.state.store.max_pending_per_tenant, app.state.store.max_pending_total) == (2, 3)
    monkeypatch.setenv("KTA_MAX_PENDING_TOTAL", "private-invalid-value")
    with pytest.raises(ValueError) as caught:
        Settings.from_env()
    assert str(caught.value) == "KTA_MAX_PENDING_TOTAL must be a positive integer"
