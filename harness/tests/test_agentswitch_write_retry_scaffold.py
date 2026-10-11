"""Implementation scaffolding for the transport's retry rule on writes.

NOT the team's graded tests. A mock HTTP transport stands in for AgentSwitch.
"""
from __future__ import annotations

import httpx
import pytest

from payroll_agent.agentswitch import AgentSwitchClient, AgentSwitchError


def client_with(statuses, monkeypatch):
    """A client whose /api/mcp answers the given statuses in order, then 200. Returns (client, posts)."""
    monkeypatch.setenv("AGENTSWITCH_IN_BASE_URL", "http://as.test")
    monkeypatch.setenv("AGENTSWITCH_IN_EMAIL", "payroll@suryodaya.test")
    monkeypatch.setenv("AGENTSWITCH_IN_PASSWORD", "a-real-looking-secret")
    monkeypatch.setenv("AGENTSWITCH_ATTEMPTS", "4")
    posts: list[str] = []
    queue = list(statuses)

    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr("payroll_agent.agentswitch.asyncio.sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": "t"})
        posts.append(request.url.path)
        status = queue.pop(0) if queue else 200
        if status != 200:
            return httpx.Response(status, text="upstream")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": []}})

    return AgentSwitchClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler))), posts


async def call(client, tool):
    return await client._mcp(client._tenant("IN"), "tools/call", {"name": tool, "arguments": {}})


@pytest.mark.parametrize("status", [502, 503])
async def test_a_write_is_never_re_sent_after_a_gateway_error(status, monkeypatch):
    """Review finding: a 502/503 can arrive after the platform acted, so re-sending PayRun.create could
    create a second run. The caller reports it as possibly changed instead."""
    client, posts = client_with([status, status, 200], monkeypatch)
    with pytest.raises(AgentSwitchError):
        await call(client, "PayRun.create")
    assert len(posts) == 1


async def test_a_write_rejected_with_429_is_retried_because_nothing_ran(monkeypatch):
    client, posts = client_with([429, 200], monkeypatch)
    await call(client, "PayRun.create")
    assert len(posts) == 2


@pytest.mark.parametrize("tool", ["PayRun.list", "PayRun.get", "EPFConfig.list"])
async def test_reads_still_retry_on_gateway_errors(tool, monkeypatch):
    client, posts = client_with([503, 502, 200], monkeypatch)
    await call(client, tool)
    assert len(posts) == 3
