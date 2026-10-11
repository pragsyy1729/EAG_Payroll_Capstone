"""Implementation scaffolding for full-name employee search.

NOT the team's graded tests. The fake mimics the live platform: ``search`` matches one word, so a
full name such as "Meera More" finds nothing (found live; "Meera" finds 5 people, "More" finds 4).
"""
from __future__ import annotations

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.core.live_graph import TaskSpec

PEOPLE = [
    {"id": "E1", "first_name": "Meera", "last_name": "More", "email": "meera.more8916@corp.in", "number": "EMP-100"},
    {"id": "E2", "first_name": "Meera", "last_name": "Pawar", "email": "meera.pawar@suryodaya.in", "number": "EMP-101"},
    {"id": "E3", "first_name": "Deepak", "last_name": "More", "email": "deepak.more@suryodaya.in", "number": "EMP-102"},
]


class FakeAgentSwitch:
    def __init__(self, denied=()):
        self.calls, self.denied = [], set(denied)

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        assert name == "Employee.list", f"search must stay read-only; unexpected tool {name}"
        if name in self.denied:
            raise AgentSwitchToolError(name, -32001, "denied")
        term = str(arguments.get("search") or "").lower()
        rows = [p for p in PEOPLE if not term or (" " not in term and (term in p["first_name"].lower()
                                                                       or term in p["last_name"].lower()))]
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": len(rows)}


def search(fake, **args):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    return workers.run_list_employees(ctx, TaskSpec("l", "list_employees", {"jurisdiction": "IN", **args}))


async def test_a_full_name_finds_the_person():
    result = await search(FakeAgentSwitch(), search="Meera More")
    assert result["total"] == 1 and [p["id"] for p in result["data"]] == ["E1"]


async def test_word_order_and_case_do_not_matter():
    for text in ("More Meera", "meera MORE", "  Meera   More "):
        assert [p["id"] for p in (await search(FakeAgentSwitch(), search=text))["data"]] == ["E1"], text


async def test_a_single_word_is_passed_through_unchanged_in_one_call():
    fake = FakeAgentSwitch()
    result = await search(fake, search="Meera", limit=5)
    assert result["total"] == 2 and fake.calls == [("Employee.list", {"search": "Meera", "limit": 5})]


async def test_no_search_text_is_passed_through_unchanged():
    fake = FakeAgentSwitch()
    await search(fake, limit=3)
    assert fake.calls == [("Employee.list", {"limit": 3})]


async def test_a_full_name_with_no_match_is_an_empty_result_not_an_error():
    result = await search(FakeAgentSwitch(), search="Meera Nobody")
    assert result["total"] == 0 and result["data"] == [] and "error" not in result


async def test_a_full_name_search_respects_the_limit_and_reports_the_true_total():
    people = [{"id": f"X{i}", "first_name": "Sam", "last_name": f"Lee{i}", "email": "", "number": ""} for i in range(8)]
    fake = FakeAgentSwitch()
    PEOPLE.extend(people)
    try:
        result = await search(fake, search="Sam Lee", limit=3)
    finally:
        del PEOPLE[-8:]
    assert result["total"] == 8 and len(result["data"]) == 3


async def test_a_failing_lookup_is_the_tools_own_error():
    result = await search(FakeAgentSwitch(denied=("Employee.list",)), search="Meera More")
    assert result["error"] is True and result["code"] == -32001
