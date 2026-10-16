# UAC: chat-sparse-emission (lane CHAT-SPARSE), rescoped

Plan: `PLAN-chat-sparse-emission-12oct.md`. Rescoped by crew/owner ruling after the round 3 eval: the
output-token work is reverted; this lane ships the out_of_scope never-blank fix.

- **AC-1** An `out_of_scope` hand-over persists the lane's own customer message as the turn's reply text,
  never an empty or null reply, including a request for help with no entities and when the
  escalation preview finds no team.
- **AC-2** The customer is sent exactly one message on that turn, the same text as before the fix. One exception, on a voice or image turn: the "I heard ..." / "I read ..." line now leads the sent message too (AC-1817), so the sent message and the stored reply match. Before the fix only the stored reply carried it.
- **AC-3** The clarify arm's question reaches the persisted reply (the former strict xfail is green).
- **AC-4** No change to the parser, its prompt, its schema or any prompt version; no migration.
