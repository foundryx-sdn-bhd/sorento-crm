# n8n integration spec - container status

**Status:** CRM side merged (PR #105), plus `key` on every render field (this branch). n8n side unbuilt.
**Audience:** whoever edits `sorento-consume-main` and `sub-semantic-parser`.
**Companion docs:** `PLAN-container-status-tracking.md` (decision log, D15 / D19 / D21 / D22 / D25a),
`container-status-tracking-acceptance-criteria.md`.

This is the contract, not a workflow design. It says what the CRM now emits, what n8n must do with
it, and which gaps are still ours to close. Where a decision was already taken in the PLAN, this
document repeats the outcome rather than reopening it.

---

## 1. Why n8n has work to do at all

Decision **D15** was reversed during review: there is ONE shipment tool, not a container-status
tool beside it. The CRM dumps every field the caller is entitled to see, and **n8n narrows**.

That split is deliberate and it is the whole of this spec:

| concern | owner | why |
| --- | --- | --- |
| may this contact see `gatepass_date`? | **CRM** | Server-side, on the contact's own grants. n8n's narrowing is a second layer, never the mechanism (D16). |
| the user asked for the ETA, so show the ETA and not 20 other dates | **n8n** | The CRM cannot know which of the returned fields answers the question. |
| is "container status list" a document request or a data question? | **n8n** (routing), **CRM** (resolution) | n8n classifies; the CRM resolves the label to a type UUID. |

**If n8n does nothing, nothing leaks** - the CRM already withholds. What happens instead is that a
contact who asks "when does GXYU5106903 arrive" gets a 20-field dump. The CRM prevents the leak;
n8n prevents the dump.

---

## 2. What the CRM emits today

### 2.1 `crm_incoming_stock_list` with `view=render`

Call it with `contact_id` + `space_id` (the Respond.io contact id and workspace) so the answer is
scoped to that contact's grants. Envelope:

```json
{
  "result_type": "incoming_stock",
  "intro": "Here is the incoming stock I found.",
  "items": [
    {
      "title": "SRT-WC-001",
      "fields": [
        {"key": "product_code", "label": "Product Code", "value": "SRT-WC-001"},
        {"key": "shipping_container_number", "label": "Container", "value": "GXYU5106903"},
        {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-07-18"},
        {"key": "gatepass_date", "label": "Gatepass", "value": "2026-07-22"}
      ],
      "flags": {"discontinued": false, "expired": false,
                "unallocated": false, "partially_allocated": false}
    }
  ],
  "attachments": [],
  "action_links": [],
  "last_updated_at": "2026-08-07T06:31:13",
  "has_result": true,
  "field_access": { "...see 2.3..." }
}
```

**`fields` is variable-length.** A field is present only when it is (a) entitled AND (b) non-empty -
empty pairs are dropped before the item is built. So an absent key means "not entitled OR not
reached yet", and the two are told apart only by `field_access` (2.3).

### 2.2 The field vocabulary - match on `key`, never on `label`

Every field of this result type carries `key`, the CRM's canonical field key. **Project on `key`.**
`label` is display text and is not a stable identifier: two label vocabularies for the same field
already disagree - the render envelope says `ETC`, `field_access.FIELD_LABELS` says
`ETC (estimated time of container closing)`, and casing differs on most of the others. Matching on
label means picking one of the two and being unable to cross-check against the other.

`key` is also exactly what `field_access.denied[].field` reports (2.3), so the entitled / withheld /
not-reached decision compares the same token on both sides. It survives any label rename, and
`requested_attributes` (N1) is already expressed in these keys, so no translation table exists
anywhere.

`key` is **omitted, never null**, where a presenter has no source key (other result types; not the
case for any `incoming_stock` field). Test for its presence, do not assume it.

**Always present (identity - never gated):**

| key | label |
| --- | --- |
| `product_code` | `Product Code` |
| `product_name` | `Product Name` |
| `shipment_number` | `Shipment` |
| `shipping_container_number` | `Container` |
| `batch_number` | `Batch` |
| `remaining_incoming_quantity` | `Incoming Quantity` |
| `warehouse_allocations` | `Warehouse Allocations` |
| `unallocated_quantity` | `Unallocated Quantity` |

**Gated (present only when entitled and filled):**

| key | label | notes |
| --- | --- | --- |
| `estimated_arrival_date` | `ETA` | Ships **allowed** for everyone holding `incoming_stock_enquiries` - today's public answer |
| `eta_delay_date` | `ETA Delay` | |
| `inspection_date` | `CIDB Inspection` | |
| `approval_date` | `CIDB Approval` | |
| `gatepass_date` | `Gatepass` | |
| `warehouse_arrival_date` | `Warehouse Arrival` | |
| `informed_collection_date` | `Collection Informed` | |
| `collection_date` | `Collection` | |
| `loading_date` | `Loading` | |
| `etc_date` | `ETC` | |
| `etd_date` | `ETD` | |
| `liner_code` | `Liner` | |
| `china_forwarder` | `China Forwarder` | |
| `malaysia_forwarder` | `Malaysia Forwarder` | |
| `consignee` | `Consignee` | |
| `delivery_warehouse` | `Delivery Warehouse` | |
| `free_days_available` | `Free Days Available` | |
| `loc` | `Location` | |
| `stacked` | `Stacked` | |
| `coa_permit_no` | `COA Permit No.` | |

Source of truth: `sorento_crm_mcp/presenters.py` `_CLEARANCE_PAIRS`. **If that tuple changes this
table is stale** - re-read it rather than trusting this copy. Sorting and grouping also key off
`key` (e.g. order by `estimated_arrival_date`), never off display text.

**Stock rows** (`crm_inventory_stock_balance_list`) are keyed too, because a cross-domain
stock/incoming block sorts across both: `product_code`, `product_name`, `warehouse`,
`system_location`, `quantity_on_hand`. Note that the last three always render, with `"-"` when
absent, so the row shape never varies - a consumer projecting on `quantity_on_hand` must expect a
non-numeric value there and not coerce it to 0.

Which result types are keyed today: `incoming_stock` (both presenters) and `stock`. The rest still
emit `{label, value}` only. Keying another one is a one-line change per call site now that
`_Builder.item` takes triples - ask rather than rebuilding a label table.

### 2.3 The `field_access` block

Present only when something was withheld. Sibling of `items`, never inside a row:

```json
"field_access": {
  "denied": [
    {"field": "gatepass_date", "agent_code": "incoming_stock_enquiries",
     "outcome": "field_not_allowed",
     "reason": "This contact holds the agent, but this field is not allowed on it. Tick the field on the agent, or add a per-contact override."}
  ],
  "note": "These fields are omitted because this caller may not see them. Absent does NOT mean the value is unknown or not yet reached."
}
```

`outcome` is one of `field_not_allowed`, `agent_not_assigned`, `contact_not_found`. The three need
different admin fixes, which is why they are distinguished - see the PLAN's D38-revised.

Each `denied[]` entry also carries `label`, the CUSTOMER-facing wording, so the refusal can be said
out loud without the consumer inventing a name for the field.

### 2.4 `field_vocabulary` - naming a field that is not there

Every `incoming_stock` envelope carries a top-level `field_vocabulary`: the full clearance key to
customer-facing label map, derived from `_CLEARANCE_PAIRS` so it cannot drift from what a PRESENT
field renders as.

```json
"field_vocabulary": {"eta_delay_date": "ETA Delay", "etd_date": "ETD",
                     "inspection_date": "CIDB Inspection", "...": "..."}
```

It is top level, not inside `field_access`, because the case that needs it has no denial at all:
four containers, one carries `eta_delay_date` and three do not. Nothing was withheld, so there is no
`field_access` block, and the three still have to be named. Harvesting the label off a sibling row
covers the mixed case; nothing covers the case where NO row carries the key, and humanising gives
"Eta delay" and "Etd".

It is a sibling of `items` and nothing rendered it before, so an existing consumer is unaffected.
**Do not ask for empty fields inside `items[].fields` instead** - that would put ~20 blank clearance
rows in every envelope, and a renderer that renders what it is given reads them out to a customer.

Absence is per-ROW (it is data, and differs row to row); denial is per-CONTACT, so it is stated
once.

**n8n must never turn a denial into a value statement.** "There is no gatepass date yet" is a lie
when the truth is "you may not see it". Preferred phrasing: *"I can't share the gatepass date -
please check with the office."* Do not quote `reason` at the contact; it is written for an admin.

---

## 3. What n8n must build

### N1 - requested-attribute matching (the main ask)

**Today:** `sub-semantic-parser` already emits `requested_attributes`, and its prompt already states
the purpose - *"the user asked about, so downstream shows only those - not the whole record"*. The
vocabulary is what is frozen: `incoming: eta`, one value (D25a).

**Required:**

1. The parser emits the attribute the user asked for, in the CRM's canonical field key
   (`gatepass_date`, `eta_delay_date`, ...) or a raw phrase the CRM canonicalises (see C2, unbuilt).
2. `sub-get-results` filters the returned `fields` **on `f.key`** to (identity fields) + (requested
   attributes), and answers from that projection. `requested_attributes` and `key` are the same
   vocabulary, so this is a set intersection with no translation table and nothing to keep in sync.
3. **No requested attribute → do not dump.** Fall back to the identity block plus `ETA`. A user who
   asks "where is my container" wants a sentence, not a table of 20 dates.
4. A requested attribute that is **absent** from `fields`:
 - present in `field_access.denied` → say it cannot be shared (2.3)
 - not in `denied` → genuinely empty; say the step has not happened yet
   That branch is the entire reason `field_access` exists. Collapsing it loses the distinction the
   CRM went to some trouble to preserve.

**Test table** (paraphrases, not keywords - the parser is an LLM, so exercise it as one):

| user says | requested attribute | answer shape |
| --- | --- | --- |
| "when is GXYU5106903 arriving" | `estimated_arrival_date` | ETA only |
| "has it cleared CIDB" | `inspection_date`, `approval_date` | both, or "not yet" |
| "any delay on the ETA" | `eta_delay_date` | delay, else "no delay recorded" |
| "can I collect it" | `gatepass_date`, `collection_date` | denied → "check with the office" |
| "where is my container" | none | identity + ETA, one sentence |
| "who is the forwarder" | `malaysia_forwarder`, `china_forwarder` | both when filled |

### N2 - route document requests to the resources domain

"Send me the container status list" is a **document** request, not a data question. It must reach
`crm_resource_attachments_list`, not `crm_incoming_stock_list`.

**Naming a container makes it a data question again.** "Container status for ABCD1234" routes to
INCOMING even though the phrase "container status" appears in it: the document domain owns the
list / report / sheet / file itself, and a named container is an enquiry about that shipment. The
phrase alone does not decide the domain (user ruling, 2026-08-09; n8n implements this as a code
guard rather than prompt text).

```
crm_resource_attachments_list
  contact_id = <respond contact id>
  space_id   = <workspace space_id>
```

**Pass the document class.** The person named ONE document, so
`attachment_type_code: "Container Status"` is what returns that one file. The name, not the UUID:
the tool takes the class by name, case-insensitive, so nothing has to resolve a type id and there is
no id to go stale. (`attachment_type_id` still takes a UUID if a caller holds one. It is SINGULAR -
there is no `attachment_type_ids`.)

**`contact_id` is a SCOPE, never the filter.** It widens which document types are visible (below),
but it does not satisfy the narrowing requirement: a call carrying only a contact returns nothing,
without reaching the backend. That is deliberate - a document request has to name a document, and
answering "what have you got" with everything a contact may hold is a directory listing rather than
an answer. If no document class resolved, ask which one they want; do not call this tool and read
out whatever comes back.

Three things to know, each of which has already caused a wrong answer:

1. **A narrowing filter is mandatory.** Without one of `attachment_ids` / `directory_id` /
   `attachment_type_id` / `attachment_type_code` / `uploaded_by`, the tool returns an empty page
   **without calling the backend at all**. The MCP log shows no HTTP request, and the agent narrates
   the empty page as "there is no such document". `contact_id` does NOT satisfy this - see above.
2. **`contact_id` + `space_id` widen, never narrow.** Container Status is not a dealer-facing type,
   so it is returned ONLY for a contact granted it (User Management → contact → Document types).
   Omit them and the response is the 9-file dealer baseline - correct, and not what the office
   asked for.
3. **If you do pass a type, it is SINGULAR: `attachment_type_id`, not `attachment_type_ids`.**
   There is no plural form and no array. An unknown key is dropped, which leaves the call with no
   narrower at all, which trips (1). `attachment_type_code: "Container Status"` is the by-name
   equivalent and avoids the whole class of mistake - no UUID to resolve, no plural to get wrong.
   The matcher is permissive: case-insensitive `code`, then case-insensitive `type_name`, then a
   catalog/catalogue spelling-variant pass against both.

   **`fallback_used` answers "did we reach the backend", NOT "is this empty legitimate".** It is
   present on a real call and absent on the no-narrower short-circuit, so it separates those two -
   and nothing else. In particular a type code that matches NO type does reach the backend and
   carries it, and still returns zero rows: `resources_service` applies an impossible-id filter on
   purpose, so a wrong document hint can never leak the wrong files. That is correct behaviour
   which happens to be indistinguishable from "no such document" - a third empty with a third
   cause. Do not read `fallback_used` as a legitimacy signal.

   The three empties, all identical to a caller today:

   | cause | reached backend | why empty |
   | --- | --- | --- |
   | no recognised narrower | no | the tool refused to browse the library |
   | type code matches no type | yes | impossible-id filter, deliberate, stops a bad hint leaking |
   | filter matched nothing | yes | genuinely no such document for this caller |
4. **The type filter alone is not enough.** `visible_type_ids` widens the baseline to
   (is_direct_access types) UNION (types granted to this contact). Container Status is NOT
   is_direct_access, so an ungranted contact gets 0 rows even with the correct parameter. An
   explicit `attachment_ids` is a different path and bypasses the type gate, which is why
   "container status for TCNU1851000" worked while "send me the container status list" did not.
5. **URLs are unsigned by design.** `attachments[].url` is the CDN address
   (`https://<cdn>/import-sources/<uuid>/Container Status 2026.xlsx`); n8n signs on the way out.
   Pass `resolve_signed_urls=true` if a ready-to-open link is wanted instead.

Exactly **one** Container Status workbook is live at any time **per company** - each import trashes
that company's previous ones (`enforce_single_current`, see
`PLAN-container-status-per-company.md`). A contact granted a single company gets a single row; a
contact granted several gets one row per company, each rendered with a `Company` line so two files
both named "Container Status 2026.xlsx" stay distinguishable. `Uploaded` and `last_updated_at`
carry each row's date.

**Every attachment carries its own `uploadedAt`**, so a consumer handing over one of several files
can say how current THAT file is:

```json
"attachments": [{"url": "...", "filename": "Container Status 2026.xlsx",
                 "mimeType": "...", "attachmentType": null,
                 "uploadedAt": "2026-08-08T11:54:05.086571"}]
```

The envelope's `last_updated_at` is the newest across the whole answer, which says nothing about an
individual file once more than one comes back. The key is omitted, never null, when the row has no
upload time.

**All of these are naive Malaysia wall-clock**, like `updated_at` - the sanitizer normalizes
`uploaded_at` before the presenter sees it. It matters beyond politeness: the `Uploaded` line is the
DAY of that instant, so a file uploaded after 16:00 MYT (08:00Z) used to render as the previous
date. No offset suffix, deliberately: an offset-aware string gets re-converted back to UTC by
n8n/luxon for display, undoing the conversion.

**The render envelope no longer carries `File ID`.** It briefly did, so a human could tell which of
several identically-named files was sent - but `render` is the CUSTOMER view, and the uuid went out
on WhatsApp under every document, on every resource-attachment answer, beside the file itself. The
id is still on the raw (non-render) response and in the CRM UI, which is where anyone debugging is
already looking. With the id gone, the `Company` line is the handle that tells identically-named
per-company workbooks apart; it renders only when the row carries a company.

### N3 - the escalation enum

Add the new agent to the `escalated_agent` enum in `sub-get-results` (PLAN S9). Unchanged from the
original plan.

---

## 4. What the CRM still owes n8n

Both of these are **blockers for N2** as written, and neither is n8n's to fix.

> **Closed: C0 - fields did not carry the field key.** n8n would have had to match on `label` and
> hold its own label → key table, which needs a canary asserting all 20 labels still resolve against
> a live response or it is a green that cannot fail. The CRM emits `key` on every field instead
> (2.2), so the table does not exist and there is nothing to keep in step.

### C1 - `attachment_type` resolution is exact-match only

`entity_resolver._probe_attachment_type` matches `lower(code)` or `lower(type_name)` against the
token, exactly. So:

| token | resolves? |
| --- | --- |
| `container status` | yes (type_name) |
| `container_status` | yes (code) |
| `container status list` | **no** |
| `container status report` | **no** |
| `shipping schedule` | **no** |
| `eta list` | **no** |

`container_status_document.KEYWORDS` already lists those aliases, and nothing reads it -
`attachment_types` has no keywords column. This is D21, unbuilt.

**Fix (CRM side):** give `attachment_types` a `keywords` JSONB column, seed it from `KEYWORDS`, and
extend the probe to match it - the same shape `contact_access_types.keywords` already uses for
"customer"/"homeowner" → `end_user`. Until then n8n must send the literal `"Container Status"`,
which means the routing words stay hardcoded in the parser prompt - the thing D22 set out to remove.

**Still blocking, on the main path** (corrected 2026-08-09 after a production run). The n8n
disallowed-entity-gate does disambiguate on `canonical_code`, so the narrowing half works - but
narrowing to the right TYPE is worthless while a type-filtered query cannot be issued under a name
the tool accepts (see the note below on `attachment_type_id`). "Container status list" is what a
person actually types, because "list" is in the document's name. Their finding raises the bar for the eventual CRM fix, though: for a granted
contact, "container status list" already matches THREE types - `Packing List` and `Stock_List` on
the word "list", `container_status` on the word "status". So a `keywords` column must **disambiguate
a multi-way hit**, not merely add aliases; a probe that returns three type ids is a wrong answer
dressed as a match. Word-level matching alone will not do it.

### C2 - `attribute` is not a resolvable reference type

D22's plan is that the parser emits the user's raw phrase and the CRM canonicalises it to a field
key, via `POST /api/v1/system/references/resolve` (a call consume-main already makes every turn).
`attribute` is not yet a registered type there, so today the mapping "gate pass" → `gatepass_date`
has to live in the n8n prompt.

**Fix (CRM side):** register `attribute` as a reference type over `GATED_FIELDS` +
`FIELD_LABELS`, returning `{field_key, label, resource}`.

**Not blocking n8n** as of 2026-08-09 either: the parser fork enumerates all 20 keys with their
trigger phrases and emits every key the user asked about, so "has it cleared CIDB" yields both
`inspection_date` and `approval_date`. That is the interim below, working - it just lives in the
prompt.

**Until C1 and C2 land**, N1 and N2 are buildable with the vocabulary hardcoded in the parser
prompt. That works and is worth doing - it is just the 31KB-prompt-edit problem the PLAN wanted to
retire, so treat the hardcoded version as the interim.

---

## 5. Verification

Do not accept this on a mock. The MCP server answers with real data:

```bash
CRM_BASE_URL=http://localhost:8000 EXTERNAL_API_KEY=<key> python -m sorento_crm_mcp
# then call crm_incoming_stock_list with view=render, contact_id, space_id
```

Cases to run against a real contact, all of which were verified CRM-side and should now be verified
end to end:

1. Contact **with** the gatepass grant asks for it → the value appears.
2. Contact **without** it asks → no `Gatepass` label, `field_access.denied` names it
   `field_not_allowed`, and the reply says it cannot be shared rather than "not yet".
3. Contact **without the agent** asks → same, `outcome: agent_not_assigned`.
4. Every case above still returns `Product Code`, `Container`, `ETA`, `Incoming Quantity` - the
   answer itself is never gated, pinned CRM-side by
   `test_render_never_gates_the_answer_itself`.
5. Contact **granted** the Container Status type asks for the list → one attachment, correct URL.
6. Contact **not granted** it asks → no attachment, and a reply that does not invent one.

A wrong `space_id` resolves to no contact at all and reports `contact_not_found`. If every field
comes back denied, check the workspace before checking the grants.

### What is NOT covered yet (status 2026-08-09)

Case 2, a genuinely DENIED field, is proven only against synthetic envelopes. It has never been run
end to end, and two apparent routes to it are not routes:

- **A no-access contact does not substitute.** Tried with contact `900000109`: access control fires
  at check-access, `get-results` never runs, so there is no envelope to inspect. Access denial and
  FIELD denial are different mechanisms and only the second is under test here.
- **Calling the CRM directly bypasses n8n's own credential.** The n8n side holds no CRM credential
  outside the workflow, so it cannot make the staff-path call that produces a denied envelope.

CRM-side, that call does produce one: `crm_incoming_stock_list` with an ETA window and NO
`contact_id` takes the staff path, is judged on `procurement.packing_lists.view_clearance`, and a
principal without it gets all 20 gated fields in `denied[]` while `estimated_arrival_date` still
ships allowed - a real mixed envelope. Reaching it from n8n needs either that credential or a
partially-granted contact, and creating one is a production write.

Do not read "we tried a contact with no access" as coverage of field-level denial.
