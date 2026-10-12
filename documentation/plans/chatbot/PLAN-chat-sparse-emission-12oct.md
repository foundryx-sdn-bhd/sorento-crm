# PLAN: chat-sparse-emission (lane CHAT-SPARSE)

Status: Plan (small fix track candidate; no migration, no auth change). Size M.

## Problem

Lane CHAT-SPEED (#1602) measured the semantic parser call as most of a chatbot turn
(p50 ~3.0 s, ~1.9 s of it decoding the JSON emission; ~408 output tokens on average, ~70%
of them keys whose value is null / [] / false). Option D gate: turn p95 <= 8 s (main
baseline with embeddings 10.9 s).

## Approach

Sparse emission: the parser emits only keys that carry a value. The engine already
tolerates absent keys for older prompt versions (`head/parser.py` `TOLERATED_ABSENT`);
this lane generalises "absent == default" to every key and stops the wire schema from
forcing every key.

1. Schema: strict structured output requires every property in `required`. Move to a
   sparse-capable schema (details filled in after reading `llm_call` and the provider).
2. Prompt: new UNLABELLED parser version built on the production line (v52) and the
   code-constant prompt, adding the sparse-emission rule. No label is moved.
3. Engine: absent == default for every key family (red tests first).
4. Measure: decode tokens / latency before vs after on 20 made-up live turns (cap ~60 calls).
5. Crew runs the per-PR eval gate.

## Test list

(filled in during Build)
