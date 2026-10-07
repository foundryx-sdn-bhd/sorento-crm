/**
 * `PLAN-oi-no-double-count-25sep.md` S0 and S2 (issue #1248), AC-ND-13..16, AC-ND-20, owner rulings 26 Sep
 * 2026: ONE History dialog per sales order line (G2) with line tabs Rows | Decisions, plus
 * Reserve only when the line has reserve history (G3). Rows lists every row that is not
 * the line's current need, the used row included (G1, G6); the Was / now story reads only
 * here.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
const getDecisionTrail = vi.fn();

// Every read the order inquiry services make goes through this one client.
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));

vi.mock('../../../_shared/services/orderInquiryReserveService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../_shared/services/orderInquiryReserveService')>();
  return { ...actual, getDecisionTrail: (...args: unknown[]) => getDecisionTrail(...args) };
});

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import { OrderInquiryLineHistoryDialog } from './OrderInquiryLineHistoryDialog';
import { foldInquiryLines } from '../../../_shared/lib/orderInquiryLineFold';
import type { OrderInquiryWorklistRow } from '../../../_shared/types/orderInquiry.types';

function row(overrides: Partial<OrderInquiryWorklistRow>): OrderInquiryWorklistRow {
  return {
    id: 'row-1',
    verb: 'ORDER',
    state: 'raised',
    qty: '10',
    linked_qty: '0',
    reserved_qty: '0',
    bundled_qty: '0',
    so_number: 'SO402757',
    item_code: 'CKS1050',
    core_line_id: 'cl-1',
    line_no: 1,
    ...overrides,
  } as OrderInquiryWorklistRow;
}

const USED = row({
  id: 'used',
  qty: '2',
  state: 'placed',
  redirected_to_pool: true,
  note: 'PO-2026/09-0023 received 24 Sep 2026, released at revision 3',
  raised_at: '2026-09-19T08:00:00Z',
  links: [{ kind: 'po', document: 'PO-2026/09-0023', qty: '2' }] as never,
});
const FRESH = row({
  id: 'fresh',
  qty: '5',
  previous_qty: '2',
  note: 'Replaces 2 used; PO-2026/09-0023 received',
  raised_at: '2026-09-25T08:00:00Z',
});

// AC-ND-15/16 predate the Before row (F2): they keep their fixture without previous_*.
const FRESH_NO_PREV = row({
  id: 'fresh',
  qty: '5',
  note: 'Replaces 2 used; PO-2026/09-0023 received',
  raised_at: '2026-09-25T08:00:00Z',
});
const CHANGED = row({
  id: 'e9fac76d',
  qty: '280',
  delivery_date: '2027-03-01',
  previous_qty: '182',
  previous_delivery_date: '2026-06-01',
  inquiry_no: 'OI-2609-0011',
  changed_at: '2026-09-17T03:25:36Z',
  raised_at: '2026-09-20T08:00:00Z',
});
const BEFORE_WHY = 'Was 182 on 01/06/2026, before OI-2609-0011 changed it';

function renderDialog(
  rows: OrderInquiryWorklistRow[],
  props: Partial<React.ComponentProps<typeof OrderInquiryLineHistoryDialog>> = {},
) {
  const [line] = foldInquiryLines(rows);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OrderInquiryLineHistoryDialog
        line={line}
        onOpenChange={() => {}}
        {...props}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getDecisionTrail.mockResolvedValue([]);
});

describe('AC-ND-14: title and tabs', () => {
  it('is titled "History - <item> (<SO> L<n>)" with Rows | Decisions, Rows first', async () => {
    renderDialog([USED, FRESH]);
    const dialog = await screen.findByRole('dialog', { name: 'History - CKS1050 (SO402757 L1)' });
    const tabs = within(dialog).getAllByRole('tab').map((tab) => tab.textContent);
    expect(tabs).toEqual(['Rows', 'Decisions']);
    expect(within(dialog).getByRole('tab', { name: 'Rows' })).toHaveAttribute('aria-selected', 'true');
  });

  it('adds a Reserve tab only when the line has reserve history, listing it', async () => {
    renderDialog([USED, FRESH], {
      reserveEntries: [
        { kind: 'reserved', qty: '2', location: 'BRW', reason: null, actor_name: 'Eling', created_at: '2026-09-24T04:25:00Z' },
      ],
    });
    const dialog = await screen.findByRole('dialog');
    const reserveTab = within(dialog).getByRole('tab', { name: 'Reserve' });
    fireEvent.mouseDown(reserveTab);
    fireEvent.click(reserveTab);
    expect(await within(dialog).findByText('Reserved 2 @ BRW')).toBeInTheDocument();
  });
});

describe('AC-ND-15 (G1, G6): the Rows tab', () => {
  it('lists Now first with its Was, then the used row with its document', async () => {
    renderDialog([USED, FRESH]);
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('Now');
    const bodyRows = within(dialog).getAllByRole('row').filter((r) => r.closest('tbody'));
    // F2 (AC-13): FRESH carries previous_qty, so the Before row sits between Now and Used.
    expect(bodyRows).toHaveLength(3);
    expect(within(bodyRows[0]).getByText('Now')).toBeInTheDocument();
    expect(within(bodyRows[0]).getByText('Was 2. Replaces 2 used; PO-2026/09-0023 received')).toBeInTheDocument();
    expect(within(bodyRows[1]).getByText('Before')).toBeInTheDocument();
    expect(within(bodyRows[2]).getByText('Used')).toBeInTheDocument();
    expect(within(bodyRows[2]).getByText('PO-2026/09-0023')).toBeInTheDocument();
  });

  it("S2 (AC-ND-20): lists the line's cancelled rows from the one Lines fetch, labelled", async () => {
    renderDialog([
      USED,
      FRESH,
      row({ id: 'sup', state: 'cancelled', note: 'Superseded by revision 2', raised_at: '2026-09-18T08:00:00Z' }),
    ]);
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('Superseded')).toBeInTheDocument();
    const bodyRows = within(dialog).getAllByRole('row').filter((r) => r.closest('tbody'));
    expect(bodyRows).toHaveLength(3);
  });

  it('AC-ND-16: a line with no earlier rows says so', async () => {
    renderDialog([FRESH_NO_PREV]);
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('No earlier rows for this line.')).toBeInTheDocument();
  });
});

describe('SO-LINE-CHANGE-HISTORY F2 (AC-7, AC-8): Before row and Delivery date column', () => {
  const bodyRowsOf = (dialog: HTMLElement) =>
    within(dialog).getAllByRole('row').filter((r) => r.closest('tbody'));

  it('AC-7: lists a Before row after Now with Qty, Delivery date, Document "-" and its Why', async () => {
    renderDialog([CHANGED]);
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('Now');
    const bodyRows = bodyRowsOf(dialog);
    expect(bodyRows).toHaveLength(2);
    const before = bodyRows[1];
    expect(within(before).getByText('Before')).toBeInTheDocument();
    expect(within(before).getByText('182')).toBeInTheDocument();
    expect(within(before).getByText('01/06/2026')).toBeInTheDocument();
    expect(within(before).getByText('-')).toBeInTheDocument();
    expect(within(before).getByText(BEFORE_WHY)).toBeInTheDocument();
    expect(within(dialog).queryByText('No earlier rows for this line.')).not.toBeInTheDocument();
  });

  it('AC-8: a Delivery date column sits between Qty and What and the Now row prints 01/03/2027', async () => {
    renderDialog([CHANGED]);
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('Now');
    const headers = within(dialog).getAllByRole('columnheader').map((h) => h.textContent?.trim());
    const at = headers.indexOf('Delivery date');
    expect(at).toBeGreaterThan(-1);
    expect(headers[at - 1]).toBe('Qty');
    expect(headers[at + 1]).toBe('What');
    expect(within(bodyRowsOf(dialog)[0]).getByText('01/03/2027')).toBeInTheDocument();
  });
});

describe('S2 (AC-ND-20): the Rows tab makes no read of its own', () => {
  it('renders the rows at once, with no fetch and no loading state', () => {
    renderDialog([
      FRESH,
      row({ id: 'sup', state: 'cancelled', note: 'Superseded by revision 2', raised_at: '2026-09-18T08:00:00Z' }),
    ]);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Superseded')).toBeInTheDocument();
    expect(within(dialog).queryByRole('status', { name: 'Loading' })).not.toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});

describe('Decisions tab', () => {
  it('reads the line decision trail, and says so when the line names no core line', async () => {
    renderDialog([row({ id: 'x', core_line_id: null, line_no: null })]);
    const dialog = await screen.findByRole('dialog');
    const decisions = within(dialog).getByRole('tab', { name: 'Decisions' });
    fireEvent.mouseDown(decisions);
    fireEvent.click(decisions);
    expect(await within(dialog).findByText('No decisions recorded for this line.')).toBeInTheDocument();
    expect(getDecisionTrail).not.toHaveBeenCalled();
  });
});
