"""Implementation scaffolding for the off-cycle workers and capabilities.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform.
"""
from __future__ import annotations

import asyncio

import pytest

from payroll_agent import offcycle, workers
from payroll_agent.agentswitch import AgentSwitchError, AgentSwitchToolError
from payroll_agent.capabilities import default_registry
from payroll_agent.core.live_graph import TaskSpec

from test_offcycle_scaffold import BONUS, CONTROLS, GOOD, TODAY, run  # noqa: E402  (shared fixtures)


class FakeAgentSwitch:
    """PayRun.list/get/create over an in-memory list; ``rest`` serves the controls endpoint."""

    def __init__(self, runs, *, controls=None, bad_controls=(), create_reply="record", reread=None):
        self.runs = [dict(r) for r in runs]
        self.controls, self.bad_controls = controls or {}, set(bad_controls)
        self.create_reply, self.reread = create_reply, reread
        self.calls: list[tuple[str, dict]] = []
        self.rest_calls: list[tuple[str, str]] = []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        await asyncio.sleep(0)
        if name == "PayRun.list":
            offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
            return {"data": self.runs[offset:offset + limit], "limit": limit, "offset": offset,
                    "total": len(self.runs)}
        if name == "PayRun.get":
            if self.reread is not None:
                return dict(self.reread)
            return dict(next(r for r in self.runs if r["id"] == arguments["id"]))
        if name == "PayRun.create":
            if self.create_reply == "error":
                raise AgentSwitchToolError(name, -32000, "validation failed")
            record = run(f"PRUN-NEW-{len(self.runs)}", id=f"new-{len(self.runs)}", status="draft",
                         employee_count=0.0, **arguments)
            self.runs.append(record)
            return {"note": "created"} if self.create_reply == "no_id" else dict(record)
        raise AssertionError(f"unexpected tool {name}")

    async def rest(self, method, path, *, jurisdiction, **_):
        self.rest_calls.append((method, path))
        assert method == "GET" and path.endswith("/controls"), f"only the controls read is allowed: {method} {path}"
        run_id = path.split("/")[-2]
        if run_id in self.bad_controls:
            raise AgentSwitchError("controls unavailable")
        return self.controls.get(run_id, {"events": []})


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setattr(workers, "_today", lambda: TODAY)


def ctx(fake):
    return workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)


def report(fake):
    return workers.run_offcycle_report(ctx(fake), TaskSpec("o", "offcycle_report", {"jurisdiction": "IN"}))


def create(fake, **overrides):
    args = {"jurisdiction": "IN", **GOOD, **overrides}
    args = {k: v for k, v in args.items() if v is not None}
    return workers.run_create_off_cycle_run(ctx(fake), TaskSpec("c", "create_off_cycle_run", args))


def tools(fake):
    return [name for name, _ in fake.calls]


async def test_the_report_lists_off_cycle_runs_with_funding_and_flags_the_disagreement():
    fake = FakeAgentSwitch([run("REG"), BONUS], controls={BONUS["id"]: CONTROLS})
    result = await report(fake)
    assert result["total"] == 1 and result["deadline_disagreements"] == ["PRUN-2026-00014"]
    assert result["runs"][0]["funding"]["verdict"] == "no_source"
    assert tools(fake) == ["PayRun.list"]
    assert fake.rest_calls == [("GET", f"/api/payroll/off-cycle/runs/{BONUS['id']}/controls")]


async def test_one_failing_controls_read_leaves_that_run_unavailable_and_the_rest_intact():
    other = run("PRUN-2", id="id-2", run_type="off_cycle", status="review")
    fake = FakeAgentSwitch([BONUS, other], controls={BONUS["id"]: CONTROLS}, bad_controls=("id-2",))
    result = await report(fake)
    by_number = {r["number"]: r["funding"] for r in result["runs"]}
    assert by_number["PRUN-2"]["unavailable"] and by_number["PRUN-2026-00014"]["verdict"] == "no_source"


async def test_a_short_run_list_is_scan_incomplete_named_for_the_capability():
    fake = FakeAgentSwitch([BONUS])
    original = fake.call_tool

    async def short(name, arguments=None, *, jurisdiction):
        reply = await original(name, arguments, jurisdiction=jurisdiction)
        return {**reply, "total": 50} if name == "PayRun.list" else reply
    fake.call_tool = short
    result = await report(fake)
    assert result["error"] is True and result["code"] == "scan_incomplete" and result["tool"] == "offcycle_report"


async def test_a_complete_request_creates_one_run_with_only_the_allow_listed_fields():
    fake = FakeAgentSwitch([run("REG", pay_date="2026-09-30"), BONUS])
    result = await create(fake)
    assert result["performed"] is True and result["run_type"] == "bonus" and result["status"] == "draft"
    assert tools(fake) == ["PayRun.list", "PayRun.create", "PayRun.get"]
    created = [args for name, args in fake.calls if name == "PayRun.create"]
    assert len(created) == 1
    assert tuple(created[0]) == offcycle.CREATE_FIELDS
    assert fake.rest_calls == []


@pytest.mark.parametrize("overrides,code", [
    ({"run_type": "regular"}, "invalid_run_type"), ({"reason": None}, "reason_required"),
    ({"pay_date": None}, "invalid_dates"), ({"pay_date": "2026-10-01", "pay_period_start": "2026-09-01",
                                              "pay_period_end": "2026-09-30"}, "pay_date_in_past")])
async def test_a_refusal_is_a_result_and_nothing_is_written(overrides, code):
    fake = FakeAgentSwitch([run("REG", pay_date="2026-09-30")])
    result = await create(fake, **overrides)
    assert result["error"] is True and result["code"] == code and "may_have_changed" not in result
    assert result["message"].endswith("nothing was changed") and "PayRun.create" not in tools(fake)


async def test_a_duplicate_is_refused():
    fake = FakeAgentSwitch([run("REG", pay_date="2026-09-30")])
    first = await create(fake)
    assert first["performed"] is True
    second = await create(fake)
    assert second["code"] == "duplicate_run" and tools(fake).count("PayRun.create") == 1


async def test_two_identical_requests_at_once_create_exactly_one_run():
    fake = FakeAgentSwitch([run("REG", pay_date="2026-09-30")])
    results = await asyncio.gather(create(fake), create(fake))
    assert sum(1 for r in results if r.get("performed")) == 1
    assert [r["code"] for r in results if r.get("error")] == ["duplicate_run"]
    assert tools(fake).count("PayRun.create") == 1


async def test_a_tool_error_on_create_is_returned_as_is_without_a_retry():
    fake = FakeAgentSwitch([run("REG", pay_date="2026-09-30")], create_reply="error")
    result = await create(fake)
    assert result["error"] is True and "may_have_changed" not in result and tools(fake).count("PayRun.create") == 1


async def test_a_create_that_returns_no_id_is_unverified_not_unchanged():
    fake = FakeAgentSwitch([run("REG", pay_date="2026-09-30")], create_reply="no_id")
    result = await create(fake)
    assert result["code"] == "outcome_unverified" and result["may_have_changed"] is True
    assert "nothing was changed" not in result["message"]


async def test_a_re_read_that_disagrees_is_reported_as_possibly_changed():
    fake = FakeAgentSwitch([run("REG", pay_date="2026-09-30")], reread=run("P", id="x", run_type="bonus", status="review"))
    result = await create(fake)
    assert result["code"] == "unexpected_state" and result["may_have_changed"] is True


def test_the_capabilities_are_registered_with_the_right_authority():
    registry = default_registry()
    assert registry.get("offcycle_report").side_effect is False
    assert registry.get("create_off_cycle_run").side_effect is True
    only_jurisdiction = registry.validate("create_off_cycle_run", {"jurisdiction": "IN"})
    assert only_jurisdiction == {"jurisdiction": "IN"}      # a missing field becomes a refusal result, not an error
