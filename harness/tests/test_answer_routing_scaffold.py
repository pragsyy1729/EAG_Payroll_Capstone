"""Implementation scaffolding for per-step routing of the final answer call.

NOT the team's graded tests.
"""
from __future__ import annotations

from types import SimpleNamespace

from payroll_agent import workers
from payroll_agent.core.live_graph import TaskSpec


def answer_ctx(calls):
    async def llm(prompt, system, **kwargs):
        calls.append(kwargs)
        return {"text": "an answer", "provider": "p", "model": "m"}

    nodes = {"n1": {"skill": "list_employees", "state": "succeeded", "input": {}, "result": {"total": 1}}}
    store = SimpleNamespace(snapshot=lambda run_id: SimpleNamespace(nodes=nodes))
    return workers.RunContext(run_id="r", store=store, llm=llm, agentswitch=None, goal="q")


async def answer(monkeypatch, **env):
    for name in ("PAYROLL_ANSWER_PROVIDER", "PAYROLL_ANSWER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    calls: list[dict] = []
    result = await workers.run_answer_with_evidence(
        answer_ctx(calls), TaskSpec("a", "answer_with_evidence", {"query": "q"}))
    assert result["text"] == "an answer"
    return calls


async def test_answer_call_carries_the_configured_provider_and_model(monkeypatch):
    calls = await answer(monkeypatch, PAYROLL_ANSWER_PROVIDER="groq", PAYROLL_ANSWER_MODEL="openai/gpt-oss-120b")
    assert calls == [{"request": {"provider": "groq", "model": "openai/gpt-oss-120b"}}]


async def test_answer_call_is_unchanged_when_nothing_is_configured(monkeypatch):
    assert await answer(monkeypatch) == [{}]


async def test_only_the_configured_fields_are_sent(monkeypatch):
    calls = await answer(monkeypatch, PAYROLL_ANSWER_PROVIDER="groq")
    assert calls == [{"request": {"provider": "groq"}}]
