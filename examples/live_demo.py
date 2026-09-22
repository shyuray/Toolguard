"""Wrap a toy agent's tool executor with the guard, in both modes.

Rules-only (no API key). Shows that the injected-destination send is blocked in
intercept mode but merely logged in monitor mode, while the user-authorized send
runs in both.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toolguard import Guard, default_workspace_config
from toolguard.trace import normalize
import json


def fake_executor(tc):
    print("    >> EXECUTED:", tc.function, tc.args.get("recipients") or tc.args)
    return "ok"


def main():
    fixture = json.load(open(os.path.join(os.path.dirname(__file__), "synthetic_trace.json"), encoding="utf-8"))
    case = next(c for c in fixture["cases"] if c["name"] == "injected_send")
    messages = normalize(case["messages"])
    # The history the agent has when it is about to send: everything up to the send.
    history = messages[:4]
    from toolguard.trace import first_write_call
    _, pending = first_write_call(messages, default_workspace_config().write_tools)

    for mode in ("monitor", "intercept"):
        print("mode =", mode)
        guard = Guard(default_workspace_config(), mode=mode)
        guarded = guard.wrap(
            executor=fake_executor,
            messages_getter=lambda: history,
            task=case["task"],
            on_block=lambda r: print("    !! GUARD %s: %s" % (r.decision.value, r.reason)),
            on_confirm=lambda r: False,  # decline on confirm in this demo
        )
        guarded(pending)
        print("-" * 60)


if __name__ == "__main__":
    main()
