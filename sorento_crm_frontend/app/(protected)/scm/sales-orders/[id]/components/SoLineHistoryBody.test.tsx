/**
 * SoLineHistoryBody - the "Deliveries" lightbox body on the SO detail's Lines tab
 * (SO-DELIVERIES-VIEW, AC-SD20, AC-SD22 to AC-SD26, AC-SD28).
 *
 * Data is shaped like the approved mock's real cases: WESERP10B with five DOs totalling 31,
 * C-FH14 on three lines with an untied DO and an undetermined Cancel SO, CB110 with an
 * item-matched cancel of 36. `DrillTable` calls `useListingColumnPreferences` even with
 * `listingKey={null}`, so (like `SoLineLinksBody.test.tsx`) this needs a QueryClientProvider,
 * the preferences stub and a `next/navigation` stub.
 */
import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});

vi.mock('next/navigation', () => ({
  usePathname: () => '/scm/sales-orders/so-1',
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

import SoLineHistoryBodyDefault, { SoLineHistoryBody } from './SoLineHistoryBody';
import type { SalesOrderDeliveries, SalesOrderLine } from '../../../types/scm.types';

afterEach(cleanup);

const L_WESERP = '3f6c1a52-8d0e-4b7a-9c11-5a1e2d3c4b01';
const L_FH1 = '3f6c1a52-8d0e-4b7a-9c11-5a1e2d3c4b02';
const L_FH2 = '3f6c1a52-8d0e-4b7a-9c11-5a1e2d3c4b03';
const L_FH3 = '3f6c1a52-8d0e-4b7a-9c11-5a1e2d3c4b04';
const L_CB110 = '3f6c1a52-8d0e-4b7a-9c11-5a1e2d3c4b05';
const ORDER_1 = '9a7d2c10-1b2c-4d3e-8f40-aa11bb22cc01';
const ORDER_2 = '9a7d2c10-1b2c-4d3e-8f40-aa11bb22cc02';

function line(over: Partial<SalesOrderLine> = {}): SalesOrderLine {
  return {
    id: L_WESERP,
    sku: 'WESERP10B',
    product_name: 'Wall Hung Basin',
    qty_ordered: 43,
    qty_delivered: 31,
    qty_cancelled: 0,
    outstanding_qty: 12,
    uom: 'PCS',
    warehouse_code: 'BRW',
    required_date: '2026-09-04',
    line_status: 'open',
    ...over,
  } as SalesOrderLine;
}

const WESERP_DOS = [
  ['202609-1082', '2026-09-07', '2026-09-07', 8],
  ['202609-1101', '2026-09-07', '2026-09-07', 3],
  ['202609-1978', '2026-09-10', '2026-09-11', 6],
  ['202609-2849', '2026-09-17', '2026-09-18', 11],
  ['202609-2865', '2026-09-17', null, 3],
].map(([no, date, actual, qty], i) => ({
  order_id: i === 0 ? ORDER_1 : ORDER_2,
  do_number: no as string,
  do_date: date as string,
  location: 'BRW',
  status: 'Picked Up / In Transit',
  actual_delivery_date: actual as string | null,
  qty: qty as number,
}));

function data(over: Partial<SalesOrderDeliveries> = {}): SalesOrderDeliveries {
  return {
    lines: [{ line_id: L_WESERP, deliveries: WESERP_DOS, cancels: [] }],
    groups: [],
    ...over,
  };
}

function renderBody(props: Partial<React.ComponentProps<typeof SoLineHistoryBody>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onRetry = props.onRetry ?? vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <SoLineHistoryBody
        line={line()}
        data={data()}
        isLoading={false}
        isError={false}
        onRetry={onRetry}
        canOpenDo
        {...props}
      />
    </QueryClientProvider>,
  );
  return { ...view, onRetry };
}

function rowOf(text: string): HTMLElement {
  return screen.getByText(text).closest('tr') as HTMLElement;
}

describe('SoLineHistoryBody - the line figures strip (AC-SD20)', () => {
  it('is exported by name and as the default', () => {
    expect(SoLineHistoryBodyDefault).toBe(SoLineHistoryBody);
  });

  it('shows the six figures of the opened line, in the grid formats', () => {
    renderBody();

    const expected: Record<string, string> = {
      Ordered: '43',
      Delivered: '31',
      Cancelled: '0',
      Outstanding: '12',
      Location: 'BRW',
      'Delivery date': '04/09/2026',
    };
    for (const [label, value] of Object.entries(expected)) {
      const hit = screen
        .getAllByText(label)
        .some((el) => (el.parentElement?.textContent ?? '').includes(value));
      expect(hit, `${label} should sit beside ${value}`).toBe(true);
    }
  });
});

describe('SoLineHistoryBody - Delivered (AC-SD22)', () => {
  it('counts the DO lines and tables DO no. / DO date / Location / Status / Actual delivery / Qty', () => {
    renderBody();

    expect(screen.getByText('5 DO lines')).toBeInTheDocument();
    for (const h of ['DO no.', 'DO date', 'Location', 'Status', 'Actual delivery', 'Qty']) {
      expect(screen.getAllByText(h).length).toBeGreaterThan(0);
    }
    const row = rowOf('202609-1978');
    expect(within(row).getByText('10/09/2026')).toBeInTheDocument();
    expect(within(row).getByText('11/09/2026')).toBeInTheDocument();
    expect(within(row).getByText('Picked Up / In Transit')).toBeInTheDocument();
    expect(within(row).getByText('6')).toBeInTheDocument();
  });

  it('sums Qty in a Total row and reads - for an empty actual delivery', () => {
    renderBody();

    const total = screen.getAllByText('Total')[0].closest('tr') as HTMLElement;
    expect(within(total).getByText('31')).toBeInTheDocument();
    expect(within(rowOf('202609-2865')).getByText('-')).toBeInTheDocument();
  });

  it('says "1 DO line" in the singular', () => {
    renderBody({
      data: data({ lines: [{ line_id: L_WESERP, deliveries: [WESERP_DOS[0]], cancels: [] }] }),
    });

    expect(screen.getByText('1 DO line')).toBeInTheDocument();
  });

  it('shows the heading alone, with no "0 DO lines" count, when the block is empty', () => {
    renderBody({ data: data({ lines: [{ line_id: L_WESERP, deliveries: [], cancels: [] }] }) });

    expect(screen.queryByText(/^0 DO lines?$/)).toBeNull();
    const heading = screen
      .getAllByText('Delivered')
      .find((el) => el.classList.contains('font-semibold'));
    expect(heading).toBeDefined();
  });

  it('reads the empty sentence when the line has no DO', () => {
    renderBody({ data: data({ lines: [{ line_id: L_WESERP, deliveries: [], cancels: [] }] }) });

    expect(screen.getByText('No DO in the CRM for this line.')).toBeInTheDocument();
  });
});

describe('SoLineHistoryBody - Cancelled (AC-SD23)', () => {
  const cancels = [
    { doc_no: 'XS-016501', doc_date: '2026-10-06', location: 'BRW-BB', match_reason: 'item', qty: 36 },
    { doc_no: 'XS-016502', doc_date: '2026-10-07', location: 'BRW-BB', match_reason: 'line_key', qty: 4 },
    { doc_no: 'XS-016503', doc_date: '2026-10-08', location: 'BRW-BB', match_reason: 'group_whole', qty: null },
  ];

  it('tables Cancel SO no. / Date / Location / Matched by / Qty with the matched-by words', () => {
    renderBody({
      line: line({ id: L_CB110, sku: 'CB110' }),
      data: data({ lines: [{ line_id: L_CB110, deliveries: [], cancels }] }),
    });

    expect(screen.getByText('3 Cancel SO lines')).toBeInTheDocument();
    for (const h of ['Cancel SO no.', 'Date', 'Matched by']) {
      expect(screen.getAllByText(h).length).toBeGreaterThan(0);
    }
    expect(within(rowOf('XS-016501')).getByText('item')).toBeInTheDocument();
    expect(within(rowOf('XS-016501')).getByText('06/10/2026')).toBeInTheDocument();
    expect(within(rowOf('XS-016502')).getByText('line key')).toBeInTheDocument();
    expect(within(rowOf('XS-016503')).getByText('every line of the item')).toBeInTheDocument();
  });

  it('shows - for a group_whole qty and totals only the known quantities', () => {
    renderBody({
      line: line({ id: L_CB110, sku: 'CB110' }),
      data: data({ lines: [{ line_id: L_CB110, deliveries: [], cancels }] }),
    });

    expect(within(rowOf('XS-016503')).getByText('-')).toBeInTheDocument();
    const totals = screen.getAllByText('Total').map((el) => el.closest('tr') as HTMLElement);
    expect(totals.some((tr) => within(tr).queryByText('40'))).toBe(true);
  });

  it('shows the heading alone, with no "0 Cancel SO lines" count, when the block is empty', () => {
    renderBody({ data: data({ lines: [{ line_id: L_WESERP, deliveries: [], cancels: [] }] }) });

    expect(screen.queryByText(/^0 Cancel SO lines?$/)).toBeNull();
    const heading = screen
      .getAllByText('Cancelled')
      .find((el) => el.classList.contains('font-semibold'));
    expect(heading).toBeDefined();
  });

  it('reads - in the Total, not 0, when every cancel row is group_whole', () => {
    renderBody({
      line: line({ id: L_CB110, sku: 'CB110' }),
      data: data({
        lines: [
          {
            line_id: L_CB110,
            deliveries: [],
            cancels: [
              { doc_no: 'XS-W1', doc_date: '2026-10-08', location: 'BRW', match_reason: 'group_whole', qty: null },
              { doc_no: 'XS-W2', doc_date: '2026-10-09', location: 'BRW', match_reason: 'group_whole', qty: null },
            ],
          },
        ],
      }),
    });

    const totals = screen.getAllByText('Total').map((el) => el.closest('tr') as HTMLElement);
    const cancelTotal = totals.find((tr) => tr.closest('table')?.textContent?.includes('Cancel SO no.'));
    expect(cancelTotal).toBeDefined();
    expect(within(cancelTotal as HTMLElement).queryByText('0')).toBeNull();
    expect(within(cancelTotal as HTMLElement).getByText('-')).toBeInTheDocument();
  });

  it('says "1 Cancel SO line" in the singular and reads the empty sentence with none', () => {
    const { unmount } = renderBody({
      line: line({ id: L_CB110, sku: 'CB110' }),
      data: data({ lines: [{ line_id: L_CB110, deliveries: [], cancels: [cancels[0]] }] }),
    });
    expect(screen.getByText('1 Cancel SO line')).toBeInTheDocument();
    unmount();

    renderBody({ data: data({ lines: [{ line_id: L_WESERP, deliveries: [], cancels: [] }] }) });
    expect(screen.getByText('No Cancel SO for this line.')).toBeInTheDocument();
  });
});

describe('SoLineHistoryBody - the product group block (AC-SD24)', () => {
  const group = {
    sku: 'C-FH14',
    line_ids: [L_FH1, L_FH2, L_FH3],
    deliveries: [
      {
        order_id: ORDER_1,
        do_number: 'PS202609-0054',
        do_date: '2026-09-02',
        location: 'BRW-BB',
        status: 'Picked Up / In Transit',
        actual_delivery_date: '2026-09-03',
        qty: 12,
      },
    ],
    cancels: [
      { doc_no: 'XS-016501', doc_date: '2026-10-06', location: 'BRW-BB', match_reason: 'undetermined', qty: 84 },
    ],
  };
  const withGroup = data({
    lines: [
      { line_id: L_FH1, deliveries: [], cancels: [] },
      { line_id: L_FH2, deliveries: [], cancels: [] },
      { line_id: L_FH3, deliveries: [], cancels: [] },
    ],
    groups: [group],
  });

  it.each([L_FH1, L_FH2, L_FH3])('shows the same block in the lightbox of line %s', (id) => {
    renderBody({ line: line({ id, sku: 'C-FH14' }), data: withGroup });

    expect(screen.getByText(/C-FH14 on this SO, not tied to one line/)).toBeInTheDocument();
    expect(screen.getByText(/3 lines/)).toBeInTheDocument();
    expect(screen.getByText('PS202609-0054')).toBeInTheDocument();
    expect(within(rowOf('XS-016501')).getByText('not tied to one line')).toBeInTheDocument();
    expect(within(rowOf('XS-016501')).getByText('84')).toBeInTheDocument();
    // the line's own two lists stay empty
    expect(screen.getByText('No DO in the CRM for this line.')).toBeInTheDocument();
    expect(screen.getByText('No Cancel SO for this line.')).toBeInTheDocument();
  });

  it('reads the product sentences when the group holds nothing', () => {
    renderBody({
      line: line({ id: L_FH1, sku: 'C-FH14' }),
      data: data({
        lines: [{ line_id: L_FH1, deliveries: [], cancels: [] }],
        groups: [{ sku: 'C-FH14', line_ids: [L_FH1, L_FH2], deliveries: [], cancels: [] }],
      }),
    });

    expect(screen.getByText('No DO in the CRM for this product here.')).toBeInTheDocument();
    expect(screen.getByText('No Cancel SO for this product here.')).toBeInTheDocument();
  });

  it('draws the group as a full bordered panel, not only a top rule', () => {
    renderBody({ line: line({ id: L_FH1, sku: 'C-FH14' }), data: withGroup });

    const panel = screen
      .getByText(/C-FH14 on this SO, not tied to one line/)
      .closest('section') as HTMLElement;
    expect(panel.classList.contains('border')).toBe(true);
  });

  it('shows no group block for a product on one line', () => {
    renderBody({ line: line({ id: L_CB110, sku: 'CB110' }), data: withGroup });

    expect(screen.queryByText(/on this SO, not tied to one line/)).toBeNull();
    expect(screen.queryByText('PS202609-0054')).toBeNull();
  });
});

describe('SoLineHistoryBody - the DO link and permission (AC-SD25)', () => {
  it('links the DO number to the order page when the viewer may open DOs', () => {
    renderBody({ canOpenDo: true });

    expect(screen.getByRole('link', { name: '202609-1082' })).toHaveAttribute(
      'href',
      `/order-management/orders/${ORDER_1}`,
    );
  });

  it('keeps the number as plain text, row still shown, without the permission', () => {
    renderBody({ canOpenDo: false });

    expect(screen.getByText('202609-1082')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '202609-1082' })).toBeNull();
    expect(screen.queryAllByRole('link')).toHaveLength(0);
  });
});

describe('SoLineHistoryBody - clear states (AC-SD26)', () => {
  it('shows the shared SectionSkeleton (role=status) while loading', () => {
    renderBody({ data: undefined, isLoading: true });

    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('keeps the loaded rows and shows no error when a background refetch fails', () => {
    renderBody({ isError: true });

    expect(screen.getByText('202609-1082')).toBeInTheDocument();
    expect(screen.queryByText('Could not load deliveries.')).toBeNull();
  });

  it('shows skeleton rows while loading, and no empty or error sentence', () => {
    const { container } = renderBody({ data: undefined, isLoading: true });

    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    expect(screen.queryByText(/^No DO in the CRM/)).toBeNull();
    expect(screen.queryByText(/^No Cancel SO/)).toBeNull();
    expect(screen.queryByText('Could not load deliveries.')).toBeNull();
  });

  it('names the failure and retries from the Try again button', () => {
    const { onRetry } = renderBody({ data: undefined, isError: true });

    expect(screen.getByText('Could not load deliveries.')).toBeInTheDocument();
    expect(screen.queryByText(/^No DO in the CRM/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe('SoLineHistoryBody - no ids on screen (AC-SD28)', () => {
  it('renders no line, order or product id as text', () => {
    const { container } = renderBody({
      data: data({
        groups: [{ sku: 'WESERP10B', line_ids: [L_WESERP, L_FH1], deliveries: [], cancels: [] }],
      }),
    });

    const text = container.textContent ?? '';
    for (const id of [L_WESERP, L_FH1, ORDER_1, ORDER_2]) {
      expect(text).not.toContain(id);
    }
    expect(text).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/);
  });
});
