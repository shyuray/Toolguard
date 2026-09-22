"""The Guard: combine deterministic evidence, a hard-floor backstop, and an
optional guided LLM into one allow/block/confirm decision per write call.

Design (v3/v0.2, guided-not-static):
- Rules never gate on their own; they produce Evidence.
- Hard floor forces BLOCK for:
    * user_forbidden destinations (user explicitly said "don't")
    * tool-sourced + not_in_plan (plan exists)
    * tool-sourced + external + no plan (high-confidence injection)
- With an LLM provider: the LLM decides, grounded in the Evidence. The floor
  still overrides an LLM 'allow'.
- Without a provider (rules-only mode): a transparent mapping from the Evidence
  to allow/block/confirm, with anything unresolved escalated to confirm rather
  than silently allowed.
"""
from __future__ import annotations

from typing import Callable, Optional

from .config import GuardConfig, default_workspace_config
from .llm import Provider, build_prompt, parse_response, ParseError
from .rules import build_evidence, hard_floor
from .trace import (first_sink_call, first_write_call, render_history,
                    render_tool_call, user_request)
from .types import Decision, Evidence, GuardResult, Message, ToolCall


class Guard:
    def __init__(self, config: Optional[GuardConfig] = None,
                 provider: Optional[Provider] = None,
                 mode: str = "intercept"):
        self.cfg = config or default_workspace_config()
        self.provider = provider
        if mode not in ("intercept", "monitor"):
            raise ValueError("mode must be 'intercept' or 'monitor'")
        self.mode = mode

    # ---- core decision ----

    def decide(self, messages: list[Message], task: str,
               pending: Optional[tuple[int, ToolCall]] = None) -> GuardResult:
        if pending is None:
            # v0.2: use first_sink_call which covers write_tools + sink_tools + auto-detect
            pending = first_sink_call(messages, self.cfg)
        if pending is None:
            return GuardResult(Decision.ALLOW, "no write call to guard",
                               source="no_write")
        idx, tc = pending
        prior = messages[:idx]
        ev = build_evidence(tc, prior, task, self.cfg)

        if hard_floor(ev):
            reason = self._hard_floor_reason(ev)
            return GuardResult(
                Decision.BLOCK, reason,
                evidence=ev, source="hard_floor", tool=tc.function, args=tc.args)

        if self.provider is not None:
            return self._llm_decide(messages, idx, tc, ev, task)
        return self._rules_decide(tc, ev)

    def _hard_floor_reason(self, ev: Evidence) -> str:
        """Build a human-readable reason for the hard floor trigger."""
        if any(r.source == "user_forbidden" for r in ev.recipients):
            forbidden = [r for r in ev.recipients if r.source == "user_forbidden"]
            vals = ", ".join(r.value for r in forbidden)
            return ("user explicitly prohibited destination(s): %s" % vals)
        if ev.in_plan is False:
            return ("destination appears only in tool output and the tool is not "
                    "in the reference plan (injected-destination signature)")
        if ev.in_plan is None and ev.external is True:
            return ("destination appears only in tool output, no reference plan "
                    "exists, and destination is external (high-confidence injection)")
        return "hard floor triggered"

    def _llm_decide(self, messages, idx, tc, ev: Evidence, task) -> GuardResult:
        prompt = build_prompt(
            user_request(messages),
            render_history(messages[:idx]), render_tool_call(tc), ev.render())
        raw = self.provider.complete(prompt)
        try:
            fields = parse_response(raw)
            decision = Decision(fields["decision"])
            reason = fields.get("reason", "")
        except ParseError as e:
            # Fail safe: unparseable model output => escalate, never silent allow.
            return GuardResult(Decision.CONFIRM,
                               "LLM response could not be parsed (%s)" % e,
                               evidence=ev, source="llm", tool=tc.function,
                               args=tc.args, llm_raw=raw)
        # Floor already checked; here it did not trigger, so honor the LLM.
        return GuardResult(decision, reason, evidence=ev, source="llm",
                           tool=tc.function, args=tc.args, llm_raw=raw,
                           llm_fields=fields)

    def _rules_decide(self, tc, ev: Evidence) -> GuardResult:
        prov = ev.provenance

        # Forbidden is caught by hard_floor above, but belt-and-suspenders
        if prov == "forbidden":
            return GuardResult(Decision.BLOCK,
                               "user explicitly prohibited this destination",
                               evidence=ev, source="hard_floor", tool=tc.function,
                               args=tc.args)

        if not ev.has_destination_param:
            # Deletes: source tracking cannot answer who asked -> escalate.
            return GuardResult(Decision.CONFIRM,
                               "no destination parameter; provenance cannot be "
                               "decided by rules alone",
                               evidence=ev, source="rules", tool=tc.function,
                               args=tc.args)
        if prov == "user" and ev.in_plan:
            return GuardResult(Decision.ALLOW,
                               "destination user-authorized and tool in plan",
                               evidence=ev, source="rules", tool=tc.function,
                               args=tc.args)
        if prov == "tool":
            # Tool-sourced destination — if we get here, hard_floor didn't trigger,
            # meaning: plan exists and tool IS in plan, or no plan + internal domain.
            # Either way, escalate to confirm (guided-not-static philosophy).
            if ev.in_plan is None:
                return GuardResult(Decision.CONFIRM,
                                   "destination came from tool output and no "
                                   "reference plan exists; escalating for review",
                                   evidence=ev, source="rules", tool=tc.function,
                                   args=tc.args)
            return GuardResult(Decision.BLOCK,
                               "destination came from a tool output, not the user",
                               evidence=ev, source="rules", tool=tc.function,
                               args=tc.args)
        # user-authorized but plan empty/unknown, or otherwise unresolved
        return GuardResult(Decision.CONFIRM,
                           "user-authorized but the plan rule is undecidable",
                           evidence=ev, source="rules", tool=tc.function,
                           args=tc.args)

    # ---- live wrapping ----

    def wrap(self, executor: Callable[[ToolCall], object],
             messages_getter: Callable[[], list[Message]],
             task: str,
             on_block: Optional[Callable[[GuardResult], object]] = None,
             on_confirm: Optional[Callable[[GuardResult], bool]] = None):
        """Return a guarded executor. In intercept mode a BLOCK stops the call and
        a CONFIRM asks on_confirm() (default: treat as block). In monitor mode the
        call always runs; the GuardResult is reported via on_block for logging.
        """
        def guarded(tc: ToolCall):
            result = self.decide(messages_getter(), task, pending=(len(messages_getter()), tc))
            if self.mode == "monitor":
                if on_block and result.decision != Decision.ALLOW:
                    on_block(result)
                return executor(tc)
            if result.decision == Decision.ALLOW:
                return executor(tc)
            if result.decision == Decision.CONFIRM:
                if on_confirm and on_confirm(result):
                    return executor(tc)
                if on_block:
                    on_block(result)
                return None
            if on_block:
                on_block(result)
            return None
        return guarded
