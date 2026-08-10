"""HowLongToBeat playtime. Best-effort, and never able to fail a run.

The endpoint moves every two or three months, so nothing is pinned. Each run
reads the homepage, fetches every chunk it links and finds the search call **by
its shape**: it is the only fetch sending `x-auth-token`. Never by picking the
most common `/api/…` string -- measured live, the chunks hold `/api/bleed`
three times and `/api/error` twice, and `_buildManifest.js` lists thirty more
routes including `/api/admin/panel`, so frequency would eventually post our
searches at an error-reporting endpoint. Two chunks agreeing is a consistency
check, not the selection rule.

The token is not scraped from the bundle either: `GET <endpoint>/init` returns
`{token, hpKey, hpVal}`, sent as three headers *and* injected into the request
body under the dynamic `hpKey`. A 403 means it expired -- re-init once and
retry, as the site itself does.

`howlongtobeat.com/robots.txt` disallows `/api` for every user-agent and Ziff
Davis's terms prohibit automated retrieval. That was reviewed and accepted:
playtime is a nullable column with a 180-day TTL.

At that TTL a miss is cached like a hit, so "searched, nothing matched" (None)
and "could not search" (`SearchFailed`) must not look alike -- one blip would
otherwise cost a game its playtime for half a year.
"""

from __future__ import annotations

import json
import logging
import re
import time

from dataclasses import dataclass

from .titles import normalize_title, numbers_compatible, similarity, split_year

log = logging.getLogger(__name__)

BASE = "https://howlongtobeat.com"

# A real browser UA. The site is behind Fastly and answers a plain client, but
# there is no reason to look like a script.
BROWSER = {
    "user-agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
    "accept": "*/*",
    "referer": BASE + "/",
    "origin": BASE,
}

_SCRIPT_SRC = re.compile(r'<script[^>]+src="([^"]+)"')
# The search call, identified by the auth header only it sends.
_SEARCH_CALL = re.compile(
    r'fetch\("(/api/[a-z0-9/_-]+)",\s*\{[^}]*?method:\s*"POST".{0,200}?x-auth-token',
    re.S)

MIN_CONFIDENCE = 0.72


class SearchFailed(Exception):
    """The search never ran: no endpoint, no auth, or the POST failed.

    Not an answer, so the caller must not cache it as a miss.
    """


@dataclass(frozen=True)
class Playtime:
    """Seconds, as the API reports them. Any of the three may be zero."""

    main: int
    plus: int
    complete: int
    title: str
    year: int | None
    hltb_id: int

    @property
    def main_hours(self) -> float | None:
        return round(self.main / 3600.0, 1) if self.main else None


class HowLongToBeat:
    def __init__(self, http):
        self._http = http
        self._endpoint = None
        self._auth = None
        self._discovered = False

    # -- endpoint discovery -------------------------------------------------

    def discover(self) -> str | None:
        """Find the current search endpoint. ~15 requests, once per run.

        Doing this per game would cost more than the searches themselves.
        """
        if self._discovered:
            return self._endpoint
        self._discovered = True
        try:
            home = self._http.get(BASE + "/", headers=BROWSER).text
            found = []
            for src in _SCRIPT_SRC.findall(home):
                url = src if src.startswith("http") else BASE + src
                body = self._http.get(url, headers=BROWSER).text
                found += _SEARCH_CALL.findall(body)
        except Exception as exc:
            log.warning("hltb endpoint discovery failed: %s", exc)
            return None

        if not found:
            log.warning("hltb: no search call found in any chunk; the client "
                        "has moved again. Playtime stays null this run.")
            return None
        if len(set(found)) > 1:
            log.warning("hltb: chunks disagree on the search path %s; taking "
                        "the most common", sorted(set(found)))
        self._endpoint = max(set(found), key=found.count)
        log.info("hltb search endpoint: %s", self._endpoint)
        return self._endpoint

    @property
    def available(self) -> bool:
        return self.discover() is not None

    def _init_auth(self) -> dict | None:
        """`{token, hpKey, hpVal}`, reissued on demand."""
        try:
            self._auth = self._http.get_json(
                f"{BASE}{self._endpoint}/init?t={int(time.time() * 1000)}",
                headers=BROWSER)
        except Exception as exc:
            log.warning("hltb auth init failed: %s", exc)
            self._auth = None
        return self._auth

    # -- search -------------------------------------------------------------

    def _post(self, terms: list[str]) -> dict:
        auth = self._auth or self._init_auth()
        for attempt in (1, 2):
            if not auth:
                raise SearchFailed(f"no auth token for {terms!r}")
            key, value = auth.get("hpKey"), auth.get("hpVal")
            payload = _search_payload(terms)
            if key:
                payload[key] = value
            try:
                response = self._http.request(
                    "POST", BASE + self._endpoint,
                    headers={**BROWSER, "content-type": "application/json",
                             "x-auth-token": auth.get("token") or "",
                             "x-hp-key": key or "", "x-hp-val": value or ""},
                    body=json.dumps(payload).encode())
                return response.json() or {}
            except Exception as exc:
                # The site treats 403 as "your token expired": it re-inits and
                # retries once, so we do the same rather than dropping the row.
                if attempt == 1 and "403" in str(exc):
                    auth = self._init_auth()
                    continue
                raise SearchFailed(f"{terms!r}: {exc}") from exc

    def lookup(self, name: str | None, release_year: int | None = None) -> Playtime | None:
        """The match, or None when the search ran and nothing matched.

        Raises `SearchFailed` when it could not run; only None is cacheable.
        """
        query = normalize_title(name or "")
        if not query:
            return None
        if not self.available:
            raise SearchFailed("no search endpoint this run")

        best, best_rank = None, 0.0
        for entry in self._post(query.split()).get("data") or []:
            title, suffix_year = split_year(entry.get("game_name") or "")
            if not numbers_compatible(query, title):
                continue
            confidence = similarity(query, title)
            if confidence < MIN_CONFIDENCE:
                continue
            year = entry.get("release_world") or suffix_year
            rank = confidence
            if release_year and year:
                rank += 0.20 if abs(int(year) - release_year) <= 1 else -0.20
            if rank > best_rank:
                best, best_rank = (entry, title, year), rank

        if not best:
            return None
        entry, title, year = best
        times = (entry.get("comp_main") or 0, entry.get("comp_plus") or 0,
                 entry.get("comp_100") or 0)
        if not any(times):
            return None               # listed but nobody has submitted a time
        return Playtime(*times, title=title,
                        year=int(year) if year else None,
                        hltb_id=entry.get("game_id"))


def _search_payload(terms: list[str]) -> dict:
    """The site's own search body, with every filter left neutral."""
    return {
        "searchType": "games",
        "searchTerms": terms,
        "searchPage": 1,
        "size": 20,
        "searchOptions": {
            "games": {
                "userId": 0, "platform": "", "sortCategory": "popular",
                "rangeCategory": "main",
                "rangeTime": {"min": None, "max": None},
                "gameplay": {"perspective": "", "flow": "", "genre": "",
                             "difficulty": ""},
                "rangeYear": {"min": "", "max": ""}, "modifier": "",
            },
            "users": {"sortCategory": "postcount"},
            "lists": {"sortCategory": "follows"},
            "filter": "", "sort": 0, "randomizer": 0,
        },
        "useCache": True,
    }
