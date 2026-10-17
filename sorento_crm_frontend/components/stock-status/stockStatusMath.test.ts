/**
 * The panel's pure arithmetic (UAC AC-13, AC-15, AC-16, AC-17, AC-18). Written before
 * `stockStatusMath.ts`: a red here is the missing module.
 */
import { describe, expect, it } from 'vitest';
import {
  documentsForLocation,
  groupSalesOrders,
  horizonOptions,
  matchesLocation,
  soColumns,
  sumLocations,
} from './stockStatusMath';
import type { StockStatus, StockStatusLocation, StockStatusSalesOrder } from './stockStatus.types';

const loc = (code: string, f: Partial<StockStatusLocation> = {}): StockStatusLocation => ({
  warehouse_id: `id-${code}`,
  warehouse_code: code,
  warehouse_name: `${code} name`,
  is_active: true,
  on_hand: 0,
  so_qty: 0,
  po_qty: 0,
  spo_qty: 0,
  forecast: 0,
  ...f,
});

const so = (f: Partial<StockStatusSalesOrder>): StockStatusSalesOrder => ({
  line_id: Math.random().toString(36),
  sales_order_id: 'so-1',
  so_number: 'SO1',
  debtor_code: '300-A',
  debtor_name: 'A CO',
  project_label: null,
  salesperson: null,
  doc_date: '2026-09-01',
  due_date: null,
  qty: 1,
  covered_in_crm: false,
  warehouse_id: 'id-BRW-BB',
  ...f,
});

describe('horizonOptions', () => {
  it('offers All then this month and the next three, each ending on its last day', () => {
    const opts = horizonOptions(new Date(2026, 9, 17));
    expect(opts.map((o) => o.label)).toEqual(['All', 'Oct', 'Nov', 'Dec', 'Jan']);
    expect(opts.map((o) => o.value)).toEqual([null, '2026-10-31', '2026-11-30', '2026-12-31', '2027-01-31']);
  });
});

describe('matchesLocation', () => {
  it('matches code or name anywhere, ignoring case and spaces around the query', () => {
    expect(matchesLocation(loc('MWH-IB'), ' mwh ')).toBe(true);
    expect(matchesLocation(loc('BRW', { warehouse_name: 'Brickfields' }), 'brick')).toBe(true);
    expect(matchesLocation(loc('BRW-BB'), 'mwh')).toBe(false);
    expect(matchesLocation(loc('BRW-BB'), '')).toBe(true);
  });
});

describe('sumLocations', () => {
  it('adds every figure of the given rows', () => {
    const t = sumLocations([
      loc('A', { on_hand: 1, so_qty: 2, po_qty: 3, spo_qty: 4, forecast: 6 }),
      loc('B', { on_hand: 10, so_qty: 20, po_qty: 30, spo_qty: 40, forecast: 60 }),
    ]);
    expect(t).toEqual({ on_hand: 11, so_qty: 22, po_qty: 33, spo_qty: 44, forecast: 66 });
  });
});

describe('documentsForLocation', () => {
  it('merges SO / PO / SPO of one location, SO negative, by due date with undated last, each with its link', () => {
    const data = {
      sales_orders: [
        so({ so_number: 'SO-LATE', sales_order_id: 's1', due_date: '2026-12-01', qty: 5 }),
        so({ so_number: 'SO-NODATE', sales_order_id: 's2', due_date: null, qty: 7 }),
        so({ so_number: 'SO-ELSEWHERE', warehouse_id: 'id-OTHER', due_date: '2026-01-01' }),
      ],
      purchase_orders: [
        { line_id: 'p', purchase_order_id: 'po1', po_number: 'PO1', supplier_code: '400-C030', supplier_name: 'F',
          issue_date: '2026-08-01', expected_date: '2026-11-01', qty: 9, warehouse_id: 'id-BRW-BB' },
      ],
      spos: [
        { allocation_id: 'x', spo_number: 'SPO-2026/10-0024', spo_line_number: 1, supplier_code: '400-C029',
          supplier_name: 'J', issue_date: '2026-10-06', arrival_date: '2026-10-17', container_number: 'SEKU1',
          qty: 50, warehouse_id: 'id-BRW-BB' },
      ],
    } as unknown as StockStatus;
    const docs = documentsForLocation(data, 'id-BRW-BB');
    expect(docs.map((d) => [d.kind, d.number, d.qty])).toEqual([
      ['SPO', 'SPO-2026/10-0024', 50],
      ['PO', 'PO1', 9],
      ['SO', 'SO-LATE', -5],
      ['SO', 'SO-NODATE', -7],
    ]);
    expect(docs.map((d) => d.href)).toEqual([
      '/procurement-management/spo-allocations/SPO-2026%2F10-0024',
      '/scm/purchase-orders/po1',
      '/scm/sales-orders/s1',
      '/scm/sales-orders/s2',
    ]);
  });
});

describe('groupSalesOrders', () => {
  const rows = [
    so({ project_label: 'KLCC', debtor_name: 'BUYER A', salesperson: 'AG1', warehouse_id: 'w1', qty: 4, due_date: '2026-11-15' }),
    so({ project_label: 'KLCC', debtor_name: 'BUYER A', salesperson: 'AG1', warehouse_id: 'w2', qty: 6, due_date: '2026-10-02' }),
    so({ project_label: null, debtor_name: 'DEALER C', debtor_code: '300-D', salesperson: null, warehouse_id: 'w1', qty: 3, due_date: null }),
  ];

  it('groups by project, falling back to the customer, largest first, per warehouse', () => {
    const g = groupSalesOrders(rows, 'project');
    expect(g.map((x) => [x.label, x.total, x.byWarehouse])).toEqual([
      ['KLCC', 10, { w1: 4, w2: 6 }],
      ['DEALER C', 3, { w1: 3 }],
    ]);
  });

  it('groups by customer, salesperson (with a No salesperson row) and delivery month (in date order, No date last)', () => {
    expect(groupSalesOrders(rows, 'customer').map((x) => [x.label, x.secondary, x.total])).toEqual([
      ['BUYER A', '300-A', 10],
      ['DEALER C', '300-D', 3],
    ]);
    expect(groupSalesOrders(rows, 'salesperson').map((x) => [x.label, x.total])).toEqual([
      ['AG1', 10],
      ['No salesperson', 3],
    ]);
    expect(groupSalesOrders(rows, 'month').map((x) => [x.label, x.total])).toEqual([
      ['Oct 2026', 6],
      ['Nov 2026', 4],
      ['No date', 3],
    ]);
  });
});

describe('soColumns', () => {
  it('keeps locations carrying SO, narrowed by the search', () => {
    const cols = soColumns([loc('BRW-IB', { so_qty: 5 }), loc('BRW-IR', { so_qty: 1 }), loc('DC1-BB')], 'brw-i');
    expect(cols.map((c) => c.warehouse_code)).toEqual(['BRW-IB', 'BRW-IR']);
    expect(soColumns([loc('BRW-IB', { so_qty: 5 }), loc('MWH')], '').map((c) => c.warehouse_code)).toEqual(['BRW-IB']);
  });
});
