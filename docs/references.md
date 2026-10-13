# Primary references

Checked on 2026-10-13. Implementation and synthetic fixtures are original; these sources document the interfaces and tradeoffs used here.

- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/): starting and stopping the polling worker.
- [FastAPI security](https://fastapi.tiangolo.com/tutorial/security/): authentication dependency and OpenAPI integration.
- [SQLite transactions](https://sqlite.org/lang_transaction.html): write transactions and lock behavior.
- [SQLite FTS5](https://sqlite.org/fts5.html): virtual tables, Unicode61, BM25 and excerpts.
- [Python sqlite3](https://docs.python.org/3.12/library/sqlite3.html): parameter binding, independent connections and transactions.
- [Python hmac](https://docs.python.org/3.12/library/hmac.html): comparison of token digests.
- [pytest](https://docs.pytest.org/en/stable/): behavior fixtures and regression checks.

SQLite tests and the HTTP demonstration verify this implementation. Official documentation alone is not proof that a particular program preserves the stated invariants.
