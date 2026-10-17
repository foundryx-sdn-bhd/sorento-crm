# CHAT-COMPOSER acceptance criteria (UAC)

Card: `chat-composer-behaviour-card.md` (rules R1-R13, crew answers Q1-Q4 = (a), 17 Oct). Plan:
`PLAN-chat-composer-17oct.md`. "Gate on" = `CHAT_COMPOSER_ENABLED=true`; "gate off" = default.
Each AC names the card rule or change it proves. S0/S1 are detailed; later slices are detailed when they start.

## Every slice

- AC-G1 Gate off: every chatbot reply is byte-identical to main. Proof: L1 replay main vs lane, grounding and turn
  tiers, 0 unintended diffs.
- AC-G2 Gate on: the replay's changed replies are exactly the slice's listed changes (diff list reviewed per slice).
- AC-G3 No composer output contains " · ", a UUID, an account count ("(N accounts)", "and N more" after a customer
  name) or a brand / size count line (R1, R2, R3, R5).
- AC-G4 A composer is a pure function of facts: it takes no customer message and reads no parser text (brief rule).
- AC-G5 Every sentence a composer prints in English has its ms and zh catalogue entry (no English fallback).

## S0 scaffolding (no reply change)

- AC-0.1 `Settings.chat_composer_enabled` defaults to False; env `CHAT_COMPOSER_ENABLED=true` sets it True; the
  composers read it at call time.
- AC-0.2 Shared line rule: a fact prints as `*Label:* value`, one per line (R1); the value printer and timestamp
  printer are the ones the lane uses today, moved, byte for byte.
- AC-0.3 Composer modules import nothing from engine / head / tail / dialogue / turn.compose / lanes.

Helpers land in the slice that first prints them (lead decision 17 Oct, seam design): dates dd/mm/yyyy (S2),
`Reply{text, question, offer}` and `Reply "more" to see the next N.` (S3, Q4), money `MYR 1,234.00` and
`No <attr> on record for <product>.` with un-asked empty fields left out (S6, Q3).

## S1 stock (gate on)

- AC-1.1 Dealer availability lines keep today's words and marks: `CODE x Q: ✅ Got stock.`, `✅ Only N got stock.`,
  `❌ No stock, ETA dd/mm/yyyy.`, `❌ No stock and no incoming. Please refer to your salesman.`, the too-big line,
  the discontinued flag line and the `Alternative:` line (R6; TRAIN cp0-7b70831ab3, cp0-2ec090127f, cp0-b5eb30e1b9).
- AC-1.2 Staff compact and detailed stock blocks keep today's layout and bytes (TRAIN cp0-2e2ebf11cb,
  cp0-37456a72da). (Q3's empty-line rule reaches these blocks when the main row printer moves, with S6 or the flip.)
- AC-1.3 A stock miss on the dealer path reads the same on every path: `Couldn't find X.` (several: `Couldn't find X,
  Y.`), and with candidates `Couldn't find X. Did you mean:` + one numbered real name per line (card s.4 stock (a);
  today three wordings, TRAIN cp0-0498229323).
- AC-1.4 WITHDRAWN (card s.4 stock (b)): the quantity question keeps today's text in both gate states, including the
  owner-ruled `N. CODE - ` fill-in lines (round 6, ruling 2, `turn/task.py:394-397`). The bare-vs-named question moves
  to S3.
- AC-1.5 Stock rows shown under another answer (an ETA ask with no incoming) use the same numbered stock block as a
  stock answer, not the "- *Product Code:*" variant (card s.4 stock (c); TRAIN cp0-5430419641, cp0-338d26fba4).
- AC-1.6 Refer marking, escalation-offer suppression and the open quantity question behave as on the gate-off path
  for the same facts (they read the structured flags).
- AC-1.8 (crew OI-1 (a), 17 Oct) When the dealer stock mix is rebuilt (a miss or a question in the reply), each
  answered product prints its full block, exactly as the no-miss reply does: its line, then `⚠️  *(PRODUCT
  DISCONTINUED)*` when discontinued, then one `Alternative: <alternative line>` per alternative (today
  `engine._stock_answer_lines` keeps titles only and drops both; TRAIN cp0-0498229323 PREV vs BOT).
- AC-1.7 ms and zh: every S1 sentence translates as it does gate off; new sentences have catalogue entries (AC-G5).

## Later slices (outline; detailed when each starts)

- S2 ETA: gate on, every date-only value on a field line prints dd/mm/yyyy, every tool (R13; TRAIN cp0-0654e3e74a,
  cp0-b90acd1184); one "Container" label and one "ETA" label (crew OI-4); an ETA miss (and its stock twin) says the absence once (TRAIN cp0-5430419641);
  incoming rows under a stock miss use the numbered block; dealer `CODE: ✅ ETA d` line and the sibling-variant picker
  unchanged.
- S3 not found / clarify / hand-over: one miss grammar; one "more" line; no internal names (TRAIN cp0-75e516bd40); no
  needless question left open after a full exact answer (R12; TRAIN cp0-b1f73c72c0,
  cp0-958b275668), but the numbered sibling-variant picker after a full answer to an exact code stays, unchanged
  (owner ruling 17 Oct, `answer.py:4846-4848`);
  access-level question only after a spec-matching product is found (R10).
- S4 files + promotions: one "attached" sentence, one dedupe; one missing-file sentence; "EXPIRED" on every path, kept
  after re-sorting (R7); dates dd/mm/yyyy (TRAIN cp0-b90acd1184).
- S5 order status: every customer header is the group name (R3); one period reply.
- S6 product info + price: `No <attr> on record for <product>.` per product; price answer = code, description,
  list price; un-asked empty fields left out (TRAIN cp0-245d689a0c); fit line unchanged (R8).
- Sets (after #1744 merges): a set reuses the single-code block, capped at 50 with the "more" line, never a reply
  without products (R2, R9; TRAIN cp0-665ba3e897, cp0-75e516bd40). OWNER RULING 17 Oct ~15:15 (supersedes 26 Sep
  "no paging"): every capped list ends with ONE `Reply "more" to see the next N.` line and "more" pages on to the
  next N, through the parser's continuation flag (no word rule); the continuation is wired once #1747 / #1755 merge.
