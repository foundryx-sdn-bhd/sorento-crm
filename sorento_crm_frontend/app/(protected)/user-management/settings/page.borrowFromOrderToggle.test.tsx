/**
 * Settings -> General -> "Borrow from another order" switch (BORROW-LOCATION-ONLY D1,
 * `documentation/plans/scm/PLAN-borrow-location-only-8oct.md`).
 *
 * RED for Phase 2: `page.tsx` has no Switch bound to `borrowFromOrderEnabled` yet, so
 * `screen.getByRole('switch', { name: ... })` finds nothing and every assertion fails
 * against TODAY's code, not a fixture bug. Off by default; the label carries the whole UI
 * (no on-screen explanation rule). Harness copied from `page.localBuyToggle.test.tsx`.
 */
import React, { type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}
Element.prototype.scrollIntoView = vi.fn();

const mockSettings: Record<string, unknown> = {
  id: 's1',
  name: 'Sorento',
  active: true,
  supportEmail: 'ops@sorento.test',
  language: 'en',
  timezone: 'Asia/Kuala_Lumpur',
  currency: 'MYR',
  currencyFormat: 'RM {value}',
  defaultUomId: null,
  deferredDeleteSeconds: 10,
  deferredActionSeconds: 5,
  localBuyRoutingEnabled: false,
  borrowFromOrderEnabled: false,
};
vi.mock('./components/settings-context', () => ({
  useSettings: () => ({ settings: mockSettings, roles: [] }),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn() },
}));

vi.mock(
  '@/app/(protected)/procurement-management/purchase-requests/services/purchaseRequestService',
  () => ({ getUsersForApproverSelect: vi.fn().mockResolvedValue([]) }),
);

// The units master, as `GET /master-data/units-of-measure/select` serves it.
vi.mock('@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query', () => ({
  useUOMSelectQuery: () => ({
    data: [
      { id: 'uom-ea', uom_code: 'EA', uom_name: 'Each' },
      { id: 'uom-l', uom_code: 'L', uom_name: 'Litre' },
      { id: 'uom-ctn', uom_code: 'CTN', uom_name: 'Carton' },
    ],
    isLoading: false,
  }),
}));

// Stubbed as a deterministic native control (the same technique as
// `page.deferredWindows.test.tsx`), so picking an option is a plain `fireEvent.change`
// rather than a Radix popover interaction in jsdom.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
  }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select
      aria-label={placeholder ?? 'select'}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));

import SettingsGeneralPage from './page';

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

/** The body of the save this page POSTs, once it has been made. */
async function savedBody(): Promise<Record<string, unknown>> {
  const call = await waitFor(() => {
    const found = apiFetch.mock.calls.find(
      ([url]) => url === '/api/user-management/settings/general',
    );
    if (!found) throw new Error('the page has not saved yet');
    return found;
  });
  return JSON.parse((call[1] as { body: string }).body);
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockSettings.borrowFromOrderEnabled = false;
  apiFetch.mockImplementation(async (url: string) => {
    if (url === '/api/procurement/suppliers/select') {
      return { ok: true, json: async () => [] };
    }
    return { ok: true, json: async () => ({ message: 'ok' }) };
  });
});

const LABEL = 'Borrow from another order';

const borrowSwitch = () => screen.getByRole('switch', { name: LABEL });

describe('Settings - Borrow from another order switch (D1)', () => {
  it('renders the switch, off, when the setting is false', () => {
    wrap(<SettingsGeneralPage />);

    const control = borrowSwitch();
    expect(control).toBeInTheDocument();
    expect(control).toHaveAttribute('aria-checked', 'false');
  });

  it('shows the switch ON when the stored setting is true', () => {
    mockSettings.borrowFromOrderEnabled = true;
    wrap(<SettingsGeneralPage />);

    expect(borrowSwitch()).toHaveAttribute('aria-checked', 'true');
  });

  it('carries no description text under the switch (no on-screen explanation rule)', () => {
    wrap(<SettingsGeneralPage />);

    const formItem = borrowSwitch().closest('[data-slot="form-item"]');
    expect(formItem).not.toBeNull();
    expect(formItem?.querySelector('[data-slot="form-description"]')).toBeNull();
  });

  it('sends borrow_from_order_enabled: true in the save body once toggled on', async () => {
    wrap(<SettingsGeneralPage />);

    fireEvent.click(borrowSwitch());
    fireEvent.click(screen.getByRole('button', { name: /Save/i }));

    const saved = await savedBody();
    expect(saved.borrow_from_order_enabled).toBe(true);
  });
});
