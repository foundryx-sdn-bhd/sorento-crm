# UAC - AutoCount ItemType on CRM products (ITEM-TYPE-CRM)

- **AC-1** A product push with `item_type_code: "KITCHEN SINK"` in a company with no such item
  type creates one item type (code = name = `KITCHEN SINK`), links the product to it, and the
  verdict carries the warning `item_type_created`.
- **AC-2** A second push naming the same item type (any case, surrounding spaces) links to the
  existing row; no second row, no `item_type_created`.
- **AC-3** A push that omits `item_type_code`, or sends it blank, leaves the product's stored item
  type unchanged.
- **AC-4** Item types are per company: the same code in another company is a different row.
- **AC-5** `GET /external/contract` lists `item_type_code` under `fields_added.products` and
  `item_type_created` in `warnings`.
- **AC-6** The migration is additive only (new table, new nullable column) and leaves one alembic
  head.
