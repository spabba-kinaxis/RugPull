"""Anthropic Messages API adapter. Reads ANTHROPIC_API_KEY from the environment."""

from __future__ import annotations

import os
from typing import Any

DEFAULT_MODEL = "claude-haiku-5-5"


class AnthropicLlm:
    def __init__(self, model: str | None = None, max_tokens: int = 1024) -> None:
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens

    def respond(self, system: str, messages: list[dict[str, Any]],
                tools: list[dict[str, Any]]) -> dict[str, Any]:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        content: list[dict[str, Any]] = []
        for block in msg.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id,
                                "name": block.name, "input": block.input})
        return {"stop_reason": msg.stop_reason, "content": content}
