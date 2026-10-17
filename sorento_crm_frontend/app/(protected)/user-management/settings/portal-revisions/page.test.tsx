/**
 * Settings -> Portal Revisions (UAC A6).
 *
 * Covers the round trip both globals take through the existing general-settings
 * setattr path, and the per-type config row edited via modal. The DataGrid rows
 * render under jsdom once `useListingColumnPreferences` is mocked.
 */
import React, { type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
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

const mockSettings = { portalRevisionsEnabled: true, portalMaxRevisions: 2 };
vi.mock('../components/settings-context', () => ({
  useSettings: () => ({ settings: mockSettings, roles: [] }),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn() },
}));

vi.mock('@/app/(protected)/sla-management/_shared/formSLAService', () => ({
  listFormSLAConfigs: vi.fn().mockResolvedValue([
    { id: 'c1', stage_code: 'project_sales_review' },
    { id: 'c2', stage_code: 'purchasing_response' },
  ]),
}));

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));

// Both saves need `user_management.settings.edit` (SETTINGS-PERM-GATE). The caller
// holds it unless a test takes it away.
const EDIT = 'user_management.settings.edit';
const granted = new Set<string>([EDIT]);
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => granted.has(slug),
}));

import PortalRevisionsSettingsPage from './page';

const CONFIGS = [
  {
    source_entity_type: 'stock_inquiry',
    is_enabled: true,
    max_revisions: null,
    allowed_statuses: ['pending_project_sales', 'pending_purchasing', 'responded'],
    restart_stage_code: null,
  },
  {
    source_entity_type: 'complaint',
    is_enabled: false,
    max_revisions: 0,
    allowed_statuses: [],
    restart_stage_code: null,
  },
];

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

/** Route the two calls this page makes: the config list and the settings save. */
function mockApi() {
  apiFetch.mockImplementation(async (url: string) => {
    if (url === '/api/v1/forms-management/revision-configs') {
      return { ok: true, json: async () => ({ items: CONFIGS }) };
    }
    return { ok: true, json: async () => ({}) };
  });
}

beforeEach(() => {
  cleanup();
  apiFetch.mockReset();
  granted.clear();
  granted.add(EDIT);
  mockSettings.portalRevisionsEnabled = true;
  mockSettings.portalMaxRevisions = 2;
});

describe('PortalRevisionsSettingsPage globals', () => {
  it('reflects the saved globals', () => {
    mockApi();
    mockSettings.portalRevisionsEnabled = false;
    mockSettings.portalMaxRevisions = 5;
    wrap(<PortalRevisionsSettingsPage />);
    expect(screen.getByLabelText('Enabled').getAttribute('aria-checked')).toBe('false');
    expect((screen.getByLabelText('Max revisions') as HTMLInputElement).value).toBe('5');
  });

  it('saves both globals as snake_case through the general settings path', async () => {
    mockApi();
    wrap(<PortalRevisionsSettingsPage />);

    fireEvent.click(screen.getByLabelText('Enabled'));
    fireEvent.change(screen.getByLabelText('Max revisions'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      const save = apiFetch.mock.calls.find(
        ([url]) => url === '/api/user-management/settings/general',
      );
      expect(save).toBeTruthy();
      expect(JSON.parse(save![1].body)).toEqual({
        portal_revisions_enabled: false,
        portal_max_revisions: 4,
      });
    });
  });

  it('refuses to save an emptied cap with a sentence rather than posting garbage', () => {
    mockApi();
    wrap(<PortalRevisionsSettingsPage />);
    fireEvent.change(screen.getByLabelText('Max revisions'), { target: { value: '' } });
    const save = screen.getByRole('button', { name: /save settings/i });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(screen.getByText('Enter a whole number between 0 and 50.')).toBeInTheDocument();
    expect(
      apiFetch.mock.calls.find(([url]) => url === '/api/user-management/settings/general'),
    ).toBeUndefined();
  });

  it('keeps a per-type cap over the limit as typed and blocks the save with a sentence', async () => {
    mockApi();
    wrap(<PortalRevisionsSettingsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Stock Inquiry' }));
    const maxField = await screen.findByLabelText('Max revisions', { selector: '#portal-revision-max' });
    fireEvent.change(maxField, { target: { value: '51' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save revision policy' }));
    expect(maxField).toHaveValue(51);
    expect(
      screen.getByText('Enter a whole number between 0 and 50, or leave it blank.'),
    ).toBeInTheDocument();
    expect(
      apiFetch.mock.calls.find(([url]) => url === '/api/v1/forms-management/revision-configs/stock_inquiry'),
    ).toBeUndefined();
  });

  it('Reset restores the saved values', () => {
    mockApi();
    wrap(<PortalRevisionsSettingsPage />);
    fireEvent.change(screen.getByLabelText('Max revisions'), { target: { value: '9' } });
    fireEvent.click(screen.getByRole('button', { name: /reset/i }));
    expect((screen.getByLabelText('Max revisions') as HTMLInputElement).value).toBe('2');
  });
});

describe('PortalRevisionsSettingsPage per-type table', () => {
  it('lists one row per portal type with its policy', async () => {
    mockApi();
    wrap(<PortalRevisionsSettingsPage />);
    expect(await screen.findByText('Stock Inquiry')).toBeInTheDocument();
    expect(screen.getByText('Complaint')).toBeInTheDocument();
    // NULL max reads as inheriting the global, not as zero.
    expect(screen.getByText('Global')).toBeInTheDocument();
    expect(
      screen.getByText('Pending project sales, Pending purchasing, Responded'),
    ).toBeInTheDocument();
    // NULL restart stage reads as the first stage of the chain.
    expect(screen.getAllByText('First stage')).toHaveLength(2);
  });

  it('shows the enabled state per type', async () => {
    mockApi();
    wrap(<PortalRevisionsSettingsPage />);
    await screen.findByText('Stock Inquiry');
    expect(screen.getByText('On')).toBeInTheDocument();
    expect(screen.getByText('Off')).toBeInTheDocument();
  });

  it('edits a config through the modal and PUTs the whole policy', async () => {
    mockApi();
    wrap(<PortalRevisionsSettingsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Stock Inquiry' }));

    const maxField = await screen.findByLabelText('Max revisions', { selector: '#portal-revision-max' });
    fireEvent.change(maxField, { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save revision policy' }));

    await waitFor(() => {
      const put = apiFetch.mock.calls.find(
        ([url]) => url === '/api/v1/forms-management/revision-configs/stock_inquiry',
      );
      expect(put).toBeTruthy();
      expect(put![1].method).toBe('PUT');
      expect(JSON.parse(put![1].body)).toEqual({
        is_enabled: true,
        max_revisions: 3,
        allowed_statuses: ['pending_project_sales', 'pending_purchasing', 'responded'],
        restart_stage_code: null,
      });
    });
  });

  it('sends a null max when the field is cleared, so the type inherits the global', async () => {
    mockApi();
    wrap(<PortalRevisionsSettingsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Complaint' }));

    const maxField = await screen.findByLabelText('Max revisions', { selector: '#portal-revision-max' });
    expect((maxField as HTMLInputElement).value).toBe('0');
    fireEvent.change(maxField, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save revision policy' }));

    await waitFor(() => {
      const put = apiFetch.mock.calls.find(
        ([url]) => url === '/api/v1/forms-management/revision-configs/complaint',
      );
      expect(put).toBeTruthy();
      expect(JSON.parse(put![1].body).max_revisions).toBeNull();
    });
  });
});

describe('PortalRevisionsSettingsPage without the edit slug', () => {
  it('offers no save and no row edit, and the globals are read-only', async () => {
    granted.clear();
    granted.add('user_management.settings.view');
    mockApi();
    wrap(<PortalRevisionsSettingsPage />);

    expect(await screen.findByText('Stock Inquiry')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /save settings/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /reset/i })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit Stock Inquiry' })).toBeNull();
    expect(screen.getByLabelText('Enabled')).toBeDisabled();
    expect(screen.getByLabelText('Max revisions')).toBeDisabled();
  });
});
