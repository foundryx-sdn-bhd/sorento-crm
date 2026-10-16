/**
 * CONTACT-TAGS browser-pass D1: under React.StrictMode (effects double-mounted, as in `next dev`) the
 * preview must still render and "Apply to N contacts" must become enabled; Retry must work after a
 * rejected first call.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const svc = vi.hoisted(() => ({ applyTagSettings: vi.fn(), applyContactTagSettings: vi.fn() }));
vi.mock('../../contact-tags/services/contactTagService', () => svc);

import ApplyTagSettingsDialog from './ApplyTagSettingsDialog';

const answer = {
  dry_run: true,
  results: [{
    contact_id: 'c1', label: 'Alice', status: 'changed', error: null,
    changes: [{ section: 'portal_forms', key: 'purchase_request', label: 'Portal form: Purchase Request',
      before: true, after: false, decided_by: ['Retail dealer'], added: [], removed: [] }],
    conflicts: [], not_applied: [],
  }],
  counts: { changed: 1, unchanged: 0, skipped: 0, failed: 0, conflicts: 0 },
};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <React.StrictMode>
      <QueryClientProvider client={qc}>
        <ApplyTagSettingsDialog open onOpenChange={vi.fn()} contactIds={['c1']} />
      </QueryClientProvider>
    </React.StrictMode>,
  );
}

afterEach(() => {
  cleanup();
  svc.applyTagSettings.mockReset();
});

describe('ApplyTagSettingsDialog under StrictMode', () => {
  it('renders the preview and enables Apply', async () => {
    svc.applyTagSettings.mockResolvedValue(answer);
    mount();
    expect(await screen.findByTestId('apply-tag-row-c1')).toBeInTheDocument();
    const apply = await screen.findByRole('button', { name: 'Apply to 1 contact' });
    expect(apply).not.toBeDisabled();
    expect(screen.queryByText(/Reading the tag settings/)).toBeNull();
  });

  it('Retry works after a rejected first call', async () => {
    svc.applyTagSettings.mockRejectedValueOnce(new Error('Server went away.')).mockResolvedValue(answer);
    mount();
    expect(await screen.findByText(/Server went away\./)).toBeInTheDocument();
    const retry = screen.getByRole('button', { name: 'Retry' });
    expect(retry).not.toBeDisabled();
    fireEvent.click(retry);
    expect(await screen.findByTestId('apply-tag-row-c1')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Apply to 1 contact' })).not.toBeDisabled());
  });
});
