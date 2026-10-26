"""A model client for the platform's OpenAI-compatible endpoint (used by the official harness run).

The planner and the answer step need prompt in, text out, so this wraps one chat completion. It reads
``OPENAI_BASE_URL`` and ``OPENAI_API_KEY`` through the ``openai`` package and the model name from
``OPENAI_MODEL``. Per-step routing hints from the gateway client (provider, model, session) are ignored here:
the platform decides the model. Every failure raises ``RuntimeError("model endpoint ...")``, which the eval
scoring treats as an infrastructure problem, never as the agent being wrong.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError

_RETRY_STATUS = (429, 500, 502, 503, 504)
_MAX_TOKENS_CAP = 16384


class OpenAICompatLLM:
    def __init__(self, client: AsyncOpenAI | None = None, *, model: str | None = None, attempts: int = 4,
                 base_delay: float = 1.0, max_tokens: int = 4096) -> None:
        try:
            self._client = client or AsyncOpenAI(max_retries=0)    # reads OPENAI_BASE_URL and OPENAI_API_KEY
        except OpenAIError as problem:
            raise RuntimeError(f"model endpoint is not configured: {problem}") from problem
        self.model = model or os.getenv("OPENAI_MODEL", "agentswitch-default")
        self.attempts, self.base_delay, self.max_tokens = max(1, attempts), base_delay, max_tokens

    async def complete(self, prompt: str, system: str, **_ignored: Any) -> dict[str, Any]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        last = "no attempt was made"
        tokens = self.max_tokens
        for attempt in range(self.attempts):
            try:
                reply = await self._client.chat.completions.create(
                    model=self.model, messages=messages, temperature=0, max_tokens=tokens)
                text = (reply.choices[0].message.content or "") if reply.choices else ""
                finish = reply.choices[0].finish_reason if reply.choices else None
                if not text.strip():       # a model that spends its budget on reasoning can return nothing visible
                    print(f"model endpoint returned empty text (finish_reason={finish})", file=sys.stderr, flush=True)
                    if finish == "length" and tokens < _MAX_TOKENS_CAP and attempt < self.attempts - 1:
                        tokens = min(tokens * 2, _MAX_TOKENS_CAP)      # it ran out of room: give it more
                        continue
                return {"text": text, "provider": "platform", "model": reply.model or self.model,
                        "finish_reason": finish}
            except APIStatusError as problem:
                if problem.status_code not in _RETRY_STATUS:
                    raise RuntimeError(f"model endpoint returned {problem.status_code}: {problem.message}") from problem
                last = f"model endpoint returned {problem.status_code}: {problem.message}"
            except APITimeoutError as problem:
                last = f"model endpoint TimeoutError: {problem}"
            except (APIConnectionError, httpx.HTTPError) as problem:
                last = f"model endpoint ConnectError: unreachable ({type(problem).__name__})"
            if attempt < self.attempts - 1:
                await asyncio.sleep(self.base_delay * 2 ** attempt)
        raise RuntimeError(last)

    async def health(self) -> dict[str, Any]:
        """Nothing is spent: a configured client is as healthy as can be known without a model call."""
        return {"ok": True, "model": self.model}

    async def close(self) -> None:
        await self._client.close()
