"""Implementation scaffolding for the injectable factories of run_task.

NOT the team's graded tests. A fake AgentSwitch client and a scripted model stand in for the platform.
"""
from __future__ import annotations

import argparse

from evals.runner import EVALS, load_tasks, run_task
from evals.transport import ScriptedLLM


class FakeClient:
    def __init__(self):
        self.calls, self.closed = [], False

    def configured(self, jurisdiction):
        return True

    async def list_tools(self, *, jurisdiction, refresh=False):
        return []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        self.calls.append(name)
        return {"data": [], "total": 0}

    async def close(self):
        self.closed = True


def task():
    return next(t for t in load_tasks(str(EVALS / "tasks" / "scaffold.jsonl")) if t["id"] == "scaffold_scripted_read_only")


async def test_the_supplied_factories_are_used_and_the_row_carries_the_verifier_results(tmp_path):
    clients, models = [], []

    def make_client():
        clients.append(FakeClient())
        return clients[-1]

    def make_llm(t, args):
        models.append(t["id"])
        return ScriptedLLM.from_file(EVALS / t["script"])

    args = argparse.Namespace(live=False, min_interval=0.0)
    row = await run_task(task(), args, tmp_path, make_client=make_client, make_llm=make_llm)
    assert models == ["scaffold_scripted_read_only"]
    assert clients and clients[0].calls and all(c.closed for c in clients)
    assert "verifier_results" in row and isinstance(row["verifier_results"], list)
    assert (tmp_path / "scaffold_scripted_read_only.json").exists()                  # the raw record is still written first


async def test_without_factories_the_defaults_are_the_real_client_and_the_gateway(tmp_path, monkeypatch):
    import evals.runner as runner
    made = {"client": 0, "llm": 0}

    class Counting(FakeClient):
        def __init__(self):
            super().__init__()
            made["client"] += 1

    monkeypatch.setattr(runner, "AgentSwitchClient", Counting)
    monkeypatch.setattr(runner, "_make_llm", lambda t, a: (made.__setitem__("llm", made["llm"] + 1)
                                                           or ScriptedLLM.from_file(EVALS / t["script"])))
    row = await runner.run_task(task(), argparse.Namespace(live=False, min_interval=0.0), tmp_path)
    assert made["client"] >= 1 and made["llm"] == 1 and "verifier_results" in row
