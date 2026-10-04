/* -------------------------------------------------------------------------------------
 * Stock visibility policy - PLAN-stock-visibility-policy, slice S4 (Phase 2).
 *
 * Which warehouses a chatbot contact may be told about, and in which of the three
 * answer shapes. One row per tier: contact override > contact access type > global
 * default, resolved by the BACKEND inside `GET /inventory/stock/balance` (the preflight
 * endpoint below is a convenience, never the mechanism - same doctrine as
 * `agent_field_access`).
 *
 * ===================================================================================
 * API CONTRACT (built in S2, wired here in S4)
 * ===================================================================================
 *
 * Permission: reads `inventory.stock.view`, writes `inventory.stock.edit`.
 * Every route lives under the `inventory` module guard.
 *
 * --- The policy shape every route returns -------------------------------------------
 *
 *   Policy = {
 *     "mode": "detailed" | "compact" | "availability",
 *     "warehouses": [{ "id": "<uuid>", "code": "BRW", "name": "Main Warehouse" }] | null,
 *          // null  = every active warehouse (the stored `warehouse_ids` is NULL)
 *          // []    = no warehouse at all, so the contact is told about no stock
 *          // Resolved rows, not bare ids: the UI renders `CODE - name` and never a UUID.
 *     "excluded_warehouses": [{ "id": "<uuid>", "code": "BRW", "name": "Main Warehouse" }] | null,
 *          // `warehouses`'s sibling (PLAN-stock-visibility-exclude-locations): null = no
 *          // exclusion, [] = every active warehouse (the OPPOSITE reading of [] above), a
 *          // list = every active warehouse except these - one created later stays visible.
 *     "hide_zero_locations": false,
 *          // Withhold the locations holding NONE of the product: the row under
 *          // `detailed`, the location line under `compact` (the total is unchanged),
 *          // nothing under `availability`, which has no line to withhold. A NEGATIVE
 *          // quantity is never hidden - it is an anomaly, not an absence.
 *     "source": "contact" | "access_type" | "default",
 *     "source_label": "Dealer" | null
 *          // The access type NAME when source == "access_type", else null. A NAME, not
 *          // the code, because the badge reads "Access type: Dealer".
 *   }
 *
 *   PolicyResponse = {
 *     "effective": Policy,        // what the chatbot applies to this tier today
 *     "override": Policy | null   // the row stored AT THIS TIER; null = the tier inherits
 *   }
 *
 *   For the default tier `override` is always present and equals `effective` - the
 *   default row always exists (seeded inert: detailed / all warehouses).
 *
 * --- Routes --------------------------------------------------------------------------
 *
 *   GET    /api/v1/inventory/stock-visibility/effective?contact_id=&space_id=
 *            -> Policy      (external principal + API key; the n8n preflight convenience)
 *
 *   GET    /api/v1/inventory/stock-visibility/contacts/{contact_id}    -> PolicyResponse
 *   PUT    /api/v1/inventory/stock-visibility/contacts/{contact_id}    -> PolicyResponse
 *            body: { "mode": "compact", "warehouse_ids": ["<uuid>", ...] | null,
 *                     "excluded_warehouse_ids": ["<uuid>", ...] | null,
 *                     "hide_zero_locations": false }
 *            Upsert. `warehouse_ids` (the include list) and `excluded_warehouse_ids`
 *            (its sibling: "every active warehouse except these") are BOTH REQUIRED
 *            (nullable, not defaulted) and replaced wholesale, never merged: a PUT
 *            replaces the whole row, so an omitted key would silently widen the
 *            policy. The two are never both non-null on one Save - 422 "Pick
 *            locations to include or to exclude, not both."
 *            `hide_zero_locations` defaults to false server-side; this app always
 *            sends it, because a PUT replaces the row and the card holds its value.
 *   DELETE /api/v1/inventory/stock-visibility/contacts/{contact_id}    -> PolicyResponse
 *            Hard delete of the override row. The body carries the tier the contact
 *            falls back to, so the UI re-renders the inherited policy without a refetch.
 *
 *            All three take an OPTIONAL `?space_id=` - only needed when `contact_id`
 *            is a Respond.io id that exists in more than one workspace. This app holds
 *            the internal `respond_contacts.id`, so it does not send one.
 *
 *   GET|PUT|DELETE /api/v1/inventory/stock-visibility/access-types/{code}
 *            Same three shapes, keyed by `contact_access_types.code`.
 *            PUT with an unknown code -> 404.
 *
 *   GET|PUT /api/v1/inventory/stock-visibility/default
 *            No DELETE: the default row is the floor of the resolution chain, so the UI
 *            offers no Remove on that surface.
 *
 *   422 on: `mode` outside the three; a `warehouse_ids` entry that is not an existing
 *   warehouse. `detail` carries the message the UI toasts via `extractApiError`.
 *
 * --- Warehouse pickers (existing route, one new filter) ------------------------------
 *
 *   GET /api/v1/inventory/warehouses?page=1&limit=200&query=&is_active=true
 *            Already exists. Server search, which is what the Locations picker uses -
 *            a client-side capped list is not allowed for a master this size. 200, not
 *            the earlier 50: 77 active warehouses overflowed the old page.
 *
 *   GET /api/v1/inventory/warehouses?page=1&limit=200&segment=dealer&is_active=true
 *            The "Dealer pool" preset. `segment` is a query filter S2 added to the
 *            existing route (the column `warehouses.segment` and `WarehouseResponse.segment`
 *            were already there). Documented here because the preset is the only caller
 *            and the PLAN's API section did not list it.
 * ----------------------------------------------------------------------------------- */

import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';

/** The three answer shapes. Ordered loosest -> most restrictive; the backend merge relies on it. */
export type StockVisibilityMode = 'detailed' | 'compact' | 'availability';

export const STOCK_VISIBILITY_MODE_ORDER: StockVisibilityMode[] = [
  'detailed',
  'compact',
  'availability',
];

export const STOCK_VISIBILITY_MODE_LABELS: Record<StockVisibilityMode, string> = {
  detailed: 'Detailed',
  compact: 'Compact',
  availability: 'Availability only',
};

export type StockVisibilitySource = 'contact' | 'access_type' | 'default';

/** Resolved warehouse, so the UI can render `CODE - name` and never a UUID. */
export interface StockVisibilityWarehouse {
  id: string;
  code: string;
  name: string | null;
  /** The location's company, set when the read spans every granted company. */
  company_name?: string | null;
}

export interface StockVisibilityPolicy {
  mode: StockVisibilityMode;
  /** null = every active warehouse; [] = none at all. */
  warehouses: StockVisibilityWarehouse[] | null;
  /**
   * `warehouses`'s sibling: null = no exclusion, [] = every active warehouse
   * (the opposite of what [] means on `warehouses`), a list = every active
   * warehouse except these.
   */
  excluded_warehouses: StockVisibilityWarehouse[] | null;
  /** Withhold the locations holding none of the product. Negatives stay visible. */
  hide_zero_locations: boolean;
  source: StockVisibilitySource;
  /** Access type NAME when `source` is `access_type`, else null. */
  source_label: string | null;
}

export interface StockVisibilityPolicyResponse {
  effective: StockVisibilityPolicy;
  /** The row stored at the requested tier. null = this tier inherits. */
  override: StockVisibilityPolicy | null;
}

export interface StockVisibilityInput {
  mode: StockVisibilityMode;
  /** null = every active warehouse; [] = none. Replaces the stored list wholesale. */
  warehouse_ids: string[] | null;
  /**
   * `warehouse_ids`'s sibling: null = no exclusion, [] = every active warehouse,
   * a list = every active warehouse except these. REQUIRED, same reasoning as
   * `warehouse_ids` - the two are never both non-null on one Save.
   */
  excluded_warehouse_ids: string[] | null;
  /**
   * Withhold the locations holding none of the product. Optional on the wire (the
   * backend defaults it to false), required here: a PUT replaces the whole row, so
   * the card sends the value it is showing rather than leaving it to a default.
   */
  hide_zero_locations: boolean;
}

/**
 * Which tier a surface edits. One component serves the contact page, the access type
 * admin and the settings default, so the tier is a prop rather than three copies.
 *
 * The scope carries only what the ROUTE needs. The access-type tier a contact inherits
 * is resolved by the backend from `contact_id`, so the card never has to be told which
 * access types the contact holds.
 */
export type StockVisibilityScope =
  | { kind: 'contact'; contactId: string }
  | { kind: 'access_type'; accessTypeCode: string }
  | { kind: 'default' };

/** react-query key, and the route segment each scope maps to. */
export function stockVisibilityScopeKey(scope: StockVisibilityScope): string[] {
  switch (scope.kind) {
    case 'contact':
      return ['stock-visibility', 'contact', scope.contactId];
    case 'access_type':
      return ['stock-visibility', 'access-type', scope.accessTypeCode];
    default:
      return ['stock-visibility', 'default'];
  }
}

/** The path the three verbs call. Kept next to the key so the two cannot drift. */
export function stockVisibilityScopePath(scope: StockVisibilityScope): string {
  switch (scope.kind) {
    case 'contact':
      return `/api/v1/inventory/stock-visibility/contacts/${encodeURIComponent(scope.contactId)}`;
    case 'access_type':
      return `/api/v1/inventory/stock-visibility/access-types/${encodeURIComponent(
        scope.accessTypeCode,
      )}`;
    default:
      return '/api/v1/inventory/stock-visibility/default';
  }
}

export async function getStockVisibility(
  scope: StockVisibilityScope,
): Promise<StockVisibilityPolicyResponse> {
  const response = await apiFetch(stockVisibilityScopePath(scope));
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load stock visibility'));
  }
  return response.json();
}

export async function saveStockVisibility(
  scope: StockVisibilityScope,
  input: StockVisibilityInput,
): Promise<StockVisibilityPolicyResponse> {
  const response = await apiFetch(stockVisibilityScopePath(scope), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save stock visibility'));
  }
  return response.json();
}

export async function deleteStockVisibility(
  scope: StockVisibilityScope,
): Promise<StockVisibilityPolicyResponse> {
  // The default row is the floor of the resolution chain and has no DELETE route; the
  // card offers no Remove there, and this guard keeps a stray caller off a 405.
  if (scope.kind === 'default') {
    throw new Error('The default stock visibility policy cannot be removed');
  }
  const response = await apiFetch(stockVisibilityScopePath(scope), { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to remove stock visibility'));
  }
  return response.json();
}

/** One page of `WarehouseResponse` rows, narrowed to what the pickers render. */
interface WarehouseListRow {
  id: string;
  warehouse_code: string;
  warehouse_name?: string | null;
  company_name?: string | null;
}

function toWarehouseRef(row: WarehouseListRow): StockVisibilityWarehouse {
  return {
    id: row.id,
    code: row.warehouse_code,
    name: row.warehouse_name ?? null,
    company_name: row.company_name ?? undefined,
  };
}

async function fetchWarehouses(
  params: URLSearchParams,
  fallback: string,
): Promise<StockVisibilityWarehouse[]> {
  const response = await apiFetch(`/api/v1/inventory/warehouses?${params.toString()}`);
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  const body = (await response.json()) as { data?: WarehouseListRow[] };
  return (body.data ?? []).map(toWarehouseRef);
}

/** Locations picker: server search, one page at a time - never a capped client-side list. */
export async function searchStockVisibilityWarehouses(
  query: string,
): Promise<StockVisibilityWarehouse[]> {
  return fetchWarehouses(
    buildDataGridParams(
      { pageIndex: 0, pageSize: 200, searchQuery: query, sorting: [{ id: 'warehouse_code', desc: false }] },
      { is_active: true, company_scope: 'grants' },
    ),
    'Failed to load locations',
  );
}

/** "Dealer pool" preset: every active warehouse whose `segment` is `dealer`. */
export async function getDealerPoolWarehouses(): Promise<StockVisibilityWarehouse[]> {
  return fetchWarehouses(
    buildDataGridParams(
      { pageIndex: 0, pageSize: 200, sorting: [{ id: 'warehouse_code', desc: false }] },
      { segment: 'dealer', is_active: true, company_scope: 'grants' },
    ),
    'Failed to load the dealer pool',
  );
}
