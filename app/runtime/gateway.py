"""Model Gateway：OpenAI 兼容客户端（对接本机 cliproxy 127.0.0.1:8317）。"""
from __future__ import annotations

import time

import httpx


class GatewayError(Exception):
    pass


class Gateway:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        retries: int = 2,
        tool_choice: str | dict | None = None,
    ) -> dict:
        choice, _ = self.chat_with_usage(
            messages, tools=tools, max_tokens=max_tokens,
            retries=retries, tool_choice=tool_choice)
        return choice

    def chat_with_usage(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        retries: int = 2,
        tool_choice: str | dict | None = None,
    ) -> tuple[dict, dict | None]:
        """OpenAI 兼容调用，返回 (choice, usage)。usage 供预算结算（GW 层用量事实）。"""
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                if resp.status_code != 200:
                    body = resp.text[:300]
                    raise GatewayError(f"LLM API {resp.status_code}: {body}")
                data = resp.json()
                choice = data["choices"][0]
                if choice.get("finish_reason") == "content_filter":
                    raise GatewayError("LLM response content filtered")
                return choice, data.get("usage")
            except (httpx.HTTPError, GatewayError, KeyError, ValueError) as exc:
                last_err = exc
                if attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
        raise GatewayError(f"LLM call failed after {retries + 1} attempts: {last_err}")