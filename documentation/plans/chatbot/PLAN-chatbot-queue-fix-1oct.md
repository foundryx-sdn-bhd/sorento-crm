# PLAN: per-contact chatbot queue fix (CHATBOT-QUEUE-FIX)

Status: built, in review (PR #1415). Small fix track: no migration, no auth/RBAC change, no new
ingest surface. Grew in-lane with item 7 (parser LLM timeout, owner 1 Oct) and the crew
decision (queue wait sized to n8n's budget, late turns merge session state).

## Journey

A dealer sends several WhatsApp messages in a row. Every message gets an answer, in order when
possible, and never the generic "Sorry, I ran into a problem..." just because an earlier turn
was slow or its release was lost. A stock question that names several codes answers every code.

## Prod evidence (1 Oct, contact 900000081, MYT)

- f0a2 ticket 1 done 10:05:35 -> 10:05:41.
- 65ce ticket 2 created 10:06:43 FAILED "QueueWait: waited 45.0s for ticket 1", 62 s after ticket 1 finished.
- a45f ticket 3 done 10:07:57 -> 10:11:49 (3m52s).
- a1d7 ticket 4 FAILED waiting for ticket 3; 4a87/b34b/9a2c ran 10:09:44+ while ticket 3 was still open.
- f0a2 "Srtswt3001 / Srtswt3001-gm stock" reply listed only SRTSWT3001-GM.

## Build

1. Holder key separate from `running`, so dead-predecessor detection works; the running turn heartbeats (TTL ~15 s).
2. Wait is heartbeat-aware: wait while the predecessor's heartbeat is fresh, proceed when stale, overall cap >= turn budget.
3. On queue timeout run the turn anyway (ordering best-effort); never send the generic error for a queue wait.
4. A timed-out ticket never advances `done` past a still-running predecessor.
5. Failed release logs at ERROR with contact + ticket; per-stage timing logged so a slow turn shows its stage.
6. A resolved code with no stock rows prints "No stock found for <CODE>".
7. The parser LLM call (every chatbot LLM call) has a hard deadline: 20 s per attempt, 35 s total.
8. Queue wait capped at 90 - 35 - 15 - 10 = 30 s; a late turn merges its session state.

## UAC

See `chatbot-queue-fix-1oct-acceptance-criteria.md`.
