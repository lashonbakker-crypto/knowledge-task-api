![Knowledge Task API](assets/banner.svg)

# Knowledge Task API

[![Tests](https://github.com/lashonbakker-crypto/knowledge-task-api/actions/workflows/ci.yml/badge.svg)](https://github.com/lashonbakker-crypto/knowledge-task-api/actions)

**A durable knowledge-indexing backend for retrieval and future agent tools.**

[中文](README.md) · [Architecture](docs/architecture.md) · [Roadmap](docs/roadmap.md) · [Validation](docs/validation.md)

Submit a text document as a durable job, recover failed or abandoned attempts, and retrieve tenant-scoped original excerpts through an authenticated API. This is a working backend MVP. LLM generation, embeddings and complete agent orchestration are future milestones.

**Python 3.11+ · FastAPI · SQLite WAL · FTS5 · pytest.**

Implemented: Bearer authentication, tenant-scoped resource access, role-restricted administrative counts, concurrent request idempotency, bounded retries, expiring/fenced leases, atomic document/index publication, cancellation, durable event cursors, SSE replay, request limits and metadata-only logs.

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

Status codes: 401 authentication; 403 administrator role; 404 missing or foreign resource; 409 conflict; 413 body limit; 422 invalid parameters; 503 unavailable storage.

## Tests and configuration

```bash
python -m pytest
python scripts/demo.py
```

See [actual validation](docs/validation.md) and [Actions](https://github.com/lashonbakker-crypto/knowledge-task-api/actions). Tests exercise real SQLite transactions, concurrent claims, an abruptly exited process, rollback, retries, fencing, API access and event replay.

Normal startup requires `KTA_KEYS_JSON`. The shape is shown in [.env.example](.env.example), which is documentation and is not loaded automatically. `KTA_DATABASE` selects the database file. Configure private access tokens and HTTPS before deployment. Registration, token rotation and OAuth are not implemented.

SQLite is a single-host starting point. Attempts may execute more than once after a crash; fencing and transactions constrain publication, and do not provide general exactly-once execution. Documents allow up to 64000 characters; JSON write bodies up to 256 KiB; queries up to 12 tokens and 20 returned hits. SSE connections close after at most 30 seconds and can reconnect with a cursor.

Unicode61 lexical tokenization has limited behavior for continuous Chinese text and does not provide semantic retrieval. FTS ranking statistics share the database; row access is tenant-filtered, but statistical isolation is not promised. Rate limits, quotas, fair scheduling, PostgreSQL, vector search and model calls remain roadmap work. This MVP is not ready for an unrestricted public service.

The initial version was created with AI assistance. Record your actual modifications and experiments using the [devlog template](docs/devlog-template.md); resume claims should reflect real contributions and evidence. Original implementation under [MIT](LICENSE). References: [primary documentation](docs/references.md).
