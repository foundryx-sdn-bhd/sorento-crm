/**
 * Staff impersonation keeps the portal on the legacy slug-less tree (`/portal`, see
 * app/(auth)/portal/page.tsx). From that landing, New builds `/portal/sample_request/new`
 * (portalNewPath with no slug) and a card opens `/portal/sample_request/<id>`. Without their own
 * routes those paths fell through to the generic `[type]` pages and 404'd, because
 * `sample_request` is not a generic submission kind. Same wrappers as price_tag_request/.
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

vi.mock('../components/SampleRequestForm', () => ({
  __esModule: true,
  default: (props: { requestId?: string; slug?: string }) => (
    <div data-testid="sample-request-form">
      {props.requestId ? `edit:${props.requestId}` : 'new'}|slug:{String(props.slug ?? '')}
    </div>
  ),
}));

import { portalDetailPath, portalNewPath } from '../lib/portal-paths';

beforeEach(() => {
  replace.mockClear();
  window.localStorage.clear();
});

describe('impersonated /portal (no slug) reaches the sample request form', () => {
  it('New from the landing opens the form, not a 404', async () => {
    expect(portalNewPath('sample_request', null)).toBe('/portal/sample_request/new');
    const { default: NewPage } = await import('./new/page');
    render(<NewPage />);
    expect(screen.getByTestId('sample-request-form').textContent).toContain('new');
  });

  it('an existing card opens that request, trailing link punctuation trimmed', async () => {
    const id = '3f1c2a9e-0b7d-4c55-9a1e-2d6f8b0c4e11';
    expect(portalDetailPath('sample_request', id, null)).toBe(`/portal/sample_request/${id}`);
    const { default: DetailPage } = await import('./[id]/page');
    await act(async () => {
      render(
        <Suspense fallback={null}>
          <DetailPage params={Promise.resolve({ id: `${id}.` })} />
        </Suspense>,
      );
    });
    expect((await screen.findByTestId('sample-request-form')).textContent).toBe(
      `edit:${id}|slug:`,
    );
  });

  it('the bare list path forwards to the landing filtered to sample requests', async () => {
    const { default: ListPage } = await import('./page');
    render(<ListPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/portal?type=sample_request'));
  });
});
