"""Implementation scaffolding for the payroll readiness rules.

NOT the team's graded tests. Pure functions only, so the rules can be checked offline.
"""
from __future__ import annotations

import json

from payroll_agent import readiness

START, END = "2026-09-01", "2026-09-30"


def person(pid, name="A B", joined="2026-01-01", exit_date=None, status="active"):
    return {"id": pid, "first_name": name.split()[0], "last_name": name.split()[1], "date_of_joining": joined,
            "exit_date": exit_date, "status": status}


def line(pid, days=26.0, working=26.0, lop=0.0, basic=20000.0, gross=40000.0, name="A B"):
    return {"employee_id": pid, "employee_name": name, "days_payable": days, "working_days": working,
            "lop_days": lop, "gross_pay": gross, "net_pay": gross * 0.9,
            "earnings": json.dumps([{"component_name": "Basic", "amount": basic},
                                    {"component_name": "HRA", "amount": gross - basic}])}


def test_period_dates_lists_every_day_and_refuses_bad_or_long_periods():
    days = readiness.period_dates(START, END)
    assert days[0] == START and days[-1] == END and len(days) == 30
    assert readiness.period_dates("2026-09-30", "2026-09-01") is None
    assert readiness.period_dates("soon", END) is None
    assert readiness.period_dates("2026-01-01", "2026-12-31") is None
    assert len(readiness.period_dates(START, START)) == 1


def test_a_joiner_paid_a_full_month_is_flagged_and_a_prorated_one_is_not():
    people = {"E1": person("E1", joined="2026-09-15"), "E2": person("E2", joined="2026-09-15")}
    found = readiness.proration([line("E1", days=26, working=26), line("E2", days=13, working=26)], people, START, END)
    assert [(f["employee_id"], f["severity"]) for f in found] == [("E1", "high")]
    assert "Joined 2026-09-15" in found[0]["message"]


def test_someone_joining_on_the_first_day_is_a_full_month():
    people = {"E1": person("E1", joined=START)}
    assert readiness.proration([line("E1")], people, START, END) == []


def test_a_leaver_paid_a_full_month_is_flagged_but_leaving_on_the_last_day_is_not():
    people = {"E1": person("E1", exit_date="2026-09-10"), "E2": person("E2", exit_date=END)}
    found = readiness.proration([line("E1"), line("E2")], people, START, END)
    assert [(f["employee_id"], f["severity"]) for f in found] == [("E1", "high")]


def test_a_prorated_line_with_no_joining_exit_or_lop_is_noted():
    people = {"E1": person("E1"), "E2": person("E2")}
    found = readiness.proration([line("E1", days=20), line("E2", days=20, lop=6.0)], people, START, END)
    assert [(f["employee_id"], f["severity"]) for f in found] == [("E1", "info")]


def test_unusable_day_counts_and_unknown_people_are_skipped_not_crashed():
    people = {"E1": person("E1", joined="2026-09-15")}
    rows = [line("E1", days=None, working=26), line("E9"), {**line("E1"), "working_days": 0}]
    assert readiness.proration(rows, people, START, END) == []


def application(aid, pid, status, start, end, number="LV-1"):
    return {"id": aid, "number": number, "employee_id": pid, "status": status, "from_date": start, "to_date": end}


def test_pending_leave_overlapping_the_period_is_flagged_by_status():
    people = {"E1": person("E1"), "E2": person("E2")}
    apps = [application("a", "E1", "pending_approval", "2026-09-10", "2026-09-12"),
            application("b", "E1", "draft", "2026-09-20", "2026-09-21"),
            application("c", "E1", "approved", "2026-09-10", "2026-09-12"),            # approved: not pending
            application("d", "E1", "pending_approval", "2026-10-02", "2026-10-03"),    # after the period
            application("e", "E1", "pending_approval", "2026-08-25", "2026-09-02"),    # straddles the start
            application("f", "E7", "pending_approval", "2026-09-10", "2026-09-12")]    # not in the run
    found = readiness.pending_leave(apps, people, START, END)
    assert sorted((f["evidence"]["application_id"], f["severity"]) for f in found) == [
        ("a", "medium"), ("b", "info"), ("e", "medium")]


def test_attendance_that_covers_the_period_raises_nothing():
    counts = {d: 5 for d in readiness.period_dates(START, END)}
    coverage, found = readiness.attendance_coverage(counts, START, END)
    assert found == [] and coverage["uncovered_tail_days"] == 0 and coverage["last_date_with_rows"] == END


def test_attendance_that_stops_early_is_medium_and_names_the_last_date():
    counts = {d: (5 if d <= "2026-09-12" else 0) for d in readiness.period_dates(START, END)}
    coverage, found = readiness.attendance_coverage(counts, START, END)
    assert coverage["last_date_with_rows"] == "2026-09-12" and coverage["uncovered_tail_days"] == 18
    assert [(f["check"], f["severity"]) for f in found] == [("attendance_coverage", "medium")]
    assert "2026-09-12" in found[0]["message"]


def test_a_weekend_sized_tail_is_not_a_finding_but_is_still_reported_as_a_fact():
    counts = {d: (5 if d <= "2026-09-28" else 0) for d in readiness.period_dates(START, END)}   # rows stop 2 days early
    coverage, found = readiness.attendance_coverage(counts, START, END)
    assert found == [] and coverage["uncovered_tail_days"] == 2 and coverage["last_date_with_rows"] == "2026-09-28"
    boundary = {d: (5 if d <= "2026-09-27" else 0) for d in readiness.period_dates(START, END)}  # exactly 3: still fine
    assert readiness.attendance_coverage(boundary, START, END)[1] == []
    over = {d: (5 if d <= "2026-09-26" else 0) for d in readiness.period_dates(START, END)}       # 4 days: flagged
    assert [f["severity"] for f in readiness.attendance_coverage(over, START, END)[1]] == ["medium"]


def test_no_attendance_at_all_is_high():
    coverage, found = readiness.attendance_coverage({d: 0 for d in readiness.period_dates(START, END)}, START, END)
    assert coverage["last_date_with_rows"] is None
    assert [(f["check"], f["severity"]) for f in found] == [("attendance_coverage", "high")]


def test_a_day_that_could_not_be_counted_is_reported_not_treated_as_empty():
    counts = {d: 5 for d in readiness.period_dates(START, END)}
    counts["2026-09-10"] = None
    coverage, found = readiness.attendance_coverage(counts, START, END)
    assert coverage["unreadable_days"] == ["2026-09-10"]
    assert [(f["check"], f["severity"]) for f in found] == [("attendance_coverage", "info")]


def test_lop_days_that_disagree_with_the_attendance_rows_are_flagged():
    lop_rows = [{"date": "2026-09-03", "is_lop": 1}, {"date": "2026-09-04", "is_lop": 1},
                {"date": "2026-08-30", "is_lop": 1},                       # outside the period
                {"date": "2026-09-05", "is_lop": 0}]
    assert readiness.lop_mismatch(line("E1", lop=2.0), lop_rows, START, END) is None
    found = readiness.lop_mismatch(line("E1", lop=5.0), lop_rows, START, END)
    assert found["check"] == "lop_mismatch" and found["evidence"] == {"line_lop_days": 5.0, "attendance_lop_days": 2}


def test_basic_share_is_one_aggregated_result_with_the_lowest_examples():
    rows = [line("E1", basic=20000, gross=50000, name="Low One"), line("E2", basic=30000, gross=50000, name="Fine"),
            {**line("E3"), "earnings": "garbage"}, line("E4", basic=10000, gross=40000, name="Lowest")]
    result = readiness.basic_share(rows)
    assert result["checked"] == 3 and result["unreadable"] == 1 and result["below_50_percent"] == 2
    assert [e["employee_name"] for e in result["examples"]] == ["Lowest", "Low One"]
    assert result["lowest_percent"] == 25.0 and "wage-code" in result["note"]


def test_assemble_numbers_sorts_and_caps_the_findings():
    many = [{"check": "proration", "severity": "info", "employee_id": f"E{i}", "employee_name": f"N{i:02}",
             "message": "m", "evidence": {}} for i in range(50)]
    high = {"check": "attendance_coverage", "severity": "high", "employee_id": None, "employee_name": None,
            "message": "m", "evidence": {}}
    result = readiness.assemble(run={"id": "R", "status": "draft", "pay_period_start": START, "pay_period_end": END},
                                calculated=True, findings=[*many, high], coverage={}, basic={}, skipped=[],
                                candidates_skipped=0)
    assert result["findings"][0]["severity"] == "high" and result["findings"][0]["id"] == "F-001"
    assert len(result["findings"]) == readiness.MAX_FINDINGS and result["truncated"] is True
    assert result["counts"]["by_severity"] == {"high": 1, "info": 50}


def test_a_joiner_whose_working_days_already_start_at_joining_is_prorated_not_flagged():
    """Found live: a July 29 joiner has days_payable 3 and working_days 3 (the platform counts working days from
    joining), which is a correctly prorated line. Prorating means fewer days than the period's working days."""
    people = {"E1": person("E1", joined="2026-07-29"), "E2": person("E2"), "E3": person("E3")}
    rows = [line("E1", days=3, working=3), line("E2", days=27, working=27), line("E3", days=27, working=27)]
    assert readiness.proration(rows, people, "2026-07-01", "2026-07-31") == []
    unprorated = [line("E1", days=27, working=27), line("E2", days=27, working=27)]
    found = readiness.proration(unprorated, people, "2026-07-01", "2026-07-31")
    assert [(f["employee_id"], f["severity"]) for f in found] == [("E1", "high")]
    assert "27 working days" in found[0]["message"]


def test_a_prorated_line_is_judged_against_the_periods_working_days_not_its_own():
    people = {"E1": person("E1"), "E2": person("E2")}
    rows = [line("E1", days=3, working=3), line("E2", days=27, working=27)]            # E1 has no joining date in the period
    found = readiness.proration(rows, people, "2026-07-01", "2026-07-31")
    assert [(f["employee_id"], f["severity"]) for f in found] == [("E1", "info")]


def test_the_report_states_which_checks_ran_and_how_much_each_considered():
    """Found live: with no findings the evidence critic could not tell that pending leave had been checked and
    called the report 'missing leave'. A check that finds nothing must still leave a trace."""
    run = {"id": "R", "status": "draft", "pay_period_start": START, "pay_period_end": END}
    result = readiness.assemble(run=run, calculated=True, findings=[], coverage={}, basic={"below_50_percent": 7},
                                skipped=[], candidates_skipped=0,
                                considered={"attendance_coverage": 30, "pending_leave": 4, "proration": 62, "lop_mismatch": 3})
    checks = result["checks"]
    assert checks["pending_leave"] == {"ran": True, "considered": 4, "unit": "leave applications (pending or draft)", "findings": 0}
    assert checks["attendance_coverage"]["unit"] == "calendar days" and checks["proration"]["unit"] == "pay lines"
    assert checks["attendance_coverage"]["considered"] == 30 and checks["basic_share"]["findings"] == 7
    assert "pending leave" in result["summary"] and "0 findings" in result["summary"]
    skipped = readiness.assemble(run=run, calculated=False, findings=[], coverage={}, basic={}, skipped=[],
                                 candidates_skipped=0, considered={"pending_leave": 2})
    assert skipped["checks"]["proration"]["ran"] is False and skipped["checks"]["pending_leave"]["ran"] is True
