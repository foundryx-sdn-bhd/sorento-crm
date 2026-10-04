# PLAN - Chatbot growth r1: richer tools, one dialogue state, field reveal, trace

Status: APPROVED by owner 7 Sep 2026 ("good to go"); lane 1 `feat/chatbot-growth-data` IN PROGRESS (A0-A9 + parser reachability + DOMAIN_SPEC + trace persistence + the in-app chatbot console landed; PR #733's Slice D turn-detail drawer and Slice C field reveal merged via `origin/main`). UAC: `chatbot-growth-r1-acceptance-criteria.md`.
Assumed, not picked: the order summary shape is the three-line pipeline (SO outstanding / DO open / delivered); the owner ended the review before choosing, so AC-905b ships that shape and the console pass will show it.
Predecessor: `PLAN-chatbot-turn-engine.md` (parity port, LIVE on prod at 043e2a0be). This plan
grows the engine along the "Growth axes" table that plan names, and stays at the **parity**
stop of the reasoning dial: every answer is built by construction from one tool envelope, no
answer LLM. The owner reaffirmed that on 7 Sep 2026.

## Why

Owner statement, 7 Sep 2026, paraphrased into the four defects it names:

1. **Data breadth.** Specs, SO outstanding, DO open with angles, PO placed, SPO last receipt
   and sellable stock cannot be answered because no read tool exposes them. Foundre's rule
   "no stock, no incoming, but a PO exists: say the PO date and quantity" is unanswerable.
2. **Rigid angle.** A DO question by customer, by transporter, by date or "how many in total"
   is one flat list. The renderer is envelope-agnostic already; the tools do not carry
   `group_by` / `include_summary` uniformly.
3. **Multi-turn understanding goes haywire** around offers, picks, escalation yes/no, and a
   filter change after a result. Cause, measured: carry is decided by TWO writers (the parser
   prompt's "always continue the previous turn" plus ten deterministic rules in
   `head/output_exchange.py`) and dialogue state is five `pending` kinds + an 8-rule
   `dym_offer` ladder + `selection_context` + `picker_*` + `_offer_carry`, each hand-added.
   Nothing ages except member offers (TTL 3).
4. **No way to see why.** `chatbot.turns.trace` records the stages; the console lists rows and
   shows a Lost / Gained / Flags / Parser drift summary. No per-turn view of the carry
   decisions, the tool call, the envelope, or the cross-domain probes.

Deferred by the owner on 7 Sep 2026, with the trigger that reopens each:

| deferred | trigger |
|---|---|
| learning from corrections beyond capture (few-shot retrieval, corpus growth, fine-tune) | this plan's console labelling surface exists AND a measured parse-error class recurs across contacts |
| L2 episodic retrieval ("the one you showed me yesterday") | a measured class of explicit backward references in the corpus that focus TTL cannot serve |
| `agent` strategy per lane, answer LLM | owner changes the dial; never for the business lane under this plan |
| flow builder / rule editor | never; visualisation is a trace viewer, the ladder stays code |

## Measured current state (origin/main 043e2a0be)

- LLM calls per turn: parser (`chatbot_semantic_parser`, strict 26-key schema) and, on
  `low_signal` only, the clarifier. Everything else is Python.
- Business lane: `fetch.select_tool` picks ONE MCP tool by cosine search over the catalog,
  restricted to `CHATBOT_READ_ONLY_TOOLS` (33 names, mirrored from `ToolSpec.read_only` /
  GET in `sorento_crm_mcp/catalog.py`, CI asserts agreement); `fetch.entity_ids_transformer`
  maps typed entities to `product_ids` / `customer_ids` / `transporter_ids` / `shipment_ids`
  and the date window; `fetch.output_structurer` renders the envelope (intro, per-item
  `fields[]`, `summary_items`, `action_links`, `requested_attributes` projection).
- Cross-domain: `lanes/business/answer.py` hard-pairs `inventory` and `incoming` only
  (`answer.py:398-405`): per requested code missing from the primary render, probe the other
  tool, render "But there is INCOMING stock (ETA)..." / "No stock and no incoming for X".
- Memory: `respond_contacts.session_vars` JSONB, 34 allow-listed keys, whole-object
  overwrite per turn by `tail/compile_state.py`. Parser prompt receives the previous reply
  text and the structured previous state. Quoted replies pull a 4-key projection of the
  quoted turn's state.
- Access: `head/access.py::check_access` returns `attributes: None,
  all_attributes_allowed: None` always; grants are agent-level (`contact_agent_access`) plus
  `stock_visibility_policies` per contact (mode, warehouse_ids, hide_zero_locations). There is
  NO field-level reveal today, for any tool.
- DO open list already exists: `crm_order_management_orders_list` with
  `order_status=outstanding`, product / customer / transporter ids, delivery date window,
  `include_summary`, `sort`, `dir`.
- Console: `GET /api/v1/system/chatbot/turns` (list, cursor), `failed-contacts`, `retry`.
  FE `system-management/chat-history` with `TurnPanel`, `StateTracePanel` (Lost / Gained /
  Flags / Parser drift), `ChatThreadDrawer`.
- Replay: `tests/chatbot/test_replay.py` (node captures), `tests/chatbot/worlds.py` +
  `test_worlds.py` (end-to-end worlds derived from 200 captures, multi-turn worlds chain the
  CRM's own session write). 72 test files under `tests/chatbot/`.

## Decisions (owner, 7 Sep 2026)

- D1 Parity stays. Deterministic structured answers. Richer tools and richer parameters, never
  an answer LLM.
- D2 All six data capabilities ship in this iteration, none deferred.
- D3 Sellable stock is per contact, OFF by default. Basis: on hand minus open SO (ordered,
  not yet DO). Open DO is not subtracted: AutoCount deducts stock at DO creation.
- D4 PO answers never reveal the supplier to a dealer; management contacts may see it. A general
  per-contact field reveal control is required, not a PO special case.
- D5 No flow builder. Trace and visualise so the owner can debug and understand a turn alone.
- D6 Memory is the long-term shape (focus, episodes, profile), not the quick fix: one writer per
  layer, per-slot decay, typed values, every read traced. Parser receives structured hints,
  never transcript prose.
- D7 Dialogue state is ONE typed `open_question` slot with a table of kinds, resolved before
  the business parse. Selections, escalation yes/no, team and tier picks and member offers
  all become kinds.
- D8 Learning from corrections: capture only in this plan (the `correction` field already
  exists). Levels B-E deferred, see triggers above.
- D10 One product tool and one order tool: specs extend `crm_master_products_list`, SO
  outstanding is a bucket of `crm_order_management_orders_list` shown beside DO under
  `include_summary`; no `sales_order` domain.
- D11 Focus decays by turns only (`chatbot_focus_ttl_turns`, default 3). Three lanes.
- D9 The per-domain tax (six code places per new domain) is collapsed into one `DOMAIN_SPEC`
  table in code. Evidence: the six places exist today and every new domain in this plan would
  touch all six.

## Design

### Slice A - Six data capabilities (MCP tools + presenters + parser vocabulary)

Every tool is a `ToolSpec` in `sorento_crm_mcp/catalog.py` (GET, so read-only by
construction) backed by one backend GET under `/api/v1/...`, rendered by a presenter in
`sorento_crm_mcp/presenters.py` that emits the envelope `output_structurer` already reads
(`intro`, `items[].fields[]`, `summary_items`, `field_vocabulary`, `result_type`). Adding a
tool = ToolSpec + backend route + presenter + one name in `CHATBOT_READ_ONLY_TOOLS` + an
embedding sync + corpus samples. No renderer change per tool.

**Uniform list parameters** on every list tool this plan touches (new and existing DO list):
`group_by` (one of the tool's declared axes), `include_summary` (bool), `sort`, `dir`,
`limit`. The envelope carries `groups[] {key, label, items[], summary_items}` when `group_by`
is set; `output_structurer` gains ONE branch that renders groups as headed sections. Parser
gains two keys: `group_by` (enum: `product | customer | date | transporter | warehouse |
supplier | null`) and `top_n` (int or null, "last 3", "top 5").

| # | tool | backend | data | axes / params | notes |
|---|---|---|---|---|---|
| A1 | `crm_master_products_list`, extended (owner: one product tool, no spec tool) | existing products route, `include_specs` query param the presenter always sets | `product_specifications.values` joined to `product_spec_registry` (label, unit, synonyms, data_type, rank_weight) | unchanged; `requested_attributes` now also matches registry `synonyms` | Presenter appends every populated spec key as a field `label: value unit` ordered by `rank_weight`. With no `requested_attributes` the renderer shows today's four fields plus ONE compact "Specs: k: v, k: v" line (no overload); with `requested_attributes` only the asked keys, and a key the product lacks answers "no <label> recorded for <code>". Domain `master_products`, intent `check_product`, nothing new in the parser vocabulary. |
| A2 | stock balance, extended | existing stock balance route | add `open_so_qty` = SUM(`sales_order_lines.qty_ordered - qty_delivered`) over open lines per product (per warehouse where the line has one, else product total), `sellable` = `on_hand - open_so_qty` | unchanged | Both fields are `restricted` (Slice C): hidden unless the contact holds `inventory.sellable`. Wording: "Sellable (on hand minus open SO)". Never negative below zero without saying so: render "0 (oversold by N)". |
| A3 | `crm_order_management_orders_list`, extended (owner: one order tool, SO beside DO, no new domain) | existing orders route | new bucket `order_status=so_outstanding` = `sales_order_lines` open with `qty_ordered - qty_delivered > 0` (ordered, no DO yet), header customer, `order_date`, `requested_delivery_date`. Existing buckets stay: `outstanding` = DO created not delivered, `delivered`. | `product_ids`, `customer_ids`, `transporter_ids`, date window, `group_by` in {customer, transporter, date, product}, `include_summary` | The list renders ONE bucket. `include_summary=true` returns a three-line pipeline in `summary_items`: SO outstanding / DO open / delivered, so "how many did ABC take" reads three numbers and "list DO" stays a list. Domain stays `order`; the parser's `order_status` enum gains `so_outstanding` with the vocabulary "SO outstanding", "ordered but no DO", "belum DO", "还没出DO". Summary shape is the one open item, see the review page. |
| A4 | merged into A3 | | | | |
| A5 | `crm_procurement_po_placed_list` | `GET /procurement/purchase-orders/placed` | `purchase_order_lines` with `qty_ordered - qty_received > 0`, `line_status = open`, header `status`, `expected_date` (line, else header), supplier | `product_ids`, `expected_date_from/to`, `group_by` in {product, supplier, date}, `include_summary` | New domain `purchase_order`, intent `check_po`. `supplier` field is `restricted` (Slice C, key `purchase_orders.supplier`). Wording "PO placed, not yet shipped". PO and SPO are never netted (`spo_allocations.po_line_id` is NULL on every row, decision 6 Aug 2026), so this tool never subtracts incoming. |
| A6 | `crm_procurement_spo_allocations_last_receipt_list` | `GET /procurement/spo-allocations/last-receipt` | `spo_allocations` with `receipt_status = received` joined to `inbound_shipments` (`warehouse_arrival_date`, else `actual_arrival_date`), `quantity_received`, `spo_number`, warehouse | `product_ids`, `top_n` (default 1), `warehouse_ids` | Unblocks domain `spo_allocation` (remove from `DEFAULT_UNSUPPORTED_DOMAINS`), intent `check_spo` gains "last in". Phase 2 first task: measure on the local prod copy that `warehouse_arrival_date` is populated for received allocations; if not, the fallback order is `actual_arrival_date`, then `updated_at`, and the presenter labels which date it shows. |

Cross-domain ladder (Foundre's rule) lives here too: `answer.py`'s hard pair becomes a
per-origin list read from `system_settings.chatbot_crossdomain_ladder` (JSON, default
`{"inventory": ["incoming", "purchase_order"], "incoming": ["inventory"]}`), same probe and
render mechanism, one rung after another until a rung has rows for the missing code. New
wording for the PO rung: "No stock and no incoming for X, but a PO is placed: {qty} expected
{date}." followed by the existing escalate offer. The `nothing_note` becomes "No stock, no
incoming and no PO for X."

`DOMAIN_SPEC` (D9; this plan adds one domain, `purchase_order`, and unblocks `spo_allocation`; the six places are the evidence and every future domain pays them): one frozen table in `app/services/chatbot/contracts.py` keyed by domain
with `intents`, `axis`, `bare_entity_type`, `switch_words`, `tools`, `escalation_team`,
`default_supported`. `AXIS_BY_DOMAIN`, `BARE_ENTITY_TYPE_BY_DOMAIN`, `DOMAIN_SWITCH_WORDS`,
`DEFAULT_UNSUPPORTED_DOMAINS` and the tool filter become views over it; a guardrail test
asserts every `DOMAIN_HINTS` value has a row and every tool in `CHATBOT_READ_ONLY_TOOLS` is
claimed by exactly one domain. The parser schema enums are generated from the same table.

**As built (7 Sep 2026), three deviations, each with its reason:**

* **No `axis` field, and `AXIS_BY_DOMAIN` is NOT a view.** That table and five others
  (`DOMAIN_SUBJECT_AXIS`, `DOMAIN_SUBJECT_HINT`, `DOMAIN_BLOCKED_HINTS`,
  `MEMBER_OFFER_FILTER_HINTS`, `DOMAIN_BROADEN_BLOCKED_HINTS`) carry a per-domain HAZARD,
  not a per-domain fact: every row names the live turn that earned it (owner rulings K2 to
  K4, C1, the 2026-08-09 promotion-brand leak). Folding them in keeps the shape and loses
  the reason, which is the mistake `PRINCIPLES.md` calls copying a mechanism without its
  justification. They stay in `head/output_exchange.py` beside their evidence, and
  `test_domain_spec.py` asserts they stay there. Two views instead of the planned two plus
  four: `BARE_ENTITY_TYPE_BY_DOMAIN` and `DOMAIN_SWITCH_WORDS`, plus `DOMAIN_HINTS`,
  `INTENT_HINTS`, `DEFAULT_UNSUPPORTED_DOMAINS` and the tool pool.
* **"Claimed by exactly one domain" is delivered as "claimed by exactly one domain OR
  named in `UNDOMAINED_CHATBOT_TOOLS`".** Twelve of the thirty-seven read-only tools answer
  surfaces the chatbot does not route to by `domain_hint` at all (projects, complaints,
  SLA) or are helpers a lane reaches for by name (`crm_lookup_resolve`, `user_guides_read`,
  `crm_system_tool_capabilities_summary`). Giving each one a domain would put twelve
  members into `DOMAIN_HINTS` that the parser must never emit. The two sets are asserted
  disjoint and exhaustive, so no tool is unaccounted for, and `CHATBOT_READ_ONLY_TOOLS` is
  their union rather than a third list.
* **The two CORE-side copies of `DEFAULT_UNSUPPORTED_DOMAINS` read the table through a
  doorway, and the DDL `server_default` stays a literal.** AC-002 forbids core importing
  `app/services/chatbot/` and `test_import_boundary.py` fails naming the importer, so
  `SystemSetting.chatbot_unsupported_domains`' Python default and `settings.py`'s
  null-reset table both call `app/modules/chatbot/lane_vocabulary.default_unsupported_domains()`
  (the module's existing doorway, `completed_lane_kinds` is the precedent). The column's
  `server_default` cannot be computed at DDL time and must equal what migration 488 wrote,
  so it stays a string, pinned to the derived list by a test that names the migration a
  change would need. Migration 488's own literal is frozen history and untouched.
* **`DOMAIN_BROADEN_BLOCKED_HINTS["spo_allocation"]` is UNCHANGED** (still `["spo"]`),
  while `DOMAIN_BLOCKED_HINTS["spo_allocation"]` lost `product`. The two answer different
  questions: the always-blocked list decides what may NARROW a read, and `product_ids` is
  the SPO tool's only narrowing parameter, so blocking it there made "last in for X" answer
  about everything. The broaden list decides what a customer asking for ALL of something is
  widening off, and no turn has ever broadened inside this domain - it was refused until A6.
  The trigger for changing it is a MEASURED broaden turn under `spo_allocation` (something
  like "last in for everything") where the carried product narrows the answer the customer
  just asked to widen; add `product` to the broaden list then, and not before.
* **The FE fallback is deleted, not corrected.** `chatbotSettingsService.FALLBACKS`
  carried a fifth copy, already stale at `['goods_receive', 'spo_allocation']`. It is now
  `[]`: the column is NOT NULL with a server default, `GET /settings` omits the key only
  when there is no settings row at all, and in that state `POST /settings/general` answers
  404, so the screen can neither read a wrong default nor save an empty list over
  anything.

**Console check (`documentation/agents/chatbot-verification.md`), 8 Sep 2026.**
`sorento_crm_backend/tests/chatbot/console_cases/2026-09-07-growth-r1.yaml`, 14 cases, run
twice against a lane backend on :8004 (the second run pins the unpromoted prompt version
migration 490 published, via the `--prompt-version` flag, so nothing is promoted):

| run | parser prompt | result |
|---|---|---|
| 1 | the tenant's `production` label (v1, FULL) | **11 pass / 3 fail** (14 cases) |
| 2 | pinned to the growth r1 body | **13 pass / 1 fail** (14 cases; the 1 is an `XFAIL` another lane owns) |
| 3 | pinned, after the three suffixed-code cases were added | **13 pass / 4 fail** (17 cases; the 3 new reds were finding 2) |
| 4 | pinned, after the #736 separator fix | **16 pass / 1 fail** (17 cases; the 1 is the `XFAIL` another lane owns) |

**Foundre's rule now works from a real turn, and it never did before.** The console check of
7 Sep found it unreachable; the cause was one missing field. `engine._TurnSwitches` - the
snapshot a turn reads its settings off - had no `chatbot_crossdomain_ladder`, so
`_crossdomain_ladder` got `None` and `_next_crossdomain_rung` read that as "no ladder
configured = the pre-A7 single probe". Every unit test passed the ladder in by hand, so
nothing saw it. This is the "a new DB column must reach every manual builder" lesson one
builder further along than the two it usually names. A second half went with it: the events
`run_crossdomain` records happen INSIDE `complete_answer`, after the head closed the row, so
`complete_turn`'s resume-from-row dropped every `crossdomain` and `reveals` event - which is
why the first console run could not tell a rung that never ran from one whose evidence was
discarded. Both are covered end to end by
`tests/chatbot/test_foundre_rung_end_to_end.py` (AC-921 and AC-922 through `run_turn`, not
through `run_crossdomain`).

What still stands red, and who owns it:

1. **The multi-turn carry into a PO follow-up** - marked `expected_red_until:
   feat/chatbot-growth-dialogue` in the case file, so it reports `XFAIL` and will report
   `XPASS` (a failure) the moment that lane fixes it. This lane changes no carry rule.
2. **FIXED (issue #736): a suffixed product code never reached the ladder.** One line in
   `crossdomain_zeroset`. The RESOLVER strips dashes and spaces from a product token
   before resolving it, so "SRTWT7445-LV-NEW" arrives as the token `SRTWT7445LVNEW` while
   the match it resolved to carries `canonical_code: "SRTWT7445-LV-NEW"`. The membership
   test that builds `requested` compared both sides with `_norm_code` (strip + upper), so
   it could never be true for a code containing a separator: `requested` stayed empty,
   `missing` with it, `active` came out False, and `run_crossdomain` returned before
   probing anything. **Foundre's rule was off for every hyphenated code**, which is most
   of the catalogue, while `CB2904` - whose token and canonical code are the same string -
   worked.

   Found by reading two live turns whose traces are otherwise identical field for field
   (console-check-1788789839): same gate shape, same single compatible entity, same
   `match_tier`, same uuid, same tool call, same empty envelope; `CB2904` recorded both
   `crossdomain` events, `SRTWT7445-LV-NEW` recorded none. The earlier `match_tier`
   theory was measured and disproven first (all five codes resolve 1 match at tier
   `exact`), which is what left the token normalisation as the only remaining difference.

   The fix reuses `_type_norm`, which already exists in the same file and whose docstring
   names this exact mismatch ("the resolver strips dashes and spaces off product-hint
   tokens before it resolves them ... This is the key both sides are compared through") -
   this call site was simply the one not using it. Applied to BOTH sides of the membership
   test only: the `_n` key that reaches persisted state and the `by_code` lookup against
   the tool's own output still use `_norm_code`, so no emitted value changes shape and the
   corpus does not move (replay + worlds 1957 passed, no divergence registered).
   `TestIssue736SeparatorInsensitiveRequestedSet` drives the zeroset with the resolver
   shape the live turns carried - `tokens` + `intersection`, no `resolutions`, which is
   the branch the defect lived in and the shape the other stubs do not produce - over
   three suffixed codes plus a spaced token, with `CB2904` as the control and a
   different-code negative that must still fail (H62).

3. **The live prompt is unstable on the two A1 spec phrasings** and they swap between runs,
   because the live REQUESTED ATTRIBUTES section has no rule for a bare "spec" and no entry
   for "steel grade" (the model emits `["dimension"]`, which the registry has no key for).
   The addendum's new bare-spec rule settles the pair under the pinned version; aligning the
   live section's attribute words with `product_spec_registry`'s keys is its own slice.

**Post-deploy, Slice A (mandatory, not optional).** Two steps, in this order, and neither
happens by itself. **Without step 1, every PO placed / last-in ask misses** ("But no
purchase_order / spo_allocation matched these") whatever the customer's phrasing or the
prompt version - verified on the restored 7 Sep 2026 prod copy: `embedding_documents` had
112 `mcp_tool` rows and `embedding_chunks` 236, neither containing a source_id for either
tool, and direct PO / last-in asks for four different, unrelated codes (C-FH14, SRTWC8517,
SRTWT7445-LV-NEW, SRT62-GM) all missed identically until the two tools were seeded.

1. **Seed the embeddings for the two new MCP tools.** Verified 7 Sep 2026:
   `app/main.py`'s `startup_event` calls `mcp_tool_registry_service.sync_catalog`, which
   writes/updates the `mcp_tools` ROW for every catalogue spec and writes NO embedding at
   all. The chatbot picks its tool by cosine search over `embedding_chunks` where
   `source_type = 'mcp_tool'`, so an unseeded tool is invisible to every turn no matter how
   the customer phrases the question. The note stays.

   ```bash
   # in sorento_crm_backend/, against the target environment's DATABASE_URL
   venv/bin/python -m app.scripts.seed_mcp_tool_capabilities \
     --only crm_procurement_po_placed_list --drain
   venv/bin/python -m app.scripts.seed_mcp_tool_capabilities \
     --only crm_procurement_spo_allocations_last_receipt_list --drain
   ```

   **`--drain` drains the WHOLE pending queue, not just these two jobs.** Measured 7 Sep
   2026 on the local prod copy: 41,685 pending rows, and the first command ran past ten
   minutes. On a database with a backlog, drop `--drain` (the two jobs are then picked up
   by the RQ worker) or process only these rows.

   `--drain` processes the queued jobs in the same run, so no worker is required; drop it
   and the RQ worker picks them up instead. Idempotent - a second run supersedes the prior
   chunks rather than duplicating them. Verify with
   `SELECT source_id FROM embedding_chunks WHERE source_type = 'mcp_tool' AND source_id
   LIKE '%po_placed_list%' OR source_id LIKE '%spo_allocations_last_receipt%';`
   (two rows expected).

   **D15 (8 Sep 2026) renamed the PO tool** from `crm_procurement_purchase_orders_placed_list`
   to `crm_procurement_po_placed_list` (see AC-999) - apply migration `492` first, then the
   next backend reload's `sync_catalog` registers the NEW `mcp_tools` row and deactivates
   the old one automatically. **Deploy runs this step's first command with the new name
   even on an environment already seeded under the old one** - the old chunks do not
   migrate themselves. Mark them not current with
   `UPDATE embedding_chunks SET is_current = false, superseded_at = now() WHERE
   source_type = 'mcp_tool' AND source_id = 'implemented::crm_procurement_purchase_orders_placed_list';`
   (deleting the row is also fine; nothing reads a non-current chunk).

   **n8n's own `sub-get-rag` query is untouched** (the owner is retiring n8n, not fixing
   it) - it still runs `source_id LIKE '%<domain_hint>%'` over the tool NAME. The PO
   tool's new name contains neither "order" nor "purchase_order", so that filter can never
   match it under any domain; `mcp_tools.chatbot_domain` is what the CRM's OWN retrieval
   reads instead (AC-999), and is unrelated to n8n's copy of the query.

   **Dev note (8 Sep 2026): a worktree's venv can be a symlink to the PRIMARY checkout,
   whose editable `sorento_crm_mcp` install points at the primary tree, not the
   worktree's own copy.** Any one-off script that imports `sorento_crm_mcp` (this
   step's seed command, `mcp_tool_registry_service.sync_catalog`, a manual
   `sync_catalog()` call) must be run with
   `PYTHONPATH="<worktree>/sorento_crm_backend:<worktree>/sorento_crm_mcp"` or it
   silently syncs against a STALE catalog - measured here: a sync without it saw a
   40-tool catalog missing both new tools, and deactivated all 161 `mcp_tools` rows,
   including the two just-renamed ones, because none of them matched what that stale
   catalog listed. `test_crossdomain_ladder.py::test_the_key_is_declared_on_the_po_toolspec`
   already carries the equivalent `sys.path` guard for a pytest process; a bare
   `venv/bin/python -c "..."` has no such guard and needs the env var instead.

2. **Move the `chatbot_semantic_parser` `production` label** onto the version migration
   `490_chatbot_parser_growth` published, in Settings > AI Prompts. Until that move the
   parser has no vocabulary for `so_outstanding`, `purchase_order` / `check_po`, the SPO
   "last in" phrasing, `group_by` or `top_n`, so the new tools are reachable only by a
   direct MCP call. PROD's label is on v1 = the FULL body and dev's is on the SLIM one;
   the migration publishes BOTH texts unlabelled, so pick the one whose predecessor the
   environment is currently on. Rolling back is the reverse label move.


### Slice B - Dialogue state: focus + open question, single writer

New keys inside `SessionVars` (persisted shape stays JSONB, allow-list grows by two):

```
focus: {
  domain:      {value, set_at_turn, set_at, source},
  products:    {value: [entity], set_at_turn, set_at, source},
  customer:    {...}, transporter: {...}, warehouse: {...},
  date_window: {value: {start, end, mode}, set_at_turn, set_at, source},
  attributes:  {...}, tier: {...}, brands: {...}
}
open_question: {
  kind, options: [frozen rows], expects: "pick" | "yes_no" | "free",
  asked_at_turn, asked_at, ttl_turns, payload
} | null
```

`source` is the rule name that set the slot (`current_message`, `reuse`, `pick`,
`quoted`). Every slot decays independently by turns (owner: turns only, no wall-clock TTL):
one new `system_settings` column `chatbot_focus_ttl_turns`, default 3. A dead slot is
dropped at intake, before the parser sees state, and a trace line records it. The `set_at`
timestamp is kept for the trace only.

**Turn order changes** in `engine.py` `_run_stages`:

1. `received` (Python): read session, apply decay, build `focus_hints` (alive slots only)
   and `open_question_hint` (kind, expects, option labels). Deterministic; the parser is the
   only LLM call in the head and it only extracts.
2. `understood`: parser prompt v3 (`chatbot_semantic_parser`, new registry version, promoted
   only after replay). The prompt no longer says "continue the previous turn". It extracts
   the current message and emits three new keys: `answers_open_question: {resolved, picks[],
   yes_no, free_text}`, `anaphora: bool` ("it", "that one", "那个"), `topic_reset: bool`
   ("another", "other", "别的", a decisive domain word). `entities[].current_message` stays
   and is always true under v3. Removed from the prompt: every instruction that tells the
   model to re-emit previous entities or previous domain (`BARE ENTITY CONTINUATION`, "keep
   the previous domain", "downstream re-applies routing" stays).
3. NEW `answered` stage: if `open_question` is alive and `answers_open_question.resolved`,
   `dialogue/open_question.py::resolve(kind, answer, options)` runs the kind's handler and
   the turn proceeds from the handler's outcome (a pick becomes focus.products with
   `source=pick` and the business lane runs; escalate yes runs the escalation lane; team
   pick sets routing). An alive question that is not answered survives until TTL; one that
   is past TTL is cleared with a trace line and never answers silently.
4. `focus` step (replaces the carry half of `output_exchange`): `dialogue/focus.py::apply`
   with exactly these rules, in order, each a named function under pytest:
   - `replace_same_axis`: a current-message entity replaces the slot of its type.
   - `reset_on_topic`: `topic_reset` clears every slot except tier and brands.
   - `reuse_alive`: a message with intent and no entity of a needed type reuses the alive
     slot; dead slots never reuse.
   - `domain_from_switch_word`: a switch word sets domain, keeps product slots.
   - `date_restated_only`: date window is set only from the current message, except the
     `reuse_alive` case of a scope continuation (the one place the executor carries today).
   - `anaphora_reuses`: `anaphora` true and no entity reuses the most recent ALIVE product
     slot; with the slot dead the bot asks which product.
   The axis executor's `clear / reuse / modify / replace / replace_combine` semantics are
   kept as the outputs of these rules, so `compile_state.py`'s reconciled entities and the
   downstream lanes read the same shapes they read today. Rules K2 and K4, the switch-word
   override, `_query_brands_carried`, `_tier_carried` and the date and attribute carry arms
   in `output_exchange.py` are deleted, not shadowed. `confident` is read: an entity with
   `confident=false` never replaces an alive slot without a picker.
5. `compile_state`: writes `focus` and `open_question` from the dialogue module; the five
   `pending` kinds, `dym_offer` ladder, `selection_context`, `picker_*` and `_offer_carry`
   are rewritten as `open_question` kinds with one handler each:

| kind | expects | options | handler outcome |
|---|---|---|---|
| `product_pick` (dym, sibling, partial-miss roster) | pick | frozen rows with uuid, code, label | focus.products set, source `pick`, business lane runs with the pick; issue #708 partial-miss keeps already-resolved siblings via `payload.keep` |
| `customer_pick` | pick | family rows with company codes | focus.customer set |
| `escalate_yes_no` | yes_no | none | yes: escalation lane; no: `escalation_declined` copy |
| `team_pick`, `company_pick` | pick | teams / ledgers | routing set, escalation continues |
| `tier_pick` | pick | tiers | focus.tier set, promotion lane reruns |
| `member_offer` | yes_no | family members | yes: business lane over members; TTL 3 as today |

Quoted replies (`replyTo.id`) resolve against the quoted turn's frozen options first, as
today, then fall back to the alive `open_question`.

`SessionVars` keeps every existing key for one release so the world corpus still grades;
`pending`, `dym_offer`, `selection_context`, `picker_*` become derived mirrors of
`open_question` written by `compile_state` (read by nothing new). Removing the mirrors is a
follow-up once the corpus is re-derived.

### Slice C - Field reveal per contact

One mechanism for D3 and D4. Presenters mark a field `restricted=<key>` in
`field_vocabulary` (keys this plan ships: `inventory.sellable`, `purchase_orders.supplier`).
New table `contact_field_reveals (id, respond_contact_id FK, field_key, granted bool,
created_at, created_by)`, unique on (contact, key). Default for a restricted field is hidden.
`head/access.py::check_access` fills `attributes` with the contact's granted keys (it returns
`None` today, the shape already exists). `output_structurer` drops any field whose
`restricted` key is not in `ctx.access.attributes`, and the envelope's `summary_items` follow
the same rule. The MCP server itself stays unfiltered (in-app assistant and n8n operators are
internal); the chatbot is the only consumer that filters.

UI: Contacts detail page, Access tab, "Field reveals" section: a checklist of the restricted
keys with labels ("Sellable stock", "PO supplier"), off by default, confirm on unchecking.
Keys are listed from a backend `GET /system/chatbot/field-reveal-keys` generated from the
presenters at MCP catalog sync (stored on `mcp_tools` as `restricted_fields` JSONB), so a new
restricted field appears in the UI without a code change here.

### Slice D - Turn detail trace view

Backend: `GET /api/v1/system/chatbot/turns/{id}` returns the row plus a normalised
`trace_detail`:

```
stages[]:       {name, started_at, ms, status}
parse:          {raw, post_processed, prompt_version, model}
decay[]:        {slot, value, set_at_turn, age_turns, age_minutes, reason}
open_question:  {before, answer, after, handler, outcome}
focus[]:        {slot, before, after, rule, source}
tool:           {name, args, envelope (truncated to 8 KB), ms}
crossdomain[]:  {rung, tool, args, rows, rendered}
reveals:        {restricted_fields_seen[], granted[], dropped[]}
session:        {before, after, diff[]}
```

The engine already writes `trace` per stage; this slice adds the structured entries at each
step above (a `trace.add(kind, payload)` helper in `engine.py`, no new table) and the read
endpoint composes them. FE: `TurnDetailDrawer` in `system-management/chat-history` opened
from a row of the existing list, sections in the order above, each collapsible, monospace for
args and envelope, the session diff as a two-column Lost / Gained list reusing
`StateTracePanel`'s summary. Usable at 375px and 1280px, wide JSON scrolls inside its own
container. No feature explanation text on screen.

### Slice E - Corpus, worlds and promotion

- Corpus samples for every tool and every angle in Slice A (`documentation/plans/chatbot/samples/`).
- Multi-turn worlds for the dialogue cases the owner named: pick then next pick, escalate
  yes / no after a result, filter change (product, then date) after a DO result, "what about
  Y" after a stock answer, a product asked two turns ago then a stock question with no
  entity after TTL (must NOT carry), the same within TTL (must carry), quoted reply pick.
- Parser v3 replayed against the existing 200 captures; every divergence registered with a
  reason (expected class: entities no longer re-emitted from context).
- Shadow mode for 3 to 7 days before promoting v3, exit as the predecessor plan: branch
  parity 99%+, reply parity 97%+ on turns with no open question, every mismatch triaged.
- **Owner console pass, per lane** (the S7 Console Pass artifact is the template: 70 cases
  from 3,149 real turns, marked pass / fail / skip on the test clone, cases 1 to 20 = 78% of
  traffic). The four automated gates run first and are what catch regressions; the console
  pass is the owner's own eyes on the reply text, which no replay grades.
  - data lane: the existing 70 re-run as a sanity subset (cases 1 to 20 plus 23 to 27) and a
    NEW section of about 20 cases for what this lane adds: specs full and one-key, sellable
    on and off, SO bucket, the three-line summary, DO by customer / transporter / date, PO
    placed with and without supplier, SPO last in and last 3, the Foundre PO rung, the
    no-stock-no-incoming-no-PO line, a 422 on a bad group_by. Written by the lane, pre-filled
    with the clone results, before hand-off.
  - dialogue lane: the full 70 re-run, section 2 (clarify and picker flows) and every
    multi-turn case mandatory, plus the six owner worlds (AC-940 to AC-946) as console
    cases. This is the lane that changes reply semantics; a fail here blocks promotion of
    parser v3 regardless of the shadow numbers.
  - trace UI lane: no console pass; agent-browser evidence run instead.

## Lanes and PRs

One lane = one PR. Three lanes, in this order, each independently deployable:

| lane | slices | why this grouping |
|---|---|---|
| `feat/chatbot-growth-data` | A, C, D backend `trace.add` for tool / crossdomain / reveals | additive: new tools, new params, new table, ladder; no change to carry semantics, so the world corpus stays green as is |
| `feat/chatbot-growth-dialogue` | B, E worlds, parser v3 in shadow | the one lane that changes reply semantics; ships behind the prompt registry version and shadow mode |
| `feat/chatbot-growth-trace-ui` | D endpoint + FE | reads what the other two wrote; can land alongside lane 1 |

Phase order per lane per `PRINCIPLES.md`: FE mock first where there is FE (C's checklist,
D's drawer), backend test-first, review, DoD. Migrations chain onto the current main head via
`scripts/alembic-reparent.sh` at the pre-PR gate; heads at planning time: 481 chatbot, 487
elsewhere (see `project_do_search_mcp_feedback_7sep`).

## Risks

- **Parser v3 changes behaviour on every continuation turn.** Mitigated by shadow mode and
  registered divergences; the multi-turn worlds are the only test that can catch a wrong
  carry (worlds.py docstring). Promotion is the owner's call.
- **Sellable subtracts open SO across warehouses when the SO line has no warehouse.** State the
  basis in the field label; measure how many open lines lack `warehouse_id` on the prod copy in
  Phase 2 and record the number in the UAC evidence.
- **`spo_allocations` date columns may be sparse.** A6 measures first; the presenter labels
  the date it used.
- **`contact_field_reveals` widens what a contact can see.** Default hidden, grants explicit,
  confirm on change, and the chatbot filter is the only path that reads it. Quoted-state
  projection still excludes access keys (H-series lesson kept).
- **`DOMAIN_SPEC` is a registry.** Justified by six existing places and four new domains in
  this plan; the guardrail test is what stops it drifting.

## Out of scope

Learning levels B-E; L2 episodic retrieval; agent strategy; flow builder; removing the
`pending` / `dym_offer` mirrors from `SessionVars` (follow-up after corpus re-derivation);
MCP-side field filtering for the in-app assistant.

## Evidence run - `feat/chatbot-growth-trace-ui` (AC-963, AC-965, AC-972, AC-973, AC-992), 7 Sep 2026

Agent-browser (isolated session `tester-chatbot-trace-ui`), FE `http://localhost:3080` (dev,
HMR), BE `http://localhost:8080`. Screenshots: `documentation/evidence/chatbot-growth-trace-ui-7sep/`.

**Data prerequisite.** The dev Postgres had zero LIVE (`is_test=false`) `chatbot.turns` rows
tied to a real `chat_histories` message - the only three non-test rows in the DB belonged to a
contact with no `chat_histories`/`respond_contacts` row at all, so nothing surfaced in the UI
(the FE never requests `include_test=true`, by design - H57/D14). To exercise the drawer against
real data rather than mocks, one real turn was posted for the existing dev contact "Jayson
Jayson" (`respond_io_id 900000008`) via the legitimate ingress contract: `POST
/api/v1/external/chat/turn` (X-API-Key, envelope borrowed from that contact's most recent
console-check envelope, `is_test: false`, message "stock for SRTWC8517-SH-UF") followed by
`POST /api/v1/external/chat-history/messages` (the same contract n8n uses to log the incoming
message) so `chat_histories.message_id` matched `chatbot.turns.message_id` and `TurnPanel` had
something to key off. Turn id `e2c0334d-8a81-4e33-82c8-455fd2f5e48b`, status `delegated` (the
business lane is not CRM-completed on this lane's backend, so the turn routes and stops - it
never reaches `failed`). This is now real data in the shared local dev Postgres for that one
contact/message; nothing was written directly to the database.

**Step 1 - Chat History drawer (AC-972, AC-973).** Sidebar: System > Messaging > Chat History
(`/system-management/chat-history`). Widened the Filters date range (default is 1 day) via the
native `datetime-local` inputs. Opened the "Jayson Jayson (+60160000509)" thread, searched
"SRTWC8517-SH-UF", found the injected incoming message with its `TurnPanel` ("In progress /
Business query / 7.1 s / #e2c0"), clicked the "Open full trace" icon button. `TurnDetailDrawer`
opened titled "Turn #e2c0" with all nine sections **in order**: Stages, Parse, Decay, Open
question, Focus, Tool, Cross-domain, Field reveals, Session - matching AC-970/972's contract
(`reveals` renders as "Field reveals", `session` diff as "Session"). Stages was expanded by
default (4 rows: received/understood/access/routed, all `ok`); every other section was
collapsed. Expanded each one by one:
  - Parse: monospace JSON in a `<pre>`, confirmed via `getComputedStyle` - `overflow-y: auto`,
    `scrollHeight 776 > clientHeight 418` (scrolls inside its own container, not the page).
  - Decay: "Nothing decayed this turn."
  - Open question: "No open question this turn."
  - Focus: "No focus rule fired this turn."
  - Tool: "No tool call recorded."
  - Cross-domain: "No cross-domain probe this turn."
  - Field reveals: "No restricted field was on this answer."
  - Session: "Lost: none / Gained: none".
  At 375x812 (`set viewport 375 812`): `document.documentElement.scrollWidth` = 375,
  `window.innerWidth` = 375 - **no horizontal page scroll**. Screenshot
  `01-turn-detail-drawer-1280.png` (collapsed, 1280) and `02-turn-detail-drawer-375.png` (375).
  **AC-973 (failed turn first) not exercised live**: no naturally-occurring LIVE failed turn
  existed to open (the only failed rows in this DB are `is_test=true` console-check runs, which
  the FE never fetches, or on a contact with no chat row), and producing one would need
  flipping `chatbot_business_lane_enabled` / `chatbot_completed_lanes` for the whole lane
  backend - out of scope for a read-only verification pass. Covered instead by
  `TurnDetailDrawer.test.tsx::'a failed turn shows the failing stage first'` - ran green
  (`npx vitest run ".../TurnDetailDrawer.test.tsx"`, 4/4 passed).

**Step 2 - Contacts > Access > Field reveals (AC-963, AC-965).** No sidebar entry for
`/user-management/contacts` exists (by design - `apps-dropdown-menu.tsx` names it the ONLY nav
path). Reached via the topbar grid icon (mislabeled `aria-label="Switch layout"`, an existing
bug) > "Internal Users / Respond contacts" > any contact row > Access tab. The "Field reveals"
card renders under Access Agents: **"No restricted field exists yet - nothing here needs a
grant."** - correct, since `GET /field-reveal-keys` returns `[]` (no presenter has declared a
`restricted=` key yet). No UUIDs on screen, no explanatory/feature-education copy beyond that
one factual sentence. At 375x812 the card itself measured `left 16, right 359, width 343px` -
entirely inside the 375px viewport, not clipped. **However** `document.documentElement.scrollWidth`
= 439 at 375px viewport (> 375) - the page DOES have horizontal scroll, traced via
`el.scrollWidth` walk to the pre-existing "Access Agents" DataGrid/table (`scrollWidth 1090`,
its own `overflow-x-auto` wrapper) whose containing Card is 422-439px wide instead of
constraining to the viewport - a flex/`min-w-0` gap in a component this PR did not touch. Not a
regression from the Field reveals card (measured separately, entirely in-bounds); flagged as a
pre-existing defect, not filed as a new backlog item by this run.

**Step 3 - Backend spot checks (curl, `http://localhost:8080`, JWT from `POST
/api/v1/auth/login` - the `/system/chatbot/*` routes are `require_permission`, JWT only, no
X-API-Key path):**
  - `GET /api/v1/system/chatbot/field-reveal-keys` -> **200**, `{"items":[]}` (no restricted
    keys declared yet - expected, matches Step 2).
  - `GET /api/v1/system/chatbot/turns/e2c0334d-8a81-4e33-82c8-455fd2f5e48b` -> **200**,
    `trace_detail` present with exactly the nine keys: `stages, parse, decay, open_question,
    focus, tool, crossdomain, reveals, session`.
  - `PUT /api/v1/system/chatbot/contacts/96f2e854-acbe-40e5-89ad-6efb29aa3c4d/field-reveals`
    with `{"granted": ["bogus.key"]}` -> **422**,
    `"Unknown field reveal key(s): bogus.key. Allowed keys: none."`.

**Step 4 - Console.** Chat History / drawer pages: one pre-existing `Each child in a list
should have a unique "key" prop` warning traced to `Demo1Layout` (the shared shell, not this
PR's components). A batch of "Maximum update depth exceeded" errors also showed up in
`errors --json` - traced to the synthetic `dispatchEvent(new Event('input'/'change'))` calls
this run used to drive the native `datetime-local` Filters inputs (CDP has no native way to
type into the two-part date/time spinner), not to real user interaction; not treated as a
product defect. No other console errors on either page.

**Overall: PASS** on AC-963, AC-965 (with the pre-existing Access Agents overflow noted, not
this PR's regression), AC-972, AC-992. AC-973 verified via vitest, not browser (no reachable
live failed turn in this dev environment without altering shared settings).

## As built, 8 Sep 2026 - `feat/chatbot-growth-data`, owner console follow-ups

Eleven items from the owner's 7-8 Sep console passes, one commit each on the data lane,
plus the review round 2 fixes. Contract changes, so nothing here is only in the diff:

| item | commit | what changed |
|---|---|---|
| 1 field-reveal keys | `0bf018e9c` | `ToolSpec.restricted_fields` declared on the stock and PO-placed tools (`inventory.sellable`, `purchase_orders.supplier`); `sync_catalog` copies them to `mcp_tools.restricted_fields`, which the Contacts > Access > Field reveals card lists. Guardrail `sorento_crm_mcp/tests/test_field_reveal_keys_declared.py`. |
| 2 Open SO / Available | `ead94caa6` | Stock answers carry ONE line per product after the rows - `Open SO n, Available n`, raw signed number, `Open SO: none` when nothing is on order. Review round 2 (S2): the line reads the backend's per-product `stock_summary` (`total_on_hand` over every warehouse row, attached in detailed mode under `include_sellable` too), never a page sum; the compact policy renders the same block and drops its per-entry pair. |
| 10 console media | `eb1055662` | Console page image and voice turns through the real extractor (`POST /console/turn` with `media`, `GET /console/media/{id}` poll). Round 2 nit 2: the poll is scoped to `message_id LIKE 'console-media-%'`. |
| 9 SO outstanding per row | `1bb6550fd` | `include_pipeline` on `GET /orders/by-product`; `stamp_so_outstanding_rows` writes `so_outstanding_qty` on every `summary.products[]` row and on the `groups[]` rows the SO side can match (S1: an unmatchable debtor-name group carries no key, never a 0); presenter field "SO outstanding (not yet DO)" after Pending Qty. Never top-level on the by-product summary. |
| 3 delivery word + name | `87e98bd9f` | `DOMAIN_SPEC["order"].switch_words` += delivery, deliveries, deliver, delivered, penghantaran, hantar, dihantar; `purchase_order` += po (turns 18d9b95c, 1d22dbb6, 98a9bec0); `spo_allocation` += spo (bd6eacf4, 796957f4). `output_exchange`: a `request_for_help` with no team, a switch word of one domain and an entity named this turn is retyped `business_query` (B2: that measured arm only; the decisive-intent half stays with the said-yes backstop). Prompt line in both bodies + addendum; **v18 FULL `16aefd99`, v19 compact `4df6deb7`** published unlabelled. |
| 3b analytics out | `04ff78ed8` | `crm_order_analytics` removed from `DOMAIN_SPEC["order"].tools` (and so from `CHATBOT_READ_ONLY_TOOLS`): it needs a `metric` the fetch lane never maps and answered empty on every pick it won (turns 87694182, 0b10a4c0, 11932963). Re-add when the lane maps a metric off the parser. Declared in `test_tool_pool_is_read_only.py::UNCALLABLE_READS`. |
| 4 PO rung | `fb542080b` | PO placed rows carry `po_date` = `purchase_orders.issue_date` (presenter "PO Date" before Expected Date); rung line "{qty} pcs on PO {no} dated {date}, expected {exp}", null parts omitted. The rung needs the per-contact key **`purchase_orders.placed`** (declared on the PO ToolSpec, enforced in `_apply_crossdomain_rung`, `granted` threaded from `ctx["access"]["attributes"]`); without it no probe and the ladder-off note. The escalate offer is written ONCE, by `tail/compose.crossdomain_compose`; neither probe writes it. |
| 7 customer-only order ask | `5e457ff41` | `services.drop_by_product_without_product`: on an `order` pool with `has_product is False` the by-product tool is dropped before `tool_filter` (`_tool_pick.dropped_no_product`). Resolver limit 15 is not capping any real family (only `vsofttesting123`, 32). |
| 5 unshipped SPO | `22098dcda` | `purchase_orders_placed_rows` returns pending, unshipped SPO allocations as rows with `kind = "spo"` (PO rows `kind = "po"`); summary counts both; presenter "Source" after PO Number; rung says "but stock is on order from the supplier:" when every row is SPO, and the three-way miss reads "No stock, no incoming and nothing on order for X." **S3: no `po_line_id` dedupe** - 0 of 80,468 allocations carry one; re-add (an allocation whose `po_line_id` points at an open PO line already in the rows is that line's shipment plan) when `spo_allocations.po_line_id` is populated. B1: the summary applies the company scope by hand on both legs. |
| 6 console default | `1e9b75813` | `ConsolePromptVersion.base` (full / compact / other, off the first 200 chars); the console pins the newest FULL version unless a stored explicit choice exists (`localStorage chatbot-console:prompt-version`); option label "v18 · full · production"; `ConsoleTurnResponse.prompt_version` -> a muted "v18" pill on bot bubbles. |
| 8 attribute projection | `5f1bf01a5` | `_project_product_specs`: base fields first (price / dimensions / description; S5/S6: a single-token entry matches the whole ask exactly, no Product Name row), spec keys by token containment (exact + every key whose key or label tokens contain the ask, registry order), ONE miss line per asked word after the items (`spec_misses`, "*<label>:* not recorded for A, B (+N more)"). Prompt: the attribute phrase is emitted whole; "outstanding SO" is `so_outstanding`, never `check_po`. **v20 FULL `7e2dbf53`, v21 compact `f50f343c`** published unlabelled. |

Owner steps: move the `production` label (v21 is the compact successor of v17, v20 the FULL
one); grant `purchase_orders.placed` per contact on Contacts > Access (the console contact
900000008 holds it for the graded A7 cases).
