"""Turn raw execution traces into normalized Message objects.

Supports the AgentDojo logging shape, where the message list lives in the
`injections` field and each message's text sits in content[*]['content'],
assistant messages carry `tool_calls`, and tool messages carry `tool_call`.
Also accepts a plain list of message dicts for other agents.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .types import Message, ToolCall


def _text(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, list):
        return "\n".join(
            p.get("content", "") for p in c
            if isinstance(p, dict) and p.get("type") == "text")
    if isinstance(c, str):
        return c
    return ""


def normalize(raw_messages: list[dict]) -> list[Message]:
    out: list[Message] = []
    for m in raw_messages:
        role = m.get("role")
        msg = Message(role=role, text=_text(m))
        if role == "assistant":
            for tc in (m.get("tool_calls") or []):
                msg.tool_calls.append(
                    ToolCall(function=tc.get("function"),
                             args=tc.get("args") or {}, id=tc.get("id")))
        if role == "tool":
            msg.ret_function = (m.get("tool_call") or {}).get("function")
        out.append(msg)
    return out


def load_trace(path: str) -> tuple[dict, list[Message]]:
    """Load one AgentDojo-style JSON file. Returns (meta, messages)."""
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    raw = d.get("injections")
    if raw is None:
        raw = d.get("messages", [])
    meta = {k: d.get(k) for k in
            ("suite_name", "pipeline_name", "user_task_id", "injection_task_id")}
    return meta, normalize(raw)


def first_write_call(messages: list[Message], write_tools: set[str]
                     ) -> Optional[tuple[int, ToolCall]]:
    """The judging unit: the first write call in the trace, or None."""
    for i, m in enumerate(messages):
        if m.role == "assistant":
            for tc in m.tool_calls:
                if tc.function in write_tools:
                    return i, tc
    return None


def user_request(messages: list[Message]) -> str:
    for m in messages:
        if m.role == "user":
            return m.text
    return ""


def render_history(prior: list[Message]) -> str:
    """Only tool calls and tool outputs, as text the LLM prompt embeds."""
    lines: list[str] = []
    for m in prior:
        if m.role == "assistant":
            for tc in m.tool_calls:
                lines.append("[assistant] tool_call: %s(%s)"
                             % (tc.function, json.dumps(tc.args, ensure_ascii=False)))
        elif m.role == "tool":
            lines.append("[tool] %s -> %s" % (m.ret_function or "tool", m.text))
    return "\n".join(lines) if lines else "(none)"


def render_tool_call(tc: ToolCall) -> str:
    return "%s(%s)" % (tc.function, json.dumps(tc.args, ensure_ascii=False))
