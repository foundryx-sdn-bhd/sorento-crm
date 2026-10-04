/**
 * CONTACT-COMPANYLESS AC8 (FE half): `useCustomerMultiPicker` is shared by the contact card,
 * the sales agent tab and the customer group page. Called the way the OTHER screens call it
 * (no opt-in), it must NOT send `company_scope`, so they keep following the header switcher.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import { useCustomerMultiPicker } from './useCustomerMultiPicker';

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue(
    new Response(
      JSON.stringify({
        data: [{ id: 'c-1', customer_code: 'C-1', customer_name: 'Alpha', company_name: 'Sorento' }],
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ),
  );
});

describe('useCustomerMultiPicker default', () => {
  it('sends no company_scope and keeps the plain "code - name" label', async () => {
    const { result } = renderHook(() => useCustomerMultiPicker(() => false));

    const options = await result.current.fetchOptions('alp');

    const url = new URL(apiFetch.mock.calls[0][0], 'http://localhost');
    expect(url.pathname).toBe('/api/v1/order-management/customers/select');
    expect(url.searchParams.has('company_scope')).toBe(false);
    expect(options.map((o) => o.label)).toEqual(['C-1 - Alpha']);
  });
});
