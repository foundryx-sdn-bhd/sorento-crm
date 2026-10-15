/**
 * Settings > Notifications: ticking a box settles.
 *
 * Clicking either checkbox on any row hung the tab - the renderer pegged at
 * ~110% CPU with a completely clean console, so not an exception, a runaway
 * re-render (M5 run 2 evidence, finding 1). The one non-standard read in the
 * table is a `form.watch(...)` called from inside a cell renderer, which
 * subscribes the PAGE to every value change and then re-runs on every render
 * that subscription causes.
 *
 * This holds the cheap invariant a table of checkboxes owes: a click costs a
 * bounded number of commits, and two clicks end where they started.
 */
import React, { type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
  notifyStockEmail: false,
  notifyStockWeb: false,
  notifyStockRoleIds: ['role-1'],
  notifyNewOrderEmail: false,
  notifyNewOrderWeb: false,
  notifyNewOrderRoleIds: [],
  notifyOrderStatusUpdateEmail: false,
  notifyOrderStatusUpdateWeb: false,
  notifyOrderStatusUpdateRoleIds: [],
  notifyPaymentFailureEmail: false,
  notifyPaymentFailureWeb: false,
  notifyPaymentFailureRoleIds: [],
  notifySystemErrorFailureEmail: false,
  notifySystemErrorWeb: false,
  notifySystemErrorRoleIds: [],
};

const mockRoles = [
  { id: 'role-1', name: 'Sales' },
  { id: 'role-2', name: 'Operations' },
];

// Save is offered only to the edit slug holder (SETTINGS-PERM-GATE); this file is
// about the checkboxes, so the caller holds it.
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => slug === 'user_management.settings.edit',
}));

vi.mock('../components/settings-context', () => ({
  useSettings: () => ({ settings: mockSettings, roles: mockRoles }),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/settings',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn() },
}));

const apiFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));

vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn().mockResolvedValue({ config: null }),
  upsertUserListColumnConfig: vi.fn().mockResolvedValue({ config: null }),
  resetUserListColumnConfig: vi.fn().mockResolvedValue({ config: null }),
}));

import NotificationSettingsPage from './page';

/**
 * The commit counter. `React.Profiler` fires once per commit of the subtree it
 * wraps, so a runaway re-render shows up here as a number that keeps climbing
 * where a settled tree shows a handful. Nothing inside the page is stubbed for
 * it - the real `Checkbox`, the real `SearchableMultiSelect` and the real
 * DataGrid all take part.
 */
const commits = { count: 0 };

function Page({ children }: { children?: ReactNode }) {
  const client = React.useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }),
    [],
  );
  return (
    <QueryClientProvider client={client}>
      <React.Profiler
        id="notifications"
        onRender={() => {
          commits.count += 1;
        }}
      >
        {children ?? <NotificationSettingsPage />}
      </React.Profiler>
    </QueryClientProvider>
  );
}

function checkboxes(): HTMLElement[] {
  return screen.getAllByRole('checkbox');
}

function isChecked(element: HTMLElement): boolean {
  return element.getAttribute('aria-checked') === 'true';
}

beforeEach(() => {
  commits.count = 0;
  apiFetch.mockClear();
});

afterEach(() => {
  cleanup();
});

describe('Settings > Notifications checkboxes', () => {
  it('settles after a click instead of committing forever', async () => {
    render(<Page />);

    await waitFor(() => expect(checkboxes()).toHaveLength(10)); // 5 rows x (Email, Web)

    commits.count = 0;
    fireEvent.click(checkboxes()[0]);

    await waitFor(() => expect(isChecked(checkboxes()[0])).toBe(true));

    // A settled tree commits a handful of times for one click. The runaway
    // version never stops.
    expect(commits.count).toBeLessThan(20);
  });

  it('ends where it started after two clicks, and keeps the other boxes alone', async () => {
    render(<Page />);
    await waitFor(() => expect(checkboxes()).toHaveLength(10));

    fireEvent.click(checkboxes()[0]);
    await waitFor(() => expect(isChecked(checkboxes()[0])).toBe(true));
    expect(isChecked(checkboxes()[1])).toBe(false);

    fireEvent.click(checkboxes()[0]);
    await waitFor(() => expect(isChecked(checkboxes()[0])).toBe(false));
    expect(isChecked(checkboxes()[1])).toBe(false);
  });

  it('keeps showing the roles picked for a row while a box is toggled', async () => {
    render(<Page />);
    await waitFor(() => expect(checkboxes()).toHaveLength(10));

    expect(screen.getByText('Sales')).toBeInTheDocument();

    fireEvent.click(checkboxes()[0]);
    await waitFor(() => expect(isChecked(checkboxes()[0])).toBe(true));

    expect(screen.getByText('Sales')).toBeInTheDocument();
  });

  it('submits the toggled value with the same payload shape', async () => {
    render(<Page />);
    await waitFor(() => expect(checkboxes()).toHaveLength(10));

    fireEvent.click(checkboxes()[0]);
    await waitFor(() => expect(isChecked(checkboxes()[0])).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const [url, init] = apiFetch.mock.calls[0] as [string, { body: string }];
    expect(url).toBe('/api/user-management/settings/notifications');
    expect(JSON.parse(init.body)).toMatchObject({
      notifyStockEmail: true,
      notifyStockRoleIds: ['role-1'],
    });
  });
});
