#!/usr/bin/env python3
"""Enforce the invariants that documentation alone cannot.

Instructions in AGENTS.md/CLAUDE.md are context, not enforcement -- an agent
(or a person) can read them and still not follow them. Everything here is a
rule where a violation is silent, plausible-looking, and expensive, so it is
checked mechanically instead.

    python scripts/check_invariants.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
failures: list[str] = []


def fail(rule: str, detail: str) -> None:
    failures.append(f"{rule}\n    {detail}")


def py_files(root: str):
    return sorted((REPO / root).rglob("*.py"))


def strip_ts_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


# --- 1. one HTTP client -----------------------------------------------------
# A bare httpx call bypasses the rate limiter, which is the difference between
# a polite crawler and a banned IP. Nothing outside net.py may import it.
def check_single_http_client():
    for path in py_files("crawler"):
        if path.name in ("net.py", "check_invariants.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in ("httpx", "requests", "urllib3", "aiohttp"):
                    fail("outbound HTTP must go through ngp/net.py",
                         f"{path.relative_to(REPO)} imports {name}")


# --- 2. the scoring layer stays pure ---------------------------------------
# A reader auditing the ranking should have to read only these files, and they
# must give the same answer for the same inputs on any machine at any time.
IMPURE = {"httpx", "requests", "sqlite3", "random", "os", "socket", "subprocess"}


def check_scoring_is_pure():
    path = REPO / "crawler/ngp/components.py"
    for node in ast.walk(ast.parse(path.read_text())):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        for name in names:
            if name in IMPURE:
                fail("components.py must stay pure (no I/O, no clock, no randomness)",
                     f"imports {name}")

    # Strip comments first: score.ts documents its own purity rule in prose,
    # and matching that text would fail the file for describing itself.
    ts = strip_ts_comments((REPO / "site/src/lib/score.ts").read_text())
    for banned in ("fetch(", "localStorage", "document.", "Date.now(", "Math.random("):
        if banned in ts:
            fail("score.ts must stay pure", f"contains {banned}")


# --- 3. weights have exactly one source ------------------------------------
# weights.toml is copied into index.json and the browser reads it from there.
# A second hardcoded copy is how the site and crawler silently disagree.
def check_weights_single_source():
    code = strip_ts_comments((REPO / "site/src/lib/score.ts").read_text())
    for match in re.finditer(r"(quality|deal|value|psplus_extra)\s*:\s*([0-9]*\.[0-9]+)", code):
        fail("score.ts must read weights from index.json, not hardcode them",
             f"found {match.group(1)}: {match.group(2)}")


# --- 4. every fuzzy match is guarded ---------------------------------------
# "Mortal Kombat 11" scores above any sane threshold against "Mortal Kombat 1".
def check_fuzzy_matching_is_guarded():
    for path in py_files("crawler"):
        if path.name in ("titles.py", "check_invariants.py") or "tests" in path.parts:
            continue
        text = path.read_text()
        fuzzy = re.search(
            r"\b(SequenceMatcher|rapidfuzz|difflib|token_sort_ratio|partial_ratio"
            r"|token_set_ratio|fuzz\.)", text)
        if fuzzy and "numbers_compatible" not in text:
            fail("fuzzy matching must be gated by titles.numbers_compatible()",
                 f"{path.relative_to(REPO)} uses {fuzzy.group(1)} without the guard")


# --- 5. shared SQLite access is locked --------------------------------------
# One connection, five worker threads. sqlite3 serialises statements but not
# transactions, so an unguarded write loses rows. Seen live.
def check_cache_methods_take_the_lock():
    path = REPO / "crawler/ngp/cache.py"
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Cache")
    for method in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
        if method.name.startswith("__"):
            continue
        source = ast.unparse(method)
        if "self._db" in source and "self._lock" not in source:
            fail("every Cache method touching the db must take self._lock",
                 f"Cache.{method.name} does not")


# --- 6. persisted-query hashes stay pinned ----------------------------------
# Probing an allowlist is reconnaissance against a third party. Hashes are
# captured from the public web client, never generated or iterated.
def check_hashes_are_literals():
    text = (REPO / "crawler/ngp/store.py").read_text()
    for suspicious in ("hashlib", "sha256(", "itertools", "range(16"):
        if suspicious in text:
            fail("never generate or probe persisted-query hashes",
                 f"store.py contains {suspicious}")
    if len(re.findall(r'"[a-f0-9]{64}"', text)) < 4:
        fail("store.py should pin operation hashes as literals",
             "fewer than 4 pinned hashes found")


# --- 7. the npm registry stays public ---------------------------------------
# A private mirror in package-lock.json breaks CI and leaks internal hostnames
# into a public repo. This already happened once.
def check_public_npm_registry():
    npmrc = REPO / "site/.npmrc"
    if not npmrc.exists() or "registry.npmjs.org" not in npmrc.read_text():
        fail("site/.npmrc must pin the public npm registry", "missing or not pinned")

    lock = REPO / "site/package-lock.json"
    if lock.exists():
        hosts = set(re.findall(r'"resolved":\s*"https?://([^/]+)', lock.read_text()))
        for host in hosts - {"registry.npmjs.org"}:
            fail("package-lock.json must resolve only through the public registry",
                 f"found {host}")


CHECKS = [
    check_single_http_client,
    check_scoring_is_pure,
    check_weights_single_source,
    check_fuzzy_matching_is_guarded,
    check_cache_methods_take_the_lock,
    check_hashes_are_literals,
    check_public_npm_registry,
]

if __name__ == "__main__":
    for check in CHECKS:
        check()
    if failures:
        print(f"{len(failures)} invariant violation(s):\n", file=sys.stderr)
        for f in failures:
            print(f"  - {f}\n", file=sys.stderr)
        sys.exit(1)
    print(f"all {len(CHECKS)} invariants hold")
