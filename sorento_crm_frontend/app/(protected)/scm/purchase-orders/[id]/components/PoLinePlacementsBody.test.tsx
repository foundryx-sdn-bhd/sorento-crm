/**
 * PoLinePlacementsBody - the "Placed" lightbox on the PO detail's lines grid (R5,
 * AC-L1/AC-L2), replacing the "Allocated to" card (AC-L3, its own test file removed with it).
 *
 * `DrillTable` calls `useListingColumnPreferences` even with `listingKey={null}` - see
 * `PlanRowDialog.test.tsx`'s own note - so this needs a `QueryClientProvider` and the
 * `next/navigation` stub too.
 */
import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});

import { vi } from 'vitest';
vi.mock('next/navigation', () => ({
  usePathname: () => '/scm/purchase-orders/po-1',
}));

import { PoLinePlacementsBody } from './PoLinePlacementsBody';
import type { PurchaseOrderLineAllocation } from '../../../types/scm.types';

afterEach(cleanup);

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** PO-2026/07-0029 shaped fixture: one line, an SPO pull and a dedication, adding up. */
const LINE: PurchaseOrderLineAllocation = {
  line_id: 'line-1',
  sku: 'WESERP10B',
  warehouse_code: 'DC1',
  outstanding: 209,
  allocated: 135,
  free: 74,
  dedicated_to: [
    { so_number: 'SO391853', reserved: 40, unplaced: 40, source: 'po_upload' },
  ],
  placements: [
    {
      kind: 'spo',
      spo_number: 'CRM-SPO-2026/08-0007',
      purchase_order_id: 'spo-po-7',
      packing_list: 'FSCU8103365',
      qty: 95,
      warehouses: [{ warehouse_code: 'BRW', qty: 95 }],
      arrival_date: '2026-09-14',
      inquiry_no: null,
      so_number: null,
      customer: null,
      agent: null,
      needed_at: null,
      location_differs: false,
    },
  ],
};

describe('PoLinePlacementsBody', () => {
  it('shows an SPO row with the SPO badge, its number and the packing list', () => {
    renderWithClient(<PoLinePlacementsBody allocation={LINE} />);

    expect(screen.getByText('SPO')).toBeInTheDocument();
    expect(screen.getByText('CRM-SPO-2026/08-0007')).toBeInTheDocument();
    expect(screen.getByText('FSCU8103365')).toBeInTheDocument();
  });

  it('links the SPO number to its own PO detail (L2, review round)', () => {
    renderWithClient(<PoLinePlacementsBody allocation={LINE} />);

    const link = screen.getByRole('link', { name: 'CRM-SPO-2026/08-0007' });
    expect(link).toHaveAttribute('href', '/scm/purchase-orders/spo-po-7');
  });

  it('shows a dedication row with the Dedicated badge and the sales order', () => {
    renderWithClient(<PoLinePlacementsBody allocation={LINE} />);

    expect(screen.getByText('Dedicated')).toBeInTheDocument();
    const row = screen.getByText('SO391853').closest('tr') as HTMLElement;
    expect(within(row).getByText('40')).toBeInTheDocument();
  });

  it('never renders a Needed at header', () => {
    renderWithClient(<PoLinePlacementsBody allocation={LINE} />);

    expect(screen.queryByText('Needed at')).toBeNull();
  });

  it('foots Outstanding, Placed and Free, all three adding up', () => {
    renderWithClient(<PoLinePlacementsBody allocation={LINE} />);

    // 95 (SPO) + 40 (dedication) = 135 Placed.
    expect(screen.getByText('Outstanding 209 · Placed 135 · Free 74')).toBeInTheDocument();
  });

  it('says nothing is placed, rather than an empty table, when there is nothing', () => {
    renderWithClient(
      <PoLinePlacementsBody
        allocation={{ ...LINE, placements: [], dedicated_to: [], outstanding: 0, allocated: 0, free: 0 }}
      />,
    );

    expect(screen.getByText('Nothing is placed on this line.')).toBeInTheDocument();
    expect(screen.getByText('Outstanding 0 · Placed 0 · Free 0')).toBeInTheDocument();
  });

  it('names an order-inquiry placement by its inquiry and sales order, never by id', () => {
    renderWithClient(
      <PoLinePlacementsBody
        allocation={{
          ...LINE,
          dedicated_to: [],
          placements: [
            {
              inquiry_no: 'OI-000001',
              so_number: 'SO416191',
              customer: 'YOTU BUILDER SDN BHD',
              agent: 'CONTACT N',
              qty: 6,
              needed_at: 'BRW',
              location_differs: true,
            },
          ],
        }}
      />,
    );

    expect(screen.getByText('OI-000001')).toBeInTheDocument();
    expect(screen.getByText('SO416191')).toBeInTheDocument();
    expect(screen.getByText('YOTU BUILDER SDN BHD')).toBeInTheDocument();
    expect(screen.getByText('Location differs')).toBeInTheDocument();
    expect(screen.queryByText(/line-1/)).toBeNull();
  });
});
