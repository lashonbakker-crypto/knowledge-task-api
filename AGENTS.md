# Project conventions

- This project is a knowledge-indexing backend, not a completed LLM agent.
- Implement real behavior and distinguish implemented features from roadmap items.
- Require authentication for documents, tasks, searches, events and administrative statistics. Keep tenant checks in repository queries.
- Use SQL parameters, bounded requests, persistent events and fenced leases.
- Execution may repeat after crashes; never claim exactly-once execution.
- Do not log tokens, request bodies, document text or raw search queries.
- Run the behavior tests and the real HTTP demo after relevant changes. Inspect actual GitHub CI before claiming it passed.
- Keep the Chinese and English README aligned. Record limitations and measured validation without invented production numbers.
- Never commit databases, env files, credentials or personal documents.
