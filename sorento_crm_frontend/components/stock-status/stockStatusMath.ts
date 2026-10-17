/**
 * The stock status panel's pure arithmetic: horizon months, location search, subtotals, the
 * documents under a location and the SO totals grouping (plan R5, R7, R8, R11).
 */
import { spoDetailHref } from '@/lib/spo-detail';
import type {
  StockStatus,
  StockStatusLocation,
  StockStatusSalesOrder,
} from './stockStatus.types';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export interface HorizonOption {
  key: string;
  label: string;
  /** Month end as YYYY-MM-DD, or null for "All". */
  value: string | null;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** "SO due by": All, this month and the next three, each meaning its last day. */
export function horizonOptions(today: Date = new Date()): HorizonOption[] {
  const out: HorizonOption[] = [{ key: 'all', label: 'All', value: null }];
  for (let i = 0; i < 4; i += 1) {
    const end = new Date(today.getFullYear(), today.getMonth() + i + 1, 0);
    const value = `${end.getFullYear()}-${pad(end.getMonth() + 1)}-${pad(end.getDate())}`;
    out.push({ key: value, label: MONTHS[end.getMonth()], value });
  }
  return out;
}

/** The location search: code or name contains the query, case and outer spaces ignored. */
export function matchesLocation(location: StockStatusLocation, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    location.warehouse_code.toLowerCase().includes(q) ||
    (location.warehouse_name ?? '').toLowerCase().includes(q)
  );
}

export interface LocationSums {
  on_hand: number;
  so_qty: number;
  po_qty: number;
  spo_qty: number;
  forecast: number;
}

export function sumLocations(locations: StockStatusLocation[]): LocationSums {
  return locations.reduce<LocationSums>(
    (t, l) => ({
      on_hand: t.on_hand + l.on_hand,
      so_qty: t.so_qty + l.so_qty,
      po_qty: t.po_qty + l.po_qty,
      spo_qty: t.spo_qty + l.spo_qty,
      forecast: t.forecast + l.forecast,
    }),
    { on_hand: 0, so_qty: 0, po_qty: 0, spo_qty: 0, forecast: 0 },
  );
}

export type DocumentKind = 'SO' | 'PO' | 'SPO';

export interface LocationDocument {
  key: string;
  kind: DocumentKind;
  number: string;
  /** The document's CRM page: every number opens its document (plan R7). */
  href: string;
  account: string | null;
  name: string | null;
  project: string | null;
  docDate: string | null;
  due: string | null;
  loading: string | null;
  /** Signed the AutoCount way: SO negative, supply positive. */
  qty: number;
  lineId: string | null;
  coveredInCrm: boolean;
}

/** Every counted document behind one location, earliest due first, undated last. */
export function documentsForLocation(
  data: Pick<StockStatus, 'sales_orders' | 'purchase_orders' | 'spos'>,
  warehouseId: string,
): LocationDocument[] {
  const docs: LocationDocument[] = [];
  for (const s of data.sales_orders) {
    if (s.warehouse_id !== warehouseId) continue;
    docs.push({
      key: `so-${s.line_id}`,
      kind: 'SO',
      number: s.so_number,
      href: `/scm/sales-orders/${s.sales_order_id}`,
      account: s.debtor_code,
      name: s.debtor_name,
      project: s.project_label,
      docDate: s.doc_date,
      due: s.due_date,
      loading: null,
      qty: -s.qty,
      lineId: s.line_id,
      coveredInCrm: s.covered_in_crm,
    });
  }
  for (const p of data.purchase_orders) {
    if (p.warehouse_id !== warehouseId) continue;
    docs.push({
      key: `po-${p.line_id}`,
      kind: 'PO',
      number: p.po_number,
      href: `/scm/purchase-orders/${p.purchase_order_id}`,
      account: p.supplier_code,
      name: p.supplier_name,
      project: null,
      docDate: p.issue_date,
      due: p.expected_date,
      loading: null,
      qty: p.qty,
      lineId: null,
      coveredInCrm: false,
    });
  }
  for (const x of data.spos) {
    if (x.warehouse_id !== warehouseId) continue;
    docs.push({
      key: `spo-${x.allocation_id}`,
      kind: 'SPO',
      number: x.spo_number,
      href: spoDetailHref(x.spo_number),
      account: x.supplier_code,
      name: x.supplier_name,
      project: null,
      docDate: x.issue_date,
      due: x.arrival_date,
      loading: x.container_number,
      qty: x.qty,
      lineId: null,
      coveredInCrm: false,
    });
  }
  return docs.sort((a, b) => {
    if (a.due === b.due) return a.number.localeCompare(b.number);
    if (a.due === null) return 1;
    if (b.due === null) return -1;
    return a.due.localeCompare(b.due);
  });
}

export type GroupBy = 'project' | 'customer' | 'salesperson' | 'month';

export const GROUP_BY_OPTIONS: { value: GroupBy; label: string }[] = [
  { value: 'project', label: 'Project' },
  { value: 'customer', label: 'Customer' },
  { value: 'salesperson', label: 'Salesperson' },
  { value: 'month', label: 'Delivery month' },
];

export interface SoGroup {
  key: string;
  label: string;
  secondary: string | null;
  byWarehouse: Record<string, number>;
  total: number;
  /** Delivery month only: the YYYY-MM it sorts by, '9999-99' for "No date". */
  sortKey: string;
  rows: StockStatusSalesOrder[];
}

function groupKey(row: StockStatusSalesOrder, by: GroupBy): { key: string; label: string; secondary: string | null; sortKey: string } {
  const customer = row.debtor_name || row.debtor_code || 'No customer';
  switch (by) {
    case 'project': {
      const label = row.project_label || customer;
      return { key: `${label}|${customer}`, label, secondary: row.project_label ? customer : null, sortKey: '' };
    }
    case 'customer':
      return { key: row.debtor_code || customer, label: customer, secondary: row.debtor_code, sortKey: '' };
    case 'salesperson': {
      const label = row.salesperson || 'No salesperson';
      return { key: label, label, secondary: null, sortKey: '' };
    }
    case 'month': {
      if (!row.due_date) return { key: 'none', label: 'No date', secondary: null, sortKey: '9999-99' };
      const ym = row.due_date.slice(0, 7);
      const label = `${MONTHS[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}`;
      return { key: ym, label, secondary: null, sortKey: ym };
    }
  }
}

/** SO totals for one angle: one row per value, quantity per warehouse, largest first. */
export function groupSalesOrders(rows: StockStatusSalesOrder[], by: GroupBy): SoGroup[] {
  const groups = new Map<string, SoGroup>();
  for (const row of rows) {
    const k = groupKey(row, by);
    const g = groups.get(k.key) ?? {
      key: k.key,
      label: k.label,
      secondary: k.secondary,
      byWarehouse: {},
      total: 0,
      sortKey: k.sortKey,
      rows: [],
    };
    g.byWarehouse[row.warehouse_id] = (g.byWarehouse[row.warehouse_id] ?? 0) + row.qty;
    g.total += row.qty;
    g.rows.push(row);
    groups.set(k.key, g);
  }
  const list = Array.from(groups.values());
  if (by === 'month') return list.sort((a, b) => a.sortKey.localeCompare(b.sortKey));
  return list.sort((a, b) => b.total - a.total || a.label.localeCompare(b.label));
}

/** The SO totals columns: locations carrying SO, narrowed by the location search. */
export function soColumns(locations: StockStatusLocation[], query: string): StockStatusLocation[] {
  return locations.filter((l) => l.so_qty > 0 && matchesLocation(l, query));
}
