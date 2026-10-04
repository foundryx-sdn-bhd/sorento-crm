# PLAN: chatbot empty "choose who to route to" list + phantom open_question_answer pick

Status: PR #1391 open, small fix track (no migration, no auth change, no new ingest surface)
Lane: CHATBOT-EMPTY-ROUTE-PICK, branch `claude/chatbot-empty-route-pick-x5e9ad`, base `origin/main`
UAC: `chatbot-empty-route-pick-acceptance-criteria.md` (alongside)

## Owner repro (30 Sep 2026, Chatbot Console, contact CONTACT Z, prompt v37)

User: "Zhin heng delivered on 23/9". Bot:

```
Customer: Zhin heng / Product: all products / Dates: 23/09/2026

Please choose who to route to (reply with the number):

If you have no preference, just reply 'yes' and we'll assign automatically.
No orders matched these.
```

Parser: `business_query` / `check_order` / document `["DO"]` / `delivered` / date 2026-09-23,
entity `{raw "Zhin heng", hint customer, canonical_code null}`, routing
`customer_service` / `order_enquiries`, and `open_question_answer {"mode":"pick","items":[],
"picked":[1]}` while no open question was pending.

## Findings (file:line at the base commit 950785de, unless said otherwise)

1. **Where the open question lives.** `State.pending` (`turn/state.py:207`, a `turn/pending.py`
   `Pending`) is the open pick or offer; the stock question is the `stock_qty` task on
   `State.focus.tasks` (`turn/task.py:1029`). `turn/question.py:96` `open_question(pending,
   tasks)` folds both into the ONE `Open question:` object the parser is shown
   (`engine.py:3318`, rendered at `:3339`). None means no line went to the parser.
2. **The phantom pick.** Nothing dropped a declared answer when that object was None. The two
   answer readers guard on `state.pending` (`turn/apply.py:2657`, `:2776`), but a pick also
   rides as `reference_positions`, and `lanes/business/gate.py:955-965` reads a non-empty list
   as "a pick was already applied" and skips "Which customer do you mean?" (`:1007`). That is
   how an ambiguous "Zhin heng" fell through to a fetch it could not scope and "No orders
   matched these." (item 3 of the brief). Fix: `turn/question.without_phantom_answer`, called
   at `engine.py` right after the parse, before the `understood` record; `phantom_answer`
   trace event.
3. **The empty picker.** The candidate list was NOT empty when it rendered. The miss composer
   offered escalation (`lanes/business/answer.py:2681`, the offer sentence rides on the "But no
   order matched these." line) and the CS member picker (`tail/member_offer.py:297-301`); then
   R6 (`order_list.py:187` `list_reply`, "inside an order list no escalate offer and no
   routing picker") took the question out and `_without_options` (`:217`) removed only the
   numbered rows, while `_one_line_miss` (`:235`) dropped the "But no ..." line. What stayed
   was the header, the "reply 'yes'" close and a blank where the offer sentence had been.
   Fix: `_without_picker` strips the whole frame by the printing sites' own strings
   (`tail/member_offer.py` `PICKER_HEADER` / `PICKER_CLOSE` / `ROSTER_HEADER` /
   `ROSTER_CLOSE`), and `answer_bridge._miss_company_picker` returns no picker with zero
   options.
4. **Prompt version.** Every parser prompt migration publishes a NEW unlabelled version and
   leaves the `production` label where it is (e.g. `alembic/versions/sa2_r9_open_question.py`,
   `chatbot_self_reference_vocab.py`); the label moves only by hand on the Prompts page. The
   "mode null when there is no Open question line" rule was published by
   `sa2_r9_open_question` (PR #1247 round 9, 27 Sep); a production version older than that
   has the `open_question_answer` key forced by the strict schema (`head/parser.py:394`) with
   no rule about it. Whether v37 predates it is a DB fact (see the PR's crew-done comment for
   the SQL); the code guard holds either way.

## Slices

1. Regression tests (red first) for the phantom pick drop and the empty picker.
2. Engine: drop `open_question_answer` and `reference_positions` when the turn has no open
   question (`turn/question.open_question(pending, tasks)` is None).
3. Order list R6 reply: when the routing picker's options are taken out, take the picker's
   header and its "reply 'yes'" close out with them; never render a picker with zero rows.
4. Report on the unresolved-customer path and the prompt version question.
