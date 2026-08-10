"""HowLongToBeat: endpoint discovery and matching.

The endpoint moves every two or three months, so the discovery rules are the
part worth pinning. Every fixture below is the shape of the real thing as
measured live: `/api/bleed` in three chunks, `/api/error` in two, and a
`_buildManifest.js` that lists thirty routes including `/api/admin/panel`.
"""

import json

import pytest

from ngp.hltb import HowLongToBeat, SearchFailed


SEARCH_CALL = (
    'let i=await fetch("{path}",{{method:"POST",headers:{{'
    '"Content-Type":"application/json","x-auth-token":t,"x-hp-key":a,"x-hp-val":l}},'
    'body:JSON.stringify(s)}});'
)
ERROR_CALL = 'fetch("/api/error",{method:"POST",headers:{"Content-Type":"application/json"}})'
MANIFEST = '"/api/bleed","/api/error","/api/admin/panel","/api/login","/api/submit"'


def home(*srcs):
    return "".join(f'<script src="{s}"></script>' for s in srcs)


class FakeHttp:
    """Serves the homepage, the chunks, the auth init and the search POST."""

    AUTH = {"token": "T", "hpKey": "hpk", "hpVal": "hpv"}

    def __init__(self, pages, auth=AUTH, results=None, post_errors=()):
        self.pages = pages                 # url suffix -> body text
        self.auth = auth                   # None makes /init fail
        self.results = results if results is not None else {"data": []}
        self.post_errors = list(post_errors)   # raised by successive POSTs
        self.calls = []
        self.posts = []

    class _Resp:
        def __init__(self, text, payload):
            self.text = text
            self._payload = payload

        def json(self):
            return self._payload

    def get(self, url, headers=None):
        self.calls.append(url)
        for suffix, body in self.pages.items():
            if url.endswith(suffix):
                return self._Resp(body, None)
        raise RuntimeError(f"404 {url}")

    def get_json(self, url, headers=None):
        self.calls.append(url)
        if "/init" in url:
            if self.auth is None:
                raise RuntimeError("HTTP 500")
            return self.auth
        raise RuntimeError(f"404 {url}")

    def request(self, method, url, headers=None, body=None):
        self.posts.append({"url": url, "headers": headers,
                           "body": json.loads(body)})
        if self.post_errors:
            raise RuntimeError(self.post_errors.pop(0))
        return self._Resp("", self.results)


def game(name="Gang Beasts", main=7755, plus=18943, hundred=27056,
         year=2014, gid=23050):
    return {"game_id": gid, "game_name": name, "release_world": year,
            "comp_main": main, "comp_plus": plus, "comp_100": hundred}


def client(**kw):
    http = FakeHttp(**kw)
    return HowLongToBeat(http), http


DEFAULT_PAGES = {
    "/": home("/c/a.js", "/c/b.js", "/c/err.js", "/c/_buildManifest.js"),
    "/c/a.js": SEARCH_CALL.format(path="/api/bleed"),
    "/c/b.js": SEARCH_CALL.format(path="/api/bleed"),
    "/c/err.js": ERROR_CALL,
    "/c/_buildManifest.js": MANIFEST,
}


class TestDiscovery:
    def test_finds_the_search_endpoint(self):
        hltb, _ = client(pages=DEFAULT_PAGES)
        assert hltb.discover() == "/api/bleed"

    def test_never_selects_the_error_reporting_endpoint(self):
        # It is a POST to /api/… in the same bundle. Only the auth header
        # tells them apart, and posting searches there would be silent.
        hltb, _ = client(pages=DEFAULT_PAGES)
        assert hltb.discover() != "/api/error"

    def test_a_route_manifest_cannot_outvote_the_real_call(self):
        # _buildManifest.js lists every route on the site, so counting bare
        # "/api/…" strings is not a selection rule.
        pages = dict(DEFAULT_PAGES)
        pages["/c/_buildManifest.js"] = MANIFEST * 40
        hltb, _ = client(pages=pages)
        assert hltb.discover() == "/api/bleed"

    def test_follows_the_path_wherever_it_moves(self):
        # Nothing is pinned: the endpoint has changed repeatedly upstream.
        pages = dict(DEFAULT_PAGES)
        pages["/c/a.js"] = SEARCH_CALL.format(path="/api/ooze")
        pages["/c/b.js"] = SEARCH_CALL.format(path="/api/ooze")
        hltb, _ = client(pages=pages)
        assert hltb.discover() == "/api/ooze"

    def test_discovery_runs_once_per_client(self):
        # 15 requests. Per game it would cost more than the searches do.
        hltb, http = client(pages=DEFAULT_PAGES)
        hltb.discover()
        before = len(http.calls)
        hltb.discover()
        assert len(http.calls) == before

    def test_a_bundle_with_no_search_call_leaves_playtime_null(self):
        hltb, _ = client(pages={"/": home("/c/err.js"), "/c/err.js": ERROR_CALL})
        assert hltb.discover() is None
        assert hltb.available is False
        with pytest.raises(SearchFailed):
            hltb.lookup("Gang Beasts")     # no endpoint is not "no entry"

    def test_an_unreachable_homepage_is_not_an_error(self):
        hltb, _ = client(pages={})
        assert hltb.discover() is None


class TestSearch:
    def test_returns_playtime_for_a_match(self):
        hltb, _ = client(pages=DEFAULT_PAGES, results={"data": [game()]})
        got = hltb.lookup("Gang Beasts")
        assert got.main == 7755
        assert got.main_hours == 2.2

    def test_sends_the_token_triple_in_headers_and_body(self):
        # The site puts hpVal in both places under the dynamic hpKey; sending
        # only the headers gets a 403.
        hltb, http = client(pages=DEFAULT_PAGES, results={"data": [game()]})
        hltb.lookup("Gang Beasts")
        post = http.posts[0]
        assert post["headers"]["x-auth-token"] == "T"
        assert post["body"]["hpk"] == "hpv"

    def test_rejects_a_sequel_whose_numbering_disagrees(self):
        hltb, _ = client(pages=DEFAULT_PAGES,
                         results={"data": [game(name="Mortal Kombat 11")]})
        assert hltb.lookup("Mortal Kombat 1") is None

    def test_prefers_the_entry_matching_the_release_year(self):
        hltb, _ = client(pages=DEFAULT_PAGES, results={"data": [
            game(name="Silent Hill 2 (2001)", year=2001, main=100, gid=1),
            game(name="Silent Hill 2", year=2024, main=200, gid=2),
        ]})
        assert hltb.lookup("Silent Hill 2", release_year=2024).hltb_id == 2
        assert hltb.lookup("Silent Hill 2", release_year=2001).hltb_id == 1

    def test_an_unrelated_result_is_not_a_match(self):
        hltb, _ = client(pages=DEFAULT_PAGES,
                         results={"data": [game(name="Bean Beasts")]})
        assert hltb.lookup("Gang Beasts") is None

    def test_a_listed_game_with_no_submitted_times_is_a_miss(self):
        hltb, _ = client(pages=DEFAULT_PAGES,
                         results={"data": [game(main=0, plus=0, hundred=0)]})
        assert hltb.lookup("Gang Beasts") is None

    def test_a_failed_auth_init_is_not_a_cacheable_miss(self):
        hltb, _ = client(pages=DEFAULT_PAGES, auth=None)
        with pytest.raises(SearchFailed):
            hltb.lookup("Gang Beasts")

    def test_a_failed_search_is_not_a_cacheable_miss(self):
        # None means "HowLongToBeat has no entry", and that is cached for 180
        # days. A blip must not buy the same silence.
        hltb, _ = client(pages=DEFAULT_PAGES, post_errors=["HTTP 502"],
                         results={"data": [game()]})
        with pytest.raises(SearchFailed):
            hltb.lookup("Gang Beasts")

    def test_an_expired_token_is_retried_once_and_still_matches(self):
        hltb, http = client(pages=DEFAULT_PAGES, post_errors=["HTTP 403"],
                            results={"data": [game()]})
        assert hltb.lookup("Gang Beasts").hltb_id == 23050
        assert len([c for c in http.calls if "/init" in c]) == 2

    def test_a_second_403_gives_up_without_faking_a_miss(self):
        hltb, _ = client(pages=DEFAULT_PAGES,
                         post_errors=["HTTP 403", "HTTP 403"])
        with pytest.raises(SearchFailed):
            hltb.lookup("Gang Beasts")

    def test_strips_edition_noise_before_searching(self):
        hltb, http = client(pages=DEFAULT_PAGES, results={"data": [game()]})
        hltb.lookup("Gang Beasts - Deluxe Edition PS4 & PS5")
        assert http.posts[0]["body"]["searchTerms"] == ["gang", "beasts"]
