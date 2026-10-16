/**
 * CONTACT-TAGS S3, UAC T3.1-T3.5: the Apply tag settings preview dialog.
 * Assumed: ./ApplyTagSettingsDialog default export, props { open, onOpenChange, contactIds: string[],
 * tagId?: string, onApplied?: () => void }. With tagId it calls applyContactTagSettings(tagId,
 * { dryRun }), otherwise applyTagSettings({ contactIds, dryRun }) - both from
 * ../../contact-tags/services/contactTagService. Test ids: counts `apply-tag-counts`, a contact card
 * `apply-tag-row-<contact_id>`. Confirm button "Apply to N contacts". Bool values render on / off.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const svc = vi.hoisted(() => ({ applyTagSettings: vi.fn(), applyContactTagSettings: vi.fn() }));
vi.mock('../../contact-tags/services/contactTagService', () => svc);

import ApplyTagSettingsDialog from './ApplyTagSettingsDialog';

function answer(dryRun: boolean) {
  return {
    dry_run: dryRun,
    results: [
      {
        contact_id: 'c1', label: 'Alice', status: 'changed', error: null,
        changes: [{ section: 'chatbot', key: 'chatbot_stock_allowed', label: 'Stock checks', before: true,
          after: false, decided_by: ['Retail'], added: [], removed: [] }],
        conflicts: [{ label: 'Skip period questions', tags: [{ name: 'Dealer', value: true }, { name: 'Retail', value: false }] }],
        not_applied: [{ label: 'Chatbot agents', reason: 'Needs access agents edit.' }],
      },
      { contact_id: 'c2', label: 'Bob', status: 'unchanged', error: null, changes: [], conflicts: [], not_applied: [] },
    ],
    counts: { changed: 1, unchanged: 1, skipped: 0, failed: 0, conflicts: 1 },
  };
}

function renderDialog(props: Record<string, unknown> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onApplied = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <ApplyTagSettingsDialog open onOpenChange={vi.fn()} contactIds={['c1', 'c2']} onApplied={onApplied} {...props} />
    </QueryClientProvider>,
  );
  return { onApplied };
}

beforeEach(() => {
  svc.applyTagSettings.mockResolvedValue(answer(true));
  svc.applyContactTagSettings.mockResolvedValue(answer(true));
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ApplyTagSettingsDialog', () => {
  it('opens with a dry run and shows counts, changes with deciding tag, conflicts and not applied', async () => {
    renderDialog();
    await waitFor(() =>
      expect(svc.applyTagSettings).toHaveBeenCalledWith({ contactIds: ['c1', 'c2'], dryRun: true }),
    );
    const counts = await screen.findByTestId('apply-tag-counts');
    expect(counts).toHaveTextContent(/1.*chang/i);
    expect(counts).toHaveTextContent(/1.*conflict/i);

    const row = screen.getByTestId('apply-tag-row-c1');
    expect(within(row).getByText('Stock checks')).toBeInTheDocument();
    expect(within(row).getByText(/Retail/)).toBeInTheDocument();
    expect(row).toHaveTextContent(/on/i);
    expect(row).toHaveTextContent(/off/i);
    expect(row).toHaveTextContent('Skip period questions');
    expect(row).toHaveTextContent(/not applied/i);
    expect(row).toHaveTextContent('Dealer');
    expect(row).toHaveTextContent('Chatbot agents');
    expect(row).toHaveTextContent('Needs access agents edit.');
  });

  it('"Apply to N contacts" sends dry_run false', async () => {
    svc.applyTagSettings.mockResolvedValueOnce(answer(true)).mockResolvedValueOnce(answer(false));
    const { onApplied } = renderDialog();
    fireEvent.click(await screen.findByRole('button', { name: 'Apply to 2 contacts' }));
    await waitFor(() =>
      expect(svc.applyTagSettings).toHaveBeenLastCalledWith({ contactIds: ['c1', 'c2'], dryRun: false }),
    );
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
  });

  it('a tag apply goes through the tag route', async () => {
    renderDialog({ tagId: 'tag-1' });
    await waitFor(() => expect(svc.applyContactTagSettings).toHaveBeenCalledWith('tag-1', { dryRun: true }));
    expect(svc.applyTagSettings).not.toHaveBeenCalled();
  });

  it('a rejected preview shows the message and the dialog stays open', async () => {
    svc.applyTagSettings.mockRejectedValueOnce(new Error('Too many contacts.'));
    renderDialog();
    expect(await screen.findByText(/Too many contacts\./)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('a rejected apply shows the message and the button stays usable', async () => {
    svc.applyTagSettings.mockResolvedValueOnce(answer(true)).mockRejectedValueOnce(new Error('Server went away.'));
    renderDialog();
    fireEvent.click(await screen.findByRole('button', { name: 'Apply to 2 contacts' }));
    expect(await screen.findByText(/Server went away\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply to 2 contacts' })).not.toBeDisabled();
  });
});
