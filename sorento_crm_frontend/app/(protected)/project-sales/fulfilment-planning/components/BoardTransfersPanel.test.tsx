/**
 * BoardTransfersPanel (PLAN section 3.D4, UAC D6-D10).
 *
 * The movements a board's confirmations raised, listed above the product matrix so they can be
 * approved without leaving the page. It LISTS rather than remembers (D8), so every test here
 * drives the data hook rather than any local click-state.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: vi.fn(),
    isLoading: false,
  }),
}));

const hasPermission = vi.fn((_slug: string) => true);
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => hasPermission(slug),
}));

const useBoardTransfers = vi.fn();
const approveMutate = vi.fn();
const approveAllMutate = vi.fn();
vi.mock('../../_shared/hooks/useBoardTransfers', () => ({
  useBoardTransfers: (...args: unknown[]) => useBoardTransfers(...args),
  useBoardTransferMutations: () => ({
    approve: { mutate: approveMutate, isPending: false },
    approveAll: { mutate: approveAllMutate, isPending: false },
  }),
}));

import { BoardTransfersPanel } from './BoardTransfersPanel';
import type { StockTransfer } from '@/app/(protected)/inventory-management/stock-transfers/types/stockTransfer.types';

function transferOf(overrides: Partial<StockTransfer> = {}): StockTransfer {
  return {
    id: 'transfer-1',
    transfer_no: 'ST-000015',
    state: 'proposed',
    kind: 'pool',
    qty: '15',
    product_id: 'prod-1',
    item_code: 'SRTWB7518',
    product_name: 'Some Product',
    from_warehouse_id: 'wh-BRW',
    from_location: 'BRW',
    to_warehouse_id: 'wh-BRW-AM',
    to_location: 'BRW-AM',
    sales_order_id: 'so-a',
    so_number: 'SO404352',
    so_line_no: 22,
    project_sales_order_id: 'pso-1',
    customer_name: 'ABC SDN BHD',
    sales_agent_id: null,
    agent_code: null,
    agent_name: null,
    supply_decision_id: null,
    revision_no: 1,
    proposed_at: '2026-08-27T10:00:00',
    approved_by: null,
    approved_by_name: null,
    approved_at: null,
    moved_by: null,
    moved_by_name: null,
    moved_at: null,
    cancelled_by: null,
    cancelled_by_name: null,
    cancelled_at: null,
    cancelled_reason: null,
    autocount_ref: null,
    created_at: '2026-08-27T10:00:00',
    updated_at: '2026-08-27T10:00:00',
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  hasPermission.mockImplementation(() => true);
});

function mockData(
  rows: StockTransfer[],
  overrides: Partial<ReturnType<typeof useBoardTransfers>> = {},
) {
  useBoardTransfers.mockReturnValue({
    data: { data: rows },
    isLoading: false,
    error: undefined,
    ...overrides,
  });
}

describe('BoardTransfersPanel: columns (D6)', () => {
  it('renders Transfer no, Product, From/To, Qty, Kind, For, State and Proposed at', () => {
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);

    for (const header of [
      'Transfer no',
      'Product',
      'From / To',
      'Qty',
      'Kind',
      'For',
      'State',
      'Proposed at',
    ]) {
      expect(
        screen.getByRole('columnheader', { name: header }),
      ).toBeInTheDocument();
    }
    expect(screen.getByText('ST-000015')).toBeInTheDocument();
    expect(screen.getByText('BRW to BRW-AM')).toBeInTheDocument();
    expect(screen.getByText('SO404352 · line 22')).toBeInTheDocument();
  });
});

describe('BoardTransfersPanel: one line per cell where the second said nothing new', () => {
  it('shows the product code once when the product name is the code', () => {
    mockData([transferOf({ item_code: 'SRTFVP08', product_name: 'SRTFVP08' })]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);

    expect(screen.getAllByText('SRTFVP08')).toHaveLength(1);
  });

  it('keeps the product name under the code when it differs', () => {
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);

    expect(screen.getByText('SRTWB7518')).toBeInTheDocument();
    expect(screen.getByText('Some Product')).toBeInTheDocument();
  });

  it('shows the order line in For without the customer name', () => {
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);

    expect(screen.getByText('SO404352 · line 22')).toBeInTheDocument();
    expect(screen.queryByText('ABC SDN BHD')).not.toBeInTheDocument();
  });
});

describe('BoardTransfersPanel: hidden when empty (D4)', () => {
  it('renders nothing when the list is empty and nothing was just confirmed', () => {
    mockData([]);

    const { container } = render(
      <BoardTransfersPanel soNumbers={['SO404352']} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('still renders, empty, once a confirmation was pressed on this board', () => {
    mockData([]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} justConfirmed />);

    expect(screen.getByText('Stock transfers')).toBeInTheDocument();
    expect(screen.getByText('Nothing has to move')).toBeInTheDocument();
  });
});

describe('BoardTransfersPanel: Approve and Approve all proposed (D7)', () => {
  it('confirms first, then approves one row through the service', () => {
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));

    // Nothing has moved yet: approving commits somebody to a physical movement, so it is
    // confirmed first, in the transfers page's own words.
    expect(approveMutate).not.toHaveBeenCalled();
    expect(screen.getByRole('alertdialog')).toHaveTextContent(
      'Approve ST-000015?',
    );
    expect(screen.getByRole('alertdialog')).toHaveTextContent(
      '15 SRTWB7518 BRW to BRW-AM',
    );

    fireEvent.click(
      screen.getByRole('button', { name: 'Approve', hidden: false }),
    );
    expect(approveMutate).toHaveBeenCalledWith('transfer-1');
  });

  it('keeps the row unapproved when the confirmation is cancelled', () => {
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(approveMutate).not.toHaveBeenCalled();
  });

  it('names the count in the Approve all confirmation, then approves every proposed row', () => {
    mockData([
      transferOf({ id: 't-1' }),
      transferOf({ id: 't-2', transfer_no: 'ST-000016' }),
    ]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Approve all proposed (2)' }),
    );

    expect(approveAllMutate).not.toHaveBeenCalled();
    const dialog = screen.getByRole('alertdialog');
    expect(dialog).toHaveTextContent('Approve 2 proposed transfers?');
    // The count, not the movements: the rows are on the page already.
    expect(dialog).toHaveTextContent(
      'Every proposed transfer listed on this page is approved.',
    );
    expect(dialog).not.toHaveTextContent('BRW to BRW-IB');

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    expect(approveAllMutate).toHaveBeenCalledWith(['t-1', 't-2']);
  });

  it('does not offer Approve all when every row is already approved', () => {
    mockData([transferOf({ state: 'approved' })]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);

    expect(
      screen.queryByRole('button', { name: /Approve all proposed/ }),
    ).not.toBeInTheDocument();
  });
});

describe('BoardTransfersPanel: nothing at all without the view grant (D9)', () => {
  it('renders no panel and asks for no list', () => {
    hasPermission.mockImplementation(
      (slug: string) => slug !== 'inventory.stock_transfers.view',
    );
    mockData([transferOf()]);

    const { container } = render(
      <BoardTransfersPanel soNumbers={['SO404352']} justConfirmed />,
    );

    expect(container).toBeEmptyDOMElement();
    // The QUERY is off too: a request that comes back 403 on every board is not a read.
    expect(useBoardTransfers).toHaveBeenCalledWith(['SO404352'], false);
  });

  it('passes the view grant to the hook when it is held', () => {
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);

    expect(useBoardTransfers).toHaveBeenCalledWith(['SO404352'], true);
  });
});

describe('BoardTransfersPanel: no buttons without inventory.stock_transfers.edit (D9)', () => {
  it('lists the transfers with no Approve button at all', () => {
    hasPermission.mockImplementation(
      (slug: string) => slug !== 'inventory.stock_transfers.edit',
    );
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);

    expect(screen.getByText('ST-000015')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Approve' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Approve all proposed/ }),
    ).not.toBeInTheDocument();
    expect(hasPermission).toHaveBeenCalledWith(
      'inventory.stock_transfers.edit',
    );
  });

  it('an approved row carries no verb either way - Mark moved belongs to the transfer record', () => {
    mockData([transferOf({ state: 'approved' })]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} />);

    expect(
      screen.queryByRole('button', { name: 'Approve' }),
    ).not.toBeInTheDocument();
  });
});

describe('BoardTransfersPanel: the inquiry footer (D10)', () => {
  it('names the count of order-inquiry rows a confirmation raised, linked to Order Inquiries', () => {
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} inquiryRows={3} />);

    expect(screen.getByText(/3 order inquiry rows raised/)).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Order Inquiries' });
    expect(link).toHaveAttribute('href', '/project-sales/order-inquiries');
  });

  it('says nothing about inquiries when none were raised', () => {
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} inquiryRows={0} />);

    expect(screen.queryByText(/order inquiry row/)).not.toBeInTheDocument();
  });

  it('reads the singular for exactly one row', () => {
    mockData([transferOf()]);

    render(<BoardTransfersPanel soNumbers={['SO404352']} inquiryRows={1} />);

    expect(screen.getByText(/1 order inquiry row raised/)).toBeInTheDocument();
  });
});
