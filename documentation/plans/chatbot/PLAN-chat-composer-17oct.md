# PLAN: CHAT-COMPOSER - one composer per answer type

**Status:** Build S0 (17 Oct). Card answered by crew (Q1-Q4 all (a)); UAC written; seam design S0/S1 in progress. Track: L, LEAD pattern (local lane).
**UAC:** `chat-composer-acceptance-criteria.md`. **Card:** `chat-composer-behaviour-card.md` (discovery, contract, rules R1-R13, per-type changes, slices, Q1-Q4).
**Lane:** CHAT-COMPOSER, branch `crew/chat-composer`, base origin/main 20c191d86. Step 3 of the 16 Oct chatbot scale
design (`crew/state/reports/CHATBOT-SCALE-DESIGN-16oct.html`, section 4 row F, section 8 step 3) and reports
`CHATBOT-SCALE-{A-ARCH,B-FAILURES,E-PATTERNS}-16oct.md` (159 formatters; replies built in many places; failure family F).
**Owner go:** 17 Oct ~14:10 ("Ok" to step 3 in parallel with steps 1-2).

## Goal

Each answer type gets ONE composer that turns resolved facts into the reply text, so wording and format rules live
in one place and every path obeys them. Answer types (brief): stock, ETA / incoming, price, spec / product info,
attachments / documents, order and document status, lists / sets, not found / clarify.

## Owner rules the composers encode (crew memory, cited in the card)

| # | Rule | Source |
|---|------|--------|
| O1 | One fact per line `Label: value`; no " · " | feedback-no-dot-separators (3 Oct) |
| O2 | Spec-set answers reuse the single-code format; no brand / size count lines | design D-notes 16 Oct, SET-STOCK-RENDER #1744 |
| O3 | Customer group: company / group name only, no account count | feedback-customer-group-name-only (2 Oct) |
| O4 | Truthful; a missing attribute is said plainly ("no weight on record for <product>") | feedback-truthful-no-fabricated-replies (12 Oct), project-cp0-owner-review-17oct |
| O5 | Never show internal ids, UUIDs or codes as candidates; real names | feedback-chatbot-never-leak-ids (13 Oct) |
| O6 | Availability is on-hand based; ETA = incoming stock, product level | feedback-availability-onhand-basis (6 Oct), CHAT-STATE ruling 16 Oct |
| O7 | Expired promo flyers still sent, marked EXPIRED, beside the live one | feedback-promo-expired-shown (15 Oct) |
| O8 | Fit line "Seat cover/Mirror for X is Y", then the usual answer | feedback-fit-line-wording (15 Oct) |
| O9 | A set reply that shows no product fails: capped product list + "reply to see more" | project-cp0-owner-review-17oct |
| O10 | Price-tier question first only once a spec-matching product is found | project-cp0-owner-review-17oct |
| O11 | No holding / progress messages | design D3 (16 Oct) |
| O12 | No needless "which one?" after a full answer (related-products picker left open = defect) | EVAL-BENCH TRAIN cp0-c7ec5f1c31 |

## Method (brief steps)

1. Discovery with file:line (formatters per answer type, duplicates, divergences, bypasses). Done: card section 1.
2. Behaviour card as ONE crew ask BEFORE code (answer-type list, composer contract, rules per type, 3-5 TRAIN
   examples per main type, edge cases, at most 5 questions with recommendations).
3. Plan + red-first slices behind a gate (gate off = byte-identical to main, proven by the $0 L1 replay vs main).
   One answer type per slice; each replaced formatter deleted; each slice proven by replay + tests + kill notes.
4. Measure on the EVAL-BENCH TRAIN bench only (crew runs held-out); one live envelope per PR run, about $5 cap each.

## Coordination

Open PRs on the same answer paths: CHAT-STATE #1747, CHAT-CLOSED-LISTS #1755, SET-STOCK-RENDER #1744 (parked; the
design names it the first composer). CHAT-CODE-LOOKUP runs in parallel. Their files are not rewritten here; overlap
goes to crew in a crew-note so crew can order merges.

## Slices (queue)

| Slice | Content | Status |
|---|---|---|
| S0 | gate `chat_composer_enabled`, composer package, `Reply`, shared format helpers, contract tests | next |
| S1 | stock (single + multi code; dealer and staff) | queued |
| S2 | ETA / incoming | queued |
| S3 | not found / clarify / hand-over | queued |
| S4 | files + promotions | queued |
| S5 | order / document status | queued |
| S6 | product info + price | queued |
| Sets | set / list wrapper, after #1744 merges (Q1) | waits #1744 |
| Flip | follow-up PR: gate removed, divergent formatters deleted (Q2) | after bench + owner hand test |

Each slice: tester red tests (test(red) commit, crew-note red-proof) -> coder green -> L1 replay gate-off 0 diffs +
gate-on diff list -> kill-proof. Reviewer once per lane (Phase 3).

## Open

- Composer contract, gate, slice order: card sections 2 and 6 (proposed; final after crew answers).
- L1 replay baseline (main 20c191d86 vs lane, docs only): grounding 0/1506, turn 0/585 unintended.
