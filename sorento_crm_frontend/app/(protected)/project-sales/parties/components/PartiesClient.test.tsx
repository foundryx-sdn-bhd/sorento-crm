/**
 * PartiesClient, converted from type-grouped cards to the shared DataGrid.
 *
 * What is pinned here is the shape the client asked for: ONE list, ONE toolbar row
 * carrying search, filters, columns and export, a pinned listing key, a row per firm
 * rather than a card per firm, and a row click that opens the record. The type is a
 * column and a toolbar filter; it is no longer a section heading.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProjectParty } from '../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const listParties = vi.fn();
const push = vi.fn();
const listingKeys: (string | null | undefined)[] = [];

vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/parties',
  useRouter: () => ({ push, replace: vi.fn() }),
}));

// The DataGrid persists column preferences over the network and shows skeleton rows
// until they resolve; stub that away, and record the key it was given so the pathname
// fallback cannot creep back in.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: ({ listingKey }: { listingKey?: string | null }) => {
    listingKeys.push(listingKey);
    return { resetToDefaults: async () => {}, isLoading: false };
  },
}));

vi.mock('../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../_shared/services/projectService')
  >();
  return {
    ...actual,
    listParties: (...args: unknown[]) => listParties(...args),
    createParty: vi.fn(),
    updateParty: vi.fn(),
    deleteParty: vi.fn(async () => undefined),
  };
});

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), custom: vi.fn() },
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

import { PartiesClient } from './PartiesClient';

function party(overrides: Partial<ProjectParty> = {}): ProjectParty {
  return {
    id: 'pt1',
    party_type: 'architect',
    name: 'Veritas Architects',
    is_active: true,
    project_count: 4,
    ...overrides,
  };
}

function envelope(rows: ProjectParty[]) {
  return { data: rows, pagination: { total: rows.length, page: 1, limit: 200 } };
}

function renderClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PartiesClient />
    </QueryClientProvider>,
  );
}

/** Radix opens its popovers on pointerdown, which fireEvent.click does not send. */
function openFilters() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /filters/i }), {
    button: 0,
    ctrlKey: false,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  listingKeys.length = 0;
  listParties.mockResolvedValue(envelope([]));
});

describe('PartiesClient', () => {
  it('puts search, filters, columns and export on ONE toolbar row', async () => {
    renderClient();

    const toolbar = (await screen.findByPlaceholderText(/Search by name/i)).closest(
      '[data-slot="card-header"]',
    ) as HTMLElement;
    expect(within(toolbar).getByRole('button', { name: /filters/i })).toBeInTheDocument();
    expect(within(toolbar).getByRole('button', { name: /columns/i })).toBeInTheDocument();
    expect(within(toolbar).getByRole('button', { name: /^export$/i })).toBeInTheDocument();
    expect(
      within(toolbar).getByRole('button', { name: 'Refresh list' }),
    ).toBeInTheDocument();
  });

  it('pins its own listing key rather than falling back to the pathname', async () => {
    renderClient();

    await waitFor(() => expect(listingKeys.length).toBeGreaterThan(0));
    expect(listingKeys).toContain('projects.parties.view');
    expect(listingKeys).not.toContain('/project-sales/parties');
  });

  it('renders one row per firm, with the facts the cards carried', async () => {
    listParties.mockResolvedValue(
      envelope([
        party({
          id: 'pt1',
          name: 'Veritas Architects',
          registration_no: '199801012345',
          project_count: 4,
          phone: '09-0000 0080',
          customer_name: null,
        }),
        party({
          id: 'pt2',
          party_type: 'trading_house',
          name: 'Bina Trading',
          project_count: 0,
          customer_name: 'Bina Trading Sdn Bhd',
          is_active: false,
        }),
      ]),
    );

    renderClient();

    expect(await screen.findByText('Veritas Architects')).toBeInTheDocument();
    expect(screen.getByText('199801012345')).toBeInTheDocument();
    expect(screen.getByText('Architect')).toBeInTheDocument();
    expect(screen.getByText('4 projects')).toBeInTheDocument();
    expect(screen.getByText('09-0000 0080')).toBeInTheDocument();

    expect(screen.getByText('Bina Trading')).toBeInTheDocument();
    expect(screen.getByText('Trading house')).toBeInTheDocument();
    expect(screen.getByText('Bina Trading Sdn Bhd')).toBeInTheDocument();
    expect(screen.getByText('None yet')).toBeInTheDocument();
    expect(screen.getByText('Inactive')).toBeInTheDocument();

    // No UUID reaches the screen.
    expect(screen.queryByText('pt1')).not.toBeInTheDocument();
  });

  it('opens the record when the row is clicked', async () => {
    listParties.mockResolvedValue(envelope([party({ id: 'pt1' })]));

    renderClient();

    fireEvent.click(await screen.findByText('Veritas Architects'));

    expect(push).toHaveBeenCalledWith('/project-sales/parties/pt1');
  });

  it('filters by type from the toolbar, and asks the server for it', async () => {
    renderClient();
    await screen.findByText('No parties yet');

    openFilters();
    fireEvent.change(await screen.findByLabelText('All types'), {
      target: { value: 'developer' },
    });

    await waitFor(() =>
      expect(listParties).toHaveBeenCalledWith(
        expect.objectContaining({ party_type: 'developer' }),
      ),
    );
  });

  it('distinguishes an empty filter result from an empty master', async () => {
    renderClient();

    expect(await screen.findByText('No parties yet')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Add the developer you are about to register a project with. Every project references one.',
      ),
    ).toBeInTheDocument();

    openFilters();
    fireEvent.change(await screen.findByLabelText('All types'), {
      target: { value: 'developer' },
    });

    expect(await screen.findByText('No parties match')).toBeInTheDocument();
  });

  // #1335: one CTA per page. The header's "Add party" is the only Add; the empty
  // state keeps its heading and hint but offers no second button.
  it('offers one Add, in the header, and no subtitle under the title', async () => {
    renderClient();

    const empty = (await screen.findByText('No parties yet')).parentElement as HTMLElement;
    expect(within(empty).queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add the first party/i })).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /add party/i })).toHaveLength(1);
    expect(
      screen.queryByText(/reused across projects/i),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /add party/i }));
    expect(await screen.findByText('Add a party')).toBeInTheDocument();
  });

  it('confirms before deleting, and says it cannot be undone', async () => {
    listParties.mockResolvedValue(envelope([party()]));

    renderClient();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Delete Veritas Architects' }),
    );

    expect(await screen.findByText('Confirm delete')).toBeInTheDocument();
    expect(screen.getByText(/This action cannot be undone/)).toBeInTheDocument();
  });

  it('edits in a modal rather than on a card', async () => {
    listParties.mockResolvedValue(envelope([party()]));

    renderClient();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Edit Veritas Architects' }),
    );

    expect(await screen.findByText('Edit Veritas Architects')).toBeInTheDocument();
    expect(screen.getByLabelText(/Name/)).toHaveValue('Veritas Architects');
  });
});
