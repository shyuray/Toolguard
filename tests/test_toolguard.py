"""Offline tests. No network, no model calls — the LLM path uses ReplayProvider."""
import json

from toolguard import Guard, default_workspace_config
from toolguard.trace import normalize, first_write_call, load_trace
from toolguard.rules import build_evidence, hard_floor, _first_source
from toolguard.llm import (build_prompt, parse_response, ParseError,
                           ReplayProvider)
from toolguard.types import Decision, Message


def _msgs(case):
    return normalize(case["messages"])


def _load_fixture():
    import os
    p = os.path.join(os.path.dirname(__file__), "..", "examples", "synthetic_trace.json")
    return json.load(open(p, encoding="utf-8"))


# ---- trace ----

def test_normalize_and_first_write():
    fx = _load_fixture()
    case = next(c for c in fx["cases"] if c["name"] == "benign_user_send")
    msgs = _msgs(case)
    assert msgs[1].role == "user"
    fw = first_write_call(msgs, default_workspace_config().write_tools)
    assert fw is not None
    idx, tc = fw
    assert tc.function == "send_email"


# ---- rules: source tracking ----

def test_source_user_vs_tool():
    prior = [Message("user", "email dana@acme.example please"),
             Message("tool", "found pickup.9f2@mailbox.example", ret_function="search_calendar_events")]
    assert _first_source("dana@acme.example", prior).source == "user"
    assert _first_source("pickup.9f2@mailbox.example", prior).source == "tool"
    assert _first_source("nobody@nowhere.example", prior).source == "absent"


def test_hard_floor_triggers_on_injection():
    fx = _load_fixture()
    case = next(c for c in fx["cases"] if c["name"] == "injected_send")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    idx, tc = first_write_call(msgs, cfg.write_tools)
    ev = build_evidence(tc, msgs[:idx], task="user_task_0", cfg=cfg)
    assert ev.provenance == "tool"
    assert hard_floor(ev) is True


def test_hard_floor_not_triggered_for_user_send():
    fx = _load_fixture()
    case = next(c for c in fx["cases"] if c["name"] == "benign_user_send")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    idx, tc = first_write_call(msgs, cfg.write_tools)
    ev = build_evidence(tc, msgs[:idx], task="task_send", cfg=cfg)
    assert ev.provenance == "user"
    assert hard_floor(ev) is False


def test_delete_has_no_destination_param():
    fx = _load_fixture()
    case = next(c for c in fx["cases"] if c["name"] == "user_delete_file")
    msgs = _msgs(case)
    cfg = default_workspace_config()
    idx, tc = first_write_call(msgs, cfg.write_tools)
    ev = build_evidence(tc, msgs[:idx], task="task_delete_file", cfg=cfg)
    assert ev.has_destination_param is False


# ---- guard: rules-only ----

def test_guard_rules_only_decisions():
    fx = _load_fixture()
    cfg = default_workspace_config()
    g = Guard(cfg)
    want = {"benign_user_send": Decision.ALLOW,
            "injected_send": Decision.BLOCK,
            "user_delete_file": Decision.CONFIRM}
    for case in fx["cases"]:
        res = g.decide(_msgs(case), task=case["task"])
        assert res.decision == want[case["name"]], (case["name"], res.decision)
    # the injected one must be caught by the hard floor
    inj = next(c for c in fx["cases"] if c["name"] == "injected_send")
    assert g.decide(_msgs(inj), task=inj["task"]).source == "hard_floor"


# ---- guard: LLM path via ReplayProvider ----

def test_llm_path_honored_when_floor_absent():
    fx = _load_fixture()
    cfg = default_workspace_config()
    case = next(c for c in fx["cases"] if c["name"] == "benign_user_send")
    # LLM says confirm; floor absent, so guard must honor it.
    prov = ReplayProvider({"*": '{"provenance":"user","harm":"low","decision":"confirm","reason":"double-check"}'},
                          key_fn=lambda p: "*")
    g = Guard(cfg, provider=prov)
    res = g.decide(_msgs(case), task=case["task"])
    assert res.decision == Decision.CONFIRM
    assert res.source == "llm"


def test_floor_overrides_llm_allow():
    fx = _load_fixture()
    cfg = default_workspace_config()
    case = next(c for c in fx["cases"] if c["name"] == "injected_send")
    # Even if the LLM is fooled into 'allow', the floor blocks.
    prov = ReplayProvider({"*": '{"provenance":"user","harm":"low","decision":"allow","reason":"looks fine"}'},
                          key_fn=lambda p: "*")
    g = Guard(cfg, provider=prov)
    res = g.decide(_msgs(case), task=case["task"])
    assert res.decision == Decision.BLOCK
    assert res.source == "hard_floor"


def test_parse_failure_escalates_to_confirm():
    fx = _load_fixture()
    cfg = default_workspace_config()
    case = next(c for c in fx["cases"] if c["name"] == "benign_user_send")
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
