"""IGDB: split-screen and player perspective, the one signal with no keyless
substitute. Optional and null-degrading: without `IGDB_CLIENT_ID` /
`IGDB_CLIENT_SECRET` the columns stay null, and nothing here can fail a run.
The `client_credentials` grant is app-only, so IGDB never sees an account.

Two things are resolved at runtime because the documentation does not answer
them:

* `external_game.category = 36` is formally deprecated in favour of
  `external_game_source`, so the live source id is read from
  `/v4/external_game_sources` rather than pinned.
* `uid` is documented only as "The other services ID for this game", and the
  three candidates are mutually incompatible: a concept id (`10002456`), a
  product id (`UP9000-PPSA03016_00-MARVELSPIDERMAN2`) or an npTitleId
  (`PPSA03016_00`). A sample is classified by shape and the join key follows.

**Never exercised against the live API** -- no Twitch credential existed on the
machine this was written on, so the request shapes are documentation-derived
and the first live run is the real test.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from urllib.parse import urlencode

log = logging.getLogger(__name__)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API = "https://api.igdb.com/v4"

PAGE = 500                      # IGDB's hard per-query limit

# The three things `uid` could be, told apart by shape. Order matters: an
# npTitleId is a strict prefix of a product id.
UID_SHAPES = (
    ("product_id", re.compile(r"^[A-Z]{2}\d{4}-[A-Z]{4}\d{5}_\d{2}-\w+$")),
    ("np_title_id", re.compile(r"^[A-Z]{4}\d{5}_\d{2}$")),
    ("concept_id", re.compile(r"^\d{4,12}$")),
)


@dataclass(frozen=True)
class Editorial:
    """The bits IGDB has that Sony does not."""

    splitscreen: bool | None = None
    online_coop: bool | None = None
    perspective: str | None = None
    time_to_beat_seconds: int | None = None


class Igdb:
    def __init__(self, http, client_id=None, client_secret=None):
        self._http = http
        self._id = client_id or os.environ.get("IGDB_CLIENT_ID", "")
        self._secret = client_secret or os.environ.get("IGDB_CLIENT_SECRET", "")
        self._token = None

    @property
    def configured(self) -> bool:
        return bool(self._id and self._secret)

    def _authorise(self) -> bool:
        """One token per run: `expires_in` is ~60 days, so it never expires
        mid-crawl and there is nothing to refresh."""
        if self._token:
            return True
        if not self.configured:
            return False
        body = urlencode({"client_id": self._id, "client_secret": self._secret,
                          "grant_type": "client_credentials"}).encode()
        try:
            payload = self._http.request(
                "POST", TOKEN_URL,
                headers={"content-type": "application/x-www-form-urlencoded"},
                body=body).json()
        except Exception as exc:
            log.warning("igdb: could not get a token: %s", exc)
            return False
        self._token = payload.get("access_token")
        return bool(self._token)

    def query(self, endpoint: str, apicalypse: str):
        """One apicalypse query. Returns [] on any failure -- optional means
        optional. Use `_query` where an empty answer and a failed one mean
        different things."""
        try:
            return self._query(endpoint, apicalypse)
        except Exception as exc:
            log.warning("igdb %s failed: %s", endpoint, exc)
            return []

    def _query(self, endpoint: str, apicalypse: str):
        """As `query`, but raises rather than flattening a failure to []."""
        if not self._authorise():
            raise RuntimeError("no token")
        return self._http.request(
            "POST", f"{API}/{endpoint}",
            headers={"Client-ID": self._id,
                     "Authorization": f"Bearer {self._token}",
                     "accept": "application/json"},
            body=apicalypse.encode()).json() or []

    # -- the join key -------------------------------------------------------

    def source_id(self) -> int | None:
        """The live id for the US PlayStation Store external-game source."""
        for source in self.query("external_game_sources", "fields id,name; limit 100;"):
            if "playstation" in (source.get("name") or "").lower():
                return source.get("id")
        log.warning("igdb: no PlayStation Store source in external_game_sources")
        return None

    def psn_index(self, source: int) -> tuple[dict[str, int], str | None]:
        """`{uid: igdb_game_id}` for every PlayStation Store link, plus the
        shape those uids turned out to be.

        The shape is what tells the caller which of our three identifiers to
        join on. If the uids are none of the three, it returns None and the
        caller falls back to name matching.

        A failure part way through the ~26 pages throws the whole index away
        rather than returning a truncated one: the caller cannot tell a short
        index from a small catalogue, and would cache the missing half as a
        confident "no split-screen" for 90 days.
        """
        index, offset = {}, 0
        while True:
            try:
                rows = self._query("external_games", (
                    f"fields game,uid; where external_game_source = {source}; "
                    f"limit {PAGE}; offset {offset};"))
            except Exception as exc:
                log.warning("igdb: external_games failed at offset %d, "
                            "dropping a partial index of %d links: %s",
                            offset, len(index), exc)
                return {}, None
            if not rows:
                break
            for row in rows:
                uid, game = row.get("uid"), row.get("game")
                if uid and game:
                    index[str(uid)] = game
            offset += len(rows)
            if len(rows) < PAGE:
                break

        shape = _classify(index)
        log.info("igdb: %d PlayStation Store links, uid shape %s",
                 len(index), shape or "unrecognised")
        return index, shape

    # -- the payload --------------------------------------------------------

    def editorial(self, game_ids) -> dict[int, Editorial]:
        """Game modes, split-screen, perspective and time-to-beat, in pages of
        500. ~26 requests for a 12.8k catalogue."""
        ids = sorted(set(game_ids))
        out: dict[int, Editorial] = {}
        for start in range(0, len(ids), PAGE):
            batch = ids[start:start + PAGE]
            rows = self.query("games", (
                "fields id,player_perspectives.name,multiplayer_modes.splitscreen,"
                "multiplayer_modes.onlinecoop,game_time_to_beats.normally; "
                f"where id = ({','.join(str(i) for i in batch)}); limit {PAGE};"))
            for row in rows:
                out[row["id"]] = _editorial(row)
        return out


def _classify(index) -> str | None:
    """Which of our identifiers these uids are, decided by majority shape."""
    sample = list(index)[:200]
    if not sample:
        return None
    for name, pattern in UID_SHAPES:
        if sum(1 for uid in sample if pattern.match(uid)) > len(sample) / 2:
            return name
    return None


def _editorial(row) -> Editorial:
    modes = row.get("multiplayer_modes") or []
    perspectives = row.get("player_perspectives") or []
    ttb = row.get("game_time_to_beats") or {}
    if isinstance(ttb, list):
        ttb = ttb[0] if ttb else {}
    return Editorial(
        # Any PlayStation release offering it is enough; IGDB reports modes
        # per platform and we do not care which one.
        splitscreen=_any(modes, "splitscreen"),
        online_coop=_any(modes, "onlinecoop"),
        perspective=(perspectives[0].get("name") if perspectives else None),
        time_to_beat_seconds=ttb.get("normally"),
    )


def _any(modes, field) -> bool | None:
    """True if any release offers it, None if IGDB says nothing at all."""
    values = [m.get(field) for m in modes if m.get(field) is not None]
    return bool(any(values)) if values else None
