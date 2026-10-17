/** NUMBER-FIELDS round 3 (R6): bad input in Rough value is refused with the sentence, nothing is sent. */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
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
    id,
    createOption,
  }: {
    value: string;
    onChange: (next: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
    id?: string;
    createOption?: { label: (q: string) => React.ReactNode; onCreate: (q: string) => void };
  }) => (
    <>
      <select
        id={id}
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
      {createOption ? (
        <button type="button" onClick={() => createOption.onCreate('Gamuda Builders')}>
          {createOption.label('Gamuda Builders')}
        </button>
      ) : null}
    </>
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

import { typeBadInput } from '@/test-utils/badInput';

describe('LeadWizardDialog bad number input (R6)', () => {
  it('Rough value with bad input: the lead is not created and the sentence shows under the box', async () => {
    renderWizard();
    fireEvent.change(screen.getByLabelText(/What is it/), {
      target: { value: 'Tower behind the showroom' },
    });
    next();
    next();
    typeBadInput(await screen.findByLabelText(/Rough value/));
    // Walk to the end; a screen that stops the walk (or sends it back here) is as good.
    for (let i = 0; i < 3; i += 1) {
      const create = screen.queryByRole('button', { name: 'Create lead' });
      if (create) {
        fireEvent.click(create);
        break;
      }
      const nextButton = screen.queryByRole('button', { name: /^Next$/ });
      if (!nextButton) break;
      fireEvent.click(nextButton);
      await screen.findAllByRole("button", { name: /^Back$|^Next$|Create lead/ });
    }
    expect(
      await screen.findByText('Enter a number of at least 0, or leave it blank.'),
    ).toBeInTheDocument();
    expect(createLead).not.toHaveBeenCalled();
  });

  it('S4: no sentence while typing, Next stays enabled, a refused press shows it and stays, a valid value clears it', async () => {
    renderWizard();
    fireEvent.change(screen.getByLabelText(/What is it/), {
      target: { value: 'Tower behind the showroom' },
    });
    next();
    next();
    const box = await screen.findByLabelText(/Rough value/);
    typeBadInput(box);
    // Typing alone says nothing and does not lock the button (owner Q1 / Q4).
    expect(screen.queryByText(/Enter a number/)).not.toBeInTheDocument();
    const nextButton = screen.getByRole('button', { name: /^Next$/ });
    expect(nextButton).not.toBeDisabled();
    // The press is refused: the sentence shows and the walk does not advance.
    fireEvent.click(nextButton);
    expect(await screen.findByText(/Enter a number/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Rough value/)).toBeInTheDocument();
    // From then on it is live: a valid value clears it.
    fireEvent.change(screen.getByLabelText(/Rough value/), { target: { value: '1500.50' } });
    expect(screen.queryByText(/Enter a number/)).not.toBeInTheDocument();
  });
});
