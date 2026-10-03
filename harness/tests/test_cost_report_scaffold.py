"""Implementation scaffolding for payroll_agent/cost_report.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

from payroll_agent import cost_report as cr


def slip(emp, gross, net, employer=0.0, ot=0.0, deductions=0.0, name=None):
    return {"id": f"s-{emp}", "employee_id": emp, "employee_name": name or f"Name {emp}",
            "gross_pay": gross, "net_pay": net, "employer_contribution": employer,
            "overtime_pay": ot, "total_deductions": deductions}


def person(emp, dept, loc):
    return {"id": emp, "_department_id_display": dept, "_work_location_id_display": loc}


EMPLOYEES = [person("a", "Production", "Pune"), person("b", "Production", "Pune"),
             person("c", "Stores", "Nashik"), person("d", "Stores", "Nashik")]
NOW = [slip("a", 1000, 900, 100, 50), slip("b", 2000, 1800, 200), slip("c", 500, 450, 50, 10)]
WAS = [slip("a", 800, 700, 80, 100), slip("b", 2000, 1800, 200), slip("d", 400, 350, 40)]
RUN = {"id": "R1", "status": "paid", "pay_period_start": "2026-07-01", "pay_period_end": "2026-07-31",
       "total_gross_pay": 3500, "total_net_pay": 3150, "total_deductions": 0,
       "total_employer_contribution": 350, "employee_count": 3}


def test_summarize_groups_by_department_and_ranks_by_cost():
    out = cr.summarize(NOW, EMPLOYEES, "department")
    assert out["totals"] == {"headcount": 3, "gross_pay": 3500.0, "net_pay": 3150.0, "total_deductions": 0.0,
                             "employer_contribution": 350.0, "overtime_pay": 60.0, "total_cost": 3850.0}
    assert [g["group"] for g in out["groups"]] == ["Production", "Stores"]
    production = out["groups"][0]
    assert production["headcount"] == 2 and production["total_cost"] == 3300.0
    assert production["share_of_cost_pct"] == 85.7
    assert out["groups"][1]["share_of_cost_pct"] == 14.3


def test_summarize_groups_by_location():
    out = cr.summarize(NOW, EMPLOYEES, "location")
    assert [g["group"] for g in out["groups"]] == ["Pune", "Nashik"]


def test_group_by_none_returns_totals_and_no_groups():
    out = cr.summarize(NOW, EMPLOYEES, "none")
    assert out["groups"] == [] and out["totals"]["headcount"] == 3


def test_an_employee_missing_from_the_employee_list_is_grouped_as_unknown():
    out = cr.summarize([slip("z", 100, 90)], EMPLOYEES, "department")
    assert [g["group"] for g in out["groups"]] == ["(unknown)"]


def test_groups_are_capped_but_totals_are_not():
    employees = [person(f"e{i}", f"D{i:02d}", "L") for i in range(35)]
    rows = [slip(f"e{i}", 100, 90) for i in range(35)]
    out = cr.summarize(rows, employees, "department")
    assert len(out["groups"]) == cr.MAX_GROUPS == 30
    assert out["groups_truncated"] is True and out["totals"]["headcount"] == 35


def test_string_amounts_are_parsed():
    out = cr.summarize([slip("a", "1000.00", "900")], EMPLOYEES, "none")
    assert out["totals"]["gross_pay"] == 1000.0 and out["unusable_amounts"] == 0


def test_an_unusable_amount_counts_as_zero_and_is_counted():
    row = {"employee_id": "a", "gross_pay": "abc", "net_pay": 10}      # other fields simply missing
    out = cr.summarize([row], EMPLOYEES, "none")
    assert out["totals"]["gross_pay"] == 0.0 and out["unusable_amounts"] == 1


def test_check_header_reports_only_mismatches():
    totals = cr.summarize(NOW, EMPLOYEES, "none")["totals"]
    ok = {"total_gross_pay": 3500, "total_net_pay": 3150, "total_deductions": 0,
          "total_employer_contribution": 350, "employee_count": 3}
    assert cr.check_header(ok, totals) == {"header_matches_slips": True, "differences": {}}
    off = cr.check_header({**ok, "total_gross_pay": 3510, "employee_count": 4}, totals)
    assert off["header_matches_slips"] is False
    assert off["differences"] == {"total_gross_pay": {"header": 3510.0, "slips": 3500.0},
                                  "employee_count": {"header": 4.0, "slips": 3}}


def test_check_header_allows_rounding_and_skips_missing_header_fields():
    totals = cr.summarize(NOW, EMPLOYEES, "none")["totals"]
    assert cr.check_header({"total_gross_pay": 3500.9}, totals)["header_matches_slips"] is True
    assert cr.check_header({"total_gross_pay": None}, totals)["differences"] == {}


def test_compare_reports_totals_groups_joiners_leavers_and_overtime():
    out = cr.compare(NOW, WAS, EMPLOYEES, "department")
    assert out["totals"]["gross_pay"] == {"previous": 3200.0, "current": 3500.0, "change": 300.0, "change_pct": 9.4}
    assert out["totals"]["total_cost"] == {"previous": 3520.0, "current": 3850.0, "change": 330.0, "change_pct": 9.4}
    assert out["overtime"] == {"previous": 100.0, "current": 60.0, "change": -40.0}
    assert out["groups"] == [
        {"group": "Production", "previous_cost": 3080.0, "current_cost": 3300.0, "change": 220.0, "change_pct": 7.1},
        {"group": "Stores", "previous_cost": 440.0, "current_cost": 550.0, "change": 110.0, "change_pct": 25.0}]
    assert out["joiners"] == {"count": 1, "employees": [{"employee_id": "c", "employee_name": "Name c"}]}
    assert out["leavers"] == {"count": 1, "employees": [{"employee_id": "d", "employee_name": "Name d"}]}


def test_a_group_with_no_previous_cost_has_no_percentage():
    out = cr.compare(NOW, [slip("a", 800, 700, 80)], EMPLOYEES, "department")
    stores = next(g for g in out["groups"] if g["group"] == "Stores")
    assert stores["previous_cost"] == 0.0 and stores["change_pct"] is None


def test_joiner_list_is_capped_but_the_count_is_full():
    rows = [slip(f"e{i:02d}", 100, 90) for i in range(30)]
    out = cr.compare(rows, [], [], "none")
    assert out["joiners"]["count"] == 30
    assert len(out["joiners"]["employees"]) == cr.MAX_PEOPLE == 25


def test_duplicate_rows_count_in_headcount_but_not_as_extra_joiners():
    rows = [slip("a", 100, 90), slip("a", 100, 90)]
    assert cr.summarize(rows, EMPLOYEES, "none")["totals"]["headcount"] == 2
    assert cr.compare(rows, [], EMPLOYEES, "none")["joiners"]["count"] == 1


def test_build_report_for_a_calculated_run():
    out = cr.build_report(run=RUN, rows=NOW, employees=EMPLOYEES, group_by="department")
    assert out["calculated"] is True and out["variance"] is None and out["skipped"] == []
    assert out["consistency"]["header_matches_slips"] is True
    assert out["period"] == {"start": "2026-07-01", "end": "2026-07-31"} and out["group_by"] == "department"


def test_build_report_for_an_uncalculated_run_reports_nothing_else():
    out = cr.build_report(run=RUN, rows=[slip("a", 0, 0)], employees=EMPLOYEES, group_by="department")
    assert out["calculated"] is False and "totals" not in out and "groups" not in out
    assert out["skipped"] == [{"what": "report", "reason": "run not calculated"}]


def test_build_report_notes_when_variance_was_wanted_but_there_is_no_baseline():
    out = cr.build_report(run=RUN, rows=NOW, employees=EMPLOYEES, group_by="none", variance_wanted=True)
    assert out["variance"] is None and out["groups"] == []
    assert out["skipped"] == [{"what": "variance", "reason": "no earlier calculated regular run"}]


def test_build_report_with_variance():
    out = cr.build_report(run=RUN, rows=NOW, employees=EMPLOYEES, group_by="department",
                          prev_rows=WAS, compared_to="R0", variance_wanted=True)
    assert out["variance"]["compared_to"] == "R0" and out["variance"]["joiners"]["count"] == 1


def test_build_report_reports_unusable_amounts():
    out = cr.build_report(run=RUN, rows=[slip("a", "abc", 900, 100)], employees=EMPLOYEES, group_by="none")
    assert {"what": "amounts", "reason": "1 unusable amount(s) counted as 0"} in out["skipped"]


def test_overtime_comes_from_the_overtime_earning_when_the_field_is_empty():
    row = {**slip("a", 1000, 900, 100, 0),
           "earnings": [{"component_name": "Basic", "amount": 700}, {"component_name": "Overtime", "amount": 300}]}
    assert cr.summarize([row], EMPLOYEES, "none")["totals"]["overtime_pay"] == 300.0


def test_overtime_falls_back_to_the_field_when_there_is_no_overtime_earning():
    row = {**slip("a", 1000, 900, 100, 75), "earnings": [{"component_name": "Basic", "amount": 925}]}
    assert cr.summarize([row], EMPLOYEES, "none")["totals"]["overtime_pay"] == 75.0


def test_overtime_earning_names_match_case_insensitively_and_are_summed():
    row = {**slip("a", 1000, 900, 100, 0),
           "earnings": [{"component_name": "overtime weekday", "amount": 100},
                        {"component_name": "Overtime Night", "amount": "50"}]}
    assert cr.summarize([row], EMPLOYEES, "none")["totals"]["overtime_pay"] == 150.0


def test_compare_reports_the_overtime_change_from_earnings():
    now = [{**slip("a", 1000, 900, 100, 0), "earnings": [{"component_name": "Overtime", "amount": 200}]}]
    was = [{**slip("a", 1000, 900, 100, 0), "earnings": [{"component_name": "Overtime", "amount": 500}]}]
    assert cr.compare(now, was, EMPLOYEES, "none")["overtime"] == {"previous": 500.0, "current": 200.0, "change": -300.0}
