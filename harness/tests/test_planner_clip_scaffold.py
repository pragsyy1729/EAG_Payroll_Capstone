"""Implementation scaffolding for planner._clip's handling of record fields.

NOT the team's graded tests. Guards the bug where a record's own `id` was cut
from the model's prompt because AgentSwitch returns fields alphabetically and
the clip kept only the first 30.
"""
from __future__ import annotations

from payroll_agent.planner import _clip


def employee_like():
    """Alphabetical like AgentSwitch's Employee rows: 40 fields sort before `id`."""
    row = {f"a{i:02d}": i for i in range(40)}
    row.update({"company_id": "company-1", "email": "x@y.in", "id": "emp-1", "number": "E-7", "zz": 1})
    return dict(sorted(row.items()))


def test_record_id_survives_the_field_cap():
    clipped = _clip({"data": [employee_like()]})["data"][0]
    assert clipped["id"] == "emp-1"
    assert clipped["number"] == "E-7"
    assert len(clipped) == 30


def test_identifying_fields_come_first():
    clipped = _clip(employee_like())
    assert list(clipped)[:2] == ["id", "number"]


def test_small_records_keep_every_field():
    small = {"status": "draft", "id": "r1", "total": 3}
    assert _clip(small) == small

