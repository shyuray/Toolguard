"""Turn raw execution traces into normalized Message objects.

Supports the AgentDojo logging shape, where the message list lives in the
`injections` field and each message's text sits in content[*]['content'],
assistant messages carry `tool_calls`, and tool messages carry `tool_call`.
Also accepts a plain list of message dicts for other agents.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .config import GuardConfig
from .types import Message, ToolCall

_EMAIL_RE = re.compile(r'[\w.+\-]+@[\w.\-]+\.\w+')
_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+', re.IGNORECASE)


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


def _arg_looks_like_sink(param_name: str, param_value: Any,
                         patterns: set[str]) -> bool:
    """Heuristic: does this arg name or value look like a sink destination?"""
    name_lower = param_name.lower()
    if any(pat in name_lower for pat in patterns):
        return True
    if isinstance(param_value, str):
        v = param_value.strip()
        if _EMAIL_RE.fullmatch(v) or _URL_RE.match(v):
            return True
    if isinstance(param_value, list):
        for item in param_value:
            if isinstance(item, str):
                v = item.strip()
                if _EMAIL_RE.fullmatch(v) or _URL_RE.match(v):
                    return True
    return False


def first_sink_call(messages: list[Message], cfg: GuardConfig
                    ) -> Optional[tuple[int, ToolCall]]:
    """Find the first tool call that qualifies as a sink.

    A call is a sink if:
    1. Its function is in write_tools or sink_tools, OR
    2. auto_detect_sinks is on AND any of its arguments look like a
       destination (name matches sink_param_patterns, or value is email/URL).

    This replaces first_write_call as the primary selection function in v0.2.
    """
    explicit = cfg.all_sink_tools
    for i, m in enumerate(messages):
        if m.role == "assistant":
            for tc in m.tool_calls:
                if tc.function in explicit:
                    return i, tc
                if cfg.auto_detect_sinks and tc.args:
                    for pname, pval in tc.args.items():
                        if _arg_looks_like_sink(pname, pval,
                                               cfg.sink_param_patterns):
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
