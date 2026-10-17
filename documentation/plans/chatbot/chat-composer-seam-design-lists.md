# CHAT-COMPOSER seam design: other capped lists + "more" (coder-facing)

Lane CHAT-COMPOSER, plan `PLAN-chat-composer-17oct.md` (slice "Lists", after "Sets"). Stacks on PR #1771 (Sets, head
ae10ea38b); branch `crew/chat-composer-lists`. Owner rule 17 Oct (crew 18 Oct): every capped list ends
`Reply "more" to see the next N.`, one WhatsApp message (3900 chars, `composers/sets.py:6`), behind `CHAT_COMPOSER_ENABLED`.
Binding rules from `chat-composer-seam-design-sets.md` (gate pattern, gate off byte-identical, ms/zh in `LANE_LABELS`
gate-on only, no word rule, "more" continues only the LAST reply's list, "That is all N." past the end) apply unchanged.
Paths: B = `sorento_crm_backend/app/services/chatbot`, M = `sorento_crm_mcp/sorento_crm_mcp/presenters.py`.

## 0. Map (18 Oct, head ae10ea38b)

| List | Writer, cap, tail today | Rows held by | Carry for "more" today |
|---|---|---|---|
| L1 code-lookup cards (es-925fab8179, 4330 chars) | B/lanes/business/fetch.py:3621-3633 `output_structurer` plain loop, no cap; up to 20 rows from `entity_resolver._prefix_probe_product` (`PREFIX_LIMIT` 20, entity_resolver.py:2055) | backend (`out["answers"]`, fetch.py:3868) | none: `_set_page` runs only with a predicate (fetch.py:3734, 3803); `focus.set_page` = None (engine.py:7135) |
| L2 outstanding By customer / By product | M:2255 `_outstanding_capped_group`, `OUTSTANDING_GROUP_CAP` 20 (M:2228), tail `... and N more customers` (M:2280) | MCP only; backend gets text + `has_result` (fetch.py:2494-2523) | pending `outstanding_detail` (fetch.py:2527-2538), no offset |
| L3 ref_status PO/PR card (staff) | B/ref_status.py:61 `_lines_text`, `MAX_LINES` 10 (:31), tail `and N more lines` (:65), English only | backend (`.all()`, :97-101, :134-138) | none |
| L4 stock pick "which one?" | B/turn/task.py:865 `pick_question`, `MAX_SLOTS` 20 (:209), tail `and N others, reply with the full code.` (:894); owner PICKER-NO-CAP 2 Oct (:890) | backend group in memory; pending `product_pick` keeps 20 options + count (engine.py:7783-7787) | pending pick, first 20 only |
| L5 "Not checked: A, B ... and N others" | B/turn/task.py:213-219 `_named`, `MAX_NAMED` 10 (:210), used :389 (task over) | `focus.tasks.not_checked` (all) | n/a, closing sentence |
| L6 find "Showing X of Y" | M:1652 `_find_envelope` | MCP | UNREACHABLE from the chatbot since #1613 (35008d5a67): only `contracts.py:112` map entry left |

## Lead decisions 18 Oct (no new rule; override anything below that disagrees)

- LD1. L1 joins now: a code-lookup multi-match is a list like a set page. Gate on: the cards are cut on a card boundary to
  one message, the header gains the range (`Here are the matching products (13, showing 1 to 9).`, the Sets phrase), the
  page ends `Reply "more" to see the next N.`, and a carry of the matched product ids lets "more" print the rest; past
  the end "That is all N.". Gate off untouched.
- LD2. L6 is dropped from this slice (unreachable; see map). Revisit only if a chatbot path to `crm_find` returns.

## CARD DELTA (crew ask 18 Oct; answers go here)

- Q1 L2 outstanding: (a) backend sends an optional `group_offset` tool argument only gate-on; the presenter prints the
  more line and the next 20 groups only when the argument is present, so gate off and other MCP clients are unchanged;
  needs the MCP deploy with the backend. (b) leave out: the existing "Reply 1 for the ... list" offer (M:2440) already
  reaches every row. Recommend (a): owner rule names every capped list; the arg keeps gate off identical.
- Q2 L4 stock pick: (a) page the picker: tail `Reply "more" to see the next N.`, "more" prints options 21-40 numbered
  on, the pending pick grows so any shown number picks; (b) keep as is (owner PICKER-NO-CAP prints every carried option;
  "reply with the full code" already serves the rest). Recommend (a), keeping "or reply with the full code" after the
  more line since a dealer who knows the code should not page.
- Q3 L5 "Not checked ... and N others": (a) not a list (closing sentence, task over, nothing to page): leave, only
  catalogue " and N others" for ms/zh (stays English today); (b) list every name up to the message budget.
  Recommend (a).
- Q4 L3 ref_status (staff-only PO/PR card): (a) page 10 lines per "more"; with several cut cards, "more" continues the
  first cut card, the next "more" the next one; (b) leave out (staff-only internal card). Recommend (a).
- Q5 L6 find: confirm LD2 (drop). Recommend drop.
