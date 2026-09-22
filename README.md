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
deterministic evidence, weighs the rest. A tiered hard floor catches
high-confidence attack patterns as a guarantee, not a hope.

## What's new in v0.2

- **Entity extraction & normalized matching**: emails and URLs are extracted by
  regex and compared by exact equality, not substring. `xa@b.example` no longer
  falsely matches `a@b.example`.
- **Negation detection**: if the user says "Do NOT send to attacker@evil.example",
  that destination is marked `user_forbidden` and hard-blocked.
- **Auto-sink detection**: tools not listed in `write_tools` but whose arguments
  look like destinations (emails, URLs, sink parameter names) are automatically
  detected and guarded.
- **Tiered hard floor**: `user_forbidden` → hard block; tool-sourced +
  external domain + no plan → hard block; tool-sourced + internal domain + no
  plan → **confirm** (guided, not static).
- **Source-to-sink taint tracking**: destinations flowing from untrusted tool
  outputs to external sinks are flagged.
- **LLM prompt sandboxing**: XML boundary tags with anti-injection directives
  protect the judge from adversarial text in tool outputs.

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
- **block** — the destination came from a tool output (not the user), was
  explicitly prohibited by the user, or the action is clearly malicious.
- **confirm** — the user authorized it but it's potentially harmful, provenance
  is ambiguous, or rules alone can't decide. Escalate to a human instead of
  silently allowing or blocking.

## How it decides

```
first sink call found (write_tools ∪ sink_tools ∪ auto-detected)
      │
      ▼
build Evidence  ── entity extraction (regex, normalized equality)
      │              negation detection (bounded window, sentence-scoped)
      │              recipient sources, plan membership,
      │              destructive?, external domain?, taint?
      │
      ▼
hard floor?
      │  user_forbidden → BLOCK
      │  tool-sourced + not_in_plan → BLOCK
      │  tool-sourced + no plan + external → BLOCK
      │  tool-sourced + no plan + internal → pass through (CONFIRM later)
      │
      ▼
LLM provider set? ── yes → guided decision (two-axis prompt + Evidence)
      │              no  → rules-only mapping (allow / block / confirm)
```

Only sink calls are judged; reads always pass.

## Configuration

`GuardConfig` is where you describe your agent:

```python
GuardConfig(
    write_tools={"send_email", "delete_file"},          # tools that modify state
    dest_params={"send_email": "recipients"},            # which param holds destinations
    destructive_tools={"delete_file"},                   # irreversible actions
    plans={"task_0": {"send_email"}},                    # reference plans (optional)
    org_domains={"acme.example"},                        # internal domains (optional)
    untrusted_source_tools={"read_email", "search_web"}, # tools reading external data
    sink_tools=set(),                                    # additional sinks beyond write_tools
    negation_keywords={"don't", "never", "不要", "禁止"},  # negative intent tokens
    auto_detect_sinks=True,                              # heuristic sink detection
    sink_param_patterns={"url", "recipient", "endpoint"}, # param name patterns
)
```

See `default_workspace_config()` for a ready-made profile.

## Design & evaluation

See [docs/DESIGN.md](docs/DESIGN.md) for the rationale, evaluation findings,
and architecture decisions.

## License

MIT
