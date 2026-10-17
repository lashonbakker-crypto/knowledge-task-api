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

## SSE terminal-event fix — 2026-10-13

- Added six deterministic scenarios: succeeded / failed / cancelled, each on an initial and resumed SSE stream. All six failed against the original implementation by missing the last persisted event, and all six pass with the corrected read order.
- A 201-event terminal backlog is delivered in order with no missing or duplicate IDs and exactly one final `end` event.
- Local Python 3.12.14 / Linux: all **47 behavior cases passed**. The real HTTP demo also passed submission, replay, conflict, indexing, search, tenant isolation and persisted SSE replay.
- The race tests use real SQLite transactions with a controlled transition after an event-read snapshot. The HTTP demo verifies the normal network flow; it does not independently force that exact interleaving over a network connection.
- Existing test-transport deprecation warning remains. No runtime dependencies changed.

## Continuous integration

The configured matrix uses Linux / Python 3.11, 3.12, 3.14, Windows / Python 3.12 and macOS 15 (Intel) / Python 3.12. GitHub reported capacity constraints for the ARM macOS runners during this maintenance round, so the standard Intel label is used to complete the same checks. This does not establish Apple Silicon coverage. Available labels: [official runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). Every job installs the project with the recorded constraints, runs the behavior tests and launches the real HTTP demo. A workflow configuration alone is not evidence of success: inspect [actual runs](https://github.com/lashonbakker-crypto/knowledge-task-api/actions).

Public deployment, PostgreSQL, hard execution timeouts, model/agent behavior and external tool side effects have not been validated in this version.

## Lease heartbeats — 2026-10-16

- Local Python 3.12.14 / Linux: **70 behavior cases passed**, including live renewal past the initial deadline, rejection at the exact expiry boundary, takeover fencing, cancellation during processing, failed renewal, stopping, and invalid duration configuration.
- `python scripts/lease_demo.py` used real SQLite and a real heartbeat thread with an explicitly advanced clock: the long task completed in one attempt after its original deadline; an obsolete worker and a cancelled worker could neither renew nor publish. This is a deterministic correctness exercise, not a real-time latency benchmark.
- `python scripts/demo.py` passed the separate genuine HTTP flow with the default processing path. The injected long callback is tested locally through Worker rather than over HTTP.
- Reproduced the original `NaN` lease bug against the old source: SQLite stored a NULL deadline, preventing both recovery and completion. The new validation refuses it before creating a database.
- No schema migration or runtime dependency changes. A live blocked callback can continue heartbeating; hard deadlines and external-side-effect guarantees remain out of scope. The existing test-transport deprecation warning remains.
- Implementation commit `c0fdd3134125709bdbd29ef54339f2e85b083bcd` passed all five jobs in [Actions run 38843619518](https://github.com/lashonbakker-crypto/knowledge-task-api/actions/runs/38843619518): Linux / Python 3.11, 3.12, 3.14; Windows / 3.12; Intel macOS / 3.12. Every job passed the behavior tests, real HTTP demo and deterministic heartbeat exercise. Actual job summaries and Linux logs were inspected.

## Processing budgets and tool bridge — 2026-10-17

- Local Python 3.12.14 / Linux: **102 behavior cases passed**. New budget scenarios cover expiry while a callback is running, exact-deadline refusal, cooperative checkpoints, shutdown, stale timeout fencing, configured HTTP-app budgets and persisted terminal SSE events.
- A controlled clock advanced only after the real SQLite publication transaction acquired its lock; publication was refused at the deadline. This checks the location of the guard, not a real-time guarantee.
- `budget_demo.py` used real SQLite with a controlled processing clock: processing_timeout, zero published documents and no automatic retry. Existing real-HTTP, heartbeat and read-only tool demos also passed. The tool demo made zero model calls.
- The old local dependency directory contained a truncated Python file; validation used a fresh virtual environment with the recorded dependency constraints. No dependency pins were changed. Existing Starlette test-transport deprecation remains.
- Arbitrary blocked callbacks can retain threads after losing ownership; storage outages can delay the failure record. No forced termination, cross-attempt total deadline, production-scale claim or model integration is implied. Actual cross-platform CI must be inspected separately.
