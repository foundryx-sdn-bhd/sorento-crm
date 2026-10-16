/**
 * CONTACT-TAGS review N4: "Apply to N contacts" counts only the rows that will change
 * (singular "Apply to 1 contact"); with nothing to change there is no apply button, only a readable state.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const svc = vi.hoisted(() => ({ applyTagSettings: vi.fn(), applyContactTagSettings: vi.fn() }));
vi.mock('../../contact-tags/services/contactTagService', () => svc);

import ApplyTagSettingsDialog from './ApplyTagSettingsDialog';

const row = (id: string, status: string) => ({
  contact_id: id, label: id, status, error: null,
  changes: status === 'changed' ? [{ section: 'chatbot', key: 'k', label: 'Stock checks', before: true, after: false, decided_by: ['T'], added: [], removed: [] }] : [],
  conflicts: [], not_applied: [],
});

function open(results: ReturnType<typeof row>[]) {
  svc.applyTagSettings.mockResolvedValue({
    dry_run: true, results,
    counts: { changed: results.filter((r) => r.status === 'changed').length, unchanged: 0, skipped: 0, failed: 0, conflicts: 0 },
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ApplyTagSettingsDialog open onOpenChange={vi.fn()} contactIds={results.map((r) => r.contact_id)} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ApplyTagSettingsDialog apply button count', () => {
  it('counts only the changing rows', async () => {
    open([row('a', 'changed'), row('b', 'changed'), row('c', 'unchanged'), row('d', 'skipped'), row('e', 'failed')]);
    expect(await screen.findByRole('button', { name: 'Apply to 2 contacts' })).toBeInTheDocument();
  });

  it('has no apply button when no row will change', async () => {
    open([row('a', 'unchanged'), row('b', 'skipped')]);
    await screen.findByTestId('apply-tag-counts');
    expect(screen.queryByRole('button', { name: /^Apply to/ })).toBeNull();
  });
});
