# Design notes

## Why separate provenance from harm

A tool-call guard has to answer two different questions that are easy to
conflate:

1. **Provenance** — did the human user authorize this destination and action, or
   did they come from something the agent *read* (a tool output, a document)? A
   prompt injection lands in tool output, so a destination that first appears
   there is the signature of an injected action.
2. **Harm** — independent of who asked, is the action destructive or does it send
   data to an external party?

Deterministic checks answer provenance well and answer harm poorly. A language
model answers harm well but, left alone, over-weights it: it blocks anything that
*looks* dangerous even when the user plainly asked for it.

## What an offline evaluation showed

We evaluated three engines on saved agent runs, using constructed
"injection-succeeded" traces as the must-block set and direct user requests as
the should-pass set (labeled purely on *who asked*):

- **Rules alone** were precise: zero misses on the must-block set. But a large
  share of the should-pass set came back *undecidable* — deletes have no
  destination to trace, and send calls under an empty reference plan can't be
  judged by plan membership.
- **LLM alone** always decided, and it caught the whole must-block set — but it
  over-blocked the should-pass set heavily, because those requests *looked*
  like exfiltration even though the user issued them directly.
- **LLM + deterministic evidence** cut that over-blocking substantially. Handed
  the facts "this recipient is in the user request" and "this tool is in the
  plan," the model flipped many blocks back to allow, with reasons that cited the
  evidence. The evidence pulled it from content-panic toward provenance
  reasoning.

On the clean subset — cases whose *content* is harmless, so the only difference
between the two sets is who asked — all three engines discriminated perfectly.
Provenance is a learnable, checkable signal; the mess is only where provenance
and harm disagree.

## The resulting architecture (v0.2)

### Evidence, not verdicts

Rules produce **evidence** — structured facts about each destination's origin,
entity type, and negation context — rather than final verdicts. The LLM or
rules-only fallback consumes this evidence to make the decision.

### Entity extraction and negation detection

v0.2 replaces the fragile substring matching of v0.1 with:

- **Regex-based entity extraction**: emails and URLs are identified by pattern
  and compared by normalized equality, not `in`. This prevents `xa@b.example`
  from falsely matching `a@b.example`.
- **Negation-aware polarity**: when a destination entity appears in a user
  message, the guard checks a bounded window (60 characters before the entity,
  stopping at sentence boundaries) for negation keywords. If "don't", "never",
  "禁止", etc. are found, the destination is marked `user_forbidden` rather than
  `user` — meaning the user *prohibited* this destination.

### Tiered hard floor

The hard floor forces a **BLOCK** that overrides the LLM, but is deliberately
kept narrow to avoid over-blocking:

| Condition | Decision | Rationale |
|---|---|---|
| Any destination is `user_forbidden` | **BLOCK** | User explicitly said "don't send to X" |
| Destination from tool output + plan exists + tool not in plan | **BLOCK** | Classic injected-destination signature (v0.1 behavior) |
| Destination from tool output + no plan + external domain | **BLOCK** | High-confidence: external target + tool-sourced + no plan |
| Destination from tool output + no plan + internal/unknown domain | **CONFIRM** | Could be legitimate ("reply to whoever emailed me"); escalate, don't hard-block |

This preserves the **guided-not-static** philosophy: the hard floor catches
high-confidence attacks, while ambiguous cases go to the LLM or human review
rather than being silently blocked.

### Auto-sink parameter detection

v0.2 introduces heuristic detection of sink parameters for tools not explicitly
listed in `write_tools` or `dest_params`. If a tool's arguments contain
parameter names matching common sink patterns (`url`, `endpoint`, `recipient`,
etc.) or values that look like emails or URLs, the guard treats the call as a
sink and applies provenance checks. This catches novel exfiltration tools
(e.g. `post_data(url=...)`) without requiring configuration changes.

Auto-detection only applies to unknown tools — tools already in `write_tools`
but not in `dest_params` are intentionally configured without destination
tracking (e.g. `delete_file`).

### LLM prompt sandboxing

The LLM judge prompt uses XML boundary tags (`<untrusted_history>`) with
explicit anti-injection directives, telling the model to treat tool output
contents as raw data, not instructions. This reduces the risk of the judge
itself being manipulated by adversarial text in the agent's execution trace.

### The `confirm` path

`confirm` exists for the honest case that earlier setups collapsed: the user
authorized something genuinely risky, or provenance is ambiguous. Escalate to a
human rather than silently allowing or blocking. The `Guard.wrap()` API takes an
`on_confirm` callback that gives the integrator control over the UX.

## What this does not measure

Constructed must-block samples, a limited set of task families, regex matching as
a proxy for taint tracking, reference plans that won't exist at deployment, and a
single model. Negation detection uses keyword heuristics, not full NLU. The guard
is a starting point for measurement and layering, not a solved defense.
