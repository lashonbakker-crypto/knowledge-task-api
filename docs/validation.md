# Validation record

## Initial local run — 2026-10-13

- Python 3.12 / Linux; SQLite 3.53.1.
- `python -m pytest --junitxml=...`: **40 passed**, zero failed.
- `python scripts/demo.py`: launched a real local HTTP server and verified submission, background indexing, search, idempotent replay, 409 conflict, 404 foreign-task access and persisted SSE replay.
- Four independent worker threads / database connections indexed 40 synthetic jobs in a regression scenario, without duplicate document publication.
- A separate process claimed a task and abruptly exited with `os._exit`; after the controlled lease deadline, another worker recovered it and the old token could not publish.
- An injected FTS storage failure rolled back both document publication and task completion.
- Tests check actual write-body bytes, safe invalid-Unicode responses, integer cursor bounds and logs without tokens, document bodies, raw queries or unmatched path values.

These are correctness scenarios, not throughput, latency, uptime or production-scale claims. Fixed clocks make the lease boundaries reproducible; the HTTP demo uses normal clocks. The dependency versions used locally are retained in `requirements.lock` as installation constraints, not a platform-independent hash lock.

The installed Starlette test transport emits one deprecation warning for its HTTPX adapter. Tests pass; migrating that test adapter is a small follow-up task rather than a runtime failure.

## Continuous integration

The configured matrix uses Linux / Python 3.11, 3.12, 3.14 and Windows, macOS / Python 3.12. Every job installs the project with the recorded constraints, runs the behavior tests and launches the real HTTP demo. A workflow configuration alone is not evidence of success: inspect [actual runs](https://github.com/lashonbakker-crypto/knowledge-task-api/actions).

Public deployment, PostgreSQL, long-running lease renewal, model/agent behavior and external tool side effects have not been validated in this version.
