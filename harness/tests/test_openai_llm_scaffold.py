"""Implementation scaffolding for the platform-model client.

NOT the team's graded tests. A mock HTTP transport stands in for the OpenAI-compatible endpoint.
"""
from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI

from evals.scoring import INFRA, infra_reason, score
from payroll_agent.openai_llm import OpenAICompatLLM


def completion(text="hello", model="platform-model"):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def llm_with(replies, seen=None):
    """Answer with the given list of statuses/exceptions in order, then repeat the last."""
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)

    sdk = AsyncOpenAI(base_url="http://m.test/v1", api_key="k", max_retries=0,
                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return OpenAICompatLLM(sdk, model="platform-model", base_delay=0.0)


async def test_a_reply_becomes_text_with_the_provider_and_model():
    seen: list = []
    llm = llm_with([(200, completion("the answer"))], seen)
    result = await llm.complete("the prompt", "the system")
    assert result == {"text": "the answer", "provider": "platform", "model": "platform-model"}
    body = seen[0]
    assert body["messages"] == [{"role": "system", "content": "the system"}, {"role": "user", "content": "the prompt"}]
    assert body["temperature"] == 0 and body["model"] == "platform-model"


async def test_routing_hints_from_the_planner_are_ignored():
    llm = llm_with([(200, completion())])
    assert (await llm.complete("p", "s", request={"provider": "groq", "model": "x"}, session="s1"))["text"] == "hello"


async def test_empty_content_is_empty_text_not_a_crash():
    sdk_reply = completion()
    sdk_reply["choices"][0]["message"]["content"] = None
    assert (await llm_with([(200, sdk_reply)]).complete("p", "s"))["text"] == ""


async def test_a_transient_503_is_retried_and_then_succeeds():
    seen: list = []
    llm = llm_with([(503, {"error": {"message": "busy"}}), (200, completion("ok"))], seen)
    assert (await llm.complete("p", "s"))["text"] == "ok" and len(seen) == 2


async def test_a_persistent_503_is_an_infra_failure():
    llm = llm_with([(503, {"error": {"message": "busy"}})])
    with pytest.raises(RuntimeError, match="model endpoint returned 503") as caught:
        await llm.complete("p", "s")
    assert infra_reason({"exception": {"message": str(caught.value)}}) is not None


async def test_a_429_is_retried_and_reported_as_infra_when_it_persists():
    seen: list = []
    llm = llm_with([(429, {"error": {"message": "slow down"}})], seen)
    with pytest.raises(RuntimeError, match="model endpoint returned 429"):
        await llm.complete("p", "s")
    assert len(seen) == 4


async def test_a_client_error_is_not_retried_and_is_still_infra():
    seen: list = []
    llm = llm_with([(400, {"error": {"message": "bad request"}})], seen)
    with pytest.raises(RuntimeError, match="model endpoint returned 400") as caught:
        await llm.complete("p", "s")
    assert len(seen) == 1 and infra_reason({"exception": {"message": str(caught.value)}}) is not None


async def test_a_timeout_and_a_connection_failure_are_infra():
    for problem, word in ((httpx.ReadTimeout("slow"), "TimeoutError"), (httpx.ConnectError("down"), "ConnectError")):
        with pytest.raises(RuntimeError, match=word) as caught:
            await llm_with([problem]).complete("p", "s")
        assert infra_reason({"exception": {"message": str(caught.value)}}) is not None


async def test_health_costs_no_model_call_and_close_is_safe():
    seen: list = []
    llm = llm_with([(200, completion())], seen)
    assert (await llm.health())["ok"] is True and seen == []
    await llm.close()


def test_a_failed_model_call_scores_as_infra_not_fail():
    record = {"exception": {"message": "model endpoint returned 503: busy"}, "preflight_error": None, "result": None}
    assert score(record, [])["state"] == INFRA
