"""Show a real read-only tool call against an isolated SQLite-backed API.

Requires development dependencies: pip install -c requirements.lock '.[test]'.
No external model, API account, network port or credentials are required.
"""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from knowledge_task_api.agent_bridge import ReadOnlyToolBridge
from knowledge_task_api.api import create_app
from knowledge_task_api.config import Settings
from knowledge_task_api.store import Store
from knowledge_task_api.worker import Worker


def main():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "agent-tool-demo.sqlite3"
        settings = Settings.demo(database, worker_enabled=False)
        store = Store(database)
        with TestClient(create_app(settings, store=store)) as client:
            created = client.post(
                "/v1/documents",
                headers={"Authorization": "Bearer demo-alice-local-only", "Idempotency-Key": "read-only-tool-demo"},
                json={"title": "Lease ownership", "text": "Durable leases fence stale workers from writing results."},
            )
            assert created.status_code == 202, created.status_code
            assert Worker(store).run_once()
            alice = ReadOnlyToolBridge(client, "demo-alice-local-only")
            bob = ReadOnlyToolBridge(client, "demo-bob-local-only-1")
            found = alice.execute("search_knowledge", {"query": "durable leases", "limit": 3})
            assert found["ok"] and len(found["hits"]) == 1
            source = alice.execute("read_knowledge_source", {"document_id": found["hits"][0]["document_id"]})
            assert source["ok"] and "stale workers" in source["excerpt"]
            other = bob.execute("search_knowledge", {"query": "durable leases"})
            assert other["ok"] and other["hits"] == []
            print(json.dumps({
                "tool_calls": ["search_knowledge", "read_knowledge_source"],
                "search_hits": len(found["hits"]),
                "source_truncated": source["truncated"],
                "cross_tenant_hits": len(other["hits"]),
                "model_calls": 0,
                "writes_via_bridge": 0,
            }, indent=2))


if __name__ == "__main__":
    main()
