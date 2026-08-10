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
unstamped and the next one continues. Do not add a separate cursor — a new source is a new
`cache.due(<source>, ttl)` pass, not new bookkeeping.

Cache a *miss* exactly like a hit. Otherwise every run re-asks Metacritic and HowLongToBeat
about the same few hundred titles that will never have an entry.

A transport failure is not a refusal. The host never answered, so it says nothing about our
pace: `net.py` retries it like a 502 and leaves the limiter alone.

HowLongToBeat gets its own `HttpClient` and its own much slower limiter, single-threaded, and it
runs in a thread alongside the store passes rather than after them. It is not sharing the store's
rate, and it should not be adding its wall clock to the store's either.

Every host is paced separately, so anything that talks to a second host belongs *in* the existing
per-concept task, not in a pass of its own. Sequential passes make the per-host rates apply end
to end, which is the thing the per-host limiters exist to avoid.

`metGetConceptById` is redundant — `metGetProductById` returns the same fields plus the concept
id. Two operations per product, not three.

Prefer `python crawler/main.py --limit 25 -v` while iterating. A full crawl is ~7,000
requests against a live third-party API.
