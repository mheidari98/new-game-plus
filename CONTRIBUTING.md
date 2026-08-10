# Contributing

```bash
make setup
make test     # pytest + vitest + invariant checks, no network
```

Nothing needs a secret. IGDB is the only key the project will ever take, and its absence must
produce null columns rather than a failure.

Behind a restrictive network, pass `--proxy http://127.0.0.1:2080`. PlayStation and Metacritic
answer directly; HowLongToBeat and IGDB do not.

## Before you change anything

Read [AGENTS.md](AGENTS.md). It carries the pitfalls and the enforced invariants, and it is the
single source for both humans and coding agents — this file deliberately does not repeat it.

`scripts/check_invariants.py` fails the build on seven rules where a violation is silent and
expensive. If one blocks you, read why it exists before working around it; each was added after
the corresponding bug reached a live run.

## Testing

Test first. If a test passes the moment you write it, you are describing existing behaviour
rather than driving new behaviour — mutate the implementation to prove the test can fail, or
delete the test.

The suites need no network. Anything that needs the live store belongs in a manual run:

```bash
python crawler/main.py --once --limit 25 -v
```

## Pull requests

CI runs the Python suite, the TypeScript suite, the invariant checks and a site build. The daily
crawl workflow is separate and should only ever fail for reasons about *data*.
