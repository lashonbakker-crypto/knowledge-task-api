"""Real SQLite budget exercise with deterministic checkpoints, no long sleep."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowledge_task_api.store import Store
from knowledge_task_api.worker import Worker


def main():
    with tempfile.TemporaryDirectory() as directory:
        now = [1000.0]
        clock = lambda: now[0]
        store = Store(Path(directory) / "budget.sqlite3", lease_seconds=30)
        payload = {"title": "Budget exercise", "text": "Public teaching fixture", "max_attempts": 3}
        task, _ = store.enqueue("alice", "budget-exercise", payload, "budget-demo")

        def process(lease, context):
            assert context.remaining_seconds == 2
            now[0] += 2
            context.checkpoint()
            raise AssertionError("checkpoint must prevent this line")

        Worker(store, processor_with_context=process, max_processing_seconds=2, processing_clock=clock).run_once()
        result = store.get_task("alice", task["id"])
        assert result["state"] == "failed" and result["error_code"] == "processing_timeout"
        assert store.statistics("alice")["documents"] == 0
        assert store.claim() is None
        print(json.dumps({"real_sqlite": True, "controlled_processing_clock": True,
                          "task_state": result["state"], "error_code": result["error_code"],
                          "published_documents": 0, "automatic_retry": False}, indent=2))


if __name__ == "__main__":
    main()
