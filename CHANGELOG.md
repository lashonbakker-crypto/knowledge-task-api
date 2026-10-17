# Changelog

## 0.4.0 — 2026-10-17

- Bound pending task admission per tenant (default 100) and globally (1000). Count queued/running work and insert under one SQLite write transaction, with an additive tenant/state index for existing version-1 databases.
- Return 429 and advisory `Retry-After: 1` without creating a task/event or reserving the rejected key. Preserve idempotent replay and 409 conflicts at capacity. Terminal states free slots; delayed retries continue to count.
- Add positive-integer configuration, concurrent independent-connection regressions, a real HTTP capacity demo and bilingual guidance. This is admission control; fair scheduling, rate limits and storage retention are still future work.

## 0.3.0 — 2026-10-17

- Add a default 300-second monotonic processing budget per claimed attempt, terminal fenced `processing_timeout`, and deadline rechecking inside the publication transaction. Expired ownership cannot terminate a replacement.
- Keep legacy processors compatible and add optional cooperative contexts, explicit environment configuration and a runnable deterministic budget exercise. No forced thread termination, schema migration or external-side-effect guarantee.

- Validate 1–12 searchable Unicode tokens in agent tool queries before HTTP calls, with negative and multilingual regression tests.

- Add an opt-in, read-only function-tool bridge for tenant-scoped search and bounded source-document reads. No model execution or new runtime dependency.
- Add real-SQLite/TestClient demo and regression tests for tenant isolation, invalid tool arguments, source truncation and refusal of model-provided writes.

## 0.2.0 — 2026-10-16

- Add fenced lease renewal and worker heartbeats, durable `lease_renewed` events, and an optional Python processing hook. Renewal keeps the attempt and token; expired or cancelled ownership cannot be revived.
- Refuse nonfinite, nonpositive or boolean durations before database creation; `NaN` previously produced an unrecoverable running task with a NULL deadline. Validate request-size configuration as a positive integer.
- Add deterministic long-processing, failed-heartbeat, cancellation, stopping and configuration regressions, plus a runnable real-SQLite heartbeat exercise and a Chinese tutorial. HTTP routes and schema version remain compatible.

## Maintenance — 2026-10-13

- Use the standard `macos-15-intel` CI runner after GitHub reported ARM capacity constraints. The same tests and real HTTP demo run there; Apple Silicon coverage is not claimed for this round.
- Fix an SSE race that could send `end` before the persisted terminal event when a task completed, failed, or was cancelled between the event and state reads. Read task state before each event page and only end after draining a terminal task's backlog.
- Add six deterministic regressions for the three terminal states on initial and resumed streams, plus a 201-event pagination check. State changes use real SQLite transactions.
- Add a beginner-friendly Chinese walkthrough of transactions, cursors, the failing interleaving, and a runnable seven-scenario exercise. No model calls or new runtime dependencies are introduced.

## 0.1.0 — 2026-10-13

- Initial durable knowledge-indexing backend: authenticated and tenant-scoped resources, idempotent submissions, retries, fenced leases, cancellation, atomic indexing, lexical retrieval, persistent events and SSE replay.
- Initial local validation: 40 behavior tests and a real HTTP demonstration. See `docs/validation.md` for scope and limitations.
