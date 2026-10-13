# Contributing

Read [architecture](docs/architecture.md), run `python -m pytest` and `python scripts/demo.py`, then propose one concrete problem or behavior change. Use synthetic documents and local example tokens.

For concurrency changes, include a reproducible interleaving or fault injection. Preserve tenant checks, fenced publication and atomic events. Do not remove failing tests or use sleeps to hide races.

Document public API changes and distinguish current behavior from roadmap work. Keep both READMEs aligned. Never submit databases, private documents, env files or access tokens. New providers and external tools require separate credentials and explicit configuration; default execution remains local indexing.
