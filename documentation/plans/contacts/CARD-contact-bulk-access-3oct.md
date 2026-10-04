# Behaviour card - CONTACT-BULK-ACCESS: copy one contact's access to many, then check them in the list

**Owner answers (4 Oct 2026):** Q1 (a) the access set only. Q2 (a) replace. Q3 (a) no bulk-set switches now.
Q4 (a) include "access differs from contact X". Q5 the owner sets up the Employee reference contact himself (no code).
Mock v1 approved ("ok").

Lane CONTACT-BULK-ACCESS, size M, 3 Oct 2026. Card before any code. Paths under
`sorento_crm_backend/app/` or `sorento_crm_frontend/` as shown. Researched on main `964f4a3c`.

## 1. The owner's job

About 240 dealer contacts already exist in the CRM (imported into Respond.io and synced). Each needs
the same access as a contact the owner has already configured by hand (the "reference contact"),
except its linked customer, which the owner links himself. Two profiles in the owner's sheet:
"Business Owner/Director (cost related)" (157) and "Employee" (82). Today that is one contact page
at a time, and afterwards the list cannot show who is configured how.

Owner's target for Owner/Director: access types Dealer AND End user (cost lives in the dealer
view), cost visible, escalation to the office OFF, packing list OFF.

## 2. Where a contact's access lives today (measured)

| Piece | Storage | Edited on |
|---|---|---|
| Access types (Dealer, End user, Office ...) | `respond_contact_access_types` M2M (`models/access.py:92`) | Edit contact dialog (`contacts/[id]/components/ContactEditDialog.tsx:286`) |
| Chatbot tier (one value: dealer / office / end_user) | `respond_contacts.chatbot_profile->tier` (`models/access.py:263`) | Chatbot card (`ContactChatbotSection.tsx:199-206`) |
| Switches: stock checks, notify salesman, packing list, ETA buffer, escalation | `respond_contacts` booleans (`models/access.py:272-288`) | Chatbot card (`ContactChatbotSection.tsx:208-245`), PUT `/contacts/{id}/chatbot` (`api/v1/user_management/contacts.py:289`) |
| Field reveals (cost = `purchase_orders.cost`, 7 keys) | `contact_field_reveals` (`services/contact_field_reveal_service.py:40-61`) | Access tab (`ContactFieldRevealsSection.tsx`), PUT `/system/chatbot/contacts/{id}/field-reveals` |
| Agent access (per agent, valid from/to) | `contact_agent_access` (`models/access.py:408`) | Access tab, and today's "Copy settings from contact" |
| Attachment-type grants | `contact_attachment_types` (`models/access.py:447`) | Contact page (`ContactAttachmentTypesSection.tsx`) |
| Market segments | `respond_contact_market_segments` | Contact page (`ContactMarketSegmentSection.tsx`) |
| Stock / spec visibility (per contact) | `stock_visibility_policies`, `spec_visibility_policies` | Contact page sections |
| Linked customers | `respond_contact_customers` | Customers section; bulk "Link customers" exists |
| Not access: CS routing rules, companies, media limits, memory level/facts, outbound switch | various | - |

Today's bulk "Copy settings from contact" (`contacts/components/BulkCopySettingsFromContactDialog.tsx:82-117`)
copies ONLY agent access, one FE request per (target, agent), stops at the first non-duplicate error
with no per-contact result, and never removes an agent the source does not hold.

List filters today: text search, `chatbot_memory_level=own`, `customers=none`
(`api/v1/user_management/contacts.py:87-98`, `services/contact_service.py:102-145`). No column or
filter for tier, cost, escalation, packing list, stock, or a specific customer.

## 3. Proposed behaviour

### 3a. Copy access from a contact (bulk action on the contacts list)

1. Select N contacts in the list (the existing selection + bulk-actions bar), pick "Copy access from
   contact", pick the source contact (searchable, the source itself is excluded from targets).
2. The dialog shows a **preview** per target: one row per contact with what changes, grouped by
   facet, e.g. `Access types: + Dealer`, `Cost: off -> on`, `Escalation: on -> off`,
   `Agents: + order_enquiries`. Contacts already identical show "No change". Linked customers are
   never in the preview and never touched.
3. "Apply to N contacts" runs once. Each target is its own transaction: one failure does not undo
   or block the others. The dialog ends on a **result table**: Updated / No change / Failed (with
   the reason) per contact, plus counts. Never a spinner without an end: the request has a timeout
   and a failure shows per-contact rows or one clear error with "nothing was changed".
4. Copy means **"make the target the same as the source"** for every copied facet (replace, not
   add): a reveal key or agent the target holds and the source does not is removed, and the preview
   shows it in red (Q2).

One server endpoint does both preview and apply, so the preview is exactly what apply would write:

```
POST /api/v1/user-management/contacts/bulk-copy-access
{ "source_contact_id": "...", "target_contact_ids": ["..."], "dry_run": true }
-> { "source": {id, label},
     "results": [ { "contact_id", "label", "status": "changed|unchanged|skipped|failed",
                    "changes": [ {"facet", "label", "before", "after"} ], "error": null } ],
     "counts": {"changed", "unchanged", "skipped", "failed"} }
```

Guarded by `user_management.contacts.edit` (the slug every per-contact access write already uses).
One audit row per changed target. No migration.

### 3b. Contacts list: see and filter access

New columns (hideable via the existing column preferences): **Tier**, **Cost** (holds
`purchase_orders.cost`), **Escalation**, **Packing list**, **Stock checks**. Access types and
Customers columns already exist.

New filters, all kept in the URL next to today's `customers=none` (`contacts/lib/listQuery.ts`):
access type (pick), tier (pick), cost visible (yes/no), escalation (yes/no), packing list (yes/no),
stock checks (yes/no), linked customer (pick one customer), and **"access differs from contact X"**
(reuses the preview's comparison server-side; lists exactly the contacts a copy from X would change).

Cross-check after configuring = filter "Access type: Dealer" + "Escalation: yes" -> should be empty;
or "access differs from <reference contact>" -> should be empty for the configured set.

### 3c. Carry-over to ACCESS-MODEL (#1434)

ACCESS-MODEL replaces field reveals + agent access with roles + per-contact overrides; switches,
access types and customers stay (its card section 3). The copy service is one list of facets, each a
read + write pair, so ACCESS-MODEL swaps two entries ("Field reveals", "Agent access") for
"Roles" and "Overrides" and the dialog, preview, result table and filters are unchanged. The "Cost"
column/filter becomes "has `purchase_cost`" via its `effective_access(contact)`. Throwaway parts:
the two facet entries and the cost predicate, nothing else.

## 4. Examples

Real dev-data examples are not reachable from this cloud sandbox (no shared DB access by contract).
The examples below follow the owner's described case; crew can run the read-only SQL in the plan on
the hand-test copy to attach real rows.

| # | Source -> target | Expected preview |
|---|---|---|
| E1 | Reference contact (Dealer + End user, cost on, escalation off, packing off) -> fresh synced dealer contact (no access types, defaults: escalation on, packing off, stock on) | `Access types: + Dealer, + End user`; `Cost: off -> on`; `Escalation: on -> off`; agents copied with the source's validity dates |
| E2 | same source -> a contact already configured identically, different linked customer | "No change" (customer ignored) |
| E3 | same source -> a contact that also holds `inventory.sellable` | `Field reveals: - Outstanding SO on stock answers` shown in red (replace, Q2) |
| E4 | target list includes the source itself | that row "Skipped: this is the source contact" |
| E5 | a target is deleted between preview and apply | that row "Failed: contact no longer exists"; others applied |

## 5. Edge cases

1. Source holds a reveal key this build does not know (another lane's seed): copied as-is is refused
   by the key list; the key is left untouched on the target and the row notes it.
2. Agent access: target rows are matched by `respond_contact_id`; legacy rows keyed only by phone
   are matched by phone so no duplicate is created.
3. Agent validity dates are copied as the source has them (they all lapse 31 Dec 2026 today;
   ACCESS-MODEL removes the window).
4. Up to 500 targets per call (the whole 240 in one go); more answers 422 with the limit.
5. Concurrent edit between preview and apply: apply recomputes per target and returns what it
   actually wrote; the result table is the truth, not the preview.
6. Caller without `contacts.edit`: the bulk action is hidden and the endpoint answers 403.
7. Company scope: `respond_contacts` has no company column (contacts are global), so the copy reaches
   exactly the contacts the per-contact Chatbot / Access cards already let a `contacts.edit` holder edit.
   The linked-customer filter counts only links inside the caller's company scope (same rule as
   `customers=none`).

## 6. Questions (owner)

Q1. Which facets does "Copy access" copy? (a) the access set: access types, chatbot tier, the five
chatbot switches, field reveals (cost), agent access; (b) (a) plus attachment-type grants, market
segments, stock and spec visibility. Never: linked customers, companies, CS routing, media limits,
memory. **Recommend (a)**: it is exactly what the owner's target config touches; (b) adds four more
sections nobody has asked to copy and each is one more line in every preview.

Q2. Copy = replace or add? (a) replace: the target ends up identical to the source per facet,
removals shown in red in the preview; (b) add only, never remove. **Recommend (a)**: "configure like
X" and the filter "differs from X" only agree if the copy makes them equal; (b) leaves stray cost
reveals in place.

Q3. Bulk-set single switches on a selection without a source (e.g. escalation off for all
selected)? (a) not now; (b) yes, a second bulk dialog. **Recommend (a)**: copying from the reference
contact already does it, and the new filters find any outlier; build (b) when a switch has to change
for a group with no reference contact.

Q4. "Access differs from contact X" filter: (a) include it; (b) only the per-field filters.
**Recommend (a)**: it reuses the preview comparison, and it is the one-click cross-check after a copy.

Q5. The 82 "Employee" contacts: (a) configure one Employee reference contact by hand (e.g. End user
only, cost off, escalation off, packing off) and copy from it; (b) give Employees the same access as
Owner/Director. **Recommend (a)**: the cost reveal is the reason the sheet splits the profiles.

## 7. Follow-ups

- **Brands (owner decision 4 Oct 2026: brands ARE a copy-access facet; built by whichever lane lands
  second).** CONTACT-BRAND-SCOPE adds `respond_contacts.brand_ids` (uuid[], NULL = all brands). Seam in
  `app/services/contact_access_copy_service.py`: one field on `AccessSnapshot` read in `snapshots`
  (keep NULL distinct from `[]`, sort the ids), one block in `diff` (facet `brands`, label "Brands",
  added/removed = brand names, NULL shown as "All brands"; place it after `tier`), one assignment in
  `_write` (the column is on the contact row, like the switches), and one line in `summary`. The list
  gets a "Brands" filter in `ContactService._apply_access_filters` + `ContactAccessFilters.tsx` (pick a
  brand; "all brands" = NULL). The dialog, preview, result table, audit row and "access differs from"
  need no change. If #1463 merges first, crew opens the follow-up against this seam.
- **Agent access writes are open to any signed-in user** (security review of this lane, pre-existing):
  `app/api/v1/user_management/access_agents.py` create/update/delete contact access use only
  `get_current_user`. Out of this lane's scope; worth its own issue.
