"""Implementation scaffolding for the seat-token mode of the AgentSwitch client.

NOT the team's graded tests. A mock HTTP transport stands in for AgentSwitch.
"""
from __future__ import annotations

import httpx
import pytest

from payroll_agent.agentswitch import AgentSwitchClient, AgentSwitchError

TOKEN = "seat-token-abc123"


def client_with(monkeypatch, *, token=TOKEN, base="http://as.test", instance="suryodaya", status=200):
    for name in ("AGENTSWITCH_TOKEN", "AGENTSWITCH_BASE_URL", "AGENTSWITCH_INSTANCE"):
        monkeypatch.delenv(name, raising=False)
    for name in ("IN", "US"):
        for suffix in ("BASE_URL", "EMAIL", "PASSWORD"):
            monkeypatch.delenv(f"AGENTSWITCH_{name}_{suffix}", raising=False)
    if token is not None:
        monkeypatch.setenv("AGENTSWITCH_TOKEN", token)
    if base is not None:
        monkeypatch.setenv("AGENTSWITCH_BASE_URL", base)
    if instance is not None:
        monkeypatch.setenv("AGENTSWITCH_INSTANCE", instance)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status, text="no")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "PayRun.list"}]}})

    return AgentSwitchClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


@pytest.mark.parametrize("instance,jurisdiction,other", [("suryodaya", "IN", "US"), ("keystone", "US", "IN"),
                                                         ("Suryodaya", "IN", "US")])
def test_the_instance_decides_the_one_configured_jurisdiction(monkeypatch, instance, jurisdiction, other):
    client, _ = client_with(monkeypatch, instance=instance)
    assert client.configured(jurisdiction) is True and client.configured(other) is False


async def test_the_token_is_sent_as_a_bearer_header_and_no_login_is_attempted(monkeypatch):
    client, seen = client_with(monkeypatch)
    tools = await client.list_tools(jurisdiction="IN")
    assert [t["name"] for t in tools] == ["PayRun.list"]
    assert all(r.url.path == "/api/mcp" for r in seen) and not any("login" in r.url.path for r in seen)
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in seen)
    assert all(str(r.url).startswith("http://as.test/") for r in seen)


async def test_a_trailing_slash_on_the_base_url_is_harmless(monkeypatch):
    client, seen = client_with(monkeypatch, base="http://as.test/")
    await client.list_tools(jurisdiction="IN")
    assert seen[0].url.path == "/api/mcp"


def test_an_unknown_instance_is_refused_at_construction(monkeypatch):
    with pytest.raises(AgentSwitchError, match="suryodaya.*keystone"):
        client_with(monkeypatch, instance="nowhere")
    with pytest.raises(AgentSwitchError):
        client_with(monkeypatch, instance=None)


async def test_a_401_is_a_clear_error_and_is_not_retried_with_a_login(monkeypatch):
    client, seen = client_with(monkeypatch, status=401)
    with pytest.raises(AgentSwitchError, match="rejected the seat token"):
        await client.list_tools(jurisdiction="IN")
    assert len(seen) == 1 and not any("login" in r.url.path for r in seen)


async def test_the_token_never_appears_in_an_error_message(monkeypatch):
    client, _ = client_with(monkeypatch, status=500)
    with pytest.raises(AgentSwitchError) as caught:
        await client.list_tools(jurisdiction="IN")
    assert TOKEN not in str(caught.value)


def test_without_a_token_the_credential_mode_is_unchanged(monkeypatch):
    client, _ = client_with(monkeypatch, token=None, base=None, instance=None)
    assert client.configured("IN") is False and client.configured("US") is False
    monkeypatch.setenv("AGENTSWITCH_IN_BASE_URL", "http://in.test")
    monkeypatch.setenv("AGENTSWITCH_IN_EMAIL", "payroll@suryodaya.test")
    monkeypatch.setenv("AGENTSWITCH_IN_PASSWORD", "a-real-looking-secret")
    assert AgentSwitchClient().configured("IN") is True


def test_a_token_without_a_base_url_falls_back_to_credential_mode(monkeypatch):
    client, _ = client_with(monkeypatch, base=None)
    assert client.configured("IN") is False
