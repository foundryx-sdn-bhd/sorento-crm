/**
 * P1 - AwaitingAcceptanceClient (AC-A7).
 *
 * The screen answers one question: which of my leads has nobody taken. So what is pinned
 * is that the server owns the order (the route takes no sort), the wait in plain words,
 * and the two things marketing can do about it without leaving the row.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AwaitingAcceptanceRow } from '../../_shared/types/leadAcceptance.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const listAwaitingAcceptance = vi.fn();
const assignLead = vi.fn();
const nudgeLeadAssignee = vi.fn();
const getUserLookup = vi.fn();

vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/lead-acceptance',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// The DataGrid persists column preferences over the network; stub that away.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock('../../_shared/services/leadAcceptanceService', () => ({
  listAwaitingAcceptance: (...args: unknown[]) => listAwaitingAcceptance(...args),
  assignLead: (...args: unknown[]) => assignLead(...args),
  acceptLead: vi.fn(),
  declineLead: vi.fn(),
  nudgeLeadAssignee: (...args: unknown[]) => nudgeLeadAssignee(...args),
}));

vi.mock('@/services/userSelectService', () => ({
  getUserLookup: (...args: unknown[]) => getUserLookup(...args),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn() },
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
  }: {
    value: string;
    onChange: (next: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select
      aria-label={placeholder ?? 'select'}
      value={value}
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

import { AwaitingAcceptanceClient } from './AwaitingAcceptanceClient';

function row(overrides: Partial<AwaitingAcceptanceRow> = {}): AwaitingAcceptanceRow {
  return {
    id: 'l1',
    lead_code: 'LEAD-000001',
    title: 'Tower behind the showroom',
    customer_id: null,
    customer_name: null,
    developer_name: 'Setia Land',
    outcome: 'open',
    project_count: 0,
    possible_duplicates: [],
    can_edit: true,
    estimated_value: '1800000',
    informant_source: 'bci',
    informant_party_label: 'Veritas Architects Sdn Bhd',
    acceptance_state: 'assigned',
    owner_user_id: 'u-ali',
    owner_name: 'Ali',
    assigned_at: '2026-07-30T02:00:00',
    hours_since_assigned: 50,
    ...overrides,
  };
}

function renderClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AwaitingAcceptanceClient />
    </QueryClientProvider>,
  );
}

/** Radix opens its menus on pointerdown, which fireEvent.click does not send. */
function openFilters() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /filters/i }), {
    button: 0,
    ctrlKey: false,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  listAwaitingAcceptance.mockResolvedValue({
    data: [],
    total: 0,
    page: 1,
    limit: 25,
  });
  assignLead.mockResolvedValue(row());
  nudgeLeadAssignee.mockResolvedValue(row());
  getUserLookup.mockResolvedValue([
    { id: 'u-ali', name: 'Ali' },
    { id: 'u-siti', name: 'Siti' },
  ]);
});

describe('AwaitingAcceptanceClient', () => {
  it('takes the order the server gives and never asks for a sort', async () => {
    renderClient();

    await waitFor(() => expect(listAwaitingAcceptance).toHaveBeenCalled());
    const params = listAwaitingAcceptance.mock.calls[0][0];
    expect(params).toEqual(
      expect.objectContaining({ pageIndex: 0, pageSize: 25 }),
    );
    expect(params.sorting).toBeUndefined();
  });

  it('keeps the toolbar while loading, so the page does not jump', () => {
    listAwaitingAcceptance.mockReturnValue(new Promise(() => {}));
    renderClient();

    expect(screen.getByRole('button', { name: /filters/i })).toBeInTheDocument();
  });

  it('says every assigned lead has been accepted when there is nothing to chase', async () => {
    renderClient();

    expect(
      await screen.findByText('Every assigned lead has been accepted'),
    ).toBeInTheDocument();
    // #1335: the header's "All leads" is the one way to the leads list; the empty
    // state keeps its hint but does not repeat it, and there is no subtitle.
    expect(screen.queryByRole('link', { name: 'Go to leads' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /leads/i })).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'All leads' })).toHaveAttribute(
      'href',
      '/project-sales/leads',
    );
    expect(
      screen.getByText(/Assign a lead from the leads list/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/who has not accepted them yet/i),
    ).not.toBeInTheDocument();
  });

  it('distinguishes an empty filter result from an empty worklist', async () => {
    renderClient();
    await screen.findByText('Every assigned lead has been accepted');

    openFilters();
    fireEvent.change(await screen.findByLabelText('Any wait'), {
      target: { value: '24' },
    });

    expect(await screen.findByText('No leads match these filters')).toBeInTheDocument();
    await waitFor(() =>
      expect(listAwaitingAcceptance).toHaveBeenCalledWith(
        expect.objectContaining({ min_hours: 24 }),
      ),
    );
  });

  it('reports a load failure rather than an empty table', async () => {
    listAwaitingAcceptance.mockRejectedValue(new Error('Backend is down'));
    renderClient();

    expect(await screen.findByText('Backend is down')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows the wait in plain words and never a UUID', async () => {
    listAwaitingAcceptance.mockResolvedValue({
      data: [row()],
      total: 1,
      page: 1,
      limit: 25,
    });
    renderClient();

    expect(await screen.findByText('2 days')).toBeInTheDocument();
    expect(screen.getByText('Ali')).toBeInTheDocument();
    expect(screen.getByText('LEAD-000001')).toBeInTheDocument();
    expect(screen.getByText(/Veritas Architects Sdn Bhd/)).toBeInTheDocument();
    expect(screen.queryByText('u-ali')).not.toBeInTheDocument();
    expect(screen.queryByText('l1')).not.toBeInTheDocument();
  });

  it('nudges the assignee from the row', async () => {
    listAwaitingAcceptance.mockResolvedValue({
      data: [row()],
      total: 1,
      page: 1,
      limit: 25,
    });
    renderClient();

    fireEvent.click(await screen.findByRole('button', { name: 'Nudge Ali' }));

    const dialog = within(await screen.findByRole('dialog'));
    fireEvent.change(dialog.getByLabelText('Message to them'), {
      target: { value: 'Tender closes Friday' },
    });
    fireEvent.click(dialog.getByRole('button', { name: 'Nudge' }));

    await waitFor(() =>
      expect(nudgeLeadAssignee).toHaveBeenCalledWith('l1', 'u-ali', 'Tender closes Friday'),
    );
  });

  it('hands the lead to somebody else from the row', async () => {
    listAwaitingAcceptance.mockResolvedValue({
      data: [row()],
      total: 1,
      page: 1,
      limit: 25,
    });
    renderClient();

    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Assign LEAD-000001 to somebody else',
      }),
    );

    const dialog = within(await screen.findByRole('dialog'));
    await waitFor(() =>
      expect(dialog.getByRole('option', { name: 'Siti' })).toBeInTheDocument(),
    );
    fireEvent.change(dialog.getByLabelText('Search people'), {
      target: { value: 'u-siti' },
    });
    fireEvent.click(dialog.getByRole('button', { name: 'Assign' }));

    await waitFor(() =>
      expect(assignLead).toHaveBeenCalledWith('l1', {
        owner_user_id: 'u-siti',
        note: null,
      }),
    );
  });

  it('filters by who is sitting on it', async () => {
    renderClient();
    await screen.findByText('Every assigned lead has been accepted');

    openFilters();
    fireEvent.change(await screen.findByLabelText('Anyone'), {
      target: { value: 'u-ali' },
    });

    await waitFor(() =>
      expect(listAwaitingAcceptance).toHaveBeenCalledWith(
        expect.objectContaining({ owner_user_id: 'u-ali' }),
      ),
    );
  });
});
