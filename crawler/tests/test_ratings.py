"""Metacritic critic scores.

Matching is the hard part, not fetching. Every case here comes from a live
search: the "Silent Hill 2" query really does return the 2001 PS2 original
above the 2024 remake, and "EA Sports FC 26" really does sit next to FC 25
and FC 27 in the same response.
"""

import pytest

from ngp.ratings import Metacritic


class FakeHttp:
    """Stands in for HttpClient, recording requests and replaying payloads."""

    def __init__(self, *payloads, raises=None):
        self.queue = list(payloads)
        self.calls = []
        self.raises = raises

    def get_json(self, url, headers=None):
        self.calls.append(url)
        if self.raises:
            raise self.raises
        return self.queue.pop(0) if self.queue else {"data": {"items": []}}


def item(title, year=None, score=80, platforms=("PlayStation 5",), slug=None):
    return {
        "typeId": 13,
        "title": title,
        "slug": slug or title.lower().replace(" ", "-"),
        "premiereYear": year,
        "criticScoreSummary": {"score": score},
        "platforms": [{"name": p} for p in platforms],
    }


def results(*items):
    return {"data": {"items": list(items)}}


class TestLookup:
    def test_reads_the_score_inline_from_the_search_response(self):
        http = FakeHttp(results(item("Gang Beasts", 2017, 68, ("PlayStation 4",))))
        got = Metacritic(http).lookup("Gang Beasts")
        assert got.score == 68
        assert got.title == "Gang Beasts"

    def test_costs_exactly_one_request(self):
        # The per-game page would add the user score and the review count at
        # double the request budget. Not worth it for one component.
        http = FakeHttp(results(item("Gang Beasts", 2017, 68)))
        Metacritic(http).lookup("Gang Beasts")
        assert len(http.calls) == 1

    def test_strips_edition_noise_before_searching(self):
        http = FakeHttp(results(item("Gang Beasts", 2017, 68)))
        Metacritic(http).lookup("Gang Beasts - Deluxe Edition PS4 & PS5")
        assert "gang%20beasts" in http.calls[0]

    def test_an_unreleased_entry_has_no_score(self):
        http = FakeHttp(results(item("EA Sports FC 27", 2026, None)))
        assert Metacritic(http).lookup("EA Sports FC 27") is None

    def test_a_zero_score_is_not_a_score(self):
        # Metacritic returns 0 for entries with no reviews at all.
        http = FakeHttp(results(item("EA SPORTS FC Mobile", 2023, 0)))
        assert Metacritic(http).lookup("EA SPORTS FC Mobile") is None

    def test_an_unrelated_top_hit_is_rejected(self):
        http = FakeHttp(results(item("Bean Beasts", 2026, 84)))
        assert Metacritic(http).lookup("Gang Beasts") is None

    def test_a_failed_request_is_a_miss_not_a_crash(self):
        # Metacritic is a single-source dependency: it degrades to null, it
        # never fails the run.
        http = FakeHttp(raises=RuntimeError("HTTP 503"))
        assert Metacritic(http).lookup("Gang Beasts") is None


class TestDisambiguation:
    def test_rejects_a_sequel_whose_numbering_disagrees(self):
        # The mandatory guard. "Mortal Kombat 11" beats any fuzzy threshold
        # against "Mortal Kombat 1" while being a different game.
        http = FakeHttp(results(item("Mortal Kombat 11", 2019, 83)))
        assert Metacritic(http).lookup("Mortal Kombat 1") is None

    def test_a_year_suffix_is_a_disambiguator_not_part_of_the_title(self):
        # Live: the 2001 original is listed as "Silent Hill 2 (2001)". Left in
        # the title, its 2001 reads as a version number and the numeric guard
        # throws away *both* candidates.
        http = FakeHttp(results(
            item("Silent Hill 2 (2001)", 2001, 89, ("PlayStation 2",)),
            item("Silent Hill 2", 2024, 86, ("PlayStation 5", "PC")),
        ))
        got = Metacritic(http).lookup("Silent Hill 2", release_year=2024)
        assert got.score == 86

    def test_prefers_the_playstation_entry_when_the_year_is_unknown(self):
        # Store release dates are missing on some rows, so platform has to
        # carry the tie on its own.
        http = FakeHttp(results(
            item("Silent Hill 2 (2001)", 2001, 89, ("PlayStation 2",)),
            item("Silent Hill 2", 2024, 86, ("PlayStation 5", "PC")),
        ))
        assert Metacritic(http).lookup("Silent Hill 2").score == 86

    def test_the_release_year_outranks_the_platform_hint(self):
        http = FakeHttp(results(
            item("Silent Hill 2 (2001)", 2001, 89, ("PlayStation 2",)),
            item("Silent Hill 2", 2024, 86, ("PlayStation 5", "PC")),
        ))
        assert Metacritic(http).lookup("Silent Hill 2", release_year=2001).score == 89

    def test_ignores_non_game_result_types(self):
        film = dict(item("Gang Beasts", 2017, 99), typeId=3)
        http = FakeHttp(results(film, item("Gang Beasts", 2017, 68)))
        assert Metacritic(http).lookup("Gang Beasts").score == 68


@pytest.mark.parametrize("name", ["", None])
def test_an_empty_name_never_reaches_the_network(name):
    http = FakeHttp()
    assert Metacritic(http).lookup(name) is None
    assert http.calls == []
