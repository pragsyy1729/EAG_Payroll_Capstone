"""Implementation scaffolding for the attendance evidence rules.

NOT the team's graded tests. Pure functions only.
"""
from __future__ import annotations

import pytest

from payroll_agent import evidence


def test_a_month_window_lists_every_calendar_day():
    days = evidence.window_days(month="2026-02")
    assert days[0] == "2026-02-01" and days[-1] == "2026-02-28" and len(days) == 28
    assert len(evidence.window_days(month="2028-02")) == 29                   # leap year
    assert len(evidence.window_days(month="2026-08")) == 31


def test_a_run_window_uses_its_own_dates():
    assert evidence.window_days(start="2026-09-15", end="2026-09-18T00:00:00") == [
        "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]


@pytest.mark.parametrize("kwargs", [{"month": "2026-13"}, {"month": "soon"}, {"month": "0000-01"},
                                    {"start": None, "end": "2026-09-30"}, {"start": "2026-09-30", "end": "2026-09-01"},
                                    {"start": "2026-08-01", "end": "2026-09-30"}, {}])
def test_an_unusable_window_is_none(kwargs):
    assert evidence.window_days(**kwargs) is None


def counts(days, with_rows):
    return {d: (4 if d in with_rows else 0) for d in days}


DAYS = evidence.window_days(month="2026-09")


def test_no_rows_at_all_is_none():
    result = evidence.assess(counts(DAYS, set()))
    assert result["verdict"] == "none" and result["last_date_with_rows"] is None and result["window"] == [DAYS[0], DAYS[-1]]


def test_rows_to_the_end_are_full_and_a_weekend_tail_is_still_full():
    assert evidence.assess(counts(DAYS, set(DAYS)))["verdict"] == "full"
    through_the_27th = {d for d in DAYS if d <= "2026-09-27"}                  # a 3-day tail to the 30th
    assert evidence.assess(counts(DAYS, through_the_27th))["verdict"] == "full"


def test_a_longer_tail_is_partial_and_names_the_last_date():
    result = evidence.assess(counts(DAYS, {d for d in DAYS if d <= "2026-09-12"}))
    assert result["verdict"] == "partial" and result["last_date_with_rows"] == "2026-09-12"
    assert result["days_with_rows"] == 12 and result["period_days"] == 30


def test_a_day_that_could_not_be_counted_makes_the_verdict_unverified():
    mixed = counts(DAYS, set(DAYS))
    mixed["2026-09-10"] = None
    result = evidence.assess(mixed)
    assert result["verdict"] == "unverified" and result["unreadable_days"] == ["2026-09-10"]
    only_unreadable = {d: None for d in DAYS}
    assert evidence.assess(only_unreadable)["verdict"] == "unverified"


def test_a_month_with_rows_only_at_the_end_is_partial_not_full():
    """Review finding: only the tail was checked, so one row on the 30th gave 'full' for a month 1 day in 30 covered."""
    only_last_day = evidence.assess(counts(DAYS, {"2026-09-30"}))
    assert only_last_day["verdict"] == "partial" and only_last_day["first_date_with_rows"] == "2026-09-30"
    assert evidence.assess(counts(DAYS, {"2026-09-28", "2026-09-29", "2026-09-30"}))["verdict"] == "partial"


def test_the_start_of_the_window_gets_the_same_three_day_tolerance_as_the_end():
    from_the_4th = evidence.assess(counts(DAYS, {d for d in DAYS if d >= "2026-09-04"}))      # a 3-day head gap
    assert from_the_4th["verdict"] == "full" and from_the_4th["first_date_with_rows"] == "2026-09-04"
    from_the_5th = evidence.assess(counts(DAYS, {d for d in DAYS if d >= "2026-09-05"}))      # 4 days: flagged
    assert from_the_5th["verdict"] == "partial"
    assert evidence.assess(counts(DAYS, set(DAYS)))["first_date_with_rows"] == "2026-09-01"
    assert evidence.assess(counts(DAYS, set()))["first_date_with_rows"] is None
