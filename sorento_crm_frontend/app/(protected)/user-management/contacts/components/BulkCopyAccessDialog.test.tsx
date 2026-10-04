/**
 * Lane CONTACT-BULK-ACCESS, UAC A3.2-A3.5: the "Copy access from contact" dialog.
 * Pick a source -> the dry run is the preview -> one apply -> a result row per contact.
 * Every path ends on a readable state (never a spinner).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const contactSvc = vi.hoisted(() => ({ getContacts: vi.fn() }));
vi.mock('../[id]/services/contactService', () => contactSvc);

const copySvc = vi.hoisted(() => ({ bulkCopyContactAccess: vi.fn() }));
vi.mock('../[id]/services/contactAccessCopyService', () => copySvc);

// Stand-in for the Radix popover select: loads options on mount, one button per option.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    value: string;
    onChange: (v: string) => void;
    fetchOptions?: (q: string) => Promise<{ value: string; label: string }[]>;
  }) => {
    const [opts, setOpts] = React.useState<{ value: string; label: string }[]>([]);
    React.useEffect(() => {
      void props.fetchOptions?.('').then(setOpts);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <div data-testid="source-select">
        {opts.map((o) => (
          <button key={o.value} type="button" onClick={() => props.onChange(o.value)}>
            {o.label}
          </button>
        ))}
      </div>
    );
  },
}));

import BulkCopyAccessDialog from './BulkCopyAccessDialog';
import type { RespondContact } from '../types/contact.types';

const targets = [
  { id: 't1', phone_number: '6011', name: 'Target One' },
  { id: 't2', phone_number: '6012', name: 'Target Two' },
] as RespondContact[];

const source = { id: 's1', phone_number: '6010', name: 'Reference Owner' };

const summary = [
  { label: 'Access types', value: 'Dealer, End user' },
  { label: 'Tier', value: 'Dealer' },
];

function response(dryRun: boolean) {
  return {
    dry_run: dryRun,
    source: { id: 's1', label: 'Reference Owner', summary },
    results: [
      {
        contact_id: 't1',
        label: 'Target One',
        status: 'changed',
        error: null,
        changes: [
          { facet: 'access_types', label: 'Access types', before: [], after: ['dealer'], added: ['Dealer'], removed: [] },
          { facet: 'escalation_allowed', label: 'Escalation', before: true, after: false, added: [], removed: [] },
          { facet: 'field_reveals', label: 'Field reveals', before: ['inventory.sellable'], after: [], added: [], removed: ['Outstanding SO on stock answers'] },
        ],
      },
      { contact_id: 't2', label: 'Target Two', status: 'unchanged', error: null, changes: [] },
    ],
    counts: { changed: 1, unchanged: 1, skipped: 0, failed: 0 },
  };
}

function renderDialog(onCheckDiffers = vi.fn(), onApplied = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <BulkCopyAccessDialog
        open
        onOpenChange={vi.fn()}
        targetContacts={targets}
        onCheckDiffers={onCheckDiffers}
        onApplied={onApplied}
      />
    </QueryClientProvider>,
  );
  return { onCheckDiffers, onApplied };
}

async function pickSource() {
  fireEvent.click(await screen.findByText('Reference Owner (6010)'));
}

beforeEach(() => {
  contactSvc.getContacts.mockResolvedValue({ data: [source, ...targets], pagination: { total: 3 } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('BulkCopyAccessDialog', () => {
  it('A3.2: the source list excludes the selected targets; picking one runs the dry run and shows its summary', async () => {
    copySvc.bulkCopyContactAccess.mockResolvedValueOnce(response(true));
    renderDialog();
    const select = await screen.findByTestId('source-select');
    await within(select).findByText('Reference Owner (6010)');
    expect(within(select).queryByText('Target One (6011)')).toBeNull();

    await pickSource();
    await waitFor(() =>
      expect(copySvc.bulkCopyContactAccess).toHaveBeenCalledWith({
        sourceContactId: 's1',
        targetContactIds: ['t1', 't2'],
        dryRun: true,
      }),
    );
    const summaryEl = await screen.findByTestId('copy-access-source-summary');
    expect(summaryEl).toHaveTextContent('Dealer, End user');
    expect(summaryEl).toHaveTextContent('Linked customers');
  });

  it('A3.3: preview shows counts and added / removed lines per contact', async () => {
    copySvc.bulkCopyContactAccess.mockResolvedValueOnce(response(true));
    renderDialog();
    await pickSource();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview changes' }));

    expect(screen.getByTestId('copy-access-counts')).toHaveTextContent('1 will change');
    expect(screen.getByTestId('copy-access-counts')).toHaveTextContent('1 already the same');
    const row = screen.getByTestId('copy-access-row-t1');
    expect(within(row).getByText('+ Dealer')).toBeInTheDocument();
    expect(within(row).getByText('Outstanding SO on stock answers')).toHaveClass('line-through');
    expect(within(row).getByText('on')).toHaveClass('line-through');
    expect(within(row).getByText('off')).toBeInTheDocument();
  });

  it('A3.4: apply sends one non-dry call and ends on the result table; "Check who still differs" hands back the source', async () => {
    copySvc.bulkCopyContactAccess.mockResolvedValueOnce(response(true)).mockResolvedValueOnce(response(false));
    const { onCheckDiffers } = renderDialog();
    await pickSource();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview changes' }));
    fireEvent.click(screen.getByRole('button', { name: /Apply to 1 contact/ }));

    await screen.findByText('Copy finished');
    expect(copySvc.bulkCopyContactAccess).toHaveBeenLastCalledWith({
      sourceContactId: 's1',
      targetContactIds: ['t1', 't2'],
      dryRun: false,
    });
    expect(screen.getByTestId('copy-access-counts')).toHaveTextContent('1 updated');
    expect(within(screen.getByTestId('copy-access-row-t1')).getByText('Updated')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Check who still differs' }));
    expect(onCheckDiffers).toHaveBeenCalledWith({ id: 's1', label: 'Reference Owner' });
  });

  it('A3.4: an apply error or timeout ends on a message, not a spinner', async () => {
    copySvc.bulkCopyContactAccess
      .mockResolvedValueOnce(response(true))
      .mockRejectedValueOnce(new Error('The server took too long to answer.'));
    renderDialog();
    await pickSource();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview changes' }));
    fireEvent.click(screen.getByRole('button', { name: /Apply to 1 contact/ }));

    const err = await screen.findByTestId('copy-access-apply-error');
    expect(err).toHaveTextContent('The server took too long to answer.');
    expect(err).toHaveTextContent('Nothing confirmed');
    expect(screen.getByText('Copy not confirmed')).toBeInTheDocument();
  });

  it('A3.2: a failed preview shows the error with Retry, and Preview stays disabled', async () => {
    copySvc.bulkCopyContactAccess
      .mockRejectedValueOnce(new Error('Contact not found'))
      .mockResolvedValueOnce(response(true));
    renderDialog();
    await pickSource();
    await screen.findByText('Contact not found');
    expect(screen.getByRole('button', { name: 'Preview changes' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview changes' })).toBeEnabled());
  });
});

describe('BulkCopyAccessDialog selection (review S1)', () => {
  it('Cancel leaves the selection alone; an answered apply clears it', async () => {
    copySvc.bulkCopyContactAccess.mockResolvedValueOnce(response(true)).mockResolvedValueOnce(response(false));
    const { onApplied } = renderDialog();
    await pickSource();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview changes' }));
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(onApplied).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }));
    fireEvent.click(screen.getByRole('button', { name: /Apply to 1 contact/ }));
    await screen.findByText('Copy finished');
    expect(onApplied).toHaveBeenCalledTimes(1);
  });
});
