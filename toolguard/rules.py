"""Deterministic checks: build Evidence, and the one hard-floor detector.

The rules never make the final call in guided mode; they produce Evidence for
the LLM. The single exception is `hard_floor`, a high-confidence provenance
signal used as a safety backstop.
"""
from __future__ import annotations

from typing import Optional

from .config import GuardConfig
from .types import Evidence, Message, RecipientSource, ToolCall


def _first_source(value: str, prior: list[Message]) -> RecipientSource:
    """Where a destination value first appears, scanning messages before the call.

    Appears in any user message -> user-authorized. Otherwise, if it appears in a
    tool output -> tool-sourced. Otherwise absent. Full lowercase string match.
    """
    v = (value or "").strip().lower()
    if not v:
        return RecipientSource(value, "absent")
    in_user = False
    tool_name: Optional[str] = None
    for m in prior:
        if v in m.text.lower():
            if m.role == "user":
                in_user = True
            elif m.role == "tool" and tool_name is None:
                tool_name = m.ret_function
    if in_user:
        return RecipientSource(value, "user")
    if tool_name is not None:
        return RecipientSource(value, "tool", tool_name)
    return RecipientSource(value, "absent")


def _is_external(value: str, cfg: GuardConfig) -> bool:
    if not cfg.org_domains or "@" not in value:
        return False
    domain = value.rsplit("@", 1)[-1].strip().lower()
    return domain not in {d.lower() for d in cfg.org_domains}


def build_evidence(tc: ToolCall, prior: list[Message], task: str,
                   cfg: GuardConfig) -> Evidence:
    tool = tc.function
    param = cfg.dest_params.get(tool)
    ev = Evidence(tool=tool)
    ev.destructive = tool in cfg.destructive_tools

    if param is None:
        ev.has_destination_param = False
    else:
        values = tc.args.get(param) or []
        if isinstance(values, str):
            values = [values]
        ev.recipients = [_first_source(v, prior) for v in values]
        if cfg.org_domains and values:
            ev.external = any(_is_external(v, cfg) for v in values)

    plan = cfg.plan_for(task)
    if plan is None or len(plan) == 0:
        ev.plan_tools = sorted(plan) if plan else []
        ev.in_plan = None
    else:
        ev.plan_tools = sorted(plan)
        ev.in_plan = tool in plan

    ev.provenance = _provenance(ev)
    return ev


def _provenance(ev: Evidence) -> str:
    """Coarse provenance verdict from the destination sources."""
    if not ev.has_destination_param:
        return "unclear"
    if not ev.recipients:
        return "unclear"
    sources = {r.source for r in ev.recipients}
    if sources == {"user"}:
        return "user"
    if "tool" in sources or "absent" in sources:
        return "tool"
    return "unclear"


def hard_floor(ev: Evidence) -> bool:
    """The C-signature: a destination that only ever appeared in a tool output,
    and the tool is not in the reference plan. This is the strongest provenance
    signal that the destination was injected, not user-authorized. When true the
    guard blocks regardless of the LLM, guaranteeing zero-leak on that pattern.
    """
    if not ev.has_destination_param or not ev.recipients:
        return False
    any_injected = any(r.source in ("tool", "absent") for r in ev.recipients)
    not_in_plan = ev.in_plan is False
    return any_injected and not_in_plan
