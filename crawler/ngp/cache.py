"""Resumable TTL cursor and parsed-extract cache.

One mechanism does four jobs -- cold-start backfill, TTL refresh, catalogue
growth and crash resume -- because they are all the same question: "which
concepts have no fresh copy of source X?" A run that dies halfway simply
leaves its rows unstamped and the next run picks them up, so there is no
separate cursor to corrupt.

Caches the *parsed extract* (~800 B/product), not raw responses (~16 kB), so
the whole catalogue is ~14 MB rather than ~444 MB. Lives in actions/cache,
fully rebuildable, never committed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

DAY = 86400.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS concept (
    concept_id TEXT PRIMARY KEY,
    rank       INTEGER,
    product_id TEXT
);
CREATE TABLE IF NOT EXISTS fetched (
    source     TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    at         REAL NOT NULL,
    PRIMARY KEY (source, concept_id)
);
CREATE TABLE IF NOT EXISTS payload (
    source     TEXT NOT NULL,
    key        TEXT NOT NULL,
    body       TEXT NOT NULL,
    at         REAL NOT NULL,
    PRIMARY KEY (source, key)
);
"""


class Cache:
    def __init__(self, path=":memory:", now=time.time):
        self._now = now
        # One connection shared across worker threads. sqlite3 serialises
        # statements but NOT transactions, so concurrent writes raise "cannot
        # start a transaction within a transaction" and lose rows. Every
        # method that touches the db takes this lock; new ones must too.
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    def upsert_concepts(self, rows):
        """Re-enumerating the catalogue must not reset progress or duplicate."""
        with self._lock:
            self._db.executemany(
                "INSERT INTO concept (concept_id, rank, product_id) VALUES (?,?,?) "
                "ON CONFLICT(concept_id) DO UPDATE SET rank=excluded.rank, "
                "product_id=COALESCE(excluded.product_id, concept.product_id)",
                [(str(r["concept_id"]), r.get("rank"), r.get("product_id")) for r in rows],
            )
            self._db.commit()

    def due(self, source, ttl_days, limit=None):
        """Concepts with no fresh copy of `source`, most popular first.

        Popularity is free: the grid's default sort is sales30, so enumeration
        order is already popularity order.
        """
        with self._lock:
            sql = (
                "SELECT c.concept_id FROM concept c "
                "LEFT JOIN fetched f ON f.concept_id = c.concept_id AND f.source = ? "
                "WHERE f.at IS NULL OR f.at < ? "
                "ORDER BY c.rank"
            )
            args = [source, self._now() - ttl_days * DAY]
            if limit:
                sql += " LIMIT ?"
                args.append(limit)
            return [r["concept_id"] for r in self._db.execute(sql, args)]

    def clear_stamps(self, source):
        """Make every concept due for one source again. Returns rows cleared.

        For when a source starts capturing a field it did not before: the
        payloads stay, so `get` still answers during the same run and the other
        sources keep their own TTLs -- playtime is capped at 400 lookups a run
        and takes weeks to warm, so throwing the whole cache away to refresh
        one source costs far more than it fixes.
        """
        with self._lock:
            cleared = self._db.execute(
                "DELETE FROM fetched WHERE source=?", (source,)).rowcount
            self._db.commit()
            return cleared

    def mark(self, source, concept_id):
        with self._lock:
            self._db.execute(
                "INSERT INTO fetched (source, concept_id, at) VALUES (?,?,?) "
                "ON CONFLICT(source, concept_id) DO UPDATE SET at=excluded.at",
                (source, str(concept_id), self._now()),
            )
            self._db.commit()

    def put(self, source, key, body):
        with self._lock:
            self._db.execute(
                "INSERT INTO payload (source, key, body, at) VALUES (?,?,?,?) "
                "ON CONFLICT(source, key) DO UPDATE SET body=excluded.body, at=excluded.at",
                (source, str(key), json.dumps(body, separators=(",", ":")), self._now()),
            )
            self._db.commit()

    def get(self, source, key, ttl_days):
        with self._lock:
            row = self._db.execute(
                "SELECT body, at FROM payload WHERE source=? AND key=?", (source, str(key))
            ).fetchone()
            if not row or row["at"] < self._now() - ttl_days * DAY:
                return None
            return json.loads(row["body"])
