/**
 * CUSTOMER-BULK-OPS U1: the supplier edit page keeps the list state (page, sort, query,
 * filters) on Save and on the Back link, so the detail pager still walks the same list.
 */
import React, { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

const nav = vi.hoisted(() => ({ push: vi.fn(), search: '' }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: nav.push }),
  usePathname: () => '/procurement-management/suppliers/sup-1/edit',
  useSearchParams: () => new URLSearchParams(nav.search),
}));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/common/PageHeader', () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));
vi.mock('../../components/SupplierForm', () => ({
  default: ({ onSuccess }: { onSuccess?: () => void }) => (
    <div>
      the supplier form
      <button type="button" onClick={() => onSuccess?.()}>
        fake save
      </button>
    </div>
  ),
}));

import EditSupplierPage from './page';

const QS = 'page=3&sort=supplier_name&query=acme&status=active';

async function renderPage() {
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <EditSupplierPage params={Promise.resolve({ id: 'sup-1' })} />
      </Suspense>,
    );
  });
}

beforeEach(() => {
  nav.push.mockClear();
  nav.search = QS;
});

describe('EditSupplierPage - pager keeps list state', () => {
  it('saving returns to the detail page with the list query string', async () => {
    await renderPage();
    fireEvent.click(await screen.findByText('fake save'));
    expect(nav.push).toHaveBeenCalledWith(`/procurement-management/suppliers/sup-1?${QS}`);
  });

  it('Back to supplier keeps the list query string', async () => {
    await renderPage();
    const link = (await screen.findByText('Back to supplier')).closest('a');
    expect(link?.getAttribute('href')).toBe(`/procurement-management/suppliers/sup-1?${QS}`);
  });
});
