# UAC: CHAT-PRODUCT-LABELS

- AC-1: A product info reply (`_products` shape) to an ms contact prints Malay labels for
  Description, List Price, Dimensions and Specs; "Not defined" as a whole value reads in Malay.
- AC-2: The same reply to a zh contact prints Chinese labels and value.
- AC-3: Promotion product and product attachment replies translate every label listed in the plan
  for ms and zh.
- AC-4: Product data values (description text, product name, spec labels inside "Specs") are
  never changed.
- AC-5: "not recorded yet" as a whole value after a catalogued label translates; the access note
  sentence translates with `{label}` preserved verbatim.
- AC-6: Every new entry keeps its `{placeholders}` identical in ms and zh.
- AC-7: A staff-edited `manual` row for a new label wins over the code default.
- AC-8: An English contact's reply is byte-identical to before.
- AC-9: The product, promotion product and product file intros read in ms / zh.
- AC-10: The validity words (Valid, Expiring soon, Expired, Not yet valid, Unknown) and
  "(file link unavailable right now)" translate only as the whole value of the `Validity` /
  `File Link` label; after any other label (an order `Status`) they stay as printed.
