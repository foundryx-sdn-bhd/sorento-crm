/**
 * CRM-side price tag request service.
 *
 * Calls `/api/v1/dealer-kit/price-tag-requests` via `apiFetch`.
 *
 * ===========================================================================
 * ONE LINE, MANY TAGS (PLAN-price-tag-combos.md D3, slice S3)
 * ===========================================================================
 * A request LINE is what the salesperson asked for. A TAG is what gets
 * printed. They were the same thing until S3; now a line whose package leaves
 * a choice group open is split by marketing into one tag per candidate, each
 * with its own design, quantity, choices and marketing override. A tag is
 * shown as "1a", "1b" (line index plus a letter) and never as an id (AC-X-2).
 *
 * ---- BACKEND CONTRACT (built, S3 Phase 2) --------------------------------
 *
 *  GET /dealer-kit/price-tag-requests/{id}
 *    `lines[]` gains:
 *      `parts: PriceTagRequestLinePart[]`   what the salesperson asked for (S2)
 *      `package_warning: string | null`     the S2 guard's verdict
 *      `tags: PriceTagRequestTag[]`         one per printed tag, sort order
 *    and LOSES `marketing_price_override` / `marketing_override_reason`, which
 *    move onto the tag, and `alternatives`, which is dropped in S2.
 *
 *  POST /dealer-kit/price-tag-requests/{id}/resolve-prices
 *    body: string[] | null  (TAG ids now, or null for every tag)
 *    -> one row per TAG: today's keys plus `tag_id`, `line_id`, `tag_label`,
 *       `open_groups` and `parts`; `quantity` is the TAG's.
 *
 *  PATCH /dealer-kit/price-tag-requests/{id}/tags/{tag_id}
 *    body `{quantity?, marketing_price_override?, marketing_override_reason?}`
 *    -> the updated tag. The marketing override moved here from the retired
 *    line route.
 *
 *  POST /dealer-kit/price-tag-requests/{id}/tags/{tag_id}/split   REMOVED
 *    D6 (PLAN-price-tag-line-promo-combo-subject.md): open groups now split
 *    where tags are built (create/update/revise), one tag per candidate
 *    combination, straight-line - no Split / Pick one left for the designer
 *    to do by hand, so `splitRequestTag` and `PriceTagRequestTagUpdate.choices`
 *    are gone with it.
 *
 *  DELETE /dealer-kit/price-tag-requests/{id}/tags/{tag_id}
 *    204, or 422 `LAST_TAG` when it is the line's only tag. No UI in S3 (the
 *    UAC puts tag removal on the designer in a later round); documented because
 *    it is part of D3's route table.
 *
 *  PUT /dealer-kit/price-tag-requests/{id}/lines/{line_id}   REMOVED
 *    `updateRequestLine` is gone with it: the override it carried is a tag fact.
 *
 *  The tag sheet document keys its placements by `request_tag_id` (see
 *  `PlacedTag`), and the print payload's `resolvedData` is keyed the same way.
 *  The S3 migration rewrote every saved doc in place.
 * ===========================================================================
 *
 * ===========================================================================
 * LINE-LEVEL PROMOTION (PLAN-price-tag-line-promo-combo-subject.md D1/D5, S7/S11)
 * ===========================================================================
 * D5: CRM staff can change a line's price basis (promotion or a hand-typed
 * price) from the detail page, same rules as the portal form (S1).
 * `lookupLinePricing` is the CRM side of the same engine the portal's own
 * route calls (S7's `line_pricing`), so a product prices identically
 * whether the salesperson or marketing is looking at it.
 *
 * ---- BACKEND CONTRACT (built) ---------------------------------------------
 *
 *  POST /dealer-kit/price-tag-requests/line-pricing   same body/response as
 *    the portal's own route (see the portal service's contract block).
 *    Gated on `dealer_kit.price_tag_requests.process`.
 *
 *  PATCH /dealer-kit/price-tag-requests/{id}/lines/{line_id}
 *    body `{ promotion_id?: string | null, manual_sell_price?: number | null }`
 *    -> the refreshed request. `dealer_kit.price_tag_requests.process`, 409
 *    on a terminal request, 404 for a malformed or cross-request line id,
 *    clears the line's tags' pin fields so a pinned design picks up the
 *    price change as the usual data-change banner.
 * ===========================================================================
 */

import { apiFetch } from '@/lib/api';
import type { LineDataChange } from '@/lib/dealer-kit/product-data-changes';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { LineTagData, TagSheetDoc } from '@/lib/dealer-kit/tag-template-types';
import type { PrintBy } from '@/lib/dealer-kit/print-collection';
import {
  designPayloadFromResponse,
  type TagSheetDesignPayload,
} from '@/lib/dealer-kit/design-payload';
import type {
  LinePricingLineInput,
  LinePricingResult,
  SellPriceBasis,
} from '@/lib/dealer-kit/line-pricing-types';

export type {
  LinePricingCandidate,
  LinePricingLineInput,
  LinePricingPromotionOption,
  LinePricingResult,
  SellPriceBasis,
} from '@/lib/dealer-kit/line-pricing-types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PriceTagLineType = 'product' | 'product_set';

/** One part under a line: what the salesperson asked to come with it (S2, D2). */
export interface PriceTagRequestLinePart {
  id: string;
  product_id: string | null;
  code: string | null;
  name: string | null;
  /** The choice group this row answers. Null on a fixed or hand-added part. */
  role: string | null;
  /** The group's options, on a row still open. Codes, never ids (AC-X-2). */
  candidates: { product_id: string; code: string; name: string }[];
  sort_order: number;
}

/**
 * One printed tag under a line (D3).
 *
 * The server's `choices` (the raw `{role: product_id}` map) is never
 * rendered and is dropped from this type (F5, D6) - `choices_display` is the
 * same answer resolved to codes, which is what the rail and the Lines tab
 * show (AC-X-2). `list_price` / `sell_price` ride along because price is a
 * TAG fact since D4 - the host plus this tag's own resolved parts - and both
 * surfaces the brief asks for render them per tag.
 */
export interface PriceTagRequestTag {
  id: string;
  sort_order: number;
  /** "1a", "1b" - line index plus a letter. Never an id. */
  label: string;
  quantity: number;
  choices_display: { role: string; code: string }[];
  /** Groups still undecided on this tag: what Split / Pick one act on.
   *  Candidates carry the id beside the code - see `TagOpenGroup`. */
  open_groups: { role: string; candidates: { product_id: string; code: string }[] }[];
  marketing_price_override: number | null;
  marketing_override_reason: string | null;
  list_price: number | null;
  sell_price: number | null;
  /** r10 S6: marked "Not printed" - still designable, skipped by arrange,
   *  the PDF and the sheet counts. Absent reads as false. */
  print_excluded?: boolean;
  /** r10 S8: set when master data moved under this tag's pin and the change
   *  was applied by itself; cleared by Dismiss. Null or absent = nothing to
   *  show. */
  data_updated_at?: string | null;
  /** r10 S8: what that auto-update changed, old -> new per field. */
  data_update_changes?: LineDataChange[] | null;
  /** r10 S8: the "Before product update" version number Roll back restores. */
  data_update_version?: number | null;
}

export interface PriceTagRequestLine {
  id: string;
  line_type: PriceTagLineType;
  product_id: string | null;
  product_set_id: string | null;
  name: string;
  code: string;
  show_promo_price: boolean;
  quantity: number;
  included_accessories: string | null;
  /** Free-text note on the line (D6). Optional: absent on a request created
   *  before r7. */
  remarks?: string | null;
  sort_order: number;
  /** Resolved list price. */
  list_price: number | null;
  /** Resolved selling price. */
  sell_price: number | null;
  /** The package under this line, in display order (S2). */
  parts: PriceTagRequestLinePart[];
  /** What the S2 package guard found at submit. Null = clean. */
  package_warning: string | null;
  /** What gets printed for this line: one tag by default, N after a split. */
  tags: PriceTagRequestTag[];
  // ---- D1/D5 (built, S7/S9): the line's own promotion / manual price.
  // Optional so a response from a server that predates the migration still
  // validates.
  promotion_id?: string | null;
  promotion_name?: string | null;
  /** `YYYY-MM-DD` once the line's promotion has ended, else null (PRICE-TAG-A3-EXPIRED). */
  promotion_expired_on?: string | null;
  manual_sell_price?: number | null;
  sell_price_basis?: SellPriceBasis | null;
}

/** Header-level price mode (D5): replaces the per-line "Promo price" switch. */
export type PriceMode = 'list' | 'selling';

/**
 * The shape `entity_attachment_service.list_attachments_for_entity` answers
 * with - the SAME shape the portal's own detail route carries (D49, S1), so
 * this and the portal's `PortalAttachment` type must never drift apart again.
 * No `id` field: `link_id` is the row identity (what a key/unlink target
 * reads), `attachment_id` is what the download route is keyed on - the two
 * are NOT interchangeable.
 */
export interface PriceTagAttachment {
  link_id: string;
  attachment_id: string;
  filename: string | null;
  size: number | null;
  url: string | null;
  content_type: string | null;
  uploaded_at: string | null;
  uploader_kind: 'user' | 'contact' | 'system' | null;
  uploaded_by_name: string | null;
  uploaded_by_role: 'contact' | 'staff' | 'unknown';
  can_unlink: boolean;
}

export interface PriceTagRequestSummary {
  id: string;
  doc_number: string;
  debtor_code: string | null;
  /** Null while the portal request is still a draft (D48a). */
  debtor_name: string | null;
  needed_by_date: string | null;
  notes: string | null;
  /** Defaults to 'list' server-side; absent on a request created before r7. */
  price_mode?: PriceMode;
  status: string;
  line_count: number;
  created_at: string;
  assigned_to_id: string | null;
  assigned_to_name: string | null;
  contact_name: string | null;
  /** Who prints (r9 D7). Null on every row created before the choice existed. */
  print_by?: PrintBy | null;
  /** Which review round the design is on (D4). Pins from an earlier round
   *  render grey: they were about a proof that has since been redrawn. */
  review_round?: number;
  /** When the office said the tags were ready to pick up (D9). */
  ready_for_collection_at?: string | null;
  collected_at?: string | null;
  collected_by_name?: string | null;
  /** True when the auto-collect sweep closed it rather than a person (D11). */
  collected_auto?: boolean;
  /** The stored product-data-change count (AC-D5), refreshed by the list
   *  route only for a row a cheap query says was touched since its last
   *  check - never resolved per row on every page load. */
  data_changed_tag_count?: number;
}

export interface PriceTagRequestDetail extends PriceTagRequestSummary {
  contact_id: string;
  lines: PriceTagRequestLine[];
  attachments?: PriceTagAttachment[];
  /** Whether a completed (READY) tag sheet PDF export exists (S10 Proof tab). */
  has_completed_export?: boolean;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BASE = '/api/v1/dealer-kit/price-tag-requests';

// ---------------------------------------------------------------------------
// API surface
// ---------------------------------------------------------------------------

export interface PriceTagRequestListParams {
  page: number;
  limit: number;
  sort?: string;
  dir?: 'asc' | 'desc';
  query?: string;
  status?: string;
}

export interface PriceTagRequestListResult {
  data: PriceTagRequestSummary[];
  pagination: { total: number; page: number; limit: number };
}

export async function listPriceTagRequests(
  params: PriceTagRequestListParams,
): Promise<PriceTagRequestListResult> {
  // Through `buildDataGridParams`, which is the one place page/limit/sort/dir/
  // query are spelled. The list and the record pager both call this with the
  // page-number shape, so it is translated here rather than in two callers.
  const usp = buildDataGridParams(
    {
      pageIndex: params.page - 1,
      pageSize: params.limit,
      sorting: params.sort
        ? [{ id: params.sort, desc: params.dir === 'desc' }]
        : [],
      searchQuery: params.query ?? '',
    },
    { status: params.status },
  );

  const response = await apiFetch(`${BASE}?${usp.toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load price tag requests'));
  }

  // The page and the true total, both counted by the server. This used to fetch
  // the WHOLE table and sort and slice it here, so every keystroke shipped every
  // request in the system and the record count under the grid was the length of
  // the array that happened to arrive.
  const body: {
    data: PriceTagRequestSummary[];
    pagination: { total: number; page: number; limit: number };
  } = await response.json();

  return {
    data: body.data ?? [],
    pagination: {
      total: body.pagination?.total ?? 0,
      page: body.pagination?.page ?? params.page,
      limit: body.pagination?.limit ?? params.limit,
    },
  };
}

export async function getPriceTagRequest(
  id: string,
): Promise<PriceTagRequestDetail | null> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}`);
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load price tag request'));
  }
  return response.json();
}

/**
 * Change the print choice from the office side (r9 D7).
 *
 * ```
 * PATCH /api/v1/dealer-kit/price-tag-requests/{id}   { print_by }
 *   200 the updated request. 409 once the request is terminal.
 * ```
 *
 */
export async function updatePriceTagPrintBy(
  id: string,
  printBy: PrintBy | null,
): Promise<void> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ print_by: printBy }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to update the request'));
  }
}

// ---------------------------------------------------------------------------
// Line pricing (D1/D4/D5, S7 - see the contract block at the top of this
// file)
// ---------------------------------------------------------------------------

/**
 * One pricing call for every line (D4, S7). Staff-audience: the CRM route
 * has no contact to check against, so it prices under `staff_viewer()`.
 *
 * `_priceMode` is not sent: the route's body has no `price_mode` field
 * (`LinePricingRequest`, S7) - `sell_price` is a real number in List mode
 * too, and the mode only decides what the FORM does with the answer.
 */
export async function lookupLinePricing(
  _priceMode: PriceMode,
  lines: LinePricingLineInput[],
): Promise<LinePricingResult[]> {
  const response = await apiFetch(`${BASE}/line-pricing`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to price these lines'));
  }
  return response.json();
}

/**
 * A line's price basis (D5, S11). Clears the line's tags' pins server-side
 * so the design carries the change through the usual data-change banner.
 *
 * ```
 * PATCH /api/v1/dealer-kit/price-tag-requests/{requestId}/lines/{lineId}
 *   { promotion_id?, manual_sell_price? }
 *   200 the refreshed request. 409 once the request is terminal.
 * ```
 */
export async function updatePriceTagLinePrice(
  requestId: string,
  lineId: string,
  patch: { promotion_id?: string | null; manual_sell_price?: number | null },
): Promise<void> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(requestId)}/lines/${encodeURIComponent(lineId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to update the price'));
  }
}

/**
 * The office has printed: the tags are on the counter (r9 D8/D9).
 *
 * ```
 * POST /api/v1/dealer-kit/price-tag-requests/{id}/transition
 *   { status: "ready_for_collection" }
 *   200 { status, ready_for_collection_at }
 *   409 unless the request is `approved` AND print_by = "office".
 * ```
 *
 */
export async function markReadyForCollection(id: string): Promise<void> {
  await transitionPriceTagRequest(id, 'ready_for_collection');
}

/**
 * Somebody took them (r9 D8/D9).
 *
 * ```
 * POST /api/v1/dealer-kit/price-tag-requests/{id}/transition
 *   { status: "collected" }
 *   200 { status, collected_at, collected_by_name }
 *   409 unless the request is `ready_for_collection`.
 * ```
 *
 */
export async function markCollected(id: string): Promise<void> {
  await transitionPriceTagRequest(id, 'collected');
}

export async function claimPriceTagRequest(
  id: string,
): Promise<PriceTagRequestDetail> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}/claim`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to claim request'));
  }
  return response.json();
}

export async function transitionPriceTagRequest(
  id: string,
  action: string,
): Promise<PriceTagRequestDetail> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}/transition`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: action }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to transition request'));
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Tag update, split and pick one (D3)
// ---------------------------------------------------------------------------

export interface PriceTagRequestTagUpdate {
  quantity?: number;
  marketing_price_override?: number | null;
  marketing_override_reason?: string | null;
  /** r10 S6: the rail's Not printed toggle. */
  print_excluded?: boolean;
}

/** PATCH one tag. Replaces the retired line-level PUT. */
export async function updateRequestTag(
  requestId: string,
  tagId: string,
  data: PriceTagRequestTagUpdate,
): Promise<PriceTagRequestTag> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(requestId)}/tags/${encodeURIComponent(tagId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to update the tag'));
  }
  return (await response.json()) as PriceTagRequestTag;
}

/** Remove one tag. 422 `LAST_TAG` when it is the line's only one (AC-S3-6). */
export async function deleteRequestTag(requestId: string, tagId: string): Promise<void> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(requestId)}/tags/${encodeURIComponent(tagId)}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to remove the tag'));
  }
}

// ---------------------------------------------------------------------------
// Tag sheet design (S4)
// ---------------------------------------------------------------------------

export async function getTagSheetDoc(
  requestId: string,
): Promise<TagSheetDoc | null> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(requestId)}/design`,
  );
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load tag sheet design'));
  }
  // `source` says whether this is the autosaved draft or the last deliberate
  // save (B1). The designer opens on either identically - what it edits is the
  // document, not its provenance - so it is read past here rather than
  // returned; the field exists so the backend contract stays legible and a
  // future "unsaved changes" cue has something to read.
  const result: {
    page_id: string;
    version: number;
    doc: TagSheetDoc | null;
    source: 'draft' | 'version';
  } = await response.json();
  return result.doc ?? null;
}

/**
 * Autosave: overwrite the request's tag sheet DRAFT. Writes no version (B1).
 *
 * `keepalive` is for the page-teardown flush only. The browser cancels a normal
 * fetch when the document goes away, which is exactly the moment the last edit
 * most needs to reach the server; `keepalive` lets it outlive the page. It is
 * NOT the default because a keepalive request body is capped at 64KB and a
 * busy tag sheet exceeds that - the fetch would reject outright, turning every
 * ordinary autosave on a large document into a failure.
 */
export async function saveTagSheetDraft(
  requestId: string,
  doc: TagSheetDoc,
  options: { keepalive?: boolean } = {},
): Promise<void> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(requestId)}/design/draft`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc }),
      keepalive: options.keepalive,
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save tag sheet design'));
  }
}

/** Manual Save: snapshot the design into a new immutable version (B1). */
export async function saveTagSheetDoc(
  requestId: string,
  doc: TagSheetDoc,
): Promise<void> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(requestId)}/design`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc }),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save tag sheet design'));
  }
}

/**
 * Display data for this request's lines, resolved by the backend.
 *
 * ```
 * POST /api/v1/dealer-kit/price-tag-requests/{id}/resolve-prices
 *   body: string[] | null   line ids, or null for every line
 *   200 [{ line_id, code, name, dimensions, spec_lines, set_members, images[],
 *          list_price, sell_price, show_promo_price, included_accessories,
 *          quantity }]
 * ```
 *
 * Prices resolve at render time through the pricing engine and are never stored
 * in the tag sheet document (ADR 0008). A marketing override on the TAG wins
 * over the resolved offer, which is why this is resolved per tag rather than
 * per product.
 *
 * One row per TAG since S3 (D3) - see the contract at the top of this file.
 */
export async function resolveRequestTags(
  requestId: string,
  tagIds?: string[],
): Promise<LineTagData[]> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(requestId)}/resolve-prices`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // TAG ids since S3. Null resolves every tag, which is what the designer
      // asks for when it opens.
      body: JSON.stringify(tagIds ?? null),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to resolve tag prices'));
  }
  return response.json();
}

/**
 * The design AND everything it needs to draw itself (r9 S1/D1-D3).
 *
 * ## Expected API contract. Full shape: `lib/dealer-kit/design-payload.ts`.
 *
 * ```
 * GET /api/v1/dealer-kit/price-tag-requests/{id}/design
 *   200 { page_id, version, source: "draft" | "version", doc, lines[],
 *         assets:  { [assetId]: signedUrl },
 *         images:  { [attachmentId]: signedUrl },
 *         fonts:   [{ name, family, url }] }
 *   404 while the request has no page yet.
 * ```
 *
 * Draft-first (B1), so the detail page shows what the designer has actually
 * drawn rather than the last deliberate save - the office reads its own
 * work-in-progress, the salesperson reads the version that was sent to them.
 *
 * ONE call: the route resolves the lines and the three media maps itself, from
 * the same resolver the PDF reads, so the section never reaches for the asset
 * library route marketing has no permission for.
 */
export async function getRequestDesignPayload(
  requestId: string,
): Promise<TagSheetDesignPayload | null> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(requestId)}/design`,
  );
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load the design'));
  }
  return designPayloadFromResponse(await response.json());
}

// ---------------------------------------------------------------------------
// Export (S5)
// ---------------------------------------------------------------------------

export interface ExportResult {
  downloadId: string;
  status: string;
  filename: string | null;
}

export async function exportTagSheet(
  requestId: string,
  sheetIds?: string[],
): Promise<ExportResult> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(requestId)}/export`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sheet_ids: sheetIds ?? null }),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to export tag sheet'));
  }
  return response.json();
}
