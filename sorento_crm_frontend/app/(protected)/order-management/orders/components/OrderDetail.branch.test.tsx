/**
 * DO-BRANCH-NAME (owner, 13 Oct 2026): the synced AutoCount branch name is visible on the
 * delivery order detail so the owner can cross-check what the chatbot says.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import OrderDetail from './OrderDetail';

const useOrder = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
  usePathname: () => '/order-management/orders/o-1',
}));
vi.mock('../hooks/useOrders', () => ({
  useOrder: (...a: unknown[]) => useOrder(...a),
  ordersPagerQuery: vi.fn(),
}));
vi.mock('../actions', () => ({
  useOrderActions: () => ({ actions: [], dialogs: null, pending: false }),
}));
vi.mock('@/hooks/useDeletedRecordGuard', () => ({ useDeletedRecordGuard: () => false }));
vi.mock('@/components/common/DetailActions', () => ({ default: () => null }));
vi.mock('./OrderLinesCard', () => ({ default: () => null }));
vi.mock('./OrderFulfilledComplaintsCard', () => ({ default: () => null }));

function mockOrder(overrides: Record<string, unknown>) {
  useOrder.mockReturnValue({
    data: {
      id: 'o-1',
      order_number: 'DO-AC-0001',
      debtor_code: '300-AC',
      debtor_name: 'HANLIM TRADING SDN BHD',
      subtotal_amount: 0,
      discount_amount: 0,
      tax_amount: 0,
      total_amount: 0,
      ...overrides,
    },
    isLoading: false,
  });
}

function branchValue(): string | null {
  const label = screen.getByText('Branch');
  const cell = label.parentElement as HTMLElement;
  return within(cell).getAllByText(/.+/)[1]?.textContent ?? null;
}

beforeEach(() => useOrder.mockReset());

describe('Delivery order detail: Branch', () => {
  it('shows the branch name', () => {
    mockOrder({ branch_name: 'KLCC SITE OFFICE' });
    render(<OrderDetail orderId="o-1" listSearch="" />);
    expect(branchValue()).toBe('KLCC SITE OFFICE');
  });

  it('shows the branch name on the Delivery & tracking tab too', async () => {
    mockOrder({ branch_name: 'KLCC SITE OFFICE' });
    render(<OrderDetail orderId="o-1" listSearch="" />);
    await userEvent.click(screen.getByRole('tab', { name: /Delivery & tracking/ }));
    expect(screen.getByRole('tab', { name: /Delivery & tracking/ })).toHaveAttribute('aria-selected', 'true');
    expect(branchValue()).toBe('KLCC SITE OFFICE');
  });

  it('shows a dash when the DO has no branch name', () => {
    mockOrder({ branch_name: null });
    render(<OrderDetail orderId="o-1" listSearch="" />);
    expect(branchValue()).toBe('-');
  });
});
