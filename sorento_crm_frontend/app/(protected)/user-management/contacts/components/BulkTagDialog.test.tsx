/**
 * CONTACT-TAGS S1, UAC T2.2/T2.3/T5.4: the one "Add tag / Remove tag" dialog. Pick tags -> the
 * dry run is the preview (per-contact before/after) -> confirm applies (dry_run false). A rejected
 * call shows the extracted message and the dialog stays open and usable (never stuck).
 * Assumed: ./BulkTagDialog default export, props
 *   { open, onOpenChange, mode: 'add' | 'remove', targetContacts, onApplied? }
 * Buttons: continue = /^(continue|preview)/i, confirm = 'Add tag to 2 contacts'.
 * Services: ../../contact-tags/services/contactTagService { getContactTags, bulkTagContacts }.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const tagSvc = vi.hoisted(() => ({ getContactTags: vi.fn(), bulkTagContacts: vi.fn() }));
vi.mock('../../contact-tags/services/contactTagService', () => tagSvc);

// Stand-in for the Radix popover multi-select: one button per option, toggles the value.
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    value: string[];
    onChange: (v: string[]) => void;
    options?: { value: string; label: string }[];
    fetchOptions?: (q: string) => Promise<{ value: string; label: string }[]>;
  }) => {
    const [opts, setOpts] = React.useState(props.options ?? []);
    React.useEffect(() => {
      if (props.fetchOptions) void props.fetchOptions('').then(setOpts);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    const list = props.options ?? opts;
    return (
      <div data-testid="tag-select">
        {list.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() =>
              props.onChange(
                props.value.includes(o.value) ? props.value.filter((v) => v !== o.value) : [...props.value, o.value],
              )
            }
          >
            {o.label}
          </button>
        ))}
      </div>
    );
  },
}));

import BulkTagDialog from './BulkTagDialog';

const targets = [
  { id: 'c1', phone_number: '6011', name: 'Alice' },
  { id: 'c2', phone_number: '6012', name: 'Bob' },
] as never[];

const tag = (id: string, name: string) => ({
  id, name, description: null, is_active: true, sort_order: 0, settings: {}, contact_count: 0,
  created_at: '', updated_at: '',
});

function response(dryRun: boolean) {
  return {
    dry_run: dryRun,
    results: [
      { contact_id: 'c1', label: 'Alice', status: 'changed', tags_before: ['Old'], tags_after: ['Old', 'Dealer'], error: null },
      { contact_id: 'c2', label: 'Bob', status: 'unchanged', tags_before: ['Dealer'], tags_after: ['Dealer'], error: null },
    ],
    counts: { changed: 1, unchanged: 1, failed: 0 },
  };
}

function renderDialog(mode: 'add' | 'remove' = 'add', onApplied = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BulkTagDialog open onOpenChange={vi.fn()} mode={mode} targetContacts={targets} onApplied={onApplied} />
    </QueryClientProvider>,
  );
  return { onApplied };
}

beforeEach(() => {
  tagSvc.getContactTags.mockResolvedValue({
    data: [tag('tag-a', 'Dealer'), tag('tag-b', 'VIP')],
    pagination: { total: 2, page: 1, limit: 1000, pages: 1 },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('BulkTagDialog', () => {
  it('continuing runs the dry run for the chosen tag and shows per-contact before / after', async () => {
    tagSvc.bulkTagContacts.mockResolvedValueOnce(response(true));
    renderDialog('add');
    fireEvent.click(await screen.findByRole('button', { name: 'Dealer' }));
    fireEvent.click(screen.getByRole('button', { name: /^(continue|preview)/i }));

    await waitFor(() =>
      expect(tagSvc.bulkTagContacts).toHaveBeenCalledWith({
        contactIds: ['c1', 'c2'],
        addTagIds: ['tag-a'],
        removeTagIds: [],
        dryRun: true,
        applySettings: true,
      }),
    );
    expect(await screen.findByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(screen.getAllByText(/Dealer/).length).toBeGreaterThan(0);
  });

  it('remove mode sends the chosen tag as remove_tag_ids', async () => {
    tagSvc.bulkTagContacts.mockResolvedValueOnce(response(true));
    renderDialog('remove');
    fireEvent.click(await screen.findByRole('button', { name: 'VIP' }));
    fireEvent.click(screen.getByRole('button', { name: /^(continue|preview)/i }));
    await waitFor(() =>
      expect(tagSvc.bulkTagContacts).toHaveBeenCalledWith({
        contactIds: ['c1', 'c2'],
        addTagIds: [],
        removeTagIds: ['tag-b'],
        dryRun: true,
      }),
    );
  });

  it('confirming applies with dry_run false and tells the list', async () => {
    tagSvc.bulkTagContacts.mockResolvedValueOnce(response(true)).mockResolvedValueOnce(response(false));
    const { onApplied } = renderDialog('add');
    fireEvent.click(await screen.findByRole('button', { name: 'Dealer' }));
    fireEvent.click(screen.getByRole('button', { name: /^(continue|preview)/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add tag to 2 contacts' }));

    await waitFor(() =>
      expect(tagSvc.bulkTagContacts).toHaveBeenLastCalledWith({
        contactIds: ['c1', 'c2'],
        addTagIds: ['tag-a'],
        removeTagIds: [],
        dryRun: false,
        applySettings: true,
      }),
    );
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
  });

  it('a rejected preview shows the message, keeps the dialog open and lets the user retry', async () => {
    tagSvc.bulkTagContacts
      .mockRejectedValueOnce(new Error('Tag no longer exists.'))
      .mockResolvedValueOnce(response(true));
    renderDialog('add');
    fireEvent.click(await screen.findByRole('button', { name: 'Dealer' }));
    fireEvent.click(screen.getByRole('button', { name: /^(continue|preview)/i }));

    expect(await screen.findByText(/Tag no longer exists\./)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    const again = screen.getByRole('button', { name: /^(continue|preview)/i });
    expect(again).not.toBeDisabled();
    fireEvent.click(again);
    expect(await screen.findByText('Alice')).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByText('Bob')).toBeInTheDocument();
  });

  it('a rejected apply shows the message and the dialog stays usable', async () => {
    tagSvc.bulkTagContacts
      .mockResolvedValueOnce(response(true))
      .mockRejectedValueOnce(new Error('Server went away.'));
    renderDialog('add');
    fireEvent.click(await screen.findByRole('button', { name: 'Dealer' }));
    fireEvent.click(screen.getByRole('button', { name: /^(continue|preview)/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Add tag to 2 contacts' }));

    expect(await screen.findByText(/Server went away\./)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add tag to 2 contacts' })).not.toBeDisabled();
  });
});
