# PLAN: answering the bot's attachment-type question keeps the product it asked about (CARRY-ATTACH)

Status: in progress; track: small fix (backend only, under 300 changed lines, no migration, no auth/RBAC change, no new ingest surface)

## Report

Prod, 13 Oct 2026, parser v52, contact = internal staff.

- T1 "Srt8308 drawing": file sent (OK).
- T2 "Srt8313": reply asked for the attachment type although the fetch found SRT8313.jpg.
- T3 "Drawing" (answering that question): the carried product Srt8313 was dropped, the
  resolver saw only "technical drawing", the reply said no Technical Specifications matched
  and offered escalation. BUG.

Scope: fix T3. T2 waits on an owner ruling (send with "same as before" vs keep asking).

## Root cause

(filled in as the trace is verified)
