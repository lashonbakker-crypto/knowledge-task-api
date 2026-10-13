import json
import logging

import pytest

from knowledge_task_api.api import MAX_CURSOR
from knowledge_task_api.config import Settings
from knowledge_task_api.worker import Worker

DOCUMENT = {"title": "Reliable jobs", "text": "Durable retries recover a task after a worker crash."}
BOB = {"Authorization": "Bearer demo-bob-local-only-1"}
ADMIN = {"Authorization": "Bearer demo-admin-local-only"}


def create(client, headers):
    result = client.post("/v1/documents", json=DOCUMENT, headers=headers)
    assert result.status_code == 202, result.text
    return result.json()


def test_end_to_end_index_and_retrieve(client, headers, store):
    task = create(client, headers)
    assert task["state"] == "queued"
    assert Worker(store).run_once()
    done = client.get(f"/v1/tasks/{task['id']}", headers=headers).json()
    assert done["state"] == "succeeded"
    document = client.get(f"/v1/documents/{done['result_document_id']}", headers=headers)
    assert document.json()["body"] == DOCUMENT["text"]
    hits = client.get("/v1/search", params={"q": "worker crash"}, headers=headers).json()
    assert hits["items"][0]["id"] == done["result_document_id"]


def test_idempotent_replay_and_conflict(client, headers):
    first = create(client, headers)
    replay = client.post("/v1/documents", json=DOCUMENT, headers=headers)
    assert replay.json()["id"] == first["id"]
    assert replay.headers["Idempotency-Replayed"] == "true"
    conflict = client.post("/v1/documents", json={**DOCUMENT, "text": "Different"}, headers=headers)
    assert conflict.status_code == 409


@pytest.mark.parametrize("endpoint", ["/v1/search?q=worker", "/v1/tasks/missing", "/v1/documents/missing", "/v1/tasks/missing/events", "/v1/tasks/missing/stream", "/v1/stats", "/v1/admin/stats"])
def test_read_endpoints_require_authentication(client, endpoint):
    result = client.get(endpoint)
    assert result.status_code == 401
    assert result.headers["WWW-Authenticate"] == "Bearer"


def test_write_endpoints_require_authentication(client):
    assert client.post("/v1/documents", json=DOCUMENT, headers={"Idempotency-Key": "document-request-01"}).status_code == 401
    assert client.post("/v1/tasks/missing/cancel").status_code == 401
    assert client.get("/health").status_code == 200


def test_cross_tenant_access_is_not_found(client, headers, store):
    task = create(client, headers)
    Worker(store).run_once()
    doc = store.get_task("alice", task["id"])["result_document_id"]
    for path in [f"/v1/tasks/{task['id']}", f"/v1/tasks/{task['id']}/events", f"/v1/tasks/{task['id']}/stream", f"/v1/documents/{doc}"]:
        assert client.get(path, headers=BOB).status_code == 404
    assert client.post(f"/v1/tasks/{task['id']}/cancel", headers=BOB).status_code == 404
    assert client.get("/v1/search?q=worker", headers=BOB).json()["items"] == []


def test_admin_statistics_are_role_restricted(client, headers):
    create(client, headers)
    assert client.get("/v1/admin/stats", headers=headers).status_code == 403
    assert client.get("/v1/admin/stats", headers=ADMIN).json()["tasks"] == {"queued": 1}
    assert client.get("/v1/stats", headers=BOB).json()["tasks"] == {}


def test_cancel_and_sse_replay_after_cursor(client, headers, store):
    task = create(client, headers)
    path = f"/v1/tasks/{task['id']}"
    first = client.get(path+"/events", headers=headers).json()["next_cursor"]
    Worker(store).run_once()
    response = client.get(path+"/stream", headers={**headers, "Last-Event-ID": str(first)})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: queued" not in response.text
    assert "event: running" in response.text
    assert "event: succeeded" in response.text
    assert "event: end" in response.text
    assert client.post(path+"/cancel", headers=headers).status_code == 409


@pytest.mark.parametrize("cursor", ["not-a-number", "-1", str(MAX_CURSOR+1)])
def test_invalid_event_cursor_is_controlled(client, headers, cursor):
    task = create(client, headers)
    path = f"/v1/tasks/{task['id']}/stream"
    assert client.get(path, headers={**headers, "Last-Event-ID": cursor}).status_code == 400


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("resume", [False, True])
def test_stream_delivers_terminal_event_when_task_finishes_during_poll(
    client, headers, store, monkeypatch, terminal, resume
):
    task = create(client, headers)
    lease = store.claim()
    read_events = store.task_events
    cursor = read_events("alice", task["id"])[-1]["id"] if resume else 0
    transitioned = False

    def read_then_finish(tenant, task_id, after=0, limit=100):
        nonlocal transitioned
        items = read_events(tenant, task_id, after, limit)
        if not transitioned:
            transitioned = True
            if terminal == "succeeded":
                assert store.complete(lease)
            elif terminal == "failed":
                assert store.fail(lease, retryable=False)
            else:
                assert store.cancel(tenant, task_id)["state"] == "cancelled"
        return items

    # Commit a real state/event transaction after the event-read snapshot.
    monkeypatch.setattr(store, "task_events", read_then_finish)
    response = client.get(
        f"/v1/tasks/{task['id']}/stream",
        headers={**headers, "Last-Event-ID": str(cursor)},
    )
    assert response.status_code == 200
    blocks = [
        dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        for block in response.text.split("\n\n")
        if block and not block.startswith(":")
    ]
    expected = read_events("alice", task["id"], cursor)
    assert [int(block["id"]) for block in blocks if "id" in block] == [
        item["id"] for item in expected
    ]
    assert [block["event"] for block in blocks] == [
        item["kind"] for item in expected
    ] + ["end"]
    assert json.loads(blocks[-2]["data"]) == expected[-1]
    assert json.loads(blocks[-1]["data"]) == {"state": terminal}


def test_terminal_stream_drains_multiple_event_pages(client, headers, store):
    task = create(client, headers)
    lease = store.claim()
    with store.connection(write=True) as connection:
        for number in range(198):
            store.event(connection, task["id"], "progress", {"step": number}, store.clock())
    assert store.complete(lease)
    response = client.get(f"/v1/tasks/{task['id']}/stream", headers=headers)
    assert response.status_code == 200
    ids = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
    expected = store.task_events("alice", task["id"], limit=300)
    assert len(ids) == 201
    assert ids == [item["id"] for item in expected]
    assert response.text.count("event: end\n") == 1
    assert response.text.endswith('event: end\ndata: {"state": "succeeded"}\n\n')


def test_huge_query_cursor_does_not_reach_sqlite(client, headers):
    task = create(client, headers)
    assert client.get(f"/v1/tasks/{task['id']}/events", params={"after": 10**100}, headers=headers).status_code == 422


def test_missing_idempotency_key_and_unknown_fields_are_rejected(client, headers):
    assert client.post("/v1/documents", json=DOCUMENT, headers={"Authorization": headers["Authorization"]}).status_code == 422
    assert client.post("/v1/documents", json={**DOCUMENT, "tenant": "bob"}, headers=headers).status_code == 422


def test_actual_body_limit_cannot_be_bypassed_by_content_length(client, headers):
    data = b'x' * (256*1024+1)
    result = client.post("/v1/documents", content=data, headers={**headers, "Content-Length": "1", "Content-Type": "application/json"})
    assert result.status_code == 413
    assert client.get("/v1/stats", headers=headers).json()["tasks"] == {}


def test_unicode_validation_error_is_safe_and_does_not_reflect_document(client, headers):
    data = b'{"title":"private test title","text":"\\ud800"}'
    result = client.post("/v1/documents", content=data, headers={**headers, "Content-Type": "application/json"})
    assert result.status_code == 422
    assert "private test title" not in result.text
    assert "input" not in result.json()["detail"][0]


def test_request_logs_do_not_contain_credentials_or_documents(client, headers, caplog):
    with caplog.at_level(logging.INFO, logger="knowledge_task_api.requests"):
        result = client.post("/v1/documents", json=DOCUMENT, headers={**headers, "X-Request-ID": "known-request-01"})
    assert result.headers["X-Request-ID"] == "known-request-01"
    assert "known-request-01" in caplog.text
    assert headers["Authorization"] not in caplog.text
    assert DOCUMENT["text"] not in caplog.text
    assert DOCUMENT["title"] not in caplog.text


def test_no_implicit_production_demo_keys(monkeypatch):
    monkeypatch.delenv("KTA_KEYS_JSON", raising=False)
    with pytest.raises(ValueError, match="required"):
        Settings.from_env()


def test_request_logs_exclude_query_and_unmatched_path_values(client, headers, caplog):
    with caplog.at_level(logging.INFO, logger="knowledge_task_api.requests"):
        client.get("/v1/search", params={"q": "private-query-canary"}, headers=headers)
        client.get("/private-path-canary", headers=headers)
    assert "private-query-canary" not in caplog.text
    assert "private-path-canary" not in caplog.text
    assert '"route": "unmatched"' in caplog.text


def test_duplicate_access_tokens_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="distinct"):
        Settings(tmp_path/"db.sqlite3", {"alice": {"key": "same-public-demo-token", "role": "user"}, "bob": {"key": "same-public-demo-token", "role": "user"}})
