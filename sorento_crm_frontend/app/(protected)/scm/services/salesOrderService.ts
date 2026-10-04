/**
 * ============================================================================
 * SALES ORDERS (M1-D14) - real API bindings (Phase 2 / M1 CP2b)
 * ============================================================================
 * Mounted under `require_module_enabled_with_api_key("scm")` at
 * `/api/v1/scm/sales-orders`. Reads gate on `scm.dashboard.view`; writes gate on
 * `scm.reorder.run`. No UUIDs surfaced - SO identified by so_number in the UI, a line's
 * warehouse by its code, addressed by id in the path.
 *
 *   GET    /sales-orders            list (page/limit/sort/dir/query/status/priority/source,
                                   date_from/date_to/customer_code/outstanding/sales_agent_id,
                                   demand_class: project | retail | unclassified,
                                   transferable: yes | no | unknown)
 *   GET    /sales-orders/agents     sales-agent options for the Agent filter/select. Gated on
 *                                   `scm.dashboard.view` - the same read permission as this
 *                                   whole router - rather than the sales-agents master's own
 *                                   `master_data.sales_agents.view`, which a role like
 *                                   Purchasing does not hold.
 *   GET    /sales-orders/uoms       active unit-of-measure options for the line UoM select.
 *                                   Same `scm.dashboard.view` gate as `agents` above, for the
 *                                   same reason - not the master data `/units-of-measure
 *                                   /select`'s `master_data.units_of_measure.view`.
 *   GET    /sales-orders/{id}       single
 *   POST   /sales-orders            create (order_type, customer_code, priority,
 *                                   requested_delivery_date?, sales_agent_id?,
 *                                   lines:[{sku,qty_ordered}])
 *   PUT    /sales-orders/{id}       update (partial; lines:[{id?,sku,qty_ordered,
 *                                   warehouse_code?,required_date?,uom?}] upserts by id/SKU).
 *                                   Response is `SalesOrder` plus `planning_change_batch`
 *                                   ({id, order_count, line_count} | null) - set when the
 *                                   edit changed a project-linked line's qty/date, the SAME
 *                                   reaction an uploaded book raises
 *                                   (`outstandingImportService`'s own `planning_change_batch`).
 *   DELETE /sales-orders/{id}       hard delete (204)
 *   POST   /sales-orders/{id}/create-do   → { sales_order, do_number }
 *   POST   /sales-orders/{id}/reset-planning { rewind_book } → { so_number, planned, removed }
 * ============================================================================
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { DataGridApiResponse } from '@/components/ui/data-grid';
import type { SalesOrder, SalesOrderFormData } from '../types/scm.types';

const BASE = '/api/v1/scm/sales-orders';

export interface SalesOrderListQuery {
  pageIndex: number;
  pageSize: number;
  sortField?: string;
  sortDir?: 'asc' | 'desc';
  searchQuery?: string;
  status?: string | null;
  priority?: string | null;
  /** Where the order came from: 'inquiry' | 'upload' | 'manual'. Omit for all. */
  source?: string | null;
  /** Order date, inclusive of both ends. ISO `yyyy-mm-dd`. */
  dateFrom?: string | null;
  dateTo?: string | null;
  customerCode?: string | null;
  /** Keep only orders with quantity still owed. `false` narrows nothing. */
  outstanding?: boolean;
  salesAgentId?: string | null;
  /** The planning class: 'project' | 'retail' | 'unclassified' (demand_class IS NULL).
   *  Omit for all. */
  demandClass?: string | null;
  /** AutoCount's Transferable flag: 'yes' | 'no' | 'unknown' (never stated). Omit for all. */
  transferable?: string | null;
}

/**
 * `lines` rides through only when the caller sent it. Omitted (not `[]`, not present
 * with the key at all - `JSON.stringify` drops an `undefined` property), an update PUT
 * leaves the order's lines untouched on the BE; sending it, even unchanged, upserts every
 * line in the array (matched by `id`, or by SKU when a line carries none).
 *
 * Per line, `id` / `warehouse_code` / `required_date` / `uom` are forwarded AS GIVEN - the
 * BE reads `model_fields_set`, so a key this object never had (not `undefined` inside an
 * object that HAS the key, but the key genuinely absent) leaves that line's stored value
 * alone, while `null`/`''` clears it. `JSON.stringify` drops an `undefined`-valued key
 * entirely, which is what makes "the caller never touched this field" reach the BE as
 * "the key is absent" rather than as an explicit clear.
 */
function toWritePayload(data: SalesOrderFormData) {
  return {
    // Both spread conditionally, and for opposite reasons. The CREATE modal sends
    // `order_type` and no `demand_class`; the detail page's edit sends `demand_class` and
    // no `order_type`, because the class is what that screen renders and `order_type` is
    // NULL on 96% of this book. A key that is genuinely absent leaves the stored column
    // alone; sending `order_type: undefined` would do the same through `JSON.stringify`,
    // but only by accident of what that function drops.
    ...(data.order_type !== undefined ? { order_type: data.order_type } : {}),
    ...(data.demand_class !== undefined ? { demand_class: data.demand_class } : {}),
    customer_code: data.customer_code,
    priority: data.priority,
    ...(data.order_date !== undefined ? { order_date: data.order_date } : {}),
    requested_delivery_date: data.requested_delivery_date ?? null,
    // `null` here is an explicit "clear the agent", not "leave it alone" - the BE
    // distinguishes a field it never received from one sent as `null` via
    // `model_fields_set`, and this key is always present in the JSON body.
    sales_agent_id: data.sales_agent_id ?? null,
    lines: data.lines?.map((l) => ({
      id: l.id,
      sku: l.sku,
      qty_ordered: l.qty_ordered,
      ...(l.warehouse_code !== undefined ? { warehouse_code: l.warehouse_code } : {}),
      ...(l.required_date !== undefined ? { required_date: l.required_date } : {}),
      ...(l.uom !== undefined ? { uom: l.uom } : {}),
      ...(l.unit_price !== undefined ? { unit_price: l.unit_price } : {}),
      ...(l.discount !== undefined ? { discount: l.discount } : {}),
    })),
  };
}

export async function getSalesOrders(
  params: SalesOrderListQuery,
): Promise<DataGridApiResponse<SalesOrder>> {
  const sorting = params.sortField
    ? [{ id: params.sortField, desc: params.sortDir === 'desc' }]
    : undefined;
  const sp = buildDataGridParams(
    {
      pageIndex: params.pageIndex,
      pageSize: params.pageSize,
      sorting,
      searchQuery: params.searchQuery,
    },
    {
      status: params.status ?? undefined,
      priority: params.priority ?? undefined,
      source: params.source ?? undefined,
      date_from: params.dateFrom || undefined,
      date_to: params.dateTo || undefined,
      // The route's own param is `customer_code` (`app/api/v1/scm/sales_orders.py`), and
      // `useCustomerOptions`' option value already IS the code, never an id - a `customer_id`
      // key here would silently match nothing (found during the view-memory rollout: the
      // filter was a no-op since 8 Aug 2026, cb1abae5e0).
      customer_code: params.customerCode || undefined,
      // Only when ON. Sending `outstanding=false` would put a param on the URL that means
      // "no filter", which then rides into the detail URL and reads as an active filter.
      outstanding: params.outstanding ? 'true' : undefined,
      sales_agent_id: params.salesAgentId || undefined,
      demand_class: params.demandClass || undefined,
      transferable: params.transferable || undefined,
    },
  );
  const res = await apiFetch(`${BASE}?${sp.toString()}`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load sales orders'));
  return res.json();
}

/** One sales order with its lines. `linked_purchase_orders` is a LIST-only field. */
export async function getSalesOrder(id: string): Promise<SalesOrder> {
  const res = await apiFetch(`${BASE}/${id}`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load the sales order'));
  return res.json();
}

export async function createSalesOrder(data: SalesOrderFormData): Promise<SalesOrder> {
  const res = await apiFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toWritePayload(data)),
  });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to create sales order'));
  return res.json();
}

/** The planning-change batch a save raised, when the edit changed a project-linked line's
 *  qty/date - the SAME reaction an uploaded book raises for the identical change
 *  (`PLAN-so-book-diff-replanning.md` section 2). Same shape as
 *  `outstandingImportService`'s `OutstandingPlanningChangeBatch`. */
export interface SalesOrderPlanningChangeBatch {
  id: string;
  order_count: number;
  line_count: number;
}

/** `SalesOrder` plus the batch THIS save raised. `null` on every save that raised nothing -
 *  most of them, and every create/read (this field only ever comes back from the PUT). */
export interface SalesOrderUpdateResult extends SalesOrder {
  planning_change_batch: SalesOrderPlanningChangeBatch | null;
}

export async function updateSalesOrder(
  id: string,
  data: SalesOrderFormData,
): Promise<SalesOrderUpdateResult> {
  const res = await apiFetch(`${BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toWritePayload(data)),
  });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to update sales order'));
  return res.json();
}

export async function deleteSalesOrder(id: string): Promise<void> {
  const res = await apiFetch(`${BASE}/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to delete sales order'));
}

/** One `sales_agents` row, as `GET /sales-orders/agents` serves it. */
export interface SalesOrderAgent {
  id: string;
  sales_agent: string;
  person_label: string | null;
  location_group: string | null;
}

/**
 * Active sales agents, for the list's Agent filter and the detail page's Agent select.
 *
 * Served off THIS router (`scm.dashboard.view`) rather than the sales-agents master's own
 * `GET /master-data/sales-agents/` (`master_data.sales_agents.view`) - a role that can read
 * SCM sales orders does not necessarily hold the master-data permission too, and would
 * otherwise 403 on this select alone.
 */
export async function getSalesOrderAgents(): Promise<SalesOrderAgent[]> {
  const res = await apiFetch(`${BASE}/agents`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load sales agents'));
  return res.json();
}

/** One active `units_of_measure` row, as `GET /sales-orders/uoms` serves it. */
export interface SalesOrderUom {
  id: string;
  uom_code: string;
  uom_name: string;
}

/**
 * Active units of measure, for the detail page's line UoM select.
 *
 * Served off THIS router (`scm.dashboard.view`), same reasoning as `getSalesOrderAgents`
 * above - the master data `/units-of-measure/select` route gates on
 * `master_data.units_of_measure.view`, which a role that can edit SCM sales orders does
 * not necessarily hold.
 */
export async function getSalesOrderUoms(): Promise<SalesOrderUom[]> {
  const res = await apiFetch(`${BASE}/uoms`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load units of measure'));
  return res.json();
}

export async function createDoFromSalesOrder(
  id: string,
): Promise<{ sales_order: SalesOrder; do_number: string }> {
  const res = await apiFetch(`${BASE}/${id}/create-do`, { method: 'POST' });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to create delivery order'));
  return res.json();
}

export interface ResetPlanningResult {
  so_number: string;
  planned: boolean;
  removed: Record<string, number>;
}

/**
 * Back to never-planned: the order's inquiries, links, allocations, transfers and supply
 * decisions go; the order and its lines stay. `rewindBook` also puts the lines a
 * planning-change upload moved back to before the first upload.
 */
export async function resetSalesOrderPlanning(
  id: string,
  rewindBook = false,
): Promise<ResetPlanningResult> {
  const res = await apiFetch(`${BASE}/${id}/reset-planning`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rewind_book: rewindBook }),
  });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to reset planning'));
  return res.json();
}
