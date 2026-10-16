/**
 * CONTACT-TAGS review R6: after "Add tag to N contacts" with Apply tag settings now, the dialog shows a
 * result step (tag counts, settings counts, failed rows with reasons, conflicts, not applied) and stays
 * open instead of closing on a tag-only toast. Rows carry `settings: { status, error, changes,
 * conflicts, not_applied }`. Result container test id `bulk-tag-result`.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const tagSvc = vi.hoisted(() => ({ getContactTags: vi.fn(), bulkTagContacts: vi.fn() }));
vi.mock('../../contact-tags/services/contactTagService', () => tagSvc);
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: { value: string[]; onChange: (v: string[]) => void;
    options?: { value: string; label: string }[] }) => (
    <div>
      {(props.options ?? []).map((o) => (
        <button key={o.value} type="button" onClick={() => props.onChange([...props.value, o.value])}>{o.label}</button>
      ))}
    </div>
  ),
}));

import BulkTagDialog from './BulkTagDialog';

const targets = [
  { id: 'c1', phone_number: '6011', name: 'Alice' },
  { id: 'c2', phone_number: '6012', name: 'Bob' },
] as never[];

const rows = (dryRun: boolean) => ({
  dry_run: dryRun,
  results: [
    { contact_id: 'c1', label: 'Alice', status: 'changed', tags_before: [], tags_after: ['Dealer'], error: null,
      settings: { status: 'failed', error: 'Write blew up', changes: [], conflicts: [], not_applied: [] } },
    { contact_id: 'c2', label: 'Bob', status: 'changed', tags_before: [], tags_after: ['Dealer'], error: null,
      settings: { status: 'changed', error: null,
        changes: [{ section: 'chatbot', key: 'k', label: 'Stock checks', before: true, after: false, decided_by: ['Dealer'], added: [], removed: [] }],
        conflicts: [{ label: 'Skip period questions', tags: [{ name: 'Dealer', value: true }, { name: 'Retail', value: false }] }],
        not_applied: [{ label: 'Chatbot agents', reason: 'Needs access agents edit.' }] } },
  ],
  counts: { changed: 2, unchanged: 0, failed: 0 },
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('BulkTagDialog result step', () => {
  it('shows what the apply did and stays open', async () => {
    tagSvc.getContactTags.mockResolvedValue({
      data: [{ id: 'tag-a', name: 'Dealer', description: null, is_active: true, sort_order: 0, settings: {},
        contact_count: 0, created_at: '', updated_at: '' }],
      pagination: { total: 1, page: 1, limit: 1000, pages: 1 },
    });
    tagSvc.bulkTagContacts.mockResolvedValueOnce(rows(true)).mockResolvedValueOnce(rows(false));
    const onOpenChange = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <BulkTagDialog open onOpenChange={onOpenChange} mode="add" targetContacts={targets} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Dealer' }));
    fireEvent.click(screen.getByRole('button', { name: /^(continue|preview)/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add tag to 2 contacts' }));

    const result = await screen.findByTestId('bulk-tag-result');
    expect(within(result).getByText(/Write blew up/)).toBeInTheDocument();
    expect(result).toHaveTextContent('Skip period questions');
    expect(result).toHaveTextContent('Chatbot agents');
    expect(result).toHaveTextContent('Needs access agents edit.');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await waitFor(() => expect(onOpenChange).not.toHaveBeenCalledWith(false));
    expect(screen.getByRole('button', { name: /close|done/i })).not.toBeDisabled();
  });
});
