![Knowledge Task API](assets/banner.svg)

# Knowledge Task API

[![Tests](https://github.com/lashonbakker-crypto/knowledge-task-api/actions/workflows/ci.yml/badge.svg)](https://github.com/lashonbakker-crypto/knowledge-task-api/actions)

**A durable knowledge-indexing backend for retrieval and future agent tools.**

[中文](README.md) · [Architecture](docs/architecture.md) · [Roadmap](docs/roadmap.md) · [Validation](docs/validation.md)

Submit a text document as a durable job, recover failed or abandoned attempts, and retrieve tenant-scoped original excerpts through an authenticated API. This is a working backend MVP. LLM generation, embeddings and complete agent orchestration are future milestones.

**Python 3.11+ · FastAPI · SQLite WAL · FTS5 · pytest.**

Implemented: Bearer authentication, tenant-scoped resource access, role-restricted administrative counts, concurrent request idempotency, bounded retries, expiring/fenced leases with worker heartbeats, atomic document/index publication, cancellation, durable event cursors, SSE replay, request limits and metadata-only logs.

The SSE loop reads task state before each event page so a terminal transition between reads cannot close the stream ahead of its persisted event. Deterministic tests cover success, failure and cancellation on initial/resumed streams, plus a 201-event backlog. A [Chinese walkthrough](docs/01-sse-terminal-events.md) explains the race and provides a runnable exercise.

Long processing can extend a live lease without spending another attempt. Expired, cancelled or replaced owners cannot renew or publish. `create_app(..., processor=callback)` provides a Python processing hook; the HTTP payload stays the same. The [lease walkthrough](docs/02-lease-heartbeats.md) and `scripts/lease_demo.py` demonstrate the boundaries with real SQLite and a controlled clock. Heartbeats do not impose a processing timeout or protect external side effects.

## Read-only agent tool integration

The [read-only tool bridge](docs/03-agent-tool-bridge.md) exposes two function-tool schemas, `search_knowledge` and `read_knowledge_source`, backed by existing authenticated API GET endpoints. Model-provided arguments cannot choose URLs, tokens or write operations. Run `python scripts/agent_bridge_demo.py` to verify real SQLite retrieval and tenant isolation without calling a model. **This is not a complete agent runtime.**

Each attempt now has a monotonic processing budget (300 seconds by default). Live timed-out ownership is revoked and records terminal `processing_timeout`; it is not automatically retried. Publication checks the deadline again after acquiring the database lock. Use `processor_with_context(lease, context)` for cooperative checkpoints. This does not forcibly terminate arbitrary callbacks. See the [bilingual processing-budget guide](docs/04-processing-budgets.md).

Pending work is bounded to 100 tasks per tenant and 1000 globally by default. Admission counts queued and running tasks, including scheduled retries, inside the insertion transaction. A full queue returns 429 with an advisory `Retry-After: 1`; existing idempotent requests still replay and changed payloads still return 409. Configure positive integers `KTA_MAX_PENDING_PER_TENANT` and `KTA_MAX_PENDING_TOTAL` for the normal server. Terminal tasks free capacity. Fair scheduling, request-rate limits and historical storage retention remain future work. See the [bilingual capacity guide](docs/05-queue-capacity.md).

## Run locally

```bash
git clone https://github.com/lashonbakker-crypto/knowledge-task-api.git
cd knowledge-task-api
python -m venv .venv
source .venv/bin/activate
python -m pip install -c requirements.lock ".[test]"
python -m knowledge_task_api serve --demo
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1`. Demo mode uses public sample tokens and only binds to `127.0.0.1`. Open `http://127.0.0.1:8080/docs` for the API explorer; Alice's demo token is `demo-alice-local-only`.

Stop that server before running the isolated real-HTTP demo:

```bash
python scripts/demo.py
python scripts/lease_demo.py
python scripts/budget_demo.py
python scripts/capacity_demo.py
```

The script launches a temporary server and database, checks job submission, idempotent replay, conflicting requests, background indexing, retrieval, tenant boundaries and SSE, then terminates the server. No model account or external database is needed.

## API

| Method and route | Behavior |
| --- | --- |
| `GET /health` | Public database-read check and version |
| `POST /v1/documents` | Queue indexing; requires `Idempotency-Key` |
| `GET /v1/tasks/{id}` | Read your task |
| `POST /v1/tasks/{id}/cancel` | Cancel queued/running tasks; completed/failed returns 409 |
| `GET /v1/tasks/{id}/events` | Persisted events with `after` and `limit` |
| `GET /v1/tasks/{id}/stream` | SSE replay with cursor / `Last-Event-ID` |
| `GET /v1/documents/{id}` | Original document and source task |
| `GET /v1/search?q=...` | Tenant-filtered lexical AND search |
| `GET /v1/stats` | Your task and document counts |
| `GET /v1/admin/stats` | Admin-only aggregate counts |

Send `Authorization: Bearer TOKEN`. Submitting an identical decoded document with the same tenant and idempotency key returns the original task and `Idempotency-Replayed: true`; changing its content returns 409. A successful task includes `result_document_id`. The JSON payload accepts `title`, `text` and optional `max_attempts` (1–5, default 3).

Status codes: 401 authentication; 403 administrator role; 404 missing or foreign resource; 409 conflict; 413 body limit; 422 invalid parameters; 429 pending capacity reached; 503 unavailable storage.

## Tests and configuration

```bash
python -m pytest
python scripts/demo.py
python scripts/lease_demo.py
python scripts/budget_demo.py
```

See [actual validation](docs/validation.md) and [Actions](https://github.com/lashonbakker-crypto/knowledge-task-api/actions). Tests exercise real SQLite transactions, concurrent claims, an abruptly exited process, rollback, retries, fencing, API access and event replay.

Normal startup requires `KTA_KEYS_JSON`. The shape is shown in [.env.example](.env.example), which is documentation and is not loaded automatically. `KTA_DATABASE` selects the database file; `KTA_PROCESSING_SECONDS` sets the per-attempt budget for normal server/worker startup. Configure private access tokens and HTTPS before deployment. Registration, token rotation and OAuth are not implemented.

SQLite is a single-host starting point. Attempts may execute more than once after a crash; fencing and transactions constrain publication, and do not provide general exactly-once execution. Documents allow up to 64000 characters; JSON write bodies up to 256 KiB; queries up to 12 tokens and 20 returned hits. Heartbeats run at one third of the lease duration by default. Nonfinite or nonpositive durations are refused before database creation. Stopping prevents publication and waits up to five seconds; arbitrary callbacks need their own timeouts. SSE connections close after at most 30 seconds and can reconnect with a cursor.

Unicode61 lexical tokenization has limited behavior for continuous Chinese text and does not provide semantic retrieval. FTS ranking statistics share the database; row access is tenant-filtered, but statistical isolation is not promised. Request-rate limits, fair scheduling, PostgreSQL, vector search and model calls remain roadmap work. This MVP is not ready for an unrestricted public service.

The initial version was created with AI assistance. Record your actual modifications and experiments using the [devlog template](docs/devlog-template.md); resume claims should reflect real contributions and evidence. Original implementation under [MIT](LICENSE). References: [primary documentation](docs/references.md).
