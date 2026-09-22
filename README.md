# toolguard

A provenance-aware, LLM-guided guard for AI agent tool calls.

`toolguard` sits between the moment an agent *decides* to call a write tool
(send an email, delete a file, create an event) and the moment that call
actually runs. For each write call it returns **allow / block / confirm** with a
reason and an audit record, so a prompt-injected agent doesn't quietly send your
data to an attacker.

The core idea: separate **who asked** (provenance) from **what it does** (harm).
Deterministic checks answer provenance precisely — a recipient that first appears
in a *tool output* was not authorized by the user. An LLM, grounded in that
deterministic evidence, weighs the rest. A single hard rule (the
injected-destination signature) is a safety floor that can override the LLM, so
the clearest attack pattern is a guarantee, not a hope.

## Install

```bash
pip install -e .            # rules-only, no dependencies
pip install -e ".[gemini]"  # add the Gemini LLM provider
```

Python 3.9+. The rules engine has zero dependencies; an LLM provider is optional.

## Quick start

Rules-only (no API key):

```python
from toolguard import Guard, default_workspace_config
from toolguard.trace import load_trace

meta, messages = load_trace("trace.json")
guard = Guard(default_workspace_config())
result = guard.decide(messages, task=meta["user_task_id"])
print(result.decision.value, "-", result.reason)   # e.g. block - injected-destination signature
```

Guided (rules + LLM):

```python
from toolguard.llm import GeminiProvider
guard = Guard(default_workspace_config(), provider=GeminiProvider())  # reads GEMINI_API_KEY
result = guard.decide(messages, task=meta["user_task_id"])
print(result.decision.value, result.llm_fields)     # provenance / harm / decision
```

Wrap a live agent's executor:

```python
guarded = guard.wrap(executor=my_tool_runner,
                     messages_getter=lambda: my_agent.history,
                     task="user_task_0",
                     on_block=lambda r: log(r),
                     on_confirm=lambda r: ask_human(r))   # return True to proceed
result_or_none = guarded(pending_tool_call)
```

Try it without writing code:

```bash
python -m toolguard demo                 # runs the bundled synthetic fixture
python -m toolguard eval --traces DIR/   # runs the guard over a folder of traces
python examples/live_demo.py             # monitor vs intercept on one attack case
```

## The three decisions

- **allow** — the user authorized this destination/action and it isn't harmful.
- **block** — the destination came from a tool output (not the user), or the
  action is clearly malicious.
- **confirm** — the user authorized it but it's potentially harmful, or rules
  alone can't decide (e.g. a delete has no destination to trace). Escalate to a
  human instead of silently allowing or blocking.

## How it decides

```
first write call
      │
      ▼
build Evidence  ── deterministic: recipient sources, plan membership,
      │              destructive?, external domain?
      ▼
hard floor?  ── destination only in tool output AND tool not in plan
      │  yes → BLOCK (overrides the LLM)
      │  no
      ▼
LLM provider set? ── yes → guided decision (two-axis prompt + Evidence)
      │              no  → rules-only mapping (allow / block / confirm)
```

Only write calls are judged; reads always pass. The judging unit is the trace's
first write call.

## Configuration

`GuardConfig` is where you describe your agent (see `default_workspace_config()`
for an AgentDojo-style example):

- `write_tools` — the tools that change state; only these are judged.
- `dest_params` — tool → the parameter holding destinations (`send_email` →
  `recipients`). Tools not listed have no destination to trace.
- `destructive_tools` — irreversible tools (delete/overwrite), flagged in evidence.
- `plans` — task id → the tools its reference plan legitimately uses.
- `org_domains` — internal email domains; a destination outside them is flagged
  external (leave empty to disable).

## Evaluating on your own runs

The guard reads the AgentDojo logging shape (messages under `injections`), so you
can replay saved runs offline. `ReplayProvider` lets you feed pre-recorded model
responses instead of live calls, to evaluate an LLM guard without spending quota:

```python
from toolguard.llm import ReplayProvider
guard = Guard(cfg, provider=ReplayProvider(saved_responses, key_fn=my_key))
```

## Limitations

- String matching is a proxy for real provenance/taint tracking; an attacker who
  launders a value through paraphrase can defeat it.
- Reference plans rarely exist at deployment time; they are the weakest input.
- The LLM guard shares the failure modes of the model behind it, including being
  swayed by content it reads. The hard floor is deliberately narrow so it does
  not depend on the model being right.
- Single judging unit (first write call) — a trace with several writes needs the
  guard invoked per call, which `wrap()` does.

See `docs/DESIGN.md` for the evaluation that motivated this architecture.

## License

MIT. This project ships only fictional example data; it contains no personal or
account information.
