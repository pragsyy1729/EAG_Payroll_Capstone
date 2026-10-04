"""Implementation scaffolding for payroll_agent/statutory.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

from datetime import date

from payroll_agent import statutory as st

TODAY = date(2026, 8, 3)


def slip(**fields):
    return {"employee_id": "a", "employee_name": "A", "gross_pay": 1000.0, "net_pay": 900.0,
            "esi_covered": 0, **fields}


ROWS_IN = [slip(epf_employee=100, epf_employer=100, esi_employee=5, esi_employer=20,
                professional_tax=200, tds=50),
           slip(employee_id="b", epf_employee=200, epf_employer=200, professional_tax=200, tds=0)]
RUN_IN = {"id": "R1", "status": "paid", "pay_period_start": "2026-07-01", "pay_period_end": "2026-07-31",
          "total_epf_employee": 300, "total_epf_employer": 300, "total_esi_employee": 5,
          "total_esi_employer": 20, "total_pt": 400, "total_tds": 50}
CONFIGS = {
    "EPFConfig": [{"employee_contribution_rate": 12.0, "employer_contribution_rate": 12.0, "eps_rate": 8.33,
                   "eps_wage_ceiling": 15000.0, "edli_rate": 0.5, "edli_wage_ceiling": 15000.0,
                   "admin_charges_rate": 0.5}],
    "ESIConfig": [{"employee_contribution_rate": 0.75, "employer_contribution_rate": 3.25, "wage_ceiling": 21000.0}],
    "PTConfig": [{"deduction_cycle": "monthly", "annual_cap": 2500.0}],
    "LWFConfig": [{"deduction_cycle": "half_yearly", "employee_contribution": 25.0,
                   "employer_contribution": 75.0, "state": "Maharashtra"}],
}


def india(rows=ROWS_IN, run=RUN_IN, configs=CONFIGS):
    return st.build_statutory(run=run, rows=rows, jurisdiction="IN", configs=configs, today=TODAY)


def test_due_dates_for_a_july_period():
    dues = st.due_dates("2026-07-31", TODAY)
    assert dues["tds"]["date"] == "2026-08-07" and dues["tds"]["status"] == "upcoming"
    assert dues["tds"]["days_left"] == 4
    assert dues["epf"]["date"] == dues["esi"]["date"] == "2026-08-15" and dues["epf"]["days_left"] == 12
    assert dues["tds"]["basis"] == "standard India calendar, not from AgentSwitch"


def test_due_dates_for_march_and_december_periods():
    assert st.due_dates("2026-03-31", date(2026, 4, 1))["tds"]["date"] == "2026-04-30"
    assert st.due_dates("2026-03-31", date(2026, 4, 1))["epf"]["date"] == "2026-04-15"
    december = st.due_dates("2026-12-31", date(2027, 1, 1))
    assert december["tds"]["date"] == "2027-01-07" and december["epf"]["date"] == "2027-01-15"


def test_due_status_overdue_and_due_today():
    late = st.due_dates("2026-07-31", date(2026, 8, 20))
    assert late["tds"]["status"] == "overdue" and late["tds"]["days_left"] == -13
    assert st.due_dates("2026-07-31", date(2026, 8, 15))["epf"]["status"] == "due_today"


def test_an_unparseable_period_end_gives_no_due_dates():
    assert st.due_dates(None, TODAY) == {} and st.due_dates("not a date", TODAY) == {}


def test_india_statutes_amounts_due_dates_and_header_check():
    out = india()
    assert [(s["statute"], s["side"], s["amount"]) for s in out["statutes"]] == [
        ("EPF", "employee", 300.0), ("EPF", "employer", 300.0), ("ESI", "employee", 5.0),
        ("ESI", "employer", 20.0), ("Professional tax", "employee", 400.0), ("TDS", "employee", 50.0)]
    tds = next(s for s in out["statutes"] if s["statute"] == "TDS")
    assert tds["due"]["date"] == "2026-08-07" and tds["due"]["days_left"] == 4
    pt = next(s for s in out["statutes"] if s["statute"] == "Professional tax")
    assert pt["due"] is None and "state" in pt["note"]
    assert out["consistency"] == {"matches": True, "compared": 6, "differences": {}}
    assert out["deposit_tracking"].startswith("not tracked in AgentSwitch")
    assert out["period"] == {"start": "2026-07-01", "end": "2026-07-31"} and out["calculated"] is True


def test_a_header_mismatch_is_reported():
    out = india(run={**RUN_IN, "total_tds": 60})
    assert out["consistency"]["matches"] is False
    assert out["consistency"]["differences"] == {"total_tds": {"header": 60.0, "slips": 50.0}}


def test_rows_with_no_amount_and_no_header_are_not_listed_and_no_header_means_null():
    out = india(run={"id": "R1", "status": "paid", "pay_period_end": "2026-07-31"})
    assert all(s["statute"] != "EPS" for s in out["statutes"])
    assert out["consistency"] == {"matches": None, "compared": 0, "differences": {}}


def test_esi_flag_counts_covered_employees_above_the_ceiling_only():
    rows = ROWS_IN + [slip(employee_id="z", gross_pay=25000, esi_covered=1),
                      slip(employee_id="y", gross_pay=20000, esi_covered=1),
                      slip(employee_id="x", gross_pay=30000, esi_covered=0)]
    flag = india(rows=rows)["flags"]["esi_covered_above_ceiling"]
    assert flag["count"] == 1 and flag["ceiling"] == 21000.0
    assert [e["employee_id"] for e in flag["employees"]] == ["z"]


def test_config_is_echoed_and_missing_configs_are_noted():
    out = india()
    assert out["config"]["ESIConfig"]["wage_ceiling"] == 21000.0
    assert out["config"]["PTConfig"] == {"deduction_cycle": "monthly", "annual_cap": 2500.0}
    assert out["config"]["LWFConfig"]["state"] == "Maharashtra"
    bare = india(configs={})
    assert {item["what"] for item in bare["skipped"]} == {"esi flag", "config"}
    assert "flags" in bare and bare["flags"] == {}


US_DEDUCTIONS = [{"component_name": "Federal Withholding", "amount": 100},
                 {"component_name": "Ohio State Withholding", "amount": 30},
                 {"component_name": "Social Security", "amount": 62},
                 {"component_name": "Medicare", "amount": 14.5},
                 {"component_name": "Health Insurance", "amount": 50}]
ROWS_US = [{"employee_id": "a", "gross_pay": 1000.0, "net_pay": 743.5, "deductions": US_DEDUCTIONS}]
RUN_US = {"id": "U1", "status": "approved", "pay_period_start": "2026-08-01", "pay_period_end": "2026-08-31",
          "total_deductions": 256.5}


def us(rows=ROWS_US, run=RUN_US):
    return st.build_statutory(run=run, rows=rows, jurisdiction="US", configs={}, today=TODAY)


def test_us_withholdings_come_from_deduction_components_and_employer_rows_are_unavailable():
    out = us()
    employee = [(s["statute"], s["amount"]) for s in out["statutes"] if s["side"] == "employee"]
    assert employee == [("Federal income tax", 100.0), ("State income tax", 30.0),
                        ("Social Security", 62.0), ("Medicare", 14.5)]
    employer = [s for s in out["statutes"] if s["side"] == "employer"]
    assert [s["statute"] for s in employer] == ["Social Security (employer match)", "Medicare (employer match)",
                                                "FUTA", "SUTA"]
    assert all(s["available"] is False and s["amount"] is None for s in employer)
    assert employer[0]["note"] == "not in AgentSwitch data for this seat"
    assert all(s["due"] is None for s in out["statutes"])
    assert out["non_statutory_deductions"] == 50.0
    assert out["consistency"] == {"matches": True, "compared": 1, "differences": {}}
    assert "flags" not in out and "config" not in out


def test_us_rate_checks_are_informational_and_can_fail():
    checks = {c["statute"]: c for c in us()["rate_checks"]}
    assert checks["Social Security"]["within_tolerance"] is True and checks["Medicare"]["observed_pct"] == 1.45
    assert checks["Social Security"]["basis"].startswith("public statutory rate")
    heavy = [{**ROWS_US[0], "deductions": [{"component_name": "Social Security", "amount": 100}]}]
    assert {c["statute"]: c for c in us(rows=heavy, run={**RUN_US, "total_deductions": 100})["rate_checks"]}[
        "Social Security"]["within_tolerance"] is False


def test_us_deductions_that_are_malformed_never_crash():
    rows = [{**ROWS_US[0], "deductions": 5}, {**ROWS_US[0], "deductions": [None, 3, {"component_name": "Medicare", "amount": "x"}]}]
    out = us(rows=rows, run={**RUN_US, "total_deductions": 0})
    assert {"what": "amounts", "reason": "1 unusable amount(s) counted as 0"} in out["skipped"]


def test_an_uncalculated_run_reports_nothing_else():
    out = india(rows=[{"net_pay": 0}])
    assert out["calculated"] is False and "statutes" not in out
    assert out["skipped"] == [{"what": "report", "reason": "run not calculated"}]


def test_unusable_india_amounts_are_counted_once():
    out = india(rows=[slip(epf_employee="abc", epf_employer=100), slip(epf_employee=float("nan"))])
    assert {"what": "amounts", "reason": "2 unusable amount(s) counted as 0"} in out["skipped"]
