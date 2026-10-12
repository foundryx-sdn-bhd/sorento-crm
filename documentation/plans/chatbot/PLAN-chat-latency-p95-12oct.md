# PLAN: chatbot reply latency p95 <= 8 s (lane CHAT-SPEED)

Status: Step 1 (measure) in progress. Track: small fix track (no migration, no auth change).

## Goal

Chatbot reply latency p95 <= 8 s (option D gate, ARCH page
`documentation/plans/chatbot/ARCH-scalable-chat-11oct.html`, PR #1566). Baseline main p95 8.36 s,
PR #1582 run 10.0 s. The prompt diet (#1568) trimmed only about 7 percent.

## Step 1: measure (no code)

Per-turn timing breakdown (parser LLM call, grounding/resolver incl. embedding lookups, tool
calls, DB queries, presenter, any second LLM call). Table posted as a crew-note on the PR.

## Step 2: fixes that keep answers identical

Each fix red-first where testable, with before/after timing. No regex/keyword rules on user text.
