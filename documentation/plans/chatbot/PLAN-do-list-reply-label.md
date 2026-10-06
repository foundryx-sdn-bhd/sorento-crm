# PLAN - DO-LIST-REPLY-LABEL: a quick-reply label reply answers the pending detail offer, never re-runs the summary

Status: small fix track (diff well under 300 lines, no migration, no auth change, no new ingest surface). Lane started 6 Oct 2026 from origin/main 198db40e. Build in progress.
Issue: crew lane DO-LIST-REPLY-LABEL (owner 6 Oct 22:55, prod, "quite frustrating"). Related: STUCK-QTY-LOOP #1471 (stale pending question replay), ROGUE #1506 class A (bare word under a pending question).
UAC: `do-list-reply-label-acceptance-criteria.md`
Classification: chatbot engine, `app/services/chatbot/turn/decide.py` (pure). No wording change, no prompt change.

## Symptom (prod)

A dealer asks about outstanding deliveries. The bot answers the "Delivery order outstanding"
summary and ends with "Reply 1 for the delivery order list." plus a quick reply labelled
"Delivery order list". The dealer taps the quick reply; respond.io sends the TEXT
"Delivery order list". The bot re-sends the SAME summary instead of the list. "1" works.

Prod trace (contact 551644114, turn 11): the session's open question is
`outstanding_detail`, expects `pick`, one option `{position 1, label "Delivery order
list", payload {value: "do"}}`, filters `{scope: "do", ...}`.

## Cause (file:line, measured against the code, 6 Oct 2026)

The parser is TOLD the option's label: `turn/question.py::_options` (line 44) states the
pending to the parser as `Open question: {"kind":"pick_one","options":[{"position":1,
"code":"Delivery order list"}],"owed":["pick"]}` (an option without a `code` key uses its
label as the code), and `head/parser.py:774` also prints `Open question options:` with the
same labels. The prompt rule "A CODE OR NAME FROM THE LIST IS ITS POSITION" and the worked
example `options "1. Sales order list, 2. Delivery order list" + "DO list" ->
reference_positions [2]` (`chatbot_parser_prompt.py` line 98) make the parser return
`reference_positions: [1]` / `open_question_answer.picked: [1]` for the label. So the
prompt context is NOT the gap.

The same prompt also says (line 593): `"delivery order", "SO", "DO" fill "document" ...
even when the document word is the WHOLE message`. So the label reply arrives as BOTH a
pick of position 1 AND `document: ["DO"]`.

The engine then throws the pick away: `turn/decide.py:664-677`, the outstanding kinds'
named-document arm, reads `_named_scope(verdict)` (the `document` field) BEFORE the picked
position and returns `NEW_ASK "named_document"` with `scope: "do"`.
`turn/apply.py:643-676` (`_answer_outstanding`) then re-runs the REPORT for the named scope
with `detail: None` (line 673: a named scope is "a new scope, not a pick off the offer"),
which is the very summary already on screen. The rule was written for "Sales order" typed
right after the DO detail list (decide.py:665-669: `document: ["SO"]` and
`reference_positions: [1]` on one verdict, and the position must NOT win there because the
only option on offer was "Delivery order list").

## Fix (engine seam, parser decides)

In `decide()`'s outstanding-kinds arm: when the parser declared a pick AND the document it
named is the SAME scope as the option it picked (`_picked_scope(pending, positions) ==
_named_scope(verdict)`), the message is the pick (`ANSWER "picked_scope"`), not a new
document. The "Sales order" case keeps its reading: the named scope ("so") differs from the
picked option's ("do"), so the named-document arm still wins. No word is read; both halves
are the parser's own fields (`reference_positions`, `document`) against the pending's own
stored option payload.

`_answer_outstanding` is untouched: an `ANSWER picked_scope` over `outstanding_detail`
already runs the list (`detail: "do"`) and keeps the offer open (contract 39).

## Reproduction (console check harness)

Cloud has no parser key. The YAML case `tests/chatbot/console_cases/2026-10-06-do-list-
reply-label.yaml` (turn A the outstanding ask, turn B "Delivery order list", turn C "1")
is posted on the PR as a `crew-handtest` for the crew to run live on the Mac and capture
`open_question_answer` for B and C. The pytest replay below pins the verdict shape the
prompt's own worked example prescribes (positions [1] + document ["DO"]) and the engine's
reading of it.

## Tests

- `tests/chatbot/test_do_list_reply_label.py`: red-first.
  - decide(): the label verdict (positions [1], document ["DO"], open_question_answer pick
    [1]) over the prod-shaped `outstanding_detail` pending reads `ANSWER picked_scope`,
    scope "do", exactly as the bare "1" verdict does.
  - decide(): "Sales order" after the DO list (document ["SO"], positions [1]) still reads
    `NEW_ASK named_document` scope "so" (the guard the fix must not break).
  - apply(): the label verdict yields `trace.outstanding == {kind: outstanding_detail,
    scope: do, detail: do}` and the offer stays open, identical to the "1" turn; the
    summary (`detail: None`) is never re-run.
  - Both-scope offer (two options): "Delivery order list" (positions [2], document ["DO"])
    picks position 2's list.

## Owner question (NOT built here)

The summary's "By product" section printed 300+ lines in one message (hundreds of 1-unit
rows). The block is rendered by `sorento_crm_mcp/sorento_crm_mcp/presenters.py:2067`
(`_outstanding_group`, one line per row, "in the order given"); the route already ranks the
rows outstanding desc, total desc, name asc (R10), so the top of the list is already the
part worth reading. Proposal for the owner to rule on: cap the "By product" (and "By
customer") block at the top N rows (N = 20, say) and close the block with one trailer line,
"... and K more products (reply 1 for the full list)", keeping the counts and the "By
location" block whole. An alternative is a hard line cap on the whole message with the same
trailer. Nothing is built until ruled; the detail list is unchanged either way.
