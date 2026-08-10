"""Append-only price history.

This is the only thing in the project that accumulates -- everything else is
re-fetched every run -- so a bug here loses data permanently instead of
costing a re-crawl. Competitors have 5-12 years of history and we have none,
which is exactly why the honesty rules below are tested rather than trusted.
"""

from datetime import date

import pytest

from ngp.history import append_changes, load, month_path, record, summarise


def day(n, month=8, year=2026):
    return date(year, month, n)


class TestPartitioning:
    def test_partitions_by_month(self):
        # A single append-only file re-stores the whole blob on every commit:
        # ~1.8 GB of git objects in year one. Directories also cap at 3,000
        # entries, so a file per day would break in year nine.
        assert month_path("h", day(9)).as_posix() == "h/prices/2026/08.csv"

    def test_pads_the_month(self):
        assert month_path("h", day(3, month=1)).as_posix() == "h/prices/2026/01.csv"


class TestAppend:
    def test_writes_the_first_observation(self, tmp_path):
        written = append_changes(tmp_path, [("A", 1999, 5999)], day(1))
        assert written == 1
        assert load(tmp_path)["A"] == [(day(1), 1999, 5999)]

    def test_writes_nothing_when_the_price_is_unchanged(self, tmp_path):
        append_changes(tmp_path, [("A", 1999, 5999)], day(1))
        assert append_changes(tmp_path, [("A", 1999, 5999)], day(2)) == 0
        assert len(load(tmp_path)["A"]) == 1

    def test_writes_a_row_when_the_price_moves(self, tmp_path):
        append_changes(tmp_path, [("A", 1999, 5999)], day(1))
        append_changes(tmp_path, [("A", 999, 5999)], day(2))
        assert [o[1] for o in load(tmp_path)["A"]] == [1999, 999]

    def test_writes_a_row_when_only_the_list_price_moves(self, tmp_path):
        # A permanent price cut changes the base while the sale price holds.
        append_changes(tmp_path, [("A", 1999, 5999)], day(1))
        append_changes(tmp_path, [("A", 1999, 3999)], day(2))
        assert len(load(tmp_path)["A"]) == 2

    def test_appends_rather_than_rewriting_the_month(self, tmp_path):
        append_changes(tmp_path, [("A", 1999, 5999)], day(1))
        append_changes(tmp_path, [("B", 999, 1999)], day(2))
        assert sorted(load(tmp_path)) == ["A", "B"]

    def test_reads_history_across_month_boundaries(self, tmp_path):
        append_changes(tmp_path, [("A", 1999, 5999)], date(2026, 7, 30))
        append_changes(tmp_path, [("A", 999, 5999)], date(2026, 8, 2))
        assert [o[0] for o in load(tmp_path)["A"]] == [date(2026, 7, 30), date(2026, 8, 2)]

    def test_reads_history_across_year_boundaries(self, tmp_path):
        append_changes(tmp_path, [("A", 1999, 5999)], date(2026, 12, 30))
        append_changes(tmp_path, [("A", 999, 5999)], date(2027, 1, 2))
        assert [o[1] for o in load(tmp_path)["A"]] == [1999, 999]

    def test_an_absent_history_directory_is_an_empty_history(self, tmp_path):
        # A fresh clone, or a first run before the data branch exists.
        assert load(tmp_path / "nothing-here") == {}

    def test_skips_rows_with_no_usable_price(self, tmp_path):
        # "Unavailable" products parse to None and must not become a zero.
        assert append_changes(tmp_path, [("A", None, 5999)], day(1)) == 0
        assert load(tmp_path) == {}


class TestRecord:
    """One call does the write and hands back what to score against, so the
    stored file and the scored series cannot disagree about what changed."""

    def test_todays_price_counts_toward_its_own_history(self, tmp_path):
        # "The lowest price we have ever recorded" must include the price on
        # offer right now, or a genuine all-time low scores as ordinary.
        append_changes(tmp_path, [("A", 1999, 5999)], day(1))
        series, written = record(tmp_path, [("A", 999, 5999)], day(2))
        assert written == 1
        assert series["A"] == [(day(1), 1999, 5999), (day(2), 999, 5999)]

    def test_an_unchanged_price_writes_nothing_and_adds_nothing(self, tmp_path):
        append_changes(tmp_path, [("A", 1999, 5999)], day(1))
        series, written = record(tmp_path, [("A", 1999, 5999)], day(2))
        assert written == 0
        assert series["A"] == [(day(1), 1999, 5999)]

    def test_what_it_returns_is_what_it_stored(self, tmp_path):
        record(tmp_path, [("A", 1999, 5999), ("B", 999, 999)], day(1))
        series, _ = record(tmp_path, [("A", 1499, 5999)], day(2))
        assert series == load(tmp_path)


class TestSummarise:
    def test_says_nothing_below_the_observation_floor(self, tmp_path):
        # Three points is not a price history. Competitors have a decade; the
        # honest answer here is "not yet", not a confident number.
        series = [(day(n), 1999, 5999) for n in range(1, 4)]
        assert summarise(series, min_observations=4) is None

    def test_reports_the_lowest_price_seen(self):
        series = [(day(1), 1999, 5999), (day(2), 999, 5999),
                  (day(3), 1499, 5999), (day(4), 2999, 5999)]
        assert summarise(series, min_observations=4).min_cents == 999

    def test_typical_sale_is_the_median_of_actual_discounts(self):
        # The full-price observations are not sales and must not drag the
        # median up toward the list price.
        series = [(day(1), 5999, 5999), (day(2), 2999, 5999),
                  (day(3), 1999, 5999), (day(4), 999, 5999)]
        assert summarise(series, min_observations=4).typical_sale_cents == 1999

    def test_typical_sale_is_none_when_it_has_never_been_discounted(self):
        series = [(day(n), 5999, 5999) for n in range(1, 5)]
        assert summarise(series, min_observations=4).typical_sale_cents is None

    def test_counts_the_observations_it_used(self):
        series = [(day(n), 1999, 5999) for n in range(1, 6)]
        assert summarise(series, min_observations=4).count == 5


@pytest.mark.parametrize("floor", [1, 4, 10])
def test_the_observation_floor_is_the_caller_s_to_set(floor):
    series = [(day(n), 1999, 5999) for n in range(1, 5)]
    assert (summarise(series, min_observations=floor) is None) == (floor > 4)
