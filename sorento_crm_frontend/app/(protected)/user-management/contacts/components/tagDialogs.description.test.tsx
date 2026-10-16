/**
 * CONTACT-TAGS browser-pass: every new tag dialog carries a DialogDescription / aria-describedby, so
 * Radix does not warn "Missing `Description` or `aria-describedby={undefined}`".
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const svc = vi.hoisted(() => ({
  getContactTags: vi.fn(), bulkTagContacts: vi.fn(), applyTagSettings: vi.fn(), applyContactTagSettings: vi.fn(),
  createContactTag: vi.fn(), updateContactTag: vi.fn(), getContactTag: vi.fn(),
}));
vi.mock('../../contact-tags/services/contactTagService', () => svc);

import BulkTagDialog from './BulkTagDialog';
import ApplyTagSettingsDialog from './ApplyTagSettingsDialog';
import ContactTagFormDialog from '../../contact-tags/components/ContactTagFormDialog';

function qc() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}
const warnings = () => {
  const spies = [vi.spyOn(console, 'warn'), vi.spyOn(console, 'error')];
  return () => spies.flatMap((s) => s.mock.calls.map((c) => String(c[0]))).filter((m) => m.includes('Missing `Description`'));
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('tag dialogs declare a description', () => {
  it('BulkTagDialog', async () => {
    svc.getContactTags.mockResolvedValue({ data: [], pagination: { total: 0, page: 1, limit: 1000, pages: 0 } });
    const found = warnings();
    render(
      <QueryClientProvider client={qc()}>
        <BulkTagDialog open onOpenChange={vi.fn()} mode="add" targetContacts={[{ id: 'c1', phone_number: '1', name: 'A' }] as never[]} />
      </QueryClientProvider>,
    );
    await screen.findByRole('dialog');
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveAttribute('aria-describedby'));
    expect(found()).toEqual([]);
  });

  it('ApplyTagSettingsDialog', async () => {
    svc.applyTagSettings.mockResolvedValue({ dry_run: true, results: [], counts: { changed: 0, unchanged: 0, skipped: 0, failed: 0, conflicts: 0 } });
    const found = warnings();
    render(
      <QueryClientProvider client={qc()}>
        <ApplyTagSettingsDialog open onOpenChange={vi.fn()} contactIds={['c1']} />
      </QueryClientProvider>,
    );
    await screen.findByRole('dialog');
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveAttribute('aria-describedby'));
    expect(found()).toEqual([]);
  });

  it('ContactTagFormDialog', async () => {
    const found = warnings();
    render(
      <QueryClientProvider client={qc()}>
        <ContactTagFormDialog open onOpenChange={vi.fn()} tag={null} />
      </QueryClientProvider>,
    );
    await screen.findByRole('dialog');
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveAttribute('aria-describedby'));
    expect(found()).toEqual([]);
  });
});
