# PLAN - Order inquiry handover email to purchasing (parallel run)

Status: **APPROVED 16 Sep 2026, building.** UAC: `scm-oi-handover-email-acceptance-criteria.md`.
Owner rulings R1-R10 accepted in Lavish (scratchpad `oi-purchasing-email-proposal.html`,
"ok let's have this first and we further improve, go"). Go-live is 17 Sep; the parallel run
starts with it.

## 0. Why

From go-live CS raises order inquiries in the system AND keeps sending the manual mail
("BRW-BB @ SO397450 , SO397460", Excel attached) so the two can be compared before the manual
mail is retired. Purchasing needs a system mail shaped like the manual one to compare against.
Six real mails and three workbooks were studied (3 Apr, 18 Dec, 21 / 27 / 28 Jul 2026 threads
from `project.sadmin03`); their vocabulary is the one the inquiry rows already carry.

Measured on the 0915 production copy: every inquiry row ever written is ORDER (11,774) or
ORDER BACK (84); zero rows settled in place, cancelled or marked changed; 2 amendments, 0 order
change notices. The revision path is covered by pytest only, so the parallel run is its first
real exercise.

## 1. Rulings (owner, 16 Sep)

| # | Ruling |
| --- | --- |
| R1 | Automation, not n8n, not a hard-wired sender: recipients, template, on/off on one Automation row. Retire = untick Enabled. |
| R2 | One email per CS write (one commit), rows grouped by SO. No row cap. |
| R3 | Two tables, no CHANGE columns. SO table: S/O NO, CUSTOMER, PROJECT. Line table: SO DATE, S/O NO, CUSTOMER, PROJECT, ITEM CODE, QTY, DELIVERY DATE, REMARK. A change = old value struck through in its own cell, new beside it; verb in REMARK. |
| R4 | Subject `OI: {location} @ {SO list}`; several locations: `OI: {SO list}`. Headline = verbs present. |
| R5 | Sender on the thread via recipient option `include_actor` ("Cc the person who raised it"). |
| R6 | Purchasing's own actions (confirm, reject, link, unlink) and the sheet importer never fire it. |
| R7 | No workbook attachment. Trigger to add: purchasing forwards to a supplier and needs the file. |
| R8 | `order_inquiry_changed_with_links` untouched. |
| R9 | Seed template + automation on deploy, enabled, role Purchasing + Cc actor; admin types extra addresses after deploy. |
| R10 | Item swap prints as two lines (CANCEL BALANCE + ORDER); REPLACE ITEM is slice 2 after go-live. |

Reversal recorded: the Aug handshake ruling "AC-I4: this stops being an email" stands for the
in-app task. The email is the parallel-run comparison and lives on an Automation row the owner
can switch off; no code is removed to retire it.

## 2. What exists (measured)

- Rows: `projects.order_inquiry_rows` (`app/models/project_so.py`), verbs `IV_*`, labels at
  `project_order_inquiry_service.py:209` (`ADVANCE`, `DELAY`, `CHANGE SO NO`, `CANCEL BALANCE`).
  In-place change keeps `previous_qty` / `previous_delivery_date` (`_settle_row_in_place`, ~890).
- Write seams, all in `ProjectOrderInquiryService`, all already taking `actor_user_id`:
  `refresh_for_decision` (463), `derive_for_amendment` (1353), `derive_for_book_change` (1405),
  `ensure_inquiry` (1541); inner writers `OrderInquiryRow(` at 4 sites, `_settle_row_in_place`,
  `_retire_settled_cancel_balance` (1137), `_retire_uncovered_rows` (1296).
- Post-commit pattern already used twice in the same file: queue on `Session.info` mid-transaction,
  drain in `register_order_inquiry_post_commit_dispatch`'s `after_commit` listener on a FRESH
  session, discard on `after_soft_rollback`. `_dispatch_changed_with_links` is the model.
- Automations: `AutomationService.dispatch_event(trigger_type, context=, source_kind=, source_id=)`
  (`automation_service.py:272`) -> `_execute` -> `_send_per_match` (renders `match.context` through
  `EmailTemplateService.render`, sandboxed Jinja with `{% for %}`) -> `Notification` + email
  delivery -> `notifications` RQ queue -> `email_outbox_service.enqueue`. First resolved recipient
  is To, the rest Cc (`notification_tasks.py:252-268`).
- Recipients: `automation_recipients.resolve_recipients(db, config, promotion_context=, source_id=)`
  with keys `user_ids`, `role_ids`, `include_promotion_owner`, `include_assigned_cs_pic`,
  `extra_emails`. FE picker: `system-management/automation/components/RecipientPicker.tsx:155-180`,
  types `automation.types.ts:4-7`.
- Trigger catalog: `automation_triggers.py`, `register(TriggerSpec(...), fn)`; event-driven specs
  return `[]` from their pull function (see `order_inquiry_changed_with_links`, 464-500).
- Seed precedent: `alembic/versions/212_seed_pr_sponsorship_approved_automation.py` (idempotent by
  `email_templates.code` and `(trigger_type, name)`). Current head: `ptag_0011_line_promo`.
- Link: `build_order_inquiry_link(so_number)` -> `/project-sales/order-inquiries?query=<SO>`.
- Customer / project: separate fields on the join the worklist uses; `project_customer_label`
  (303) is the only thing that joins them. The email keeps them apart.
- Importer `project_order_inquiry_import_service.py` constructs `OrderInquiryRow(` itself (1404):
  it never passes through the service's writers, so it is excluded by construction (R6).

## 3. Design (simplest thing that works)

One recorder, one drain, one trigger, one template.

### 3.1 Recorder

`ProjectOrderInquiryService._record_handover(row, *, kind, was=None, actor_user_id=None)` appends
`{row_id, kind, was, actor_user_id, tx_chain}` to `Session.info[_HANDOVER_PENDING_KEY]`.

Called from:

| seam | kind | was |
| --- | --- | --- |
| the 4 `OrderInquiryRow(` constructions (raise) | `raised` | for verb CHANGE SO NO: the source order facts; for ADVANCE / DELAY raised by the amendment: `previous_delivery_date` (already on the row) |
| `_settle_row_in_place`, qty / date moved | `settled` | `{qty, delivery_date}` captured BEFORE overwrite (the same values it writes to `previous_*`) |
| `_settle_row_in_place`, need drops to zero | `cancelled` | `{qty}` |
| `_retire_uncovered_rows`, `_retire_settled_cancel_balance` | `cancelled`, always. A replacement raised in the same commit prints as its own line beside it; no pairing (R10 rule, AC-H19, revised 16 Sep after the coder found the "no replacement" cross-reference would need every raise in the commit indexed by line) | `{qty}` |

`actor_user_id` is what the entry point received (`refresh_for_decision`, `derive_for_amendment`,
`derive_for_book_change`); inner writers get it passed down or read it from an instance attribute
set at the entry point. Fallback: the inquiry header's `raised_by`.

Acknowledge, reject, `place_on_po*`, `auto_place_for_products`, `link_now`, `unplace*` never touch
the recorder (R6, AC-H8). The importer never reaches it (AC-H9).

`tx_chain` follows `_notify_purchasing`'s C2 rule so a sibling order's savepoint rollback discards
only its own entries (`after_soft_rollback` handler, same as the existing two).

### 3.2 Drain

`_fire_pending_handover(session)` in `register_order_inquiry_post_commit_dispatch`: pop the queue,
open `SessionLocal()`, re-read the rows (current values) with their inquiry, project SO, customer,
project and stock location (reuse the worklist's join / `_project_customer_labels` inputs, not the
joined label), build ONE context, call
`AutomationService(fresh).dispatch_event("order_inquiry_handover", context=ctx, source_kind="order_inquiry_handover", source_id=<first inquiry id>)`.
Any exception is logged, never raised (post-commit work never raises).

### 3.3 Context (AC-H17)

```
{
  "handover": {
    "subject_scope": "BRW-BB @ SO397450 , SO397460",
    "verbs": ["ORDER"], "headline": "ORDER",
    "orders": [{"so_number": "SO397450", "customer": "BUIMACO", "project": "SLG / TUJU RESIDENCE AT JLN KUCHING, KL"}],
    "lines": [{"so_date": "02/04/2026", "so_number": "SO397450", "customer": "BUIMACO", "project": "...",
               "item_code": "CB6633", "qty": "540", "delivery_date": "21/07/2026", "remark": "ADVANCE",
               "was": {"delivery_date": "03/08/2026"}}],
    "line_count": 10,
    "link": "https://.../project-sales/order-inquiries?query=SO397450"
  },
  "actor": {"name": "Maryam Ariffin", "email": "person32@example.com"},
  "today": "2026-09-16"
}
```

`was` carries only the keys that differ; `null` when nothing does. `remark` rules (one function,
`handover_remark(kind, row, was)`, table-tested):

| kind / verb | remark |
| --- | --- |
| raised, any verb | verb label; ORDER BACK appends `cited_document`; `note` appended after ` - ` when present |
| settled, date earlier / later | `ADVANCE` / `DELAY` (the inquiry engine's own rule, `verb_for`) |
| settled, qty down by N / up by N | `CANCEL BALANCE N NOS` / `ORDER N` |
| settled, both moved | date verb first, then qty phrase, joined by `, ` |
| cancelled | `CANCEL BALANCE <old qty> NOS` |

Subject scope (AC-H7): distinct locations over the lines; exactly one -> `<loc> @ <SO list>`, else
`<SO list>`.

### 3.4 Trigger spec

`register(TriggerSpec(type="order_inquiry_handover", label="Order inquiry handover to purchasing",
description=..., config_schema={"type":"object","properties":{},"additionalProperties":False}),
_trigger_order_inquiry_handover)` returning `[]` (event-driven), beside the existing OI trigger.

### 3.5 Recipients (AC-H11)

`include_actor: bool` in `_normalize_recipient_config`; `resolve_recipients` adds
`promotion_context["actor"]["email"]` when set. FE: one checkbox "Cc the person who raised it"
in `RecipientPicker.tsx`, type in `automation.types.ts`. Reusable by any trigger that puts `actor`
in its context.

### 3.6 Template + seed (AC-H13, AC-H14)

Migration `<next>_seed_oi_handover_automation` (id <= 32 chars, `down_revision` = main head at
PR time via `scripts/alembic-reparent.sh`). Seeds:

- `email_templates.code = order_inquiry_handover_default`, subject
  `OI: {{ handover.subject_scope }}`, `body_html` = headline in red, SO table, line table with
  `{% if line.was and line.was.qty %}<s>{{ line.was.qty }}</s> {% endif %}{{ line.qty }}` per cell,
  footer with `{{ actor.name }}` and the link; `body_text` = same tables in plain text, changed
  cells as `new (was old)`.
- `automations` row: name `Order inquiry to purchasing`, `trigger_type = order_inquiry_handover`,
  `action_type = send_email`, `enabled = true`, `group_matches = false`,
  `recipient_config = {"user_ids": [], "role_ids": [ids of user_roles where slug like 'purchasing%'], "extra_emails": [], "include_actor": true}`.
  On a database with no purchasing role (CI) `role_ids` is empty; the row still seeds.

Idempotent by template code and `(trigger_type, name)`. Downgrade deletes both.

## 4. Phases

- **Phase 1 (FE mock):** the checkbox (AC-H16), driven against the existing form. The email's
  visual contract is the three mocks on the Lavish page; the seeded template reproduces them.
- **Phase 2 (BE, tester first):** red tests in `tests/test_order_inquiry_handover_automation.py`
  (pattern: `tests/test_order_inquiry_changed_with_links_automation.py`), then the coder.
  Order: trigger spec -> recorder + drain -> context builder + remark rules -> recipients ->
  migration + template -> FE checkbox swap to real (it already is real; the payload key is the
  contract).
- **Phase 3:** reviewer (Opus) with kill tests on AC-H1, AC-H8, AC-H11; security-reviewer only if
  the diff touches a guard (it should not); browser walk of the Automation form checkbox; guide.

## 5. Captain's test list (for the tester)

| AC | test | assertion in words |
| --- | --- | --- |
| H1 | `test_one_dispatch_per_commit_two_orders` | two orders raised in one session, `dispatch_event` mocked, called once, `orders` has both SOs, `lines` count = rows raised |
| H2 | `test_raised_rows_no_was_remark_is_verb` | ORDER / RESERVE & ORDER / ORDER BACK lines: `was is None`, remark as ruled, ORDER BACK carries cited document |
| H3 | `test_settle_date_earlier_is_advance_later_is_delay` | parametrized over earlier / later |
| H4 | `test_settle_qty_down_cancel_balance_up_order` | parametrized over down / up, N in the phrase |
| H5 | `test_settle_to_zero_prints_cancel_balance` | qty "0", was.qty old, remark |
| H6 | `test_change_so_row_carries_source_in_was` | source SO / customer / project in `was`, target on the line |
| H7 | `test_subject_scope_single_and_mixed_location` | parametrized |
| H8 | `test_purchasing_actions_do_not_dispatch` | acknowledge, reject, place_on_po, unplace: mock never called |
| H9 | `test_importer_apply_does_not_dispatch` | importer apply on a small sheet: mock never called |
| H10 | `test_rollback_discards_pending` | record then rollback then commit an unrelated write: not called, queue empty |
| H11 | `test_include_actor_adds_actor_email` | true + actor: present once; false: absent; no actor: absent |
| H12 | `test_trigger_in_catalog` | catalog lists `order_inquiry_handover` |
| H13 | `test_seed_migration_idempotent` | upgrade twice: one template, one automation, enabled, include_actor true; downgrade removes |
| H14 | `test_template_renders_strike_and_text_was` | fixture context: `<s>03/08/2026</s>` in HTML, `21/07/2026 (was 03/08/2026)` in text, both tables, link |
| H15 | `test_dispatch_runs_after_commit_not_before` | no outbox row before commit, one after |
| H17 | `test_context_shape_and_formats` | keys present, dd/mm/yyyy, qty without decimals, verbs order |
| H18 | `test_actor_fallback_to_raised_by` | seam without actor uses header raised_by; none -> actor null |
| H19 | `test_retired_row_prints_cancelled_line` | retired row prints qty 0, was.qty, CANCEL BALANCE N NOS; a replacement in the same commit prints beside it |
| H16 | vitest `RecipientPicker.include_actor.test.tsx` | tick / untick round-trips the key |

## 6. Verify while building (coder reports, does not guess)

1. Where an `IV_CHANGE_SO` row keeps its target order (the OI engine maps `CHANGE_REPOINT`; find
   the field the raise writes) - AC-H6 depends on it.
2. Whether `derive_for_book_change` commits once per upload or once per order (decides how many
   emails one book upload sends; the plan accepts either, the guide must say which).
3. Whether `_send_per_match` / `AutomationRun` dedupes by `source_id`; if it does, use a per-commit
   token as `source_id`.

## 6b. Review round 1 (16 Sep, Opus): B1 + S1-S5

B1 blocker: the drain marked `session.get_transaction()` (root) while the recorder tagged the
innermost savepoint, so any confirm inside `begin_nested()` (planning-change apply, book upload)
never dispatched. Fix: mark `get_nested_transaction() or get_transaction()`, the head
`_transaction_chain` already uses (AC-H21). S1 marker growth (AC-H24). S2 three queries per line
plus per-row flushes (AC-H25). S3 AC-H6 deferred to slice 2. S4 carry gate leaks when the live
handshake is missing (AC-H22). S5 named line re-confirmed at a new qty printed a bare ORDER:
the silently cancelled old row now prints cancelled beside it (AC-H23). Nits: `today` in
Asia/Kuala_Lumpur, docstring at the recorder, dead `IV_RELEASE` vocabulary.

## 6c. Review round 2 (16 Sep, Opus): READY, one ruling

Ruling (captain, 16 Sep 23:30): the drain fires ONLY when the ROOT transaction commits, never at
a savepoint release. Reason: `planning_change_service.apply` gives each order its own savepoint,
so firing at release sent one mail per order (R2 says one per write) and could send a mail for
rows a later parent rollback removed. Lines recorded under a released savepoint wait for the root
commit; an inner rollback still discards only its own lines (C2 rule). AC-H27 / AC-H28.
Also ruled: the migration goes back to skip-when-present (Alembic never re-runs an applied
revision, so the UPDATE branch could only overwrite an admin's hand edit; AC-H13 unchanged);
"Raised by" date dd/mm/yyyy (AC-H14). Noted, not built: a carried line holding two owed rows
compares against one of them (N2); the edit form resends `group_matches` (inert here, N4).

## 6d. Review round 3 (16 Sep, Opus): READY

Root-only drain verified at SQLAlchemy source level and by a six-shape probe (root, one
savepoint, two deep, inner rollback, root rollback, 50 idle savepoints): one dispatch at the root
commit, no marker or pending entry survives. Additive follow-ups taken before merge: seed test
asserts `one_email: true` (AC-H13 text updated), AC-H24 marker assertions, guide verb list,
checkbox evidence. Recorded, not built: `_send_grouped` ignores `one_email` (seeded row has
grouping off; trigger = an admin wants both); a session closed without commit or rollback keeps
its pending entries until the session is discarded (harmless).

## 7. After merge

Owner types `purchasing@` and the CS manager into the automation's extra emails. Replay on the
0915 copy: amend SO397450 dates as the 21 Jul mail did and compare. Ask CS for the missing
examples (add item, qty increase, location change, explicit replace, a second admin's mails).
Slice 2 (REPLACE ITEM) opens as its own lane after go-live.
