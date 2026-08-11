"""The published index.

Measured: row-of-objects at 12k games is 1.67 MB gzipped and blows the 800 KB
budget; columnar with integer dictionaries and no cover art is 517 KB. The
layout is not a style choice.
"""

import gzip
import json

import pytest

from ngp.components import Weights
from ngp.publish import GZIP_BUDGET_BYTES, build_index, render, save, save_art


def row(i=0, **over):
    base = dict(
        id=f"UP9000-PPSA{i:05d}_00-ABCDEFGHIJKLMNOP",
        name=f"Test Game {i}",
        platforms=["PS5"],
        price_cents=1999,
        base_cents=5999,
        discount_pct=67,
        is_free=False,
        plus_extra=False,
        plus_classics=False,
        genres=["Action", "Adventure"],
        esrb="ESRB_TEEN",
        local_players=2,
        psvr2=None,
        dualsense=True,
        release_year=2023,
        quality=78.5,
        discount_depth=72.1,
        price_anchor=61.0,
        evidence="high",
    )
    base.update(over)
    return base


@pytest.fixture
def weights():
    return Weights.defaults()


class TestLayout:
    def test_is_columnar_not_row_objects(self, weights):
        idx = build_index([row(0), row(1)], weights)
        assert isinstance(idx["cols"]["name"], list)
        assert idx["cols"]["name"] == ["Test Game 0", "Test Game 1"]

    def test_every_column_has_one_entry_per_game(self, weights):
        idx = build_index([row(i) for i in range(5)], weights)
        assert all(len(col) == 5 for col in idx["cols"].values())

    def test_cover_art_is_not_published(self, weights):
        # Measured at 27.1% of the naive payload -- the single largest line
        # item. Art is fetched for the visible viewport instead.
        idx = build_index([row(0, cover="https://image.api.playstation.com/x.png")], weights)
        assert not any("cover" in k or "art" in k for k in idx["cols"])

    def test_repeated_strings_become_dictionary_indices(self, weights):
        idx = build_index([row(i, genres=["Action"]) for i in range(3)], weights)
        assert "Action" in idx["dicts"]["genres"]
        assert all(isinstance(v, list) for v in idx["cols"]["genres"])
        assert idx["cols"]["genres"][0] == [idx["dicts"]["genres"].index("Action")]

    def test_esrb_is_dictionary_encoded(self, weights):
        idx = build_index([row(0), row(1, esrb="ESRB_MATURE")], weights)
        assert set(idx["dicts"]["esrb"]) == {"ESRB_TEEN", "ESRB_MATURE"}

    def test_tier_is_not_published(self, weights):
        # Nothing reads it: the browser buckets by base price itself.
        idx = build_index([row(0, tier="premium")], weights)
        assert "tier" not in idx["cols"] and "tier" not in idx["dicts"]


class TestAntiDrift:
    """Weights live in one file, copied into the payload. The browser reads
    them from here, never from its own constant, so the two cannot disagree."""

    def test_weights_are_embedded(self, weights):
        idx = build_index([row(0)], weights)
        assert idx["meta"]["weights"]["final"]["quality"] == 0.45

    def test_embedded_weights_match_the_source_file(self, weights):
        idx = build_index([row(0)], weights)
        assert idx["meta"]["weights"] == weights.as_dict()


class TestScoreComponents:
    """The crawler publishes components; the browser computes the score. That
    is what makes the PS+ toggle and the sliders real."""

    def test_components_are_published(self, weights):
        idx = build_index([row(0)], weights)
        for field in ("quality", "discount_depth", "price_anchor"):
            assert field in idx["cols"]

    def test_no_precomputed_final_score(self, weights):
        idx = build_index([row(0)], weights)
        assert "final" not in idx["cols"] and "score" not in idx["cols"]


class TestMetadata:
    def test_counts_games(self, weights):
        idx = build_index([row(i) for i in range(7)], weights)
        assert idx["meta"]["count"] == 7

    def test_carries_a_generated_timestamp(self, weights):
        idx = build_index([row(0)], weights, generated_at="2026-08-10T00:00:00Z")
        assert idx["meta"]["generated_at"] == "2026-08-10T00:00:00Z"


class TestBudget:
    """render() reports the real sizes so the caller's guard can refuse before
    save() puts anything on disk."""

    def test_twelve_thousand_games_fit_the_gzip_budget(self, weights):
        games = [row(i, name=f"Some Game With A Realistic Length Title {i}")
                 for i in range(12_000)]
        _, _, stats = render(games, weights)
        assert stats["gzip_bytes"] < GZIP_BUDGET_BYTES, (
            f"{stats['gzip_bytes']} exceeds the {GZIP_BUDGET_BYTES} budget"
        )

    def test_render_reports_both_sizes(self, weights):
        _, _, stats = render([row(i) for i in range(50)], weights)
        assert stats["raw_bytes"] > stats["gzip_bytes"] > 0

    def test_output_is_valid_json_and_reloadable(self, weights, tmp_path):
        out = tmp_path / "index.json"
        body, packed, _ = render([row(0), row(1)], weights)
        save(out, body, packed)
        loaded = json.loads(out.read_text())
        assert loaded["meta"]["count"] == 2

    def test_gzip_sidecar_is_written_for_precompression(self, weights, tmp_path):
        # Pages serves gzip but not brotli, and precompressing is free here.
        out = tmp_path / "index.json"
        save(out, *render([row(0)], weights)[:2])
        gz = out.with_suffix(".json.gz")
        assert gz.exists()
        assert json.loads(gzip.decompress(gz.read_bytes()))["meta"]["count"] == 1


class TestCoverArt:
    """Art is fetched and stored but deliberately kept out of index.json.

    Measured on the live payload: a URL adds 34.6 B gzipped per row that gzip
    cannot shrink -- the 48-hex asset hash is random -- so at the 12,736-game
    catalogue it is 428 KB and pushes the index past the budget. It goes to a
    build-only file instead, and the static pages bake in the <img>.
    """

    ART = "https://image.api.playstation.com/vulcan/ap/rnd/202306/1219/abc.png"

    def test_art_is_not_a_published_column(self, weights):
        index = build_index([row(0, art=self.ART)], weights)
        assert "art" not in index["cols"]
        assert "art" not in index["dicts"]

    def test_art_url_does_not_reach_the_payload_at_all(self, weights):
        body, _, _ = render([row(0, art=self.ART)], weights)
        assert b"image.api.playstation.com" not in body

    def test_save_art_keys_urls_by_product_id(self, tmp_path):
        out = tmp_path / "art.json"
        games = [row(0, art=self.ART), row(1, art=self.ART)]
        rows, _ = save_art(out, games)
        assert rows == 2
        assert json.loads(out.read_text())[games[0]["id"]] == self.ART

    def test_games_without_art_are_absent_rather_than_null(self, tmp_path):
        # A missing key is what lets the page render no image at all; a null
        # would still have to be checked and costs bytes in the build input.
        out = tmp_path / "art.json"
        rows, _ = save_art(out, [row(0, art=None), row(1, art=self.ART)])
        assert rows == 1
        assert list(json.loads(out.read_text())) == [row(1)["id"]]

    def test_limit_takes_the_popularity_head(self, tmp_path):
        # Rows arrive in popularity order, so a limit is a "most-browsed"
        # slice. The served manifest exists because the full one is 428 KB.
        out = tmp_path / "art-head.json"
        rows, _ = save_art(out, [row(i, art=self.ART) for i in range(5)], limit=2)
        assert rows == 2
        assert list(json.loads(out.read_text())) == [row(0)["id"], row(1)["id"]]

    def test_save_art_creates_its_directory(self, tmp_path):
        out = tmp_path / "nested" / "art.json"
        save_art(out, [row(0, art=self.ART)])
        assert out.exists()
