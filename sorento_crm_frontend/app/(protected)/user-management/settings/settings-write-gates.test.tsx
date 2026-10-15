/**
 * SETTINGS-PERM-GATE: the SMTP, Notifications and Social tabs show their save
 * actions only to a caller the server will let save.
 *
 * The three writes behind them used to check only "signed in". They now need
 * `user_management.settings.secrets` (SMTP save and test send) and
 * `user_management.settings.edit` (Notifications, Social), so a caller without
 * the slug must not be offered a button that answers 403. The fields stay
 * visible and read-only, the way Integrations keeps its secrets cards to the
 * slug holder.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
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

const EDIT = 'user_management.settings.edit';
const VIEW = 'user_management.settings.view';
const SECRETS = 'user_management.settings.secrets';

const granted = new Set<string>();
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => granted.has(slug),
}));

const mockSettings: Record<string, unknown> = {
  id: 's1',
  smtp: {
    smtp_host: 'smtp.example.test',
    smtp_port: '587',
    smtp_secure: true,
    smtp_username: 'mailer',
    smtp_from: 'noreply@example.test',
    smtp_password: null,
  },
  socialFacebook: 'https://facebook.example.test/page',
  notifyStockEmail: false,
  notifyStockWeb: false,
  notifyStockRoleIds: [],
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

vi.mock('./components/settings-context', () => ({
  useSettings: () => ({ settings: mockSettings, roles: [{ id: 'role-1', name: 'Sales' }] }),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/settings',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn(), dismiss: vi.fn() },
}));

const apiFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));

vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn().mockResolvedValue({ config: null }),
  upsertUserListColumnConfig: vi.fn().mockResolvedValue({ config: null }),
  resetUserListColumnConfig: vi.fn().mockResolvedValue({ config: null }),
}));

import SmtpSettingsPage from './smtp/page';
import NotificationSettingsPage from './notifications/page';
import SocialSettingsPage from './social/page';

function renderPage(Page: React.ComponentType) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Page />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  granted.clear();
  apiFetch.mockClear();
});

afterEach(() => {
  cleanup();
});

describe('Settings > SMTP', () => {
  it('offers no save and no test send without the secrets slug, even to a settings editor', () => {
    granted.add(VIEW);
    granted.add(EDIT);
    renderPage(SmtpSettingsPage);

    expect(screen.queryByRole('button', { name: /save smtp settings/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /test connection/i })).toBeNull();
    expect(screen.getByDisplayValue('smtp.example.test')).toBeDisabled();
  });

  it('offers save and test send to the secrets slug holder', () => {
    granted.add(SECRETS);
    renderPage(SmtpSettingsPage);

    expect(screen.getByRole('button', { name: /save smtp settings/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /test connection/i })).toBeEnabled();
    expect(screen.getByDisplayValue('smtp.example.test')).toBeEnabled();
  });
});

describe('Settings > Notifications', () => {
  it('offers no save without the edit slug', async () => {
    granted.add(VIEW);
    renderPage(NotificationSettingsPage);

    const boxes = await screen.findAllByRole('checkbox');
    expect(screen.queryByRole('button', { name: /save settings/i })).toBeNull();
    for (const box of boxes) {
      expect(box).toBeDisabled();
    }
  });

  it('offers save to the edit slug holder', async () => {
    granted.add(EDIT);
    renderPage(NotificationSettingsPage);

    const boxes = await screen.findAllByRole('checkbox');
    expect(screen.getByRole('button', { name: /save settings/i })).toBeInTheDocument();
    expect(boxes[0]).toBeEnabled();
  });
});

describe('Settings > Social', () => {
  it('offers no save without the edit slug', () => {
    granted.add(VIEW);
    renderPage(SocialSettingsPage);

    expect(screen.queryByRole('button', { name: /save settings/i })).toBeNull();
    expect(screen.getByDisplayValue('https://facebook.example.test/page')).toBeDisabled();
  });

  it('offers save to the edit slug holder', () => {
    granted.add(EDIT);
    renderPage(SocialSettingsPage);

    expect(screen.getByRole('button', { name: /save settings/i })).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://facebook.example.test/page')).toBeEnabled();
  });
});
