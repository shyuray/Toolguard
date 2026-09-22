"""Offline tests. No network, no model calls — the LLM path uses ReplayProvider.

v0.2 additions:
- test_negation_user_forbidden
- test_negation_scoping_does_not_bleed
- test_zero_plan_source_to_sink_external_blocked
- test_zero_plan_source_to_sink_internal_confirms
- test_auto_sink_detection
- test_normalized_entity_matching
"""
import json

from toolguard import Guard, default_workspace_config
from toolguard.trace import normalize, first_write_call, first_sink_call, load_trace
from toolguard.rules import build_evidence, hard_floor, _first_source, extract_emails
from toolguard.llm import (build_prompt, parse_response, ParseError,
                           ReplayProvider)
from toolguard.types import Decision, Message
from toolguard.config import GuardConfig


def _msgs(case):
    return normalize(case["messages"])


def _load_fixture():
    import os
    p = os.path.join(os.path.dirname(__file__), "..", "examples", "synthetic_trace.json")
    return json.load(open(p, encoding="utf-8"))


def _case(name):
    fx = _load_fixture()
    return next(c for c in fx["cases"] if c["name"] == name)


# ---- trace ----

def test_normalize_and_first_write():
    case = _case("benign_user_send")
    msgs = _msgs(case)
    assert msgs[1].role == "user"
    fw = first_write_call(msgs, default_workspace_config().write_tools)
    assert fw is not None
    idx, tc = fw
    assert tc.function == "send_email"


# ---- rules: source tracking ----

def test_source_user_vs_tool():
    cfg = default_workspace_config()
    prior = [Message("user", "email dana@acme.example please"),
             Message("tool", "found pickup.9f2@mailbox.example",
                     ret_function="search_calendar_events")]
    assert _first_source("dana@acme.example", prior, cfg).source == "user"
    assert _first_source("pickup.9f2@mailbox.example", prior, cfg).source == "tool"
    assert _first_source("nobody@nowhere.example", prior, cfg).source == "absent"


def test_hard_floor_triggers_on_injection():
    case = _case("injected_send")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    idx, tc = first_write_call(msgs, cfg.write_tools)
    ev = build_evidence(tc, msgs[:idx], task="user_task_0", cfg=cfg)
    assert ev.provenance == "tool"
    assert hard_floor(ev) is True


def test_hard_floor_not_triggered_for_user_send():
    case = _case("benign_user_send")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    idx, tc = first_write_call(msgs, cfg.write_tools)
    ev = build_evidence(tc, msgs[:idx], task="task_send", cfg=cfg)
    assert ev.provenance == "user"
    assert hard_floor(ev) is False


def test_delete_has_no_destination_param():
    case = _case("user_delete_file")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    idx, tc = first_write_call(msgs, cfg.write_tools)
    ev = build_evidence(tc, msgs[:idx], task="task_delete_file", cfg=cfg)
    assert ev.has_destination_param is False


# ---- guard: rules-only ----

def test_guard_rules_only_decisions():
    cfg = default_workspace_config()
    g = Guard(cfg)
    want = {"benign_user_send": Decision.ALLOW,
            "injected_send": Decision.BLOCK,
            "user_delete_file": Decision.CONFIRM}
    fx = _load_fixture()
    for case in fx["cases"]:
        if case["name"] not in want:
            continue
        res = g.decide(_msgs(case), task=case["task"])
        assert res.decision == want[case["name"]], (case["name"], res.decision)
    # the injected one must be caught by the hard floor
    inj = _case("injected_send")
    assert g.decide(_msgs(inj), task=inj["task"]).source == "hard_floor"


# ---- guard: LLM path via ReplayProvider ----

def test_llm_path_honored_when_floor_absent():
    cfg = default_workspace_config()
    case = _case("benign_user_send")
    # LLM says confirm; floor absent, so guard must honor it.
    prov = ReplayProvider(
        {"*": '{"provenance":"user","harm":"low","decision":"confirm","reason":"double-check"}'},
        key_fn=lambda p: "*")
    g = Guard(cfg, provider=prov)
    res = g.decide(_msgs(case), task=case["task"])
    assert res.decision == Decision.CONFIRM
    assert res.source == "llm"


def test_floor_overrides_llm_allow():
    cfg = default_workspace_config()
    case = _case("injected_send")
    # Even if the LLM is fooled into 'allow', the floor blocks.
    prov = ReplayProvider(
        {"*": '{"provenance":"user","harm":"low","decision":"allow","reason":"looks fine"}'},
        key_fn=lambda p: "*")
    g = Guard(cfg, provider=prov)
    res = g.decide(_msgs(case), task=case["task"])
    assert res.decision == Decision.BLOCK
    assert res.source == "hard_floor"


def test_parse_failure_escalates_to_confirm():
    cfg = default_workspace_config()
    case = _case("benign_user_send")
    prov = ReplayProvider({"*": "not json at all"}, key_fn=lambda p: "*")
    g = Guard(cfg, provider=prov)
    res = g.decide(_msgs(case), task=case["task"])
    assert res.decision == Decision.CONFIRM
    assert res.source == "llm"


# ---- llm helpers ----

def test_parse_response_variants():
    assert parse_response('{"decision":"allow","reason":"x"}')["decision"] == "allow"
    assert parse_response('```json\n{"decision":"block","reason":"y"}\n```')["decision"] == "block"
    assert parse_response('Sure. {"decision":"confirm","reason":"z"} done')["decision"] == "confirm"
    for bad in ("", "no json", '{"decision":"maybe"}'):
        try:
            parse_response(bad)
            assert False, "should have raised"
        except ParseError:
            pass


def test_prompt_has_two_axes():
    p = build_prompt("req", "hist", "send_email(...)", "EVIDENCE")
    assert "Provenance" in p and "Harm" in p and "EVIDENCE" in p
    assert '"decision"' in p


# ============================================================================
# v0.2 NEW TESTS
# ============================================================================

# ---- negation detection ----

def test_negation_user_forbidden():
    """User explicitly says 'Do NOT send to attacker@evil.example'.
    The guard must recognize this as user_forbidden and BLOCK via hard_floor.
    """
    case = _case("negation_forbidden")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    g = Guard(cfg)
    res = g.decide(msgs, task=case["task"])
    assert res.decision == Decision.BLOCK, f"Expected BLOCK, got {res.decision}"
    assert res.source == "hard_floor"
    # Check that the evidence marks the recipient as forbidden
    assert res.evidence is not None
    forbidden = [r for r in res.evidence.recipients if r.source == "user_forbidden"]
    assert len(forbidden) >= 1
    assert "attacker@evil.example" in [r.value for r in forbidden]


def test_negation_scoping_does_not_bleed():
    """User says 'Don't delete the old file, but email the report to alice@acme.example'.
    Negation applies to 'delete', not to alice's email. alice must be source='user'.
    """
    case = _case("negation_scoping")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    g = Guard(cfg)
    res = g.decide(msgs, task=case["task"])
    # alice@acme.example should be user-authorized, not forbidden
    assert res.evidence is not None
    alice = [r for r in res.evidence.recipients if r.value == "alice@acme.example"]
    assert len(alice) == 1
    assert alice[0].source == "user", f"Expected 'user', got '{alice[0].source}'"
    # Decision should be ALLOW (user-authorized + tool in plan)
    assert res.decision == Decision.ALLOW


def test_zero_plan_external_blocked():
    """No plan for this task. Destination from tool output + external domain.
    Hard floor should trigger (high-confidence injection).
    """
    case = _case("zero_plan_external")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    cfg.org_domains = {"acme.example"}  # enable external detection
    g = Guard(cfg)
    res = g.decide(msgs, task=case["task"])
    assert res.decision == Decision.BLOCK, f"Expected BLOCK, got {res.decision}"
    assert res.source == "hard_floor"


def test_zero_plan_internal_confirms():
    """No plan for this task. Destination from tool output but INTERNAL domain.
    Should NOT hard-block — should CONFIRM (guided-not-static philosophy).
    """
    case = _case("zero_plan_internal")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    cfg.org_domains = {"acme.example"}  # colleague@acme.example is internal
    g = Guard(cfg)
    res = g.decide(msgs, task=case["task"])
    assert res.decision == Decision.CONFIRM, f"Expected CONFIRM, got {res.decision}"
    assert res.source == "rules"  # rules-only fallback, not hard_floor


def test_auto_sink_detection():
    """post_data(url=...) is not in write_tools but 'url' matches sink heuristic.
    The guard should detect it as a sink and judge it.
    """
    case = _case("auto_sink_post_data")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    g = Guard(cfg)
    res = g.decide(msgs, task=case["task"])
    # Should NOT return "no write call to guard" — it must be judged
    assert res.source != "no_write", "auto-sink detection failed: tool not judged"
    # The URL came from tool output, so it should be blocked or confirmed
    assert res.decision in (Decision.BLOCK, Decision.CONFIRM), (
        f"Expected BLOCK or CONFIRM, got {res.decision}")


def test_normalized_entity_matching():
    """'xa@b.example' in prior should NOT match target 'a@b.example'.
    This was a substring bug in v0.1 where `'a@b' in 'xa@b'` was True.
    """
    cfg = default_workspace_config()
    prior = [Message("user", "Watch out for xa@b.example, it is suspicious.")]
    result = _first_source("a@b.example", prior, cfg)
    # a@b.example should NOT be found in user message (xa@b.example != a@b.example)
    assert result.source == "absent", (
        f"Expected 'absent', got '{result.source}' — substring match bug still present")
