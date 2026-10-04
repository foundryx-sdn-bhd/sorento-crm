/**
 * Fulfilment Planning wire shapes (Stage 1B), transcribed from
 * `documentation/plans/scm/STAGE1B-scm-front-planning-reconciliation.md` section 3.
 *
 * The screen these feed answers one question: is this Project SO reconciled to the
 * AutoCount document yet, and if not, which line stops it. Nothing here carries a
 * per-line workflow state - the whole SO has exactly one pre-confirmation state
 * (AC-A03), so no line ever reads confirmed, partially confirmed or purchasing-ready.
 *
 * Quantities are decimal STRINGS for the same reason the sales-order lines are: a float
 * round trip loses the tail of a quantity the customer signed for.
 *
 * Fields the contract does not promise are optional, so a backend that has not shipped
 * one yet renders a stated absence rather than crashing.
 */

/**
 * The whole sales order's one state. `confirmed` is Stage 1C's addition and means an
 * ACTIVE supply decision exists; a superseded or challenged decision reads
 * `needs_cs_review` again, so there is never a per-line one.
 *
 * `not_started` is the AutoCount-driven plan's addition (PLAN-fulfilment-planning-from-
 * autocount-so section 6): an outstanding project-class sales order that nobody has
 * planned yet. It is the state of a row that has no planning record at all, which is why
 * a `not_started` row carries no `id` and no `provisional_ref`.
 */
export type ReviewState =
  | 'not_started'
  | 'awaiting_reconciliation'
  | 'needs_cs_review'
  | 'confirmed';

export const REVIEW_STATE_LABELS: Record<ReviewState, string> = {
  not_started: 'Not started',
  awaiting_reconciliation: 'Awaiting reconciliation',
  needs_cs_review: 'Needs CS review',
  confirmed: 'Confirmed',
};

/**
 * Why the Project SO header is, or is not, attached to a core sales order.
 *
 * `adopted` is the AutoCount-driven arm: the planning record WAS the core sales order, so
 * there is no separately authored document to disagree with and reconciliation is a one-way
 * sync rather than a diff.
 */
export type ReconciliationHeaderOutcome =
  | 'no_document'
  | 'no_core_so'
  | 'linked'
  | 'adopted';

/**
 * What happened to one Project SO line when it was mapped against the core SO lines.
 * `duplicate` is the core line another Project SO already holds, which is a different
 * problem from `missing`: the line exists, it is just not this sales order's to take.
 */
export type ReconciliationLineLink =
  | 'linked'
  | 'missing'
  | 'ambiguous'
  | 'duplicate';

/**
 * `surplus` is the only kind that has no Project line: it is a core line the AutoCount
 * document carries and the Project SO does not, so it is named by item code alone.
 */
export type ReconciliationExceptionKind =
  | 'header'
  | 'missing'
  | 'ambiguous'
  | 'duplicate'
  | 'surplus';

/**
 * Row of `GET /project-sales/fulfilment-planning`.
 *
 * The worklist is a union of two arms, one row per subject, and the arms are disjoint by
 * construction: arm 1 is an outstanding core sales order (`row_kind = 'sales_order'`), arm
 * 2 is a planning record that has no core sales order yet (`row_kind = 'planning_record'`).
 * A core order that HAS been planned still comes back as arm 1, carrying the planning
 * record's `id` and state.
 *
 * Everything a not-started row cannot have is optional, because it genuinely does not exist
 * yet: no planning record means no `id`, no `provisional_ref`, no `status`, and no counts
 * off a mirror nobody has written. `project_id` is nullable because an adopted order has no
 * project registration and must not invent one.
 */
export interface FulfilmentPlanningRow {
  /** Which arm this row came from. Addressing only, never rendered. */
  row_kind?: 'sales_order' | 'planning_record';
  /** The planning record's id. Absent on a not-started row: there is no record. */
  id?: string | null;
  /** The core `sales_orders` id, for addressing the SCM sales order. Never rendered. */
  sales_order_id?: string | null;
  /** The AutoCount / core sales-order number. The human key of an arm-1 row. */
  so_number?: string | null;
  /** Whether the planning record was authored here or adopted from the core book. */
  origin?: 'authored' | 'adopted' | null;
  provisional_ref?: string | null;
  autocount_doc_no?: string | null;
  project_id?: string | null;
  project_code?: string | null;
  project_name?: string | null;
  /** The project name when one is registered, else the core order's own project string. */
  project_label?: string | null;
  customer_name?: string | null;
  /** Who sold it (`sales_orders.sales_agent_id` -> `sales_agents`). Null on an authored
   * record, which carries no agent of its own, and on a core order nobody could resolve one
   * for. */
  agent_code?: string | null;
  agent_label?: string | null;
  po_number?: string | null;
  area_group?: string | null;
  /** The existing sales-order status (published, amended, adopted, ...), not a review state. */
  status?: string | null;
  line_count: number;
  lines_linked?: number;
  exception_count?: number;
  /** Decimal STRING, summed over the still-owed lines. Same reason line quantities are. */
  outstanding_qty?: string | null;
  /** Earliest still-owed required date across the lines. The order the work is due in. */
  earliest_required_date?: string | null;
  review_state: ReviewState;
  updated_at?: string | null;
  /**
   * The newest PENDING planning-change batch on this order's core sales order, or null/absent
   * (`PLAN-scm-board-picks-up-pending-change.md`, AC-B7). Same id the SCM Sales Orders list
   * and the fulfilment board name off `planning_change_service.pending_batch_id_by_sales_order`
   * - the `Changed` pill here links to the board with that batch already loaded.
   */
  planning_change_batch_id?: string | null;
}

export interface ReconciliationHeader {
  outcome: ReconciliationHeaderOutcome;
  /** The AutoCount number the core order carries. Never its id. */
  core_so_number?: string | null;
  /** The sentence the backend wrote. Shown as-is; the outcome code is never the message. */
  reason: string;
}

export interface ReconciliationLine {
  id: string;
  line_no: number;
  product_code?: string | null;
  description?: string | null;
  qty: string;
  uom?: string | null;
  delivery_date?: string | null;
  stock_location?: string | null;
  link: ReconciliationLineLink;
  /**
   * How many core lines could still be this one: 1 on a linked line, the number of core
   * candidates at that product and date on an ambiguous one, 0 otherwise.
   */
  candidate_count: number;
  reason: string;
}

export interface ReconciliationException {
  /** Absent on a surplus core line, which no Project line claims. */
  line_no?: number | null;
  item_code?: string | null;
  kind: ReconciliationExceptionKind;
  message: string;
}

/**
 * `GET /project-sales/sales-orders/{pso_id}/reconciliation`, and the body
 * `POST .../reconcile` answers with after it has written the links.
 */
export interface ReconciliationSummary {
  project_sales_order_id: string;
  provisional_ref: string;
  autocount_doc_no?: string | null;
  /** Nullable: an adopted order has no project registration. */
  project_id?: string | null;
  project_code?: string | null;
  project_name?: string | null;
  customer_name?: string | null;
  po_number?: string | null;
  area_group?: string | null;
  status: string;
  /**
   * Null on an order that is not published or amended: a draft is reconciled against
   * nothing, so it carries no state rather than one it has not earned (AC-A03), and
   * `ReviewStatePill` renders nothing for it.
   */
  review_state: ReviewState | null;
  header: ReconciliationHeader;
  lines: ReconciliationLine[];
  exceptions: ReconciliationException[];
  lines_total: number;
  lines_linked: number;
}

/**
 * The columns the server can order the union by, and therefore the only ones the worklist
 * offers a sort affordance on. A header that toggled a sort the server ignores would look like
 * a broken control, so the closed set lives here and drives both the grid and the URL.
 */
export const FULFILMENT_PLANNING_SORT_FIELDS = [
  'so_number',
  'customer_name',
  'project_label',
  'earliest_required_date',
  'outstanding_qty',
  'line_count',
  'review_state',
  'provisional_ref',
  'po_number',
  'area_group',
  'updated_at',
] as const;

export type FulfilmentPlanningSortField =
  (typeof FULFILMENT_PLANNING_SORT_FIELDS)[number];

export interface FulfilmentPlanningListParams {
  page?: number;
  limit?: number;
  query?: string;
  review_state?: ReviewState;
  project_id?: string;
  sales_order_id?: string;
  /** One of `FULFILMENT_PLANNING_SORT_FIELDS`. Omitted leaves the server's own order. */
  sort?: string;
  dir?: 'asc' | 'desc';
}

/**
 * What Start planning answers with (`POST /project-sales/fulfilment-planning/adopt`).
 *
 * Adoption is idempotent: pressing it twice, or a second CS pressing it, returns the record
 * that already exists with `already_adopted` true rather than writing a second one. That is
 * why it takes no confirmation dialog - it destroys nothing and it repeats safely.
 */
export interface AdoptSalesOrderResult {
  project_sales_order_id: string;
  so_number: string;
  review_state: ReviewState;
  already_adopted: boolean;
}

// ---------------------------------------------------------------------------
// Stage 1C: supply composition and atomic confirmation
// ---------------------------------------------------------------------------

/**
 * The four ways a line's open quantity can be met (plan 3.1). `timely_spo` is dated
 * location supply rather than a CS decision, so CS never types its quantity; the other
 * three are the components CS composes.
 */
export type SupplyComponentKind = 'timely_spo' | 'reserve' | 'borrow' | 'buy';

/** Where a Borrow comes from: free stock at another location, or another project's hold. */
export type BorrowSource = 'other_location' | 'other_project';

export type SupplyDecisionState = 'active' | 'superseded' | 'challenged';

export interface SupplyComponent {
  kind: SupplyComponentKind;
  qty: string;
  /**
   * The short sentence the rule that produced the quantity wrote (AC-B14), for example
   * "Reserve 10: free stock at BRW covers the need by the delivery date". Deterministic,
   * frozen with the snapshot at confirmation, and shown beside the quantity always.
   */
  reason: string;
  /** Warehouse CODE. The pool code on a BRW-pool Reserve, the donor's on a Borrow. */
  source_location?: string | null;
  /**
   * Addressing only, never rendered: the confirm payload names warehouses by id, and the
   * screen names them by code. Same reason `ReconciliationLine.id` exists.
   */
  source_warehouse_id?: string | null;
  /** The donor project's human reference on a cross-project Borrow. Never its id. */
  donor_project_ref?: string | null;
  donor_project_id?: string | null;
  /** What CS typed: the Borrow reason, or the reason a discontinued product is bought. */
  cs_reason?: string | null;
  /**
   * Ladder v2 (`PLAN-demo-followups-19aug-ladder-v2.md` section E): which rung of the
   * source ladder produced this component - `incoming` | `pool` | `group_take` |
   * `group_borrow` | `cross_group_borrow` | `buy`. `null`/absent on a component frozen
   * before ladder v2 landed.
   */
  rung?: string | null;
  /** The donor sales order for a `group_borrow` component (section E.4). */
  donor_so_number?: string | null;
  donor_line_no?: number | null;
  donor_agent_code?: string | null;
  /** The donor shares this line's own sales agent (section 8). */
  same_agent?: boolean;
  /** The order-back this component raised: equal to what was taken. */
  order_back_qty?: string | null;
  /** Addressing only, never rendered: re-identifies the donor's own line at confirm. */
  donor_core_line_id?: string | null;
  /**
   * The donor's own delivery date: the order-back's urgency, and the month its debt lands
   * in on the Stock Debt view. The server has always sent it on a borrow component; it is
   * declared here because the sheet's borrow row now states whose order pays for it.
   */
  donor_required_date?: string | null;
  /**
   * LADDER v7.1 STEP 3 (`supply_borrow`, S4): the incoming document this quantity comes off.
   *
   * `supply_key` addresses it (`spo:<allocation id>` / `po:<purchase order line id>`) and is
   * NEVER rendered - it is what the Confirm moves the placement link onto. `supply_document`
   * is how a person names it (`SPO 202607-S0105`, `PO 202607-P0031 line 3`), written by the
   * server so the drill row and the engine's sentence cannot spell one document two ways.
   * Both absent on every other rung.
   */
  supply_key?: string | null;
  supply_document?: string | null;
  /** The day it lands: an SPO's arrival, a PO line's `issue_date + lead time` (R29). */
  arrival_date?: string | null;
}

/** One incoming SPO leg at the line's location. Timely covers, advisory does not. */
export interface SupplySpoRef {
  spo_number: string;
  arrival_date?: string | null;
  qty: string;
}

/** What Borrowing this quantity does to whoever holds it now (AC-B09). */
export interface BorrowDonorImpact {
  free_before: string;
  free_after_full_borrow: string;
  /** Already committed to another sales order at that location. */
  committed_qty: string;
}

/**
 * A donor, with its OWN position beside what it could give (PLAN 13.11).
 *
 * The captain, on a list that stated free stock alone: "before I decide to borrow, I need to
 * know I am not hurting them". Free nets reserved and confirmed holds only, so it reads as
 * raw on-hand on this book; the AutoCount triple is what says whether they can spare it.
 *
 * The server RANKS the list by what meeting THIS line would leave each donor with
 * (`available_after_need`, then availability, then free) and flags exactly one row
 * `recommended`. The screen never re-sorts it: a list that reshuffles as a quantity is typed
 * is not a recommendation.
 */
export interface BorrowCandidate {
  source: BorrowSource;
  warehouse_code: string;
  /** Addressing only (see `SupplyComponent.source_warehouse_id`). */
  warehouse_id: string;
  donor_project_ref?: string | null;
  donor_project_id?: string | null;
  /** What this donor can give: the location's free stock, or the donor project's own hold. */
  free_qty: string;
  /** AutoCount's Stock Status columns for the donor's own (product, location) pile. */
  qty_on_hand?: string | null;
  so_qty?: string | null;
  spo_qty?: string | null;
  /** on hand - SO + SPO. Signed: a donor the book has oversold says so. */
  available_qty?: string | null;
  qty_free?: string | null;
  qty_committed?: string | null;
  /** What this line still has to cover at the borrow rung: the Buy the ladder proposed. */
  need_qty?: string | null;
  /** `available_qty - need_qty`, signed. The ranking key, and the default "After borrow". */
  available_after_need?: string | null;
  /** First in the ranking. Exactly one candidate carries it. */
  recommended?: boolean;
  donor_impact: BorrowDonorImpact;
  /**
   * Ladder v2 (section E): `group_borrow` or `cross_group_borrow` for a new group-aware
   * donor row, `null`/absent for a plain `other_location`/`other_project` donor the old
   * ladder already offered.
   */
  rung?: string | null;
  /** The donor sales-order line this row names, for `group_borrow` (section E.4). */
  donor_so_number?: string | null;
  donor_line_no?: number | null;
  donor_agent_code?: string | null;
  /** Addressing only, never rendered: re-identifies the donor's own line at confirm. */
  donor_core_line_id?: string | null;
  /** Ranked below this line, so the ladder proposes it automatically. `false` on a
   * same-agent donor ranked above this line: offered, never auto-composed. */
  lower_ranked?: boolean;
  /** The donor shares this line's own sales agent (section 8) - offered at any rank. */
  same_agent?: boolean;
  /**
   * The SAME location facts the Grid Location table states for this warehouse (S4,
   * `PLAN-local-supplier-oi-routing.md`), so the manual Borrow modal renders a source as that
   * table rather than doing its own arithmetic. Absent on a donor the server has not wired
   * this for yet - the modal falls back to the plain fields above.
   */
  location?: BoardCellLocation;
}

/** The components as they were frozen at confirmation, read back on a confirmed order. */
export interface SupplyFrozenLine {
  open_qty: string;
  components: SupplyComponent[];
  /** The reason given for overriding the proposal, when one was. Absent otherwise. */
  amend_reason?: string | null;
  /** The reason a discontinued product was still bought, when one was given. */
  buy_reason?: string | null;
}

export interface SupplyLine {
  project_line_id: string;
  line_no: number;
  item_code?: string | null;
  /** Addressing only, never rendered - what the Proof button asks the classification
   * evidence endpoint with. */
  product_id?: string | null;
  description?: string | null;
  uom?: string | null;
  /** The core line's current open fulfilment quantity (AC-B01), in the line UOM. */
  open_qty: string;
  /**
   * Whether the product's supplier sits in the home country (S3,
   * `PLAN-local-supplier-oi-routing.md`). A `local` Buy raises no Order Inquiry row on
   * confirm - the sheet marks it with a `Local` pill and nothing else changes. `null`
   * while `local_buy_routing_enabled` (System Settings) is off, the shipped default
   * (`PLAN-local-buy-routing-toggle.md`) - every Buy then reaches Order Inquiries and
   * no pill renders. Absent on a server that has not wired origin resolution yet.
   */
  buy_origin?: 'local' | 'overseas' | null;
  required_date?: string | null;
  /**
   * Warehouse CODE of the line's fulfilment location, read off the CORE sales-order line's
   * own `warehouse_id`. Nobody is asked for it and nothing defaults it (captain's decision,
   * PLAN-fulfilment-planning-from-autocount-so section 11 question 2).
   */
  fulfilment_location?: string | null;
  /**
   * The core sales-order line states no warehouse, so this line cannot be planned against a
   * location at all. Nothing is proposed for it and Confirm refuses it by name; the way out
   * is to state the location on the SCM sales order, which the sheet links to. Never a
   * guessed default.
   */
  fulfilment_location_missing?: boolean;
  /**
   * Why this line cannot be planned on the sheet at all (it has no reconciled AutoCount
   * line, so there is no current open quantity to promise against). The sheet shows it
   * blocked and never names it in a confirmation; `null` on every plannable line.
   */
  unplannable_reason?: string | null;
  /** ABC A by quantity on retail demand (3.3a): the shared pool is not offered at all. */
  is_dealer_hot_selling: boolean;
  /** ABC A by quantity on project demand (3.3a): the pool is capped by its own availability. */
  is_project_hot_selling: boolean;
  /** Classified (a non-null letter on that class, at an active location) but not hot -
   * "Cold at retail" / "Cold at project". `false` while the class is hot too. */
  dealer_classified: boolean;
  project_classified: boolean;
  /** No non-null ABC letter in either demand class (AC-B05, amended 3.3a). */
  classification_unavailable: boolean;
  is_discontinued: boolean;
  pool_location?: string | null;
  /** The old reorder-level cap. Always null now (19 August 2026, PLAN 3.3a) - dealer
   * hot-selling offers the pool nothing and project hot-selling caps it by availability
   * instead. Kept for wire compatibility. */
  pool_cap?: string | null;
  pool_reorder_level?: string | null;
  /**
   * The pool-share carve-out (LADDER V8, R-C), stated on THIS line the way the board's cell
   * states it on its locations (B2, fix round 5): `{warehouse_id: available_for_project}`
   * for every site pool this line's own walk consulted. Read into `PoolShareLimits` by
   * `poolShareLimitsFromLine` (`_shared/lib/poolShare.ts`), the sheet's own source for the
   * allowance the board reads off `BoardCellLocation` instead.
   */
  pool_allowances?: Record<string, string | null | undefined>;
  /** The five site pools' NET (R-D), bounding every pool's allowance together. */
  pools_net?: string | null;
  components: SupplyComponent[];
  /**
   * THE FIVE STEPS OF LADDER v7.1 FOR THIS LINE, taken or not (R36, AC-S3-14).
   *
   * The same six answers about the same walk the board's contribution carries, so the ONE
   * table renders on either surface (`BoardLadderOptionsTable`) - the server's schemas are
   * siblings for the same reason (`SupplyLadderOption` / `BoardLadderOption`). Undeclared
   * here the sheet dropped a populated array on the floor and the drawer showed no options
   * at all, which reads as a line the engine never walked.
   */
  options?: BoardLadderOption[];
  timely_spo: SupplySpoRef[];
  advisory_spo: SupplySpoRef[];
  borrow_candidates: BorrowCandidate[];
  frozen?: SupplyFrozenLine | null;
}

export interface SupplyDecision {
  revision_no: number;
  state: SupplyDecisionState;
  confirmed_by_name?: string | null;
  confirmed_at?: string | null;
  /** Why the active revision no longer matches the order. Present when challenged. */
  challenged_reason?: string | null;
}

/**
 * A line the server will not confirm, named the only way it may be (AC-C02).
 *
 * `line_no` is optional because a refusal can be about the sales order rather than about
 * one of its lines (nothing is mapped yet, the order moved on underneath the sheet), and
 * the screen then names the order instead - the same rule `ReconciliationException`
 * follows for a surplus core line.
 */
export interface SupplyFailingLine {
  line_no?: number | null;
  /** #1362 item 5: AutoCount's own sales-order line number, the one a message names. */
  so_line_no?: number | null;
  item_code?: string | null;
  reason: string;
}

/** `GET /project-sales/sales-orders/{pso_id}/supply`. */
export interface SupplyProposal {
  project_sales_order_id: string;
  provisional_ref: string;
  autocount_doc_no?: string | null;
  /** The core sales-order number. The human key of an adopted order. */
  sales_order_number?: string | null;
  /** The core `sales_orders` id. Addressing only, for the /scm link; never rendered. */
  sales_order_id?: string | null;
  /** Nullable: an adopted order has no project registration. */
  project_id?: string | null;
  project_code?: string | null;
  project_name?: string | null;
  status?: string | null;
  review_state: ReviewState | null;
  decision?: SupplyDecision | null;
  lines: SupplyLine[];
  failing_lines?: SupplyFailingLine[];
}

export interface ConfirmReserveComponent {
  warehouse_id: string;
  qty: string;
  /**
   * The warehouse CODE the id names, when whoever built this component knew it.
   *
   * Addressing is the id's job and the endpoint reads nothing else; this is here so a
   * READER does not have to guess. A composition read back off a planning-change row is
   * printed in board words ("Pool share 15 at BRW"), and with the id alone the only
   * honest thing to print is "another location" - or, before this existed, the raw UUID
   * (measured on SO419595 line 9, 13 September 2026).
   */
  location?: string | null;
}

export interface ConfirmBorrowComponent {
  source: BorrowSource;
  warehouse_id: string;
  /**
   * The donor warehouse's CODE, when whoever built this component knew it.
   *
   * The same field `ConfirmReserveComponent` carries, and it matters more here: a borrow's
   * donor is by definition a warehouse this line does not hold at, so it appears nowhere
   * else on a planning-change row for a reader to resolve the id against.
   */
  location?: string | null;
  donor_project_id?: string | null;
  qty: string;
  /** Mandatory: no Borrow is written without one (AC-B09). */
  reason: string;
  /**
   * Ladder v2 group borrow (section E.4): the donor's own sales-order line, from
   * `BorrowCandidate.donor_core_line_id`. Present, this component is checked against
   * that line's LIVE committed quantity at confirm, not against free stock. Absent, this
   * is an ordinary `other_location` free-stock borrow, unchanged.
   */
  donor_core_line_id?: string | null;
  /** Round-tripped from the donor row so the order-back note needs no second lookup. */
  donor_so_number?: string | null;
  donor_line_no?: number | null;
  donor_agent_code?: string | null;
  same_agent?: boolean;
  /** The donor's own required date - the order-back's urgency (section E.4). */
  donor_required_date?: string | null;
  /**
   * LADDER v7.1 STEP 3 (`supply_borrow`, S4): the incoming document this quantity comes off.
   *
   * `supply_key` addresses it (`spo:<allocation id>` / `po:<purchase order line id>`) and is
   * NEVER rendered - it is what the Confirm moves the placement link onto. `supply_document`
   * is how a person names it (`SPO 202607-S0105`, `PO 202607-P0031 line 3`), written by the
   * server so the drill row and the engine's sentence cannot spell one document two ways.
   * Both absent on every other rung.
   */
  supply_key?: string | null;
  supply_document?: string | null;
  arrival_date?: string | null;
}

export interface ConfirmLine {
  project_line_id: string;
  timely_spo_qty: string;
  reserve: ConfirmReserveComponent[];
  borrow: ConfirmBorrowComponent[];
  buy_qty: string;
  /** Why this line still buys a discontinued product, when the product is discontinued and `buy_qty > 0`. Optional. */
  buy_reason?: string | null;
  /**
   * Why this composition is not the engine's, in the planner's own words. Frozen with the
   * line: every other component carries the sentence of the RULE that produced it, and those
   * explain a decision nobody took once a person has overridden them.
   */
  amend_reason?: string | null;
  /**
   * "This might be a system problem, flag it for investigation" (R10). Travels with whichever
   * verdict was given and is frozen beside the reason, so the pill still warns after a reload.
   */
  suspected_system_issue?: boolean;
}

export interface ConfirmSupplyBody {
  lines: ConfirmLine[];
  /**
   * The planning-change batch this Confirm is answering (AC-P3-4).
   *
   * Set when the board was opened at `?orders=...&batch=<id>`: the confirmation then APPLIES
   * that batch - the lines above become the batch rows' own compositions, the batch reads
   * applied with actor and time, and one revision is written. Absent on an ordinary board
   * Confirm, which is the common case and behaves exactly as it always has.
   *
   * A second Confirm on a batch already applied is refused with a message rather than writing
   * a second revision.
   */
  batch_id?: string | null;
  /**
   * The mirror `project_line_id`s of COVERED lines a `rejected` draft was staged on (owner
   * ruling 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`: "we should confirm the
   * rejection" - reject on a confirmed line is a STAGED decision like every other board
   * decision, and Confirm is what commits it, never the draft save itself). Never overlaps
   * `lines` - a line is either REPLACED (named in `lines`) or DROPPED (named here). Absent
   * on every ordinary Confirm; may not travel alongside `batch_id`.
   */
  rejected_line_ids?: string[];
}

export interface ConfirmException {
  line_no?: number | null;
  item_code?: string | null;
  message: string;
}

/** `POST /project-sales/sales-orders/{pso_id}/confirm`. */
/** One line a confirmation froze, as the server echoes it (FULFIL-CONFIRM-SCOPE). */
export interface ConfirmedLine {
  project_line_id: string;
  line_no?: number | null;
  item_code?: string | null;
}

export interface ConfirmResult {
  /** Null only when every named line was already fulfilled and nothing was written (#1362). */
  revision_no: number | null;
  confirmed_at?: string | null;
  review_state: string;
  inquiry_rows_created: number;
  exceptions: ConfirmException[];
  /**
   * The physical movements this confirmation raised, and how many it could NOT write
   * (`PLAN-scm-cs-planning-uat.md` section E).
   *
   * The transfer write is best-effort on the server so a failure cannot fail a promise
   * already made, but a movement nobody was told about is a movement nobody makes - so a
   * non-zero `transfers_failed` is said out loud. Optional, because a revision confirmed
   * against a server that predates the field carries neither.
   */
  transfers_written?: number | null;
  transfers_failed?: number | null;
  /**
   * And how many open movements it KEPT rather than re-raising (R16): the same instruction
   * at the same quantity survives a reconfirm with its state and its approval intact.
   */
  transfers_kept?: number | null;
  /** The lines this press froze, in payload order, and how many covered lines rode forward. */
  lines_confirmed?: ConfirmedLine[] | null;
  lines_carried?: number | null;
  lines_withdrawn?: ConfirmedLine[] | null;
  /** How many of the confirmed lines were flagged as a suspected system problem (R10). */
  suspected_issues?: number | null;
  /**
   * How many covered lines this SAME press withdrew (`ConfirmSupplyBody.rejected_line_ids`,
   * owner ruling 23 Sep 2026). The toast needs it beside the confirmed count - "N confirmed"
   * says nothing about the lines this press also took OUT.
   */
  rejected_count?: number | null;
  /** Named lines skipped because nothing was open on them, their saved decision cleared (#1362). */
  lines_fulfilled_skipped?: number | null;
  /** Buys confirmed as decided over goods that landed for their line (#1362). */
  landed_buy_notices?: SupplyFailingLine[] | null;
}

export interface FulfilmentPlanningListEnvelope {
  data: FulfilmentPlanningRow[];
  total: number;
  page: number;
  limit: number;
}

// ---------------------------------------------------------------------------
// The multi-order planning board (PLAN section 13)
//
// The board is a LENS (section 13.4): it reads across several sales orders and writes no
// decision object of its own. The persisted decision stays per sales order, atomic across
// its lines, exactly where Stage 1C put it. Everything below is either a read model or the
// board's own working draft.
//
// Axis naming is deliberate: `dateBuckets` across, `productRows` down. The delivery-schedule
// matrix calls a PRODUCT a `column` (its API's word, kept even after that grid was
// transposed), so borrowing its vocabulary would leave two grids using one word for opposite
// things.
// ---------------------------------------------------------------------------

/**
 * How the date axis is cut, as a calendar control: date, day, week or month (13.3, captain's
 * decision; `date` added S1, PLAN-board-oi-mechanical-22sep.md, owner ruling 22 Sep 2026).
 * `date` is the default: one column per distinct required date actually present, no calendar
 * fill. `day` renders a scrolling 30-day window rather than a column per distinct date, because
 * the book carries 349 of them - it stays available as its own option.
 */
export type BoardGranularity = 'date' | 'day' | 'week' | 'month';

/**
 * A column of the board. Every dated column is a real date, however far past (the captain,
 * verbatim: "don't put overdue together, still split by the date, don't put under overdue").
 * `no_date` is the one answer that has no place on a timeline, and it is pinned last.
 */
export type BoardBucketKind = 'dated' | 'no_date';

export interface BoardDateBucket {
  key: string;
  kind: BoardBucketKind;
  /** What the column header reads. Already formatted for display. */
  label: string;
  /** ISO date of the bucket start, for a dated bucket only. Ordering key. */
  start?: string | null;
  /**
   * The bucket's WHOLE period ended before the board's `as_of`, so nothing in it can still be
   * met on time. The period CONTAINING `as_of` is false - some of its dates are still to come,
   * and tinting this week as lost would be wrong - and `no_date` is always false.
   *
   * The SERVER's verdict, read and never re-derived, for the same reason
   * `BoardPolicy.discriminates_nothing` is: only the side that did the bucketing knows where a
   * week or month falls relative to `as_of`, and a tint derived here would disagree with the
   * columns it is painting.
   *
   * This is the TINT only. The count of late lines comes from `BoardContribution.is_past`,
   * which is a different question - see there.
   */
  is_past?: boolean;
}

/**
 * How a contributing line's quantity is proposed to be met. Mirrors SupplyComponentKind.
 *
 * `borrow` only ever appears on a COVERED line. The engine proposes none on either surface - a
 * Borrow needs a donor and a reason from a person (AC-B09) - but a line an active decision
 * covers states the composition that was FROZEN for it, and a frozen Borrow is a source like
 * any other. Printing it as anything else would describe a decision nobody took.
 */
export type BoardSourceKind =
  | 'reserve'
  | 'timely_spo'
  | 'buy'
  | 'borrow'
  | 'unplannable';

/**
 * The five rows of the proof, as INTERNAL KEYS - ladder v5
 * (`PLAN-scm-cs-planning-uat.md` section 1e). Nothing renders these: the reader is shown
 * `question`. `own` is question 1, the ownership group read as one pile, with the old
 * read-only `reserve_own` strip folded into it (it existed to name the queue, which is one
 * of that question's facts rather than a question of its own, and `QueueLink`'s dialog
 * still opens exactly that location).
 *
 * Ladder v7.1 (S3, PLAN section 3.2) renames the middle of the walk: `order_borrow` is
 * borrowing ON HAND from a later order and `supply_borrow` is borrowing the SUPPLY a later
 * order holds (S4). They are added rather than substituted because a trail frozen under v5
 * still names `cross_group_borrow` / `group_borrow`, and a snapshot that fails to render is
 * worse than one that reads in the older words.
 *
 * `incoming` / `group_take` / `reserve_own` / `reserve_pool` / plain `borrow` are retired
 * spellings, kept only so a stale cached trail does not fail to render.
 */
export type BoardTrailKind =
  | 'own'
  | 'order_borrow'
  | 'supply_borrow'
  | 'pool'
  | 'cross_group_borrow'
  | 'group_borrow'
  | 'buy'
  | 'incoming'
  | 'group_take'
  | 'reserve_own'
  | 'reserve_pool'
  | 'borrow';

/**
 * Did this question supply anything? One word, five times, so the proof can be scanned.
 *
 * It replaced a five-valued `outcome` (`took` / `nothing_left` / `not_eligible` /
 * `offered` / `none_needed`), which was five shades of the same two answers and put the
 * reasoning in a pill instead of in the sentence. The distinction survives where it
 * matters, in `why`.
 */
export type BoardTrailAnswer = 'yes' | 'no';

/**
 * One of the four questions ladder v5 asks about a line, or Buy (the captain: "can you
 * justify how you arrive at the buy ... need more justification", then "the justification
 * needs to be STRUCTURED instead of plain text", then, on 26 August: "our thought process
 * is simpler now").
 *
 * FIVE ROWS arrive, always, and every one is answered - "the pool was checked and had none"
 * is the answer to that question, and a row the server omitted would read as a question
 * nobody asked. A line that cannot be planned carries an empty trail, because no ladder was
 * walked for it.
 *
 * THE FIGURES ARE INSIDE `why`: the group's net, the pile's net, the donor group's net, the
 * cap. That is what retired `opening`, `offered` and `remaining_after` - eight columns of
 * arithmetic a reader had to do themselves.
 */
export interface BoardTrailStep {
  /** 1-based, in the order the questions are asked. */
  step: number;
  /** The internal rung key. Addressing and test ids only - never rendered. */
  kind: BoardTrailKind;
  /** The question, in the words a planner would ask it. */
  question: string;
  /** Whether this question supplied anything. */
  answer: BoardTrailAnswer;
  /** What the line took from it. */
  took: string;
  /** The warehouses it came from, comma-joined, or null on a No. */
  from?: string | null;
  /** The source's warehouse code. Null for Buy, which is held nowhere, and for Borrow. */
  location?: string | null;
  /** The same warehouse by id. Addressing only, never rendered. */
  warehouse_id?: string | null;
  /** Question 1 only: what the queue in front of this line wants, and how long it is. */
  ahead_qty?: string | null;
  ahead_lines?: number | null;
  /**
   * WHO is in that queue: the top of it in rank order, how many more, and a count of the WHOLE
   * queue by the factor that put each line in front.
   *
   * The captain, reading `478 | 18730 across 142 lines | 0 | 0 | 21 | Nothing left`: "what does
   * this mean? why do the orders stand ahead of me? why?" Totals answer neither question.
   * Own-location rung only; every other rung sends an empty list, because no other rung queues.
   */
  ahead?: BoardAheadLine[];
  ahead_more?: number;
  ahead_by_factor?: Record<string, number>;
  /**
   * WHY it ended that way, in one plain sentence from the server. Never assembled here: the side
   * that walked the ladder is the side that knows, and a sentence written on the client would
   * eventually describe a rule the engine no longer applies.
   */
  why?: string | null;
  /** One short hint, never a paragraph: "hot-selling: pool only", "MWH-IB 12000 · BRW 9000". */
  note?: string | null;
  /**
   * The pool's pile behind the `reserve_pool` rung, in AutoCount's vocabulary. Null on every other
   * rung, and on a pool rung with no pile to describe (no shared pool; the pool is this location).
   */
  pool?: BoardTrailPool | null;
}

/**
 * The five steps of ladder v7.1, in the order they are walked (PLAN section 3.2).
 *
 * `use` is the free pile (own group, then the other project groups), `order_borrow` is on hand
 * held by a later order, `supply_borrow` is the SUPPLY a later order holds (one document, S4),
 * then the pool's own book, then Buy.
 *
 * `pool_share` is ladder v8's replacement for `pool` (`PLAN-scm-fulfilment-feedback-2sep.md`
 * S2, R-A/R-B): the site pool of the asking bin is asked FIRST rather than last, for up to its
 * share allowance, and the label reads "Use BRW stock". `pool` is kept only so a trail frozen
 * under v7.1 still renders in its own words; no LIVE v8 walk emits it.
 */
export type BoardLadderStep =
  | 'pool_share'
  | 'use'
  | 'order_borrow'
  | 'supply_borrow'
  | 'pool'
  | 'buy';

/**
 * ONE STEP OF THE LADDER, WITH THE DATE IT WOULD FULFIL THE UNIT BY (R36, AC-S3-14).
 *
 * The captain, on round 6 of the plan review: the ladder used to report only the composition it
 * chose, so a planner reading "Buy 32" could not see that borrowing an SPO would have landed the
 * unit six weeks earlier, or that the pool could have covered it today. The engine now states
 * EVERY step it walked, whether or not it was taken, and the two questions that decide between
 * them - can it cover the WHOLE unit, and WHEN would the unit be fulfilled.
 *
 * The proposal is still the first WHOLE option in step order; `chosen` marks it. Amend is how a
 * planner takes a different one, and until S4 wires that, this table is read-only.
 */
export interface BoardLadderOption {
  step: BoardLadderStep;
  /** The step in the words a planner reads it by. The SERVER's sentence, never assembled here. */
  label: string;
  /**
   * Whether this step covers the WHOLE planning unit. A step that covers part of it gives
   * nothing (R10, R33), so this is what decides whether the option was available at all.
   */
  whole: boolean;
  /**
   * `YYYY-MM-DD`: when the unit would be fulfilled if this option were taken - today for on
   * hand (plus two days when a transfer is needed), the SPO's arrival, the PO's `issue + lead`,
   * `as_of + lead` for Buy. Null when the step gives nothing, so there is no date to state.
   */
  fulfil_date?: string | null;
  /**
   * How many days after the line's required date that lands. `0` is on time and renders blank -
   * a column of zeroes says nothing; a number is the whole point of the row. Null alongside a
   * null `fulfil_date`.
   */
  days_late?: number | null;
  /**
   * WHOSE order pays for it, when the option creates a stock debt: the donor's number, and the
   * month its own required date falls in (`YYYY-MM`), which is where the debt lands on the Stock
   * Debt view. Both absent on `use` and `buy`, which owe nobody.
   */
  debt_so_number?: string | null;
  debt_month?: string | null;
  /** The option the engine proposed. Exactly one option carries it, or none when nothing covers. */
  chosen: boolean;
  /**
   * How much THIS step can give (S2, R-B). On `pool_share` it is the SHARE - the one step
   * that may cover part of the unit rather than whole-or-nothing, so the one row whose
   * quantity `whole` does not already state. On every other row it is what that step would
   * contribute to what is LEFT after the share ("Use BRW stock 450, Use our locations 0,
   * Buy 200", AC-2.1). `0` renders as `0`, never blank (R-K).
   */
  gives_qty?: string | null;
  /**
   * The step's own sentence, where the quantity alone does not say it - "600 is more than
   * the 450 BRW can spare" (AC-2.4). Set on `pool_share`; null elsewhere, because a reason
   * per row for its own sake is noise.
   */
  reason?: string | null;
}

/**
 * The shared pool's pile as the pool rung saw it.
 *
 * The captain, on `Pool BRW | Had 0` beside an Inventory screen showing `Available 1`: "why it
 * shows 0?" Two true numbers: `left` is what the POOL'S OWN book ranked ahead of this line left,
 * `available` is the pile's whole position. Both arrive, with the subtraction between them.
 */
export interface BoardTrailPool {
  location: string;
  /** Addressing only, never rendered. */
  warehouse_id?: string | null;
  on_hand: string;
  /** What the whole open book still owes at the pool, and what is on the water to it. */
  so_qty: string;
  spo_qty: string;
  /** `on_hand - so_qty + spo_qty`, SIGNED and never clamped. */
  available: string;
  reserved: string;
  /** On hand less reserved less confirmed holds - what the engine may plan against. */
  free: string;
  /** What the pool's own orders ranked AHEAD of this line claim of that, and how many lines. */
  claimed_ahead_qty: string;
  claimed_ahead_lines: number;
  /** What was left for THIS line when the rung was reached - the rung's own `opening`. */
  left: string;
  reorder_level: string;
  /** The old reorder-level cap. Always null now (19 August 2026, PLAN 3.3a) - dealer
   * hot-selling offers the pool nothing and project hot-selling caps it by availability
   * instead. Kept for wire compatibility. */
  cap?: string | null;
}

/**
 * The item facts the ladder judged a line on, said rather than implied.
 *
 * The captain: "where is the consideration of dealer hot selling / project hot selling /
 * discontinued, to see if we can take from BRW?" Amended 19 August 2026 (PLAN 3.3a):
 * hot-selling is judged PER DEMAND CLASS, BY QUANTITY delivered in that class - a SKU can be
 * hot-selling on retail demand, on project demand, on both (dealer wins) or on neither.
 * Own-location Reserve is always eligible regardless of either flag; the flags gate only how
 * much the SHARED POOL contributes. `retail_classification_available: false` is "nobody has
 * judged either class" (no delivered demand of either in the trailing-12mo window), which is
 * a different answer from "not hot-selling".
 */
export interface BoardItemFlags {
  /** ABC A by quantity on retail demand: the pool contributes nothing at all. */
  dealer_hot_selling: boolean;
  /** The locations where it earned that, by code. */
  dealer_hot_selling_where: string[];
  /** ABC A by quantity on project demand: the pool contributes only while its own signed
   * availability stays positive. */
  project_hot_selling: boolean;
  /** The locations where it earned that, by code. */
  project_hot_selling_where: string[];
  /** Classified (a non-null letter on that class, at an active location) but not hot -
   * "Cold at retail" / "Cold at project". `false` while the class is hot too. */
  dealer_classified: boolean;
  project_classified: boolean;
  discontinued: boolean;
  retail_classification_available: boolean;
}

/** One line standing in front of this one at its pile, and what put it there. */
export interface BoardAheadLine {
  so_number: string;
  line_no?: number | null;
  qty: string;
  required_date?: string | null;
  rank_score: number;
  /**
   * The policy factor with the largest weighted difference in that line's favour (a factor
   * that line carries and this one does not counts in full). `earlier_date`, `line_order` or
   * `tie_break` when the two scores are EQUAL: the policy separated nothing and the queue was
   * decided by the tie-break, in that order, so naming a factor would claim a difference that
   * is not there.
   */
  leading_factor?: string | null;
  /** An earlier line of the SAME sales order, which is not a rival at all. */
  same_order?: boolean;
}

/** One donor of a FROZEN Borrow, as the confirmation takes it back. */
export interface BoardDecisionBorrow {
  source: BorrowSource;
  warehouse_id?: string | null;
  /** The donor's warehouse CODE, which is what the screen reads. */
  location?: string | null;
  donor_project_id?: string | null;
  qty: string;
  /** The PERSON's reason. The confirmation refuses a Borrow that carries none. */
  reason: string;
  /** Ladder v2 (section E.4): the donor sales-order line this Borrow named. */
  rung?: string | null;
  donor_so_number?: string | null;
  donor_line_no?: number | null;
  donor_agent_code?: string | null;
  same_agent?: boolean;
  /** Addressing only, never rendered: re-identifies the donor's own line at confirm. */
  donor_core_line_id?: string | null;
  donor_required_date?: string | null;
  /** The order-back this component raised: equal to what was taken. */
  order_back_qty?: string | null;
  /**
   * LADDER v7.1 STEP 3 (`supply_borrow`, S4): the incoming document this quantity comes off.
   *
   * `supply_key` addresses it (`spo:<allocation id>` / `po:<purchase order line id>`) and is
   * NEVER rendered - it is what the Confirm moves the placement link onto. `supply_document`
   * is how a person names it (`SPO 202607-S0105`, `PO 202607-P0031 line 3`), written by the
   * server so the drill row and the engine's sentence cannot spell one document two ways.
   * Both absent on every other rung.
   */
  supply_key?: string | null;
  supply_document?: string | null;
  arrival_date?: string | null;
}

/**
 * What the ACTIVE revision froze for one line (13.4).
 *
 * The WHOLE composition rather than a summary of it, because the Amend editor is seeded from it
 * (a covered line has no proposal to seed from) and an amendment posts the composition back in
 * these words. An UNTOUCHED covered line is never posted from the board: the server carries every
 * covered line the body does not name into the next revision itself, verbatim.
 */
/**
 * One SPO share of a frozen decision, in the shape a Reserve row already has.
 *
 * `timely_spo_qty` is the TOTAL and is what `ConfirmLine` takes the composition back in; this
 * says WHERE each share was coming to and WHICH question drew it. Under ladder v5 the water is
 * question 1's answer (`rung: group_take`), so it reads under "Use own location" exactly as
 * the suggestion beside it does.
 */
export interface BoardDecisionIncoming {
  warehouse_id?: string | null;
  location?: string | null;
  qty: string;
  /** `group_take` on a v5 confirmation, `incoming` on one frozen under the retired rung 1. */
  rung?: string | null;
}

export interface BoardLineDecision {
  revision_no: number;
  confirmed_at?: string | null;
  timely_spo_qty: string;
  /** The same quantity split per document location and rung. Empty on an older revision. */
  incoming?: BoardDecisionIncoming[];
  /** Same shape as an amendment's Reserve: addressed by id, labelled by code. */
  reserve: BoardReserveComponent[];
  borrow: BoardDecisionBorrow[];
  buy_qty: string;
  /** Why a discontinued product was bought (AC-B11), when the revision froze one. */
  buy_reason?: string | null;
  /** Why the composition was not the engine's, in the planner's own words. */
  amend_reason?: string | null;
  /** The frozen Buy was an ORDER BACK, and the document CS cited for it (part 2 4b). */
  order_back?: boolean;
  cited_document?: string | null;
  /**
   * The planner flagged this decision as one the numbers behind it look wrong for (R10).
   *
   * Echoed back on the frozen decision so the warning stays on the pill after a reload: a
   * flag that only existed in the session's draft would say the doubt had been answered the
   * moment the page was refreshed.
   */
  suspected_system_issue?: boolean;
}

/**
 * One contributing sales-order line inside a cell: the row of the breakdown table the captain
 * asked for ("which sales order, which customer, which project, the quantity, where should it
 * be supplied from").
 */
export interface BoardContribution {
  /**
   * THIS LINE's own stock position, location by location (R1).
   *
   * The same rows the cell carries, netted of this line's own quantity and no other's, so
   * the group subtotal IS the offer the ladder made it (`max(group net + this line's open
   * qty, 0)`) and the "N available" beside each Reserve input is this line's figure. A cell
   * holding two lines carries two tables. Absent on a line whose bucket is outside the day
   * window, which builds no cell.
   */
  locations?: BoardCellLocation[];
  /** Stable key for the draft. Addressing only, never rendered. */
  key: string;
  sales_order_id: string;
  /**
   * The CORE sales-order line id, which is how its pile queue is asked for. Addressing only,
   * never rendered. NOT `project_line_id`: that is the mirror and is null until the order is
   * adopted, while a line with no mirror still stands in the queue at its pile.
   */
  line_id?: string | null;
  /**
   * The product this line is for, for the drill-down only, never rendered. Two products on the
   * live book share the item code `B2155-NL-BLUE`, so a queue looked up by code would answer
   * confidently about the wrong pile.
   */
  product_id?: string | null;
  so_number: string;
  customer_name?: string | null;
  /**
   * The customer's own id, for GROUPING only - never rendered (no UUIDs in the UI).
   *
   * Two customers can share a name, and a board that merged them would show one row totalling
   * two companies' demand. Absent falls back to the name, which is the merge this exists to
   * prevent, so the fallback is a stated compromise rather than the design.
   */
  customer_id?: string | null;
  /** Who sold it (`sales_orders.sales_agent_id` -> `sales_agents`). Null when the sales order
   * carries no agent. */
  agent_code?: string | null;
  /** Who the code belongs to, shown as the code's `title`, never in place of the code. */
  agent_label?: string | null;
  project_label?: string | null;
  /**
   * The project's normalised key, for grouping only, never rendered.
   *
   * A STRING rather than an id on purpose: an adopted order has no project registration, so the
   * project string on the order IS its identity. Grouping on the raw label instead would merge
   * two spellings of one project and split one project written two ways.
   */
  project_key?: string | null;
  line_no: number;
  /**
   * #1362 item 5: AutoCount's own sales-order line number (`sales_order_lines.line_no`), the
   * "No." the sales order's Lines tab shows. What the Line column, the drawer title and every
   * confirm message print. `line_no` is the planning ADDRESS (the draft key) and is printed
   * only as "row N" when AutoCount gave the line no number.
   */
  so_line_no?: number | null;
  item_code: string;
  /**
   * What this line ASKS FOR: the PLAN quantity, `coalesce(qty_required, qty_ordered)`.
   *
   * NOT the still-owed figure, and no longer an alias of `qty_outstanding` below (14 Sep
   * 2026 ruling). The board asks who decided where a line's stock comes from rather than
   * whether delivery is outstanding, so a delivered unit nobody sourced is a unit to put
   * back and the ladder is asked for the whole quantity.
   *
   * This is the number every composition must sum to: the server's balance check validates
   * against it (`_LineFacts.open_qty`), so the editor, `lineFor` and the cell total all
   * read this one and never `qty_outstanding`.
   */
  qty: string;
  /**
   * What the sales order ORDERED on this line, as a fact off the server.
   *
   * Never derived here by adding delivered to owed: a number the client invented is a number
   * nobody can be held to, and the two would drift the first time a return or a cancellation
   * moved one of them. Absent renders as a stated absence, never as a guess.
   */
  qty_ordered?: string | null;
  /**
   * What is still owed the CUSTOMER: ordered less delivered, floored at zero.
   *
   * A FACT ABOUT DELIVERY, and nothing composes against it. It was an alias of `qty` until
   * the 14 September 2026 ruling and the two now differ on every line with a delivery -
   * printing one under the other's name would make the screen state a delivery that never
   * happened, or ask a planner to balance against a figure the server will refuse.
   */
  qty_outstanding?: string | null;
  /** What has already been delivered against the line. Ordered - delivered = outstanding. */
  qty_delivered?: string | null;
  /**
   * The engine's own proposal, as numbers rather than as a sum over `sources`.
   *
   * Read, never re-derived. The board now proposes what the SHEET proposes - pool and borrow
   * are considered, not just own-location reserve then buy - so any client notion of "nothing
   * free here, therefore buy" would be a second, worse allocator disagreeing with the real one.
   */
  qty_proposed_reserve?: string | null;
  qty_proposed_incoming?: string | null;
  qty_proposed_buy?: string | null;
  /**
   * WHY the Reserve is the size it is, in the strip's vocabulary (PLAN 13.7, fair share).
   *
   * A line may reserve from its own location only what is left after the demand the active
   * policy ranks ahead of it there. `so_qty_ahead` is what those lines still want and
   * `lines_ahead` is how many they are - the active policy decides the order, required date is
   * the tie-break.
   */
  so_qty_ahead?: string | null;
  lines_ahead?: number | null;
  /**
   * What was LEFT AT THIS LINE'S OWN LOCATION when it was reached: on hand, less reserved, less
   * what confirmed decisions hold, less `so_qty_ahead`.
   *
   * THREE NUMBERS LIVE NEAR EACH OTHER AND NONE MAY BE PRINTED AS ANOTHER:
   * - `BoardCellLocation.available_qty` is the WHOLE pile's position, signed, as AutoCount
   *     states it (on hand - all SO + all SPO). It is about the pile, not about any line;
   * - this is what was left for THIS line at its own location;
   * - `qty_proposed_reserve` is what the line actually TOOK, and it may EXCEED this one,
   *     because the shared pool is a second source with a queue of its own. A live card reads
   *     "0 left for this line" beside a Reserve of 9 drawn from the pool, and both are true.
   */
  available_to_this_line?: string | null;
  /** How much could be borrowed for this line, across the donors below. */
  qty_borrow_available?: string | null;
  borrow_candidates?: BoardBorrowCandidate[];
  /**
   * The mirror line the confirmation names. Addressing only, never rendered.
   *
   * A board row cannot be committed without it: `ConfirmLine.project_line_id` is what the
   * per-order confirm endpoint keys on.
   */
  project_line_id?: string | null;
  /** The line's REAL required date, not the bucket it landed in. */
  required_date?: string | null;
  /**
   * This LINE's own required date is before `as_of`.
   *
   * Not the same question as `BoardDateBucket.is_past`, and the difference is the whole reason
   * both exist: a line due yesterday is late even though the week it sits in has not ended, so
   * counting late lines off the bucket flag undercounts them.
   */
  is_past?: boolean;
  /** The core sales-order line's own warehouse code. Null means the source record is silent. */
  fulfilment_location?: string | null;
  /**
   * The same warehouse by id, for the confirm payload only. Never rendered.
   *
   * Needed on the LINE and not only on the sources: an amendment that reserves on a line the
   * engine proposed nothing for has no reserve source to read a warehouse off.
   */
  fulfilment_warehouse_id?: string | null;
  /** The line states no location, so it cannot be planned and blocks its order (AC-FP16). */
  unplannable: boolean;
  /**
   * The BOOK closed this line, and a pending planning change is what will retire it (R3,
   * scenario S5).
   *
   * It owes nothing - the board carries it at zero - but it is still on screen, because a
   * line that vanished the moment it was cancelled would take its change with it and leave
   * nobody anywhere to press Confirm. Nothing is decided FOR it: its apply is the retire
   * path, which is why it counts toward Confirm (N) while carrying no decision of its own.
   */
  cancelled?: boolean;
  /** `sales_order_lines.priority`, when anybody stated one. Almost nobody does. */
  priority?: 'high' | 'medium' | 'low' | null;
  /**
   * Where this row came in the competition for the cell's stock, and why (13.5).
   *
   * `rank_score` is the reorder engine's own `SUM(w*v) / SUM(w present)` over the active
   * `scm.priority_policy`. The factors are carried with it because a ranking nobody can
   * inspect is a ranking nobody will trust: the planner has to be able to see that this order
   * won on need-by date and lost on document age.
   */
  rank_score: number;
  rank_factors: BoardRankFactor[];
  /** The default rule's proposal for this row, in the order the engine proposes them. */
  sources: BoardSource[];
  /**
   * What the ENGINE suggested for this line, beside what was decided (AC-D2).
   *
   * The live ladder on an undecided line - the same list as `sources` there - and the
   * composition FROZEN at confirm on a covered one, where `sources` states the decision and
   * the suggestion would otherwise be lost the moment somebody amended it.
   *
   * `null` on a revision written before the proposal was frozen. "Not recorded" and "the
   * engine suggested nothing" are different answers and the screen says which.
   */
  proposed?: BoardProposed | null;
  /**
   * HOW that proposal was arrived at: the ladder, rung by rung, in the order it was walked.
   *
   * The sources say what the answer is; this says what was checked to get there, including the
   * rungs that gave nothing. Empty for a line that cannot be planned at all.
   */
  trail?: BoardTrailStep[];
  /**
   * WHAT ELSE COULD HAVE BEEN DONE, and when each would land (R36, AC-S3-14).
   *
   * One entry per step of ladder v7.1, in step order, taken or not. The trail says what was
   * CHECKED; this says what each answer would have COST in days, which is the comparison a
   * planner amending the proposal is actually making.
   *
   * Absent - never an empty array - on a line no ladder was walked for (unplannable, or covered
   * by a decision frozen before options existed): "nothing was offered" and "this was built
   * before the engine stated its options" are different answers, and the screen says which.
   */
  options?: BoardLadderOption[];
  /**
   * The item facts the ladder judged this line on. Null, never a set of `false`s, on a line the
   * ladder did not walk (unplannable, covered): it was judged against nothing.
   */
  item_flags?: BoardItemFlags | null;
  /**
   * Ladder v6: the PLANNING UNIT this line was composed inside - every line of its sales
   * order for the same item, location and delivery date, planned as one quantity and either
   * covered from stock whole or bought whole. `unit_line_count` 1 is the ordinary line.
   */
  unit_qty?: string | null;
  unit_line_count?: number | null;
  /**
   * Free stock at this row's location ran out before this row was reached, so the default
   * rule gave it to an earlier-dated row and this one is bought instead (13.5). Named because
   * today the two orders would both silently propose the same stock and the second would only
   * be refused at confirmation.
   *
   * Always false on a covered row: a decided line is not competing for anything.
   */
  contested: boolean;
  /**
   * An ACTIVE decision on this line's sales order already covers it (13.4).
   *
   * The board went on proposing for such a line - "Buy 43" beside a decision that had borrowed
   * 10 and bought 33 - because it kept the line in its own demand while the pile's queue
   * rightly left it out. A covered row is not re-planned: `sources` and `qty_proposed_*` are
   * the FROZEN composition, the trail is empty (no ladder was walked), and the three share
   * fields are absent because it is not in the queue they count.
   */
  covered?: boolean;
  /** What was frozen, when the row is covered. Absent otherwise, never an empty object. */
  decision?: BoardLineDecision | null;
  /**
   * AC-DT-2 (`PLAN-oi-decision-trail-ui.md`): who confirmed the ACTIVE decision that
   * covers this line, and when, flattened onto the line for the Confirmed chip's own
   * tooltip - `decision` above carries no confirmer, since it is shaped for re-posting
   * an amendment. `null`/absent on an uncovered line.
   */
  decided_by_name?: string | null;
  decided_at?: string | null;
  decision_revision?: number | null;
  /**
   * A decision SAVED here but not yet confirmed (S4, R-F): survives leaving the page,
   * another device, another planner. `null`/absent on a line nobody has saved.
   *
   * Distinct from `decision` above, which is what an ACTIVE (confirmed) revision froze -
   * a line can carry a `draft` with no `decision` (saved, not yet confirmed), a `decision`
   * with no `draft` (confirmed, and Confirm deletes the draft it promotes), or neither
   * (untouched, running on the suggestion).
   */
  draft?: BoardLineDraft | null;
  /**
   * AC-DT-2: the same saver facts as `draft.saved_by` / `draft.saved_at`, flattened
   * onto the line under the trail's own names for the tooltip. `null`/absent when
   * nobody has saved one, same as `draft` itself.
   */
  draft_saved_by_name?: string | null;
  draft_saved_at?: string | null;
  /**
   * The order inquiry purchasing was given for this line, reached through the planning
   * record's mirror line, and the state that instruction is in.
   *
   * `null` when there is none - which is most of the board: an inquiry exists only once
   * somebody has confirmed supply on the order. Never an empty object, because "nobody has
   * been told about this line" and "told, about nothing" are different answers.
   */
  order_inquiry?: BoardLineOrderInquiry | null;
  /**
   * What ANOTHER sales order borrowed OFF this line (AC-L6). The captain, 25 August 2026:
   * the donor's cell reads "71 lent to SO415472".
   *
   * An empty list when nothing was lent, never absent, so the cell has one shape to read.
   */
  lent_to?: BoardLineLending[];
  /**
   * Whether the product's supplier sits in the home country (S3,
   * `PLAN-local-supplier-oi-routing.md`), computed once per product for the whole board. A
   * `local` Buy raises no Order Inquiry row on confirm; the List view and the ladder options
   * table mark it with a `Local` pill. `null` while `local_buy_routing_enabled` (System
   * Settings) is off, the shipped default (`PLAN-local-buy-routing-toggle.md`) - every Buy
   * then reaches Order Inquiries and no pill renders. Absent on a server that has not wired
   * origin resolution yet.
   */
  buy_origin?: 'local' | 'overseas' | null;
}

/** What the engine suggested for one line, in the same shape a source is stated in. */
export interface BoardProposed {
  components: BoardSource[];
}

/** One borrow taken OFF a board row by another sales order (AC-L6). */
export interface BoardLineLending {
  /** How much was taken. */
  qty: string;
  /** The order that took it, by its document number - never a UUID. */
  so_number?: string | null;
  /** Its line on that order, so two lines of one order are told apart. */
  line_no?: number | null;
}

/** The order inquiry a board row belongs to, in the two words a person reads it by. */
export interface BoardLineOrderInquiry {
  /** `OI-000123`. Null only on a row raised before inquiries were numbered. */
  inquiry_no?: string | null;
  /** The ROW's own state (`raised` / `placed` / `actioned` / `cancelled`). */
  state: string;
  /**
   * S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-15): the inquiry HEADER's own id and
   * this row's own OI row id, addressing the List view's "OI" column link
   * (`/project-sales/order-inquiries/<inquiry_id>?row=<row_id>`) - resolved server-side
   * off the winning row (`project_fulfilment_board_service._order_inquiries`).
   * Optional because a row raised before inquiries were numbered carries neither; absent
   * means the cell reads as plain text.
   */
  inquiry_id?: string | null;
  row_id?: string | null;
  /**
   * The handshake (`PLAN-scm-oi-handshake.md`): `awaiting`, `acknowledged`, `changed` or
   * `rejected`. A different question from `state`, which says where the quantity sits.
   */
  /**
   * `awaiting` / `acknowledged` / `changed` / `rejected`, or NULL once CS has answered a
   * refusal: the cell is then about their decision and not about the objection that
   * prompted it, so there is no acknowledgement state left to report.
   */
  ack_state?: string | null;
  /**
   * Why purchasing refused it, and who did. Set only on a rejected row - and that is the
   * row whose LINE is undecided again, so the cell has to say why it came back.
   */
  rejected_reason?: string | null;
  rejected_by_name?: string | null;
  /**
   * AC-RL-07 (`PLAN-oi-replan-received-links.md`): what backs the instruction - the
   * winning row's own documents, the SAME reader (`links_for_rows`) the OI worklist
   * chip uses. Empty on a row nobody has linked.
   */
  documents?: BoardLineOrderInquiryDocument[];
  /**
   * Whether the winning row was itself redirected off a document that had already
   * landed (AC-RL-10) - the fulfilment list's inquiry cell reads this for the `used`
   * word (AC-RL-06).
   */
  redirected?: boolean;
}

/** One document behind a board line's instruction (AC-RL-07). */
export interface BoardLineOrderInquiryDocument {
  document: string | null;
  kind: 'po' | 'spo';
  received: boolean;
}

/**
 * One weighted factor behind a row's rank. `present: false` means the value was unknown and the
 * factor was dropped from BOTH sums, never scored as zero: an unknown is not a bad score, and
 * treating it as one is how a ranking starts lying.
 */
export interface BoardRankFactor {
  key: string;
  /** What the policy weights it at. Shown in the tooltip, never as a bare number beside the
   * normalised value: `demand_class 1.00 x0` reads to everybody as a weight of 1.00. */
  weight: number;
  value: number | null;
  /**
   * The absolute fact behind the normalised value, as text: "2026-09-03", "45 days",
   * "project". This is what a planner recognises; the normalised 0-to-1 number is an artefact
   * of the scoring and means nothing on its own, so the raw fact leads.
   */
  raw?: string | null;
  present: boolean;
}

/** The `scm.priority_policy` row a board was ranked by. Named on screen, never assumed. */
export interface BoardPolicy {
  name: string;
  factors: Record<string, number>;
  demand_class_weights: Record<string, number>;
  /**
   * True when this is a what-if the planner asked for rather than the row that is live.
   * A previewed ranking is labelled and cannot be committed against (13.5).
   */
  is_preview: boolean;
  /**
   * The SERVER's verdict that this policy separates none of these rows, so the ranking is flat.
   *
   * Read, never re-derived. The screen can see a weight of zero, but it cannot see a factor
   * that is weighted and CONSTANT - every row on this board is project-class, so `demand_class`
   * can carry weight 3.0 and still order nobody. Only the side that scored the rows knows that.
   */
  discriminates_nothing: boolean;
}

export interface BoardSource {
  kind: BoardSourceKind;
  qty: string;
  /**
   * WHICH LADDER composed this component (`"v5"` today).
   *
   * A frozen suggestion outlives the rule that made it: "MWH-IB has 30 available in the IB
   * group" is a v3 sentence about ONE warehouse's availability, and under v4 that reading
   * does not exist. A LIVE source carries today's version, because it is today's answer by
   * definition; a FROZEN one carries what was stamped when it was frozen, and is absent
   * only on a snapshot older than the stamp itself. The screen labels anything that is not
   * today's rather than passing it off as today's answer (AC-V8).
   */
  ladder?: string | null;
  /** Warehouse code for a Reserve; null for Buy, which has no location by definition. */
  location?: string | null;
  /**
   * The same warehouse by id, for the confirm payload only. Never rendered (no UUIDs in the
   * UI): `ConfirmReserveComponent.warehouse_id` is what the endpoint takes, and the screen
   * names warehouses by code.
   */
  warehouse_id?: string | null;
  /** The sentence the rule wrote, shown beside the quantity. Never a bare code. */
  reason: string;
  /** SPO number when the source is incoming stock. */
  spo_number?: string | null;
  arrival_date?: string | null;
  /** Ladder v2 (section E): which rung produced this source. */
  rung?: string | null;
  donor_so_number?: string | null;
  donor_line_no?: number | null;
  donor_agent_code?: string | null;
  same_agent?: boolean;
  /** Addressing only, never rendered: re-identifies the donor's own line at confirm. */
  donor_core_line_id?: string | null;
  /** The donor's own required date - the order-back's urgency (section E.4). */
  donor_required_date?: string | null;
  /**
   * LADDER v7.1 STEP 3 (`supply_borrow`, S4): the incoming document this quantity comes off.
   *
   * `supply_key` addresses it (`spo:<allocation id>` / `po:<purchase order line id>`) and is
   * NEVER rendered - it is what the Confirm moves the placement link onto. `supply_document`
   * is how a person names it (`SPO 202607-S0105`, `PO 202607-P0031 line 3`), written by the
   * server so the drill row and the engine's sentence cannot spell one document two ways.
   * Both absent on every other rung.
   */
  supply_key?: string | null;
  supply_document?: string | null;
  /**
   * R7 (`PLAN-board-received-stock-own-arrival.md`, S3/S5): `"own_arrival"` on a Reserve
   * born from goods that landed FOR this line (or the rest of its own sales order),
   * `null`/absent on an ordinary Reserve. What the board's "Received N" chip keys off
   * (AC-S5-1) - the same string the engine writes on `contribution["sources"]`
   * (`FulfilmentBoardService._source`).
   */
  source?: 'own_arrival' | null;
}

/** A donor the engine found for a line's Borrow. Named, never an id. */
export interface BoardBorrowCandidate {
  source: BorrowSource;
  warehouse_code: string;
  /**
   * Addressing only, never rendered. The board COMPOSES a Borrow now rather than only
   * mentioning that one is possible, and `ConfirmBorrowComponent` names the donor by id: a
   * warehouse code cannot be resolved into one here without guessing at an id.
   */
  warehouse_id?: string | null;
  donor_project_ref?: string | null;
  donor_project_id?: string | null;
  free_qty: string;
  /** The donor's own AutoCount position, and where the ranking put it (PLAN 13.11). */
  qty_on_hand?: string | null;
  so_qty?: string | null;
  spo_qty?: string | null;
  available_qty?: string | null;
  qty_free?: string | null;
  qty_committed?: string | null;
  /** This line's residual at the borrow rung, and what the donor keeps once it is met. */
  need_qty?: string | null;
  available_after_need?: string | null;
  recommended?: boolean;
  /** What taking it costs whoever holds it (AC-B09). Shown while the borrow is chosen. */
  donor_impact?: BorrowDonorImpact;
  /**
   * Ladder v2 (section E): `group_borrow` or `cross_group_borrow` for a new group-aware
   * donor row, absent for a plain `other_location`/`other_project` donor.
   */
  rung?: string | null;
  donor_so_number?: string | null;
  donor_line_no?: number | null;
  donor_agent_code?: string | null;
  /** Addressing only, never rendered: re-identifies the donor's own line at confirm. */
  donor_core_line_id?: string | null;
  /** Ranked below this line, so the ladder proposes it automatically. */
  lower_ranked?: boolean;
  /** The donor shares this line's own sales agent (section 8) - offered at any rank. */
  same_agent?: boolean;
  /**
   * The SAME location facts the Grid Location table states for this warehouse (S4,
   * `PLAN-local-supplier-oi-routing.md`), threaded through `borrowCandidatesOf` onto
   * `BorrowCandidate.location` for the manual Borrow modal. Absent on a server that has not
   * wired this yet.
   */
  location?: BoardCellLocation;
}

/** One incoming purchase leg at a location, with the document that carries it. */
export interface BoardIncomingLeg {
  spo_number: string;
  arrival_date?: string | null;
  qty: string;
}

/**
 * What one cell owes at ONE location, and what is actually there - the captain's "where will I
 * need to source to fulfil", answered with facts rather than with a proposal.
 *
 * EVERY STOCK FIGURE IS NULL, NEVER ZERO, when the sales order states no location. Rendering a
 * null as "0" would tell the planner there is nothing in stock when the truth is that nobody
 * said where to look, and those are opposite instructions.
 */
/**
 * Where a location stands relative to the cell, as the server tags it.
 *
 * The table lists every location the LADDER consulted, not only the agent's ownership group,
 * so the reader has to be able to tell them apart: `BRW` (a site pool holding 1716) and
 * `DC1-BB` (a group warehouse holding nothing) were two identical-looking rows, and the card
 * quoted a figure only one of them could explain.
 */
export type BoardLocationWhere = 'own' | 'group' | 'site_pool' | 'other_group';

export interface BoardCellLocation {
  location: string | null;
  /** Own location / ownership group / site pool / outside the group. Defaults to `own`. */
  where?: BoardLocationWhere;
  /**
   * The product and warehouse this position is about, for the drill-down only, never rendered.
   *
   * Two products on the live book share the item code `B2155-NL-BLUE`, so resolving a stock
   * position from the code would answer about the wrong one.
   */
  product_id?: string | null;
  warehouse_id?: string | null;
  /** AutoCount's own four, in AutoCount's own words. `available_qty` is SIGNED. */
  so_qty?: string | null;
  spo_qty?: string | null;
  available_qty?: string | null;
  /**
   * The open PURCHASE-order balance here, less what an order-inquiry row already claims off
   * those lines. SPO documents are excluded - they are `spo_qty` already.
   *
   * INFORMATION ONLY and outside `available_qty` on purpose: a purchase order reaches a
   * project line through a link, never by sitting at the location.
   */
  po_open_qty?: string | null;
  /**
   * Ladder v4: what the SET this row belongs to nets between ITS OWN locations, signed -
   * the ownership group for an `own` / `group` row, the five site pools for a `site_pool`
   * row, the donor group for an `other_group` row.
   *
   * THE NUMBER THE ENGINE DECIDED ON. `MWH-IB` reads 7000 available and lends nothing,
   * because the IB group it belongs to nets -15514; the row's own figure explains none of
   * that, and the subtotal prints this instead of a sum of whichever rows are on screen.
   */
  net?: string | null;
  /** Which set that net covers, for the subtotal's label: a group code, or `pools`. */
  net_of?: string | null;
  /**
   * THE RAW net (N1, fix round 5): `net` above has the asking line's own demand added back
   * in, so the SUBTOTAL a planner reads matches "what is left for me" - but the SERVER's own
   * confirm-time guard and `stock-detail` bound a pool-share composition by the net WITHOUT
   * that addition. `poolShareLimitsOf` reads THIS field, never `net`, or it could admit a
   * split the server's own guard refuses the instant this line's own demand padded the
   * figure past what the raw pile carries.
   */
  net_raw?: string | null;
  /** What is owed here. `qty` is kept as an alias of it. */
  qty: string;
  qty_demand?: string | null;
  qty_on_hand?: string | null;
  qty_reserved?: string | null;
  qty_free?: string | null;
  /** Free stock left AFTER this board's own proposals have drawn it down. */
  qty_free_remaining?: string | null;
  /**
   * What confirmed decisions are already holding here. On hand, less reserved, less this, IS
   * `qty_free` - the third term of that arithmetic, so the sum can close on screen.
   */
  qty_held_by_decisions?: string | null;
  /** What the WHOLE BOOK still owes here, not merely the orders on this board. */
  qty_owed_all_orders?: string | null;
  /** Of that, the part a confirmed decision already covers. */
  qty_owed_confirmed?: string | null;
  qty_incoming?: string | null;
  incoming?: BoardIncomingLeg[];
  qty_proposed_reserve?: string | null;
  qty_proposed_incoming?: string | null;
  qty_proposed_buy?: string | null;
  /**
   * S2 (`PLAN-scm-fulfilment-feedback-2sep.md`, R-K): what a `site_pool` row - and the
   * "Site pool subtotal" row built from it - may give a PROJECT line once the pool's own
   * dealer share is kept back: `min(floor(available_qty x (100 - pool_share_pct) / 100),
   * max(net, 0))`, `net` being this SAME row's own five-pool net. Never rendered for an
   * `own` / `group` / `other_group` row (there is no pool share to keep there); on an
   * addressable `site_pool` row it is always a number, `0` included, never blank.
   *
   * The SAME allowance the walk's own step 0 asked the pool for
   * (`front_planning_engine.available_for_project`): the planner reads the number the
   * engine obeyed, never a second computation of it.
   */
  available_for_project?: string | null;
}

/** One cell: this row, by this bucket, across every selected order. */
export interface BoardCell {
  /**
   * What the cell is LABELLED by. The item code on the product axis, and the sales-order
   * number, customer name or project label on the pivoted ones - so the dialog title reads the
   * same way whichever axis produced the cell.
   */
  item_code: string;
  /**
   * What the cell is KEYED by, when that is not its label. Client-side only, set when the board
   * is pivoted: two customers can share a name, so the label cannot be the key.
   */
  row_key?: string;
  bucket_key: string;
  /** Summed across every contributing line, including the unplannable ones (13.7). */
  total_qty: string;
  /**
   * One entry per location. More than one is normal, not exotic, and now for two reasons: the
   * cell's own lines can name several, AND the whole of the sales agent's ownership group is
   * listed beside them (see `location_group`). A group entry carries a demand of `0` - no line
   * of this cell sits there - and the stock facts that are the reason it is listed.
   */
  locations: BoardCellLocation[];
  /**
   * The agents' warehouse-suffix ownership group whose locations are listed above alongside
   * the ones this cell's lines name (`BB` for BRW-BB / MWH-BB / DC1-BB). Several, joined by
   * " / ", when the cell holds orders of agents in different groups. Null when none could be
   * resolved, and `location_group_note` then says why.
   */
  location_group?: string | null;
  /** Why only the line's own location is listed. Set ONLY when `location_group` is null. */
  location_group_note?: string | null;
  contributions: BoardContribution[];
  /** Contributions whose sales order states no location for them. */
  unplannable_count: number;
  /** Contributions the default allocation rule could not cover from free stock. */
  contested_count: number;
  /**
   * Contributions whose own required date is already past. Sent so the summary can be counted
   * without walking every contribution of every cell.
   */
  past_count?: number;
  /** How many DIFFERENT sales orders contribute here. One means an order competing with itself. */
  distinct_order_count?: number;
  /** Whether the ranking actually put these rows in an order, or they all scored alike. */
  rank_separates?: boolean;
}

export interface BoardProductRow {
  item_code: string;
  description?: string | null;
}

/**
 * What the board's VERTICAL axis is (the captain: "how about if we want vertical is sales
 * order, is customer, is project").
 *
 * The horizontal axis is always dates. The pivot is a different grouping of the SAME
 * contributions into cells - no second fetch, and no second idea of what a line is.
 */
export type BoardRowAxis = 'product' | 'sales_order' | 'customer' | 'project';

/**
 * One row of the board, whichever axis is chosen.
 *
 * `key` is an id wherever an id exists and is never rendered; `label` is what the reader sees.
 * Keeping them apart is the whole reason two customers with one name stay two rows.
 */
export interface BoardAxisRow {
  key: string;
  label: string;
  /** Secondary text for the row header, e.g. a product's name under its code. */
  description?: string | null;
}

/**
 * Why Undo of this order's newest confirm would be refused, even though it carries a journal
 * (`PLAN-board-undo-last-confirm.md`, R1): purchasing has already acted on one of the order's
 * OI rows since the confirm - linked a PO line, or marked a row actioned - after the confirm's
 * own journal stopped writing. Null when nothing blocks it. `linked`, not `manual_link` (review
 * round): the `auto` flag on the link is irrelevant - an AutoCount pairing purchasing's own
 * upload placed is purchasing's work as much as a hand click.
 *
 * `changed` (`PLAN-scm-oi-handover-r2-undo.md`, S4, AC-R2-24): a row the journal covers now
 * holds a value another writer wrote after the confirm - undo would overwrite that write, so
 * the park refuses instead of silently discarding it.
 */
export type BoardUndoRefusal = 'linked' | 'actioned' | 'changed' | null;

/**
 * What the board knows about undoing an order's newest confirm (`undo_0001`, S1, #978): the
 * revision the gear entry names, who confirmed it and when, and whether purchasing's own work
 * since then refuses the undo. Null when the order has no active decision, or its active
 * decision was not journalled (a pre-lane revision, or one minted by `uncover_lines` rather
 * than the board's own confirm routes) - there is nothing this lane can replay.
 */
export interface BoardUndo {
  revision_no: number;
  confirmed_at?: string | null;
  confirmed_by_name?: string | null;
  refusal: BoardUndoRefusal;
  /** Addressing only, never rendered: the pending action's payload, so a Confirm
   * written during the countdown is detected server-side (AC-UC-28). */
  decision_id: string;
  /**
   * `journal` replays the order's own journalled revision the normal way. `reconstructed`
   * (`PLAN-scm-oi-handover-r2-undo.md`, S5, AC-R2-30) is a best-effort undo of a decision
   * confirmed before the journal existed - the board only sets this for an admin/superadmin
   * requester; every other role gets `undo: null` for that order and never sees the entry.
   */
  mode: 'journal' | 'reconstructed';
}

/**
 * AC-DT-1 (`PLAN-oi-decision-trail-ui.md`): the board panel's own "Revision N, confirmed
 * by <name>, N lines" line. Distinct from `BoardUndo` above, which is gated to a
 * journalled (or, admin-only, reconstructable) revision for the undo gear specifically -
 * this is a plain read of the ACTIVE decision and is present whenever the order has one.
 */
export interface BoardOrderDecisionHeader {
  revision_no: number;
  confirmed_by_name?: string | null;
  confirmed_at?: string | null;
  line_count: number;
}

/** One selected order's standing, which is what makes the partial-decision reality visible. */
export interface BoardOrderStanding {
  sales_order_id: string;
  so_number: string;
  /**
   * The PLANNING RECORD's id, which is what `POST .../sales-orders/{pso_id}/confirm` takes.
   * Addressing only, never rendered. An order that has not been adopted has none, and the
   * board says so instead of offering a Confirm that cannot post.
   */
  project_sales_order_id?: string | null;
  customer_name?: string | null;
  /** Lines of this order inside the SELECTION - never only the ones a window is showing. */
  line_count: number;
  /** How many of those lines carry a verdict in the draft. The client's, never the server's. */
  decided_count: number;
  /**
   * Of those, how many would actually be POSTED by a Confirm - approved and amended, never
   * rejected. A rejected line is a decision to leave it undecided, so a button counting it
   * would promise to commit something the body deliberately omits.
   */
  committing_count?: number;
  /**
   * Lines an ACTIVE decision covers that the planner has not amended. The body never names
   * them; the server carries them into the next revision verbatim, so a Confirm leaves them
   * decided without posting them.
   */
  carried_count?: number;
  /** Lines that can never be decided here because their sales order states no location. */
  unplannable_count: number;
  /**
   * The newest PENDING planning-change batch on this order, or null/absent
   * (`PLAN-scm-board-picks-up-pending-change.md`, AC-B1). Lets the board load and draw a
   * change's Was/Now table and pre-marked suggestion without a `?batch=` URL param.
   */
  pending_change_batch_id?: string | null;
  /**
   * Whether the order's newest confirm can be undone from the gear, and why not when it
   * cannot. Absent/null on a server that predates S1, or when the order has no active
   * decision, or the active decision was never journalled.
   */
  undo?: BoardUndo | null;
  /**
   * AC-DT-1: the board panel's own "Revision N, confirmed by <name>, N lines" line.
   * `null`/absent when the order has no active decision at all.
   */
  decision?: BoardOrderDecisionHeader | null;
}

/**
 * What one order is about to commit, and what it is deliberately leaving behind.
 *
 * Confirm is NOT gated on completeness (13.4, captain's decision): a planner commits the lines
 * they are sure about precisely so the undecided ones keep flowing to reorder planning. So the
 * screen owes them a plain statement of what is being left, rather than a disabled button.
 */
export interface BoardCommitPreview {
  /** Lines carrying a verdict that would be written by this confirmation. */
  committing: number;
  /** Lines with no verdict, which stay outstanding and keep counting as demand. */
  leaving_undecided: number;
  /** Of those, the ones that could never be decided here (no location on the sales order). */
  blocked: number;
}

/**
 * `GET /project-sales/fulfilment-planning/board`. Claims nothing - no stock is reserved and
 * no decision is written by opening it - but it does mirror a core line that arrived after
 * adoption onto the planning record for an adopted order, so the line carries a
 * `project_line_id` and is confirmable in the same response (#969).
 *
 * The four `*_count` totals are SELECTION-scoped: counted over every contributing line before
 * any window is applied, so they are identical on day, week and month and do not move when the
 * day window is scrolled somewhere empty. Anything summed off `cells` is window-scoped and
 * answers a different question - see `line_count`.
 */
export interface PlanningBoard {
  granularity: BoardGranularity;
  /** Which policy produced the ranking on show. Always stated (13.5). */
  policy: BoardPolicy;
  /** The date the board was built against, so `is_past` is reproducible in a test. */
  as_of: string;
  /**
   * Every contributing line in the selection, windowed or not. The denominator of every
   * headline number on this screen: summing `cells[].contributions.length` instead counts what
   * a window happens to be showing, which is how "160 of 160" became "1 of 2" on a day view.
   */
  line_count: number;
  /** Of those, the lines whose own required date is past. The banner's numerator. */
  past_line_count: number;
  /** Of those, the lines whose sales order states no fulfilment location (AC-FP16). */
  unplannable_line_count: number;
  /** Of those, the lines the allocation rule could not cover from free stock (13.5). */
  contested_line_count: number;
  /**
   * S2 (R-K): how much of a site pool is kept back for dealers, in percent, off the active
   * policy row. Every site-pool ROW already carries the server's own
   * `available_for_project`; this is what the Stock tab's pool SUBTOTAL applies the same
   * rule with, over the pool's own net - a figure that belongs to the SET and so appears on
   * no row.
   */
  pool_share_pct?: number;
  dateBuckets: BoardDateBucket[];
  productRows: BoardProductRow[];
  cells: BoardCell[];
  /**
   * Every contributing line of the SELECTION, in the same shape a cell's own `contributions`
   * carry, but NEVER windowed: `cells` only exists for a bucket that made it onto screen, and
   * at day granularity that is a 30-day window, not the whole selection. Approve all, the
   * "N approved / M undecided" strip, the List view and Confirm all approved all read THIS
   * list - flattening `cells[].contributions` silently drops every line outside the window.
   */
  contributions: BoardContribution[];
  /**
   * One standing per selected order, built from ALL its rows rather than the displayed ones.
   * `decided_count` is always 0 here (deviation 4) and is overlaid from the client draft.
   */
  orders: BoardOrderStanding[];
}

/** What CS did to one contributing row. The board's working draft, persisted nowhere yet. */
export type BoardVerdict = 'approved' | 'amended' | 'rejected';

/** One warehouse's share of an amended Reserve. Addressed by id, labelled by code. */
export interface BoardReserveComponent {
  warehouse_id: string;
  /** The warehouse CODE, for the pill and the editor. Never the id. */
  location?: string | null;
  qty: string;
  /**
   * Which rung the confirmation froze this share under. Server-supplied on a FROZEN
   * decision only; the Amend editor never sets it, and the confirmation ignores it coming
   * back. Read rather than inferred: `BRW-BB` and the pool `BRW` share a site prefix and are
   * not the same kind of supply, so the code cannot answer this question.
   */
  rung?: string | null;
}

/** One donor an amendment borrows from. The confirm body's borrow component, plus its code. */
export interface BoardBorrowComponent {
  source: BorrowSource;
  warehouse_id: string;
  warehouse_code?: string | null;
  donor_project_ref?: string | null;
  donor_project_id?: string | null;
  qty: string;
  /** Mandatory: no Borrow is written without one (AC-B09). */
  reason: string;
  /** Ladder v2 group borrow (section E.4), round-tripped from the donor candidate row. */
  donor_core_line_id?: string | null;
  donor_so_number?: string | null;
  donor_line_no?: number | null;
  donor_agent_code?: string | null;
  same_agent?: boolean;
  donor_required_date?: string | null;
  /**
   * LADDER v7.1 STEP 3 (`supply_borrow`, S4): the incoming document this quantity comes off.
   *
   * `supply_key` addresses it (`spo:<allocation id>` / `po:<purchase order line id>`) and is
   * NEVER rendered - it is what the Confirm moves the placement link onto. `supply_document`
   * is how a person names it (`SPO 202607-S0105`, `PO 202607-P0031 line 3`), written by the
   * server so the drill row and the engine's sentence cannot spell one document two ways.
   * Both absent on every other rung.
   */
  supply_key?: string | null;
  supply_document?: string | null;
  arrival_date?: string | null;
}

export interface BoardDecision {
  verdict: BoardVerdict;
  /**
   * The whole Reserve as one number.
   *
   * Kept beside the components below because it is what a decision taken before the board
   * could compose one carries, and what the pill falls back to. The components are the
   * decision; this is a summary of them.
   */
  reserve_qty?: string;
  /**
   * The composition the planner typed, in full: the SAME four kinds the per-order sheet
   * composes, so an amendment made on the board and one made on the sheet reach the
   * confirmation as the same body.
   *
   * Present on an amend made in the editor. Absent on an approval (the proposal stands, and
   * the confirmation reads the server's own numbers) and on a rejection (nothing is posted).
   */
  reserve?: BoardReserveComponent[];
  borrow?: BoardBorrowComponent[];
  buy_qty?: string;
  /** Why this line still buys a discontinued product, when the product is discontinued and `buy_qty > 0`. Optional. */
  buy_reason?: string;
  /** The server's incoming cover, carried through unedited: it is dated supply, not a choice. */
  timely_spo_qty?: string;
  /**
   * Mandatory on an amend that displaces the default priority rule, and on a reject. Same
   * shape as the Borrow reason the per-line card already demands (AC-B09): a decision a
   * person made by hand has to say why, or the snapshot cannot explain itself later.
   */
  reason?: string;
  /**
   * This Buy is an ORDER BACK, not a fresh purchase (part 2 section 4b, captain 25 Aug):
   * the quantity is a shortfall against something already ordered or already shipped. The
   * order inquiry row is raised with verb `ORDER_BACK`, which is the ONE verb whose links
   * may name an SPO allocation as well as a purchase order line.
   *
   * Only meaningful with `buy_qty > 0`; an amendment that buys nothing has no row to mark.
   */
  order_back?: boolean;
  /**
   * The document CS named on the order back, if they named one. It is not a link by
   * itself - it is what the auto-link walk tries FIRST, before any tier or date
   * (`ProjectOrderInquiryService` candidate order). Free text on purpose: CS types what
   * the form says, and a document we do not hold is recorded rather than refused.
   */
  cited_document?: string | null;
  /**
   * "This might be a system problem, flag it for investigation" (R10).
   *
   * A SECOND answer, beside the verdict rather than instead of it: a planner who amends a
   * line because the availability beside it reads wrong is telling us two different things,
   * and a decision that only recorded the amendment lost the one worth chasing. It travels
   * with whichever verdict was given - approved, amended or rejected - and the confirmation
   * stores it beside `amend_reason` and counts it in the result.
   */
  suspected_system_issue?: boolean;
  /**
   * Seeded by the board's OWN pre-mark effect (`preMarkedKeys`, `FulfilmentBoardPanel`), never
   * chosen by a person and never posted to the server - "Nothing is written here" until Confirm
   * or an explicit Save/Amend/Reject replaces this whole entry with one a person actually gave
   * (`decide()` always writes a fresh object, so an act on a pre-mark drops the flag on its own).
   *
   * PLAN-board-change-proposed-pill (owner ruling 18 Sep 2026): distinguishes the pill's
   * "Change proposed" from "Saved" - a pre-mark with no server-saved `contribution.draft` yet is
   * not an autosave, and the Verdict column reads it to withhold the Undo arrow too (nothing has
   * actually been saved here to undo).
   */
  preMarked?: boolean;
}

/**
 * The panel's own working copy, keyed by `BoardContribution.key` (13.4). Seeded from every
 * `contribution.draft` the board carries and written through to the server by `decide()`.
 */
export type BoardDraft = Record<string, BoardDecision>;

/**
 * A decision saved on the SERVER but not yet confirmed (S4, R-F, PLAN-scm-fulfilment-
 * feedback-2sep.md). `PUT /fulfilment-planning/lines/{contribution_key}/draft` upserts one
 * of these; `DELETE` (Undo) removes it; Confirm promotes the keys it posts and deletes their
 * drafts in the same write. Drafts are SHARED, not per user (R-F: "a second planner sees the
 * same saved lines and the pill names who saved").
 */
export interface BoardLineDraft {
  decision: BoardDecision;
  /** The saver's display name - never a UUID. */
  saved_by: string;
  /** ISO timestamp. */
  saved_at: string;
  /**
   * The engine has re-suggested this line since it was saved (AC-4.4: "a line saved but
   * then re-suggested by a new upload"). Computed by the SERVER on every board read,
   * against the LINE's own facts at save time (S1, code review round 3) - open quantity
   * and required date, never `proposed` below. False on an ordinary saved line.
   */
  stale?: boolean;
  /**
   * WHAT THE ENGINE SUGGESTED at save time (D12, #573, captain ruling): the contribution's
   * own `sources` the moment the draft was written, in the SAME shape. NOT what `stale`
   * above is judged against (that stays the line's own facts, S1) - this exists only so
   * the Sales Order page's Suggested column can read a saved-but-unconfirmed line's
   * composition the way the board's list view already does ("BRW 3 (BRW)"), until Confirm
   * freezes a revision. `null`/absent on a draft saved before D12, or on a save nothing
   * was offered for.
   */
  proposed?: BoardSource[] | null;
}

// ---------------------------------------------------------------------------
// Stock Status with Detail: what the figures on a location ROW of the cell's stock table are
// made of, expanded under that row.
//
// AutoCount shows this as a document list under the position, and the captain reads it there.
// The FE mirrors that: a header line that is the arithmetic, then the documents that produce
// it, then a total that adds back up to the header.
// ---------------------------------------------------------------------------

/** One sales order standing behind the SO quantity. */
export interface StockDetailSalesOrder {
  sales_order_id: string;
  so_number: string;
  customer_name?: string | null;
  customer_id?: string | null;
  /** The BIN this claim sits at. A GROUP read merges several bins into one list. */
  location?: string | null;
  /** Who sold it. Null on a purchase-order row (`StockDetailIncoming`), which is not a sales
   * document and carries no agent by construction. */
  agent_code?: string | null;
  project_label?: string | null;
  demand_class?: string | null;
  doc_date?: string | null;
  delivery_date?: string | null;
  so_qty: string;
  /** The core sales-order line this row is. Addressing only. */
  line_id?: string | null;
  /**
   * One of the lines the drawer was opened for (R5). The list is otherwise a wall of other
   * people's documents, and a planner has to be able to find their own row in it.
   *
   * No rank and no queue state here any more: the queue screen exists to explain a ranking,
   * and half of that question in this list made the one it answers harder to read.
   */
  is_this_line?: boolean;
  /**
   * Set only on the drawer's OWN line when nothing is open on it any more (#1362): what was
   * delivered. Listed at `so_qty` "0" so "My line" still finds it.
   */
  fulfilled_qty?: string | null;
}

/** One purchase order standing behind the SPO quantity. */
export interface StockDetailIncoming {
  spo_number: string;
  supplier_name?: string | null;
  /** The bin it lands at, for the same reason a sales-order row states one. */
  location?: string | null;
  expected_date?: string | null;
  spo_qty: string;
  /**
   * How many days late the promised arrival is; 0 when it is today, ahead or unstated.
   *
   * The quantity is counted either way (captain, 26 August 2026: trust the book - the goods
   * are owed until a re-uploaded book says they arrived). This is what stops that being
   * read as a fresh promise: an overdue row is named as overdue, so the buyer can see which
   * supplier to chase instead of wondering why the cover never lands.
   */
  overdue_days?: number | null;
  /**
   * The day the WALK plans this late document against - `today + overdue_grace_days`
   * (R-O, 3 September 2026). Null when the document is not late, or when it is so late
   * that nothing is assumed about it at all.
   */
  assumed_date?: string | null;
  /**
   * False when the document is later than `overdue_dead_days` and therefore counts as
   * nothing (R31, as R-O leaves it). The row reads "not counted".
   */
  counted?: boolean;
}

/**
 * A confirmed hold on this set's stock, taken by a line booked OUTSIDE the set (R40).
 *
 * Cross-group stock only moves as a PINNED hold, and such a hold appears in no sales-order
 * row of the group whose pile it is drawn from - so a running balance without it would walk a
 * pile bigger than the one a planner can draw on.
 */
export interface StockDetailHold {
  /** The order holding it. Null when its line has not been reconciled to a core order yet. */
  so_number?: string | null;
  location?: string | null;
  /** The holder's own required date, which is where the hold sits in the walk. */
  required_date?: string | null;
  qty: string;
}

/** One member of the set a group read covers, and the pile the drill opens its walk on. */
export interface StockDetailBin {
  warehouse_id: string;
  location: string;
  qty_on_hand: string;
}

/**
 * `GET /project-sales/fulfilment-planning/stock-detail?product_id=&warehouse_id=`.
 *
 * The field names are the SERVER's, checked against `app/schemas/project_board.py`. This type
 * used to declare a `warehouse_code` the backend never sends and to omit three fields it always
 * sends, so a reader of the type learnt the wrong shape and a renderer of `warehouse_code` would
 * have printed nothing for ever without a type error.
 */
export interface StockDetail {
  product_id: string;
  item_code: string;
  description?: string | null;
  /** Null on a GROUP read: the whole set is the answer, and no single bin is it. */
  warehouse_id?: string | null;
  /** The warehouse CODE, which is what the screen shows. Named `location` by the server. */
  location?: string | null;
  /**
   * The SET this read covers - the ownership-group suffix (`IB`) or `pools` - and its members.
   * Null / empty on the ordinary one-bin read. Step 1 of the ladder draws the GROUP's pile, so
   * a running balance is only true when it is read over the group.
   */
  group?: string | null;
  bins?: StockDetailBin[];
  /**
   * `PLAN-oi-request-cs-reserve.md` 3.9: one entry per member of `bins` (the same set), in
   * `CellStockTable`'s own shape - so the OI stock grid (`OrderInquiryStockGrid`) renders the
   * SAME component the board's cell dialog does, rather than a second matrix built from
   * `bins` alone, which carries on-hand only. `where` is `group` for every member of a
   * GROUP read (this endpoint carries no asking line, so it cannot say which one bin is
   * "its own") and `own` for a plain one-bin read; `net`/`net_of` are stated on a GROUP
   * read only.
   */
  locations?: BoardCellLocation[];
  qty_on_hand: string;
  so_qty: string;
  spo_qty: string;
  /** SIGNED: on hand - SO + SPO. Negative is the shortfall and is shown as it arrives. */
  available_qty: string;
  qty_reserved: string;
  /** What confirmed decisions hold here. On hand, less reserved, less this, IS `qty_free`. */
  qty_held_by_decisions: string;
  qty_free: string;
  sales_orders: StockDetailSalesOrder[];
  incoming: StockDetailIncoming[];
  /** Confirmed holds taken by lines booked outside this set. Group reading only. */
  holds?: StockDetailHold[];
  /**
   * S2 (R-K): the five site pools' own net, for capping "Available for Project" on the
   * `group: 'pools'` reading's running ledger. Absent on every other read (a bin, or a
   * non-pool group), which has no pool share to cap.
   *
   * Signed, and read off the engine's own `netting().pools_net()`, so the cap the ledger
   * applies is the cap the walk was bound by (R-D).
   */
  five_pool_net?: string | null;
  /**
   * S2 (R-K): how much of a site pool is kept back for dealers, in percent, off the active
   * policy row. Sent with `five_pool_net` on the `group: 'pools'` reading only; the ledger
   * needs it because it computes its running balances itself and the server never sees them.
   */
  pool_share_pct?: number | null;
}

// ---------------------------------------------------------------------------
// S3 (PLAN-scm-planning-feedback-31aug): the lightbox's own jumps. Declared here, not in
// either component, so `CellStockTable` and `StockDocumentsPanel` - which already import
// each other - share one shape without a circular import between them.
// ---------------------------------------------------------------------------

/**
 * A jump landing: what to scroll to and flash once the right section's documents are on
 * screen. `nonce` is what re-fires the effect on a repeat press of the SAME jump (the
 * mockup's "My line" chip works "from anywhere" - a second click while already on the row
 * must still re-scroll and re-flash it).
 */
export type StockJumpTarget =
  | { kind: 'this-line'; nonce: number }
  | { kind: 'donor'; nonce: number }
  | { kind: 'document'; nonce: number };

/** The donor named by the active suggestion (AC-3.3/3.13), for the row badge and the jump. */
export interface StockDonorMatch {
  soNumber: string;
  /** The donor's own core line id, when the suggestion named one - an exact match where a
   * `so_number` match alone would light up every line of a donor with several. */
  lineId?: string | null;
  /** The holding location, for finding which SECTION to expand. */
  location?: string | null;
}

/** The SPO named by an incoming suggestion (AC-3.4), for the row badge and the jump. */
export interface StockDocumentMatch {
  spoNumber: string;
  location?: string | null;
}

export interface CellStockTableHandle {
  /** AC-3.1/3.2: the default landing on open, and what the toolbar's "My line" repeats. */
  jumpToThisLine: () => void;
  /**
   * AC-3.3/3.13: the toolbar's "Donor" button (no argument - the FIRST donor the shown
   * line's suggestion names) and the suggestion sentence's own donor link, which passes ITS
   * OWN source's donor explicitly (review round, S3): a step-2 combine can name several
   * donors on one line (R35), and a second donor's link has to open its own row, not the
   * first donor's.
   */
  jumpToDonor: (donor?: StockDonorMatch) => void;
  /** AC-3.4: the suggestion sentence's SPO link and the toolbar's document button. Same
   * explicit-argument shape as `jumpToDonor`, for the same reason. */
  jumpToDocument: (documentInfo?: StockDocumentMatch) => void;
}

/**
 * One line of the queue at a pile: `GET /project-sales/fulfilment-planning/queue`.
 *
 * The captain, having been shown the top three beside the rung: "I need to know what is ahead of
 * me to have the visibility, and why they are ahead of me, meaning I need to know their rank
 * also."
 */
export interface PileQueueLine {
  /** 1-based, in the order the stock is served. */
  position: number;
  /** The CORE sales-order line. Addressing only. */
  line_id: string;
  /** Its sales order, so the row can link to it. Addressing only, never rendered. */
  sales_order_id?: string | null;
  so_number: string;
  /** Null until the order has been adopted: an un-mirrored line has no line number. */
  line_no?: number | null;
  customer_name?: string | null;
  qty: string;
  required_date?: string | null;
  order_date?: string | null;
  payment_terms_days?: number | null;
  demand_class?: string | null;
  rank_score: number;
  /** The same per-factor breakdown a board row carries, so one popover explains both. */
  rank_factors: BoardRankFactor[];
  /**
   * Which factor puts this line in front of the one that asked. Null for that line itself and
   * for every row BEHIND it, which is in front of nothing (the screen reads that as "Behind
   * this line"), and null when nobody asked.
   */
  leading_factor?: string | null;
  /** What the queue has claimed by the time it has served this row, this row included. */
  cumulative_ahead_qty: string;
  is_this_line: boolean;
  /** Always false on a row that is here: a covered line is EXCLUDED from the queue. */
  is_covered_excluded: boolean;
}

/** The whole queue at one pile, in the order the stock is actually served. */
export interface PileQueue {
  product_id: string;
  item_code: string;
  description?: string | null;
  warehouse_id: string;
  /** The warehouse CODE, which is what the screen shows. */
  location: string;
  /** What the pile held before the queue drew on it: the trail's own opening figure. */
  qty_free_opening: string;
  /** Where the asked-about line stands. Null when no line was named. */
  this_line_position?: number | null;
  /** The rule that produced this order, named. Always the LIVE policy. */
  policy_name: string;
  lines: PileQueueLine[];
}

/**
 * One location's contribution to a demand class's ranking - the Proof popover's own row.
 *
 * The captain, reading the trail: "don't give me jargon like abc classification, just tell
 * me hot selling or cold selling, at project or retail, with some button for me to view
 * detail as a proof". This is that proof.
 */
export interface ClassificationEvidenceLocation {
  warehouse_code: string;
  qty_delivered: string;
  rank: number;
  of: number;
  /** This row's OWN share of the class's total quantity - "Its share" in the popover. */
  share_pct?: number | null;
  /** The running share INCLUDING this row - "Ranked above it" is this minus `share_pct`. */
  cumulative_share_pct?: number | null;
  /** A/B/C. */
  letter?: string | null;
  hot: boolean;
}

export interface ClassificationEvidenceClass {
  demand_class: 'retail' | 'project';
  /** "Dealer" (retail customers) or "Project" - the word the captain asked for. */
  label: string;
  verdict: 'hot' | 'cold' | 'unclassified';
  locations: ClassificationEvidenceLocation[];
}

/** `GET .../fulfilment-planning/classification?product_id=...` - the Proof button's payload. */
export interface ClassificationEvidence {
  product_id: string;
  item_code?: string | null;
  computed_at?: string | null;
  window_days: number;
  hot_cut_pct: number;
  classes: ClassificationEvidenceClass[];
}

// ---------------------------------------------------------------------------
// The Plans page (PLAN-demo-followups-19aug-ladder-v2 D1): "is the plan stored, how do I
// review it". `GET /project-sales/plans` over `so_supply_decisions`, cross-order.
// ---------------------------------------------------------------------------

export type PlanState = 'active' | 'superseded' | 'challenged';

/** One row: one supply decision revision. Addressed by `project_sales_order_id` and
 * `sales_order_id`, never rendered - `so_number` is what prints. */
export interface PlanRow {
  project_sales_order_id: string;
  sales_order_id?: string | null;
  project_id?: string | null;
  so_number?: string | null;
  customer_name?: string | null;
  agent_code?: string | null;
  agent_label?: string | null;
  revision_no: number;
  state: PlanState;
  decided_by_name?: string | null;
  decided_at?: string | null;
  /** How many lines THIS revision covers - never the order's whole line count (13.4). */
  line_count: number;
  /** "Reserve 213 · Buy 145", summed across every line the revision covers. */
  components_summary?: string | null;
  /** Why the active revision no longer matches the order. Present only when `state` is
   * `challenged`. */
  challenged_reason?: string | null;
}

export const PLAN_SORT_FIELDS = [
  'so_number',
  'customer_name',
  'agent_code',
  'revision_no',
  'state',
  'decided_at',
] as const;

export type PlanSortField = (typeof PLAN_SORT_FIELDS)[number];

export interface PlanListParams {
  page?: number;
  limit?: number;
  query?: string;
  /** Defaults to `active` server-side - what is stored NOW. */
  state?: PlanState;
  agent_code?: string;
  sort?: PlanSortField;
  dir?: 'asc' | 'desc';
}

export interface PlanListEnvelope {
  data: PlanRow[];
  total: number;
  page: number;
  limit: number;
}

// ---------------------------------------------------------------------------
// Confirm all approved (D3): the board's "Confirm all approved" writes several orders at
// once, each in its own transaction server-side, and reports one result per order.
// ---------------------------------------------------------------------------

export interface ConfirmManyOrderBody {
  pso_id: string;
  lines: ConfirmLine[];
  /**
   * This order's OWN planning-change batch (`PLAN-scm-board-picks-up-pending-change.md`,
   * AC-B5/AC-B6): a board can show two orders on two different pending batches, so the
   * batch an order answers travels WITH that order. `null`/absent when the order has none.
   * Falls back to `ConfirmManyBody.batch_id` server-side when absent.
   */
  batch_id?: string | null;
  /** This order's own half of `ConfirmSupplyBody.rejected_line_ids` (owner ruling 23 Sep
   * 2026). Same rule, same refusal alongside a batch. */
  rejected_line_ids?: string[];
  /** Scope this order's press to exactly these mirror line ids (what a Preview showed). */
  only_line_ids?: string[];
}

export interface ConfirmManyBody {
  orders: ConfirmManyOrderBody[];
  /**
   * DEPRECATED as the primary shape (change 4): the board-wide fallback for a single-batch
   * press, kept so a caller that has not moved to `orders[].batch_id` still works. One per
   * board: it was opened at `?orders=...&batch=<id>` and every order on it belonged to that
   * batch. Absent on an ordinary Confirm.
   */
  batch_id?: string | null;
  /** Run the press and roll it back: the answer names what it WOULD raise (FULFIL-CONFIRM-SCOPE v2). */
  preview?: boolean;
}

/** One order inquiry row a previewed press would raise. */
export interface PreviewInquiryRow {
  line_no?: number | null;
  item_code?: string | null;
  verb: string;
  /** False: the row already exists and is only settled by this press. */
  is_new?: boolean;
  qty: string | number;
  delivery_date?: string | null;
  stock_location?: string | null;
  note?: string | null;
}

/** One stock transfer a previewed press would propose. */
export interface PreviewTransfer {
  line_no?: number | null;
  kind: string;
  is_new?: boolean;
  qty: string | number;
  from_location?: string | null;
  to_location?: string | null;
}

/** One order's outcome. `ok` decides which half is populated. */
export interface ConfirmManyOrderResult {
  /** True on a preview: nothing below was committed. */
  preview?: boolean | null;
  inquiry_rows?: PreviewInquiryRow[] | null;
  /** The covered lines this press took out of the confirmation. */
  lines_withdrawn?: ConfirmedLine[] | null;
  transfers?: PreviewTransfer[] | null;
  pso_id: string;
  ok: boolean;
  decision_revision?: number | null;
  inquiry_rows_created?: number | null;
  lines_decided?: number | null;
  lines_undecided?: number | null;
  /** The lines this order's press froze, and how many covered lines rode forward. */
  lines_confirmed?: ConfirmedLine[] | null;
  lines_carried?: number | null;
  /**
   * The movements this order's confirmation raised, the same figure the single-order
   * `ConfirmResult` already carries. Optional because a server that predates the field
   * sends neither, and the board's toast then reports 0 rather than inventing a count.
   */
  transfers_written?: number | null;
  transfers_failed?: number | null;
  /** The open movements this order's confirmation kept as they were (R16). */
  transfers_kept?: number | null;
  /** The lines this order's planner flagged as a suspected system problem (R10). */
  suspected_issues?: number | null;
  /** How many covered lines THIS order's own press withdrew, the per-order twin of
   * `ConfirmResult.rejected_count` (owner ruling 23 Sep 2026). */
  rejected_count?: number | null;
  /** The per-order twin of `ConfirmResult.lines_fulfilled_skipped` (#1362). */
  lines_fulfilled_skipped?: number | null;
  /**
   * #1362 (owner, 29 Sep 2026): lines the server's recheck refused, left out so the rest of
   * the order confirmed. Each keeps its saved decision.
   */
  lines_held_back?: SupplyFailingLine[] | null;
  /**
   * #1362: Buys confirmed as decided over goods that landed for their line. The link stays;
   * purchasing adjusts it off the Buy's order inquiry row.
   */
  landed_buy_notices?: SupplyFailingLine[] | null;
  error?: string | null;
  failing_lines?: SupplyFailingLine[] | null;
}

/** `POST /project-sales/fulfilment-planning/confirm-all`. */
export interface ConfirmManyResult {
  results: ConfirmManyOrderResult[];
}
