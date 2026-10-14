/**
 * S3-01, S3-02 - the detail page's toolbar row holds Back, and nothing else.
 *
 * Everything that used to sit up here (the pager, the status gear, Edit, Delete)
 * belongs to the record card now. This asserts the toolbar for a converted page,
 * so a later module cannot quietly put a second button back on that row.
 */
import React, { Suspense } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';

import OrderDetailPage from './page';

const searchString = 'page=2&limit=50&sort=created_at&dir=desc&order_status_id=s1';

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(searchString),
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => '/order-management/orders/o-1',
}));

// The record card has its own tests; this one is about the toolbar row.
vi.mock('../components/OrderDetail', () => ({
  default: () => <div data-testid="order-detail" />,
}));

vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// #1669: the page is gated on `order_management.orders.view` via RequireAccess.
const usePermissions = vi.fn();
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => usePermissions(),
}));

function grant(slugs: string[]) {
  usePermissions.mockReturnValue({
    permissions: slugs,
    permissionSet: new Set(slugs),
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  });
}

const PARAMS = Promise.resolve({ id: 'o1' });

beforeEach(async () => {
  cleanup();
  grant(['order_management.orders.view']);
  await PARAMS;
});

async function renderPage() {
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <OrderDetailPage params={PARAMS} />
      </Suspense>,
    );
  });
}

describe('Delivery order detail toolbar', () => {
  it('S3-01, S3-02: the toolbar action row holds exactly one Back, carrying the list query', async () => {
    await renderPage();

    const back = screen.getByRole('link', { name: /Back to delivery orders/ });
    expect(back.getAttribute('href')).toBe(`/order-management/orders?${searchString}`);

    // The action row is Back's own container: it holds that one control.
    const actionRow = back.closest('div') as HTMLElement;
    expect(actionRow.querySelectorAll('a, button')).toHaveLength(1);

    // None of the record-card controls leaked back onto the toolbar.
    expect(screen.queryByRole('button', { name: /Delivery order options/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Edit$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Previous delivery order/ })).toBeNull();
  });

  it('#1669: a user without order_management.orders.view sees the access-denied state, not the record', async () => {
    grant([]);
    await renderPage();

    expect(screen.getByText("You don't have access to this page")).toBeTruthy();
    expect(screen.queryByTestId('order-detail')).toBeNull();
  });
});
