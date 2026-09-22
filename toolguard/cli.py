"""Command line: `python -m toolguard demo` and `... eval`."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .config import default_workspace_config
from .guard import Guard
from .trace import first_write_call, load_trace
from .types import Decision


def _demo() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    fixture = os.path.normpath(os.path.join(here, "..", "examples", "synthetic_trace.json"))
    data = json.load(open(fixture, encoding="utf-8"))
    cfg = default_workspace_config()
    for case in data["cases"]:
        from .trace import normalize
        messages = normalize(case["messages"])
        for mode in ("monitor", "intercept"):
            guard = Guard(cfg, mode=mode)
            res = guard.decide(messages, task=case["task"])
            print("[%-9s] %-22s -> %-9s (%s) | %s"
                  % (mode, case["name"], res.decision.value, res.source, res.reason))
        print("-" * 70)
    return 0


def _eval(args) -> int:
    import glob
    cfg = default_workspace_config()
    guard = Guard(cfg)
    rows = []
    for path in sorted(glob.glob(os.path.join(args.traces, "*.json"))):
        meta, messages = load_trace(path)
        res = guard.decide(messages, task=meta.get("user_task_id") or "user_task_0")
        rows.append((os.path.basename(path), res.decision.value, res.source))
    for name, dec, src in rows:
        print("%-40s %-9s %s" % (name, dec, src))
    print("total: %d" % len(rows))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="toolguard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="run the bundled synthetic fixture (rules-only)")
    ev = sub.add_parser("eval", help="run the guard over a directory of traces")
    ev.add_argument("--traces", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "demo":
        return _demo()
    if args.cmd == "eval":
        return _eval(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
