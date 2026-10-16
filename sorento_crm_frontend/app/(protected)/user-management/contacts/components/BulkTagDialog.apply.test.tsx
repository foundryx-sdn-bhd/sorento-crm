/**
 * CONTACT-TAGS S3, UAC T5.4: BulkTagDialog step 2 (add mode) offers "Apply tag settings now" (default,
 * shows the setting changes) / "Don't apply now"; the confirm button "Add tag to N contacts" sends
 * apply_settings accordingly. Remove mode has no apply choice. bulkTagContacts gains
 * `applySettings: boolean`; a row carries `settings: { status, changes: [{label,before,after,...}] }`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const tagSvc = vi.hoisted(() => ({ getContactTags: vi.fn(), bulkTagContacts: vi.fn() }));
vi.mock('../../contact-tags/services/contactTagService', () => tagSvc);

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
    return (
      <div>
        {(props.options ?? opts).map((o) => (
          <button key={o.value} type="button" onClick={() => props.onChange([...props.value, o.value])}>
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
      {
        contact_id: 'c1', label: 'Alice', status: 'changed', tags_before: [], tags_after: ['Dealer'], error: null,
        settings: { status: 'changed', changes: [{ section: 'chatbot', key: 'chatbot_stock_allowed',
          label: 'Stock checks', before: true, after: false, decided_by: ['Dealer'], added: [], removed: [] }] },
      },
      { contact_id: 'c2', label: 'Bob', status: 'unchanged', tags_before: ['Dealer'], tags_after: ['Dealer'], error: null },
    ],
    counts: { changed: 1, unchanged: 1, failed: 0 },
  };
}

function renderDialog(mode: 'add' | 'remove') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BulkTagDialog open onOpenChange={vi.fn()} mode={mode} targetContacts={targets} onApplied={vi.fn()} />
    </QueryClientProvider>,
  );
}

async function toStepTwo() {
  fireEvent.click(await screen.findByRole('button', { name: 'Dealer' }));
  fireEvent.click(screen.getByRole('button', { name: /^(continue|preview)/i }));
}

beforeEach(() => {
  tagSvc.getContactTags.mockResolvedValue({
    data: [tag('tag-a', 'Dealer')], pagination: { total: 1, page: 1, limit: 1000, pages: 1 },
  });
  tagSvc.bulkTagContacts.mockResolvedValue(response(true));
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('BulkTagDialog apply choice', () => {
  it('defaults to "Apply tag settings now", previews setting changes and confirms with applySettings true', async () => {
    renderDialog('add');
    await toStepTwo();
    await waitFor(() =>
      expect(tagSvc.bulkTagContacts).toHaveBeenCalledWith(expect.objectContaining({ dryRun: true, applySettings: true })),
    );
    expect(await screen.findByRole('radio', { name: /apply tag settings now/i })).toBeChecked();
    expect(await screen.findByText('Stock checks')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add tag to 2 contacts' }));
    await waitFor(() =>
      expect(tagSvc.bulkTagContacts).toHaveBeenLastCalledWith(
        expect.objectContaining({ dryRun: false, applySettings: true, addTagIds: ['tag-a'] }),
      ),
    );
  });

  it('"Don\'t apply now" confirms with applySettings false', async () => {
    renderDialog('add');
    await toStepTwo();
    fireEvent.click(await screen.findByRole('radio', { name: /don't apply now/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Add tag to 2 contacts' }));
    await waitFor(() =>
      expect(tagSvc.bulkTagContacts).toHaveBeenLastCalledWith(
        expect.objectContaining({ dryRun: false, applySettings: false }),
      ),
    );
  });

  it('remove mode has no apply choice', async () => {
    renderDialog('remove');
    await toStepTwo();
    await screen.findByText('Alice');
    expect(screen.queryByRole('radio', { name: /apply tag settings now/i })).toBeNull();
  });
});
