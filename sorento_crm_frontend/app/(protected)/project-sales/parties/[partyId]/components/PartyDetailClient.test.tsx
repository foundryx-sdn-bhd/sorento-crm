/**
 * PartyDetailClient - the "form view" the client asked for beside the list view.
 *
 * The rule being pinned is the CRUD standard's: every section renders whether or not it
 * holds anything, with an explicit empty state. A section that vanishes on missing data
 * teaches people the field does not exist, and they stop filling it in.
 *
 * The four states are all covered here because a detail page that only works when the
 * record loads is a detail page that shows a blank screen the day it does not.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProjectParty } from '../../../_shared/types/project.types';

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

vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/parties/pt1',
  useRouter: () => ({ push, replace: vi.fn() }),
}));

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock('../../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../_shared/services/projectService')
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

import { PartyDetailClient } from './PartyDetailClient';

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

function renderDetail(partyId = 'pt1') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PartyDetailClient partyId={partyId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listParties.mockResolvedValue(envelope([party()]));
});

describe('PartyDetailClient', () => {
  it('shows a loading skeleton rather than an empty page', () => {
    listParties.mockReturnValue(new Promise(() => {}));

    renderDetail();

    expect(screen.getByTestId('party-detail-loading')).toBeInTheDocument();
  });

  it('reports a load failure and offers a retry', async () => {
    listParties.mockRejectedValue(new Error('Backend is down'));

    renderDetail();

    expect(await screen.findByText('Backend is down')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('says the record is gone rather than rendering a blank form', async () => {
    listParties.mockResolvedValue(envelope([party({ id: 'other' })]));

    renderDetail('pt1');

    expect(await screen.findByText('This party no longer exists')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to parties' })).toBeInTheDocument();
  });

  it('renders every section with the record filled in', async () => {
    listParties.mockResolvedValue(
      envelope([
        party({
          registration_no: '199801012345',
          phone: '09-0000 0080',
          email: 'person16@example.com',
          address: 'Level 8, Menara X, Kuala Lumpur',
          notes: 'Specifies our basins on hotel work.',
          customer_name: 'Veritas Sdn Bhd',
          project_count: 4,
        }),
      ]),
    );

    renderDetail();

    expect(await screen.findByRole('heading', { name: 'Veritas Architects' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Identity' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Contact' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Commercial' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Notes' })).toBeInTheDocument();

    expect(screen.getByText('199801012345')).toBeInTheDocument();
    expect(screen.getByText('09-0000 0080')).toBeInTheDocument();
    expect(screen.getByText('person16@example.com')).toBeInTheDocument();
    expect(screen.getByText('Level 8, Menara X, Kuala Lumpur')).toBeInTheDocument();
    expect(screen.getByText('Veritas Sdn Bhd')).toBeInTheDocument();
    expect(screen.getByText('4 projects')).toBeInTheDocument();
    expect(screen.getByText('Specifies our basins on hotel work.')).toBeInTheDocument();

    // No UUID reaches the screen.
    expect(screen.queryByText('pt1')).not.toBeInTheDocument();
  });

  it('renders every section on a bare record, each with its own empty state', async () => {
    listParties.mockResolvedValue(envelope([party({ project_count: 0 })]));

    renderDetail();

    expect(await screen.findByRole('heading', { name: 'Identity' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Contact' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Commercial' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Notes' })).toBeInTheDocument();

    // An unknown FIELD is a dash, not a sentence explaining why it matters. The
    // section-level empty states below still speak, because "no projects yet" needs a
    // next step; a blank cell does not.
    expect(screen.getAllByText('-').length).toBeGreaterThan(0);
    expect(screen.getByText(/None yet\. Register a project/i)).toBeInTheDocument();
    expect(screen.getByText(/Nothing written down/i)).toBeInTheDocument();
  });

  it('edits through the same modal the list uses', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));

    expect(await screen.findByText('Edit Veritas Architects')).toBeInTheDocument();
  });

  it('confirms before deleting, and says it cannot be undone', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /^Delete$/ }));

    expect(await screen.findByText('Confirm delete')).toBeInTheDocument();
    expect(screen.getByText(/This action cannot be undone/)).toBeInTheDocument();
  });
});
