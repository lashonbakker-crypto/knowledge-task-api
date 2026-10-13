# Changelog

## Unreleased — 2026-10-13

- Use the standard `macos-15-intel` CI runner after GitHub reported ARM capacity constraints. The same tests and real HTTP demo run there; Apple Silicon coverage is not claimed for this round.
- Fix an SSE race that could send `end` before the persisted terminal event when a task completed, failed, or was cancelled between the event and state reads. Read task state before each event page and only end after draining a terminal task's backlog.
- Add six deterministic regressions for the three terminal states on initial and resumed streams, plus a 201-event pagination check. State changes use real SQLite transactions.
- Add a beginner-friendly Chinese walkthrough of transactions, cursors, the failing interleaving, and a runnable seven-scenario exercise. No model calls or new runtime dependencies are introduced.

## 0.1.0 — 2026-10-13

- Initial durable knowledge-indexing backend: authenticated and tenant-scoped resources, idempotent submissions, retries, fenced leases, cancellation, atomic indexing, lexical retrieval, persistent events and SSE replay.
- Initial local validation: 40 behavior tests and a real HTTP demonstration. See `docs/validation.md` for scope and limitations.
