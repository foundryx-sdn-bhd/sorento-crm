# PLAN: a brand follow-up on a document set keeps the document type (ATTACH-SET-BRAND-FOLLOWUP)

Status: in progress (small fix track: backend only, no migration, no auth/RBAC change, no new
ingest surface). Based on PR #1490's branch (ATTACH-GROUNDING-TYPO), where the owner found it.

## Bug (owner hand test, 6 Oct 2026 22:40, #1490 test copy at 117224451, prompt v58)

Dealer contact, General Enquiries:

1. "which tap has technical drawing" -> "Here's what you want: taps with technical
   specifications (298)" and a brand breakdown (Sorento 215, Mocha 42, No Logo 4, Cabana 1).
   Correct: the attachments tool was called with `attachment_type_ids = [Technical
   Specifications]`.
2. "sorento" -> "Here's what you want: Sorento taps with technical specifications (215,
   showing 1 to 50)" followed by rows whose Attachment Type is Product Photos.
   Wrong: the brand narrowed the set, but the attachments tool was called with
   `product_ids` only, so it listed every file of those products (photos first). The header
   and the rows disagree. Owner: "it forgot the attachment type".

## Cause

Turn 1 sends the document type as a resolved `attachment_type` entity: the resolver places
"technical drawing" on an `attachment_types` row, the gate keeps it in `compatible_entities`,
`turn_runtime._entities_for` hands it to the fetch, and `fetch.entity_ids_transformer`
(`TYPE_TO_PARAM["attachment_type"] = "attachment_type_ids"`, `lanes/business/fetch.py:327`)
turns its uuid into the tool argument.

Turn 2 is a set-page turn. `with_brand_from_offer` (`turn_runtime.py:3385`) reads the brand
off the carried `set_key.other_brands`, `turn/apply.py:3634` (`set_brand_chosen`) replans the
turn as a `FetchSpec` whose only filter is `set_page: {set_key}` with no entities, and the
runner (`turn_runtime.py:2900`) builds the fetch entities from the page's product ids ALONE:

```python
if page_predicate is not None:
    entities = [{"uuid": pid, "entity_type": "product", "canonical_code": None} for pid in page_ids]
```

`set_key` (`set_page_carry`, `turn_runtime.py:3292`) describes the set by its `require` leg
(`{"attachment_type": "technical drawing"}`), specs, brand, product ids and access levels.
The `require` leg is enough to RECOUNT the set (`page_the_set` -> `resolve_product_set`
-> `_leg_attachment_type`), which is why the header count (215) is right, but nothing in the
carry says which `attachment_types` row the first answer fetched under, so the page's tool
call has no `attachment_type_ids`. The count answer ("10" after "how many should I show?")
walks the same branch and has the same defect.

## Fix

The document type is part of the set's description, exactly as `access_levels` already is
(security B2 on `set_page_carry`): the same `require` leg fetched under two document types
is two different file lists.

1. `turn_runtime.envelope_of` reports the attachment-type uuids the fetch went out with
   (`attachment_type_ids`, read off the fetch entities, the same rows
   `attachment_type_labels` names).
2. `set_page_carry` takes `attachment_type_ids` and writes it into `set_key` when non-empty;
   `engine.py` passes `envelopes[0]["attachment_type_ids"]` at both carry sites (withheld /
   listed / partly listed, and the offer-only carry). `page_the_set`'s `next_key` and
   `apply.set_brand_chosen`'s `key.update` both spread the old key, so the ids survive a
   brand pick and a further "another N".
3. The runner's page branch appends one `{"uuid": id, "entity_type": "attachment_type"}`
   row per carried id beside the product rows, so `entity_ids_transformer` emits
   `attachment_type_ids` and `attachment_type_labels` names the type for the composer's
   "X has no Y" line on the page too.

No wording changes. #1502 (a product-scoped fetch never takes a set predicate) is the
opposite direction and is untouched: this adds entities to a set-page fetch, it never adds a
predicate to a product fetch.

## Tests (red first)

`tests/chatbot/test_attach_set_brand_followup.py`:

- Two-turn engine test on fake data (real resolver, real brands table, attachments tool
  stubbed to honour `attachment_type_ids` the way the real tool does): turn 1 "which tap has
  technical drawing" (the set is withheld, broken down by brand), turn 2 "sorento". The turn
  2 tool call carries the same `attachment_type_ids` as turn 1, every row is a Technical
  Drawing file, and the header names technical drawings.
- The count path: "10" after turn 1 carries the ids too.
- `set_page_carry` unit: the ids land in `set_key`, and an empty list writes no key.
- `envelope_of` unit: `attachment_type_ids` is the fetch's attachment-type uuids.

Kill test: with the runner's page branch reverted the engine tests go red.

## Console replay

`tests/chatbot/console_cases/2026-10-06-attach-set-brand-followup.yaml`: the two turns as
the owner typed them, cold, graded on the reply header and the rows' Attachment Type.
