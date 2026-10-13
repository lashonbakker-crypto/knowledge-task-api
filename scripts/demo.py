"""Launch an isolated real HTTP server and verify a complete indexing flow."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "knowledge_task_api", "serve", "--demo", "--database", str(root/"knowledge.sqlite3"), "--port", str(port)],
                cwd=REPO_ROOT, stdout=log, stderr=log,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            try:
                def call(path, *, method="GET", payload=None, token="demo-alice-local-only", key=None):
                    headers = {"Authorization": "Bearer " + token}
                    if key:
                        headers["Idempotency-Key"] = key
                    data = None
                    if payload is not None:
                        data = json.dumps(payload).encode()
                        headers["Content-Type"] = "application/json"
                    request = urllib.request.Request(base+path, data=data, headers=headers, method=method)
                    try:
                        response = urllib.request.urlopen(request, timeout=3)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        text = response.read().decode()
                        result = text if response.headers.get("Content-Type", "").startswith("text/event-stream") else json.loads(text)
                        return response.status, result, response.headers

                deadline = time.monotonic()+20
                while True:
                    try:
                        if call("/health")[0] == 200:
                            break
                    except (urllib.error.URLError, TimeoutError):
                        pass
                    if process.poll() is not None or time.monotonic()>deadline:
                        raise RuntimeError("HTTP server did not become ready")
                    time.sleep(0.05)
                document = {"title": "Lease recovery", "text": "A durable lease prevents stale workers from publishing results."}
                code, first, _ = call("/v1/documents", method="POST", payload=document, key="demo-document-01")
                assert code == 202
                code, replay, headers = call("/v1/documents", method="POST", payload=document, key="demo-document-01")
                assert code == 202 and replay["id"] == first["id"]
                assert headers["Idempotency-Replayed"] == "true"
                assert call("/v1/documents", method="POST", payload={**document, "text": "different"}, key="demo-document-01")[0] == 409
                path = "/v1/tasks/" + first["id"]
                while True:
                    code, task, _ = call(path)
                    assert code == 200
                    if task["state"] == "succeeded":
                        break
                    if task["state"] in {"failed", "cancelled"} or time.monotonic()>deadline:
                        raise RuntimeError("indexing did not complete")
                    time.sleep(0.05)
                code, hits, _ = call("/v1/search?"+urllib.parse.urlencode({"q": "durable lease"}))
                assert code == 200 and hits["items"][0]["id"] == task["result_document_id"]
                assert call(path, token="demo-bob-local-only-1")[0] == 404
                code, stream, _ = call(path+"/stream")
                assert code == 200 and "event: succeeded" in stream and "event: end" in stream
                print(json.dumps({"real_http": True, "idempotent_replay": True, "conflict_status": 409, "cross_tenant_status": 404, "task_state": task["state"], "search_hits": len(hits["items"]), "sse_replay": True}, indent=2))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        text = (root/"server.log").read_text(encoding="utf-8")
        assert "demo-alice-local-only" not in text
        assert document["text"] not in text


if __name__ == "__main__":
    main()
