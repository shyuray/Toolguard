"""Core data types for toolguard."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Decision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    CONFIRM = "confirm"
    UNDECIDABLE = "undecidable"


class EntityKind(str, Enum):
    """What kind of value a destination entity is."""
    EMAIL = "email"
    URL = "url"
    PATH = "path"
    GENERIC = "generic"


@dataclass
class ToolCall:
    function: str
    args: dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None


@dataclass
class Message:
    """One turn in an execution trace."""
    role: str  # system | user | assistant | tool
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    ret_function: Optional[str] = None  # for role == "tool": the call it answers


@dataclass
class RecipientSource:
    """Where a destination value first appeared in the trace.

    source values:
        "user"           — found in a user message (positive / authorizing context)
        "user_forbidden" — found in a user message inside a negation context
        "tool"           — first appeared in a tool output
        "absent"         — not found anywhere in prior messages
    """
    value: str
    source: str            # "user" | "user_forbidden" | "tool" | "absent"
    source_tool: Optional[str] = None  # tool name when source == "tool"
    entity_type: EntityKind = EntityKind.GENERIC
    negation_context: Optional[str] = None  # the negation phrase when user_forbidden

    @property
    def text(self) -> str:
        if self.source == "user":
            return "user request"
        if self.source == "user_forbidden":
            ctx = (" (%s)" % self.negation_context) if self.negation_context else ""
            return "user request (FORBIDDEN — user explicitly prohibited this destination%s)" % ctx
        if self.source == "tool":
            return "tool output (%s)" % (self.source_tool or "unknown")
        return "not found"


# v0.2 alias — new name, old name kept for backward compatibility
DestinationEntity = RecipientSource


@dataclass
class Evidence:
    """Deterministic facts a guard can hand to an LLM or use directly."""
    tool: str
    recipients: list[RecipientSource] = field(default_factory=list)
    has_destination_param: bool = True
    plan_tools: list[str] = field(default_factory=list)
    in_plan: Optional[bool] = None       # None => plan empty/unknown
    destructive: bool = False
    external: Optional[bool] = None       # any destination outside org domains
    provenance: str = "unclear"           # "user" | "tool" | "forbidden" | "unclear"
    source_to_sink_violation: bool = False  # v0.2: taint from untrusted source to sink

    def render(self) -> str:
        lines = ["Evidence collected by deterministic checks:"]
        if not self.has_destination_param:
            lines.append(
                "- pending tool %s has no destination parameter; "
                "source tracking not applicable" % self.tool)
        elif self.recipients:
            for r in self.recipients:
                lines.append("- destination %s first appears in: %s" % (r.value, r.text))
        else:
            lines.append("- pending tool %s has no destination values" % self.tool)
        lines.append("- tools in the reference plan for this task: [%s]"
                     % ", ".join(self.plan_tools))
        if self.in_plan is None:
            lines.append("- pending tool %s is in the reference plan: "
                         "unknown (reference plan is empty)" % self.tool)
        else:
            lines.append("- pending tool %s is in the reference plan: %s"
                         % (self.tool, "yes" if self.in_plan else "no"))
        lines.append("- action is destructive/irreversible: %s"
                     % ("yes" if self.destructive else "no"))
        if self.external is not None:
            lines.append("- a destination is outside the organization's domains: %s"
                         % ("yes" if self.external else "no"))
        if self.source_to_sink_violation:
            lines.append("- SOURCE-TO-SINK VIOLATION: destination originated from "
                         "untrusted tool output flowing to an external sink")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "recipients": [{"value": r.value, "source": r.source,
                            "source_tool": r.source_tool,
                            "entity_type": r.entity_type.value,
                            "negation_context": r.negation_context}
                           for r in self.recipients],
            "has_destination_param": self.has_destination_param,
            "plan_tools": self.plan_tools,
            "in_plan": self.in_plan,
            "destructive": self.destructive,
            "external": self.external,
            "provenance": self.provenance,
            "source_to_sink_violation": self.source_to_sink_violation,
        }


@dataclass
class GuardResult:
    decision: Decision
    reason: str
    evidence: Optional[Evidence] = None
    source: str = "rules"       # "hard_floor" | "rules" | "llm" | "no_write"
    tool: Optional[str] = None
    args: Optional[dict] = None
    llm_raw: Optional[str] = None
    llm_fields: Optional[dict] = None  # parsed provenance/harm/decision from the LLM

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "source": self.source,
            "tool": self.tool,
            "args": self.args,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "llm_fields": self.llm_fields,
            "llm_raw": self.llm_raw,
        }
