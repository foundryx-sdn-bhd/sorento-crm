/**
 * P1 - LeadWizardDialog (AC-A1, AC-A2, AC-A3, AC-A4).
 *
 * Three things carry the design and are pinned here:
 * - the development is the only required decision, so a lead with NO buyer submits
 * - the informant is collected as its own thing and posted as informant_* fields
 * - picking a salesperson calls ASSIGN after the create, because assignment stamps the
 *     clock and notifies them; it is not a column on the create
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

const createLead = vi.fn();
const assignLead = vi.fn();
const getUserLookup = vi.fn();

vi.mock('../../_shared/services/projectService', () => ({
  createLead: (...args: unknown[]) => createLead(...args),
  listParties: vi.fn(async () => ({
    data: [
      { id: 'p1', name: 'Veritas Architects Sdn Bhd', party_type: 'architect' },
      { id: 'p2', name: 'Setia Land', party_type: 'developer' },
    ],
    pagination: { total: 2, page: 1, limit: 200 },
  })),
  listLeads: vi.fn(),
  getLead: vi.fn(),
  updateLead: vi.fn(),
  changeLeadStatus: vi.fn(),
  previewQualify: vi.fn(),
  qualifyLead: vi.fn(),
  disqualifyLead: vi.fn(),
  reopenLead: vi.fn(),
  deleteLead: vi.fn(),
  listDisqualifyReasons: vi.fn(async () => []),
  getLeadMetrics: vi.fn(),
  getCustomerPortfolio: vi.fn(),
  listProjects: vi.fn(),
}));

vi.mock('../../_shared/services/leadAcceptanceService', () => ({
  listAwaitingAcceptance: vi.fn(),
  assignLead: (...args: unknown[]) => assignLead(...args),
  acceptLead: vi.fn(),
  declineLead: vi.fn(),
  nudgeLeadAssignee: vi.fn(),
}));

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

vi.mock(
  '@/app/(protected)/order-management/shared/hooks/use-customer-select-query',
  () => ({
    useCustomerSelectQuery: () => ({
      data: [{ id: 'c1', customer_name: 'Sunway Construction', customer_code: 'C-001' }],
      isLoading: false,
    }),
  }),
);

import { LeadWizardDialog } from './LeadWizardDialog';

function renderWizard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onDone = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <LeadWizardDialog onDone={onDone} />
    </QueryClientProvider>,
  );
  return { onDone };
}

function next() {
  fireEvent.click(screen.getByRole('button', { name: /^Next$/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
  createLead.mockResolvedValue({ id: 'l1', lead_code: 'LEAD-000001', title: 'Tower' });
  assignLead.mockResolvedValue({
    id: 'l1',
    lead_code: 'LEAD-000001',
    owner_name: 'Ali',
  });
  getUserLookup.mockResolvedValue([{ id: 'u-ali', name: 'Ali' }]);
});

describe('LeadWizardDialog', () => {
  it('opens on the development, which is the only required answer', () => {
    renderWizard();

    expect(screen.getByLabelText(/What is it/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Next$/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/What is it/), {
      target: { value: 'Tower behind the showroom' },
    });
    expect(screen.getByRole('button', { name: /^Next$/ })).toBeEnabled();
  });

  it('collects the informant separately from the buyer', async () => {
    renderWizard();
    fireEvent.change(screen.getByLabelText(/What is it/), {
      target: { value: 'Tower behind the showroom' },
    });
    next();

    expect(await screen.findByLabelText('Their reference')).toBeInTheDocument();
    expect(screen.getByLabelText('Contact name')).toBeInTheDocument();
    expect(screen.getByLabelText('Often none')).toBeInTheDocument();
    // The buyer is nowhere near this step.
    expect(screen.queryByLabelText('Usually not known yet')).not.toBeInTheDocument();
  });

  it('records a lead with an informant and no buyer at all', async () => {
    const { onDone } = renderWizard();

    fireEvent.change(screen.getByLabelText(/What is it/), {
      target: { value: 'Tower behind the showroom' },
    });
    fireEvent.change(screen.getByLabelText('Location'), {
      target: { value: 'Setia Alam' },
    });
    next();

    fireEvent.change(await screen.findByLabelText('-'), {
      target: { value: 'bci' },
    });
    fireEvent.change(screen.getByLabelText('Their reference'), {
      target: { value: 'BCI-778812' },
    });
    fireEvent.change(screen.getByLabelText('Contact name'), {
      target: { value: 'Lim, QS' },
    });
    next();

    // Buyer step: deliberately left empty.
    expect(await screen.findByLabelText('Usually not known yet')).toHaveValue('');
    next();

    fireEvent.click(await screen.findByRole('button', { name: 'Record lead' }));

    await waitFor(() => expect(createLead).toHaveBeenCalledTimes(1));
    expect(createLead).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Tower behind the showroom',
        customer_id: null,
        new_customer: null,
        informant_source: 'bci',
        informant_ref: 'BCI-778812',
        informant_contact_name: 'Lim, QS',
        informant_party_id: null,
      }),
    );
    expect(assignLead).not.toHaveBeenCalled();
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it('assigns after creating when a salesperson is picked', async () => {
    renderWizard();

    fireEvent.change(screen.getByLabelText(/What is it/), {
      target: { value: 'Tower behind the showroom' },
    });
    next();
    next();
    next();

    const owner = await screen.findByLabelText('Leave with marketing for now');
    await waitFor(() => expect(getUserLookup).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'Ali' })).toBeInTheDocument(),
    );
    fireEvent.change(owner, { target: { value: 'u-ali' } });

    fireEvent.click(screen.getByRole('button', { name: 'Record lead' }));

    await waitFor(() => expect(assignLead).toHaveBeenCalledTimes(1));
    expect(assignLead).toHaveBeenCalledWith('l1', {
      owner_user_id: 'u-ali',
      note: null,
    });
  });

  it('keeps the lead when the assignment fails, rather than losing the whole entry', async () => {
    assignLead.mockRejectedValue(new Error('Ali has left'));
    const { onDone } = renderWizard();

    fireEvent.change(screen.getByLabelText(/What is it/), {
      target: { value: 'Tower behind the showroom' },
    });
    next();
    next();
    next();

    const owner = await screen.findByLabelText('Leave with marketing for now');
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'Ali' })).toBeInTheDocument(),
    );
    fireEvent.change(owner, { target: { value: 'u-ali' } });
    fireEvent.click(screen.getByRole('button', { name: 'Record lead' }));

    await waitFor(() => expect(createLead).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it('summarises who told us and says the buyer is not known yet', async () => {
    renderWizard();

    fireEvent.change(screen.getByLabelText(/What is it/), {
      target: { value: 'Tower behind the showroom' },
    });
    next();
    fireEvent.change(await screen.findByLabelText('-'), {
      target: { value: 'referral' },
    });
    next();
    next();

    expect(await screen.findByText('Not known yet')).toBeInTheDocument();
    expect(screen.getByText('Referral')).toBeInTheDocument();
  });
});
