"""Implementation scaffolding for the off-cycle report and create rules.

NOT the team's graded tests. Pure functions only, so the rules can be checked offline.
"""
from __future__ import annotations

from datetime import date

from payroll_agent import offcycle

TODAY = date(2026, 10, 11)


def run(number, **fields):
    base = {"id": f"id-{number}", "number": number, "run_type": "regular", "status": "paid",
            "pay_period": "September 2026", "pay_period_start": "2026-09-01", "pay_period_end": "2026-09-30",
            "pay_date": "2026-09-30", "approval_deadline": "2026-09-26", "deadline_state": "on_time",
            "employee_count": 10.0, "total_net_pay": 100000.0, "off_cycle_reason": None,
            "company_id": "C1", "payroll_bank_account_id": "B1", "payment_method": "neft"}
    return {**base, **fields}


BONUS = run("PRUN-2026-00014", run_type="bonus", status="draft", pay_period="Bonus 2026-09-30",
            employee_count=0.0, total_net_pay=0.0, off_cycle_reason="Bonus")
CONTROLS = {"events": [
    {"event_type": "funding_check", "verdict": "no_source", "reason_code": "funding_no_source_linked",
     "required_amount": 0.0, "available_amount": None, "shortfall": None, "created_at": "2026-09-20T10:00:00"},
    {"event_type": "other", "verdict": "x", "created_at": "2026-09-21T10:00:00"}]}


def test_funding_is_the_latest_funding_check():
    assert offcycle.funding(CONTROLS) == {"verdict": "no_source", "reason_code": "funding_no_source_linked",
                                          "required_amount": 0.0, "available_amount": None, "shortfall": None}
    newer = {"events": [*CONTROLS["events"], {"event_type": "funding_check", "verdict": "ok",
                                               "created_at": "2026-09-25T10:00:00"}]}
    assert offcycle.funding(newer)["verdict"] == "ok"
    assert offcycle.funding({"events": []}) == {"none_recorded": True}
    assert offcycle.funding(None) == {"none_recorded": True}


def test_select_runs_drops_regular_runs_and_caps_the_list_newest_first():
    many = [run(f"PRUN-{i:03}", run_type="bonus", pay_date=f"2026-01-{i % 28 + 1:02}") for i in range(30)]
    chosen = offcycle.select_runs([run("REG"), run("NOTYPE", run_type=None), *many])
    assert len(chosen) == offcycle.MAX_RUNS and all(r["run_type"] == "bonus" for r in chosen)
    dates = [r["pay_date"] for r in chosen]
    assert dates == sorted(dates, reverse=True)


def test_a_passed_deadline_the_platform_calls_on_time_is_a_disagreement():
    row = offcycle.summarize(BONUS, offcycle.funding(CONTROLS), TODAY)
    assert row["deadline_passed"] is True and row["platform_deadline_state"] == "on_time"
    assert row["deadline_disagreement"] is True and row["empty"] is True
    assert row["funding"]["verdict"] == "no_source" and row["reason"] == "Bonus"


def test_a_paid_run_past_its_deadline_is_not_a_missed_deadline():
    row = offcycle.summarize(run("X", run_type="bonus", status="paid"), {"none_recorded": True}, TODAY)
    assert row["deadline_passed"] is False and row["deadline_disagreement"] is False


def test_a_future_deadline_is_not_passed_and_a_bad_date_is_not_a_crash():
    assert offcycle.summarize(run("X", run_type="bonus", status="draft", approval_deadline="2026-12-01"),
                              {}, TODAY)["deadline_passed"] is False
    assert offcycle.summarize(run("X", run_type="bonus", status="draft", approval_deadline="soon"),
                              {}, TODAY)["deadline_passed"] is False


def test_the_report_counts_and_names_the_problem_runs():
    other = run("PRUN-2", run_type="off_cycle", status="review", approval_deadline="2026-12-01", employee_count=3.0)
    report = offcycle.build_report([run("REG"), BONUS, other],
                                   {BONUS["id"]: offcycle.funding(CONTROLS), other["id"]: {"unavailable": "boom"}},
                                   TODAY)
    assert report["total"] == 2 and report["shown"] == 2 and report["truncated"] is False
    assert report["deadline_disagreements"] == ["PRUN-2026-00014"] and report["empty_runs"] == ["PRUN-2026-00014"]
    assert {r["number"]: r["funding"] for r in report["runs"]}["PRUN-2"] == {"unavailable": "boom"}


def test_a_report_with_no_off_cycle_runs_is_empty_not_an_error():
    report = offcycle.build_report([run("REG")], {}, TODAY)
    assert report["total"] == 0 and report["runs"] == []


import pytest  # noqa: E402

GOOD = {"run_type": "bonus", "reason": "Diwali bonus", "pay_period_start": "2026-10-01",
        "pay_period_end": "2026-10-31", "pay_date": "2026-10-31"}
EXISTING = [run("REG", status="paid", pay_date="2026-09-30"), BONUS]


def refusal(**changes):
    return offcycle.refusal_for_create({**GOOD, **changes}, EXISTING, TODAY)


def test_a_complete_request_is_not_refused():
    assert refusal() is None


@pytest.mark.parametrize("changes,code", [
    ({"run_type": "regular"}, "invalid_run_type"),
    ({"run_type": None}, "invalid_run_type"),
    ({"run_type": "payroll"}, "invalid_run_type"),
    ({"reason": None}, "reason_required"),
    ({"reason": "   "}, "reason_required"),
    ({"pay_date": None}, "invalid_dates"),
    ({"pay_date": "31/10/2026"}, "invalid_dates"),
    ({"pay_period_end": "2026-09-30"}, "invalid_dates"),     # end before start
    ({"pay_date": "2026-09-30"}, "invalid_dates"),            # pay date before the period starts
    ({"pay_period_start": "2026-10-05", "pay_period_end": "2026-10-06", "pay_date": "2026-10-08"},
     "pay_date_in_past"),
])
def test_each_bad_request_is_refused_with_its_code(changes, code):
    result = refusal(**changes)
    assert result is not None and result["code"] == code and result["message"]


def test_a_duplicate_period_is_refused_unless_the_first_was_cancelled():
    dup = run("PRUN-DUP", run_type="bonus", status="draft", pay_period_start="2026-10-01", pay_period_end="2026-10-31")
    assert offcycle.refusal_for_create(GOOD, [*EXISTING, dup], TODAY)["code"] == "duplicate_run"
    cancelled = {**dup, "status": "cancelled"}
    assert offcycle.refusal_for_create(GOOD, [*EXISTING, cancelled], TODAY) is None
    other_type = {**dup, "run_type": "correction"}
    assert offcycle.refusal_for_create(GOOD, [*EXISTING, other_type], TODAY) is None


def test_no_run_to_copy_the_bank_account_from_is_refused():
    bare = [run("X", company_id=None), run("Y", payroll_bank_account_id="")]
    assert offcycle.refusal_for_create(GOOD, bare, TODAY)["code"] == "no_template_run"


def test_the_template_is_the_latest_usable_non_cancelled_run():
    runs = [run("A", pay_date="2026-01-31", company_id="C-OLD"),
            run("B", pay_date="2026-09-30", company_id="C-NEW"),
            run("C", pay_date="2026-10-30", company_id="C-CANCELLED", status="cancelled")]
    assert offcycle.template_run(runs)["company_id"] == "C-NEW"


def test_the_payload_has_exactly_the_allow_listed_keys_and_copies_the_account():
    payload = offcycle.create_payload(GOOD, offcycle.template_run(EXISTING))
    assert tuple(payload) == offcycle.CREATE_FIELDS
    assert payload["company_id"] == "C1" and payload["payroll_bank_account_id"] == "B1"
    assert payload["payment_method"] == "neft" and payload["off_cycle_reason"] == "Diwali bonus"
    assert payload["pay_period"] == "Bonus 2026-10-31" and payload["run_type"] == "bonus"
    assert "status" not in payload and not any(k.startswith("total_") for k in payload)


def test_a_given_pay_period_label_is_kept():
    assert offcycle.create_payload({**GOOD, "pay_period": " Festival bonus "},
                                   offcycle.template_run(EXISTING))["pay_period"] == "Festival bonus"


def test_a_matching_re_read_is_a_verified_success_and_says_the_run_is_empty():
    payload = offcycle.create_payload(GOOD, offcycle.template_run(EXISTING))
    done = offcycle.outcome(payload, run("PRUN-9", id="N1", run_type="bonus", status="draft", employee_count=0.0))
    assert done["performed"] is True and done["id"] == "N1" and done["number"] == "PRUN-9"
    assert "empty" in done["note"] and "AgentSwitch" in done["note"]


def test_a_re_read_that_does_not_match_is_reported_as_possibly_changed():
    payload = offcycle.create_payload(GOOD, offcycle.template_run(EXISTING))
    for wrong in (run("P", run_type="regular", status="draft"), run("P", run_type="bonus", status="review")):
        result = offcycle.outcome(payload, wrong)
        assert result["error"] is True and result["code"] == "unexpected_state" and result["may_have_changed"] is True


def test_unverified_is_never_worded_as_nothing_changed():
    result = offcycle.unverified("it returned no run id")
    assert result["may_have_changed"] is True and "nothing was changed" not in result["message"]
