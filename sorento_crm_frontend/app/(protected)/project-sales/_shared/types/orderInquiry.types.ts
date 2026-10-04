/**
 * Order inquiry rows: what purchasing is told to do (P10, AC-I1 to AC-I7).
 *
 * Quantities are strings, as everywhere else in this module: a 5,950 unit pre-order
 * netted against four dated deliveries has to add up to the unit, and a float round trip
 * does not guarantee that.
 *
 * Nothing here is shown as a UUID. The row carries the sales order NUMBER, the item CODE
 * and the warehouse CODE; the ids are only ever used to address the row.
 */

/** AC-I2's whole vocabulary, exactly as the backend stores it. */
export type OrderInquiryVerb =
  | 'ORDER'
  | 'RESERVE_AND_ORDER'
  | 'ADVANCE'
  | 'DELAY'
  | 'CHANGE_SO'
  | 'CANCEL_BALANCE'
  | 'PRE_ORDERED_DO_NOT_ORDER'
  | 'ALREADY_INBOUND'
  /**
   * The order back: a confirmed borrow left the DONOR location oversold, or CS wrote
   * `ORDER BACK` on the inquiry form against a document already on its way. Its own verb
   * because it is the only one that may be linked to an SPO allocation as well as to a
   * purchase order line (PLAN-scm-purchasing-uat-journey.md section 4b).
   */
  | 'ORDER_BACK'
  /**
   * A planning-change batch released a line's whole claim: the reserve freed at its own
   * location, and this Buy is no longer for this line - it is now for the pool
   * (PLAN-so-book-diff-replanning.md section 6). Informational, like DELAY/ADVANCE.
   */
  | 'RELEASE';

/**
 * `placed` reads "Linked" on every screen (AC-I1) and keeps its stored value: the row is
 * covered by links whose quantities sum to its own. `partly_linked` is the middle the
 * links table made expressible - some of the quantity is on a document, the rest is still
 * demand - and it is counted by `scm.committed_v` for exactly the unlinked remainder.
 */
export type OrderInquiryState =
  | 'raised'
  | 'partly_linked'
  | 'actioned'
  | 'cancelled'
  | 'placed';

/**
 * A "supplied with" bundle (PLAN-scm-supplied-with-companions.md): this row's product
 * rides inside one or more other rows' own line rather than getting its own PO line,
 * derived (never typed) by `ProjectOrderInquiryService.derive_bundles`.
 *
 * Owner ruling 9 Sep (UAC D10): the UI never says "host". `item_codes` is every item the
 * rule names, in rule order - length 1 for the common case (CKSW015 with CKS1050),
 * length 2+ for a pair rule (SC-RL with X + Y). The worklist headline reads the single
 * code when there is one, `N items` otherwise; the lightbox names every one of them.
 * `row_id` addresses the ANCHOR row (the rule's first matching item) - the info icon
 * opens that row's own lightbox rather than this one's, since a fully-bundled row has no
 * backing documents of its own to show.
 */
export interface OrderInquiryBundledWith {
  row_id: string;
  item_code: string;
  item_codes: string[];
  /** The anchor row's OWN coverage ("1 of 1"), resolved server-side (review round 1
   * item 8) - never resolved client-side by scanning a page's own loaded rows, which
   * is only ever right when the anchor happens to be on the SAME page as this row.
   * Null when the anchor has no links of its own yet ("Nothing linked yet", S5). */
  anchor_headline: string | null;
}

/**
 * One HOST's own change (`PLAN-oi-bundled-row-host-change.md`), read from that host's
 * own live row at display time - a bundled companion has no sheet row, no PO and no Was
 * of its own, so the (i) reads each host's instead. `qty`/`delivery_date`/`previous_qty`/
 * `previous_delivery_date` are all null when the host has no open row on the same order
 * inquiry header.
 */
export interface OrderInquiryBundledHostChange {
  item_code: string;
  qty: string | null;
  delivery_date: string | null;
  previous_qty: string | null;
  previous_delivery_date: string | null;
}

/** One PRIOR raise of the same SO line under the same inquiry
 * (PLAN-oi-worklist-split-customer-project.md, Slice 2) - the Raised at cell's own
 * tooltip. */
export interface OrderInquiryRaiseHistoryEntry {
  raised_at?: string | null;
  raised_by_name?: string | null;
}

/**
 * One placement: this row's quantity, or part of it, sitting on ONE purchase order line
 * or ONE SPO allocation (`projects.order_inquiry_links`, PLAN section 3.I). A row keeps
 * its FULL quantity and carries many links - never the split rows the cascade used to
 * write, which turned nine sales-order lines into eleven instructions.
 */
export interface OrderInquiryLink {
  id: string;
  /** Which book the document lives in. `spo` is only ever offered to an ORDER BACK row. */
  kind: 'po' | 'spo';
  /** `202607-S0105`, `SPO-2026/08-0061`. Never an id. */
  document: string;
  /** `L3` when the book numbered the line. Absent when it did not - never invented. */
  line_label?: string | null;
  qty: string;
  /** Where that document line lands the goods. Blank when the book names none. */
  location?: string | null;
  /** The document's own date, and the line's promised arrival. */
  issue_date?: string | null;
  expected_date?: string | null;
  /**
   * Q5's location fit, 1 to 5: 1 the row's own location, 2 the same group at another
   * site, 3 a site pool, 4 a sibling location at the site, 5 anywhere else. Never a
   * filter, only a rank - a link outside tier 1 is the split instruction for AutoCount.
   */
  tier?: number | null;
  /**
   * The document arrives AFTER the row's own required date (AC-P3-7). Stated, never acted
   * on: purchasing decides whether a late document is still the answer, and unlinking it
   * for lateness would leave the row with nothing rather than with something late.
   */
  late?: boolean;
  /**
   * BY HOW MUCH it is late, in days between the row's own required date and the
   * document's expected date (AC-D17). Null or absent when the document is not late.
   * `late` says THAT it arrives after the row needs it; this says how far after, which
   * is the difference between "look at this" and "this is fine".
   */
  late_days?: number | null;
  /** Written by the cascade rather than by a person. */
  auto?: boolean;
  linked_at?: string | null;
  /** WHO linked it, by name. Null on a cascade link, which nobody did by hand. */
  linked_by_name?: string | null;
  /** Addresses the PO popover. Null on an SPO link - there is no purchase order to open. */
  po_id?: string | null;
  /**
   * Issue #1215 point 2: which PO line this link sits on, so the lightbox can
   * highlight it on the lines grid. Null on an SPO link.
   */
  po_line_id?: string | null;
  /**
   * R15 (owner rulings, 25 Sep 2026, hand test on stack C): the mirror of `po_line_id`
   * above for the other book - which `spo_allocations` row this link sits on, so the
   * SPO lightbox can highlight it the same way the PO lightbox already does. Null on a
   * `po`-kind link.
   */
  spo_allocation_id?: string | null;
  /**
   * R17 (owner rulings, 25 Sep 2026): the purchase order an SPO link's allocation
   * draws its supply from (`SPOAllocation.po_line_id` traced to its own header) - lets
   * the "via SPO" PO cell open that PO directly rather than guessing by number. Null
   * on a `po`-kind link and on an SPO allocation with no resolved supply PO line.
   */
  purchase_order_id?: string | null;
  /**
   * The purchase order an SPO link's allocation was raised FROM, per the AutoCount
   * feed's own statement (owner's 9 Sep feedback: "if we link by SPO, where do we see
   * the PO number of this SPO?"). Plain text, never a link yet - a later slice decides
   * where it goes. Null on a `po`-kind link and on an SPO the book named no source for.
   */
  source_po_number?: string | null;
  /**
   * WRITTEN NOWHERE (PLAN-scm-oi-worklist-excel-parity.md, R-E): an SPO entry the SPO
   * column shows because the row's linked PO line has an open SPO allocation for the
   * same product, marked "via PO" - not a link a person or the cascade made. Absent or
   * false on every link written today.
   */
  derived?: boolean;
  /**
   * The mirror: this `po`-kind entry's `source_po_number` is itself read off an SPO
   * link, so the PO column marks it "via SPO" (R-E). Absent or false otherwise.
   */
  derived_po?: boolean;
  /**
   * The document this link points at is FULLY received (`PLAN-oi-replan-received-links.md`
   * S1, AC-RL-17): a PO line whose `qty_received >= qty_ordered` or `line_status =
   * 'closed'`, or an SPO allocation that fails `open_incoming_clauses()`. Goods that have
   * landed are location stock, not a promise still in transit - the PO/SPO chip and the
   * backing-documents dialog mark it so purchasing reads it as history rather than as
   * something still to chase. Absent or false on an open document.
   */
  received?: boolean;
  /**
   * How much of THIS link's document line has been received: `PurchaseOrderLine.
   * qty_received` for a `po`-kind link, `SPOAllocation.quantity_received` for `spo`.
   * Present on every link regardless of `received` - a partly received document states
   * the figure too, even though `received` itself only flips true once the WHOLE line is
   * done. Null or absent when the book states no receipt yet.
   */
  received_qty?: string | null;
  /**
   * S1b (`PLAN-oi-replan-received-links.md`, AC-RL-20 to AC-RL-24): a concrete
   * instruction on an open link that has drifted outside the product's lead-time
   * window - never a reason, never "early" (owner ruling 16 Sep), never the word
   * "repoint" on screen (ruling 17 Sep). `reallocate` names EVERY other linkable row
   * of the same product with open need, earliest first - the first is the suggested
   * target; `unlink` means there is no such row. Null on a received link or one still
   * inside the window. Nothing is written from the chip's own lightbox - purchasing
   * acts in AutoCount, and S5 (our link follows the book) reacts to that.
   */
  suggestion?: OrderInquiryLinkSuggestion | null;
}

/**
 * One document line the cascade SUGGESTS for this row - never an `order_inquiry_links`
 * row (`PLAN-oi-links-autocount-truth-24sep.md` section 3.3, owner ruling "the idea is
 * the suggested link shouldn't be counted as real link and actually appearing in the PO
 * or SPO column"). Always called a SUGGESTED LINK, in code and on screen - unrelated to
 * `OrderInquiryLink.suggestion` (S1b's `reallocate`/`unlink` advice on a REAL link),
 * which keeps its own name so the two concepts can never be confused.
 *
 * `suggested_links` rides on the row (`OrderInquiryRow`/`OrderInquiryWorklistRow`) only -
 * dropped from the lightbox payloads (`OrderInquiryPoDetail`/`OrderInquirySpoDetail`,
 * review round 2 Should fix 5) once R13 retired the lightbox's own separate "Suggested
 * for" panel: the row's own array and the Lines tab's Allocated/Suggested columns are
 * the whole answer now. Separate from `links` either way - a row's PO/SPO cells and
 * State pill never read this array (section 3.5/3.7).
 */
export interface OrderInquirySuggestedLink {
  /** Which book. `spo` only on an ORDER BACK row, same as a real link. */
  kind: 'po' | 'spo';
  /** `202607-S0105`, `SPO-2026/08-0061`. Never an id: what the buyer quotes. */
  document: string;
  /** Addresses the PO lightbox. Null on an SPO suggestion. */
  po_id?: string | null;
  po_line_id?: string | null;
  spo_allocation_id?: string | null;
  location?: string | null;
  qty: string;
  expected_date?: string | null;
  /** BY HOW MUCH the suggested document lands after the row's own required date - same
   * convention as `OrderInquiryLink.late_days`. Null or absent when it is not late. */
  late_days?: number | null;
  /** Which cascade door wrote it (section 2.5/3.4) - never rendered, `converted` only
   * ever appears after the S5 conversion script (section 3.8). */
  trigger?: string | null;
}

/** One row a `reallocate` suggestion could move the link to (AC-RL-20). */
export interface OrderInquiryLinkSuggestionCandidate {
  inquiry_no: string | null;
  item_code: string | null;
  so_number: string | null;
  delivery_date: string;
  open_qty: string;
}

/**
 * A `reallocate` suggestion names EVERY sooner row with open need, earliest first -
 * the first is the suggested target (17 Sep rulings); `unlink` names none.
 */
export type OrderInquiryLinkSuggestion =
  | {
      kind: 'reallocate';
      candidates: OrderInquiryLinkSuggestionCandidate[];
    }
  | { kind: 'unlink' };

/**
 * The HANDSHAKE (`PLAN-scm-oi-handshake.md`), beside `state` and never merged with it:
 * `state` says where the quantity sits, this says whether purchasing has taken the
 * instruction on. Nothing links to a row before it is acknowledged.
 */
export type OrderInquiryAckState = 'awaiting' | 'acknowledged' | 'changed' | 'rejected';

/** The four facts a row carries about its handshake, on every reader of a row. */
export interface OrderInquiryAckFields {
  ack_state?: OrderInquiryAckState | string;
  /** Who took it on, by name. Never an id: the column prints this as it comes. */
  acknowledged_by_name?: string | null;
  acknowledged_at?: string | null;
  /** Who refused it, and why. Both set only on a rejected row. */
  rejected_by_name?: string | null;
  rejected_at?: string | null;
  rejected_reason?: string | null;
  /** When CS last amended a row purchasing had already acknowledged. */
  changed_at?: string | null;
  /**
   * What the row said BEFORE that amendment - the Was half of the Was / Now table. Two
   * figures off the row itself; the note beside them says the same thing as prose for a
   * person, and nothing parses it back.
   */
  previous_qty?: string | null;
  previous_delivery_date?: string | null;
  /** OI-PRODUCT-FOLLOW: the item code before a Confirm moved the row onto the SO line's
   * new product - the "was X" under the Product cell. */
  previous_item_code?: string | null;
}

export interface OrderInquiryRow extends OrderInquiryAckFields {
  id: string;
  order_inquiry_id: string;
  so_line_id?: string | null;
  project_sales_order_id?: string | null;
  /** The AutoCount document number when it has been adopted, else our own reference. */
  sales_order_ref?: string | null;
  /**
   * The trace back to the decision that raised the row (AC-D06), added by Stage 1C: the
   * sales order LINE number, the Project SO's own reference, and which confirmed revision
   * decided the quantity. Human identifiers only - a buyer expanding a Project
   * contribution reads the same words CS confirmed it with, never an id.
   */
  line_no?: number | null;
  project_so_ref?: string | null;
  decision_revision?: number | null;
  so_date?: string | null;
  project_customer?: string | null;
  is_amendment?: boolean;

  item_code?: string | null;
  qty: string;
  delivery_date?: string | null;
  /** Empty until an allocation is confirmed (AC-H5). Never defaulted to a location. */
  stock_location?: string | null;
  verb: OrderInquiryVerb | string;
  /** The verb in the client's own spelling, or the SPO reference for an inbound row. */
  remark?: string | null;
  spo_ref?: string | null;
  /** Which pre-order or inbound shipment covers this quantity (AC-I3a). */
  covered_by?: string | null;
  /** The date a DELAY moved from, the sales order a CHANGE SO points at. */
  note?: string | null;
  /**
   * Every document this row is linked to, and how much of it sits there
   * (`projects.order_inquiry_links`). Empty on a raised row. `po_ref` above is the FIRST
   * link's document, kept as the one-word display the older screens read.
   */
  links?: OrderInquiryLink[];
  /**
   * The sum of `links[].qty` for the REAL links only - `links` also carries synthetic
   * "via PO" entries (`derived: true`) for a linked PO's own open SPO allocations, which
   * never wrote an `order_inquiry_links` row and are excluded from this sum.
   * `qty - linked_qty` is what still flows to reorder planning.
   */
  linked_qty?: string;
  /**
   * `PLAN-oi-links-autocount-truth-24sep.md` section 3.5: what the cascade suggests for
   * this row's unlinked remainder - never a real link, never read by `state`/`po_ref`/
   * `spo_ref`. Empty or absent on a row the cascade has not covered (S2 mock: absent on
   * every row the real backend answers today, until S3/S4 land).
   */
  suggested_links?: OrderInquirySuggestedLink[];
  /** The document CS cited on an order back, which the cascade tries before any other. */
  cited_document?: string | null;
  po_ref?: string | null;
  po_line_id?: string | null;
  /**
   * Whether this row has anywhere to link to at all. Verb AND product, not product alone:
   * an ORDER BACK row may link to an SPO allocation as well as to a purchase order line
   * (part 2 section 4b), so a flag that only counted purchase orders hid the Link action
   * on the one row the feature was built for. The row action offers Link only when this
   * is true - no dead end where the dialog opens to say there is nothing to link.
   */
  has_link_candidate?: boolean;

  state: OrderInquiryState | string;
  actioned_at?: string | null;
  actioned_by_name?: string | null;
  created_at?: string | null;
}

export interface OrderInquiryDetail {
  id: string;
  project_sales_order_id: string;
  amendment_id?: string | null;
  state: string;
  raised_at?: string | null;
  /** The purchasing task the rows are attached to (AC-I4). */
  task_id?: string | null;
  task_name?: string | null;
  rows: OrderInquiryRow[];
}

export interface OrderInquirySummary {
  total: number;
  raised: number;
  actioned: number;
  cancelled: number;
}

export interface OrderInquiryListParams {
  query?: string;
  verb?: string;
  state?: string;
  sales_order_id?: string;
  page?: number;
  limit?: number;
  sort?: string;
  dir?: 'asc' | 'desc';
}

export interface OrderInquiryListEnvelope {
  data: OrderInquiryRow[];
  total: number;
  page: number;
  limit: number;
}

/* -------------------------------------------------------------- the worklist
 *
 * Purchasing's own list, across every project AND every adopted AutoCount order. The
 * per-project list above answers "what did this project raise"; this one answers "what
 * do I have to buy", which is a different question with a different owner, and the rows
 * an adopted order raises belong to no project at all so they appear on no other screen.
 *
 * The columns are the ones on the spreadsheet purchasing already works from
 * (`JAN - DEC 2026 ORDER.xlsx`, one sheet per delivery month), in its order, so the
 * screen and the file can be read side by side.
 */

export interface OrderInquiryWorklistRow extends OrderInquiryAckFields {
  id: string;
  /**
   * `OI-000123` - the number of the inquiry this row belongs to, off its own header.
   * What a person calls the instruction: "the inquiry on SO414033" stops being an answer
   * the moment an amendment raises the second one on that order.
   *
   * Optional only because a row written before the column existed carries none; every
   * inquiry raised since is stamped with one.
   */
  inquiry_no?: string | null;
  /** The core sales order's order date. The date on the document, not the raise date. */
  so_date?: string | null;
  so_number?: string | null;
  item_code?: string | null;
  /** Addressing only, never rendered - two products share one item code on the live
   * book, so the stock grid (`OrderInquiryStockGrid`) keys on this, never `item_code`. */
  product_id?: string | null;
  product_name?: string | null;
  qty: string;
  delivery_date?: string | null;
  /** `BUIMACO / TUJU RESIDENCE`, or the core order's customer when there is no project.
   * Stays for the Excel export and the search box; the worklist screen itself prints
   * `customer_name` and `project_title` below as two columns instead. */
  project_customer?: string | null;
  /** The Customer column, split out of `project_customer` (PLAN-oi-worklist-split-
   * customer-project.md). */
  customer_name?: string | null;
  /** The Project column, split out of `project_customer`. Carries the same PRE-ORDER
   * note the combined field does. */
  project_title?: string | null;
  /** Blank until a purchase order the row can be traced to exists. Never a guess. */
  supplier?: string | null;
  supplier_id?: string | null;
  po_number?: string | null;
  /**
   * Where the PO gets placed for: the donor an order-back row left oversold, the
   * confirmed allocation's warehouse for a plan/confirmed row, otherwise the line's own
   * fulfilment location. Blank when neither is known.
   */
  location?: string | null;
  /**
   * What flows to reorder planning, for this row's own SO line (the captain, 20 Aug:
   * "show the quantity, quantity taken from PO, and the remaining quantity, cause this
   * is what flows to reorder planning"). `taken_from_po` sums every SIBLING placed
   * ORDER row on the same line; `remaining_open` sums every raised ORDER row on the
   * line - `committed_v`'s own confirmed leg, what still counts as demand. On a raised
   * row that includes itself.
   */
  taken_from_po?: string;
  remaining_open?: string;
  /**
   * Where this row's quantity actually sits (AC-I5): one entry per link, PO or SPO. The
   * "Linked to" column reads `linked_qty of qty` and then names each document. Empty on a
   * row nobody has linked - which is what "still to link" looks like.
   */
  links?: OrderInquiryLink[];
  linked_qty?: string;
  /**
   * `PLAN-oi-links-autocount-truth-24sep.md` section 3.5: the Suggested column, after
   * SPO, on both the worklist and the OI detail Lines tab (`orderInquirySuggestedColumn`
   * reads this - never `links`). Absent or empty on every row today (S2, FE mock).
   */
  suggested_links?: OrderInquirySuggestedLink[];
  /**
   * PLAN-scm-supplied-with-companions.md, S2. `bundled_qty` never exceeds `qty - linked_qty`
   * (a link a person already made stays a link); `bundled_with` is null on an un-bundled
   * row, or when a rule matched but the bundle worked out to 0 (e.g. supplier mismatch,
   * an unmet pair, or the rule's own item is inactive).
   */
  bundled_qty?: string;
  bundled_with?: OrderInquiryBundledWith | null;
  /**
   * PLAN-oi-bundled-row-host-change.md. One entry per host item code, in rule order,
   * for a bundled row - null on a non-bundled row. The companion has no Was of its own
   * (no sheet row, no PO), so the (i) reads each host's OWN change instead.
   */
  bundled_host_changes?: OrderInquiryBundledHostChange[] | null;
  /** The document CS cited on an order back. Named on the row so the walk can honour it. */
  cited_document?: string | null;
  /** Same as `OrderInquiryRow.has_link_candidate`, for this cross-project worklist. */
  has_link_candidate?: boolean;
  /**
   * The row's linked document turned out to be FULLY RECEIVED by the time a replan met it
   * (`PLAN-oi-replan-received-links.md`, S3): goods already shipped to other orders are
   * location stock, not this line's any more, so settle-in-place did not carry the row -
   * it kept its old `qty`/`delivery_date`/`links` as history and a fresh ORDER row was
   * raised for the full need instead. The Qty cell marks it and the Buy / Purchased /
   * Incoming card totals ignore it (AC-RL-04, AC-RL-16) - it is not owed anywhere any
   * more. Absent or false on every row today (0 rows on prod as of 16 Sep 2026; this
   * plan is the first writer of the column).
   */
  redirected_to_pool?: boolean;
  /**
   * PLAN-oi-cancelled-line-used-confirm.md (AC-CL-1): true when the sales order line
   * this row sits on has `line_status = cancelled`. Greyed the same way a used row is,
   * with its own `cancelled` pill beside the quantity, and excluded from Buy only -
   * Purchased/Incoming still count it when it holds a link (AC-CL-4). Absent or false
   * on every row before this plan.
   */
  line_cancelled?: boolean;
  /** `PLAN-oi-request-cs-reserve.md` 3.5 (AC-RS-20): `requested` while an open reserve
   * request row exists, `reserved` once CS has actually reserved something (and no open
   * request), `declined` when CS's latest answer was 0 (6e.4), else null. */
  reserve_state?: 'requested' | 'reserved' | 'declined' | string | null;
  /** 3.4 (AC-RS-12): the sum of the row's reserve links - already included in
   * `taken_from_po`/`remaining_open`, both of which sum every link with no target filter. */
  reserved_qty?: string;
  /** Round 4 (`PLAN-oi-request-cs-reserve.md` 6e.2): the OPEN reserve request row's
   * own `qty_requested` for this row - "0" when `reserve_state` is not `requested`.
   * The Lines grid's `Request to reserve N` pill and the tick's default stage both
   * read N off this. */
  requested_qty?: string;
  /** Who sold it (`sales_orders.sales_agent_id` -> `sales_agents`), off the same core
   * sales order the S/O no column reaches. Null when the row reaches no core order, or
   * that order carries no agent. */
  agent_code?: string | null;
  agent_label?: string | null;
  state: OrderInquiryState | string;
  /** When purchasing was told. The spreadsheet's per-day tabs are this date. */
  raised_at?: string | null;
  /**
   * WHO told them, by name: the person who confirmed the supply revision that raised
   * THIS row, falling back to the inquiry header for an amendment-born row that has no
   * revision. Per row, never off the header alone - the header is re-stamped on every
   * reconfirm, so it would name the latest reconfirmer beside an older row's own clock.
   * Never an id: the cell prints this as it comes. Null when nobody was recorded.
   */
  raised_by_name?: string | null;
  /**
   * The Raised at cell's own tooltip (PLAN-oi-worklist-split-customer-project.md): a re-confirm
   * cancels a carried line's row and raises a fresh one, so `raised_at` above moves on
   * to that re-confirm's own time - this is where the earlier raise(s) still live.
   * Newest first. Empty on a row with no SO line, or nothing prior. Never on the Excel
   * export.
   */
  raise_history?: OrderInquiryRaiseHistoryEntry[];
  /**
   * AC-DT-3 (`PLAN-oi-decision-trail-ui.md`): the actual `order_inquiry_raises` EVENT
   * this row traces to - Raised or Reconfirmed, by whom, when - distinct from
   * `raised_by_name`/`raised_at` above, which name WHO currently owns the row (a
   * coalesce, not an event). `null` on all three when nothing matches - a row migrated
   * before raises were recorded.
   */
  raise_event_kind?: 'raised' | 'reconfirmed' | null;
  raise_event_by_name?: string | null;
  raise_event_at?: string | null;
  verb: OrderInquiryVerb | string;
  note?: string | null;

  /** Addressing only, never rendered: how the row reaches its sales order. */
  project_id?: string | null;
  project_sales_order_id?: string | null;
  core_sales_order_id?: string | null;
  /**
   * S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-1/AC-B6-7): the AutoCount line number,
   * for the "SO line" column's own text (`SO402757 · L5`, `orderInquirySoLineLabel`).
   */
  line_no?: number | null;
  /**
   * `PLAN-oi-no-double-count-25sep.md` S1 (AC-ND-21): the sales order line's own Qty and
   * No., the numbers the sales order's Lines grid shows - the SO Qty column (owner ruling
   * 26 Sep, G4). Null when the row names no sales order line.
   */
  so_line_qty?: string | null;
  so_line_no?: number | null;
  /**
   * PR #1266 review S1: the row's MIRROR sales order line, one-to-one with the core
   * line but present before AutoCount reconciles it (`core_line_id` above is null until
   * then). The Lines tab folds on it, the same key the header's Lines count reads (G10).
   * Null when the row names no sales order line.
   */
  so_line_id?: string | null;
  /**
   * S6 (AC-B6-1/AC-B6-7): the CORE sales-order line's own id, which the backend resolves
   * server-side off the row's mirror line - never the mirror id itself, which this screen
   * has no use for. Addresses the "SO line" link's own `?line=` param
   * (`orderInquirySoLineHref`); null when the mirror reaches no core line, in which case
   * the cell reads as plain text, the same fallback `orderInquiryRowHref` already uses
   * for a row that reaches no book at all.
   */
  core_line_id?: string | null;
  /** Came from the AutoCount book rather than a document authored here. */
  is_adopted?: boolean;
  /**
   * The placed purchase order this row traces to (same coalesce the `po_number` column
   * reads) - addresses the "PO no" cell's popup, `GET .../order-inquiries/po/{po_id}`.
   * Null on a row nobody has placed yet.
   */
  po_id?: string | null;
}

export interface OrderInquiryWorklistParams {
  query?: string;
  /**
   * `PLAN-oi-header-list-detail.md`, S3/AC-DT-02: every non-cancelled row of ONE header -
   * the OI detail page's own Lines tab, Export Excel, and a whole-OI Confirm's `filter`
   * payload (AC-CF-01).
   */
  inquiry_id?: string;
  /** `PLAN-oi-no-double-count-25sep.md` S2 (AC-ND-20): cancelled rows too, for the OI
   * detail's Lines tab and its History dialog. */
  include_history?: boolean;
  /** `YYYY-MM`, the delivery month, which is the sheet tab. */
  delivery_month?: string;
  /** `YYYY-MM-DD`, the day the rows were raised, which is the per-day tab. */
  raised_date?: string;
  state?: string;
  project_id?: string;
  /**
   * S5 (`PLAN-oi-project-label-from-so.md` section 5): the Project column's own text,
   * exact match - a registered project's title or an adopted order's SO-level label,
   * off the summary's own `projects` facet. Separate from `project_id`, which stays
   * UUID-only for an existing deep link; the filter picker sends this one now.
   */
  project?: string;
  supplier_id?: string;
  /** The id of the person who raised the rows, picked off the summary's own list. */
  raised_by?: string;
  /**
   * Where the row is linked (AC-I5). `po` and `spo` mean "has at least one link of that
   * kind"; `none` means no link at all, which is the buyer's own worklist.
   */
  linked?: 'po' | 'spo' | 'none';
  /**
   * WHAT the row still needs, which is the cards' own filter (AC-I11) and a different
   * question from `linked`: a row linked 5 of 8 to a purchase order carries `po` AND
   * `buy`, so it answers to either card, while `linked` asks only where its links point.
   * A cancelled row carries no kind at all - its quantity is not owed any more.
   */
  kind?: OrderInquiryKind;
  /**
   * WHERE THE HANDSHAKE STANDS (AC-H4). A third question beside `state` and `linked`:
   * purchasing's own worklist is "what have I not acknowledged yet", and CS reads the
   * same list for "what has purchasing refused".
   *
   * `to_confirm` is the page's own default (R3): awaiting AND changed in one word,
   * because both mean the same thing to purchasing - nobody has said yes to this yet.
   */
  ack?: OrderInquiryAckState | 'to_confirm';
  /** The row's Location column, equality (S1, R-K). Warehouse code, never a UUID. */
  location?: string;
  /** The row's Agent column, equality (S1, R-K). The sales agent's id. */
  agent?: string;
  /** `YYYY-MM` on the SO date (S1, R-K) - the "SO month" filter. */
  so_month?: string;
  /**
   * Prefix, case-insensitive (S1, R-K): a row's PO link document, or an SPO link's
   * `source_po_number`.
   */
  po_number?: string;
  /** Prefix, case-insensitive (S1, R-K): a row's SPO link (own or derived, S5). */
  spo_number?: string;
  /**
   * The bounds of one schedule-matrix cell's own period (S3): a click's drilldown asks
   * the list for exactly this cell's axis + period rather than reading rows back out of
   * the matrix response. `YYYY-MM-DD`, inclusive both ends.
   */
  delivery_from?: string;
  delivery_to?: string;
  /**
   * The same cell's axis (S3): equality on the very column the matrix grouped by, which
   * is what the drilldown narrows on rather than a text match on the printed label. Both
   * together or neither - `axis` names the column, `axis_key` the value.
   */
  axis?: OrderInquiryMatrixAxis;
  axis_key?: string;
  page?: number;
  limit?: number;
  sort?: string;
  dir?: 'asc' | 'desc';
}

/**
 * The matrix's own request (S3): the same list filters, plus which axis and which date
 * cut. `GET {BASE}/order-inquiries/matrix?axis=&by=&<list filters>`.
 */
export interface OrderInquiryMatrixParams extends OrderInquiryWorklistParams {
  axis: OrderInquiryMatrixAxis;
  by: OrderInquiryMatrixGranularity;
}

export interface OrderInquiryWorklistEnvelope {
  data: OrderInquiryWorklistRow[];
  total: number;
  page: number;
  limit: number;
}

export interface OrderInquiryMonthTotal {
  /** `2026-01`. */
  month: string;
  /** `JAN 26`, spelled the way the sheet tab is. */
  label: string;
  rows: number;
  qty: string;
}

export interface OrderInquiryFacet {
  id: string;
  label: string;
  rows: number;
}

/**
 * The three answers purchasing has for a row (section 3.I2): the quantity is on an SPO
 * already on its way, it is on a purchase order, or nobody has put it on anything.
 * `_shared/lib/orderInquiryKinds` holds the words, the colours and the arithmetic.
 */
export type OrderInquiryKind = 'spo' | 'po' | 'buy';

/** The `kinds` facet: quantity, per kind, over every matching row (AC-I11). */
export interface OrderInquiryKindTotals {
  spo: string;
  po: string;
  buy: string;
}

export interface OrderInquiryWorklistSummary {
  /** The visible set: every filter applied, the month included. */
  total_rows: number;
  total_qty: string;
  by_state: {
    raised: number;
    /** Some of the quantity is on documents, the rest is still demand (section 3.I). */
    partly_linked: number;
    actioned: number;
    cancelled: number;
    /** Wholly covered by links. Stored as `placed`, read as "Linked" (AC-I1). */
    placed: number;
    total: number;
  };
  /**
   * The axes the screen's own controls are built from. Each is computed with every
   * filter EXCEPT its own, because a control that empties itself the moment you use it
   * cannot be used a second time.
   */
  by_month: OrderInquiryMonthTotal[];
  suppliers: OrderInquiryFacet[];
  projects: OrderInquiryFacet[];
  /** The people who raised the rows in view, id + name. The "Raised by" filter's list. */
  raised_by: OrderInquiryFacet[];
  /**
   * The Location and Agent filters' own lists (S1, R-K), each computed with its own
   * filter dropped like every other axis here. Optional while the backend still answers
   * without them (Phase 1 mock) - the select then renders empty rather than erroring.
   */
  locations?: OrderInquiryFacet[];
  agents?: OrderInquiryFacet[];
  /**
   * The three cards above both views: quantity linked to SPO allocations, quantity
   * linked to purchase order lines, and the unlinked remainder that still has to be
   * bought. Computed over every matching row rather than the page on screen - a card
   * that counted one page would claim less than pressing it reveals - and with the
   * `kind` filter itself dropped, like every other control here, so pressing one card
   * leaves the other two readable.
   */
  kinds: OrderInquiryKindTotals;
  /**
   * How many rows sit at each acknowledgement state (AC-H4) - the Acknowledgement
   * filter's own counts, computed with that filter dropped like every other control
   * here. Optional so a page rendered against an older answer still reads.
   */
  ack?: OrderInquiryAckCounts;
  /**
   * Where the page's "Link up to" date starts (AC-LH5): the latest completed reorder
   * run's own "Plan until", `YYYY-MM-DD`. Read from here rather than invented on the page,
   * so the plan and the buyer cannot be working to two different horizons. Null or absent
   * means no run has named one, and then no horizon is in force.
   */
  link_up_to_default?: string | null;
}

/** The four counts behind the Acknowledgement filter. */
export interface OrderInquiryAckCounts {
  awaiting: number;
  acknowledged: number;
  changed: number;
  rejected: number;
  /**
   * Awaiting plus changed, which is what the default filter asks for (R3). Optional
   * while the summary still answers the four states alone; the page adds the two up
   * itself until it arrives.
   */
  to_confirm?: number;
}

/**
 * What one upload from this page WROTE, read off the job once the worker is done with it
 * (AC-H13). `GET .../order-inquiries/upload-jobs/{job_id}`.
 *
 * The page has none of this at queue time - the write happens on the worker - so the two
 * next steps it offers wait for `finished` and then read this: `product_ids` narrows Link
 * now to the book that just arrived, `documents` filters the purchase-order list to it.
 * `document_count` is the whole truth where `documents` is capped, so a caller can tell
 * "these are all of them" from "these are the first fifty".
 */
export interface UploadJobScope {
  job_id: string;
  status: string;
  finished: boolean;
  job_type?: string | null;
  filename?: string | null;
  product_ids: string[];
  documents: string[];
  document_count: number;
}

/** What one Acknowledge press did: rows taken on, and what linked at that moment. */
export interface AcknowledgeResult {
  acknowledged: number;
  linked_rows: number;
  links: number;
  /** Rows taken on but due after the horizon, so left Not linked (AC-LH1). */
  after_horizon?: number;
  /** The horizon the press ran under, `YYYY-MM-DD`. */
  link_up_to?: string | null;
  /** Whether a horizon was in force at all (S1). */
  link_horizon?: 'date' | 'none';
  /**
   * PLAN-oi-confirm-per-so (AC-CF-8): a rejected or cancelled row named in `row_ids` or
   * matching `filter` that this press left alone. Optional so a page rendered against an
   * older answer still reads; the FE reads `undefined` as 0 rather than hiding the count.
   */
  skipped?: number;
}

/**
 * What one Unconfirm press did (PLAN-oi-worklist-split-customer-project.md, owner 18 Sep 2026) - the
 * Actions menu's own reverse of Confirm. No linking figures: unlike Confirm, nothing
 * else moves.
 */
export interface UnacknowledgeResult {
  /** Rows that were `acknowledged`/`changed` and are now back to `awaiting`. */
  updated: number;
  /** Rows named that were already `awaiting`/`rejected`, cancelled, or not this
   * company's to touch - never an error, always just left alone. */
  skipped: number;
}

/* --------------------------------------------------------- the schedule matrix
 *
 * A 2D read of the same worklist rows the list shows (D1, reworked): the captain wanted
 * "vertically I can see by product, by sales order, by customer, by agent, then
 * horizontally is the dates ... by date, by month, by year" - a matrix like the
 * fulfilment planning board's, not a day-grid calendar.
 *
 * S3 (16 Sep 2026): computed SERVER-SIDE now, not client-side off an unpaged list fetch -
 * the old approach capped at `MATRIX_FETCH_LIMIT` (1,000) rows and a delivery-filtered
 * worklist has already exceeded that on prod. `GET {BASE}/order-inquiries/matrix?axis=
 * &by=&<list filters>` groups the SAME filtered set server-side, one row GROUP BY, and
 * answers every bucket rather than the first 1,000 rows' worth of them.
 */

/** The vertical axis the captain named, in the order they named it. */
export type OrderInquiryMatrixAxis = 'product' | 'sales_order' | 'customer' | 'agent';

/** How the date axis is cut. Week is the default, matching the planning board's own. */
export type OrderInquiryMatrixGranularity = 'day' | 'week' | 'month' | 'year';

/**
 * One cell of the matrix, exactly as the server answers it (contract section 6): this
 * axis value, by this date bucket, across every matching row that lands there.
 *
 * `axis_key`/`period` are never rendered (no UUIDs in the UI); `axis_label` is. `period`
 * is the ISO date the bucket STARTS on - week buckets start Monday, month/year bucket on
 * the first of the month/year - and is also the bucket's own ordering and grouping key,
 * so the FE never invents a second one. `buy`/`po`/`spo` are the stage sums of R-F, and
 * `rows` is the ROW COUNT summed into the cell, not the rows themselves: a click drills
 * down by asking the list for this cell's own axis + period, never by reading rows back
 * out of the matrix response (S3, "the drilldown keeps calling the list").
 */
export interface OrderInquiryMatrixCell {
  axis_key: string;
  axis_label: string;
  period: string;
  qty: string;
  buy: string;
  po: string;
  spo: string;
  rows: number;
}

/** One row of the table, built off the distinct `axis_key`/`axis_label` pairs the
 * server's cells carry. `key` is never rendered; `label` is. */
export interface OrderInquiryMatrixRow {
  key: string;
  label: string;
}

/** One column of the table, built off the distinct `period` values the server's cells
 * carry, labelled client-side for the chosen granularity. */
export interface OrderInquiryMatrixBucket {
  key: string;
  label: string;
  /** ISO date of the bucket's start - the ordering key, same value as `period`. */
  start: string;
}

/* --------------------------------------------------------- Place on PO (section G, G2)
 *
 * "identify which outstanding PO has quantity to fulfil this order inquiry, tag it, and
 * the quantity to be ordered is deducted" (the captain, 20 Aug). A raised ORDER row is
 * tagged to one or more open supplier PO lines; the row leaves `state = 'raised'` and the
 * reorder engine stops suggesting it. Untag ("Unplace") returns it.
 *
 * G2 (the captain, live-testing G, 20 Aug afternoon): placement now happens
 * AUTOMATICALLY - the cascade tags every raised row it can, earliest PO line first, ties
 * by document sequence, partial coverage allowed, POs only (never SPO-). This dialog
 * survives as OVERRIDE + AUDIT, not as the workflow: it opens already showing the
 * cascade's own preview (`default_take` per candidate), lets the taken quantity be
 * adjusted per line, and posts the whole allocation in one call. A row several lines
 * cover SPLITS on the backend - one row per PO line taken - so a placement can return
 * more than one row.
 */

/** One EXISTING tag already on a candidate's PO line - the row's expand. */
export interface OrderInquiryPoCandidateClaim {
  so_number?: string | null;
  item_code?: string | null;
  qty: string;
  placed_date?: string | null;
}

/**
 * One open document line the row could be linked to, in the walk's own order (Q5 + Q7):
 * the cited document first, then SPO allocations before PO lines on an ORDER BACK row,
 * then location tier, then the PO's issue date, then the line's expected date, then the
 * document number. Location NEVER filters a candidate out, it only ranks it.
 */
export interface OrderInquiryPoCandidate {
  /** Which book. `spo` candidates are offered to an ORDER BACK row and to nothing else. */
  kind: 'po' | 'spo';
  /** The PO line's id, or the SPO allocation's. Exactly one of the two is set. */
  po_line_id?: string | null;
  spo_allocation_id?: string | null;
  po_number: string;
  /** `L3` when the book numbered the line. */
  line_label?: string | null;
  /** Where that line lands the goods, and how well it fits the row's own location. */
  location?: string | null;
  tier: number;
  /** The document's own date - the cascade's FIRST key (Q7). */
  issue_date?: string | null;
  /** CS named this document on the order back, so the walk tries it before any other. */
  cited: boolean;
  /** Blank when the purchase order carries no supplier - never a guess. */
  supplier_name?: string | null;
  expected_date?: string | null;
  qty_ordered: string;
  qty_received: string;
  /** What OTHER placed rows already claim off this same line. */
  already_tagged: string;
  /** The line's balance, net of `already_tagged` - what is ACTUALLY left for this row. */
  remaining: string;
  /** `remaining >= ` the row's own quantity. */
  covers: boolean;
  /** The earliest candidate that covers the row. At most one candidate carries this. */
  recommended: boolean;
  /** The line's own held price. Blank when the purchase order carries none. */
  unit_cost?: string | null;
  currency?: string | null;
  /** The row's expand: every OTHER row already tagged onto this same PO line. */
  claims: OrderInquiryPoCandidateClaim[];
  /**
   * What the cascade would take off THIS line for THIS row (G2) - server-computed by the
   * SAME walk `auto_place_for_products` runs, so the dialog's preview and the auto pass
   * can never disagree. `"0"` when the cascade never reaches this line.
   */
  default_take: string;
  /**
   * G7 dedication (`PLAN-scm-reorder-oi-feedback-1sep.md` S6): the SO NUMBER another
   * claim names on this line, when that SO is not this row's own. `remaining` already
   * nets it out; this is what the dialog greys the row with - "Dedicated to SO xxxx".
   * Null on the ordinary line, or when this row's own SO is the one holding it.
   */
  dedicated_to?: string | null;
  /**
   * G12's project-bin lock: this line sits at a project-segment warehouse and NO SO has
   * claimed it, not even this row's own - "Unattributed - link manually". A manual link
   * still succeeds and writes the claim; always false on a pool-destination line.
   */
  unattributed?: boolean;
  /**
   * S8 (AC-CF-24): what THIS row already takes off this line, `"0"` when it holds none.
   * `remaining` is already credited back to include it, so re-placing the same take
   * never reads as over the line's remaining.
   */
  current_take?: string;
  /**
   * S8 review round (17 Sep): `false` only on a candidate FORCED into the list because
   * this row already links to it - a line closed, or its PO taken off active/partial,
   * since that link was written. Every ordinary candidate the walk offers is `true`.
   */
  line_open?: boolean;
}

/**
 * The Link dialog's own GET (S8): the candidate list, plus the header line the dialog
 * reads - "N still to link of Q" - computed off the row's own unlinked remainder.
 */
export interface OrderInquiryPoCandidatesResponse {
  candidates: OrderInquiryPoCandidate[];
  still_to_link: string;
  /**
   * S8 review round (17 Sep): `row.qty - row.bundled_qty` - the SAME ceiling
   * `_place_on_po_set` enforces server-side. The dialog's own capacity check and
   * footer total read this, not the row's bare `qty` prop, which overstates what a
   * bundled row can actually hold.
   */
  linkable_qty?: string;
}

/**
 * One line of a link - this row takes `qty` off ONE document line. Exactly one of
 * `po_line_id` / `spo_allocation_id` is set, the same rule the link row's own CHECK
 * constraint holds.
 */
export interface OrderInquiryPoAllocation {
  po_line_id?: string;
  spo_allocation_id?: string;
  qty: string;
}

/** `POST .../order-inquiries/auto-place` (G2 rule 4) - the worklist's "Auto-place". */
export interface AutoPlaceRequest {
  /** Omitted: every product carrying a raised ORDER/RESERVE & ORDER row. */
  product_ids?: string[];
  /** The named rows and nothing else ("Link selected"); wins over `product_ids`. */
  row_ids?: string[];
  /**
   * The LINK HORIZON (AC-LH1), `YYYY-MM-DD`. A row due after it is left Not linked and
   * counted on `after_horizon`. Omitted means the reorder plan's own horizon.
   */
  link_up_to?: string;
  /**
   * "No horizon at all", said out loud (S1). An empty date box used to send NOTHING, which
   * the server reads as "take the plan's own" - the opposite of what clearing it means, and
   * it left the page unable to link a far-future row once a plan run named a horizon.
   * Omitted still means the plan's own; `'date'` is implied by `link_up_to`.
   */
  link_horizon?: 'date' | 'plan' | 'none';
  /**
   * `PLAN-oi-header-list-detail.md`, AC-AL-01: the OI detail page's own "Auto link" with
   * nothing ticked - runs the cascade over that header's linkable rows only, touching no
   * row of another header. Mutually exclusive with `row_ids` on the wire, same as the
   * acknowledge endpoint's own `filter`.
   */
  filter?: { inquiry_id: string };
}

export interface AutoPlaceResult {
  placed_rows: number;
  allocations: number;
  products_touched: number;
  /** Rows still owed but due after the horizon, left Not linked on purpose (AC-LH2). */
  after_horizon?: number;
  /** The horizon the pass ran under, stated back so a zero has a date beside it. */
  link_up_to?: string | null;
  /** Whether a horizon was in force at all (S1) - `'none'` says the null above is a
   *  deliberate "no horizon", not a plan that has never named one. */
  link_horizon?: 'date' | 'none';
  /**
   * G4 (`PLAN-oi-links-autocount-truth-24sep.md`): "Auto link all"'s own book step -
   * rows AutoCount itself ties to a document, a REAL link. Absent on a backend that has
   * not yet split the cascade into suggested links (S2/S3); the toast then falls back to
   * `placed_rows` alone, exactly as it reads today.
   */
  book_linked_rows?: number;
  /** The rows the cascade only SUGGESTED, on top of `book_linked_rows` - never written
   * as an `order_inquiry_links` row, never counted as bought. Same absence rule. */
  suggested_rows?: number;
  /**
   * R18 (`PLAN-oi-links-autocount-truth-24sep.md` 3.6, supersedes G1): how many rows
   * this pass actually moved - book-linked this pass, or given a different suggestion
   * than the one they held coming in. "Link selected" is THIS route, scoped to the
   * ticked rows via `row_ids` - there is no separate route for it - and reads this to
   * say whether recalculating against AutoCount caught a mistake, rather than a
   * blanket re-link.
   */
  changed_rows?: number;
}

/**
 * `POST .../order-inquiries/unplace-all` (the captain, 20-21 Aug): "unplace all" for
 * the CURRENT worklist scope. The SAME filter shape `OrderInquiryWorklistParams` sends
 * to `GET /order-inquiries`, minus `state` - this always means placed rows, whatever
 * else is filtered - and never `product_ids`: the worklist paginates server-side, so a
 * client-derived product list would miss rows behind page 1. Every field omitted means
 * every placed row in the company.
 */
export interface UnplaceAllRequest {
  query?: string;
  delivery_month?: string;
  raised_date?: string;
  project_id?: string;
  /** S5: text, exact match on the Project column - separate from `project_id`. */
  project?: string;
  supplier_id?: string;
  raised_by?: string;
}

export interface UnplaceAllResult {
  unplaced: number;
}

/**
 * `POST .../order-inquiry-rows/{rowId}/unplace` - Unlink. With a `link_id` it removes
 * THAT link and leaves the rest; without one it removes every link the row holds, which
 * is what the old whole-row untag meant and what "Unlink all" on a row still means.
 */
export interface UnlinkRequest {
  link_id?: string;
}

/**
 * `GET .../order-inquiries/unplace-all-preview` - the confirm dialog's own numbers,
 * resolved server-side against the SAME filters `unplace-all` itself reads, never off
 * whatever page happens to be loaded. `product_code`/`product_name` are set only when
 * EVERY matching row resolves to the same product.
 */
export interface UnplaceAllPreview {
  count: number;
  product_code?: string | null;
  product_name?: string | null;
}

/* ----------------------------------------------------------- the PO popup
 *
 * `GET {BASE}/order-inquiries/po/{po_id}` - the "PO no" cell's popover (the captain,
 * 20 Aug). Gated the same as the worklist's own read (`projects.projects.view`), never
 * `scm.dashboard.view` - purchasing works this worklist off project permissions, so it
 * never calls the SCM purchase-orders route.
 */

/** One line of the purchase order behind a placed worklist row. Read straight off the
 * line's own balance - never netted against other rows' claims, which is a different
 * reading that belongs to the "Place on PO" candidates. */
export interface OrderInquiryPoDetailLine {
  /**
   * Issue #1215 point 2: the line's own identity, so the lightbox can highlight the
   * exact line an opening row's link sits on - the SKU alone is ambiguous the moment a
   * PO carries two lines of the same item.
   */
  id?: string | null;
  sku?: string | null;
  product_name?: string | null;
  qty_ordered: string;
  qty_received: string;
  remaining: string;
  /**
   * Every order inquiry row's own placement on THIS line, summed - never netted
   * against anything else, unlike the "Place on PO" candidate walk's own `remaining`.
   * Optional only for a caller that predates this field.
   */
  allocated?: string | null;
  location?: string | null;
  /**
   * The AutoCount book's own sales-order linkage for this line - the SAME fact and the
   * SAME fields the SCM purchase-order detail's Lines tab prints, rendered by the SAME
   * component, so a line's linkage reads identically on both screens. Never a guess.
   */
  book_so_number?: string | null;
  /** True when the book named a sales order this CRM does not hold. */
  book_so_unresolved?: boolean | null;
}

/**
 * WHO is holding this document's quantity - the Allocated to panel of the lightbox
 * (plan section 4.3). A draft allocation (its row still awaiting or changed) reads
 * Proposed; one on a confirmed row reads Confirmed. Never an id on screen: the inquiry
 * number and the sales order number are what a person quotes.
 */
export interface OrderInquiryDocumentAllocation {
  inquiry_no?: string | null;
  so_number?: string | null;
  item_code?: string | null;
  qty: string;
  /** The ROW's handshake state, which is what makes its link a draft or a real one (R1). */
  ack_state?: OrderInquiryAckState | string | null;
  /**
   * When the link was made (naive UTC, as every datetime on this wire is). On the schema
   * since the lightbox landed and missing here, so a reader of this type could not see a
   * field the endpoint sends.
   */
  linked_at?: string | null;
  /**
   * Issue #1215 point 2: which PO line this allocation sits on, so the lightbox can
   * highlight it on the lines grid. Null on an SPO allocation.
   */
  po_line_id?: string | null;
}

export interface OrderInquiryPoDetail {
  id: string;
  po_number: string;
  supplier_code?: string | null;
  supplier_name?: string | null;
  expected_date?: string | null;
  status: string;
  lines: OrderInquiryPoDetailLine[];
  /**
   * Who this purchase order's quantity is spoken for by. Absent until the backend
   * answers it (plan section 5.1), and the panel then says so rather than claiming none.
   */
  allocations?: OrderInquiryDocumentAllocation[];
}

/* --------------------------------------------------------- the SPO lightbox
 *
 * `GET {BASE}/order-inquiries/spo/{spo_number}` - the same dialog the PO number opens,
 * for the other book. An SPO has no purchase order behind it, so it is addressed by its
 * NUMBER (which is what the link carries) rather than by an id.
 */

/** One allocation line of the shipping order: what is on it, what has landed, where. */
export interface OrderInquirySpoDetailLine {
  /**
   * R15 (owner rulings, 25 Sep 2026, hand test on stack C): the line's own identity
   * (`spo_allocations.id`), the same reason `OrderInquiryPoDetailLine.id` exists
   * (issue #1215 point 2) - without it the SPO lightbox has no field to highlight a
   * line by, unlike the PO lightbox next door.
   */
  id?: string | null;
  sku?: string | null;
  product_name?: string | null;
  allocated: string;
  received: string;
  remaining: string;
  /**
   * The warehouse code the book named, else its raw location code. Blank when the book
   * named neither, and the cell says "no location" rather than printing a dash.
   */
  location?: string | null;
  /**
   * The purchase order this line was raised from, per AutoCount's own statement
   * (owner's 9 Sep feedback). Plain text, never a link yet. Null when the book named
   * no source.
   */
  source_po_number?: string | null;
  /**
   * This line's own `spo_allocations.spo_line_number` (R31b, stock debt lane) - what a
   * caller's `highlightLines` names to mark and jump to a specific line.
   */
  spo_line_number?: number | null;
}

export interface OrderInquirySpoDetail {
  spo_number: string;
  supplier_name?: string | null;
  /** When the shipment is expected. */
  eta?: string | null;
  /** The inbound shipment behind it, when one exists. */
  shipment_ref?: string | null;
  container_no?: string | null;
  lines: OrderInquirySpoDetailLine[];
  allocations?: OrderInquiryDocumentAllocation[];
}

/**
 * `POST {BASE}/order-inquiries/reject` - one reason, many rows (plan section 5.6). The
 * batch reports per row so a refusal the server would not take is named rather than
 * swallowed into a count.
 */
export interface OrderInquiryBulkRejectResult {
  rejected: number;
  results?: { row_id: string; ok: boolean; error?: string | null }[];
}

/* -------------------------------------------------- the OI DOCUMENT (header)
 *
 * `PLAN-oi-header-list-detail.md` (contract section). One row per order inquiry HEADER -
 * one sales order's whole set of purchasing instructions - as distinct from the per-LINE
 * worklist above. `orderInquiryService.ts`'s header functions call the real
 * `GET /api/v1/project-sales/order-inquiry-headers*` routes; this shape matches the
 * backend response models field for field.
 */

export type OrderInquiryHeaderStatus = 'outstanding' | 'completed';

/** One row of the Documents view (AC-HL-02). */
export interface OrderInquiryHeader {
  id: string;
  inquiry_no: string;
  /** The pre-renumbering number (plan S1) - never shown, only ever matched by search. */
  legacy_inquiry_no?: string | null;
  /** The FIRST raise, fixed for life (owner ruling R5). */
  raised_at?: string | null;
  raised_by_name?: string | null;
  /** The CORE sales order id, for the S/O no link (AC-HL-06). Null when there is none. */
  sales_order_id?: string | null;
  project_sales_order_id?: string | null;
  so_number?: string | null;
  so_date?: string | null;
  customer_name?: string | null;
  customer_code?: string | null;
  project_id?: string | null;
  project_title?: string | null;
  agent_name?: string | null;
  /** Non-cancelled rows only (AC-LS-05). */
  lines_total: number;
  lines_to_confirm: number;
  qty_total: string;
  /** Derived, never stored (plan S2): `lines_to_confirm > 0` is outstanding. */
  status: OrderInquiryHeaderStatus;
}

/** One entry of the detail page's Raise history card (AC-DP-07). Newest first. */
export interface OrderInquiryHeaderRaiseEntry {
  kind: 'raised' | 'reconfirmed';
  by_name?: string | null;
  at?: string | null;
}

/** `GET /order-inquiry-headers/{id}` (AC-DT-01). */
export interface OrderInquiryHeaderDetail extends OrderInquiryHeader {
  /** The ERP document type, when the order carries one (General tab's Order card). */
  order_type?: string | null;
  raise_history: OrderInquiryHeaderRaiseEntry[];
}

export interface OrderInquiryHeaderListParams {
  state?: OrderInquiryHeaderStatus | 'all';
  query?: string;
  raised_by?: string;
  agent?: string;
  /** Exact match on the Project column's own text (reviewer B2, fix round 22 Sep
   * 2026) - never a `projects.id`: 0 of 738 headers on the prod copy carry a
   * registered project. */
  project?: string;
  page?: number;
  limit?: number;
  sort?: string;
  dir?: 'asc' | 'desc';
}

export interface OrderInquiryHeaderListEnvelope {
  data: OrderInquiryHeader[];
  total: number;
  page: number;
  limit: number;
}

/** One purchase order this header's lines are linked to (AC-DT-03, AC-DP-08). */
export interface OrderInquiryRelatedPurchaseOrder {
  po_id: string;
  po_number: string;
  supplier_name?: string | null;
  po_date?: string | null;
  lines_linked: number;
  qty_linked: string;
}

/** One shipping order this header's lines are linked to. */
export interface OrderInquiryRelatedSpo {
  spo_number: string;
  supplier_name?: string | null;
  lines_linked: number;
  qty_linked: string;
}

/** `GET /order-inquiry-headers/{id}/related-documents`. */
export interface OrderInquiryHeaderRelatedDocuments {
  purchase_orders: OrderInquiryRelatedPurchaseOrder[];
  spos: OrderInquiryRelatedSpo[];
}
