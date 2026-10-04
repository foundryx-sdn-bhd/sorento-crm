/**
 * P1 - AssignLeadDialog (AC-A4).
 *
 * Assignment is not ownership, so the dialog must not let anybody submit an empty
 * handover, and it must say who is holding the lead right now.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const getUserLookup = vi.fn();

vi.mock('@/services/userSelectService', () => ({
  getUserLookup: (...args: unknown[]) => getUserLookup(...args),
}));

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
    disabled,
  }: {
    value: string;
    onChange: (next: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
    disabled?: boolean;
  }) => (
    <select
      aria-label={placeholder ?? 'select'}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">{placeholder ?? ''}</option>
      {(options ?? []).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

import { AssignLeadDialog } from './AssignLeadDialog';

function renderDialog(props: Partial<React.ComponentProps<typeof AssignLeadDialog>> = {}) {
  const onConfirm = vi.fn(async () => {});
  const onDone = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AssignLeadDialog
        leadCode="LEAD-000001"
        currentOwnerName={null}
        onDone={onDone}
        onConfirm={onConfirm}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onConfirm, onDone };
}

beforeEach(() => {
  vi.clearAllMocks();
  getUserLookup.mockResolvedValue([{ id: 'u-ali', name: 'Ali' }]);
});

describe('AssignLeadDialog', () => {
  it('waits for the people list before offering a choice', async () => {
    getUserLookup.mockReturnValue(new Promise(() => {}));
    renderDialog();

    expect(await screen.findByLabelText('Loading people')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Assign' })).toBeDisabled();
  });

  it('says nobody holds the lead when nobody does', async () => {
    renderDialog();
    expect(await screen.findByText('Nobody holds this lead yet.')).toBeInTheDocument();
  });

  it('names the current holder on a reassignment', async () => {
    renderDialog({ currentOwnerName: 'Ali' });
    expect(await screen.findByText('Currently with Ali.')).toBeInTheDocument();
  });

  it('sends the person and the note, trimmed to null when empty', async () => {
    const { onConfirm, onDone } = renderDialog();

    const picker = await screen.findByLabelText('Search people');
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'Ali' })).toBeInTheDocument(),
    );
    fireEvent.change(picker, { target: { value: 'u-ali' } });
    fireEvent.click(screen.getByRole('button', { name: 'Assign' }));

    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith('u-ali', null));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it('carries a note through when there is one', async () => {
    const { onConfirm } = renderDialog();

    const picker = await screen.findByLabelText('Search people');
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'Ali' })).toBeInTheDocument(),
    );
    fireEvent.change(picker, { target: { value: 'u-ali' } });
    fireEvent.change(screen.getByLabelText('Message to them'), {
      target: { value: 'Tender closes Friday' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Assign' }));

    await waitFor(() =>
      expect(onConfirm).toHaveBeenCalledWith('u-ali', 'Tender closes Friday'),
    );
  });
});
