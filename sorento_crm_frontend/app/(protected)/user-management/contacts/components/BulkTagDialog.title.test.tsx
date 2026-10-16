/**
 * CONTACT-TAGS browser-pass: after confirming "Add tag to N contacts" the dialog title reads like a
 * result ("Tags added to 2 contacts"), not "Preview: add tag to 0 contacts".
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
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
    { contact_id: 'c1', label: 'Alice', status: 'changed', tags_before: [], tags_after: ['Dealer'], error: null },
    { contact_id: 'c2', label: 'Bob', status: 'changed', tags_before: [], tags_after: ['Dealer'], error: null },
  ],
  counts: { changed: 2, unchanged: 0, failed: 0 },
});

afterEach(() => {
  cleanup();
  tagSvc.bulkTagContacts.mockReset();
});

it('the result step is titled as a result', async () => {
  tagSvc.getContactTags.mockResolvedValue({
    data: [{ id: 'tag-a', name: 'Dealer', description: null, is_active: true, sort_order: 0, settings: {},
      contact_count: 0, created_at: '', updated_at: '' }],
    pagination: { total: 1, page: 1, limit: 1000, pages: 1 },
  });
  tagSvc.bulkTagContacts.mockResolvedValueOnce(rows(true)).mockResolvedValueOnce(rows(false));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BulkTagDialog open onOpenChange={vi.fn()} mode="add" targetContacts={targets} />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Dealer' }));
  fireEvent.click(screen.getByRole('button', { name: /^(continue|preview)/i }));
  fireEvent.click(await screen.findByRole('button', { name: 'Add tag to 2 contacts' }));
  expect(await screen.findByRole('heading', { name: /tags? added to 2 contacts/i })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /preview/i })).toBeNull();
});

/**
 * The real flow: the list clears its selection in `onApplied`, so `targetContacts` is EMPTY by the time the
 * result step renders. The title must count from the answer (counts.changed), not from the prop; the answer
 * is the real POST /contacts/bulk-tags shape (counts {changed, unchanged, failed}, rows with `settings`).
 */
it('the result title counts the answer even after the list cleared its selection', async () => {
  const real = (dryRun: boolean) => ({
    dry_run: dryRun,
    results: ['c1', 'c2'].map((id) => ({
      contact_id: id, label: id, status: 'changed', tags_before: [], tags_after: ['Dealer'], error: null,
      settings: { contact_id: id, label: id, status: 'changed', changes: [], conflicts: [], not_applied: [], error: null },
    })),
    counts: { changed: 2, unchanged: 0, failed: 0 },
  });
  tagSvc.getContactTags.mockResolvedValue({
    data: [{ id: 'tag-a', name: 'Dealer', description: null, is_active: true, sort_order: 0, settings: {},
      contact_count: 0, created_at: '', updated_at: '' }],
    pagination: { total: 1, page: 1, limit: 1000, pages: 1 },
  });
  tagSvc.bulkTagContacts.mockResolvedValueOnce(real(true)).mockResolvedValueOnce(real(false));

  function Host() {
    const [selected, setSelected] = React.useState(targets);
    return (
      <BulkTagDialog open onOpenChange={vi.fn()} mode="add" targetContacts={selected} onApplied={() => setSelected([])} />
    );
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Host />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Dealer' }));
  fireEvent.click(screen.getByRole('button', { name: /^(continue|preview)/i }));
  fireEvent.click(await screen.findByRole('button', { name: 'Add tag to 2 contacts' }));
  expect(await screen.findByRole('heading', { name: 'Tags added to 2 contacts' })).toBeInTheDocument();
});
