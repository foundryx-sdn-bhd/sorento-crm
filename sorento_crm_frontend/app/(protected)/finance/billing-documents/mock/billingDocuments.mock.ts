/**
 * Phase 1 fixture for the billing documents screens. Masked customers only. Replaced by the
 * real API in Phase 2 (flip USE_MOCK in services/billingDocumentService.ts), then deleted.
 */
import type {
  BillingDocumentDetail,
  BillingDocumentLine,
  BillingDocumentListParams,
  BillingDocumentListResponse,
  BillingDocumentListRow,
  BillingDocumentStatus,
  BillingDocumentType,
  BillingSelectOption,
  CustomerOutstanding,
} from '../types/billingDocument.types';

interface MockCustomer {
  id: string;
  code: string;
  name: string;
  agentCode: string;
  ar: number | null;
  arAsOf: string | null;
}

const AS_OF = '2026-10-16T09:00:00';

export const MOCK_AGENTS: { code: string; name: string }[] = [
  { code: 'AG01', name: 'Agent One' },
  { code: 'AG02', name: 'Agent Two' },
];

export const MOCK_CUSTOMERS: MockCustomer[] = [
  { id: 'cust-a', code: '300-A001', name: 'Customer A Sdn Bhd', agentCode: 'AG01', ar: null, arAsOf: AS_OF },
  { id: 'cust-b', code: '300-B002', name: 'Customer B Enterprise', agentCode: 'AG02', ar: null, arAsOf: AS_OF },
  { id: 'cust-c', code: '300-C003', name: 'Customer C Cash Debtor', agentCode: 'AG01', ar: null, arAsOf: null },
];

const SALES_ORDERS: Record<string, { id: string; doc_no: string }> = {
  'SO-1001': { id: 'so-1001', doc_no: 'SO-1001' },
  'SO-1002': { id: 'so-1002', doc_no: 'SO-1002' },
  'SO-1003': { id: 'so-1003', doc_no: 'SO-1003' },
};

/** item, name, description, qty, price, discount, tax code, tax rate, from type, from no. */
type L = [
  string | null,
  string | null,
  string | null,
  number,
  number,
  number,
  string | null,
  number,
  string | null,
  string | null,
];

interface Seed {
  id: string;
  type: BillingDocumentType;
  no: string;
  date: string;
  cust: string;
  status?: BillingDocumentStatus;
  currency?: string;
  rate?: number;
  outstanding: number | null;
  due: string | null;
  ref?: string | null;
  description?: string | null;
  against?: string;
  lines: L[];
}

const r2 = (n: number) => Math.round(n * 100) / 100;
const SR = 'SR-6';

const SEEDS: Seed[] = [
  {
    id: 'bd-01', type: 'invoice', no: 'IV-000101', date: '2026-08-28', cust: 'cust-a',
    outstanding: 1060, due: '2026-09-27', ref: 'PO-A-5521',
    lines: [
      ['TILE-6060-GR', 'Porcelain Tile 600x600 Grey', null, 100, 8, 0, SR, 6, 'DO', 'DO-260801'],
      ['TILE-3060-WH', 'Wall Tile 300x600 White', null, 50, 4, 0, SR, 6, 'DO', 'DO-260801'],
    ],
  },
  {
    id: 'bd-02', type: 'invoice', no: 'IV-000102', date: '2026-10-05', cust: 'cust-a',
    outstanding: 2320, due: '2026-11-04', description: 'Showroom restock',
    lines: [
      ['SINK-SS-01', 'Stainless Steel Sink 1 Bowl', null, 10, 150, 0, SR, 6, 'SO', 'SO-1002'],
      ['TAP-CHR-02', 'Basin Tap Chrome', null, 20, 50, 0, SR, 6, 'SO', 'SO-1002'],
    ],
  },
  {
    id: 'bd-03', type: 'invoice', no: 'IV-000103', date: '2026-07-21', cust: 'cust-b',
    outstanding: 0, due: '2026-08-20',
    lines: [['PAINT-5L-WH', 'Emulsion Paint 5L White', null, 40, 45, 0, SR, 6, 'DO', 'DO-260702']],
  },
  {
    id: 'bd-04', type: 'invoice', no: 'IV-000104', date: '2026-10-10', cust: 'cust-b',
    outstanding: 5724, due: '2026-11-30',
    lines: [
      ['PAINT-5L-WH', 'Emulsion Paint 5L White', null, 60, 45, 100, SR, 6, 'DO', 'DO-261001'],
      ['PAINT-1L-BL', 'Emulsion Paint 1L Blue', null, 100, 28, 0, SR, 6, 'DO', 'DO-261002'],
    ],
  },
  {
    id: 'bd-05', type: 'cash_sale', no: 'CS-000050', date: '2026-10-12', cust: 'cust-c',
    outstanding: 0, due: null, description: 'Walk-in',
    lines: [['TAP-CHR-02', 'Basin Tap Chrome', null, 2, 50, 0, SR, 6, null, null]],
  },
  {
    id: 'bd-06', type: 'credit_note', no: 'CN-000010', date: '2026-10-08', cust: 'cust-a',
    outstanding: -200, due: null, against: 'IV-000102', description: 'Damaged goods',
    lines: [['SINK-SS-01', 'Stainless Steel Sink 1 Bowl', null, 2, 150, 0, SR, 6, 'IV', 'IV-000102']],
  },
  {
    id: 'bd-07', type: 'debit_note', no: 'DN-000005', date: '2026-09-20', cust: 'cust-b',
    outstanding: 106, due: '2026-10-05', against: 'IV-000103', description: 'Late delivery charge',
    lines: [[null, null, 'Handling charge', 1, 100, 0, SR, 6, null, null]],
  },
  {
    id: 'bd-08', type: 'invoice', no: 'IV-000105', date: '2026-09-02', cust: 'cust-a', status: 'cancelled',
    outstanding: null, due: null,
    lines: [['TILE-6060-GR', 'Porcelain Tile 600x600 Grey', null, 20, 8, 0, SR, 6, 'SO', 'SO-1001']],
  },
  {
    id: 'bd-09', type: 'invoice', no: 'IV-000106', date: '2026-10-01', cust: 'cust-b',
    currency: 'USD', rate: 4.45, outstanding: 1000, due: '2026-11-10',
    lines: [['EXP-PAINT-20L', 'Export Paint 20L', null, 10, 100, 0, null, 0, null, null]],
  },
  {
    id: 'bd-10', type: 'invoice', no: 'IV-000107', date: '2026-10-15', cust: 'cust-a',
    outstanding: null, due: null,
    lines: [['TAP-CHR-02', 'Basin Tap Chrome', null, 12, 50, 0, SR, 6, 'SO', 'SO-1003']],
  },
  {
    id: 'bd-11', type: 'invoice', no: 'IV-000108', date: '2026-06-18', cust: 'cust-c',
    outstanding: 0, due: '2026-06-18',
    lines: [['TILE-3060-WH', 'Wall Tile 300x600 White', null, 30, 4, 0, SR, 6, null, null]],
  },
  {
    id: 'bd-12', type: 'invoice', no: 'IV-000109', date: '2026-06-30', cust: 'cust-b',
    outstanding: 3180, due: '2026-07-30',
    lines: [['PAINT-5L-WH', 'Emulsion Paint 5L White', null, 40, 75, 0, SR, 6, 'SO', 'SO-1001']],
  },
  {
    id: 'bd-13', type: 'credit_note', no: 'CN-000011', date: '2026-09-25', cust: 'cust-b',
    outstanding: -318, due: null, against: 'IV-LEGACY-0007',
    lines: [['PAINT-5L-WH', 'Emulsion Paint 5L White', null, 4, 75, 0, SR, 6, null, null]],
  },
  {
    id: 'bd-14', type: 'invoice', no: 'IV-000110', date: '2026-10-16', cust: 'cust-a',
    outstanding: 4240, due: '2026-12-15',
    lines: [['TILE-6060-GR', 'Porcelain Tile 600x600 Grey', null, 500, 8, 0, SR, 6, 'DO', 'DO-261601']],
  },
  {
    id: 'bd-15', type: 'cash_sale', no: 'CS-000051', date: '2026-10-14', cust: 'cust-c',
    outstanding: 0, due: null,
    lines: [['SINK-SS-01', 'Stainless Steel Sink 1 Bowl', null, 1, 150, 0, SR, 6, null, null]],
  },
];

function buildLines(seed: Seed): BillingDocumentLine[] {
  return seed.lines.map((l, i) => {
    const [item, name, desc, qty, price, discount, taxCode, taxRate, fromType, fromNo] = l;
    const net = r2(qty * price - discount);
    const tax = r2((net * taxRate) / 100);
    return {
      line_no: i + 1,
      item_code: item,
      product_name: name,
      description: desc,
      uom: item ? 'UNIT' : null,
      quantity: qty,
      unit_price: price,
      discount_amount: discount,
      net_amount: net,
      tax_code: taxCode,
      tax_rate: taxCode ? taxRate : null,
      tax_amount: tax,
      line_total: r2(net + tax),
      from_doc_type: fromType,
      from_doc_no: fromNo,
      // A line transferred from an SO or from a DO whose SO is known.
      sales_order:
        fromType === 'SO' && fromNo
          ? (SALES_ORDERS[fromNo] ?? null)
          : fromType === 'DO' && fromNo
            ? (SALES_ORDERS[DO_TO_SO[fromNo]] ?? null)
            : null,
    };
  });
}

const DO_TO_SO: Record<string, string> = {
  'DO-260801': 'SO-1001',
  'DO-261001': 'SO-1002',
};

function customerOf(id: string): MockCustomer {
  return MOCK_CUSTOMERS.find((c) => c.id === id) as MockCustomer;
}

function toRow(seed: Seed): BillingDocumentListRow {
  const lines = buildLines(seed);
  const net = r2(lines.reduce((s, l) => s + l.net_amount, 0));
  const tax = r2(lines.reduce((s, l) => s + l.tax_amount, 0));
  const c = customerOf(seed.cust);
  const agent = MOCK_AGENTS.find((a) => a.code === c.agentCode);
  return {
    id: seed.id,
    document_type: seed.type,
    doc_no: seed.no,
    doc_date: seed.date,
    customer_id: c.id,
    customer_name: c.name,
    debtor_code: c.code,
    agent_code: agent?.code ?? null,
    agent_name: agent?.name ?? null,
    currency_code: seed.currency ?? 'MYR',
    net_total: net,
    tax_total: tax,
    total: r2(net + tax),
    status: seed.status ?? 'posted',
    outstanding: seed.outstanding,
    due_date: seed.due,
    outstanding_as_of: seed.outstanding === null ? null : AS_OF,
  };
}

const ROWS: BillingDocumentListRow[] = SEEDS.map(toRow);

// Customer totals: the sum of the synced open items (a customer with none synced reads null).
for (const c of MOCK_CUSTOMERS) {
  if (!c.arAsOf) continue;
  c.ar = r2(
    ROWS.filter((r) => r.customer_id === c.id && r.outstanding !== null).reduce(
      (s, r) => s + (r.outstanding as number),
      0,
    ),
  );
}

export function mockList(params: BillingDocumentListParams): BillingDocumentListResponse {
  const q = params.searchQuery.trim().toLowerCase();
  let rows = ROWS.filter((r) => {
    if (params.documentTypes?.length && !params.documentTypes.includes(r.document_type)) return false;
    if (params.status && r.status !== params.status) return false;
    if (params.customerId && r.customer_id !== params.customerId) return false;
    if (params.agentCode && r.agent_code !== params.agentCode) return false;
    if (params.dateFrom && r.doc_date < params.dateFrom) return false;
    if (params.dateTo && r.doc_date > params.dateTo) return false;
    if (params.openOnly && !(r.outstanding !== null && r.outstanding !== 0)) return false;
    if (params.salesOrderId) {
      const seed = SEEDS.find((s) => s.id === r.id) as Seed;
      if (!buildLines(seed).some((l) => l.sales_order?.id === params.salesOrderId)) return false;
    }
    if (q && !`${r.doc_no} ${r.customer_name} ${r.debtor_code}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const sort = params.sorting[0] ?? { id: 'doc_date', desc: true };
  const key = sort.id as keyof BillingDocumentListRow;
  rows = [...rows].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    // Blanks sort last in either direction.
    if (av === null || av === undefined) return bv === null || bv === undefined ? 0 : 1;
    if (bv === null || bv === undefined) return -1;
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return (sort.desc ? -cmp : cmp) || (a.doc_no < b.doc_no ? -1 : 1);
  });
  const start = params.pageIndex * params.pageSize;
  return {
    data: rows.slice(start, start + params.pageSize),
    pagination: { total: rows.length },
  };
}

export function mockDetail(id: string): BillingDocumentDetail | null {
  const seed = SEEDS.find((s) => s.id === id);
  if (!seed) return null;
  const row = toRow(seed);
  const lines = buildLines(seed);
  const againstRow = seed.against ? ROWS.find((r) => r.doc_no === seed.against) : undefined;
  const orders = new Map<string, { id: string; doc_no: string }>();
  const dos = new Set<string>();
  for (const l of lines) {
    if (l.sales_order) orders.set(l.sales_order.id, l.sales_order);
    if (l.from_doc_type === 'DO' && l.from_doc_no) dos.add(l.from_doc_no);
  }
  const notes = ROWS.filter((r) => {
    const s = SEEDS.find((x) => x.id === r.id) as Seed;
    return s.against === seed.no;
  }).map((r) => ({
    id: r.id,
    document_type: r.document_type,
    doc_no: r.doc_no,
    doc_date: r.doc_date,
    total: r.total,
    outstanding: r.outstanding,
  }));
  return {
    ...row,
    currency_rate: seed.rate ?? 1,
    local_net_total: r2(row.net_total * (seed.rate ?? 1)),
    ref: seed.ref ?? null,
    description: seed.description ?? null,
    last_synced_at: '2026-10-16T09:05:00',
    source_modified_at: '2026-10-15T17:40:00',
    against: seed.against ? { id: againstRow?.id ?? null, doc_no: seed.against } : null,
    lines,
    related: { notes, sales_orders: [...orders.values()], delivery_order_nos: [...dos] },
  };
}

export function mockOutstanding(customerId: string): CustomerOutstanding {
  const c = MOCK_CUSTOMERS.find((x) => x.id === customerId);
  return { customer_id: customerId, ar_outstanding: c?.ar ?? null, ar_as_of: c?.ar === null ? null : (c?.arAsOf ?? null) };
}

export function mockCustomerOptions(query: string): BillingSelectOption[] {
  const q = query.trim().toLowerCase();
  return MOCK_CUSTOMERS.filter((c) => !q || `${c.code} ${c.name}`.toLowerCase().includes(q)).map((c) => ({
    value: c.id,
    label: `${c.code} - ${c.name}`,
  }));
}

export function mockAgentOptions(): BillingSelectOption[] {
  return MOCK_AGENTS.map((a) => ({ value: a.code, label: `${a.code} - ${a.name}` }));
}
