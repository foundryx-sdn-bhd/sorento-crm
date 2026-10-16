# PLAN: CHAT-STATE - one explicit conversation state for follow-ups

**Status:** Plan (step 1 of 4: behaviour card being drafted; no code yet). Track: L, LEAD pattern.
**Lane:** CHAT-STATE, branch `crew/chat-state`. Step 1 of the 16 Oct chatbot scale design
(`crew/state/reports/CHATBOT-SCALE-DESIGN-16oct.html`, sections 2, 3, 4 rows B/C/G, 8 step 1).

## Goal

Follow-ups right ~62% today (first asks ~92%). Bar for this step: follow-up slice >= 80% (design step 1),
owner hand-test bar >= 90% on crew's held-out set over 3 runs, first asks not worse.

## Shape (from the brief, owner-approved design)

- ONE state record per contact conversation: subject (products / filters / customer / period / report
  shape), the bot's open question (what it asked + options offered), the last list shown (position ->
  item), a pending task.
- Always shown to the parser in full, compact fixed format, never squeezed or cut.
- Parser outputs CHANGES to it (keep / new subject / add or clear a filter / pick n / answer open
  question / switch topic); code applies them deterministically and logs state before/after each turn.
- No word rules in code; parser decides meaning, code executes. Closed candidate lists are step 2
  (not here). No new WhatsApp messages. Prompts as code: one chain in the repo.

## Steps

1. Behaviour card (carry/reset rules table citing owner rulings, 3-5 real examples, edge cases,
   <= 5 open questions) filed as ONE crew ask. -> `chat-state-behaviour-card.md`
2. UAC + this plan filled in, then red-first slices (tester -> coder), L1 replay on every slice,
   follow-up TRAIN slice measured via EVAL-BENCH.
3. Reviewer + security-reviewer (state store holds customer data; SEC-MCP-AUTH #1742 scope rules).
4. No owner hand test from this lane alone.

## Code map (filled from the A-ARCH digest)

TBD

## Slices

TBD after the card is answered.
