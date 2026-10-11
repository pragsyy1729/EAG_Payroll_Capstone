"""Implementation scaffolding for the statutory calendar additions.

NOT the team's graded tests. Pure functions only.
"""
from __future__ import annotations

from datetime import date

import pytest

from payroll_agent import statutory

TODAY = date(2026, 10, 11)


@pytest.mark.parametrize("period_end,quarter_due", [
    ("2026-06-30", "2026-07-31"), ("2026-08-31", "2026-10-31"), ("2026-12-31", "2027-01-31"),
    ("2026-03-31", "2026-05-31"), ("2026-01-31", "2026-05-31"), ("2026-04-30", "2026-07-31")])
def test_form_24q_is_due_after_the_quarter_of_the_period_end(period_end, quarter_due):
    assert statutory.india_returns(period_end, TODAY)["form_24q"]["date"] == quarter_due


@pytest.mark.parametrize("period_end,form_16_due", [
    ("2026-08-31", "2027-06-15"), ("2026-03-31", "2026-06-15"), ("2026-04-01", "2027-06-15"),
    ("2027-01-31", "2027-06-15"), ("2024-02-29", "2024-06-15")])
def test_form_16_is_due_on_15_june_after_the_financial_year(period_end, form_16_due):
    assert statutory.india_returns(period_end, TODAY)["form_16"]["date"] == form_16_due


def test_india_entries_are_labelled_and_carry_status_and_days_left():
    entry = statutory.india_returns("2026-08-31", TODAY)["form_24q"]
    assert entry["basis"] == statutory.INDIA_BASIS and entry["status"] == "upcoming" and entry["days_left"] == 20
    past = statutory.india_returns("2026-03-31", TODAY)["form_24q"]
    assert past["status"] == "past_due_date" and past["days_left"] < 0
    assert "Q4" in past["rule"] and "Jan-Mar" in past["rule"]


@pytest.mark.parametrize("bad", [None, "", "soon", "2026-13-40", "1999-01-01", "2101-01-01"])
def test_an_unusable_period_end_gives_an_empty_calendar(bad):
    assert statutory.india_returns(bad, TODAY) == {} and statutory.us_returns(bad, TODAY) == {}


def test_form_941_follows_the_quarter_and_a_saturday_due_date_moves_to_monday():
    q3 = statutory.us_returns("2026-09-30", TODAY)["form_941"]
    assert q3["date"] == "2026-11-02"                          # 31 October 2026 is a Saturday
    assert "moved from Saturday 2026-10-31" in q3["rule"] and "holidays are not applied" in q3["basis"]
    q1 = statutory.us_returns("2026-03-31", TODAY)["form_941"]
    assert q1["date"] == "2026-04-30" and "moved" not in q1["rule"]    # a Thursday
    q2 = statutory.us_returns("2026-05-15", TODAY)["form_941"]
    assert q2["date"] == "2026-07-31"                          # Q2, a Friday


def test_form_940_and_w2_are_due_31_january_after_the_year_and_a_sunday_moves_to_monday():
    result = statutory.us_returns("2026-12-15", TODAY)
    assert result["form_941"]["date"] == "2027-02-01"          # Q4: 31 January 2027 is a Sunday
    assert result["form_940"]["date"] == "2027-02-01" and result["form_w2"]["date"] == "2027-02-01"
    assert statutory.us_returns("2026-05-15", TODAY)["form_940"]["date"] == "2027-02-01"


def test_state_schedules_report_cycle_and_state_but_assert_no_date():
    configs = {"PTConfig": [{"state": "Maharashtra", "deduction_cycle": "monthly"}],
               "LWFConfig": [{"state": "Maharashtra", "deduction_cycle": "half_yearly"}]}
    result = statutory.state_schedules(configs)
    assert result["professional_tax"] == {"state": "Maharashtra", "cycle": "monthly",
                                          "due": "state-specific; verify with the state portal (no date asserted)"}
    assert result["lwf"]["cycle"] == "half_yearly" and "date" not in result["lwf"]
    assert statutory.state_schedules({"PTConfig": []}) == {}
    assert set(statutory.state_schedules({"LWFConfig": [{"state": "X"}]})) == {"lwf"}
