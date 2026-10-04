# PLAN - Escalation acceptance carries the AGENT (and, round 4, the BRAND) half of routing, not only the team

Status: implemented, awaiting review (small fix track)
Domain: chatbot
Branch: `fix/chatbot-escalation-agent-carry` (worktree `sorento_crm-escalation-agent-carry`)
Base: `origin/main` 2280975f9
UAC: `chatbot-escalation-agent-carry-acceptance-criteria.md`

## Observed (prod transcript, 22 Sep 2026, contact CONTACT O)

1. `incoming container SRTSC07` -> parser routes `domain incoming` -> team `purchasing`,
   agent `incoming_stock_enquiries` (parser prompt line 534). Answer ends with the roster
   offer "Reply with a number to check its incoming, or reply 'yes' to escalate to
   purchasing team." (`lanes/business/answer.py:3930`).
2. `yes` -> escalated. Assigned to Chew Hong, whose only `purchasing` link is
   `(agent general_enquiries, team "Purchasing - Product, Stock Inquiry")`. The correct
   pool is `(agent incoming_stock_enquiries, team purchasing)` = "Purchasing - Packing
   List" / "Purchasing - Incoming" (Lucas, Jereen Tee).

## Cause (read, not guessed)

`/external/next-assignee` resolves a pool by the PAIR `(agent_code, team_code)`
(`app/api/v1/external/next_assignee.py:147-183`). The escalation lane sends
`agent_code = ctx.parse.output.routing.suggested_agent` and `team_code = context_item.team`
(`lanes/escalation.py:1140-1156`).

On the acceptance turn ("yes") the two halves are treated differently:

- **team** is carried: `turn_runtime.lane_parse_output` (`turn_runtime.py:566-572`) takes
  the accepted offer's team, then `pending.team`, then `_prior_suggested_team(session)`,
  then `DEFAULT_SUGGESTED_TEAM`.
- **agent** is NOT carried: a bare "yes" names no domain, so the parser emits
  `suggested_agent: null` (prompt line 544), and `turn_runtime.with_routing_agent_default`
  (`turn_runtime.py:422-441`, called at `engine.py:1399`) stamps
  `DEFAULT_SUGGESTED_AGENT = "general_enquiries"` (`contracts.py:177`). Nothing reads the
  offer or the prior turn.

Result: `(general_enquiries, purchasing)` -> the wrong tier-1 team.

## Fix (one seam)

Carry the agent the way the team is carried, from the same source, and only when the
parser named none THIS turn:

1. **Offer write, NINE fresh mint sites stamped, ONE deliberately not** (the original
   plan named two; implementation and two reviewer rounds found the other seven -
   every place that mints a NEW pending an escalation acceptance can answer stores the
   minting turn's `routing.suggested_agent` on `payload["agent"]`, single-team/top-level
   or per-option beside `payload["team"]`, the hold option always `agent: None`):
   1. `turn/compose.py::_team_pick_question` (single-team top-level payload; multi-team
      per-option) - reached only by a genuine MULTI-domain miss.
   2. `answer_bridge.py::_miss_question`'s did-you-mean/require-specific roster arm
      (combined-member and roster-alone, per-option `escalate_offered` payload).
   3. `answer_bridge.py::_miss_question`'s escalate-catalog arm (company-clarify and bare
      "Yes" branches) - the THIRD mint site, found in implementation: this is the one a
      real SINGLE-domain fetch miss actually goes through (`via_fetched_empty` ->
      `answer_bridge.answer_for` -> `_miss_question`), not `_team_pick_question`.
   4. `answer_bridge.py::_miss_question`'s member-offer arm (top-level payload) -
      reviewer round 1, SHOULD-2.
   5. `engine.py::_question_offered`'s team clarify arm (`_option_payload`'s "team"
      branch, per-option, reads `ctx` directly) - SHOULD-2.
   6. `engine.py::_question_offered`'s company clarify arm (top-level payload) - SHOULD-2.
   7. `engine.py::_question_offered`'s member-offer arm (top-level payload) - SHOULD-2.
   8. `engine.py::_question_offered`'s escalate-catalog arm (top-level payload, the twin
      of `answer_bridge.py`'s own bare-"Yes" branch) - SHOULD-2.
   9. `answer_bridge.py::apply_silent_company_offer` (top-level payload, `raw_team` off
      THIS turn's own routing) - review round 2, item 3, the NINTH fresh mint site,
      missed by round 1.

   Two sites round 2 also touched are NOT in the nine above, on purpose:
   `turn/compose.py::compose`'s roster re-arm is NOT a fresh mint - it re-uses an
   EXISTING carried pending (`dataclasses.replace`, never `.ask()`) - so it is its own
   fix, item 5 below, reading the SAME source as the `team` expression right beside it
   rather than an independent `ctx` read. `answer_bridge.py::_crossdomain_offer_pending`
   is the ONE deliberately-UNSTAMPED site (review round 2, item 3): its team comes off
   the cross-domain RUNG's own render block, never off this turn's routing at all (the
   function takes no `parser`/routing argument to read one from), so there is no "this
   turn's agent" to stamp - it falls through to the default chain exactly as it always
   did, now with a comment saying why rather than a silent gap.
   `Pending` already has `payload: dict`; no dataclass field is added.
2. **Acceptance read.** In `turn_runtime.with_routing_agent_default` (the one seam every
   turn passes before the access read - keep it there so access is checked against the
   carried agent, not the default), fill `routing.suggested_agent` in this order when the
   parser's raw value is empty: `_accepted_pending_agent(pending, verdict)`, then
   `DEFAULT_SUGGESTED_AGENT`. The function needs `pending` (`state_in.pending`, loaded at
   `engine.py:1225`, so it is available at line 1399) as an extra keyword argument; every
   other caller keeps working with it absent. A `session=` keyword and its own
   `_prior_suggested_agent` fallback were part of the first round and REMOVED in review
   round 1 (SHOULD-4): no writer in this codebase ever produces
   `variables.routing.suggested_agent` for a previous turn to carry - unlike the team
   half, which `_prior_suggested_team` reads off a nest a real writer DOES still produce.
3. **Gate** (SHOULD-1, refined in review round 2). `_accepted_pending_agent` only reads a
   pending that is actually ACCEPTABLE as an escalation, the same condition
   `turn/apply.py:691` uses: `pending.kind in OFFER_KINDS or pending.payload.get(
   "escalate_offered") is True`. Without it a plain roster pending with no escalate offer
   attached could still supply an agent while `lane_parse_output`'s own team chain (which
   gates on `OFFER_KINDS` alone for that same read) left the team at its default - the two
   halves of the pair disagreeing. Round 2's own residual finding: "ACCEPTABLE" is not
   "ACCEPTED" - for the `escalate_offered` ROSTER arm specifically (never for a proper
   `OFFER_KINDS` pending, whose team is read unconditionally at the same
   `lane_parse_output` line and so keeps carrying its agent unconditionally too), the
   carry now also requires THIS turn to actually accept - the same signal `decide()`
   itself reads at `turn/decide.py:571-573` (`is_affirmative` or
   `escalation.is_escalation_confirmation`), or a numbered pick landing on one of the
   roster's own options. A non-accepting message over a still-open escalate-offered
   roster no longer supplies an agent while the team stays at its own default too.
4. **Member-option fall-through** (SHOULD-3). A numbered pick that lands on a MEMBER
   option (a combined roster+CS offer's own member half - no `payload["agent"]`, no
   `payload["hold"]`) falls through to the pending's own top-level agent instead of
   returning `None` outright; only the explicit hold option or an option that actually
   names an agent short-circuits the read.
5. **Roster re-arm, one source for both halves** (review round 2, SHOULD-A, BLOCKING).
   `turn/compose.py::compose`'s roster re-arm (a miss over a STILL-OPEN roster that
   already carries an escalate offer) used to read `team=carried.team or teams[0]` for
   the team but `carried.payload.get("agent") or ctx.suggested_agent` for the agent -
   TWO INDEPENDENT sources. A roster CAN carry a team with no agent at all
   (`answer_bridge.py`'s D4 narrower roster, `turn/apply.py`'s narrow ask), and the old
   expression paired a STALE carried team with THIS turn's fresh agent whenever that
   happened - measured: an incoming miss left `team=purchasing` with no agent, and a
   LATER order-domain miss re-armed it as `(order_enquiries, purchasing)`, a pair
   `/external/next-assignee` has no link for. Fixed to read both halves off the SAME
   branch: `"agent": carried.payload.get("agent") if carried.team else
   getattr(ctx, "suggested_agent", None)` - when the team is the roster's OWN
   (`carried.team` truthy), the agent is the roster's own too, carried or not; only when
   the team itself falls to `teams[0]` (this turn's) does the agent follow it.
6. A parser that DID name an agent this turn is never overridden - the carried value only
   fills a null.

Nothing else moves: `lane_parse_output`'s team chain, `_next_assignee_body`, `_sla_body`,
the access check and the prompt are untouched.

## Round 4 (owner-approved scope extension, 22 Sep 2026): the BRAND half

Owner ruling, live: a MOCHA product's escalation must go to Lucas, a SORENTO product's
to Jereen (Packing List tags in prod: Jereen = every brand except mocha, Lucas = mocha).
`/external/next-assignee` already narrows the round-robin pool by `brand_code`
(`app/services/user_service.py:1681-1735`, tagged + untagged, normalised lower-case) -
the escalation lane already SENDS `brand_code = context_item.brand_code`
(`lanes/escalation.py:1140-1156`), resolved by `escalation_context`'s five-rung ladder
(`:190-312`: picked-member row -> company pick -> same-team roster arms -> stated_brand
-> none). The offer turn itself already resolves a brand
(`lanes/business/gate.py:1633`'s `routing_brand`) - nothing carried it to the
ACCEPTANCE turn, the SAME gap the agent had before round 1: `same_team` reads
`session_vars.variables`, a nest the rearch tail never writes, so a bare "yes" after an
incoming/ETA miss reached `escalation_context` with `brand_code: null` and the whole
team rotated (confirmed with a failing test first, per the brief).

Two changes, mirroring the agent carry exactly:

1. **Mint.** The SAME nine sites (plus the roster re-arm) that stamp `payload["agent"]`
   now also stamp `payload["brand_code"]` = the gate's `routing_brand` for THIS turn
   (`None` when the gate has none). Reached differently per site: `answer_bridge.py::
   _miss_question` and `apply_silent_company_offer` already take `gate` as a parameter;
   `engine.py::_question_offered` reads `values["gate"]` (`run_tail`'s own `values` dict,
   already in scope - no new plumbing needed); `turn/compose.py`'s `_team_pick_question`
   and the roster re-arm needed a NEW `TurnContext.routing_brand` field, set at
   construction from the SAME `resolver_payload.get("gate")` the neighbouring
   `resolver_gate=` kwarg already reads. The roster re-arm reuses round 2's own
   one-source rule: `"brand_code": carried.payload.get("brand_code") if carried.team
   else ctx.routing_brand`. `_crossdomain_offer_pending` stays unstamped, same reason as
   the agent - no gate reachable there at all.
2. **Accept.** `_accepted_pending_agent` and a new `_accepted_pending_brand` are now
   both thin wrappers around one shared reader, `_accepted_pending_field(pending,
   verdict, field)` - the gate, the accept check (round 2's member-pick/undeclined-yes
   qualifier) and the per-option/top-level fall-through are IDENTICAL for both fields,
   parameterized only by which payload key is read. `lane_parse_output` writes
   `out["escalation"]["carried_brand"]` the SAME "one flag on ctx.parse.output" idiom
   `preferred_assignee_id`/`company_pick` already use. `escalation_context` gets ONE new
   rung, `elif carried_brand:`, inserted after the roster arms and before
   `stated_brand` - the picked-member and company-pick arms are UNTOUCHED, because a
   SPECIFIC row's own brand must keep outranking a generic carry (AC-1806(d) pins this).

## Round 5 (evidence, 22 Sep 2026): does the brand carry hold for a photo escalation?

Owner asked whether round 4's carry also holds for a `product_attachment` (photo/
drawing request) escalation, not just `incoming` - a different DOMAIN, a different
team (`marketing_product`, not `purchasing`), a different agent
(`general_enquiries`, not `incoming_stock_enquiries`), fetched through a different
tool (`crm_master_product_attachments_list`, not the incoming-stock probe). Two new
engine-replay tests, AC-1807/AC-1808, over a freshly seeded `Marketing - Product`
team (Kia Yee = mocha, Tay Zhi Yang = every other brand - prod's own tagging, Charissa
untagged and not reproduced here since two members already prove the narrowing).

Both went GREEN on the first run - **no fix needed**. The attachment miss takes the
SAME mint site round 1 already found and stamped for the incoming miss:
`answer_bridge.py::_miss_question`'s escalate-catalog arm (the bare-"Yes" branch,
since this journey names one team and one company). Confirmed load-bearing by
temporarily reverting that ONE site's `brand_code` stamp and re-running both tests -
both went red with `brand_code: None` on the wire body, exactly the pre-round-4
symptom, then green again once restored. This is evidence, not a new mint site: the
gate/mint-site coverage round 4 already built covers `product_attachment` for free,
because `gate.py::run_gate`'s own `routing_brand` derivation and `_miss_question`'s
escalate-catalog arm are BOTH domain-agnostic - neither reads `domain_hint` to decide
whether to run.

## Non-goals

- No change to `ESCALATION_TEAMS` / `SUGGESTED_TEAM_OPTIONS` (the dropdown holds
  agent_teams link codes; "Purchasing - Packing List" is reachable under `purchasing` once
  the agent half is right).
- No prompt edit. No new session key. No migration.

## Tests (tester writes first, `tests/chatbot/`)

Pure-python where possible (`with_routing_agent_default` takes dicts); one engine replay
through the stubbed parser to prove the wire body. See the UAC for the list; each AC maps
to one test name.

Run: `SORENTO_ENV_FILE=.env.ci-tests venv/bin/pytest tests/chatbot/<file> -q` from the lane
backend (`sorento_ci` is the private test DB; the primary venv is symlinked in).

## Verification

No screen changes: pytest is the gate. Journey runner / console check not required for a
routing-pair fix that no UI renders; the prod transcript above is the reproduction and the
replay test is its record.
