# PLAN: chat fix PR-B, report engine (lane CHAT-ADVISOR-SPIKE, chat-fix LEAD)

Status: Review READY (reviewer + security-reviewer, 16 Oct), owner hand test + ci owed; Track L; red tests 913265d3a1; S0 baseline on main 12/25 right, 5/25 right with at most
one question. Track: L (expected diff over 300 lines; no migration, no auth/RBAC change, no new
ingest). Branch `crew/chat-fix-reports` off origin/main ccb1ce9820, worktree `sorento_crm-chat-fix-reports` (the PR-A
worktree stays on PR-A: the crew hand-test copy :8109/:3109 runs from it).

## Goal

Owner problem statement 16 Oct: "the report engine, it is not very smart and dynamic". Measured: 7 of 25 real report
asks right with at most one question; 7 right only after 2 or 3 questions; 11 failed; follow-ups 2 of 8 right
(ARCH-advisor-16oct section 3, console sessions 3-14 Oct). Fix inside the existing engine, no new model call, no word
rule over the user's message, and the parser input stays byte-identical to main / prompt v94 (PR-A lesson: a parser
input change gets its own PR behind the parser-gold gate).

Owner rulings kept: 4 Oct, no default period / count / metric / basis, all four are asked (`report_ask.py:9`);
16 Oct, "ask all that is needed at once in a structured way"; 8 Oct, a new axis after a ranking is asked, never
guessed (`report_ask.py:604-606`); the parser decides follow-up vs new, the engine applies it.

## Facts (origin/main ccb1ce9820, paths under `sorento_crm_backend/app/services/chatbot/` unless shown)

- Four required fields PERIOD, TOP_N, METRIC, BASIS (`lanes/business/report_ask.py:225-243`), collected one per
  turn: `required_fields.py:310-318` returns on the first missing field.
- Answers already come from the parser's fields (`report_ask.py:180-213`, no word reading), but an answering turn
  takes only the asked field plus metric/basis (`_reply_choices` :552-561); a period or count given early in the
  same reply is dropped and asked again (#8 "2026, top 10, by quantity delivered").
- `answer_drops=("sales_channel", "report_ask_words")` (:242) does not hold the report type, so an answer that
  says "delivered" is re-routed by `turn_runtime.py:1894-1897` (`_DOCUMENT_STATUS_TO_ORDER_STATUS`) and returns the
  delivery-order list (#23).
- Precedent for two questions in one reply read from one answer: `lanes/business/chat_report.py:399-442`
  (`_both_questions`, `_answer`, partial answer is not a miss).
- Follow-ups: the parser's `ranking_refine` (`head/parser.py:292`) is applied only when the held frame is a
  sales ranking: the frame is carried only for `order_status == sales_ranking` (`turn_runtime.py:1950-1956`),
  written at `engine.py:7028-7033`, dispatched on status at `lanes/business/__init__.py:2183`; the multi-ask path
  never stores a frame (`turn/sub_asks.py`, no frame reference); the frame builder is
  `lanes/business/fetch.py:2772-2794`.
- New axis: `report_ask.py:604-632` turns the refine off and runs a fresh ask, so the held period and product are
  dropped and asked again (#22).
- Rate limit 10 per 10 minutes counts every call, refines included (`app/api/v1/order_management/report_ask.py:46-47`,
  `:95`, hit at `:342-345`) (#13).
- Channel names: the data stores the Dealer channel as "retail" (`app/services/reports/ask.py:186`) while the
  parser's `sales_channel` enum is dealer | project (`head/parser.py:257`) (#20). Parser schema = parser input:
  PR-B2.
- Customer stamp picker: `turn_runtime.py:3104` `stamp_customer_ask` asks which account even when every account
  carries the same stamp (W02 t2, "no DO" on all 4).
- Dealer brand on a sales total (#15, R15): the parser reads the brand (entity MOCHA, confident) but
  `crm_sales_report` carries no brand, though `sales_report_service.py:614,652` already filters by `brand_ids`.
- "Sorento or Mocha?" is a COMPANY question: `app/api/v1/sales/analysis.py:102,292` returns
  `{"status": "clarify"}` and the MCP presenter sends it as a plain reply (`sorento_crm_mcp/presenters.py:3481`),
  so no open question is registered and the next "sorento" is not read as its answer (R24 t6 ran an unrelated
  sales report).
- Sales report with no subject (R03, R23): `crm_sales_report` is sent with no product or customer, the route answers
  HTTP 422 `subject_required`, and the reply is "I could not fetch sales just now".
- An answer to the top selling basis question that the parser reads with another `order_status` (R12 "delivered" ->
  sales_ranking; R17 "已送货" -> sales_ranking/category) leaves the top selling ask: a customer picker or "找不到
  马桶". A new ask while a question is open (R22 "i want to see the top customer", parser top_selling, no basis) is
  read as a failed answer: "I don't know 'i want to see the top customer' as a basis".

## Changes (each its own red-first slice)

0. S0 measurement (lead, scratch, not in the PR): the 25 chains as a report slice (masked, kept in crew/state/eval,
   never in the repo) + a replay runner on the dev DB, prompt pinned v94, history isolated (quiet staff contact).
   The runner feeds each chain's recorded user turns in order whatever the bot asked, and scores from the turn
   trace: final report params (period, top_n, metric, basis, axis, filters) vs the chain's expected params, and
   questions before data. Baseline on main, then on PR-B. Locates the three anchors above.
1. S1 P3-F1 one structured question: when more than one field is unsettled, one reply lists every missing field,
   numbered, each with its choices ("1. Period: this month / last month / this year  2. How many: 5 / 10 / 20
   3. Rank by: amount / quantity  4. Basis: delivered / ordered"; zh/ms through `label_catalog.py`). Any answering
   turn reads every field the parser set (dates, `top_n`, measure/rank_by, basis), not only the asked one; a
   partial answer re-asks only what is left; no regex over the reply (the existing `_COUNT_RE` is not copied).
   `required_fields.py` gets an opt-in "ask all" mode used by the report ask only; other asks keep one per turn.
   S1b the top selling ask (a separate path, `crm_top_selling_report`) asks metric then basis one per turn too
   (`lanes/business/__init__.py:511-529` `_top_selling_question` via `ranking_ask.next_question`; the answer is
   read only if it answers the one asked axis, `turn/apply._top_selling_waiting`): when both are unsettled it asks
   both in one numbered reply, and an answer settling either or both is read (S0 baseline R01/R02: "By quantity or
   by amount?" then "Delivered (transferred to DO) or ordered?").
2. S2 report type held on an answer: an answering turn keeps the report ask's `order_status` (add it to the
   answer's held keys) so "delivered" fills BASIS and never re-routes to the delivery-order list.
   S2b the same for the top selling ask: a turn whose parse fills the open field (basis / metric) is its answer
   whatever `order_status` the parser put on it (R12, R17).
   S2c a turn whose parse fills no open field and reads as a new report ask (another report type or axis) is a new
   ask, not a failed answer (R22). The test is structural: which open fields the parse filled. A reply naming
   another axis that fills only the period stays a new ask (review round 8, "customers last month"); one that
   fills the count, metric or basis is the answer and the slot's axis wins (R16 noise, see S2d).
   S2d DROPPED after measuring (built c408aee148, reverted): re-targeting the held ask to the parser's group_by on
   an answering turn. On 4 live runs, 14 answer turns carried a group_by different from the held axis and only 2
   were a real axis change (R25 "how about by brand instead?"); 12 were noise on bare answers ("top 5" ->
   sales_agent, "dealer only" -> customer, "delivered" -> category). The slot's axis wins on an answer (main's
   behaviour, now pinned by a guard test). R25 moves to PR-C: why the parser emits group_by on a bare "top 5" is a
   parser-input question.
3. S3 P3-F2 engine applies the parser's refine: with a report held and `ranking_refine` true, apply the parser's
   changed keys to the held report whatever report type the parser picked; carry the frame for every report ask,
   not only sales ranking (`turn_runtime.py:1950-1956`, `engine.py:7028-7033`, dispatch `__init__.py:2183`); the
   multi-ask stores its report frame like a single ask (`turn/sub_asks.py`).
4. S4 P3-F4 keep the period: a new axis is still asked (8 Oct rule) but the held period and product stay
   (`report_ask.py:616-632`).
5. S5 P3-F5 dealer brand: a sales total whose parse carries a brand passes it as `brand_ids` (route, MCP catalog,
   lane), never silently unfiltered (R15). S5b the response echoes the applied brand (`brand_name`, like the
   outstanding report) and the reply names it, so an empty brand-filtered total does not read as no sales at all.
6. S6 P3-F6 display: header "Top 10 of N" naming the sort measure first, no duplicate names, readable dates (no
   ISO), category name not code, month rows in date order.
7. S7 P3-F7 the sales analysis company question (`status: clarify`) is registered as an open question with the
   company names as options (the existing open-question roster path: labels in the per-turn parser context, the
   parser's `reference_positions`), so the next "sorento" answers it and re-runs the held analysis for that company
   (R20 t6, R24 t6). Per-turn context only while that question is open; the system prompt is unchanged.
8. S8 P3-F8 limit: NO CODE. A client-sent "do not count this call" flag would let any API-key caller skip the
   limit. The limit is already a setting (`report_ask.py` route `_rate_limit_setting` :95). Crew ruling 16 Oct:
   keep the default 10 asks per 10 minutes, the owner may raise it himself. With S1 an ask uses fewer calls anyway.
9. S9 NEW-2 a customer picker whose options all carry the same stamp answers directly instead of asking
   (`turn_runtime.py:3104-3140`).
10. S10 a sales report with no product and no customer subject is never sent to `crm_sales_report` (422): for a
   staff contact it runs as the sales analysis for the period (company totals, `crm_sales_analysis`); a
   customer-scoped contact keeps its links as today (R03, R23).

Out (named, with where it goes):
- P3-F3 parser conflicts (refine rule vs "sales total" rule, prompt v94 :245-246 vs :212) and channel names from
  data (`head/parser.py:257`): parser input change -> PR-B2, its own prompt version behind parser-gold TRAIN x2.
  Fixes #20 and the parser half of #24.
- #21, #25 follow-up context: PR-C (P1). R25 too (the parser's group_by on an answering turn, S2d).
- R23 (one case): a 6-question multi-ask message whose sales sub-ask, after two period questions, runs
  `crm_sales_report` with only the channel (its product subject lost; live turn 9981c08e, session holds only the
  open period question). Trigger to fix: a second multi-ask chain losing a sub-ask's subject.
- Found by the security review, pre-existing, not PR-B: `/api/v1/sales/analysis` refuses only contacts WITH
  customer links; a non-staff contact with no links holding `sales_orders.sales_report` (off by default) can get
  company totals. PR-B's own reroute is staff-only. Reported to crew for the owner.
- "How many dealers sell X" answered as a count (`reports/ask.py:291` has `total_count`): not in the fix table;
  trigger = a second real chain asking a count.

Overlap: PR #1572 (+30k lines, old report work) is not needed; owner KEEP/CLOSE table says close after PR-B.
Open PRs on the same files (checked 16 Oct 04:35): #1648 V1-DOC-SCOPE edits `required_fields.py` and
`tests/chatbot/test_report_ask_lane.py` (S1 touches both: keep S1's `required_fields.py` change additive, merge
main before the PR gate if #1648 lands first); #1707 (PV2 R5, draft) and #1660 edit `chat_report.py`, which PR-B
only reads as precedent. No open PR touches `lanes/business/report_ask.py`.

## Gates

- Red-first commit + kill test per slice (tester writes red tests from the UAC before the coder; one coder kept
  alive). No commit by the lead while a subagent works in the worktree.
- Unit suites `tests/chatbot/test_report_ask_lane.py` and `tests/chatbot/test_report_ask_clarify.py` green, plus
  every touched module's direct suite (`-n 2`, `SORENTO_ENV_FILE=.env.ci-tests`).
- Parser input parity: rendered parser input sha256 identical on main vs PR-B for the 25 chains' first asks (no LLM;
  the PR-A parity script).
- Report slice (S0) on PR-B vs main, same prompt v94, same DB, two scripts: the recorded user turns (main: 12/25
  right, 5/25 with at most one question) and a merged variant for 11 chains where the user's own pre-data answers
  are sent as one reply (the fair test of S1). No chain right on main goes wrong. Target: about 19-21 of 25 right
  (R20, R24 need PR-B2 for the parser half; R21, R25 are PR-C context cases). Runner + scorer + expectations:
  crew/state/eval/advisor-spike/report-slice/ (digest.py, expected.json).
- Walkthrough rows not written for (W01-W12): same or better.
- reviewer; security-reviewer not required (no auth/RBAC/ingest/upload/scoping change); if S8 touches the route,
  reviewer checks the limit still bounds fresh asks.
- Live calls: slice run ~25 chains x ~3 turns = ~75 parser calls (~40k tokens each, dev key), one run on main, one
  on PR-B, one final; estimate posted to crew before each run. Spend reported on the PR.
- Owner hand test before merge (console, quiet staff contact). Crew labels ci once at READY.

## Lane end

Drop `sorento_chatfix_ci` only when PR-A and PR-B no longer need it; plan + UAC move to `_archive/chatbot/` once
Status is terminal.
