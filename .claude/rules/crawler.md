---
paths:
  - "crawler/**/*.py"
  - "scripts/*.py"
---

# Crawler

Every outbound request goes through `ngp/net.py`. It owns the AIMD limiter, tenacity retries,
the HTTP/2 connection pool and the proxy setting; a bare `httpx` call bypasses all four.

A 429 means "later", not "never". Back off and retry — dropping the row loses data that the
run cannot recover. The TTL cursor makes even a hard abort lossless, so never trade a row for
a faster exit.

`Cache` shares one SQLite connection across five worker threads. `sqlite3` serialises statements
but not transactions, so an unguarded write raises "cannot start a transaction within a
transaction" and silently loses rows. Take `self._lock` in any method that touches `self._db`.

Enrichment is keyed per concept, not per product, and is resumable: a run that dies leaves rows
unstamped and the next one continues. Do not add a separate cursor.

`metGetConceptById` is redundant — `metGetProductById` returns the same fields plus the concept
id. Two operations per product, not three.

Prefer `python crawler/main.py --once --limit 25 -v` while iterating. A full crawl is ~4,700
requests against a live third-party API.
