"""Exercise backpressure and idempotency through a real, isolated HTTP server."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "capacity.sqlite3"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (Path(temporary) / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--server", str(database), str(port)],
                cwd=ROOT, stdout=log, stderr=log,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
            try:
                def call(path, *, tenant="alice", key=None, payload=None):
                    token = "demo-alice-local-only" if tenant == "alice" else "demo-bob-local-only-1"
                    headers = {"Authorization": "Bearer " + token}
                    if key:
                        headers["Idempotency-Key"] = key
                    if payload is not None:
                        headers["Content-Type"] = "application/json"
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}" + path,
                        data=json.dumps(payload).encode() if payload is not None else None,
                        headers=headers,
                    )
                    try:
                        response = urllib.request.urlopen(request, timeout=3)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        return response.status, json.loads(response.read()), response.headers

                deadline = time.monotonic() + 20
                while True:
                    try:
                        if call("/health")[0] == 200:
                            break
                    except (urllib.error.URLError, TimeoutError):
                        pass
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("HTTP server did not become ready")
                    time.sleep(0.05)
                payload = {"title": "Bounded queue", "text": "Only accepted work occupies capacity."}
                first = call("/v1/documents", key="capacity-first", payload=payload)
                assert first[0] == 202
                assert call("/v1/documents", key="capacity-second", payload=payload)[0] == 202
                limited = call("/v1/documents", key="capacity-next", payload=payload)
                assert limited[0] == 429 and limited[2]["Retry-After"] == "1"
                assert call("/v1/documents", tenant="bob", key="capacity-first", payload=payload)[0] == 202
                assert call("/v1/documents", tenant="bob", key="capacity-next", payload=payload)[0] == 429
                replay = call("/v1/documents", key="capacity-first", payload=payload)
                assert replay[0] == 202 and replay[1]["id"] == first[1]["id"]
                assert replay[2]["Idempotency-Replayed"] == "true"
                assert call("/v1/documents", key="capacity-first", payload={**payload, "text": "changed"})[0] == 409
                assert call("/v1/tasks/" + first[1]["id"] + "/cancel", payload={})[0] == 200
                assert call("/v1/documents", key="capacity-next", payload=payload)[0] == 202
                print(json.dumps({"real_http": True, "tenant_limit": 2, "global_limit": 3,
                                  "capacity_status": 429, "replay_when_full": True,
                                  "conflict_status": 409, "capacity_released_by_cancel": True}, indent=2))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        text = (Path(temporary) / "server.log").read_text(encoding="utf-8")
        assert "demo-alice-local-only" not in text and payload["text"] not in text


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--server":
        import uvicorn
        from knowledge_task_api.api import create_app
        from knowledge_task_api.config import Settings

        settings = replace(Settings.demo(Path(sys.argv[2]), worker_enabled=False),
                           max_pending_per_tenant=2, max_pending_total=3)
        uvicorn.run(create_app(settings), host="127.0.0.1", port=int(sys.argv[3]), access_log=False)
    else:
        main()
