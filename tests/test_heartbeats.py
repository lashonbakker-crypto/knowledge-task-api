import sqlite3
import threading

import pytest

from knowledge_task_api.config import Settings
from knowledge_task_api.store import Store
from knowledge_task_api.worker import Worker


PAYLOAD = {"title": "Long task", "text": "Public deterministic test fixture", "max_attempts": 3}


def queued(store):
    return store.enqueue("alice", "long-task-0001", PAYLOAD, "test-request")[0]


def test_live_renewal_prevents_reclaim_and_does_not_spend_an_attempt(store, clock):
    task = queued(store)
    lease = store.claim()
    clock.advance(8)
    assert store.renew(lease)
    clock.advance(4)
    assert store.claim() is None
    assert store.complete(lease)
    result = store.get_task("alice", task["id"])
    assert result["state"] == "succeeded" and result["attempt"] == 1
    assert [e["kind"] for e in store.task_events("alice", task["id"])] == ["queued", "running", "lease_renewed", "succeeded"]


def test_expired_or_replaced_lease_cannot_be_renewed_or_publish(store, clock):
    task = queued(store)
    old = store.claim()
    clock.advance(10)
    assert not store.renew(old)
    replacement = store.claim()
    assert replacement.token > old.token
    assert not store.renew(old)
    assert not store.complete(old)
    assert store.renew(replacement)
    assert store.complete(replacement)
    assert store.statistics("alice")["documents"] == 1


def test_cancelled_lease_is_not_revived_by_heartbeat(store):
    task = queued(store)
    lease = store.claim()
    store.cancel("alice", task["id"])
    before = store.task_events("alice", task["id"])
    assert not store.renew(lease)
    assert not store.complete(lease)
    assert store.task_events("alice", task["id"]) == before


def test_worker_renews_during_processing_past_original_deadline(store, clock, monkeypatch):
    task = queued(store)
    renewed = threading.Event()
    original = store.renew

    def renew(lease):
        result = original(lease)
        renewed.set()
        return result

    def process(lease):
        clock.advance(8)
        assert renewed.wait(3), "heartbeat did not run"
        clock.advance(4)
        assert store.claim() is None

    monkeypatch.setattr(store, "renew", renew)
    worker = Worker(store, processor=process, heartbeat_interval=0.01)
    assert worker.run_once()
    assert store.get_task("alice", task["id"])["state"] == "succeeded"
    assert store.statistics("alice")["documents"] == 1


def test_heartbeat_storage_failure_retries_without_publication(store, monkeypatch):
    task = queued(store)
    attempted = threading.Event()

    def unavailable(lease):
        attempted.set()
        raise sqlite3.OperationalError("fixture; do not log document text")

    monkeypatch.setattr(store, "renew", unavailable)
    worker = Worker(store, processor=lambda lease: attempted.wait(3), heartbeat_interval=0.01)
    assert worker.run_once()
    result = store.get_task("alice", task["id"])
    assert result["state"] == "queued"
    assert result["error_code"] == "temporary_index_failure"
    assert store.statistics("alice")["documents"] == 0


def test_processor_failure_still_retries_and_stops_heartbeat(store):
    task = queued(store)

    def process(lease):
        raise sqlite3.OperationalError("fixture")

    assert Worker(store, processor=process, heartbeat_interval=0.01).run_once()
    assert store.get_task("alice", task["id"])["state"] == "queued"
    assert not any(t.name == "knowledge-lease" and t.is_alive() for t in threading.enumerate())


def test_cancellation_during_processing_prevents_publication(store):
    task = queued(store)
    worker = Worker(store, processor=lambda lease: store.cancel("alice", lease.id), heartbeat_interval=0.01)
    assert worker.run_once()
    assert store.get_task("alice", task["id"])["state"] == "cancelled"
    assert store.statistics("alice")["documents"] == 0


def test_stop_during_processing_requeues_without_publication(store):
    task = queued(store)
    worker = Worker(store, heartbeat_interval=0.01)
    worker.processor = lambda lease: worker.stop()
    assert worker.run_once()
    assert store.get_task("alice", task["id"])["state"] == "queued"
    assert store.statistics("alice")["documents"] == 0


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), float("-inf"), True])
def test_invalid_lease_duration_is_rejected_before_database_creation(tmp_path, value):
    database = tmp_path / "invalid.sqlite3"
    with pytest.raises(ValueError):
        Store(database, lease_seconds=value)
    assert not database.exists()


@pytest.mark.parametrize("field,value", [("lease_seconds", float("nan")), ("stream_seconds", float("inf")), ("max_request_bytes", 1.5)])
def test_settings_refuse_unbounded_or_invalid_limits(tmp_path, field, value):
    settings = Settings.demo(tmp_path / "example.sqlite3")
    parameters = {"database": settings.database, "keys": settings.keys, field: value}
    with pytest.raises(ValueError):
        Settings(**parameters)


@pytest.mark.parametrize("value", [0, -1, 10, float("nan"), float("inf"), True])
def test_invalid_heartbeat_interval_is_rejected(store, value):
    with pytest.raises(ValueError):
        Worker(store, heartbeat_interval=value)
