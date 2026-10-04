# PLAN: Chatbot answer polish, 12 Sep 2026 (five owner findings from live turns)

Status: implemented (PR pending)
Branch: `feat/chatbot-answer-polish` (worktree `.claude/worktrees/chatbot-answer-polish`)
UAC: `chatbot-answer-polish-12sep-acceptance-criteria.md`
Lane stack: backend :8080 (uvicorn, worktree venv symlinked to the primary venv), MCP :8765
pointed at :8080. No frontend change, so no dev server.

## Owner findings (12 Sep 2026, from the chatbot console on production)

1. **"and 2 more" on the Specs line.** A product answer with more than eight populated spec
   keys ends its `*Specs:*` line with "and N more" and there is no way to expand it.
   Owner: frustrating, do not truncate.
2. **"I have attached the file(s) below." with no file.** The cross-domain block (stock
   ask answered with INCOMING rows, or the reverse) ends with that sentence whenever the
   probe envelope carried attachments, but nothing downstream ever sends them:
   `tail/compose.crossdomain_compose` folds `block["block"]` (text only) into the reply
   and the send lane reads `envelope.attachments` from the PRIMARY answer alone
   (`engine._attachments_src`). The sentence has never been true.
3. **PO rung labels are not bold.** The cross-domain PO rung prints `Product Code:`,
   `Ordered:`, `Outstanding:`, `PO date:`, `Location:` in plain text while every other
   field line in the reply is `*Label:* value`.
4. **A typed PREFIX never climbs the ladder.** "ETA SRTWT6236" resolves (tier `and`) to the
   one family member SRTWT6236-GY, the incoming lookup finds nothing, and the reply stops at
   "But no incoming matched these." with no stock probe and no PO rung - although
   SRTWT6236-GY has an open PO line (202607-S0034, 99 outstanding, BRW). "ETA SRTWT6236-GY"
   (exact) climbs correctly. Reproduced on the lane stack against the 0907 prod copy.
   Cause: `crossdomain_zeroset`'s non-`resolutions` branch (`tokens` x `intersection`)
   only requests a product whose `_type_norm(canonical_code)` EQUALS a typed token; the
   prefix family member is dropped, `requested` is empty, `_xd.active` is False, nothing
   probes. The `missing` loop two screens down already treats a typed code as satisfied by
   any `startswith` family member, so the two halves disagree about what "requested" means.
5. **Detailed and compact modes disagree on a zero-everywhere product.** Same contact,
   "SRT6550-DIY ETA": compact prints `*Total:* 0 (O/S: 21)` and "stock is 0 at every
   location ... but PO is placed"; detailed prints "No incoming and no stock", as if the
   product were unknown. Reproduced on the lane stack: the console contact's
   `stock_visibility_policies` row (prod copy of 7 Sep, and the API's own
   `GET /inventory/stock-visibility/contacts/900000008`) carries
   `hide_zero_locations = true`; flipping it to false makes detailed print the BRW row at
   0 and the same zero sentence as compact. The screenshot's toggle reads off with the
   Save button lit, which is the unsaved-draft shape. Beyond the setting, the two modes
   genuinely disagree under hide-zero: `_apply_stock_visibility` (compact/availability)
   drops zero LOCATION LINES but keeps the product's block "because dropping the block
   would say 'I never found it' instead of 'none left'" (its own comment,
   `inventory_service.py` ~1300), while the detailed row filter (`~766`,
   `Stock.quantity_on_hand != 0`) removes every row of an all-zero product, so the chatbot
   reads it as absent.

## Decisions

- D1 (finding 1): the Specs line lists EVERY populated spec key, in registry order, no cap
  and no "and N more". `_SPEC_SUMMARY_CAP` goes. The per-word miss line's "(+N more)" code
  cap (`_MISS_CODES_CAP`) is a different thing and stays.
- D2 (finding 2): the cross-domain block never says "I have attached the file(s) below."
  The `mention` and the `xd_files` gate in `crossdomain_render` go; the block's own
  `attachments` key may stay (nothing reads it) or go, coder's call - the sentence is the
  defect. Forwarding the files instead was considered and not taken: the cross-domain
  probe does not pass through the primary answer's per-contact document handling, and
  "simplest thing that works" is to stop claiming a send that does not happen. If the owner
  wants the files, that is a follow-up with its own filter, not this lane.
- D3 (finding 3): `_crossdomain_rung_text` renders `*Product Code:* X`, `*Ordered:* N`,
  `*Outstanding:* N`, `*PO date:* D`, `*Location:* L`. Omission rules, ordering and the
  one-blank-line separation from PLAN-po-placed-fields-and-rung-wording are unchanged.
- D4 (finding 4): in `crossdomain_zeroset`'s `else` branch (no `resolutions`), an
  intersection product is requested when `_type_norm(canonical_code)` equals a typed token
  OR starts with one (`strict=False` either way, exactly as today's equal case). A token
  shorter than 4 characters never prefix-matches (guards "SRT" from requesting every
  product in the intersection). The `resolutions` branch is untouched: it already adds a
  single non-exact match. Every downstream rule (`missing` prefix satisfaction, the zero
  flag, the rung, `can_state_absence`) is unchanged.
- D5 (finding 5): detailed mode's hide-zero keeps the rows of a product that is zero at
  EVERY policy-visible location (the same "none left, not never found" rule compact
  already applies), and drops zero rows only for a product that has stock somewhere. A
  negative row is never dropped (existing rule). Implemented in
  `InventoryService` at the detailed row filter as a SQL predicate:
  `quantity_on_hand != 0 OR NOT EXISTS (another Stock row, same product_id, same company,
  within the policy's warehouse criterion, with quantity_on_hand != 0)` - the subquery
  carries the company filter and the warehouse criterion EXPLICITLY (issue #832: a
  correlated EXISTS escapes the do_orm_execute company filter). Pagination totals stay
  correct because the rule lives in the query, not a Python post-filter.

## Files touched

- `sorento_crm_backend/app/services/chatbot/lanes/business/fetch.py` - `_project_product_specs`
  no-attribute branch: no cap; `_SPEC_SUMMARY_CAP` removed; docstring updated.
- `sorento_crm_backend/app/services/chatbot/lanes/business/answer.py` - `crossdomain_render`
  drops `mention`; `_crossdomain_rung_text` bold labels; `crossdomain_zeroset` else-branch
  prefix request (D4).
- `sorento_crm_backend/app/services/inventory_service.py` - detailed hide-zero predicate (D5).
- Tests (written FIRST by the tester, made green by the coder):
  `tests/chatbot/test_product_spec_projection.py` (flip the cap test, add a 12-key case),
  `tests/chatbot/test_crossdomain_ladder.py` (bold labels; no mention sentence; prefix
  zeroset), `tests/chatbot/test_foundre_rung_end_to_end.py` (expectation strings),
  `tests/test_stock_visibility_policy.py` (detailed hide-zero keeps an all-zero product,
  drops zero rows beside real stock, keeps a negative row).
- `tests/chatbot/console_cases/2026-09-12-answer-polish.yaml` - the five live turns as
  graded cases (finding 4: "ETA SRTWT6236" must carry the PO block; finding 5 with the
  console contact's hide-zero left as prod has it).

## Verification

Console check on the lane stack (`scripts/chatbot_console_check.py --say ...` against
:8080 with MCP :8765) for: "ETA SRTWT6236", "SRT6550-DIY ETA" (detailed), a product ask
with > 8 specs (SRTKT71SS), and a stock ask whose incoming rows carry attachments
(CWCX7605-S-ECO). Evidence under `documentation/plans/chatbot/evidence/answer-polish-12sep/`.
Then the same turns on production after deploy (chatbot-verification.md).
