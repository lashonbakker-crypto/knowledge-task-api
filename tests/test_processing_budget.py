import threading
from contextlib import contextmanager
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from knowledge_task_api.api import create_app
from knowledge_task_api.config import Settings, processing_seconds_from_env
from knowledge_task_api.worker import Worker


PAYLOAD = {"title": "Budget fixture", "text": "Public temporary test document", "max_attempts": 3}


def enqueue(store):
    return store.enqueue("alice", "processing-budget", PAYLOAD, "budget-test")[0]


def test_legacy_callback_returning_at_deadline_cannot_publish(store, clock):
    task = enqueue(store)
    worker = Worker(store, processor=lambda lease: clock.advance(2), max_processing_seconds=2,
                    processing_clock=clock, heartbeat_interval=0.01)
    assert worker.run_once()
    result = store.get_task("alice", task["id"])
    assert result["state"] == "failed" and result["error_code"] == "processing_timeout"
    assert result["attempt"] == 1 and store.statistics("alice")["documents"] == 0
    assert store.claim() is None
    assert [event["kind"] for event in store.task_events("alice", task["id"])].count("failed") == 1


def test_still_running_callback_loses_ownership_on_budget_expiry(store, clock, monkeypatch):
    task = enqueue(store)
    timed_out = threading.Event()
    original = store.timeout

    def timeout(lease):
        result = original(lease)
        timed_out.set()
        return result

    def process(lease):
        clock.advance(2)
        assert timed_out.wait(3), "monitor did not record timeout while callback was running"
        assert store.get_task("alice", task["id"])["state"] == "failed"
        assert not store.renew(lease) and not store.complete(lease)

    monkeypatch.setattr(store, "timeout", timeout)
    assert Worker(store, processor=process, max_processing_seconds=2,
                  processing_clock=clock, heartbeat_interval=0.01).run_once()
    assert store.statistics("alice")["documents"] == 0


def test_context_checkpoint_stops_expired_processing(store, clock):
    task = enqueue(store)
    after_checkpoint = []

    def process(lease, context):
        assert context.remaining_seconds == 2 and not context.stop_requested
        clock.advance(2)
        context.checkpoint()
        after_checkpoint.append("must not execute")

    assert Worker(store, processor_with_context=process, max_processing_seconds=2,
                  processing_clock=clock).run_once()
    assert not after_checkpoint
    assert store.get_task("alice", task["id"])["error_code"] == "processing_timeout"


def test_short_context_processor_can_publish(store, clock):
    task = enqueue(store)

    def process(lease, context):
        clock.advance(1)
        context.checkpoint()

    assert Worker(store, processor_with_context=process, max_processing_seconds=2,
                  processing_clock=clock).run_once()
    assert store.get_task("alice", task["id"])["state"] == "succeeded"


def test_publication_budget_is_rechecked_after_acquiring_sqlite_lock(store, clock, monkeypatch):
    enqueue(store)
    lease = store.claim()
    original = store.connection
    deadline = clock() + 2

    @contextmanager
    def delayed_connection(*, write=False):
        with original(write=write) as connection:
            if write:
                clock.advance(2)
            yield connection

    monkeypatch.setattr(store, "connection", delayed_connection)
    assert not store.complete(lease, deadline=deadline, deadline_clock=clock)
    assert store.statistics("alice")["documents"] == 0


def test_obsolete_timeout_cannot_affect_replacement_owner(store, clock):
    task = enqueue(store)
    old = store.claim()
    clock.advance(10)
    new = store.claim()
    assert not store.timeout(old)
    assert store.complete(new)
    assert store.get_task("alice", task["id"])["state"] == "succeeded"


def test_context_sees_shutdown_and_requeues_without_publication(store, clock):
    task = enqueue(store)
    worker = Worker(store, max_processing_seconds=2, processing_clock=clock)

    def process(lease, context):
        worker.stop()
        assert context.stop_requested
        context.checkpoint()

    worker.processor_with_context = process
    assert worker.run_once()
    assert store.get_task("alice", task["id"])["state"] == "queued"
    assert store.statistics("alice")["documents"] == 0


def test_http_app_passes_configured_budget_and_delivers_timeout_event(tmp_path, store, clock):
    settings = replace(Settings.demo(tmp_path / "knowledge.sqlite3", worker_enabled=False), processing_seconds=2)
    app = create_app(settings, store=store, processor=lambda lease: clock.advance(2))
    app.state.worker.processing_clock = clock
    assert app.state.worker.max_processing_seconds == 2
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer demo-alice-local-only", "Idempotency-Key": "budget-http"}
        response = client.post("/v1/documents", headers=headers, json=PAYLOAD)
        assert response.status_code == 202
        path = "/v1/tasks/" + response.json()["id"]
        assert app.state.worker.run_once()
        task = client.get(path, headers=headers).json()
        assert task["state"] == "failed" and task["error_code"] == "processing_timeout"
        stream = client.get(path + "/stream", headers=headers)
        assert "event: failed" in stream.text and "processing_timeout" in stream.text
        assert "event: end" in stream.text


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, 1 << 4096])
def test_invalid_processing_budget_is_refused(store, value):
    with pytest.raises(ValueError):
        Worker(store, max_processing_seconds=value)


def test_ambiguous_processor_configuration_is_refused(store):
    with pytest.raises(ValueError, match="choose"):
        Worker(store, processor=lambda lease: None, processor_with_context=lambda lease, context: None)


def test_environment_budget_is_explicit_and_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("KTA_KEYS_JSON", '{"alice":{"key":"public-budget-fixture","role":"user"}}')
    monkeypatch.setenv("KTA_DATABASE", str(tmp_path / "database.sqlite3"))
    monkeypatch.setenv("KTA_PROCESSING_SECONDS", "2.5")
    assert Settings.from_env().processing_seconds == 2.5
    for value in ("NaN", "inf", "0", "-1", "oops"):
        monkeypatch.setenv("KTA_PROCESSING_SECONDS", value)
        with pytest.raises(ValueError, match="KTA_PROCESSING_SECONDS"):
            processing_seconds_from_env()
