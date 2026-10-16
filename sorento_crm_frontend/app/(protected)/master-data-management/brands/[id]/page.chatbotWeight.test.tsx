/**
 * R1 (owner console test of round 3 on PR #833, 27 Sep 2026): "i want weights as brand
 * preference instead of switch". Each brand carries a chatbot weight; the chatbot answers
 * the highest weighted brand first and names the others in weight order. Edited on the
 * brand RECORD page (Master Data > Brands > a brand > Edit).
 *
 * Pattern borrowed from `../../../inventory-management/warehouses/[id]/page.test.tsx`:
 * the page resolves `params` with `use()`, which suspends on the first render, and
 * `Container` pulls `SettingsProvider` context this unit test does not mount.
 */
import React, { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  act,
  waitFor,
} from '@testing-library/react';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';

import type { Brand } from '../../products/types/product.types';

const BRAND_ID = '11111111-1111-4111-8111-111111111111';

const h = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  brand: undefined as Brand | undefined,
}));

function brand(over: Partial<Brand> = {}): Brand {
  return {
    id: BRAND_ID,
    brand_code: 'TPE',
    brand_name: 'TP Enterprise',
    description: null,
    is_active: true,
    access_levels: [],
    flows_to_purchasing: true,
    chatbot_weight: 0,
    created_at: new Date('2026-01-05T02:00:00Z'),
    updated_at: new Date('2026-02-09T04:30:00Z'),
    product_count: 4,
    ...over,
  };
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: vi.fn(),
    back: vi.fn(),
    replace: vi.fn(),
    refresh: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(''),
  usePathname: () => `/master-data-management/brands/${BRAND_ID}`,
}));

// Container pulls SettingsProvider context this unit test does not need.
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

vi.mock('../hooks/useBrands', () => ({
  useBrand: () => ({ data: h.brand, isLoading: false }),
  useUpdateBrand: () => ({ mutateAsync: h.updateMutate, isPending: false }),
}));

vi.mock(
  '@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes',
  () => ({
    useContactAccessTypes: () => ({ data: [] }),
  }),
);

// `useDeferredAction` (delete) watches from mount, which reads this on every render.
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi
    .fn()
    .mockResolvedValue({ pending: null, last_outcome: null }),
}));

import BrandDetailPage from './page';

async function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback={<div>Loading route</div>}>
          <BrandDetailPage params={Promise.resolve({ id: BRAND_ID })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

async function startEdit() {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
  });
}

beforeEach(() => {
  h.updateMutate.mockReset().mockResolvedValue(undefined);
  h.brand = brand();
});

describe('Brand detail - Chatbot brand weight (R1)', () => {
  it('view mode shows the stored weight', async () => {
    h.brand = brand({ chatbot_weight: 1.5 });
    await renderDetail();

    const page = (document.body.textContent ?? '').replace(/\s+/g, ' ');
    expect(page).toMatch(/Chatbot brand weight\s*1\.5/);
    expect(page).not.toMatch(/Chatbot default brand/);
  });

  it('view mode shows 0 for an unweighted brand', async () => {
    h.brand = brand({ chatbot_weight: 0 });
    await renderDetail();

    const page = (document.body.textContent ?? '').replace(/\s+/g, ' ');
    expect(page).toMatch(/Chatbot brand weight\s*0/);
  });

  it('edit mode: the number input holds the stored weight', async () => {
    h.brand = brand({ chatbot_weight: 0.5 });
    await renderDetail();
    await startEdit();

    const input = screen.getByLabelText('Chatbot brand weight') as HTMLInputElement;
    expect(input.type).toBe('number');
    expect(input.value).toBe('0.5');
  });

  it('typing a weight sends it as a number', async () => {
    h.brand = brand({ chatbot_weight: 0 });
    await renderDetail();
    await startEdit();

    fireEvent.change(screen.getByLabelText('Chatbot brand weight'), {
      target: { value: '1.5' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save brand/i }));

    await waitFor(() => expect(h.updateMutate).toHaveBeenCalled());
    const [{ data: payload }] = h.updateMutate.mock.calls[0];
    expect(payload.chatbot_weight).toBe(1.5);
    expect(payload).not.toHaveProperty('is_chatbot_default');
  });

  it('an untouched save keeps the stored weight', async () => {
    h.brand = brand({ chatbot_weight: 0.1 });
    await renderDetail();
    await startEdit();

    fireEvent.click(screen.getByRole('button', { name: /save brand/i }));

    await waitFor(() => expect(h.updateMutate).toHaveBeenCalled());
    const [{ data: payload }] = h.updateMutate.mock.calls[0];
    expect(payload.chatbot_weight).toBe(0.1);
  });

  // PR #833 round 5 N3: the server takes 0 to 9999, so the record page does too. The figure
  // stays as typed; the check runs on Save.
  it('edit mode: the input is bounded to 0 to 9999 and 10000 cannot be saved', async () => {
    h.brand = brand({ chatbot_weight: 0 });
    await renderDetail();
    await startEdit();

    const input = screen.getByLabelText('Chatbot brand weight') as HTMLInputElement;
    expect(input.min).toBe('0');
    expect(input.max).toBe('9999');

    fireEvent.change(input, { target: { value: '10000' } });
    expect(input.value).toBe('10000');
    expect(screen.getByRole('button', { name: /save brand/i })).not.toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /save brand/i }));
    expect(screen.getByText('Enter 9999 or less')).toBeInTheDocument();
    expect(h.updateMutate).not.toHaveBeenCalled();
  });

  it('edit mode: a blank weight is refused with a message, never saved as 0', async () => {
    h.brand = brand({ chatbot_weight: 3 });
    await renderDetail();
    await startEdit();

    const input = screen.getByLabelText('Chatbot brand weight') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save brand/i }));

    expect(screen.getByText(/^Enter a number between 0 and 9999/)).toBeInTheDocument();
    expect(h.updateMutate).not.toHaveBeenCalled();
  });

  // PR #833 round 5 pass N-r5-1: the record page says why, in the dialog's own words, once
  // Save has been pressed, and the message follows the field from then on.
  it('edit mode: an out of range weight says the same words as the dialog', async () => {
    h.brand = brand({ chatbot_weight: 0 });
    await renderDetail();
    await startEdit();

    const input = screen.getByLabelText('Chatbot brand weight') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '10000' } });
    expect(screen.queryByText('Enter 9999 or less')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /save brand/i }));
    expect(screen.getByText('Enter 9999 or less')).toBeInTheDocument();

    fireEvent.change(input, { target: { value: '-1' } });
    expect(screen.getByText('Enter 0 or more')).toBeInTheDocument();
    expect(screen.queryByText('Enter 9999 or less')).toBeNull();

    fireEvent.change(input, { target: { value: '5' } });
    expect(screen.queryByText('Enter 0 or more')).toBeNull();
    expect(screen.queryByText('Enter 9999 or less')).toBeNull();
  });
});
