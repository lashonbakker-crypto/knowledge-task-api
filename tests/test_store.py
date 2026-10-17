import sqlite3
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from knowledge_task_api.store import Conflict, Missing, Store
from knowledge_task_api.store import Lease
from knowledge_task_api.worker import Worker

PAYLOAD = {"title": "Retries", "text": "Retry a temporary failure using an expiring lease.", "max_attempts": 3}


def submit(store, *, tenant="alice", key="document-request-01", payload=None):
    return store.enqueue(tenant, key, payload or PAYLOAD, "trace-request-01")[0]


def test_concurrent_idempotency_creates_one_task(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.enqueue("alice", "document-request-01", PAYLOAD, "trace-request-01"), range(8)))
    assert len({task["id"] for task, _ in results}) == 1
    assert sum(not replay for _, replay in results) == 1
    assert store.statistics("alice")["tasks"] == {"queued": 1}


def test_idempotency_conflict_and_tenant_namespace(store):
    first = submit(store)
    with pytest.raises(Conflict):
        submit(store, payload={**PAYLOAD, "text": "Different body"})
    second = submit(store, tenant="bob")
    assert first["id"] != second["id"]


def test_two_workers_cannot_claim_the_same_lease(store):
    submit(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.claim(), range(2)))
    assert sum(lease is not None for lease in results) == 1


def test_expired_worker_is_fenced_even_before_reclaim(store, clock):
    task = submit(store)
    old = store.claim()
    clock.advance(10)
    assert store.complete(old) is False
    assert store.fail(old) is False
    current = store.claim()
    assert current.token != old.token
    assert current.attempt == 2
    assert store.complete(old) is False
    assert store.complete(current) is True
    assert store.get_task("alice", task["id"])["state"] == "succeeded"
    assert store.statistics("alice")["documents"] == 1


def test_cancel_running_revokes_worker_publication(store):
    task = submit(store)
    lease = store.claim()
    assert store.cancel("alice", task["id"])["state"] == "cancelled"
    assert store.complete(lease) is False
    assert store.fail(lease) is False
    assert store.statistics("alice")["documents"] == 0
    store.cancel("alice", task["id"])
    assert [e["kind"] for e in store.task_events("alice", task["id"])].count("cancelled") == 1


def test_complete_is_atomic_and_cannot_publish_twice(store):
    task = submit(store)
    lease = store.claim()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.complete(lease), range(2)))
    assert sorted(results) == [False, True]
    assert store.statistics("alice")["documents"] == 1
    with pytest.raises(Conflict):
        store.cancel("alice", task["id"])


def test_index_failure_rolls_back_document_and_completion(store):
    task = submit(store)
    lease = store.claim()
    with store.connection(write=True) as connection:
        connection.execute("CREATE TRIGGER reject_index BEFORE INSERT ON document_fts_data BEGIN SELECT RAISE(ABORT,'injected index failure'); END")
    with pytest.raises(sqlite3.Error):
        store.complete(lease)
    assert store.statistics("alice")["documents"] == 0
    assert store.get_task("alice", task["id"])["state"] == "running"


def test_retry_backoff_and_attempt_limit(store, clock):
    task = submit(store)
    for attempt, delay in [(1, 1), (2, 2), (3, 0)]:
        lease = store.claim()
        assert lease.attempt == attempt
        store.fail(lease)
        row = store.get_task("alice", task["id"])
        assert row["state"] == ("queued" if attempt < 3 else "failed")
        assert store.claim() is None
        clock.advance(delay)
    assert store.statistics("alice")["documents"] == 0


def test_crash_recovery_does_not_exceed_attempt_limit(store, clock):
    task = submit(store, payload={**PAYLOAD, "max_attempts": 1})
    old = store.claim()
    clock.advance(11)
    assert store.claim() is None
    assert store.get_task("alice", task["id"])["state"] == "failed"
    assert store.complete(old) is False


def test_restart_preserves_jobs_and_replay_cursor(store, clock):
    task = submit(store)
    first = store.task_events("alice", task["id"])[0]
    reopened = Store(store.database, clock=clock, lease_seconds=10)
    lease = reopened.claim()
    reopened.complete(lease)
    assert [e["kind"] for e in reopened.task_events("alice", task["id"], first["id"])] == ["running", "succeeded"]


def test_tasks_events_documents_and_search_are_scoped(store):
    task = submit(store)
    store.complete(store.claim())
    document = store.get_task("alice", task["id"])["result_document_id"]
    with pytest.raises(Missing):
        store.get_task("bob", task["id"])
    with pytest.raises(Missing):
        store.cancel("bob", task["id"])
    with pytest.raises(Missing):
        store.task_events("bob", task["id"])
    with pytest.raises(Missing):
        store.get_document("bob", document)
    assert store.search("bob", "retry") == []
    assert store.search("alice", "retry")[0]["id"] == document
    assert store.statistics("bob") == {"documents": 0, "tasks": {}}


def test_search_treats_operators_as_literal_tokens(store):
    task = submit(store, payload={**PAYLOAD, "text": "retry OR fallback"})
    store.complete(store.claim())
    assert len(store.search("alice", 'retry OR fallback')) == 1
    assert store.search("alice", "retry OR impossible") == []
    assert store.search("alice", "' OR 1=1; DROP TABLE documents; --") == []
    assert store.get_task("alice", task["id"])["state"] == "succeeded"


def test_parallel_workers_finish_real_index_jobs(store):
    for number in range(40):
        submit(store, key=f"document-{number:08d}")

    def consume():
        worker = Worker(store)
        count = 0
        while worker.run_once():
            count += 1
        return count

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(lambda _: consume(), range(4))) == 40
    assert store.statistics("alice") == {"documents": 40, "tasks": {"succeeded": 40}}


def test_worker_retries_a_transient_storage_error(store, monkeypatch):
    task = submit(store)
    def unavailable(lease, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(store, "complete", unavailable)
    assert Worker(store).run_once()
    assert store.get_task("alice", task["id"])["state"] == "queued"


def test_unknown_schema_is_not_overwritten(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="schema"):
        Store(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_abrupt_worker_process_exit_is_recovered(store, clock):
    task = submit(store)
    code = """
import json, os, sys
from dataclasses import asdict
from pathlib import Path
from knowledge_task_api.store import Store
lease = Store(Path(sys.argv[1]), clock=lambda: 1000, lease_seconds=10).claim()
print(json.dumps(asdict(lease)), flush=True)
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", code, str(store.database)], capture_output=True, text=True, check=False)
    assert result.returncode == 17
    old = Lease(**json.loads(result.stdout))
    assert store.get_task("alice", task["id"])["state"] == "running"
    clock.advance(11)
    new = store.claim()
    assert new.attempt == 2
    assert store.complete(old) is False
    assert store.complete(new) is True
