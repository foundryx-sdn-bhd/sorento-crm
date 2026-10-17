/**
 * Portal-side price tag request service.
 *
 * Calls `/api/v1/public/portal/submissions/price_tag_request` and
 * `/api/v1/public/portal/lookups` via `portalFetch`.
 *
 * ===========================================================================
 * COMBOS ON A REQUEST LINE (PLAN-price-tag-combos.md D2, slice S2)
 * ===========================================================================
 * A cabinet is sold as a catalogue package, so the line the salesperson picks
 * grows child PART rows: the fixed parts that always come with it, and one
 * OPEN row per choice group they may leave undecided. Submit is never refused
 * for a package reason; a guarded product with no package, or with parts taken
 * off, carries a `package_warning` marketing reads instead.
 *
 * ---- BACKEND CONTRACT (built, S2 Phase 2) --------------------------------
 *
 *  GET /api/v1/public/portal/lookups/product-combos/{product_id}
 *    -> ProductCombosLookup
 *    Same `_assert_visible` gate as `price-tag-items`. The form calls it the
 *    moment a product is picked on a line.
 *
 *    `combos[]` is `{combo_id, name, parts: [{product_id, code, name,
 *    choice_group}]}` - exactly the D1 tables, read through the host.
 *
 *    `host_guarded` is a deviation from D2's literal shape (which listed the
 *    combos alone), ratified by the captain: the client-side warning rule needs
 *    to know whether this product's `class_label` is in system settings'
 *    `price_tag_guarded_classes`, and answering it on the call the form is
 *    already making beats both a second round trip and shipping the tenant's
 *    settings list out to the portal. The server evaluates the same list the
 *    submit-time guard evaluates, so the two cannot disagree.
 *
 *  Line payload (POST/PUT, `PriceTagRequestLineInput`)
 *    `alternatives` is GONE (the column is dropped in Phase 2). Each line now
 *    sends `combo_id` and `parts: LinePartIn[]` in display order; a resolved
 *    part sends `{product_id, role?}`, an open one `{role, candidates: [id]}`.
 *    `show_promo_price` is still NOT sent: it is derived server-side from the
 *    header's `price_mode` on every save (D5), so a client value was always
 *    dead weight. AC-S2-8 lists it; the r7 review already settled it the other
 *    way, and that decision is left standing.
 *
 *  Line read shape (`PriceTagRequestLine`)
 *    gains `combo_id`, `package_warning` and `parts: PriceTagRequestLinePart[]`
 *    - parts RESOLVED (code, name, and each candidate's code and name) the same
 *    way a line already resolves its own product's code and name, because the
 *    portal shows codes and never ids.
 * ===========================================================================
 *
 * ===========================================================================
 * LINE-LEVEL PROMOTION (PLAN-price-tag-line-promo-combo-subject.md D1-D4, S7)
 * ===========================================================================
 * D1: the request-level `promotion_id` header select is retired; a promotion
 * (or a hand-typed price) now lives on the LINE. `price_mode` stays on the
 * header. D4: one pricing call answers every line at once.
 *
 * ---- BACKEND CONTRACT (built) ---------------------------------------------
 *
 *  POST /api/v1/public/portal/lookups/line-pricing
 *    body  { lines: [{ key, product_id, part_product_ids: string[],
 *            candidate_product_ids: string[], promotion_id?: string | null }] }
 *    ->    LinePricingResult[]  (one per input `key`, same order)
 *
 *    `list_price` = sum of list price over the parent + every RESOLVED part
 *    (an unresolved candidate group contributes nothing). `promotion_options`
 *    is every active promotion covering at least one product on the line,
 *    sorted lowest tag total first; `auto_promotion_id` is that first id (or
 *    null with none covering). `sell_price` is the total under `promotion_id`
 *    when given, else under `auto_promotion_id`, else null. `parts_at_list`
 *    names which part product ids print at list under that promotion (D3: a
 *    promotion may cover the parent but not every part). `candidates` prices
 *    every id in `candidate_product_ids` the same way, for the part row's
 *    "CODE  RM x" copy (S2).
 *
 *  Line create/update/response gains `promotion_id`, `promotion_name`,
 *  `manual_sell_price`, `list_price`, `sell_price`, `sell_price_basis`
 *  (`manual` | `promotion` | `list`); the request-level `promotion_id` /
 *  `promotion_name` are dropped (backfilled into lines first).
 * ===========================================================================
 */

import { extractApiError } from '@/lib/api-client';
import type { PrintBy } from '@/lib/dealer-kit/print-collection';
import type {
  ChangeRequestPayload,
  ReviewComment,
} from '@/lib/dealer-kit/review-comments';
import {
  fetchPortalAttachmentBytes,
  portalFetch,
  unwrap,
  type PortalAttachment,
  type PortalRevisionDraft,
  type PortalRevisionPolicy,
  type PortalSubmissionSummary,
} from './portal-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PriceTagLineType = 'product' | 'product_set';

/** A part product the customer picks ONE of, inside an open row. */
export interface LinePartCandidate {
  product_id: string;
  code: string;
  name: string;
}

/**
 * One part row under a line (D2).
 *
 * Resolved: `product_id` is set and `candidates` is empty - a specific product
 * is on the tag. Open: `product_id` is null and `candidates` holds the group's
 * options - the salesperson left the choice to marketing, who split it into one
 * tag per option (S3). `role` is the choice group's label on both, so a resolved
 * row still says which group it answered.
 */
export interface PriceTagRequestLinePart {
  id: string;
  product_id: string | null;
  /** Resolved from the product, as everywhere else in the portal. Null on an open row. */
  code: string | null;
  name: string | null;
  role: string | null;
  candidates: LinePartCandidate[];
  sort_order: number;
}

/**
 * One printed tag under a line (D3), as the portal read view needs it.
 *
 * The portal neither splits nor prices a tag - marketing does both - so this
 * carries only what a salesperson is shown: the label a pin's rail entry names
 * ("1a"), and the id a pin anchors to.
 */
export interface PriceTagRequestTag {
  id: string;
  /** "1a", "1b" - the line's position plus a letter. Never an id. */
  label: string;
  quantity: number;
}

export interface PriceTagRequestLine {
  id: string;
  line_type: PriceTagLineType;
  product_id: string | null;
  product_set_id: string | null;
  /** Human-readable name resolved from the product/set. */
  name: string;
  /** Human-readable code resolved from the product/set. */
  code: string;
  show_promo_price: boolean;
  quantity: number;
  included_accessories: string | null;
  /** Free-text note on the line (D6). Set on the line, not the header, so
   *  each product can carry its own instruction. */
  remarks: string | null;
  sort_order: number;
  /** Derived class on the product - used for the set guard. */
  product_class?: string | null;
  /** The catalogue package this line was asked for as (D2). Null = none chosen. */
  combo_id?: string | null;
  /** What the package guard found at submit, for marketing to read. Null = clean. */
  package_warning?: string | null;
  /** The parts under this line, in display order. */
  parts?: PriceTagRequestLinePart[];
  /** What actually prints for this line: one tag by default, N after a split. */
  tags?: PriceTagRequestTag[];
  // ---- D1: the line's own promotion / manual price, resolved server-side.
  // Optional so a server row that predates the migration keeps validating;
  // the form falls back to `lookupLinePricing` while these are absent.
  promotion_id?: string | null;
  promotion_name?: string | null;
  /** `YYYY-MM-DD` once the line's promotion has ended, else null (PRICE-TAG-A3-EXPIRED). */
  promotion_expired_on?: string | null;
  manual_sell_price?: number | null;
  list_price?: number | null;
  sell_price?: number | null;
  sell_price_basis?: 'manual' | 'promotion' | 'list' | null;
}

/** Header-level price mode (D5): replaces the per-line "Promo price" switch.
 *  `selling` requires a promotion - the server rejects it without one. */
export type PriceMode = 'list' | 'selling';

export interface PriceTagRequestSummary {
  id: string;
  doc_number: string;
  debtor_code: string | null;
  /** Null on a draft: Save Draft validates nothing (D48a). */
  debtor_name: string | null;
  /** Null on a draft, for the same reason as `debtor_name`. */
  needed_by_date: string | null;
  notes: string | null;
  /** The registered project, when one is linked; `project_code` is what is shown. */
  project_id?: string | null;
  project_code?: string | null;
  project_label?: string | null;
  /** Defaults to 'list' server-side; absent on a request created before r7. */
  price_mode?: PriceMode;
  status: string;
  line_count: number;
  created_at: string;
  /** Set while the request is a draft the salesperson has not submitted. */
  portal_draft_at?: string | null;
  /** Who prints (r9 D7). Null until the salesperson says, and required at submit. */
  print_by?: PrintBy | null;
  /** Which review round the design is on (D4). Pins from an earlier round
   *  render grey: they were about a proof that has since been redrawn. */
  review_round?: number;
  /** Set when the office marked the tags ready to pick up (D9). */
  ready_for_collection_at?: string | null;
  collected_at?: string | null;
  /** R3-1: the same revision fields the legacy kinds' own summaries carry. */
  revision_no?: number;
  last_revised_at?: string | null;
  has_revision_draft?: boolean;
}

export interface PriceTagRequestDetail extends PriceTagRequestSummary {
  contact_id: string;
  lines: PriceTagRequestLine[];
  /** Same shape the legacy submission kinds already carry (`_list_attachments_for`
   *  serves both), which is what lets the PO dropzone and its preview/download
   *  read this request exactly like any other portal submission. */
  attachments: PortalAttachment[];
  /** Set while the request is a draft. This, not the status, is what says so:
   *  a draft's status is `new`, the same status a submitted request keeps until
   *  marketing claims it. */
  portal_draft_at?: string | null;
  /** Whether a finished tag sheet PDF exists for this request - what the
   *  read-only header's gear reads to enable/disable Download PDF without a
   *  second round trip. */
  has_completed_export?: boolean;
  /**
   * r10 S9: `null` = never asked for one, `pending` = queued/running,
   * `failed` = the last attempt died (no retry happened on its own),
   * `ready` = the same thing `has_completed_export` says, spelled out so the
   * menu item can read all three states apart instead of only yes/no.
   */
  latest_export_status?: 'ready' | 'pending' | 'failed' | null;
  /**
   * D-P6/AC-B6: true while a post-submit edit is allowed (status `new` or
   * `changes_requested`, not a draft - a draft is already editable via
   * `portal_draft_at`). The FE Edit button reads this, never the status
   * list directly. Sent by the server since S8.
   */
  is_editable?: boolean;
  /** AC-R7 round 3: the policy block (allowed, remaining, blocked reason) -
   *  same as the legacy kinds' detail bodies, read straight off the
   *  re-fetched request instead of a second `useRevisionPolicy` GET. */
  revision?: PortalRevisionPolicy | null;
  /** The in-progress revise composer, if any - rides along the same way. */
  revision_draft?: PortalRevisionDraft | null;
}

export interface DebtorOption {
  code: string;
  name: string;
}

export interface PromotionOption {
  id: string;
  name: string;
}

export interface ProductOption {
  id: string;
  code: string;
  name: string;
  product_class: string | null;
}

export interface ProductSetOption {
  id: string;
  code: string;
  name: string;
}

/**
 * One row of the lines table's single Item picker (D47): a set OR a product, in
 * the same list, because a dealer does not know which of the two a thing is.
 *
 * `id` is the real `products.id` / `product_sets.id`, which is what a line's
 * foreign key stores. The portal's generic product lookup answers with a code and
 * no id at all, so a product line built from it could never be saved.
 */
export interface TagItemOption {
  kind: PriceTagLineType;
  id: string;
  code: string;
  name: string;
}

/** One part of a combo, as the combos lookup answers it. */
export interface ComboPartOption {
  product_id: string;
  code: string;
  name: string;
  /** Null = fixed part. A label = one of the options for that label. */
  choice_group: string | null;
}

/** One catalogue package on a host product, named the way the catalogue names it. */
export interface ProductComboOption {
  combo_id: string;
  name: string;
  parts: ComboPartOption[];
}

export interface ProductCombosLookup {
  /** The host's class is in system settings' `price_tag_guarded_classes` (D2). */
  host_guarded: boolean;
  combos: ProductComboOption[];
}

/** One part on the way OUT, in the payload's own shape (D2). */
export type LinePartIn = {
  product_id?: string | null;
  role?: string | null;
  /** Product ids, on an open row only. Empty on a resolved one. */
  candidates?: string[];
};

/**
 * One line on the way OUT (AC-S2-8).
 *
 * Spelled out rather than derived from `PriceTagRequestLine` with `Omit`: the
 * read shape now carries resolved parts (codes, names, candidate names) and a
 * server-computed `package_warning`, none of which the client sends.
 *
 * A `type`, not an `interface`: the portal revise call takes
 * `Record<string, unknown>[]`, and only a type alias gets TypeScript's implicit
 * index signature - an interface would need a cast at that one call site.
 */
export type PriceTagRequestLineInput = {
  line_type: PriceTagLineType;
  product_id: string | null;
  product_set_id: string | null;
  combo_id: string | null;
  quantity: number;
  included_accessories: string | null;
  remarks: string | null;
  product_class: string | null;
  parts: LinePartIn[];
  // D1/D2 (S6): the line's own price basis - mutually exclusive
  // (AC-S6-4, picking a promotion clears manual and vice versa). Sent on
  // create, update AND revise (the same `payloadLines()` builds all three).
  promotion_id?: string | null;
  manual_sell_price?: number | null;
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BASE = '/api/v1/public/portal/submissions/price_tag_request';
const LOOKUPS = '/api/v1/public/portal/lookups';

// ---------------------------------------------------------------------------
// Lookups
// ---------------------------------------------------------------------------

export async function lookupDebtors(query?: string): Promise<DebtorOption[]> {
  const usp = new URLSearchParams();
  if (query) usp.set('q', query);
  const qs = usp.toString();
  const url = qs
    ? `${LOOKUPS}/debtors-for-agent?${qs}`
    : `${LOOKUPS}/debtors-for-agent`;
  const res = await portalFetch(url);
  const items = await unwrap<
    { customer_code: string | null; customer_name: string | null; debtor_code: string | null; debtor_name: string | null }[]
  >(res, 'Failed to load debtors');

  return items.map((d) => ({
    code: d.debtor_code ?? d.customer_code ?? '',
    name: d.debtor_name ?? d.customer_name ?? '',
  }));
}

export async function lookupPromotions(query?: string): Promise<PromotionOption[]> {
  const usp = new URLSearchParams();
  if (query) usp.set('q', query);
  const qs = usp.toString();
  const url = qs ? `${LOOKUPS}/promotions?${qs}` : `${LOOKUPS}/promotions`;
  const res = await portalFetch(url);
  return unwrap<PromotionOption[]>(res, 'Failed to load promotions');
}

export async function lookupProducts(query?: string): Promise<ProductOption[]> {
  const usp = new URLSearchParams();
  if (query) usp.set('q', query);
  const qs = usp.toString();
  const url = qs
    ? `${LOOKUPS}/products?${qs}`
    : `${LOOKUPS}/products`;
  const res = await portalFetch(url);
  const items = await unwrap<
    { product_code: string; product_name: string | null; category_code: string | null; category_name: string | null }[]
  >(res, 'Failed to load products');

  return items.map((p) => ({
    // The portal products endpoint returns product_code, not an id.
    // Use product_code as the id since that is the identifier available.
    id: p.product_code,
    code: p.product_code,
    name: p.product_name ?? '',
    product_class: p.category_name ?? null,
  }));
}

// TODO: No portal product-sets lookup endpoint exists yet. Keep client-side stub.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export async function lookupProductSets(query?: string): Promise<ProductSetOption[]> {
  return [];
}

/**
 * Sets and products in one list, for the lines table's single Item dropdown.
 *
 * The alternatives picker reads the same call and keeps the products: one endpoint
 * for one question ("what can go on a tag?") beats two round trips per keystroke,
 * and the handful of sets it also returns costs nothing.
 */
export async function lookupTagItems(query?: string): Promise<TagItemOption[]> {
  const usp = new URLSearchParams();
  if (query && query.trim()) usp.set('q', query.trim());
  const qs = usp.toString();
  const url = qs
    ? `${LOOKUPS}/price-tag-items?${qs}`
    : `${LOOKUPS}/price-tag-items`;
  const res = await portalFetch(url);
  return unwrap<TagItemOption[]>(res, 'Failed to load products and sets');
}

/**
 * The catalogue packages a picked product is sold as, plus whether its class is
 * guarded (D2). Called once per product pick on a line.
 */
export async function lookupProductCombos(
  productId: string,
): Promise<ProductCombosLookup> {
  const res = await portalFetch(
    `${LOOKUPS}/product-combos/${encodeURIComponent(productId)}`,
  );
  return unwrap<ProductCombosLookup>(res, 'Failed to load the packages for this product');
}

// ---------------------------------------------------------------------------
// Line pricing (D1-D4, S7 - see the contract block at the top of this file)
// ---------------------------------------------------------------------------

export type {
  LinePricingCandidate,
  LinePricingLineInput,
  LinePricingPromotionOption,
  LinePricingResult,
  SellPriceBasis,
} from '@/lib/dealer-kit/line-pricing-types';
import type {
  LinePricingLineInput as LinePricingLineInputT,
  LinePricingResult as LinePricingResultT,
} from '@/lib/dealer-kit/line-pricing-types';

/**
 * One pricing call for every line (D4, S7). Audience-scoped to THIS
 * contact - the portal route reads it off the portal token, not a param
 * this call sends.
 *
 * `_priceMode` is not sent either: the route's body has no `price_mode`
 * field (`LinePricingRequest`, S7) - `sell_price` is a real number in List
 * mode too, and the mode only decides what the FORM does with the answer.
 */
export async function lookupLinePricing(
  _priceMode: PriceMode,
  lines: LinePricingLineInputT[],
): Promise<LinePricingResultT[]> {
  const res = await portalFetch(`${LOOKUPS}/line-pricing`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines }),
  });
  return unwrap<LinePricingResultT[]>(res, 'Failed to price these lines');
}

// ---------------------------------------------------------------------------
// Refusals that name a field
// ---------------------------------------------------------------------------

/**
 * A refusal the form can put where the problem is (D48b).
 *
 * The server answers `{message, detail, code}` and `detail` on these routes is a
 * comma-separated list of field keys: `debtor_name`, `needed_by_date`, `lines`,
 * `line:<index>`. `unwrap` keeps only the message, which is why a set guard
 * refusal used to arrive as a toast with no way back to the row it was about.
 */
export class PriceTagRequestError extends Error {
  readonly code: string | null;
  readonly fields: string[];

  constructor(message: string, code: string | null, fields: string[]) {
    super(message);
    this.name = 'PriceTagRequestError';
    this.code = code;
    this.fields = fields;
  }
}

async function unwrapNamingFields<T>(res: Response, fallback: string): Promise<T> {
  if (res.ok) return (await res.json()) as T;
  // The clone is what lets `extractApiError` own the message (one implementation
  // of that, per PRINCIPLES) while this still reads the code and the field list
  // beside it. A body can only be consumed once.
  const spare = res.clone();
  const message = await extractApiError(res, fallback);
  let body: { code?: unknown; detail?: unknown } | null = null;
  try {
    body = (await spare.json()) as { code?: unknown; detail?: unknown };
  } catch {
    body = null;
  }
  const code = typeof body?.code === 'string' ? body.code : null;
  const fields =
    typeof body?.detail === 'string' && body.detail
      ? body.detail.split(',').map((f) => f.trim()).filter(Boolean)
      : [];
  throw new PriceTagRequestError(message, code, fields);
}

// ---------------------------------------------------------------------------
// CRUD
// ---------------------------------------------------------------------------

export async function listRequests(
  q?: string,
): Promise<PriceTagRequestSummary[]> {
  const usp = new URLSearchParams();
  if (q && q.trim()) usp.set('q', q.trim());
  const qs = usp.toString();
  const res = await portalFetch(qs ? `${BASE}?${qs}` : BASE);
  const data = await unwrap<{ items: PriceTagRequestSummary[] }>(
    res,
    'Failed to load requests',
  );
  return data.items ?? [];
}

/**
 * The same list in the shape the portal landing's cards read (D45).
 *
 * The price tag endpoint answers its own row type rather than the legacy
 * `PortalSubmissionSummary`, and adapting it HERE is what lets the landing stay
 * ignorant of the difference without any legacy endpoint changing. `doc_number`
 * is the card's primary line, the dealer is its customer line, and a request
 * still carrying `portal_draft_at` is a draft, which is the only thing that
 * tells a saved-but-unsent request from a submitted one.
 */
export async function listRequestsAsSummaries(
  q?: string,
): Promise<PortalSubmissionSummary[]> {
  const rows = await listRequests(q);
  return rows.map((r) => {
    const isDraft = Boolean(r.portal_draft_at);
    return {
      id: r.id,
      kind: 'price_tag_request' as const,
      // A draft may have no dealer yet, and a card with a blank first line reads
      // as a broken row rather than an unfinished one.
      title: r.debtor_name || r.doc_number,
      document_number: r.doc_number,
      reference: null,
      status: r.status,
      is_editable: isDraft,
      is_draft: isDraft,
      created_at: r.created_at,
      customer_name: r.debtor_name,
      needed_by_date: r.needed_by_date,
      // AC-R7: the same revision fields the legacy kinds' own summaries carry
      // (D45's landing card reads these for the "Rev N" / "Revising" badges) -
      // the list payload already carries all three, this just stopped
      // dropping them.
      revision_no: r.revision_no,
      last_revised_at: r.last_revised_at,
      has_revision_draft: r.has_revision_draft,
    };
  });
}

export async function getRequest(id: string): Promise<PriceTagRequestDetail | null> {
  const res = await portalFetch(
    `${BASE}/${encodeURIComponent(id)}`,
  );
  if (res.status === 404) return null;
  // D-P6/S8: `is_editable` is the server's own field now (AC-B6) - true for a
  // draft, or a submitted request at New / Changes requested. No FE mock left.
  return unwrap<PriceTagRequestDetail>(res, 'Failed to load request');
}

/**
 * What Save Draft posts, which is whatever the salesperson has filled in so far
 * (D48a). Everything is nullable because the request row is: completeness is
 * checked on submit, where the server can name what is missing.
 */
export interface CreatePriceTagRequestInput {
  debtor_code: string | null;
  debtor_name: string | null;
  // D1 (S6): no request-level `promotion_id` any more - both `PriceTagRequestCreate`
  // and `...Update` `extra="forbid"` it now; the promotion is a LINE fact
  // (`PriceTagRequestLineInput.promotion_id`).
  needed_by_date: string | null;
  notes: string | null;
  /** The optional registered project; null clears it. */
  project_id?: string | null;
  price_mode: PriceMode;
  /**
   * Who prints (r9 D7). Null on a draft; `submit` refuses with 422
   * `PRINT_BY_REQUIRED` while it is still null, because the answer decides
   * whether the request ends at approved or waits for a collection.
   */
  print_by: PrintBy | null;
  // `show_promo_price` is NOT sent (D5, review fix): the service derives it
  // on every line save from the header's own `price_mode`, so a value the
  // client sent was always dead weight, immediately overridden either way.
  lines: PriceTagRequestLineInput[];
}

export async function createRequest(
  data: CreatePriceTagRequestInput,
): Promise<PriceTagRequestDetail> {
  const res = await portalFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapNamingFields<PriceTagRequestDetail>(res, 'Failed to create request');
}

export async function updateRequest(
  id: string,
  data: Partial<CreatePriceTagRequestInput>,
): Promise<PriceTagRequestDetail> {
  const res = await portalFetch(
    `${BASE}/${encodeURIComponent(id)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    },
  );
  return unwrapNamingFields<PriceTagRequestDetail>(res, 'Failed to update request');
}

/**
 * The salesperson confirming they have the tags (r9 D8).
 *
 * ```
 * POST /api/v1/public/portal/submissions/price_tag_request/{id}/collect
 *   200 { status: "collected" }
 *   409 unless the request is `ready_for_collection`.
 * ```
 *
 * The status and nothing else: the caller refetches the request, so a second
 * copy of the timestamp here would be one more thing that can disagree.
 */
export async function collectRequest(id: string): Promise<{ status: string }> {
  const res = await portalFetch(`${BASE}/${encodeURIComponent(id)}/collect`, {
    method: 'POST',
  });
  return unwrap<{ status: string }>(res, 'Failed to mark this collected');
}

export async function submitRequest(id: string): Promise<{ status: string }> {
  const res = await portalFetch(
    `${BASE}/${encodeURIComponent(id)}/submit`,
    { method: 'POST' },
  );
  return unwrapNamingFields<{ status: string }>(res, 'Failed to submit request');
}

/** Hard-delete a draft. The server refuses once it has been submitted. */
export async function deleteRequest(id: string): Promise<void> {
  const res = await portalFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  if (res.ok) return;
  throw new Error(await extractApiError(res, 'Failed to delete draft'));
}

export async function approveRequest(id: string): Promise<{ status: string }> {
  const res = await portalFetch(
    `${BASE}/${encodeURIComponent(id)}/approve`,
    { method: 'POST' },
  );
  return unwrap<{ status: string }>(res, 'Failed to approve request');
}

/**
 * Send the round's change requests (r9 S2/D5).
 *
 * ```
 * POST /api/v1/public/portal/submissions/price_tag_request/{id}/request-changes
 *   { comments: [{ line_id, x, y, w, h, body }], note? }
 *   200 { status: "changes_requested", round, comments: ReviewComment[] }
 * ```
 *
 * One call for the whole round: the pins are placed locally and nothing reaches
 * the server until Send, so a salesperson can put five pins down, delete two,
 * and the request changes state exactly once. The old text-only `{ note }` body
 * stays accepted server-side for one release and lands as a general comment.
 *
 * Contract and the row shape: `lib/dealer-kit/review-comments.ts`.
 *
 */
export async function requestChanges(
  id: string,
  payload: ChangeRequestPayload,
): Promise<{ status: string; round: number; comments: ReviewComment[] }> {
  const res = await portalFetch(
    `${BASE}/${encodeURIComponent(id)}/request-changes`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  return unwrap<{ status: string; round: number; comments: ReviewComment[] }>(
    res,
    'Failed to send the change requests',
  );
}

/**
 * Every change request sent on this design, all rounds (D6).
 *
 * ```
 * GET /api/v1/public/portal/submissions/price_tag_request/{id}/review-comments
 *   200 ReviewComment[]
 * ```
 *
 */
export async function listReviewComments(id: string): Promise<ReviewComment[]> {
  const res = await portalFetch(
    `${BASE}/${encodeURIComponent(id)}/review-comments`,
  );
  return unwrap<ReviewComment[]>(res, 'Failed to load the change requests');
}

// ---------------------------------------------------------------------------
// Download the finished PDF (D19/S2)
// ---------------------------------------------------------------------------

/** `attachment; filename="tags.pdf"` -> `tags.pdf`, or null when absent. */
function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const quoted = /filename\*?=(?:UTF-8'')?"([^"]+)"/i.exec(header);
  if (quoted?.[1]) return decodeURIComponent(quoted[1]);
  const bare = /filename\*?=(?:UTF-8'')?([^;]+)/i.exec(header);
  return bare?.[1] ? decodeURIComponent(bare[1].trim()) : null;
}

function saveBlobAs(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoked a tick later: the click starts the save asynchronously and some
  // browsers have not read the object url yet by the time this line runs.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * The request's latest completed tag sheet PDF, fetched with the portal token
 * and saved through the browser (D19). Same-origin bytes route, not a signed
 * URL: a bucket's presigned link is cross-origin and sends no auth header, so
 * `fetch` is what the gear's Download PDF item actually calls.
 */
export async function downloadPriceTagPdf(id: string): Promise<void> {
  const res = await fetchPortalAttachmentBytes(
    `${BASE}/${encodeURIComponent(id)}/download`,
  );
  if (!res.ok) {
    throw new Error(await extractApiError(res, 'Failed to download the PDF'));
  }
  const blob = await res.blob();
  const filename =
    filenameFromContentDisposition(res.headers.get('Content-Disposition')) ||
    'tag-sheet.pdf';
  saveBlobAs(blob, filename);
}

/**
 * r10 S9: queue a tag sheet export for this request when none is ready yet
 * (Approve auto-queues one, but a demo caught three requests where that
 * export failed and nothing on the portal could ask for a second try).
 *
 * ```
 * POST /api/v1/public/portal/submissions/price_tag_request/{id}/export
 *   202 { download_id: string }
 *   404 wrong contact's request
 *   409 request not `approved` or later
 * ```
 *
 * A double click (or the poll racing a slow one) is idempotent server-side
 * (AC-S9-7): a second call while one is still pending answers 202 with the
 * SAME `download_id`, never a second queued render.
 *
 * The caller polls `getRequest` for `latest_export_status` to flip to
 * `ready` (or `failed`), same as the designer's own export button.
 */
export async function requestPriceTagExport(id: string): Promise<{ status: string }> {
  const res = await portalFetch(
    `${BASE}/${encodeURIComponent(id)}/export`,
    { method: 'POST' },
  );
  return unwrap<{ status: string }>(res, 'Failed to queue the PDF export');
}
