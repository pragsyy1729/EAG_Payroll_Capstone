"""Implementation scaffolding for the eval runner's task preconditions.

NOT the team's graded tests. An armed task assumes its target records are in certain states; a
precondition re-reads them first so a task never runs if another team has changed one.
"""
from __future__ import annotations

from evals.runner import check_preconditions

TASK = {"jurisdiction": "IN", "preconditions": [
    {"tool": "EmployeeLoan.get", "args": {"id": "L1"}, "expect": {"status": "cancelled"},
     "claim": "LOAN-1 is cancelled"}]}


class FakeClient:
    def __init__(self, record=None, error=None):
        self.record, self.error = record, error

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        if self.error:
            raise self.error
        return self.record


async def test_met_preconditions_let_the_task_run():
    assert await check_preconditions(TASK, {}, FakeClient({"status": "cancelled"})) is None


async def test_a_changed_record_stops_the_task_with_a_clear_reason():
    reason = await check_preconditions(TASK, {}, FakeClient({"status": "draft"}))
    assert "precondition not met" in reason and "LOAN-1 is cancelled" in reason and "the task was not run" in reason


async def test_a_precondition_that_cannot_be_checked_also_stops_the_task():
    reason = await check_preconditions(TASK, {}, FakeClient(error=RuntimeError("down")))
    assert "could not be checked" in reason and "down" in reason


async def test_a_task_with_no_preconditions_is_unaffected():
    assert await check_preconditions({"jurisdiction": "IN"}, {}, FakeClient({})) is None
