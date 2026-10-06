# UAC - Chatbot: re-attach the production answer half

Plan: `PLAN-chatbot-answer-half-reattach.md`. Status: APPROVED by the owner 20 Sep 2026 (lavish), with the roster cap ruling (AC-1710, AC-1711).
Each AC names how it is verified: [pytest], [journey] (a chain under
`tests/chatbot/journeys/`, run live by `scripts/chatbot_journey.py` on the lane stack), or
[hand pass] (owner on :3081). "Production copy" = the text the production composer returns today;
a journey asserts it verbatim from the owner's pasted production replies (hand passes 7 and 8).

## Structure (general to every business domain)

- AC-1680 [pytest] No production copy string exists twice. A source scan of `turn/` and
  `turn_runtime.py` finds none of: "Which product do you mean?", "Which customer do you mean?",
  "Which price tier applies to you?", "Did you mean", "No matching results found",
  "Would you like me to escalate to", "search needs to be more specific". Parametrized over the
  list, so a new duplicate fails by name.
- AC-1681 [pytest] `resolve_gate.run` is called exactly once per fetched domain per turn, and
  `resolve_kinds` returns its payload (`resolved`, `gate`, `aggregate`, `tier_gate`, `_exit_kind`)
  beside today's seven values. Parametrized over every seeded business domain.
- AC-1682 [pytest] The tool runner receives the resolver's REAL gate; the synthetic
  `{"compatible_entities": ...}` gate is gone.
- AC-1683 [pytest] A single-domain business turn's reply text is produced by
  `tail/outcome.escalate_catalog` + `tail/reply_ladder.compose_reply` over the production
  composers' fragments; `turn/compose.compose` is reached only for a multi-domain plan, a lane
  question (outstanding, sales report, forms) or a team pick. Parametrized over the four bridge arms.
- AC-1684 [pytest] Every question the bridge raises is a `Pending` minted by `pending.ask`, carries
  the production roster's options in order, and is written by `turn/tail.py` only (one tail, one
  session writer). A roster kind keeps contract 36: `answered_positions` layers onto it.
- AC-1685 [pytest] `test_s6c_answer_lane.py` and `test_replay.py` are green with no edit to either.

## Security

- AC-1686 [pytest] A contact without `sales_orders.sales_report` who sends a sales report ask whose
  customer token matches several customers reads the sales report refusal. No roster, no pending,
  no customer label in the reply, no resolver call. Parametrized over the fresh ask and the
  channel-worded ask. (Security review SF-1.)
- AC-1687 [pytest] The same contact answering "1" to a carried `sales_report_detail` offer, and on
  a carried `focus.status == "sales_report"`, reads the refusal with no fetch. (SF-2.)
- AC-1688 [pytest] A tool never runs unfiltered because its subject did not resolve: with a
  non-empty `unplaced` and an EMPTY entity list the fetch is a miss, and a cross-domain rung with
  no entities does not run. Parametrized over every domain that has a ladder. (Security N-1, F9.)
- AC-1689 [pytest] Company scope, hidden-spec projection, restricted-field drop and whole-domain
  grant refusals hold through the bridge: `test_engine_company_scope.py`,
  `test_spec_visibility_projection.py`, `test_last_cost_gate.py` green; a denied domain's reply is
  the refusal only.

## Narrowing follows production

- AC-1690 [pytest + journey] A product roster is asked only in the domains
  `gate.REQUIRE_SPECIFIC_DOMAINS` names. In every other domain a family token is a silent prefix
  filter. Journey: "delivery for srtwc286" answers at once with orders across the family (F1);
  "promo for srtwc286" never shows a product picker (F3).
- AC-1691 [pytest] No roster is ever asked with fewer than two options, in any domain and for any
  entity kind. Parametrized over every narrowing policy. (F8.)
- AC-1692 [pytest] A hyphenated or spaced code the resolver could not place is never offered back
  as an option: `narrow` and `turn_runtime` fold a token by the same rule. Cases: "SRTWT165-FT",
  "srtwt7202-new", "sttwc286-SH".
- AC-1693 [pytest] Before the `narrow` roster arms are deleted, every ask `narrow.decide` raises
  that production does not is listed in the PR with its fate: moved under a signed ruling
  (AC-1119, S19) or deleted.

- AC-1710 [pytest] Roster cap is data: `chatbot_entity_kinds.roster_cap` (default 10) cuts every
  roster of that kind (gate customer arm, gate product arm, did-you-mean list). Parametrized over
  the kinds and over caps 3 and 10; no literal 8 or 10 cuts a roster anywhere in
  `lanes/business/` or `turn/`. The migration seeds 10 on every existing row and the column reaches
  the config API response (asserted, `response_model` drops undeclared fields).
- AC-1711 [vitest + browser] The Chatbot entity kinds config screen edits the cap as a number
  field (min 2), saves, and the next turn's roster honours it. 375px and 1280px.

## Production copy, by finding

- AC-1694 [journey] F2 customer picker: "Which customer do you mean? Please choose:", each option
  with its company suffix "(MCH, SRT)" and a "has DO" / "no DO" stamp that honours the product in
  the same message ("delivery for srtwc286 chin chun": CHIN CHUN HOMEMART reads "no DO").
- AC-1695 [journey] F2 pick: answering "1" to that picker returns the scope block
  ("Customer: CHIN CHUN HARDWARE SDN BHD (MCH, SRT)" / "Product: srtwc286" / "Dates: all dates"),
  "Here are the orders I found." and the family's orders. Never "No matching results found".
- AC-1696 [journey] F1/F5 order hit: the scope block and "Here are the orders I found." open every
  order answer, with "all customers" / "all products" / "all dates" where unfiltered, and dates as
  dd/mm/yyyy.
- AC-1697 [journey] F3 access-level picker: "Which access level do you need for srtwc286?", one
  line per entitled tier stamped "has promotion" / "no promotion", and the closing line
  'Reply with the number(s), e.g. "1", "1 and 2", or "all".'
- AC-1698 [journey] F3 answers: "1" returns that tier's promotions with their files; "1 and 2" and
  "all" return each chosen tier's. Never a miss where production finds promotions.
- AC-1699 [journey] F4 order miss: scope block, "Here's what you want:" bullets, "But no order from
  01/10/2026 to 31/10/2026 matched these. Reply 'all dates' to search without the date filter, or
  would you like me to escalate to *Sorento* customer service team?", then the customer service
  member picker and "If you have no preference, just reply 'yes' and we'll assign automatically."
- AC-1700 [journey] F4 answers: a position assigns that member, "yes" assigns automatically,
  "all dates" re-runs the search without the window.
- AC-1701 [journey] F6 attachment roster: "product_attachment search needs to be more specific.
  Multiple matches found. Please choose:" with each option stamped "has Product Photos" /
  "no Product Photos", scoped to the attachment type asked.
- AC-1702 [journey] F7 attachment miss: "Here's what you want:" with the product and
  attachment_type bullets, "But no Product Photos matched these. Would you like me to escalate to
  marketing product team?"
- AC-1703 [journey] F8 did-you-mean: 'Couldn't find "SRTWT165-FT" (product). Did you mean:' with
  stamped neighbours and "Reply with a code to continue, or would you like me to escalate to
  purchasing certification team?". Same shape for "Incoming srtwt7202-new" and
  "Technical drawings sttwc286-SH".
- AC-1704 [journey] F8 answers: replying with one of the offered codes (or its position) continues
  the original ask for that code.
- AC-1705 [journey] F9 incoming miss: "Here's what you want:" + "But no incoming matched these." +
  "But here are the stock details for the requested products:" for THAT product only + the ladder
  sentence + the purchasing team offer. No other product code appears in the reply.
  STOCK-MISS-RENDER (crew ruling (a), 6 Oct 2026, prod turn #f763): the stock rung applies ONLY
  when the incoming tool actually ran and came back empty. On a resolver not_found exit (or any
  fetch-step refusal) no incoming lookup ran, so "No incoming for X" would be an unmeasured claim:
  the reply is the miss text alone, no rung (`test_rearch_r4_miss_engine.py::TestAC1705...`).

## Regression and gate

- AC-1706 [journey] Every chain already green on 484c79d92 stays green: hand pass 6 and 7 chains,
  `sales-report.json` 5/5, the smoke chain. Any chain that pinned the rearch-only grammar is
  re-pinned to production copy and listed.
- AC-1707 [pytest] `test_turn_replay.py` green; every re-recorded `prod_sample` fixture has a signed
  `replay_turns/DIVERGENCES.md` entry naming the production string it now carries.
- AC-1708 [journey] A multi-domain ask ("stock and incoming for cb2805q") answers as it does on
  484c79d92: one section per domain, each under its own production intro.
- AC-1709 [hand pass] Owner re-pass on http://localhost:3081 against the hand pass 8 list: every
  row reads as production does.
