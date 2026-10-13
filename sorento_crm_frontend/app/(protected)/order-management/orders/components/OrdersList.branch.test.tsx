/**
 * DO-BRANCH-NAME (owner, 13 Oct 2026): the delivery order list carries a Branch column,
 * visible by default, right after Debtor Name.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import OrdersList from './OrdersList';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
  usePathname: () => '/order-management/orders',
}));
// Nothing answers the remembered-view request under jsdom; without this the grid sits on skeletons.
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(async (listingKey: string) => ({ listing_key: listingKey, config: null })),
  upsertUserListColumnConfig: vi.fn(async (listingKey: string, config: unknown) => ({ listing_key: listingKey, config })),
  resetUserListColumnConfig: vi.fn(async () => undefined),
}));
vi.mock('../hooks/useOrders', () => ({
  useOrders: () => ({
    data: {
      data: [
        { id: 'o-1', order_number: 'DO-1', debtor_name: 'HANLIM TRADING SDN BHD', branch_name: 'KLCC SITE OFFICE' },
        { id: 'o-2', order_number: 'DO-2', debtor_name: 'CHIN CHUN HARDWARE', branch_name: null },
      ],
      pagination: { total: 2, page: 1, limit: 50, pages: 1 },
    },
    isLoading: false,
    isPlaceholderData: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    isFetching: false,
  }),
}));
vi.mock('../../shared/hooks/use-order-status-select-query', () => ({
  useOrderStatusSelectQuery: () => ({ data: [] }),
}));
vi.mock('../actions', () => ({ OrderRowActions: () => null }));
vi.mock('@/components/upload-activity', () => ({ useImportJobDrawer: () => ({ notifyImportQueued: vi.fn() }) }));
vi.mock('@/hooks/useListStateFromUrl', () => ({ useListStateFromUrl: () => undefined }));
vi.mock('@/hooks/useDeferredBulkAction', () => ({ useDeferredBulkAction: () => ({ start: vi.fn(), pending: false }) }));
vi.mock('@/lib/pending-entity-store', () => ({
  pendingEntityKey: (t: string, id: string) => `${t}:${id}`,
  usePendingEntityKeys: () => new Set<string>(),
}));
vi.mock('@/app/(protected)/system-management/import-jobs/autocount-pull/hooks/useAutocountPull', () => ({
  useAutocountPullAction: () => ({ label: 'Pull from AutoCount', onClick: vi.fn(), disabled: false }),
}));
vi.mock('@/app/(protected)/system-management/import-jobs/autocount-pull/components/PullScopeDialog', () => ({
  PullScopeDialog: () => null,
}));
vi.mock('@/components/template/TemplateUploadDialog', () => ({ TemplateUploadDialog: () => null }));
vi.mock('./OrderTrackingUploadDialog', () => ({ OrderTrackingUploadDialog: () => null }));
vi.mock('./OrderLinesImportDialog', () => ({ OrderLinesImportDialog: () => null }));

describe('Delivery order list: Branch column', () => {
  it('shows a Branch column after Debtor Name, with the branch name or a dash', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <OrdersList />
      </QueryClientProvider>,
    );
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    const debtor = headers.indexOf('Debtor Name');
    expect(debtor).toBeGreaterThan(-1);
    expect(headers[debtor + 1]).toBe('Branch');

    const cell = await screen.findByText('KLCC SITE OFFICE');
    expect(cell.getAttribute('title')).toBe('KLCC SITE OFFICE');
    const rowTwo = screen.getByText('CHIN CHUN HARDWARE').closest('tr') as HTMLElement;
    const cells = Array.from(rowTwo.querySelectorAll('td')).map((td) => td.textContent?.trim());
    expect(cells[cells.indexOf('CHIN CHUN HARDWARE') + 1]).toBe('-');
  });
});
