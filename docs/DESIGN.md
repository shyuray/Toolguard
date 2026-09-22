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

## The resulting architecture

- Rules produce **evidence**, not verdicts.
- One **hard floor** — destination only in tool output *and* tool not in plan —
  forces a block and overrides the LLM. It is deliberately narrow so it does not
  rely on the model being right; it makes the clearest attack pattern a
  guarantee.
- The **LLM decides** everything else, grounded in the evidence, reasoning along
  the two axes explicitly and emitting `allow` / `block` / `confirm`.
- **confirm** exists for the honest case the earlier setup collapsed: the user
  authorized something genuinely risky. Escalate to a human rather than silently
  allowing or blocking.
- Without an LLM provider the guard degrades to a transparent rules-only mapping,
  escalating anything unresolved to `confirm`.

## What this does not measure

Constructed must-block samples, a single task family, string matching as a proxy
for taint tracking, reference plans that won't exist at deployment, and a single
model. The guard is a starting point for measurement and layering, not a solved
defense.
