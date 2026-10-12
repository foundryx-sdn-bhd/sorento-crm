# PLAN: chatbot reply latency p95 <= 8 s (lane CHAT-SPEED)

Status: Step 2 built (three answer-identical fixes pushed); awaiting the crew eval gate. Track:
small fix track (no migration, no auth/RBAC change, about 200 changed lines of code plus tests).

## Goal

Chatbot reply latency p95 <= 8 s (option D gate, ARCH page
`documentation/plans/chatbot/ARCH-scalable-chat-11oct.html`, PR #1566).

Before numbers (crew eval, main 4ad65522f): without embeddings p50 4710 / p95 9426 ms; with
embeddings working, hidden p50 4127 / p95 10923 ms, dev p50 4019 / p95 13269 ms.

## Step 1: where the time goes (measured 12 Oct 2026)

Parser (gpt-5.4-mini, prompt v3 = 154 KB = 40.6k tokens, 99% cached), 20 made-up turns, 40
live calls: p50 3.2 s, p95 5.8 s, max 10.5 s. Time to first token about 1.0 s; decoding the
emission (354-528 tokens, no reasoning tokens) about 1.9 s. So the prompt diet could not
move it: the cost is in the OUTPUT. 70% of the emission is keys whose value is empty.

CRM side (`engine.run_turn` in process, parser stubbed with the 20 real emissions, MCP faked,
tiny local DB): warm p50 0.52-0.64 s, p95 2.5-2.7 s. cProfile put 44% of turn time in the
company-scope `do_orm_execute` listener: a SELECT on a non-scoped table got
`with_loader_criteria` for all 158 scoped classes (15.3 ms vs 1.1 ms per query, about 40 such
queries per turn).

Embeddings: Tier 3 of the resolver embedded each unresolved token in its own OpenAI round trip
(about 0.33 s each), and re-embedded the carried subject on every follow-up turn.

## Step 2: fixes (answer-identical)

1. `company_scope._scoped_classes_in`: the fallback keeps only scoped classes whose table is in
   the statement or reachable through a relationship. Same SQL, compared per statement in
   `tests/test_company_scope_fallback.py`. CRM side after: p50 0.14-0.15 s, p95 0.44-0.57 s,
   replies identical on all 20 turns. Applies to every API request.
2. `entity_resolver._prefetch_embeddings`: all Tier-3 tokens of one resolve in one embedding
   call (same vectors, cosine 1.0). `tests/test_resolver_tier3_one_embedding_call.py`.
3. `entity_resolver._VECTOR_CACHE`: query vectors remembered across requests (bounded LRU,
   512 float64 arrays).

Known trade-off: if the provider hangs, a failed batch is followed by the per-token calls
(the old path), so an outage costs one extra embedding timeout per resolve. Answers unchanged.

Security review round (12 Oct): the walk also reads `column_property` expressions and the
criteria inside loader options (`with_loader_criteria`, relationship `.and_()`); any other
option type, or a lambda, scopes every class as before. Registry caches reset on
`after_configured`.

Measured and dropped: reusing one OpenAI HTTP connection (numbers through the sandbox proxy were
noise, not evidence).

## Open (owner decision, crew-ask on PR #1602)

- Sparse parser emission (about 1.1-1.3 s per turn, changes the model's output, needs its own
  eval-gated lane).
- Hedged parser request for the provider tail (adds spend and TPM pressure).
- Pre-existing, not changed: `query(exists().where(Product...))` is not company-filtered by the
  listener (`with_loader_criteria` does not reach a Core `exists()`).
