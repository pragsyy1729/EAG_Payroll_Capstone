"""Headcount rules: how many employees, in total and by department, status and employment type.

No network, no async: ``workers.run_headcount_report`` fetches; this module counts. The counts are computed
here because a model asked to tally a hundred rows lists them or miscounts them, and its tally would not appear
in any tool result for the answer to be checked against.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

UNASSIGNED = "Unassigned"
UNKNOWN = "unknown"


def _department(row: dict[str, Any]) -> str:
    return str(row.get("_department_id_display") or row.get("department") or UNASSIGNED)


def _status(row: dict[str, Any]) -> str:
    return str(row.get("status") or UNKNOWN)


def build_headcount(rows: list[dict[str, Any]]) -> dict[str, Any]:
    departments: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        departments.setdefault(_department(row), []).append(row)
    ordered = sorted(departments.items(), key=lambda item: (-len(item[1]), item[0]))
    return {
        "calculated": True,
        "total": len(rows),
        "by_status": dict(Counter(_status(row) for row in rows)),
        "by_employment_type": dict(Counter(str(row.get("employment_type") or UNKNOWN) for row in rows)),
        "by_department": {
            name: {"total": len(members), "by_status": dict(Counter(_status(row) for row in members))}
            for name, members in ordered
        },
        "note": ("These are counts computed in code from every employee record. Quote them as they are and "
                 "do not list individual employees unless the user asked for names."),
    }
