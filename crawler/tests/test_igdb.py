"""IGDB: optional, null-degrading, and unverified against the live API.

No Twitch credential exists on the machine this was written on, so these
tests pin the *logic* -- the runtime-resolved source id, the uid-shape
classifier, and above all the rule that nothing here can fail a run. The
request shapes themselves are documentation-derived and the first live run
is their real test.
"""

import pytest

from ngp.igdb import Igdb


class FakeHttp:
    def __init__(self, responses=None, raises=None):
        self.responses = responses or {}     # url suffix -> payload
        self.raises = raises
        self.calls = []

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def request(self, method, url, headers=None, body=None):
        self.calls.append({"url": url, "headers": headers or {},
                           "body": (body or b"").decode()})
        if self.raises:
            raise self.raises
        for suffix, payload in self.responses.items():
            if url.endswith(suffix):
                return self._Resp(
                    payload(self) if callable(payload) else payload)
        return self._Resp([])


def client(**kw):
    http = FakeHttp(**kw)
    return Igdb(http, client_id="cid", client_secret="secret"), http


TOKEN = {"access_token": "tok", "expires_in": 5_184_000}


class TestOptionality:
    def test_no_credentials_means_no_requests(self, monkeypatch):
        monkeypatch.delenv("IGDB_CLIENT_ID", raising=False)
        monkeypatch.delenv("IGDB_CLIENT_SECRET", raising=False)
        http = FakeHttp()
        igdb = Igdb(http)
        assert igdb.configured is False
        assert igdb.query("games", "fields id;") == []
        assert http.calls == []

    def test_a_dead_api_returns_nothing_rather_than_raising(self):
        igdb, _ = client(raises=RuntimeError("HTTP 503"))
        assert igdb.query("games", "fields id;") == []
        assert igdb.source_id() is None
        assert igdb.editorial([1, 2, 3]) == {}

    def test_a_refused_token_stops_there(self):
        igdb, http = client(responses={"/oauth2/token": {}})
        assert igdb.query("games", "fields id;") == []
        assert len(http.calls) == 1, "no point querying without a token"


class TestAuth:
    def test_uses_the_app_only_client_credentials_grant(self):
        # App credentials, not a user login: IGDB never sees an account.
        igdb, http = client(responses={"/oauth2/token": TOKEN})
        igdb.query("games", "fields id;")
        assert "grant_type=client_credentials" in http.calls[0]["body"]

    def test_sends_both_client_id_and_bearer(self):
        igdb, http = client(responses={"/oauth2/token": TOKEN})
        igdb.query("games", "fields id;")
        headers = http.calls[1]["headers"]
        assert headers["Client-ID"] == "cid"
        assert headers["Authorization"] == "Bearer tok"

    def test_authorises_once_per_run(self):
        # The token lasts ~60 days, so there is nothing to refresh mid-crawl.
        igdb, http = client(responses={"/oauth2/token": TOKEN})
        igdb.query("games", "fields id;")
        igdb.query("games", "fields id;")
        assert sum("oauth2" in c["url"] for c in http.calls) == 1


class TestSourceId:
    def test_resolves_the_source_at_runtime(self):
        # `category = 36` is formally deprecated; pinning it targets a field
        # IGDB is retiring.
        igdb, _ = client(responses={
            "/oauth2/token": TOKEN,
            "/external_game_sources": [{"id": 1, "name": "Steam"},
                                       {"id": 36, "name": "PlayStation Store US"}],
        })
        assert igdb.source_id() == 36

    def test_says_so_when_there_is_no_playstation_source(self):
        igdb, _ = client(responses={"/oauth2/token": TOKEN,
                                    "/external_game_sources": [{"id": 1, "name": "Steam"}]})
        assert igdb.source_id() is None


class TestUidShape:
    """IGDB documents `uid` only as "The other services ID for this game".
    The three candidates are mutually incompatible, so the shape is measured
    rather than assumed."""

    @pytest.mark.parametrize("uid,shape", [
        ("10002456", "concept_id"),
        ("UP9000-PPSA03016_00-MARVELSPIDERMAN2", "product_id"),
        ("PPSA03016_00", "np_title_id"),
    ])
    def test_classifies_each_candidate(self, uid, shape):
        igdb, _ = client(responses={
            "/oauth2/token": TOKEN,
            "/external_games": lambda http: (
                [{"game": 7, "uid": uid}]
                if "offset 0;" in http.calls[-1]["body"] else []),
        })
        assert igdb.psn_index(36)[1] == shape

    def test_an_unrecognised_shape_is_reported_not_guessed(self):
        # The caller falls back to name+year matching, which is a different
        # and much more careful code path.
        igdb, _ = client(responses={
            "/oauth2/token": TOKEN,
            "/external_games": lambda http: (
                [{"game": 7, "uid": "spider-man-2"}]
                if "offset 0;" in http.calls[-1]["body"] else []),
        })
        index, shape = igdb.psn_index(36)
        assert shape is None
        assert index == {"spider-man-2": 7}


class TestEditorial:
    def test_reads_the_fields_sony_does_not_publish(self):
        igdb, _ = client(responses={"/oauth2/token": TOKEN, "/games": [{
            "id": 5,
            "player_perspectives": [{"name": "Third person"}],
            "multiplayer_modes": [{"splitscreen": True, "onlinecoop": False}],
            "game_time_to_beats": {"normally": 61200},
        }]})
        got = igdb.editorial([5])[5]
        assert got.splitscreen is True
        assert got.online_coop is False
        assert got.perspective == "Third person"
        assert got.time_to_beat_seconds == 61200

    def test_any_release_offering_splitscreen_counts(self):
        igdb, _ = client(responses={"/oauth2/token": TOKEN, "/games": [{
            "id": 5, "multiplayer_modes": [{"splitscreen": False},
                                           {"splitscreen": True}],
        }]})
        assert igdb.editorial([5])[5].splitscreen is True

    def test_silence_is_null_not_false(self):
        # "IGDB does not say" and "this game has no split-screen" are
        # different claims, and the site must not print the second for the
        # first.
        igdb, _ = client(responses={"/oauth2/token": TOKEN, "/games": [{"id": 5}]})
        got = igdb.editorial([5])[5]
        assert got.splitscreen is None
        assert got.perspective is None

    def test_batches_within_the_500_row_limit(self):
        igdb, http = client(responses={"/oauth2/token": TOKEN, "/games": []})
        igdb.editorial(range(1200))
        queries = [c for c in http.calls if c["url"].endswith("/games")]
        assert len(queries) == 3
        assert all("limit 500;" in q["body"] for q in queries)
