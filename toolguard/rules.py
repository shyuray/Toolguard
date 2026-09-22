"""Deterministic checks: build Evidence, entity extraction, negation detection,
auto-sink parameter detection, and the hard-floor backstop.

v0.2 changes
- Entity extraction via regex (emails, URLs) replaces raw substring matching.
- Negation-aware source detection (user_forbidden).
- Auto-sink parameter detection for unlisted tools.
- Hard floor expanded: user_forbidden, or tool-sourced + external + no plan.
- Source-to-sink taint flag.
"""
from __future__ import annotations

import re
from typing import Optional

from .config import GuardConfig
from .types import (EntityKind, Evidence, Message, RecipientSource,
                    ToolCall)

# ---------------------------------------------------------------------------
# Entity extraction helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r'[\w.+\-]+@[\w.\-]+\.\w+')
_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+', re.IGNORECASE)

# Sentence boundary characters for negation window scoping
_SENTENCE_BOUNDARY = set('。，、！？.!?,;:\n')


def extract_emails(text: str) -> set[str]:
    """Extract all email-like tokens from *text*, lowercased."""
    return {m.lower() for m in _EMAIL_RE.findall(text)}


def extract_urls(text: str) -> set[str]:
    """Extract all URL-like tokens from *text*, lowercased."""
    return {m.lower() for m in _URL_RE.findall(text)}


def normalize_entity(value: str) -> str:
    return (value or "").strip().lower()


def classify_entity(value: str) -> EntityKind:
    v = normalize_entity(value)
    if _EMAIL_RE.fullmatch(v):
        return EntityKind.EMAIL
    if _URL_RE.match(v):
        return EntityKind.URL
    return EntityKind.GENERIC


# ---------------------------------------------------------------------------
# Negation-aware source detection
# ---------------------------------------------------------------------------

def _has_negation_near(text: str, entity_start: int,
                       keywords: set[str], window: int = 60) -> Optional[str]:
    """Check if any negation keyword appears in the *window* characters before
    *entity_start* in *text*, stopping at sentence boundary characters.

    Returns the matched negation keyword or None.
    """
    begin = max(0, entity_start - window)
    segment = text[begin:entity_start].lower()
    # Trim to the last sentence boundary (only look within the same clause)
    for i in range(len(segment) - 1, -1, -1):
        if segment[i] in _SENTENCE_BOUNDARY:
            segment = segment[i + 1:]
            break
    for kw in sorted(keywords, key=len, reverse=True):  # longest first
        if kw in segment:
            return kw
    return None


def _first_source(value: str, prior: list[Message],
                  cfg: GuardConfig) -> RecipientSource:
    """Where a destination value first appears, scanning messages before the call.

    v0.2: uses normalized entity equality (not substring), and checks for
    negation context in user messages to flag user_forbidden.
    """
    v = normalize_entity(value)
    if not v:
        return RecipientSource(value, "absent", entity_type=classify_entity(value))

    etype = classify_entity(value)
    in_user = False
    user_forbidden = False
    negation_phrase: Optional[str] = None
    tool_name: Optional[str] = None

    for m in prior:
        mtext = m.text
        # Extract entities from this message via regex for precise matching
        if etype == EntityKind.EMAIL:
            entities = extract_emails(mtext)
        elif etype == EntityKind.URL:
            entities = extract_urls(mtext)
        else:
            # For generic values, fall back to normalized equality on whitespace-split tokens
            entities = {t.strip().lower() for t in mtext.split()}

        if v in entities:
            if m.role == "user":
                # Check for negation in a bounded window around the entity
                idx = mtext.lower().find(v)
                if idx >= 0 and cfg.negation_keywords:
                    neg = _has_negation_near(mtext, idx, cfg.negation_keywords)
                    if neg is not None:
                        user_forbidden = True
                        negation_phrase = neg
                    else:
                        in_user = True
                else:
                    in_user = True
            elif m.role == "tool" and tool_name is None:
                tool_name = m.ret_function

    # Priority: user_forbidden > user > tool > absent
    if user_forbidden:
        return RecipientSource(value, "user_forbidden",
                               entity_type=etype,
                               negation_context=negation_phrase)
    if in_user:
        return RecipientSource(value, "user", entity_type=etype)
    if tool_name is not None:
        return RecipientSource(value, "tool", tool_name, entity_type=etype)
    return RecipientSource(value, "absent", entity_type=etype)


# ---------------------------------------------------------------------------
# Auto-sink parameter detection
# ---------------------------------------------------------------------------

def detect_sink_params(tc: ToolCall, cfg: GuardConfig) -> list[str]:
    """Return parameter names from *tc* that look like destination/sink params.

    Used when the tool is not in cfg.dest_params and auto_detect_sinks is on.
    """
    if not cfg.auto_detect_sinks:
        return []
    hits: list[str] = []
    for param_name, param_value in tc.args.items():
        name_lower = param_name.lower()
        # Check 1: param name matches a known sink pattern
        if any(pat in name_lower for pat in cfg.sink_param_patterns):
            hits.append(param_name)
            continue
        # Check 2: param value looks like email or URL
        if isinstance(param_value, str):
            if _EMAIL_RE.fullmatch(param_value.strip()) or _URL_RE.match(param_value.strip()):
                hits.append(param_name)
            continue
        if isinstance(param_value, list):
            for item in param_value:
                if isinstance(item, str) and (
                        _EMAIL_RE.fullmatch(item.strip()) or _URL_RE.match(item.strip())):
                    hits.append(param_name)
                    break
    return hits


def _is_external(value: str, cfg: GuardConfig) -> bool:
    if not cfg.org_domains or "@" not in value:
        return False
    domain = value.rsplit("@", 1)[-1].strip().lower()
    return domain not in {d.lower() for d in cfg.org_domains}


# ---------------------------------------------------------------------------
# Evidence building
# ---------------------------------------------------------------------------

def build_evidence(tc: ToolCall, prior: list[Message], task: str,
                   cfg: GuardConfig) -> Evidence:
    tool = tc.function
    ev = Evidence(tool=tool)
    ev.destructive = tool in cfg.destructive_tools

    # Determine which parameter holds destinations
    param = cfg.dest_params.get(tool)
    auto_params: list[str] = []

    if param is not None:
        # Explicitly mapped destination parameter
        values = tc.args.get(param) or []
        if isinstance(values, str):
            values = [values]
        ev.recipients = [_first_source(v, prior, cfg) for v in values]
        if cfg.org_domains and values:
            ev.external = any(_is_external(v, cfg) for v in values)
    elif tool not in cfg.write_tools and cfg.auto_detect_sinks:
        # Auto-detect sink parameters for UNKNOWN tools only.
        # Tools in write_tools but not in dest_params were intentionally
        # configured without destination params (e.g. delete_file).
        auto_params = detect_sink_params(tc, cfg)
        if auto_params:
            all_values: list[str] = []
            for ap in auto_params:
                av = tc.args.get(ap) or []
                if isinstance(av, str):
                    av = [av]
                all_values.extend(av)
            ev.recipients = [_first_source(v, prior, cfg) for v in all_values]
            if cfg.org_domains and all_values:
                ev.external = any(_is_external(v, cfg) for v in all_values)
        else:
            ev.has_destination_param = False
    else:
        ev.has_destination_param = False

    # Plan membership
    plan = cfg.plan_for(task)
    if plan is None or len(plan) == 0:
        ev.plan_tools = sorted(plan) if plan else []
        ev.in_plan = None
    else:
        ev.plan_tools = sorted(plan)
        ev.in_plan = tool in plan

    ev.provenance = _provenance(ev)

    # Source-to-sink taint: any destination from an untrusted tool flowing to a sink
    if ev.recipients:
        for r in ev.recipients:
            if r.source == "tool" and r.source_tool in cfg.untrusted_source_tools:
                ev.source_to_sink_violation = True
                break

    return ev


def _provenance(ev: Evidence) -> str:
    """Coarse provenance verdict from the destination sources."""
    if not ev.has_destination_param:
        return "unclear"
    if not ev.recipients:
        return "unclear"
    sources = {r.source for r in ev.recipients}
    if "user_forbidden" in sources:
        return "forbidden"
    if sources == {"user"}:
        return "user"
    if "tool" in sources or "absent" in sources:
        return "tool"
    return "unclear"


def hard_floor(ev: Evidence) -> bool:
    """High-confidence provenance signals that force BLOCK, overriding the LLM.

    Triggers when:
    1. Any destination is user_forbidden (user explicitly prohibited it), OR
    2. Destination from tool output AND tool not in plan (original v0.1 behavior), OR
    3. Destination from tool output AND no plan AND external domain
       (high-confidence injection: external + tool-sourced + no reference plan).

    Does NOT trigger for tool-sourced + no plan + internal/unknown domain
    (those go to CONFIRM, preserving the guided-not-static philosophy).
    """
    if not ev.has_destination_param or not ev.recipients:
        return False

    # Signal 1: user explicitly said "don't send to X"
    if any(r.source == "user_forbidden" for r in ev.recipients):
        return True

    any_injected = any(r.source in ("tool", "absent") for r in ev.recipients)
    if not any_injected:
        return False

    # Signal 2: original — tool-sourced + plan exists + not in plan
    not_in_plan = ev.in_plan is False
    if not_in_plan:
        return True

    # Signal 3: tool-sourced + no plan + external domain
    no_plan = ev.in_plan is None
    if no_plan and ev.external is True:
        return True

    return False
