"""Deterministic lease exercise with real SQLite and a real heartbeat thread."""

import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge_task_api.store import Store
from knowledge_task_api.worker import Worker


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def advance(self, seconds):
        with self.lock:
            self.now += seconds


def main():
    with tempfile.TemporaryDirectory() as temporary:
        clock = Clock()
        store = Store(Path(temporary) / "leases.sqlite3", clock=clock, lease_seconds=10)
        payload = {"title": "Lease exercise", "text": "Public teaching fixture", "max_attempts": 3}
        task, _ = store.enqueue("alice", "long-processing", payload, "lease-demo")
        renewed = threading.Event()
        original_renew = store.renew

        def observed_renew(lease):
            result = original_renew(lease)
            if result:
                renewed.set()
            return result

        store.renew = observed_renew

        def process(lease):
            clock.advance(8)
            if not renewed.wait(5):
                raise RuntimeError("heartbeat did not renew the live lease")
            clock.advance(4)
            assert store.claim() is None, "a competitor reclaimed a renewed task"

        Worker(store, processor=process, heartbeat_interval=0.01).run_once()
        result = store.get_task("alice", task["id"])
        assert result["state"] == "succeeded" and result["attempt"] == 1

        stale_task, _ = store.enqueue("alice", "expired-owner", payload, "lease-demo")
        old = store.claim()
        clock.advance(10)
        assert not store.renew(old), "an expired lease was revived"
        replacement = store.claim()
        assert replacement is not None and replacement.token > old.token
        assert not store.complete(old), "an obsolete owner published"
        assert store.complete(replacement)
        assert store.get_task("alice", stale_task["id"])["attempt"] == 2

        cancelled, _ = store.enqueue("alice", "cancelled-owner", payload, "lease-demo")
        lease = store.claim()
        store.cancel("alice", cancelled["id"])
        assert not store.renew(lease) and not store.complete(lease)
        assert store.statistics("alice")["documents"] == 2

        print(json.dumps({"real_sqlite": True, "controlled_clock": True,
                          "past_original_deadline": "succeeded in one attempt",
                          "expired_owner": "renewal and publication refused",
                          "cancelled_owner": "renewal and publication refused"}, indent=2))


if __name__ == "__main__":
    main()
