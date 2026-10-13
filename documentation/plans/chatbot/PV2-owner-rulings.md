# PV2 owner rulings carried from v1 chatbot code (PV2-CARRY)

Status: in progress (PV2-CARRY lane, 13-14 Oct). Contract: `CONTRACT-PV2-13oct.md` section 7.

Every owner-ruling reference in `sorento_crm_backend/app/services/chatbot/**` (plus `ideation_capture*.py` and `reports/**` where they mention chatbot rulings) is listed below with
`file:line`, the ruling, and how PV2 preserves it (or "v1 only" when PV2 tonight never reaches that path).
None is dropped.

## Summary

Total references listed: **740** (417 in the main tables, 323 in Appendix A). Every file:line below is a distinct source line; near-duplicate lines at one site share a row (the line column lists all of them). Nothing is marked dropped.

Main tables (lines matched by the owner-ruling patterns) and Appendix A (other owner-attributed lines: hand tests, retests, Qn answers, owner-approved rounds, plus a few false matches where `owner` is a variable name or a record owner) by PV2 handling:

| handling | main | appendix A | total |
|---|---|---|---|
| `bridge` | 198 | 133 | 331 |
| `apply_turn` | 113 | 101 | 214 |
| `clarify_copy` | 15 | 6 | 21 |
| `reports` | 35 | 24 | 59 |
| `ideas` | 4 | 2 | 6 |
| `resolve` | 22 | 13 | 35 |
| `parser v1 only` | 22 | 8 | 30 |
| `v1 only` | 8 | 36 | 44 |
| **total** | **417** | **323** | **740** |

## How the list was built

Run from `sorento_crm_backend/app/services`, over `chatbot/` (excluding the new `chatbot/pv2/`), `ideation_capture*.py` and `reports/`:

```bash
P='owner (ruling|rule|call|decision|said|asked)|owner:|owner \(|per owner|owner [0-9]|ruling|rule \(|[0-9]+ (Sep|Oct|Aug) rule'
grep -rniE "$P" chatbot ideation_capture*.py reports          # 409 lines: the main tables (plus 8 adjacent lines at the same sites)
grep -rniE "owner" chatbot ideation_capture*.py reports | grep -viE "$P"   # 471 more; read all, the real owner feedback is Appendix A
```

The second grep (471 lines) catches rulings phrased as "owner hand test", "owner retest", "owner Q4 (b)", "owner-approved", "owner console", "owner report", "owner finding". They are owner decisions recorded where the fix landed, so they are listed too. About 150 of the 471 are not rulings (a variable named `owner`, "the owner switches a lane on", record ownership) and a handful of those false hits are listed anyway, flagged `Not a ruling`, so no file:line a grep hits is silently skipped. Appendix A lists 323 of the 471 lines (the rest are variable names, lane switchboard comments and the like). Only lines under `chatbot/` count towards the "chatbot code" figure in the contract (393 of 409 for the first grep; 15 are in `reports/`, 1 in `ideation_capture_service.py`).

## Handling key

| handling | meaning |
|---|---|
| `bridge` | PV2 lookups reach this code through `pv2/bridge.py to_v1_verdict`, so the rule holds unchanged. Engine stages after parse: routing, fetch, compose, formatters, tail, lanes, answer paths. |
| `apply_turn` | The rule is about carrying, clarifying, held subject, short replies, pending question or topic switch. PV2 enforces it in `pv2/apply.py` (how is said in the row). Note the v1 `turn/apply.py` still runs on the bridged verdict, so these rules also hold there; `apply_turn` must feed it a verdict that lets it do so. |
| `clarify_copy` | Wording of clarify/options, no codes or ids (`pv2/clarify_copy.py`). |
| `reports` | Report parse and ask rulings, handled by PV2-REPORTS (`pv2/report_policy.py` mirrors them, for example the 4 Oct "ranking never defaults metric or basis: it asks"). |
| `ideas` | Ideation dialogue (PV2-IDEAS). |
| `resolve` | Matching and resolution (PV2-RESOLVE). PV2 forbids fuzzy/ILIKE, so a v1 fuzzy rule is "v1 only" for resolution; customer-name candidates are kept as options only. |
| `parser v1 only` | Lives in the v1 parser prompt, `head/parser.py` or its post-processing; PV2 replaces it (the model returns index keys, code validates); the row says whether behaviour is preserved. |
| `v1 only` | A path PV2 never reaches tonight (the flag off keeps it), or a false match. |

## Rulings `apply_turn` must implement specifically

These are the v1 carry, short-reply and pending rules that nothing downstream of the bridge can restore if `apply_turn` gets them wrong. Each is also in the tables below.

1. **A non-answer never closes the open question.** `turn/apply.py:1001`, `3593`, `2014`, `2018`, `3840`; `turn/decide.py:23`. `small_talk` leaves held AND pending exactly as they were, emits no Lookup; an aside that does not answer keeps the pending (a miss counts only for an `answers_bot_question` that fails). A domain switch (`new_topic`) does clear it (`apply.py:3840`, "1" under an old list must not escalate).
2. **Answer = exact printed option key, nothing else.** `engine.py:401`, `904`; `turn/decide.py:142`, `240`, `778`; `required_fields.py:43`, `336`; `head/parser.py:915`; `turn/question.py:181`. No pending or a key not among the options is a MISS with the real options re-asked, never idle chat and never a guess; only a declared answer is an answer, anything else is a new question that wins. A pick replaces the value on its own axis (no merge).
3. **New ask vs refinement table.** `turn/decide.py:521`; `turn/apply.py:1112`, `1146`, `1136`. `new_topic`: held becomes this ask only, so every carried kind it did not name goes, including period, attachment_type, specs, report, tier and extra. `refines_held`: combines across kinds and replaces only within the kind named (op `set`); it never evicts other kinds (`apply.py:428`).
4. **Carry-attach (13 Oct, prod).** `turn_runtime.py:4606`. "Srt8313" after "Srt8308 drawing": a new product (op `set`, `refers_to_held` false) on a refinement KEEPS the held attachment_type and domain and replaces the product in place; the "11 Oct carried + new subject -> which_held" check must not fire when the kind is replaced rather than added. Put this case in the replay tables.
5. **Settled refs are never re-asked.** `turn/narrow.py:470`, `533`, `522`, `528`. A held ResolvedRef is settled and is what Lookup fetches; on a domain change only re-ask when the new domain is single-subject and several refs are held.
6. **Detail offers survive their own pick.** `turn/apply.py:572`, `581`; `turn/pending.py:29` (tier pick persists across picks). `outstanding_detail`, `sales_report_detail` and tier pickers are `v1_open_question` pendings and must not be cleared by the pick (decide by `pending.v1_kind`); every other pick clears its pending.
7. **Single-option yes/no.** `turn/apply.py:746`, `3104`; `turn/decide.py:260`. "Did you mean X?" is yes/no over one option: yes applies it (typed quantity rides on), no refers to the salesman with nothing left open (dealer) or closes it.
8. **Second ambiguous token is queued, not dropped, not asked together.** `turn/apply.py:895`, `1812`; `turn/reconcile.py:73`. CONTRACT GAP: `PendingQuestion` is one slot. Queue the rest (for example in the first pending's payload) and ask the next after the first is answered.
9. **Picker is not sticky; a bare number after a spent pick is a quantity.** `turn/apply.py:2600`, `2605`, `3348`; `turn/task.py:876`, `308`; `engine.py:432`, `778`; `turn_runtime.py:3303`. After a pick clears the pending a lone number is never a `row_pick` into the old list unless the parser says `row_pick` and `last_rows` exists. CONTRACT GAP: `Entity`/`ResolvedRef` carry no quantity (`turn/state.py:31`, "the parser owns quantity"), so "SRTWC286 x 10" would lose its 10; stock quantity tasks pass through as `v1_open_question` tonight.
10. **Chained question: the current question is judged first.** `turn/apply.py:2629`. Earlier rosters stay available only as `last_rows` (cap 20), never as a second pending.
11. **Broaden vs family.** `turn/apply.py:1729`; `turn/decide.py:180`; `head/parser.py:126`. "All" on a named axis = op `clear` on that kind; "all variants" = replace the variant ref with its family ref. Both must be implemented, they are different.
12. **Ranking follow-ups.** `turn/apply.py:1390`, `1528`; `turn_runtime.py:3195`; `head/parser.py:263`, `288`, `292`; `lanes/business/ranking_ask.py:1`; `lanes/business/report_ask.py:9`, `610`; `turn/state.py:182`. A fresh ranking starts an empty ReportSpec and never borrows customer, channel or period; "by amount" edits only the axis named; metric and basis are asked, never defaulted (4 Oct, together in one question); a new group_by on a held ranking with no `row_pick` asks scoped vs global (never guessed).
13. **Self-reference.** `lanes/business/__init__.py:995`, `2313`; `lanes/business/fetch.py:641`. "My / me / our" means all the asker's accounts and a carried customer must not narrow it. CONTRACT GAP: the schema has no self-reference signal (needs an index entity kind or flag).
14. **Cleared period vs absent.** `lanes/business/__init__.py:1005`. `cleared=['period']` (the customer said "all dates") must reach the verdict as `broaden_axis=date_window`, distinct from `period: null`.
15. **Multi-ask.** `turn/sub_asks.py:10`, `12`, `315`, `319`; `engine.py:5965`, `6183`. Asks answered in order; the which-one question for a family head/prefix applies to a multi-ask turn only and an exact variant code is answered as typed; one question per turn even when two asks raise the same one.
16. **One code path for "changed the subject".** `topic.py:1`, `53`. `new_topic` clears pending, `last_rows` and non-named carried kinds together, never separately (the roster, carried entities and escalation offer must not disagree). `turn/episode_digest.py:47`, `138`: a topic switch is the one close trigger for an episode, so the v1 tail must still see it on the bridged verdict.
17. **Human handled and tier rules.** `engine.py:5759` (human intervention closes nothing: state untouched); `engine.py:5741` and `turn/profile_facts.py:600` (a fresh promotion ask re-opens the tier roster, tier is never carried into a new ask); `turn/state.py:255` and `turn/compose.py:850` (staff tier gets no handover offer: `OfferHandover` must check `profile.tier`).
18. **Handover.** `lanes/escalation.py:519`, `989`, `1015`, `1057`, `1074`; `turn/decide.py:778`; `turn/apply.py:2039`, `2085`. `HandOver` only when `escalation.requested`; an unresolved or unconfident product is resolved/clarified BEFORE any team question; an unclear team is a ClarifyAct with team display names, never a derived or guessed team.
19. **Carried refs never produce a miss line.** `engine.py:5258`, `5488`, `5519`; `turn_runtime.py:2111`, `5526`. Only tokens named this turn can be "not found"; the bridge must set `current_message` false on held rows.
20. **No unfiltered lookup and no claim without a lookup.** `turn_runtime.py:3880`, `3891`, `3820`, `5531`; `answer_bridge.py:2096`. An entity that is unresolved/ambiguous becomes a ClarifyAct, never a Lookup with zero filters; a miss over a pick keeps the open roster.
21. **Quoted reply.** `engine.py:1178`; `quoted.py:3`; `turn/tail.py:22`, `53`. CONTRACT GAP: the quoted turn's held/pending replace the live ones as `state_in`, and a follow-up the quoted answer opened replaces the live picker. `apply_turn` needs an optional quoted-state input or a quoted pick will route against the wrong pending.
22. **Several attachment types in one ask.** `lanes/business/answer.py:4182`, `4204`, `5093`; `lanes/business/gate.py:978`. `Ask.attachment_type` is a single key, so "photo and cert" must be one ask per type (answered in order) or the bridge loses the multi-type verdict key.
23. **Learned profile statements.** `turn/profile_facts.py:485`, `518`. `Ask` has no `profile_statement`, so PV2 turns write no learned facts (usual brands): accept as v1 only or add the field.
24. **Declined non-escalation offer.** `tail/outcome.py:199`, `turn_runtime.py:2031`. A "no" to a non-escalation offer clears that pending.
25. **Pick payload decides the effect.** `turn/apply.py:292`. A pick landing on a member-typed option reaches the acceptance path whatever the pending kind; `answers_bot_question` applies the option's `payload.kind`.

Rulings that PV2 deliberately changes (called out so the conflict is visible, none dropped):

- `engine.py:624`, `turn/task.py:832`, `848`, `turn_runtime.py:3299`: the bare-number product ladder (exact, prefix family, substring family). PV2 forbids prefix and substring, so a non-exact number is unresolved and gets a clarify with real candidates; the old behaviour stays on the flag-off path.
- `head/parser.py:320`, `lanes/escalation.py:1015`: "the customer's own team word, verbatim" for several teams. PV2 uses `team_key` from the index or a clarify; no word matching.
- `lanes/business/report_ask.py:595`: top_n has a default with a note today. The contract table asks metric and basis together and marks period and top_n defaults pending owner.


## Main tables

Paths are relative to `sorento_crm_backend/app/services/chatbot`; `../` marks files outside it.

### analytics/backfill.py

| line | ruling | PV2 handling |
|---|---|---|
| 12, 20, 84 | Reply attribution windows for analytics (next incoming row of same contact; a late send is not a reply), crew rulings Q2/Q3. | `bridge` - Offline analytics over stored turns, reads chatbot.turns rows that PV2 writes unchanged. |
| 15 | HYBRID: saved parser output (state_trace.parser_raw) is labelled from the parser, with a free subject. | `bridge` - Analytics backfill reads parser_raw; the PV2 trace (pv2_turn fact) must keep a parser_raw-shaped field so the labeller keeps working. |

### analytics/labeller.py

| line | ruling | PV2 handling |
|---|---|---|
| 5, 20, 136 | HYBRID labelling (9 Oct): ask subject is free, resolver prefix/substring/embedding guesses are not trusted, a casual message with no domain is noise. | `bridge` - Offline labeller, not on the turn path; PV2 also forbids the guessing resolvers, which strengthens the premise. |
| 48 | Labeller batches of 40 asks per model call. | `bridge` - Offline analytics constant, unaffected. |

### answer_bridge.py

| line | ruling | PV2 handling |
|---|---|---|
| 17 | Captain ruling 20 Sep: the answer bridge lives outside turn/, as the single home for divergences from the shared ladder. | `bridge` - answer_bridge runs after the v1 verdict; PV2 never touches it. |
| 57 | compose.py and the bridge share one copy of the pending-mint precedence (Hazards section). | `bridge` - Same engine stages run on the bridged verdict. |
| 227, 1506, 1885 | 22 Sep R6: the cross-domain ladder never mutates suggested_team; the question's own origin-domain team is used (stock origin always warehouse, incoming always purchasing). | `bridge` - Ladder runs inside the miss arm on the bridged verdict. |
| 231, 1032, 1036, 1184, 1226, 1267, 2007 | Hand pass 12 round 3 R2/R5: a did-you-mean pick that hits runs no resolver of its own; the did-you-mean roster and the CS member offer combine into ONE pending whose numbering continues. | `bridge` - Roster and miss arm run unchanged. The combined pending is a v1 Pending, so PV2 persists it as pending kind v1_open_question (v1_kind kept) and the pick passes through as open_question_answer. |
| 247, 2143 | 24 Sep ruling (ported #1118) and 26 Sep F1: a dealer's stock ask never offers an escalation; the did-you-mean is a pick of the suggested code(s) carrying the typed quantity, one candidate included. | `bridge` - Dealer stock path in the bridge; the did-you-mean pick resolves as v1_open_question. |
| 337 | Owner: an item that exists in both Sorento and Mocha is searched on both sides and the offer escalates to either company. | `bridge` - Dual-company search happens in fetch and the ladder, not parse. |
| 799 | PICKER-ONE (13 Oct): the customer picker no longer rewrites text; each row is stamped from the gate probe and named by ledger family label, company tag only when companies differ. | `clarify_copy` - PV2 customer-name candidate options (resolver, display names) should reuse the same line shape; the has-DO stamp and naming stay in candidates_by_kind via the bridge. clarify_copy never prints codes or ids. |
| 864 | Ledger-family grouping keeps two or more representatives as separate customer options (same rule as gate._cust_base). | `resolve` - PV2-RESOLVE customer name candidates (cap 8) should group by ledger family the same way so duplicates do not become separate options. |
| 1357 | Hand pass 11: the company is clarified with the user when not clear; the offer keeps production's sentence and companies ride on the options. | `bridge` - Company clarify stays bridge-run; any PV2-minted company clarify must list company display names. |
| 1551 | 8 Sep: ladder rungs (491_chatbot_ladder_incoming_po) must actually run and show on the trace. | `bridge` - Unchanged. |
| 1622 | 11 Sep second ruling R2(b): a zero code never earns the 'only other' line; the zero sentence says it. | `bridge` - Formatter rule, unchanged. |
| 2096 | Contract 36, hand pass 2 item 8: a roster survives its own pick AND a miss over it; the escalate offer is a sentence the roster carries, not a replacement question. | `apply_turn` - A pick that misses must not clear a still-open roster pending: apply_turn keeps the pending and counts a MISS (re-ask with real options) instead of replacing it. |

### contracts.py

| line | ruling | PV2 handling |
|---|---|---|
| 52 | Owner rulings K2-K4, C1: each carried row is annotated with the live turn that put it there. | `bridge` - Focus row annotation is v1 State, produced by focus_from_v2 for carried refs. |
| 493, 660 | Captain rulings 1 and 2 (sales-report plan): one shared sales-report arg contract and the sales ranking verdict keys. | `reports` - ReportSpec maps onto these 59-key verdict args via the bridge; report_measures must emit the same keys. |
| 514 | Owner access ruling 26 Sep: the sales report uses its own access key, no new key. | `bridge` - Grant check in the lane, unchanged. |
| 730 | #1118 port (owner ruling 24 Sep): dealer stock verdict keys. | `bridge` - Verdict keys the bridge must be able to emit. |

### dealer_stock.py

| line | ruling | PV2 handling |
|---|---|---|
| 1 | 26 Sep: the dealer stock ask never offers an escalation; it refers to the salesman. | `bridge` - Lane logic after parse. |

### dispatch.py

| line | ruling | PV2 handling |
|---|---|---|
| 27 | 5 Sep: nothing chatbot-shaped in .env; URL and key live on the workspace row. | `v1 only` - Infra rule, not parse related, unchanged. |

### do_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 4 | 2 Oct / 4 Oct (every contact): a DO list ask with no range, or over 31 days, is answered with one line and fetches nothing. | `bridge` - Fetch-stage rule for ORDER_TOOLS; the bridged verdict carries the date window. |

### engine.py

| line | ruling | PV2 handling |
|---|---|---|
| 148 | TOP-N-UNCAP (30 Sep): a top 200 goes out as several messages, so delivery never relies on n8n chunking. | `bridge` - Tail splitting, after the answer. |
| 216 | MULTI-ASK Q5 (9 Oct): a long answer splits as text, never a file, no tiny chunks. | `bridge` - Tail splitting, after the answer. |
| 401, 904 | The parser reports, the engine judges: a message that is only a position ("2"), a list ("1 and 2") or "all" is read in code; every position must be on the roster else it is not a bare pick; on a bare pick the picked option IS the axis and a carried value naming none of the picked options is dropped. | `apply_turn` - answers_bot_question must carry an exact option key; any key not among the pending options is a MISS (re-ask with the real options). A picked option replaces the carried value on its axis, it never merges. |
| 494 | No second prompt: a misread is fixed one way every time by the engine. | `v1 only` - Engine post-parse correction; with PV2 the single parse already carries index keys. |
| 624 | ROGUE-CONVO-REVIEW (6 Oct): a bare number tagged as product takes the same ladder as a code-like token (exact, prefix family, substring family from the resolver probe) and gets the picker. | `resolve` - v1 only for resolution: PV2 forbids prefix/substring, so a bare number that is not an exact product code becomes unresolved and a clarify with real candidates; the v1 ladder stays reachable only through the flag-off path. |
| 1178 | QUOTED-REPLY (7 Oct): a quoted bot message is this turn's context; that turn's focus and open question replace the live ones. | `apply_turn` - apply_turn needs the quoted turn's held/pending as its input state when the envelope carries a quote (the v1 engine swaps state_in before apply). Flag: contract has no quoted-reply field; CARRY must read quoted turn facts or the quoted pick will misroute. |
| 1336 | 28 Sep: a console dry run is recognised by ingress console plus console_origin extra. | `bridge` - Console detection is also the PV2 flag condition (parser_mode); unchanged. |
| 2025 | Reviewer S2 on PR #1273 (owner): a dealer sees only its own customers; ruling pending owner confirmation. | `resolve` - Customer name candidates in PV2 must be built from the contact's grants/company scope (as the contract says), so a dealer never lists other customers; the scope stays in the resolver. |
| 2348 | STOCK-FILL-PREDICATE (6 Oct): a plan already scoped to a resolved product reads as reason product subject, not a stray-word miss. | `bridge` - Plan/fetch stage. |
| 4255 | Roster cap (20 Sep): chatbot_entity_kinds.roster_cap trims rosters on specific_options. | `bridge` - Gate stage; PV2 customer candidate cap 8 is the PV2-side equivalent. |
| 4368 | AC-107/H5 (23 Sep): an attachment with a falsy url is unreadable, not a plain-text fallthrough; no ledger row, no job. | `v1 only` - Media intake before parse. |
| 4432 | AC-007 (23 Sep): same attachment handling as every other declared branch kind. | `v1 only` - Media intake before parse. |
| 4624 | Memory lane A Q5: the recall re-parse is gone, exactly one parser call per turn. | `parser v1 only` - PV2 keeps one call per turn (contract: no re-calls); history/MEMORY replaces recall. |
| 4754 | Fix round 12 PR #833 (28 Sep): once grounding reads the ask as a described product set, the set's leg (stock, incoming, promotion, document) is the turn's domain. | `bridge` - Grounding and with_set_leg run on the bridged verdict. |
| 5129 | A turn whose only fetch is a granted chat report is scoped by the asker rule (dealer only). | `reports` - Scope handling for report asks is passed exactly as the lanes pass it (contract section 5). |
| 5241 | A pick settles only its kind; the unresolved product must still reach the promotion tool. | `apply_turn` - apply_turn: a pick sets only the kind it answers (option payload kind); other held kinds are untouched, and an unresolved held ref is resolved or clarified before Lookup. |
| 5253 | Fan-out domain hint is its own rule (reviewer MB-1). | `bridge` - Engine stage. |
| 5258, 5488, 5519 | STOCK-FILL-PREDICATE (6 Oct): a carried uuid-less word is not a token this message named, so its miss is never said back; the scope gate reason names only this message's tokens. | `apply_turn` - Carried (held) refs that were not named this turn must be marked refers_to_held / carried and never produce an 'unrecognised' line; bridge must set current_message false for carried rows. |
| 5741, 5746 | Hand pass 10 (21 Sep): a fresh promotion ask that names no access level re-opens the tier roster, never settles on the last picked tier. | `apply_turn` - A new_topic/new ask in the promotion domain must not inherit the previously picked tier: tier is not carried across a new ask (clear on new_topic, keep only on refines_held). |
| 5759 | Q2 ruling: human intervention closes nothing; the topic is whatever it was when the bot resumes. | `apply_turn` - A human-handled turn must leave held/pending/topics untouched (state unchanged, like small_talk). |
| 5965 | MULTI-ASK Q1(b): a multi-ask turn (2+ sub_asks) whose typed code maps to two or more products with family null asks which; a one-ask turn keeps today's path. | `apply_turn` - Several asks in one message answered in order; an ambiguous family head across asks -> ClarifyAct (which one) only for multi-ask, one-ask keeps the normal path. |
| 6103 | #1118 port (24 Sep): the open task's own pick. | `bridge` - Stock open task passes through as v1_open_question. |
| 6183 | AC-1708 (20 Sep): an offer or access_ask exit is the SAME question whether the message named one domain or two; ask it once. | `apply_turn` - One ClarifyAct per turn for the same question even with two asks; never two wordings of one question. |
| 6278 | R5 (20 Sep): the lane's run_fetch is called for every FetchSpec, no pre-fetch trigger shortcut. | `bridge` - Fetch stage. |
| 6406 | R4 (owner ruling 5): the record-key rerun gate asks the RESOLVER what the token is, not the parser's hint. | `resolve` - PV2 refs come from the exact resolver (kind from the resolved row), never from the model's kind alone; consistent with R4. |
| 6482 | 16 Sep: a single un-uuid'd candidate passes through deferred; no escalate offer manufactured from a name alone. | `resolve` - PV2 unresolved single candidate -> clarify with real candidates or truthful none, never an escalation offer. |
| 6588 | 8 Sep: incoming ladder rung (491_chatbot_ladder_incoming_po) must run. | `bridge` - Ladder, unchanged. |
| 6650 | Hand pass 12 round 3 Group F: a bare positional pick runs no resolver, so the header falls to the focus carry; a multi-uuid customer option carries no name. | `bridge` - Header comes from focus_from_v2(held); held ResolvedRef label must be filled so the header is never blank. |
| 6781, 6832 | No paging (26 Sep): a counted set is remembered only when too long to list, so the answer to 'how many should I show?' lists that many of the same set; ROGUE-CONVO Q4: a set read from words inside an unplaced token is never carried. | `apply_turn` - Answer to a how-many question is top_n on the same held set (v1_open_question passthrough tonight); never carry a set derived from an unplaced token. |
| 6919 | PICKER-ESCALATION (4 Oct): an empty order list is left as composed. | `bridge` - Order list tail. |
| 7085 | 26 Sep F1: whatever composed the stock answer for a dealer never carries an escalation. | `bridge` - Compose stage. |
| 8058 | 23 Sep AC-1866: a member_offer's numbered text list IS the offer; quick replies withhold its options. | `clarify_copy` - Roster kinds (including PV2 clarify options) go out as the numbered text list only, not as buttons. |
| 8066 | QUICK-REPLY-NAMES (6 Oct): quick replies are kept only for fixed short option kinds; every roster kind's labels are the numbered text list alone. | `clarify_copy` - ClarifyAct options are numbered text list lines; quick-reply buttons only for fixed short options (yes/no). |
| 8232 | S6 (16 Sep): stock allowed is a CRM fact on the contact row, default ON, read via load_profile. | `bridge` - Profile seam, same. |
| 8469 | PR #1247 round 6, ruling 3: a rate limit that never cleared is one plain message. | `parser v1 only` - llm_call handles retries for PV2's call as well; same helper, keep. |
| 8474 | AC-MEM086 Q10: the dealer never reads exception text. | `bridge` - Error path, same for PV2. |
| 9480 | CUSTOMER-ASKS-REFER-ONLY (1 Oct): log every reply that referred the customer to their salesman, and only those. | `bridge` - Tail. |
| 9491 | 28 Sep: a chat console turn writes the refer rows and enqueues the real salesman job; other dry runs do neither. | `bridge` - Tail. |

### escalation_control.py

| line | ruling | PV2 handling |
|---|---|---|
| 25 | 30 Sep: the dealer line the stock ask already prints is used for blocked contacts. | `bridge` - Lane. |

### head/parser.py

| line | ruling | PV2 handling |
|---|---|---|
| 68, 1029 | PR #1247 round 6, ruling 3: a 429 is waited out inside the call. | `parser v1 only` - PV2 call.py should reuse llm_call's wait-out of rate limits (same helper); behaviour preserved if it does. |
| 120 | No paging (26 Sep): continuation key stays only for the how-many answer; answer is top_n. | `parser v1 only` - PV2 has no continuation key; the how-many answer is v1_open_question passthrough with top_n (behaviour preserved through the bridge). |
| 126 | Broaden (17 Sep): broaden_axis says which axis, broaden_to says how far ('family' widens a variant to its family, 'all' drops the axis). | `parser v1 only` - PV2: op clear on the entity kind = 'all'; 'family' = replace the variant ref with its family ref. apply_turn must implement both widenings (family vs drop); the model returns op and key. |
| 155 | #1262 slice 5 (F4) ruling 1: the parser owns quantity; never regex-stripped from raw downstream. | `parser v1 only` - PV2 Entity has no quantity field; Spec number is separate. Gap: stock quantity per product must be carried by the schema or the bridge will lose 'SRTWC286 x 10'. Flag for CORE. |
| 162 | #1118 port (24 Sep): stock-ask v2 verdict keys. | `parser v1 only` - Keys exist in the 59-key verdict; bridge emits them. |
| 200, 202 | Does this message name a domain or status word of its own (17 Sep): the discriminator between NEW ASK and REFINEMENT; replaces scope_exclusive. | `parser v1 only` - PV2 act refines_held vs new_topic replaces domain_in_message; the bridge derives domain_in_message from act so decide() runs unchanged. Behaviour preserved if the mapping holds. |
| 246 | Sales-report channel filter key (captain ruling 4). | `parser v1 only` - PV2 uses ReportSpec Filter(dimension channel); bridge maps back. |
| 263 | Owner 26 Sep: never assume the basis or the grain; null rank_by is asked too. | `reports` - PV2-REPORTS report_policy: metric and basis = ask, asked together (mirrors 4 Oct ranking rule). |
| 288 | REPORT-ENGINE (4 Oct): ranking_refine says the message only refines the ranking on screen. | `reports` - PV2 act refines_held with held report edits field by field. |
| 292 | REPORT-DRILLDOWN (8 Oct): a follow-up ranking on a new axis is scoped (rows of last ranking), global, or unsure (asked). | `reports` - row_pick drills into that row; a new group_by on a held ranking with no row_pick must ask scoped vs global (clarify, never guess). |
| 320 | Owner rules R-a/R-c console pass 4: team word maps to an enum member when exactly one, else the customer's own word verbatim. | `parser v1 only` - PV2 Escalation.team_key from index else null; null -> clarify with team display names. Verbatim-word behaviour not preserved (no word matching), replaced by clarify. |
| 689 | proceed_anyway joins the verdict keys by the same rule (ported #1118). | `parser v1 only` - Bridge verdict key; PV2 emits it only via answers_bot_question option payload. |
| 696 | open_question_answer joins by the same rule (PR #1247 round 8). | `apply_turn` - answers_bot_question + option_key maps to open_question_answer in the bridged verdict for v1_open_question pendings. |
| 807 | Hand pass 3 (17 Sep): the parser is shown the focus, not just the previous reply, so a refinement and a domain switch are judged against the subject. | `parser v1 only` - PV2 MEMORY block HELD section does exactly this (history.py render_memory). |
| 904 | Hand pass 3 ruling 2: a one-line subject, omitted whole when the focus is empty. | `parser v1 only` - MEMORY HELD line prints 'none' when empty (contract section 2). |
| 915 | D17 (13 Sep): deterministic code never reads words; a question answered by a POSITION states the roster to the parser. | `apply_turn` - PENDING QUESTION options printed as keyed options; answer.option_key is exactly one printed key (strengthens D17). |

### label_catalog.py

| line | ruling | PV2 handling |
|---|---|---|
| 107 | PRODUCT-ALTERNATIVES (8 Oct): the two refer-bearing verdicts print without their refer when an alternative with stock printed. | `bridge` - Formatter label, unchanged. |
| 248 | Owner ruling (ask a69045557): a '... and N more' line is a count only, no pointer; the offer line points. | `bridge` - Formatter label, unchanged. |

### lanes/business/__init__.py

| line | ruling | PV2 handling |
|---|---|---|
| 87 | 26 Sep: top selling has no default metric and never assumes the grain or the basis. | `reports` - report_policy mirrors it: metric and basis = ask, asked together in one question. |
| 100 | Retest R8 (27 Sep): one short question in words when a message cannot narrow a ranking. | `clarify_copy` - Report clarify copy: one short question, no codes. |
| 288 | R19 (13 Sep): EVERY filter line prints every time, 'all' where the filter was not given. | `bridge` - Formatter, unchanged (and PV2 header line Read as: is separate). |
| 763 | Sales-report ruling 2: the detail re-ask repeats whichever DETAIL_OFFER_KIND was actually open. | `bridge` - Pending kind sales_report_detail passes through as v1_open_question with v1_kind kept. |
| 995, 2313 | CHATBOT-SELFREF-SCOPE Q6a: 'my' / 'me' / 'our' means ALL the asker's linked accounts; a carried account never narrows a self-reference ask. | `apply_turn` - A self-reference ask must not inherit a held customer ref: a self-reference report ask replaces/clears the held customer kind. Needs a parser signal (self-reference) or entity kind 'self' in the PV2 index. Flag for CORE. |
| 1005 | S18: crm_sales_report arg builder tells 'no date window' from 'the customer said all dates' via broaden_axis. | `apply_turn` - Period op clear (explicit all dates) vs period null (not mentioned) must both survive into the bridged verdict: cleared=['period'] -> broaden_axis date_window. |
| 1021 | #1118 port (24 Sep): dealer own-customer scope D13/D20. | `bridge` - Lane scope. |
| 1194 | Plan ruling 13: a typed brand off the list ends the carry and is said back as unplaced. | `resolve` - A brand key not in the pinned index becomes null, phrase logged, and a clarify/truthful unplaced line; never kept in held. |
| 1220, 1234, 1980, 2090 | Captain ruling 3 / R17 / AC-1119 S4c D5: product-code and location resolution for the outstanding report; a refinement's own product entity lives off parse_output entities so it dies with the offer. | `bridge` - Outstanding gate resolution after the verdict; unchanged. |
| 1421 | CA-3: a chat report fetch is scoped by the asker rule (dealer only), not by customer links. | `reports` - Scope passed as the lanes do. |
| 1447 | Owner ruling Q5/Q2/Q4: SO cards gated by the SO reveal key, refused outside links, one card per SO. | `bridge` - Lane. |
| 1642 | Sales-report ruling 2: an absent kind in the re-ask marker reads as outstanding_detail. | `bridge` - Backward-compat read, unchanged. |
| 1781 | R13 (13 Sep): domain order plus an outstanding order_status picks the outstanding report; a product is no longer required, a customer is subject enough. | `bridge` - Route decision in the lane. |
| 1813 | LOWSTOCK-FILTER-ASK (2 Oct): nothing runs until the product category is settled; taken from the message, else asked; a product-code ask names its own scope. | `reports` - Low stock is a bridged ask: LowStock group (group_by, all_categories) feeds low_stock_ask; category missing -> existing required-field question. |
| 2026, 2097 | 26 Sep Q2: a report is both the table in text and the same query as Excel; override uses its own access key, no new key. | `bridge` - Lane. |
| 2298 | DO-ASK-SIMPLIFY rules 3-4 (2 Oct, every contact 4 Oct): a DO list ask with no range or over 31 days answers one line and fetches nothing. | `bridge` - Fetch-stage rule. |
| 2445 | PRODUCT-ALTERNATIVES round B (8 Oct): an asked discontinued product with a stored alternative gets the alternative's answer folded in. | `bridge` - Lane. |

### lanes/business/alternatives.py

| line | ruling | PV2 handling |
|---|---|---|
| 2, 17, 22, 209 | 8-9 Oct: Alternative line; delivery absent for old code; 'No ETA' / 'no Product Photos.' not-found lines; at least show that we tried to search. | `bridge` - Formatter and lane. |

### lanes/business/answer.py

| line | ruling | PV2 handling |
|---|---|---|
| 465, 488, 563, 750, 813, 870, 871, 927, 985, 1113, 1169, 1294, 1325, 1492 | 11 Sep second ruling R1/R2: compact stock reply grouping, granted values shown as '(O/S: n)', incoming line grouping (po/spo kinds, nothing splits into plain group). | `bridge` - Compose formatter, unchanged. |
| 521, 2543 | 24 Sep (#1118 port) D17; hand pass 12 round 2 M2: described-set stock reply shape. | `bridge` - Formatter. |
| 943, 964 | 6 Sep: codes that came back empty on BOTH sides are named; a ZERO-flagged entry's rows are hidden. | `bridge` - Formatter. |
| 1186 | 2026 ruling: no rendered Source field; sentences speak of what is ON ORDER. | `bridge` - Formatter. |
| 1357 | 13 Oct STAFF-STOCK-ETA: open lines of the same product are shown on the staff stock answer. | `bridge` - Formatter. |
| 1558, 3232 | 22 Sep R6: a stock-origin ask is always suggested to warehouse; a stock/incoming question with no row follows the ladder rule. | `bridge` - Ladder. |
| 2237 | STRICT NOT-FOUND (owner decision, twice affirmed): when the customer's actual code is not found it says so, never a substitute. | `bridge` - Truthful reply rule; PV2 inherits it through the bridge (and its own 'no customer named' line). |
| 2368 | 6 Sep: a display name must never stand in for the code (prod turn 631d4b65). | `bridge` - Formatter; consistent with PV2 'no internal codes' except where today's formatters print product codes. |
| 2757, 2965, 2987 | 26 Sep no paging: a counted set is listed up to SET_LIST_MAX and says the count; 'no cap', 'always break it down'. | `bridge` - Formatter. |
| 2769 | Fix round 9 PR #833 (owner): 'no explanations' in the did-not-match line. | `bridge` - Formatter; fits the no-explanations-in-UI rule. |
| 2775 | 28 Sep: the incoming fix applies to stock too. | `bridge` - Formatter. |
| 2822, 3171, 4773 | ESCALATION-CONTROL Q2: a blocked contact's miss names what was not found, then 'Please refer to your salesman.' | `bridge` - Compose. |
| 3464, 4031 | ROGUE-CONVO-REVIEW (6 Oct) Q4: 'Here's what you want' lists what was typed; the miss line prints the typed tokens. | `bridge` - Formatter. |
| 4182, 4204, 5093 | ATTACHMENT-MULTI (2 Oct) Q1(a)/Q2(a): a several-type miss names the missing types; files that exist are sent; every type of a several-type ask is stamped. | `apply_turn` - Specs/attachment_type: PV2 Ask.attachment_type is a single key, so a several-type ask ('photo and cert') needs one ask per type (asks list answered in order) or a gap flagged for CORE; the bridge must set the multi-type verdict key. |
| 4334 | 10 Sep owner ruling reverses 6 Sep: reply shape for incoming/has-incoming. | `bridge` - Formatter. |
| 4353 | R-LASTIN (9 Oct): a lone 'last in' with no SPO line answers from SPO only. | `bridge` - Formatter. |

### lanes/business/chat_report.py

| line | ruling | PV2 handling |
|---|---|---|
| 14 | 10 Oct card Q1b/Q2b: when BOTH report fields are missing, ask both in ONE reply, one per line; whatever is missing is asked again; words become ids via existing resolvers. | `reports` - report_policy asks missing fields together in ONE question; resolution stays in resolver (matches PV2 principle). |

### lanes/business/fetch.py

| line | ruling | PV2 handling |
|---|---|---|
| 175 | 8 Sep: the vector search went ('we can drop the rag'). | `resolve` - Fits PV2 no embeddings. |
| 641 | CHATBOT-SELFREF-SCOPE Q6a (30 Sep): 'my sales' means all linked accounts. | `apply_turn` - See __init__.py:995: self-reference clears held customer; bridge sets the self-reference verdict key. |
| 750 | AC-22: a subject or kind ask is answered by explicit links and the kind. | `bridge` - Fetch. |
| 870 | S18: a PRODUCT-ONLY ask (resolved product, no customer) reads the report by product. | `bridge` - Fetch. |
| 1164 | 24 Sep #1118 port D13/D20: the dealer's own scope. | `bridge` - Fetch. |
| 1231, 3635 | 26 Sep no paging: a set that fits one reply is listed, else a count and the rest is withheld. | `bridge` - Fetch/answer. |
| 1838 | D12 (8 Sep): product details for a variant code answers per code. | `bridge` - Fetch. |
| 2231 | R14 (13 Sep): with both lists on offer, the presenter adds a line. | `bridge` - Formatter. |
| 2776 | 27 Sep top selling ruling: 'top 10 for ...' is an answer, never the miss lane. | `bridge` - Fetch. |
| 3310 | Owner ruling: a non-order tool's reply must not use order-tool state. | `bridge` - Fetch. |
| 3454 | PRODUCT-ALTERNATIVES Q4(a) 8 Oct: stored alternatives shown. | `bridge` - Fetch. |

### lanes/business/gate.py

| line | ruling | PV2 handling |
|---|---|---|
| 43, 945, 398 | Roster cap (20 Sep): rosters trimmed on specific_options by chatbot_entity_kinds.roster_cap. | `bridge` - Gate; PV2 candidate cap 8 separate. |
| 57 | PICKER-NO-CAP (2 Oct): column server default for the cap. | `bridge` - Gate. |
| 114 | No ALLOWS_EMPTY row ruling: a bare 'last purchase cost' needs a product. | `bridge` - Gate; required field ask. |
| 143 | PLAN-low-stock S6 (14 Sep console round 2): INTENTS that can run without an entity. | `bridge` - Gate. |
| 978 | ATTACHMENT-MULTI Q3(a): one plain question when several types are missing. | `clarify_copy` - Single plain question, no codes. |
| 1135 | Console pass 3 (6 Sep): picker lines read 'N. name (company code)'. | `clarify_copy` - Roster line format owner ruled; PV2 candidate lines keep 'N. display name' and add the company tag only when needed; the company code is a display tag today's picker prints, so keep it as today's formatters do. |

### lanes/business/low_stock_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 15, 17, 179, 204, 493 | Crew ruling Q2/Q3: a supplier name the parser placed is checked against the supplier master; a grouping the workbook cannot split by is asked supplier/category/both/none; the second miss runs with no supplier filter and says so. | `reports` - LowStock.group_by from the parser; exact supplier match against the master (no fuzzy); the miss line is the existing lane formatter. |
| 225 | Owner ruling Q2 #1445: category required, supplier and grouping taken when given. | `reports` - Required-field policy preserved: category asked, supplier/grouping optional. |

### lanes/business/miss_suggest.py

| line | ruling | PV2 handling |
|---|---|---|
| 480 | F2: per-token UX cap bypassed for product_attachment on the miss suggestion. | `bridge` - Miss arm. |
| 860 | F1 amendment (1 Sep 2026): a composite join that cannot attribute an answer row is not guessed. | `bridge` - Miss arm. |
| 952 | #768 ruling: this arm always fails open. | `bridge` - Miss arm. |
| 1031 | False match: a private helper parameter named owner (no ruling). | `v1 only` - Not a ruling. |

### lanes/business/pickers.py

| line | ruling | PV2 handling |
|---|---|---|
| 10, 263 | PICKER-ONE (13 Oct): the customer picker no longer rewrites text; one stamp per picker line. | `clarify_copy` - Customer candidate options use the same one-stamp line shape. |
| 171 | Console pass 3 (6 Sep): the picker suffix claims a DO, so an unverified one is not shown. | `bridge` - Picker stamp. |

### lanes/business/predicate.py

| line | ruling | PV2 handling |
|---|---|---|
| 189 | Fix round 12 PR #833 (owner 28 Sep): the incoming fix applies to stock set questions. | `bridge` - Predicate. |

### lanes/business/ranking_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 1 | The one place that picks the next clarify question for a ranking (4 Oct): metric first then basis, neither defaulted. | `reports` - The 4 Oct 'ranking never defaults metric or basis: it asks' - report_policy mirrors it (ask both in one question). |

### lanes/business/report_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 9 | A ranking never defaults the metric or the basis: it asks (4 Oct), via ranking_ask.next_question as top selling does. | `reports` - report_policy.py mirrors it tonight (metric, basis = ask, one question). Proposed defaults (period this_month, top_n 10) marked pending owner. |
| 498 | 1b code review S4: the sales report prefix rule so SRT5674 covers SRT5674-N. | `bridge` - Report engine arg. |
| 595 | Crew ruling: top_n gets a default with a note; period keeps the helper's own give-up. | `reports` - Conflict flagged in contract: top_n default exists here with a note; PV2 table keeps current behaviour. |
| 610 | Owner rule: no reading on a new axis right after a ranking is asked, never guessed (drill defaults to unsure). | `reports` - New group_by with no row_pick on a held ranking -> ask scoped vs global (clarify never guesses). |

### lanes/business/resolve_gate.py

| line | ruling | PV2 handling |
|---|---|---|
| 562 | 6 Oct: the resolver resolves a brand word as that brand with no probe (exact brand rows). | `resolve` - PV2 brand key is exact index key; same intent. |
| 1039 | PICKER-ONE 13 Oct: customer_bases_with_do on the picker stamp. | `bridge` - Picker. |
| 1250 | Crew ruling 2 Oct: the names merely carrying a word are not the same as the customer. | `resolve` - PV2 customer names are exact only; the one allowed containing-name search just builds options. |
| 1541 | 6 Sep: actual_delivery_date = DELIVERED DOs. | `bridge` - Fetch. |

### lanes/business/services.py

| line | ruling | PV2 handling |
|---|---|---|
| 571, 598 | Reviewer S2 PR #1273: a dealer sees only its own customers (pending owner confirmation); 'enforced' = top-selling rule (linked and no active office access). | `resolve` - Candidate scope for customers must follow the same enforced rule. |

### lanes/casual.py

| line | ruling | PV2 handling |
|---|---|---|
| 314, 331 | PR #1247 round 6 ruling 3: a 429 is waited out; if it never clears one plain apology. | `bridge` - Shared llm_call; unchanged for small_talk. |

### lanes/escalation.py

| line | ruling | PV2 handling |
|---|---|---|
| 15, 735 | H26 (27 Sep, #865 fix option 1): brand half closed; the escalation brand is the brand of the resolved product. | `bridge` - Escalation lane after the verdict; HandOver goes through run_escalation_lane. |
| 81, 1442 | WA-MSG-TRIM (3 Oct): one handover is ONE customer WhatsApp message. | `bridge` - HandOver lane. |
| 519 | A clarify beats a wrong assignment ('we clarify with the user'). | `apply_turn` - Escalation with an unclear team -> ClarifyAct with team display names; never guess a team (Escalation.team_key null -> clarify). |
| 989 | 6 Sep: the escalation uses the parser's OWN answer, never a derived one. | `apply_turn` - HandOver driven by escalation.requested only (parser semantic verdict). |
| 1015, 1081, 1238 | R-a/R-b console pass 4 (7 Sep): ambiguous team words are asked about; the derived team is deleted. | `apply_turn` - team_key from the index else null -> ClarifyAct asking which team. |
| 1057, 1074 | D1 console pass 3: gating on the parser's team made the arm fire on every turn with no named team. | `apply_turn` - Do not fire HandOver unless escalation.requested is true. |
| 1478 | 5 Sep: reply is the record of what was composed. | `bridge` - Lane. |
| 1490 | QUICK-REPLY-NAMES (6 Oct): company clarify passes no quick replies. | `clarify_copy` - Company/roster options are text only. |
| 1495 | AC-MEM084/085 Q10: a turn that reaches the reply stage never leaks exception text. | `bridge` - Error path. |
| 1598 | Per ruling's own note: reply quoting. | `bridge` - Lane. |
| 1621 | 22 Sep R1 (AC-EQ-1..3): the quoted body is text if truthy else a fallback. | `bridge` - Lane. |

### lanes/escalation_services.py

| line | ruling | PV2 handling |
|---|---|---|
| 47 | 22 Sep: no [object Object] in the SLA comment. | `bridge` - Lane. |
| 63 | #865 27 Sep fix option 1: brand of the resolved product. | `bridge` - Lane. |

### lanes/ideate.py

| line | ruling | PV2 handling |
|---|---|---|
| 3 | Ideation should be at the MCP side (said twice): the lane calls crm_ideation_turn through the MCP runtime client. | `ideas` - Extraction and save stay ideation's own (contract s5); PV2 shares dialogue mechanics only. |
| 205 | 24 Sep #1179: a dry run calls the tool too, as a test turn (is_test rides the call). | `ideas` - Unchanged; PV2-IDEAS must keep is_test pass-through. |

### llm_call.py

| line | ruling | PV2 handling |
|---|---|---|
| 6 | 26 Sep PR #1247 round 6 ruling 3: a 429 is waited out inside the call. | `parser v1 only` - PV2 call.py reuses llm_call. |

### media_intake.py

| line | ruling | PV2 handling |
|---|---|---|
| 133, 172 | AC-107/H5 (23 Sep): a falsy url is unreadable, not plain text. | `v1 only` - Media intake before parse. |
| 413 | Q2: a count, never '+N more'. | `bridge` - Formatter. |

### order_list.py

| line | ruling | PV2 handling |
|---|---|---|
| 15, 192 | PICKER-ESCALATION (4 Oct): the escalation lane stays one message; an empty list is left as composed. | `bridge` - Tail. |

### quoted.py

| line | ruling | PV2 handling |
|---|---|---|
| 3 | Quoted-reply rulings R1/R2 (7 Oct): reads ChatHistory and ChatbotTurn only, scoped to one contact and the is_test world. | `apply_turn` - See engine.py:1178: apply_turn must be able to take the quoted turn's state. |

### refer_asks.py

| line | ruling | PV2 handling |
|---|---|---|
| 1 | REFER-SALESMAN (30 Sep): every reply that refers to the salesman is logged. | `bridge` - Tail. |

### required_fields.py

| line | ruling | PV2 handling |
|---|---|---|
| 43, 85, 129, 336 | LOWSTOCK-SEMANTIC crew Q4 (4 Oct): only a declared parser answer (fill/pick/all/done/cancel) answers a required-field question; anything else is a new question that always wins; 'clear' is topic_reset with no answer. | `apply_turn` - Exactly the PV2 rule: answers_bot_question needs pending and an exact option key; any other act is a new question; refines/new_topic pop the pending. |

### so_status.py

| line | ruling | PV2 handling |
|---|---|---|
| 9, 290 | Owner rulings on PR #1435 (behaviour card v2): one card per SO, words only; 'all my sales orders' is the SO list (option 2). | `bridge` - Lane. |

### tail/compose.py

| line | ruling | PV2 handling |
|---|---|---|
| 36 | ESCALATION-CONTROL Q2: a blocked contact's miss carries the salesman line. | `bridge` - Tail. |

### tail/outcome.py

| line | ruling | PV2 handling |
|---|---|---|
| 199 | AC-1703 tail (20 Sep): a decline over a non-escalation offer closes it. | `apply_turn` - A 'no' to a non-escalation offer clears that pending (answers_bot_question with the no option payload). |

### tail/scope_block.py

| line | ruling | PV2 handling |
|---|---|---|
| 26 | Captain 24 Aug: the scope header is narrowed (ported verbatim). | `bridge` - Formatter. |
| 190 | Hand pass 12 round 3 Group F: a customer row's own name is shown even when the carry has none. | `bridge` - Formatter. |

### topic.py

| line | ruling | PV2 handling |
|---|---|---|
| 1, 53 | ONE notion of 'the customer changed the subject' (6 Sep): picker roster, carried entities and the pending escalation offer must not disagree about what the conversation is about. | `apply_turn` - new_topic must clear pending, last_rows and non-named carried kinds in one step (single code path), never separately. |

### turn/apply.py

| line | ruling | PV2 handling |
|---|---|---|
| 73, 2189 | 29 Sep: SPO belongs to spo_allocation, not incoming (DOMAIN_BY_DOCUMENT); #1118 port 24 Sep: D29 exact-code narrowing and D13 demand_qty normalisation. | `bridge` - Map applied by v1 apply on the bridged verdict; the model's domain key for SPO must be spo_allocation in the index. |
| 264 | Review round 2: a help-request shape is the parser's to classify, not the gate's. | `parser v1 only` - PV2 has no message_type; small_talk/unclear acts; no engine gate over wording. |
| 292 | Hand pass 12 round 3 R3: a position pick landing on a MEMBER-typed option reaches the escalation acceptance path even when the pending's kind is a roster kind. | `apply_turn` - Option payload kind decides the effect of a pick, not the pending kind: answer.option_key applies its payload (kind member -> HandOver/acceptance). |
| 562 | Hand pass 3 row 5: a NEW ASK that names a document is a new scope; the report re-runs and the old question goes with it. | `apply_turn` - new_topic or refines_held with a changed document drops the pending question. |
| 572, 581 | 13 Sep contract 39: after '1' (SO list), typing '2' gives the DO list: a detail offer stays on screen across its own pick; sales report: no scope question exists. | `apply_turn` - Detail offers (outstanding_detail, sales_report_detail) are v1_open_question pendings that survive a pick: pending is NOT cleared on a pick of those kinds (pending.v1_kind decides). |
| 746, 3104 | 26 Sep F1: 'Couldn't find ELP3753. Did you mean ELP3754?' is a yes/no over its one option; yes picks (quantity rides on), no refers to the salesman, nothing left open. | `apply_turn` - Single-option did-you-mean: answers_bot_question yes -> option payload applied; no -> pending clears and the dealer line; options yes/no exact keys. |
| 895, 1812 | #1262 slice 8 (F1b siblings) ruling 6: a SECOND ambiguous token in the same message is queued on the pick's payload and asked next, never the same options re-printed or silently dropped. | `apply_turn` - Gap: contract has one PendingQuestion. apply_turn must queue further ambiguous items (e.g. in pending.options payload or topics) and ask the next after the first answer; never drop and never ask two at once. Flag for CARRY. |
| 1001 | Hand pass 3: when NOTHING matches the open question, the question stays open exactly as it was and the message is planned as itself. | `apply_turn` - Pending is kept (not cleared) when the message is a new topic/aside that does not answer it; with misses counted only for answers_bot_question failures. |
| 1112 | 17 Sep: a NEW ASK that says what it asks starts from that domain's defaults: every carried kind it did not name goes; a refinement never evicts, combines across kinds and replaces only within the kind named. | `apply_turn` - new_topic: held = this ask only (old held pushed to topics). refines_held: per-kind op semantics (set replaces only that kind, add combines). |
| 1146 | Hand pass 10: a NEW ASK also drops tier, extra and date window (axes that hold no subject). | `apply_turn` - new_topic resets period, attachment_type, specs, report together with refs; only refines_held carries them. |
| 1390, 1528 | Owner: no default metric, never assume; 'by amount' under a ranking changes only the axis named; a fresh ask states its own filters (customer, channel, window not carried). | `apply_turn` - refines_held on a held report edits field by field; a fresh ranking (new_topic) starts an empty ReportSpec; period/customer/channel never borrowed from the last ask. |
| 1729 | The ONE reader of broaden_axis + broaden_to (17 Sep): 'all' drops the axis; 'family' widens a variant to its family. | `apply_turn` - op clear drops the kind; widening to family replaces variant ref with the family ref (implement both). |
| 1907 | Draft ideas: a draft is open and a question or bare confirm names nothing of its own -> the draft absorbs the turn. | `ideas` - idea_missing_field reply updates the held draft (update_held=True), never a second idea. |
| 2014, 2018 | S6 cluster 4 (16 Sep): idle chat ('hello') never fetches; extended by Defect 5 (17 Sep) to the offer-open case. | `apply_turn` - small_talk leaves state unchanged and keeps the pending open; no Lookup is emitted. |
| 2039, 2085 | Contract 111, #866 port, hand pass 9: a product token the parser is not confident about is settled BEFORE any team question; out_of_scope only when no product token. | `apply_turn` - Escalation + unresolved product ref -> resolve/clarify the product first, then HandOver (HandOver last in order). |
| 2600, 2605, 3348 | 26 Sep round 3 ruling 2 / round 5: once the which-one pick is spent, a lone number is the product's quantity, never a pick; the picker is not sticky. | `apply_turn` - Pending clears on answers_bot_question; afterward a bare number is NOT a row_pick unless last_rows exists and the parser says row_pick; quantity needs a Spec/qty field (gap for CORE). |
| 2629 | Issue #1352 chained-question rule (29 Sep): the engine judges a message against the CURRENT question first; earlier rosters stay stored underneath for a later pick. | `apply_turn` - Gap: pending is a single slot. Current question wins; earlier roster kept as last_rows (cap 20), not as pending. |
| 3593 | S6 cluster 4 (16 Sep): 'hello' typed while an offer is open: the pending is carried unchanged and the message is planned as itself. | `apply_turn` - small_talk: state unchanged incl. pending and held. |
| 3661 | No paging (26 Sep): the one carried set is the one the last answer was too long to list; the only message reading it is the answer to 'how many should I show?' (top_n); anything else closes it. | `apply_turn` - v1_open_question pending 'how_many_to_show' passes through; any other act closes it. |
| 3840 | Sticky-offer rule (contract 43, AC-1167): thanks/no fetch nothing; a domain switch counts as a new ask and the '1' under its list no longer escalates (pending cleared on a switch). | `apply_turn` - new_topic clears the old pending; small_talk keeps it. |

### turn/compose.py

| line | ruling | PV2 handling |
|---|---|---|
| 101 | ATTACHMENT-MULTI R3 (2 Oct) Q2(a): the files that exist are sent and the missing types are named. | `bridge` - Compose. |
| 178 | 16 Sep: payload.team names the team in the escalation. | `bridge` - Compose. |
| 334 | Hand pass 3 row 2: 'All' over a customer roster fetched for every listed customer. | `apply_turn` - answers_bot_question 'all' option exact key applies the all-payload (every customer ref) - not a guess. |
| 637 | Never-stuck (STOCK-FILL-PREDICATE, 6 Oct): the bot never leaves the dealer with no next step. | `clarify_copy` - Every ClarifyAct ends with a question that has options; after 2 misses OfferHandover. |
| 718 | ORDER-BROADEN (5 Oct): no offer on a dealer-view block. | `bridge` - Compose. |
| 744 | 24 Sep #1118 port D15: 'just proceed' option. | `bridge` - Compose. |
| 825 | 4 Oct: the first answering domain that has a team names the escalation team. | `bridge` - Compose. |
| 850 | AC-S11-1 owner ruling 2: staff (office tier) get no escalation offer. | `bridge` - Compose; PV2 OfferHandover must not be shown to staff-tier either? Offer handover is the PV2 offer after 2 misses: flag for CARRY to check profile.tier. |
| 887 | ESCALATION-CONTROL Q2: the miss is said, then the salesman line. | `bridge` - Compose. |
| 1152 | QUICK-REPLY-NAMES (6 Oct): a re-print of the question uses the same text-only rule. | `clarify_copy` - Text list only for roster kinds. |

### turn/context.py

| line | ruling | PV2 handling |
|---|---|---|
| 31 | Sum of the layer caps, no separate global rule (contract 6.2). | `parser v1 only` - Parser context assembly (MEMORY block replaces it); PV2 cap is 3 exchanges + head 32k chars. |

### turn/decide.py

| line | ruling | PV2 handling |
|---|---|---|
| 23 | S6 cluster 4: a casual message plans as itself with the pending carried. | `apply_turn` - See apply.py:3593. |
| 125, 180, 521 | 17 Sep: THE TABLE - a message is a NEW question when it says WHAT it asks (domain_in_message), a refinement when it only says WHICH one (entities); an entity op replace; broaden_to widens. | `apply_turn` - act new_topic vs refines_held is that table; entity op set = replace within the kind; domain_in_message is derived from act when building the v1 verdict. |
| 142 | Hand pass 3 half of the pick rule: a customer typing a label back at a roster that offered it has answered position 2; exact string match against the option label only, never word reading. | `apply_turn` - The model returns answer.option_key (exact printed key); code never matches the label text. Behaviour preserved with the key; a typed label that the model fails to map becomes a MISS. |
| 240 | Hand pass 3 (17 Sep): a message answers the open question in exactly three ways: reference_positions, label match, affirmative/negative; answers_open_question retired. | `apply_turn` - Replaced by answers_bot_question + exact option_key; yes/no are options with keys on yes/no pendings. |
| 374 | R15 (13 Sep): does the parser's own verdict say this turn kept the subject of the open question and merely narrowed it? Two signals, both the parser's. | `apply_turn` - refines_held with refers_to_held/entities of the held kind = narrowing; no word lists. |
| 540 | Captain 17 Sep: scope_exclusive removed from schema entirely. | `parser v1 only` - Absent in PV2. |
| 598 | F3, AC-S4-2 (hand pass 3 T6): 'got eta' names another domain with no entity: a domain switch over the standing subject; closes the offer. | `apply_turn` - refines_held with changed domain keeps refs ('delivery for this' keeps refs) and drops the pending; same domain-switch semantics. |
| 644 | Hand pass 3 row 5; browser pass 6 item 5: a refinement reading wins when no positions. | `apply_turn` - When there is no option_key and the ask refines, treat as refines_held, not an answer. |
| 778 | Issue #1323 (28 Sep): an escalation offer is accepted on the parser's ONE semantic verdict, for every offer shape (yes/no, team menu, company pick). | `apply_turn` - Acceptance is answers_bot_question with the offer's option key (or escalation.requested); no yes/no word lists. |

### turn/episode_digest.py

| line | ruling | PV2 handling |
|---|---|---|
| 47, 138 | Q2 ruling: a conversation ends on a topic switch only (one close trigger, same for live engine and backfill). | `apply_turn` - new_topic closing an episode must still be reported to episode_digest (the v1 tail runs on the bridged verdict); apply_turn pushing held to topics is the PV2 side. Flag: the session gap rule (4 h) is a second trigger PV2 adds; check episode_digest consumers. |

### turn/fetch.py

| line | ruling | PV2 handling |
|---|---|---|
| 80 | 8 Sep: what is ON ORDER is a per-contact reveal. | `bridge` - Fetch. |

### turn/memory.py

| line | ruling | PV2 handling |
|---|---|---|
| 70 | Q7 ruling: retention is a COUNT, never a date. | `bridge` - Episodes unchanged (EARLIER SESSIONS comes from them). |
| 450 | Memory lane A Q5: last-3-summaries context layer replaces recall in the SAME parser call. | `parser v1 only` - PV2 MEMORY EARLIER SESSIONS section reuses episode_digest. |

### turn/narrow.py

| line | ruling | PV2 handling |
|---|---|---|
| 15 | Core owns the ledger-family rule (app/services/ledger_family.py); the stock-ask record names a customer-less ask by it. | `bridge` - Shared helper. |
| 342 | AC-1119 exact-code rule; family covered for sales_report when typed now. | `bridge` - Narrow stage. |
| 470, 533 | 16 Sep captain: the narrower never re-asks for an entity the conversation settled (a focus-carried entity with a uuid is settled); R6: a SETTLED carry is the thing to fetch. | `apply_turn` - Held ResolvedRef (key = live id) is settled: never re-clarified; a refine that does not name the kind keeps it; Lookup uses held refs. |
| 522, 528 | R-a hand pass 6 (17 Sep): re-roster on a domain switch only where the domain must narrow to one. | `apply_turn` - refines_held with a changed domain re-asks only when the new domain is single-subject and several refs are held (which_held), else keeps refs. |

### turn/pending.py

| line | ruling | PV2 handling |
|---|---|---|
| 29 | Hand pass 9 D3 (20 Sep): tier_pick roster stays answerable across as many picks as made; only a message typing a fresh entity retires it. | `apply_turn` - pending of kind tier-pick persists across picks (v1_open_question) and clears on new_topic. |
| 117 | 26 Sep ~07:40Z: ranked-list option rows have no name; idx is the printed rank. | `apply_turn` - last_rows Option has key/label: row_pick uses printed rank; label is the display name, fine. |
| 222, 231, 241 | QUICK-REPLY-NAMES (6 Oct, prod incident): quick-reply buttons only when EVERY title is a fixed short option (Meta cuts titles at 20 chars). | `clarify_copy` - ClarifyAct options go as numbered text; buttons only for fixed options (yes/no). |

### turn/plan.py

| line | ruling | PV2 handling |
|---|---|---|
| 69 | #1118 port (24 Sep): task_pick not ported. | `bridge` - Plan stage. |

### turn/policy.py

| line | ruling | PV2 handling |
|---|---|---|
| 41 | Roster cap (20 Sep). | `bridge` - Policy. |

### turn/policy_rows.py

| line | ruling | PV2 handling |
|---|---|---|
| 9 | FROZEN SEED DATA (16 Sep captain): any change goes through a migration. | `bridge` - Policy rows unchanged. |
| 18, 83, 87, 107, 188 | RECORD_KEY_KIND (21 Sep hand pass 12 round 2 Group C); S6b/S12: narrow_by_tier, list_all and product token on a promotion ask. | `bridge` - Policy rows, run after the verdict. |

### turn/profile_facts.py

| line | ruling | PV2 handling |
|---|---|---|
| 33 | Q7: a tally looks over a COUNT of closed episodes, never a date. | `bridge` - Profile facts feed CONTACT line. |
| 485, 518 | Q6 ruling: a parser profile_statement is validated against the vocabulary; a key over its cap is a 422. | `parser v1 only` - PV2 Ask schema has no profile_statement. Gap: learned-by-statement facts (usual brands) lose their write path in v2; flag for CORE or accept v1 only. |
| 600 | Hand pass 10 (21 Sep): a fresh promotion ask re-opens the tier roster rather than settling on a previously picked tier; the chat-side tier write was removed. | `apply_turn` - Same as engine.py:5741: tier is never carried into a new ask. |
| 691 | A fact from 400 days ago is rendered exactly like today's; the DATE tells it is old. | `bridge` - Profile render. |

### turn/question.py

| line | ruling | PV2 handling |
|---|---|---|
| 28 | No-paging, full-counts ruling means the bot never asks how many to show; first question of that kind is the trigger to build it. | `apply_turn` - HOW_MANY_TO_SHOW stays a v1 pending (v1_open_question) tonight. |
| 99 | LOWSTOCK-SEMANTIC Q4: `about` names the ask, `owed` the field, numbered options ride as pick_one. | `clarify_copy` - Required-field question shape: numbered options, no keys. |
| 181 | Round 4 hand test: '1' after an escalation of a product went out is the product answered again; positions keep a casual '1' out of idle chat. | `apply_turn` - answers_bot_question with no pending and held product = MISS (re-ask with real options), never idle chat. |

### turn/reconcile.py

| line | ruling | PV2 handling |
|---|---|---|
| 73 | F1b siblings ruling 6: several ambiguous tokens queued, not dropped. | `apply_turn` - See apply.py:895. |

### turn/refer.py

| line | ruling | PV2 handling |
|---|---|---|
| 1 | CUSTOMER-ASKS-REFER-ONLY (1 Oct): the ONE way a composer refers. | `bridge` - Compose. |

### turn/route.py

| line | ruling | PV2 handling |
|---|---|---|
| 30 | Captain 20 Sep: main has no branch_kind. | `bridge` - Route. |

### turn/state.py

| line | ruling | PV2 handling |
|---|---|---|
| 4 | #1118 port (24 Sep). | `bridge` - State. |
| 31 | Ruling 1: the parser owns quantity; a CARRIED row keeps its quantity but only a row this message named prints it. | `apply_turn` - Gap: Entity has no quantity; held ResolvedRef would need qty. Flag for CORE/CARRY. |
| 162 | No paging (26 Sep): set_page is its own slot, cleared by a topic reset. | `apply_turn` - how-many carry cleared on new_topic. |
| 166 | Round 4 R5 (PR #833): the ask a clarify question was about is held so the answer ('tap' after 'tap or wash basin?') applies to it. | `apply_turn` - PendingQuestion.kind clarify_<about> + options: the answer's payload sets the thing the clarify asked about. |
| 182 | REPORT-ENGINE (4 Oct): the route args of the last sales ranking that RAN are held so ranking_refine re-runs it with only the changed keys. | `reports` - Held ReportSpec; refines_held edits only changed fields. |
| 211 | S6 (16 Sep): chatbot_stock_allowed is a CRM fact, default ON. | `bridge` - Profile. |
| 222 | 26 Sep F1: a dealer ask cannot have escalation, contact point is the salesperson. | `bridge` - Profile. |
| 255 | F8 ruling 2: staff (office tier) is the one check every bot-initiated escalation offer reads. | `apply_turn` - OfferHandover / HandOver offers must check profile.tier != staff (policy), like the v1 composer. |
| 265 | SO-PO-REF Q4: the audience decides what PO means; non-staff po_refs = their own PO number. | `bridge` - Profile. |

### turn/sub_asks.py

| line | ruling | PV2 handling |
|---|---|---|
| 1, 8, 72 | MULTI-ASK-PLAN S4 (owner card 9 Oct): the per-ask runner answers each ask in order, nothing unasked, in the asker's language, no cap. | `apply_turn` - Ask list answered in order; the last ask's held is held next turn, earlier ones go on topics. |
| 10, 12, 315 | Q1(b)/Q2: a typed code with variants and no family asks back once (sub_ask_scope) in a multi-ask turn only; a one-ask turn keeps today's path. | `apply_turn` - Ambiguity only blocks multi-ask runs with a ClarifyAct (which one / all N); one-ask follows the normal v1 path. |
| 57 | R-LASTIN (9 Oct): 'last in' is the last SPO line's delivery date everywhere. | `bridge` - Formatter/tool choice. |
| 319 | 12 Oct: an exact variant code (SRTWT7445-LV) is answered as typed; only a family head or bare prefix asks 'only this or all N?'. | `resolve` - Exact code in held -> no clarify; family head/prefix (non-exact) -> clarify with real candidates. |

### turn/tail.py

| line | ruling | PV2 handling |
|---|---|---|
| 22, 53 | QUOTED-REPLY R4 and AC-QR-26 (7 Oct): a quoted answer that opened its own follow-up replaces the live picker and task; otherwise the live open question and task are restored. | `apply_turn` - See engine.py:1178: quoted-turn handling in apply_turn is needed for the same write-back rules. |

### turn/task.py

| line | ruling | PV2 handling |
|---|---|---|
| 3, 4 | #1118 port (24 Sep) ruling for chatbot-stock-ask-v2. | `bridge` - Task. |
| 40 | 26 Sep F1: a dealer's stock ask ends in 'Please refer to your salesman.', never an escalation offer. | `bridge` - Task. |
| 57, 396 | Round 6 ruling 2 (console test round 4): point form, one numbered line per product 'How many units for each?'. | `bridge` - Task question text. |
| 308 | 26 Sep F2 'okay': one bare number after a question about several products applies to each product still owed. | `apply_turn` - v1_open_question stock task passes through; a bare number answers it (not a row_pick). |
| 322, 623, 1193 | STOCK-QTY-PARTIAL-FILL R1 (6 Oct): lines answered now are fetched alone, answered earlier ones are not refetched, a restated line is answered again. | `bridge` - Task logic. |
| 832, 848 | ROGUE-CONVO-REVIEW (6 Oct): a bare number tagged as product matches its code family via the resolver's substring probe; a word ('new') inside half the family's codes names none. | `resolve` - v1 only for the substring probe (PV2 forbids it): PV2 treats a non-exact number as unresolved and clarifies with real candidates. |
| 859, 876 | 26 Sep round 3: a which-one list is numbered like the other pickers; the list is not sticky (round 5), once one is picked it is closed. | `apply_turn` - Pick clears the pending; numbered options in clarify copy. |

### turn_runtime.py

| line | ruling | PV2 handling |
|---|---|---|
| 103 | 8 Sep: granted_reveals are probed at all only when granted. | `bridge` - Access. |
| 442 | S6: contact off = stock denied; an unknown contact has nobody to have switched it. | `bridge` - Profile. |
| 552, 896 | 6 Oct: the one brand-identity rule is exact name or code, never spelling or containment; a misspelt word must reach the resolver and be said back. | `resolve` - Matches PV2: brand key accepted only if in the pinned index and live; misspelt word -> key null, phrase logged, clarify with real brands. |
| 1851, 1981 | 22-23 Sep R7/R9 (AC-EQ-16/17/19): escalation team chain: accepted offer's team, this turn's domain team, ...; R7 supersedes R6. | `bridge` - Escalation routing. |
| 2031 | AC-1703 tail (20 Sep): a decline over a non-escalation offer. | `apply_turn` - See tail/outcome.py:199. |
| 2111 | STOCK-FILL-PREDICATE: a carried non-product row the engine handed over may resolve on its own but its miss is not said back. | `apply_turn` - Carried refs: miss never said back. |
| 2350 | Hand pass 12 R8 (turn 7c39e638): a counted described set (positive qualifying_total) is not a guess. | `bridge` - Resolver payload predicate. |
| 2431 | so_status scope rule (a link, or a linked debtor code in same company). | `bridge` - Lane. |
| 2566 | F1a ruling 7: a brand-hinted token matching a LIVE brand (exact, case-insensitive) resolves inside the chatbot, never through the shared resolver. | `resolve` - Same as PV2 exact brand key. |
| 2804 | Captain 16 Sep (browser pass 3 turn 10): customer names are filled for compatible rows. | `bridge` - Resolver payload. |
| 3209 | #1118 port (24 Sep). | `bridge` - Resolver payload. |
| 3303 | 6 Oct: '9031 x 3' is that code's quantity (code_or_number). | `apply_turn` - Quantity with a code; same quantity gap as state.py:31. |
| 3420, 3438, 3605 | Hand pass 12 round 2 Group C / round 4 R4 (ruling 5): whether this message's own entity IS the record key is decided by what the RESOLVER placed its token as, not the parser's hint. | `resolve` - Resolver decides the kind (ResolvedRef.kind from the row). |
| 3592 | Slice R3 (20 Sep): the resolver's tier_gate output drives has/no promotion stamps. | `bridge` - Resolver payload. |
| 3680 | Round 4 S6 ruling 13: a typed brand off the list ends the carry. | `apply_turn` - Brand not in index -> key null -> clarify; the brand kind is dropped from held when replaced. |
| 3820, 5531 | STOCK-FILL-PREDICATE (6 Oct, 17:50, #1501 principle: no claim without a lookup): a set predicate exists only for a set question with no specific product; a miss is claimed only when a tool actually ran. | `apply_turn` - Truthful replies: ClarifyAct/Lookup never claim 'not found' without a lookup; no fabricated spec. |
| 3880, 3891 | Hand pass 12 round 3 R6 / round 4 R8: a stock/inventory tool must never be called with zero filters when the message named entities and none placed. | `apply_turn` - Unresolved entity (resolution none/ambiguous) -> ClarifyAct, never an unfiltered Lookup. |
| 4011 | Hand pass 12 round 2 Group C: a combined (REFINE) call that MISSED reruns exactly once on the record key alone. | `bridge` - Fetch rerun. |
| 4194 | ROGUE-CONVO-REVIEW Q4 (6 Oct): a miss on the customer's own product token is a miss, whatever the spec tier read off the words around it. | `resolve` - Exact product code only; a description line with a code inside is one unresolved phrase. |
| 4241 | No paging: the carry exists only for the answer to 'how many should I show?'; cleared next turn. | `apply_turn` - v1_open_question how-many passthrough; cleared on next turn. |
| 4606 | CARRY-ATTACH (13 Oct prod; owner: 'the parser should be able to understand'): 'Srt8313' after 'Srt8308 drawing' is the same question about a new product; the settled attachment type stays on the focus. | `apply_turn` - refines_held: new product entity (op set) with refers_to_held false must KEEP held attachment_type and domain; product replaced in place (which_held does NOT fire because the kind is replaced, not added). Must be in the replay tables. |
| 4971 | Hand pass 11 defect 2: a RESOLVED attachment_type row with is_certificate routes the escalation to purchasing_certification/general_enquiries deterministically. | `resolve` - Uses the resolved attachment type's data flag, no regex on text; PV2 resolve_attachment_type gives the same row. |
| 5526 | The composer's miss rule (an offer to escalate) must not read a carried token as a miss. | `apply_turn` - Carried tokens never miss; same as 2111. |

### ../ideation_capture_service.py

| line | ruling | PV2 handling |
|---|---|---|
| 62 | Owner: the similar-ideas 'new' option is English in every language ('exact'). | `ideas` - PV2-IDEAS options 1..N + new keep the English 'new' wording; the digit/'new' text check stays for calls without choice_in. |

### ../reports/ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 110 | REPORT-DRILLDOWN (8 Oct): the customer card link a drill-down uses is labelled so the reply says it ('Customers assigned to'). | `reports` - row_pick drill maps onto the customer_agent filter; the existing run_ask label is reused, so the header line shows it. |

### ../reports/datasets/billing_documents.py

| line | ruling | PV2 handling |
|---|---|---|
| 6, 7, 12, 15, 97 | Finance rulings Q12-Q15: invoiced basis uses AutoCount local_net_total signed in one place, cancelled documents excluded, blocks are the sales order type, agent is the document's own. | `reports` - Report engine dataset rules; the ReportSpec basis = invoiced reaches them unchanged through run_ask. |

### ../reports/datasets/chat_asks.py

| line | ruling | PV2 handling |
|---|---|---|
| 221 | Crew rulings Q1/Q3: a text-era caveat is printed once after the era lines. | `reports` - Chat dataset (CHAT_MEASURES) formatter, unchanged. |

### ../reports/datasets/chat_report_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 257 | Crew ruling 10 Oct: no default asker; DEFAULT_ASKERS is the one switch of a chat run. | `reports` - Chat report default, unchanged; report_policy must not add its own asker default. |

### ../reports/datasets/delivery_order_lines.py

| line | ruling | PV2 handling |
|---|---|---|
| 20, 148 | Owner ruling PR #1401 (rule a2): a legacy DO with more than one distinct line total takes each line as the lesser of qty x price x discount and its stored total. | `reports` - Dataset math, unchanged. |

### ../reports/datasets/sales_order_lines.py

| line | ruling | PV2 handling |
|---|---|---|
| 58 | Ruling Q14: both bases group by the sales order type. | `reports` - Dataset, unchanged. |

### ../reports/datasets/sales_order_lines_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 85 | False match: a helper parameter named owner (no ruling). | `v1 only` - Not a ruling. |
| 101 | REPORT-DRILLDOWN (8 Oct): the customer CARD link filters lines of customers whose assigned agent is one of the values, whoever sold the order. | `reports` - Drill filter used by row_pick; reached via run_ask FILTERS. |

### ../reports/definitions/sales_yearly.py

| line | ruling | PV2 handling |
|---|---|---|
| 6, 17 | Owner ruling 26 Sep 06:27 (Lavish) 1 and 2: the screen, Excel and chatbot share one query; the second dataset groups by sales order type (Q14). | `reports` - Shared report kernel; no change. |

## Appendix A: other owner-attributed lines

### analytics/facts.py

| line | ruling | PV2 handling |
|---|---|---|
| 99, 168 | Not a ruling: contact_type is what the owner (a person) reads in analytics; line 168 is a variable named owner. | `v1 only` - Not a ruling. |

### analytics/labeller.py

| line | ruling | PV2 handling |
|---|---|---|
| 12 | Nothing runs before the owner approves taxonomy v1. | `bridge` - Offline labeller gate. |

### answer_bridge.py

| line | ruling | PV2 handling |
|---|---|---|
| 308, 452, 1243 | Round 4 (22 Sep, owner-approved): the brand and agent resolved this turn ride the bridge like the product axis. | `bridge` - Bridge stage. |
| 1727 | LOWSTOCK-FILTER-ASK (hand test 3 Oct): a low stock reply names its filter. | `bridge` - Formatter. |
| 1893 | STOCK-MISS-RENDER (owner report 6 Oct, prod turn f763): the ladder's own rendered text is used on a stock miss. | `bridge` - Formatter. |

### console_service.py

| line | ruling | PV2 handling |
|---|---|---|
| 553 | Owner asked for image/voice file types in the console picker. | `v1 only` - Console UI. |

### contracts.py

| line | ruling | PV2 handling |
|---|---|---|
| 592, 655 | R22 / R16 (owner rounds 9 and 5, 13 Sep): outstanding_scope / outstanding_detail keys; the delivery status the question was asked about. | `bridge` - Verdict keys; bridge must be able to set them. |

### dealer_stock.py

| line | ruling | PV2 handling |
|---|---|---|
| 51 | REFER-ONLY-FIXES (owner console 3 Oct): the routing picker the offer hung off is dropped on a refer-only reply. | `bridge` - Tail. |

### delegate.py

| line | ruling | PV2 handling |
|---|---|---|
| 4, 29 | The OWNER switches each lane on (enabled_lanes / completed branch kinds). | `v1 only` - Deploy switchboard. |

### do_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 26 | Owner Q1(a) 2 Oct: at most 31 days for a DO list, both ends included. | `bridge` - Fetch rule. |

### engine.py

| line | ruling | PV2 handling |
|---|---|---|
| 335, 397, 898 | PR #1353 fix rounds 1-3 (owner hand test and retests, v48, 29 Sep): 'promotion for srtwc286' -> '1' -> '2' and the Office files re-sent; a bare pick reads in code. | `apply_turn` - Pick clears the pending once applied so a following '2' is evaluated against last_rows, not the old pending; no resend. |
| 432, 441, 778 | AVAIL-MODE-REPLIES Q4(b), Q2(a), v2 note 2 (2-3 Oct): only the explicit 'all' is refused; a code named twice asks for the mode; '<qty> of <option>' over a stock pick. | `apply_turn` - Quantity-on-pick over a stock option needs the qty field (see turn/state.py:31 gap); v1 open-question passthrough tonight. |
| 606, 815 | AVAIL-MODE-REPLIES / hand test 3 Oct: an availability-access stock reply, and 'our picker mechanism is the same'. | `bridge` - Stock pick path. |
| 631 | STOCK-MISS-RENDER (6 Oct owner report): 'Srt 364-hp x 1 stock' miss render. | `bridge` - Formatter. |
| 2026 | Pending owner confirmation: linked dealer's top selling never reaches the generic customer resolver. | `resolve` - Same scope as engine.py:2025. |
| 2372, 2471, 2490, 2491, 2566, 2599, 2779, 2787, 2875, 2890 | Owner retests of top selling rounds 4-9 (27-28 Sep, PR #1273): narrowing words, label picks, year is never a rank, one entity carrying several things, 'salesman' variants, '1'/'2' under 'By quantity or by amount?' binds to that question. | `reports` - Ranking follow-ups edit the held ReportSpec; the option key answers the metric/basis question; a year is a Period, never top_n. |
| 2895, 4859 | Owner held-state rule: a new intent always wins; inside a ranking the message is judged for what it narrows. | `apply_turn` - A new ask (new_topic) always beats the held state; refines edits. |
| 3112 | Owner ask 5 Oct: the CRM decides who the AI replies to; a blocked contact gets no reply. | `v1 only` - Pre-parse gate. |
| 4720, 4731, 3673 | PR #833 rounds 7-8 (owner): a brand the last set answer offered; every descriptor the parser placed. | `apply_turn` - A brand named after a set answer refines the held set (add brand), not a new topic. |
| 4925 | Hand test 3 Oct: over an open outstanding summary 'find all my...' reads as a refinement. | `apply_turn` - refines_held on the held outstanding report. |
| 5217, 6821 | W4 (hand test round 2): a page of a carried set is answered off the carry; a set listed only in part is carried. | `apply_turn` - Set carry cleared on new_topic; kept for the how-many answer only. |
| 5282 | Owner retest 27 Sep: read off the FOCUS, not the verdict. | `bridge` - Engine stage. |
| 5765 | Fix lane round 3 R1 (28 Sep): the parser's reading of a first reply. | `bridge` - Engine stage. |
| 5962, 6020 | MULTI-ASK-PLAN round 6 and S4 (9 Oct card): a message with two or more asks answers each. | `apply_turn` - asks list answered in order. |
| 6205 | PROMO-SUBJECT-FILTER (hand test): the promotion tier ask's subject filter. | `bridge` - Lane. |
| 6430, 10133 | Round 4 owner-approved (22 Sep): this turn's own resolved brand rides. | `bridge` - Bridge. |
| 6673 | Retest of round 4 R5: the carried window narrowed. | `apply_turn` - period set replaces the held period. |
| 6729 | PR #833 owner hand test item 6: offer_only for sets listed in part. | `bridge` - Engine. |
| 6844 | ATTACH-SET-BRAND-FOLLOWUP round 2 (7 Oct): a brand follow-up after an attachment set keeps the document type. | `apply_turn` - refines_held with brand add keeps attachment_type. |
| 6906, 7435 | Hand test 26 Sep slices 1-2: a reply reads what it stated about the product. | `bridge` - Engine. |
| 6963 | Owner retest 27 Sep: the lane asked a question over a ranking. | `apply_turn` - Pending kept while the ranking question is open. |
| 7287 | Owner correction 21 Sep (hand pass 12 round 3): reverts a hand pass 12 round 2 change. | `bridge` - Engine. |
| 7655, 9589, 9960, 9987 | Hand test 9 Oct: a file whose host is off the link list is sent as an attachment; the lane says why some did not go as links. | `bridge` - Tail. |
| 9091, 9095, 9231 | OWNER switches lanes on in settings (chatbot_completed_lanes). | `v1 only` - Switchboard. |
| 10029 | Owner console: the ideate lane's pointer wins over the turn-start value. | `ideas` - Ideation pointer unchanged. |

### file_links.py

| line | ruling | PV2 handling |
|---|---|---|
| 10, 39 | Owner Q5(a), Q2(a): both rows show the same link; links never expire by default. | `bridge` - Tail. |

### head/grounding.py

| line | ruling | PV2 handling |
|---|---|---|
| 3 | PR #833 round 8 (27 Sep): grounding of the 'is it the parser' complaint. | `parser v1 only` - Grounding is v1 parser-side; PV2 relies on the index. |
| 417 | 6 Oct: canonical_code is always the taxonomy value, as the owner listed them. | `parser v1 only` - PV2 uses index keys; same data. |

### head/parser.py

| line | ruling | PV2 handling |
|---|---|---|
| 172, 268 | PR #833 round 8 / fix lane round 4 (27 Sep): 'cold selling', 'least sold' ranking direction. | `parser v1 only` - PV2 Sort asc/desc in ReportSpec. |
| 377 | The owner edits the domain set through the Chatbot settings (chatbot_domains). | `parser v1 only` - PV2 index DOMAINS from chatbot_domains supported. |
| 441, 886 | PR #1247 round 8 (26 Sep console test): fifth and sixth verdict keys. | `parser v1 only` - Bridge emits. |

### label_catalog.py

| line | ruling | PV2 handling |
|---|---|---|
| 79, 80, 110 | Owner gave en/zh wording; ms flagged for owner confirmation (PR #1510, #1533). | `bridge` - Labels. |
| 85 | AVAIL-RENDER (hand test 6 Oct): the incoming line says the stock. | `bridge` - Labels. |
| 99, 777 | DISCONTINUED-ASK Q4(a) and #1448 Q3 (8 Oct): the status line both ways; 'Unknown'/'Expired' stay as printed. | `bridge` - Labels. |
| 122, 165 | Owner Q3: PO / SO / SPO / GR stay as printed. | `bridge` - Labels. |
| 680, 724 | MULTI-ASK-PLAN S4 (9 Oct card) and round 6: the per-ask runner's labels. | `bridge` - Labels. |

### lanes/business/__init__.py

| line | ruling | PV2 handling |
|---|---|---|
| 239, 2592 | LOWSTOCK-FILTER-ASK (hand test 3 Oct): every low stock reply names its filter. | `reports` - Lane formatter. |
| 456 | The owner can edit canned replies through copy.render. | `bridge` - Copy. |
| 539, 571, 2110, 2140 | Owner retests 27 Sep: a matched-nothing word keeps the ranking and says so; a word naming a customer AND a sales agent is asked; inside a ranking a word the ranking cannot narrow by is said in one line. | `reports` - Clarify never guesses (customer vs sales agent) -> ClarifyAct with options; say-so line for non-narrowable words. |
| 846, 849 | R21 (owner round 8, 13 Sep): clarify names BOTH subjects, not just the customer. | `clarify_copy` - Names both subjects in the question. |
| 1056 | D9 (8 Sep console pass): a document tool that does not answer. | `bridge` - Lane. |
| 1431 | CHATBOT-SELFREF-SCOPE hand test (30 Sep, Mr Loo): a contact with linked accounts and a self-reference. | `apply_turn` - See __init__.py:995. |
| 2386, 2412 | Hand test 8 Oct: a promotion in several companies is one promotion; PROMO-SUBJECT-FILTER: a picked level with nothing for the subject. | `bridge` - Lane. |

### lanes/business/answer.py

| line | ruling | PV2 handling |
|---|---|---|
| 932, 953, 1041, 1403, 2533 | Hand pass 11 / console pass 4 / D2-D3 12 Sep owner findings: probe row printing, no 'I have attached' sentence, one field per line per row, ETA resolves to the family. | `bridge` - Formatter. |
| 1521 | Owner's wording: stock is on order from the supplier when any row is a PO line. | `bridge` - Formatter. |
| 2913, 2996, 3009, 3088, 3352 | PR #833 owner tests: set header look, 'Other brands with stock' phrase, 'so it got match or not?', 'Could not find incoming for category'. | `bridge` - Formatter; consistent with truthful replies. |
| 4276, 4278 | Owner-approved miss mock for an outstanding ask. | `bridge` - Formatter. |
| 4502, 4511, 4517, 4518, 4522, 4524 | False/ambient matches: variable named owners (uuid ownership), no ruling. | `v1 only` - Not a ruling. |
| 4850 | Owner reversed the earlier has-incoming split. | `bridge` - Formatter. |

### lanes/business/chat_report.py

| line | ruling | PV2 handling |
|---|---|---|
| 233 | The owner's vocabulary (class label, synonyms) is matched exactly, every category with that label. | `resolve` - Exact label; aligned with PV2 aliases being model-side only. |

### lanes/business/fetch.py

| line | ruling | PV2 handling |
|---|---|---|
| 614, 3374, 3414 | PR #833 rounds 3-8 owner tests ('cert', 'message too long already', 'answer should look exactly...'). | `bridge` - Formatter. |
| 925, 932, 2711 | Owner retest 27 Sep: '1' to 'customer or sales agent?' picks that option; a ranking with no sales is an ANSWER. | `apply_turn` - answers_bot_question with option_key of the clarify (about=entity): customer vs sales agent. |
| 1400 | An owner editing chatbot_domains.tools must not make it write. | `v1 only` - Admin. |
| 1910, 1911, 1924 | DISCONTINUED-ASK owner Q1(a)/Q4(a), hand test 8 Oct: warning/tick lines. | `bridge` - Formatter. |
| 1961 | 12 Sep owner finding: 'and N more' left no way to see the rest. | `bridge` - Formatter. |
| 2514 | SELFREF hand test 30 Sep: a rendered report with the self-reference. | `bridge` - Fetch. |
| 2652 | Hand test 3 Oct: _miss_triggers. | `bridge` - Fetch. |
| 2876, 2896 | PHOTO-SEND-CAP (round 1 rule 1, kept by owner round 2); a row with no File Name (promotion, hand test 9 Oct). | `bridge` - Fetch. |
| 3612, 3627 | Hand test 26 Sep slice 1; R-d hand pass 7: result type decides. | `bridge` - Fetch. |

### lanes/business/gate.py

| line | ruling | PV2 handling |
|---|---|---|
| 110 | The owner accepted the access matrix. | `bridge` - Gate. |
| 453 | D10 (8 Sep): 'srtwc8610-sh hav incoming?'. | `bridge` - Gate. |
| 994, 1017, 1369 | R16/R18 (owner round 5, 13 Sep): uuids only a WAREHOUSE word found; a warehouse word is never a customer. | `resolve` - Index kind warehouse separates; a warehouse never becomes a customer candidate. |

### lanes/business/low_stock_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 280, 446 | Hand test 3 Oct: persisted frame; a 'brand' no category carries is not a brand. | `reports` - Exact brand-in-category check, no fuzzy. |

### lanes/business/miss_suggest.py

| line | ruling | PV2 handling |
|---|---|---|
| 138 | D10 (8 Sep): tokens gate.py marked as probed. | `bridge` - Miss arm. |
| 624, 625, 631, 1032, 1036, 1056, 1059, 1060, 1061, 1063, 1066, 1067 | False matches: variables named owner/owners (attribution census), no ruling. | `v1 only` - Not a ruling. |

### lanes/business/pickers.py

| line | ruling | PV2 handling |
|---|---|---|
| 230 | Hand pass 3 rows 1 and 7: product roster without stamps. | `bridge` - Picker stamp. |

### lanes/business/predicate.py

| line | ruling | PV2 handling |
|---|---|---|
| 249, 309, 367 | R28 console run 10; PR #833 owner tests: attachment_type dropped by the head, 'kitchen sink 1.2mm thickness', '65502 eta'. | `resolve` - Spec numbers matched against data; attribute null -> candidates or truthful none. |

### lanes/business/resolve_gate.py

| line | ruling | PV2 handling |
|---|---|---|
| 74, 1647, 1674, 1677 | Hand pass 3 rows 1 and 7: has/no-PO stamps on the roster, not the roster replaced. | `bridge` - Picker stamp. |
| 78, 1539 | R20 (owner round 7, 13 Sep): an OUTSTANDING ask does not probe. | `bridge` - Gate. |
| 213, 233 | Console pass 4 item F: 'srtwc287' bare typed product. | `resolve` - Exact code resolution. |
| 322 | Console pass 5 item B2. | `bridge` - Gate. |
| 529 | Two RECORD hints the owner named. | `bridge` - Gate. |
| 680 | Hand test 8 Oct: a pick's text never reaches the resolver as a query. | `apply_turn` - answers_bot_question applies the option payload; the pick text is never re-resolved. |
| 824 | ATTACH-SET-BRAND-FOLLOWUP round 2 (7 Oct). | `apply_turn` - See engine.py:6844. |
| 1624 | Hand pass 2 item 2: roster with no stamps. | `bridge` - Picker stamp. |

### lanes/business/services.py

| line | ruling | PV2 handling |
|---|---|---|
| 198, 461, 572, 690, 728 | Owner: has-DO / no-DO stamps, R19b customer rows (13 Sep), dealer scope, top-selling words, linked accounts. | `bridge` - Services. |

### lanes/escalation.py

| line | ruling | PV2 handling |
|---|---|---|
| 85, 191, 254 | Owner-approved (22 Sep round 4): one WhatsApp message per handover; six-rung ladder; lane_parse_output. | `bridge` - Lane. |
| 600, 604 | The owner previews the draw and routing in the console. | `v1 only` - Console preview. |
| 797, 1212, 918, 1007, 1019, 1098, 1119 | Owner 27-28 Sep tests: misspelt escalation, stale offer consumed (D1), purchasing table, brand member routing. | `apply_turn` - Escalation target from index team_key; a stale offer is not accepted (offer pending must exist; MISS otherwise). |

### lanes/escalation_services.py

| line | ruling | PV2 handling |
|---|---|---|
| 21, 261, 291 | Owner adds out_of_scope to completed lanes; 27 Sep rule: every Mocha company item and Mocha-brand goes to Mocha. | `bridge` - Lane. |

### order_list.py

| line | ruling | PV2 handling |
|---|---|---|
| 2, 273, 289 | Fix lane round 7 / CHATBOT-EMPTY-ROUTE-PICK (30 Sep) / REFER-ONLY-FIXES (3 Oct): the words that move the list, rows only, lines under a picker header. | `bridge` - Tail. |

### quick_reply_guard.py

| line | ruling | PV2 handling |
|---|---|---|
| 8 | The owner's rule (QUICK-REPLY-NAMES) is applied where each set is produced. | `clarify_copy` - Guard on buttons. |

### refer_asks.py

| line | ruling | PV2 handling |
|---|---|---|
| 179 | Hand test 3 Oct: ETA refer-ask line form. | `bridge` - Tail. |

### required_fields.py

| line | ruling | PV2 handling |
|---|---|---|
| 1 | LOWSTOCK-FILTER-ASK owner Q2: collect the REQUIRED fields before running. | `reports` - Low stock required-field helper. |

### session_state.py

| line | ruling | PV2 handling |
|---|---|---|
| 74 | False match: 'its owner speaks' (row owner), no ruling. | `v1 only` - Not a ruling. |

### tail/outcome.py

| line | ruling | PV2 handling |
|---|---|---|
| 40 | False match: roster owners (comment), no ruling. | `v1 only` - Not a ruling. |

### turn/apply.py

| line | ruling | PV2 handling |
|---|---|---|
| 148, 212, 3395 | Owner report 8 Sep: an escalation confirmation, and a message that names its own question. | `apply_turn` - Acceptance only via answers_bot_question or escalation.requested. |
| 160 | #1323 case 2 (hand test 28 Sep): 'mocha brand' over an escalation. | `apply_turn` - Brand add with escalation open is not acceptance. |
| 254, 3612 | Owner turns 8 Sep: 'delivery to hanlim' came back as an order shape; the parser decides. | `apply_turn` - refines_held domain change; no word reading. |
| 428 | Measured on the owner's own turns (16 Sep): 'i want to see...' evicts nothing. | `apply_turn` - Refinement never evicts other kinds. |
| 1136 | Defect 3 (hand pass 6, 17 Sep): the window is a focus axis. | `apply_turn` - period is a held axis; new_topic drops, refines_held keeps. |
| 1223, 1285, 1307, 1362, 1404, 1440, 1465, 1477, 1496, 1513, 1544, 1583 | Owner retests 27-28 Sep (rounds 4-7): a message with a ... , '1'/'2' under the metric question is that option, 'water closet only', no repeat of the line, ranking is a new ask, a word the last reply said it does not know is said once, answer binds to that question, one short question never the escalation, order reports a ranking hands filters to. | `apply_turn` - Ranking: pending metric/basis question answered by option_key; refines edit field by field; unknown word logged once; never escalates. |
| 2017 | Defect 5 (hand pass 6): idle chat never fetches, extended to the offer-open case. | `apply_turn` - small_talk, state unchanged. |
| 2123, 2130 | Console pass 4 item E / defect E: scope_intent broaden on a casual turn. | `apply_turn` - op clear only on a non-casual refines. |
| 2279 | PR #1247 round 8 item 4 (owner rows 9 and 11): a sibling the message names. | `apply_turn` - Sibling add. |
| 2416 | Hand pass 2 item 11: every domain the ask was asked for. | `apply_turn` - One ask per domain. |
| 2506, 2534, 3052, 3136, 3256 | Owner hand test 26 Sep slices 5-6, round 8 row 12, Q4(b), v2 note 3: 'how about 100?' as demand_qty; one bare number after a stock check; pick over several lists. | `apply_turn` - Bare number after stock = quantity, not a pick; needs qty field (gap). |
| 2684 | Round 6 read the lines off the message's shape; the owner asked the parser to. | `bridge` - Parser-side. |
| 2817 | PR #1247 round 8: the parser's own numbered-lines reading. | `bridge` - Task. |
| 3362 | Defect 2 (hand pass 6): a roster every option of which is the same. | `apply_turn` - A single-option roster is a yes/no pending. |
| 3673 | Round 7 PR #833 item 6. | `apply_turn` - See engine.py:4720. |
| 3709 | W4 round 2: a set already listed in part is carried. | `apply_turn` - Set carry. |

### turn/compose.py

| line | ruling | PV2 handling |
|---|---|---|
| 186, 935 | Round 4 owner-approved (22 Sep): the same one-source brand idiom. | `bridge` - Compose. |
| 551, 567, 875, 892, 1043 | W6, R-d, R-f (hand pass 7, 19 Sep), hand pass 2 item 8, journey chinchun-x-only-customer-survives. | `bridge` - Compose. |
| 1083, 1143 | R8 (retest round 4): the longest menu a ranking conversation prints; no menu inside a ranking. | `clarify_copy` - Cap option count in report clarify copy. |

### turn/decide.py

| line | ruling | PV2 handling |
|---|---|---|
| 2 | Owner go 18 Sep: 'we have been fixing bugs in circles'. | `apply_turn` - The subject table is the single discriminator. |
| 260 | Defect 4 (hand pass 6): a YES/NO offer with one team. | `apply_turn` - yes/no options. |
| 386, 458, 559 | R24 (owner round 9b), R-b (hand pass 6): a pronoun the message carries. | `apply_turn` - refers_to_held on the entity. |

### turn/episode_digest.py

| line | ruling | PV2 handling |
|---|---|---|
| 146, 291, 422 | Owner hand test 28 Sep / round 6 feedback: episode wording and flag. | `bridge` - Episode digest. |

### turn/narrow.py

| line | ruling | PV2 handling |
|---|---|---|
| 24, 119 | B1, R-g (hand pass 7, 19 Sep): every roster has a ledger family label; shared LABELS. | `bridge` - Narrow. |
| 288, 303 | Hand pass 2 items 10 and 6: a kind answered by a numbered pick; 'Outstsnding DO for'. | `apply_turn` - Pick applies option payload. |
| 322, 339 | Console run 3: owner typed SRTWT7445 which exists and the report ran; owner's journey. | `bridge` - Narrow. |
| 358, 380 | R-a / R-e (hand pass 6/7): narrow_to_code re-roster; 'ETA srtwc8518-SH'. | `apply_turn` - See narrow.py:522. |

### turn/pending.py

| line | ruling | PV2 handling |
|---|---|---|
| 275 | WhatsApp shows at most three reply buttons (retest 27 Sep). | `clarify_copy` - At most three buttons else text list. |

### turn/plan.py

| line | ruling | PV2 handling |
|---|---|---|
| 67 | Hand pass 2 item 10. | `bridge` - Plan. |

### turn/policy_rows.py

| line | ruling | PV2 handling |
|---|---|---|
| 184, 315 | S6d (hand pass 2 items 6, 3): order product resolves; cost answer LISTS every variant. | `bridge` - Policy rows. |

### turn/question.py

| line | ruling | PV2 handling |
|---|---|---|
| 161 | CHATBOT-EMPTY-ROUTE-PICK (30 Sep, parser v37): 'Zhin heng...' customer pick with empty route. | `apply_turn` - Pick on empty route. |

### turn/task.py

| line | ruling | PV2 handling |
|---|---|---|
| 35, 571, 598, 679, 912, 394 | Hand test 26 Sep slices 1-5 (T1-T14): the answered check is kept; a named product the task...; 'how about 100?'. | `apply_turn` - Task passes through as v1_open_question. |
| 44, 310 | PR #1247 round 6: numbered-lines verdict key; 'they can just say one number'. | `apply_turn` - Bare number applies to each owed. |
| 843, 874, 960, 1211 | AVAIL-PICKER (owner trace 5 Oct), family pick, v2 note 3 Q3(b), answered check KEPT. | `apply_turn` - Task passthrough. |

### turn_runtime.py

| line | ruling | PV2 handling |
|---|---|---|
| 119, 1245, 2020 | Round 4 owner-approved (22 Sep): brand half of the same acceptance. | `bridge` - Bridge. |
| 385 | PR #1247 round 8: 'is the context too less already?'. | `parser v1 only` - Context size; PV2 history 3 exchanges. |
| 901 | Owner's own 27 Sep: a misspelt word must reach the resolver and be said back. | `resolve` - PV2: unknown phrase logged and said back via clarify. |
| 1303, 1345, 1386, 1432, 1463 | PR #833 owner tests: 'why when I say 10', 'tap' after 'tap or wash basin', 'which basin has cert', W4 more of a list. | `apply_turn` - Clarify answer applies to the kind asked; 'cert' as attachment_type. |
| 1495, 1750, 1755 | Owner 27 Sep escalation wording; live testing 19 Sep: domain_hint order for those asks. | `bridge` - Seam. |
| 1957 | Fix lane round 8 (28 Sep): an outstanding ask after a ranking. | `apply_turn` - New report ask. |
| 2187 | RESOLVER-TOKEN-CAP (owner report 6 Oct): a catalogue photo read 19 codes. | `resolve` - Cap tokens. |
| 2234, 3044 | R-g (hand pass 7): resolver's COMPANY per match. | `bridge` - Resolver payload. |
| 2538, 2543 | Hand pass 3 rows 1 and 7: stamps. | `bridge` - Picker stamp. |
| 2630 | R1: the typed word decides there. | `bridge` - Intercept. |
| 3115 | Focus off typed words never reached candidates_by_kind's stamp. | `bridge` - Stamp. |
| 3188, 3195 | Owner round 8 R21 chain; Q3: a sales ranking never borrows the carried window. | `apply_turn` - New ranking: period not borrowed from held (do not copy period across new_topic report). |
| 3262 | AVAIL-MODE-REPLIES Q2(a) (2 Oct): a code named twice in one message. | `bridge` - Stock. |
| 3299 | AVAIL-PICKER (owner trace 5 Oct): a code-like token that is a SUBSTRING of codes. | `resolve` - v1 only for the substring probe; PV2 forbids it (non-exact -> clarify). |
| 3652 | Hand test 3 Oct: '1' / the 'Sales order list' button under a .... | `apply_turn` - Option key pick. |
| 3699, 3710, 4228, 4813 | ATTACH-SET-BRAND-FOLLOWUP (6-7 Oct): the document type the set was listed under. | `apply_turn` - Brand add keeps attachment_type. |
| 3801 | Hand test: 'okay how about all my sales order?'. | `apply_turn` - Domain change keeps customer. |
| 4236, 4266, 4358 | PR #833 item 6 / W4: offer_only; 'cabana' answered '1,136 products'. | `bridge` - Set answer. |
| 5094 | Owner option (2) PR #1435: 'outstanding' keeps the outstanding report. | `bridge` - Lane. |

### ../ideation_capture_replies.py

| line | ruling | PV2 handling |
|---|---|---|
| 5 | An owner edit of the reply prompt wins; the shipped text is the fallback. | `ideas` - Unchanged. |

### ../reports/ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 133 | Owner Q1 (8 Oct): the customers assigned to those agents, whoever sold the order. | `reports` - Drill filter. |

### ../reports/datasets/chat_asks.py

| line | ruling | PV2 handling |
|---|---|---|
| 7 | Scoping through the link blanks the asks the owner most wants to see. | `reports` - Unchanged. |

### ../reports/datasets/chat_report_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 45 | Owner override 10 Oct: unlinked askers are run by default. | `reports` - Unchanged. |

### ../reports/datasets/delivery_order_lines.py

| line | ruling | PV2 handling |
|---|---|---|
| 30 | Where the owner wants it. | `reports` - Unchanged. |

### ../reports/datasets/sales_order_lines_ask.py

| line | ruling | PV2 handling |
|---|---|---|
| 1 | Owner direction 4 Oct: every basis of the chatbot's report ask (REPORT-ENGINE). | `reports` - The ReportSpec basis list must come from this dataset. |
| 92 | False match: owner variable. | `v1 only` - Not a ruling. |

### ../reports/views_service.py

| line | ruling | PV2 handling |
|---|---|---|
| 10, 14, 149 | False matches: record ownership (owner publishes and defaults a view). | `v1 only` - Not a ruling. |
