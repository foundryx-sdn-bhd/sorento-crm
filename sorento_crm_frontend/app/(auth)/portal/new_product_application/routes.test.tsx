/**
 * Staff impersonation keeps the portal on the legacy slug-less tree (`/portal`, see
 * app/(auth)/portal/page.tsx). From that landing, New builds `/portal/new_product_application/new`
 * (portalNewPath with no slug) and a card opens `/portal/new_product_application/<id>`. Without their own
 * routes those paths fell through to the generic `[type]` pages and 404'd, because
 * `new_product_application` is not a generic submission kind. Same wrappers as price_tag_request/.
 */
import React, { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';

const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace, back: vi.fn() }),
  notFound: () => {
    throw new Error('notFound');
  },
}));

vi.mock('../components/NewProductApplicationForm', () => ({
  __esModule: true,
  default: (props: { applicationId?: string; slug?: string }) => (
    <div data-testid="npa-form">
      {props.applicationId ? `edit:${props.applicationId}` : 'new'}|slug:
      {String(props.slug ?? '')}
    </div>
  ),
}));

import { portalDetailPath, portalNewPath } from '../lib/portal-paths';

beforeEach(() => {
  replace.mockClear();
  window.localStorage.clear();
});

describe('impersonated /portal (no slug) reaches the new product application form', () => {
  it('New from the landing opens the form, not a 404', async () => {
    expect(portalNewPath('new_product_application', null)).toBe(
      '/portal/new_product_application/new',
    );
    const { default: NewPage } = await import('./new/page');
    render(<NewPage />);
    expect(screen.getByTestId('npa-form').textContent).toContain('new');
  });

  it('an existing card opens that request, trailing link punctuation trimmed', async () => {
    const id = '3f1c2a9e-0b7d-4c55-9a1e-2d6f8b0c4e11';
    expect(portalDetailPath('new_product_application', id, null)).toBe(
      `/portal/new_product_application/${id}`,
    );
    const { default: DetailPage } = await import('./[id]/page');
    await act(async () => {
      render(
        <Suspense fallback={null}>
          <DetailPage params={Promise.resolve({ id: `${id}.` })} />
        </Suspense>,
      );
    });
    expect((await screen.findByTestId('npa-form')).textContent).toBe(
      `edit:${id}|slug:`,
    );
  });

  it('the bare list path forwards to the landing filtered to new product applications', async () => {
    const { default: ListPage } = await import('./page');
    render(<ListPage />);
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(
        '/portal?type=new_product_application',
      ),
    );
  });
});

describe('the slug tree reaches the same form', () => {
  it('New opens the form with the slug', async () => {
    const { default: NewPage } =
      await import('../c/[slug]/new_product_application/new/page');
    await act(async () => {
      render(
        <Suspense fallback={null}>
          <NewPage params={Promise.resolve({ slug: 'acme' })} />
        </Suspense>,
      );
    });
    expect((await screen.findByTestId('npa-form')).textContent).toBe(
      'new|slug:acme',
    );
  });

  it('a card opens that application, and the bare list path forwards to the landing', async () => {
    const id = '3f1c2a9e-0b7d-4c55-9a1e-2d6f8b0c4e11';
    const { default: DetailPage } =
      await import('../c/[slug]/new_product_application/[id]/page');
    await act(async () => {
      render(
        <Suspense fallback={null}>
          <DetailPage
            params={Promise.resolve({ slug: 'acme', id: `${id}.` })}
          />
        </Suspense>,
      );
    });
    expect((await screen.findByTestId('npa-form')).textContent).toBe(
      `edit:${id}|slug:acme`,
    );

    const { default: ListPage } =
      await import('../c/[slug]/new_product_application/page');
    await act(async () => {
      render(
        <Suspense fallback={null}>
          <ListPage params={Promise.resolve({ slug: 'acme' })} />
        </Suspense>,
      );
    });
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(
        '/portal/c/acme?type=new_product_application',
      ),
    );
  });
});
