"""Implementation scaffolding for the reporting additions.

NOT the team's graded tests. Pure functions only, so the figures can be checked by hand.
"""
from __future__ import annotations

import json

from payroll_agent import cost_report


def comp(name, amount):
    return {"component_name": name, "amount": amount}


def line(pid, name, gross, net, deductions, earnings, **fields):
    return {"employee_id": pid, "employee_name": name, "payrun_id": "R1", "days_payable": 26.0, "working_days": 26.0,
            "lop_days": 0.0, "gross_pay": gross, "net_pay": net, "total_deductions": deductions,
            "reimbursements": 0.0, "employer_contribution": 100.0, "overtime_pay": 0.0,
            "earnings": earnings, "deductions": [comp("EPF Employee", deductions)], **fields}


ROWS = [line("E2", "Zed", 30000.0, 28200.0, 1800.0, [comp("Basic", 15000.0), comp("HRA", 15000.0)]),
        line("E1", "Asha", 40000.0, 38200.0, 1800.0,
             json.dumps([comp("Basic", 20000.0), comp("HRA", 10000.0), comp("Overtime", 10000.0)]))]
RUN = {"id": "R1", "status": "paid", "pay_period_start": "2026-08-01", "pay_period_end": "2026-08-31",
       "total_gross_pay": 70000.0, "total_net_pay": 66400.0, "total_deductions": 3600.0,
       "total_employer_contribution": 200.0, "employee_count": 2}


def test_component_totals_sum_each_earning_and_deduction_by_name():
    result = cost_report.component_totals(ROWS)
    assert result["earnings_total"] == 70000.0 and result["rows_unreadable"] == 0
    earnings = {item["component"]: item for item in result["earnings"]}
    assert earnings["Basic"] == {"component": "Basic", "total": 35000.0, "people": 2, "share_of_gross_percent": 50.0}
    assert earnings["Overtime"]["people"] == 1 and earnings["Overtime"]["share_of_gross_percent"] == 14.29
    assert [item["component"] for item in result["earnings"]][0] == "Basic"             # largest first
    assert result["deductions"] == [{"component": "EPF Employee", "total": 3600.0, "people": 2}]


def test_a_row_with_unreadable_components_is_counted_not_summed_as_zero():
    result = cost_report.component_totals([*ROWS, line("E3", "Bad", 1.0, 1.0, 0.0, "garbage")])
    assert result["rows_unreadable"] == 1 and result["earnings_total"] == 70000.0


def test_the_register_lists_every_line_sorted_by_name_with_totals_and_a_header_check():
    result = cost_report.build_register(run=RUN, rows=ROWS)
    assert result["calculated"] is True and result["lines_total"] == 2 and result["truncated"] is False
    assert [item["employee_name"] for item in result["lines"]] == ["Asha", "Zed"]
    assert result["lines"][0] == {"employee_id": "E1", "employee_name": "Asha", "days_payable": 26.0,
                                  "working_days": 26.0, "lop_days": 0.0, "gross": 40000.0,
                                  "total_deductions": 1800.0, "reimbursements": 0.0, "net": 38200.0}
    assert result["totals"]["gross_pay"] == 70000.0 and result["totals"]["headcount"] == 2
    assert result["consistency"]["header_matches_slips"] is True


def test_a_register_header_that_disagrees_with_the_lines_is_reported():
    result = cost_report.build_register(run={**RUN, "total_net_pay": 1.0}, rows=ROWS)
    assert result["consistency"]["header_matches_slips"] is False and "total_net_pay" in result["consistency"]["differences"]


def test_the_register_is_capped_and_says_so():
    many = [line(f"E{i}", f"N{i:03}", 100.0, 90.0, 10.0, [comp("Basic", 100.0)]) for i in range(250)]
    result = cost_report.build_register(run=RUN, rows=many)
    assert len(result["lines"]) == cost_report.MAX_REGISTER and result["lines_total"] == 250 and result["truncated"] is True


def test_an_uncalculated_run_has_no_register():
    result = cost_report.build_register(run=RUN, rows=[line("E1", "A", 0.0, 0.0, 0.0, [])])
    assert result["calculated"] is False and result["skipped"][0]["reason"] == "run not calculated"


def run(rid, start, **fields):
    return {"id": rid, "run_type": "regular", "status": "paid", "pay_period_start": start, **fields}


def test_same_month_last_year_finds_the_regular_run_exactly_a_year_earlier():
    current = run("NOW", "2026-09-01")
    runs = [current, run("A", "2025-09-01"), run("B", "2025-10-01"), run("C", "2024-09-01")]
    assert cost_report.same_month_last_year(current, runs)["id"] == "A"


def test_same_month_last_year_ignores_cancelled_bonus_and_missing_runs():
    current = run("NOW", "2026-08-01")
    runs = [current, run("X", "2025-08-01", status="cancelled"), run("Y", "2025-08-01", run_type="bonus")]
    assert cost_report.same_month_last_year(current, runs) is None
    assert cost_report.same_month_last_year({"id": "Z", "pay_period_start": "soon"}, runs) is None
    assert cost_report.same_month_last_year({"id": "Z"}, runs) is None


def test_build_report_is_unchanged_without_components_and_gains_them_when_asked():
    base = cost_report.build_report(run=RUN, rows=ROWS, employees=[], group_by="none")
    assert "components" not in base
    asked = cost_report.build_report(run=RUN, rows=ROWS, employees=[], group_by="none", with_components=True)
    assert asked["components"]["earnings_total"] == 70000.0
    assert {key: value for key, value in asked.items() if key != "components"} == base
