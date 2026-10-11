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


def india_run():
    return {"id": "R1", "status": "paid", "pay_period_start": "2026-08-01", "pay_period_end": "2026-08-31"}


def slip(**fields):
    return {"employee_id": "a", "employee_name": "A", "payrun_id": "R1", "gross_pay": 1000.0, "net_pay": 900.0,
            "esi_covered": 0, **fields}


CONFIGS = {"PTConfig": [{"state": "Maharashtra", "deduction_cycle": "monthly"}],
           "LWFConfig": [{"state": "Maharashtra", "deduction_cycle": "half_yearly"}]}


def test_the_india_report_gains_returns_and_state_schedules_and_keeps_its_old_keys():
    result = statutory.build_statutory(run=india_run(), rows=[slip(epf_employee=100)], jurisdiction="IN",
                                       configs=CONFIGS, today=TODAY)
    assert result["returns"]["form_24q"]["date"] == "2026-10-31"
    assert result["state_schedules"]["professional_tax"]["state"] == "Maharashtra"
    assert {"statutes", "consistency", "config", "deposit_tracking", "skipped"} <= set(result)
    assert "deposit_note" not in result


def test_the_us_report_gains_returns_and_a_deposit_note_without_a_deposit_date():
    us_run = {"id": "U1", "status": "paid", "pay_period_start": "2026-09-01", "pay_period_end": "2026-09-30"}
    rows = [slip(payrun_id="U1", deductions=[{"component_name": "Federal Withholding", "amount": 100}])]
    result = statutory.build_statutory(run=us_run, rows=rows, jurisdiction="US", configs={}, today=TODAY)
    assert result["returns"]["form_941"]["date"] == "2026-11-02" and "state_schedules" not in result
    assert "not in AgentSwitch" in result["deposit_note"] and "15th" in result["deposit_note"]


def test_an_uncalculated_run_still_reports_nothing_else():
    result = statutory.build_statutory(run=india_run(), rows=[slip(net_pay=0)], jurisdiction="IN",
                                       configs=CONFIGS, today=TODAY)
    assert result["calculated"] is False and "returns" not in result and "state_schedules" not in result


def test_a_run_with_an_unusable_period_end_has_no_returns_and_says_so():
    result = statutory.build_statutory(run={**india_run(), "pay_period_end": None}, rows=[slip(epf_employee=100)],
                                       jurisdiction="IN", configs=CONFIGS, today=TODAY)
    assert result["returns"] == {} and any(item["what"] == "return calendar" for item in result["skipped"])


def test_the_capability_description_tells_the_planner_what_may_and_may_not_be_stated():
    from payroll_agent.capabilities import default_registry
    description = default_registry().get("statutory_dues").description
    for phrase in ("Form 24Q", "Form 941", "not from AgentSwitch", "never state a date for professional tax"):
        assert phrase in description, phrase


def test_the_report_says_that_unasserted_dates_are_the_complete_answer_not_missing_evidence():
    """Found live: the evidence critic read 'state-specific; verify (no date asserted)' as missing evidence and
    the planner stalled twice with no answer."""
    india = statutory.build_statutory(run=india_run(), rows=[slip(epf_employee=100)], jurisdiction="IN",
                                      configs=CONFIGS, today=TODAY)
    us_run = {"id": "U1", "status": "paid", "pay_period_start": "2026-09-01", "pay_period_end": "2026-09-30"}
    us = statutory.build_statutory(run=us_run, rows=[slip(payrun_id="U1", deductions=[])], jurisdiction="US",
                                   configs={}, today=TODAY)
    for result in (india, us):
        assert "not missing evidence" in result["note"] and "deliberately not asserted" in result["note"]
    assert "calculated" not in statutory.build_statutory(run=india_run(), rows=[slip(net_pay=0)], jurisdiction="IN",
                                                          configs=CONFIGS, today=TODAY).get("note", "")
