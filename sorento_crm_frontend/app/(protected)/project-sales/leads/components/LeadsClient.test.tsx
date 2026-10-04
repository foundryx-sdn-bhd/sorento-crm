/**
 * S2c - LeadsClient (AC-O3, AC-O6), now on the shared DataGrid.
 *
 * Three things carry the design and are pinned here:
 * - the rows are the SHARED grid, with a pinned listing key rather than the pathname
 *     default, because a list that looks unlike every other list is a list people have
 *     to re-learn
 * - a duplicate is SURFACED, never enforced, and it names the other person so the
 *     race becomes a conversation
 * - search, filters and export share ONE toolbar row and nothing sits above the grid:
 *     the summary tiles were removed, and the metrics request with them
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadWithAcceptance } from '../../_shared/types/leadAcceptance.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const listLeads = vi.fn();
const getLeadMetrics = vi.fn();
const listingKeys: (string | null | undefined)[] = [];

vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/leads',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  // The list restores its page, sort and filters from the query string Back hands
  // it (S3-01), so it reads the URL on every render.
  useSearchParams: () => new URLSearchParams(''),
}));

// The DataGrid persists column preferences over the network; stub that away, and
// record the key it was given so the pathname fallback cannot creep back in.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: ({ listingKey }: { listingKey?: string | null }) => {
    listingKeys.push(listingKey);
    return { resetToDefaults: async () => {}, isLoading: false };
  },
}));

vi.mock('../../_shared/services/projectService', () => ({
  listLeads: (...args: unknown[]) => listLeads(...args),
  getLeadMetrics: (...args: unknown[]) => getLeadMetrics(...args),
  getLead: vi.fn(),
  createLead: vi.fn(),
  updateLead: vi.fn(),
  changeLeadStatus: vi.fn(),
  previewQualify: vi.fn(),
  qualifyLead: vi.fn(),
  disqualifyLead: vi.fn(),
  reopenLead: vi.fn(),
  deleteLead: vi.fn(),
  listDisqualifyReasons: vi.fn(async () => []),
  getCustomerPortfolio: vi.fn(),
  listProjectTasks: vi.fn(),
  createProjectTask: vi.fn(),
  updateProjectTask: vi.fn(),
  changeTaskStatus: vi.fn(),
  deleteProjectTask: vi.fn(),
  getTaskHistory: vi.fn(),
  listMyTasks: vi.fn(),
  listTemplateTasks: vi.fn(),
  createTemplateTask: vi.fn(),
  updateTemplateTask: vi.fn(),
  deleteTemplateTask: vi.fn(),
  listProjects: vi.fn(),
  getProject: vi.fn(),
  listProjectTypes: vi.fn(),
  listProjectTemplates: vi.fn(),
  listParties: vi.fn(async () => ({ data: [], pagination: { total: 0, page: 1, limit: 50 } })),
  listStakeholders: vi.fn(),
  listCollaborators: vi.fn(),
  listTakeoverRequests: vi.fn(),
  previewClashes: vi.fn(),
  registerProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
  changeProjectStatus: vi.fn(),
  addStakeholder: vi.fn(),
  updateStakeholder: vi.fn(),
  removeStakeholder: vi.fn(),
  createParty: vi.fn(),
  updateParty: vi.fn(),
  deleteParty: vi.fn(),
  createTakeoverRequest: vi.fn(),
  decideTakeoverRequest: vi.fn(),
  createProjectType: vi.fn(),
  updateProjectType: vi.fn(),
  deleteProjectType: vi.fn(),
  createProjectTemplate: vi.fn(),
  updateProjectTemplate: vi.fn(),
  deleteProjectTemplate: vi.fn(),
}));

vi.mock('../../_shared/services/leadAcceptanceService', () => ({
  listAwaitingAcceptance: vi.fn(),
  assignLead: vi.fn(),
  acceptLead: vi.fn(),
  declineLead: vi.fn(),
  nudgeLeadAssignee: vi.fn(),
}));

vi.mock('@/services/userSelectService', () => ({
  getUserLookup: vi.fn(async () => []),
}));

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

vi.mock(
  '@/app/(protected)/order-management/shared/hooks/use-customer-select-query',
  () => ({ useCustomerSelectQuery: () => ({ data: [], isLoading: false }) }),
);

import { LeadsClient } from './LeadsClient';

function lead(overrides: Partial<LeadWithAcceptance> = {}): LeadWithAcceptance {
  return {
    id: 'l1',
    lead_code: 'LEAD-000001',
    title: 'Tower behind the showroom',
    customer_id: 'c1',
    customer_name: 'Veritas Architects',
    outcome: 'open',
    project_count: 0,
    possible_duplicates: [],
    can_edit: true,
    ...overrides,
  };
}

const NO_METRICS = {
  total: 0,
  open: 0,
  qualified: 0,
  disqualified: 0,
  decided: 0,
  conversion_rate: null,
  projects_from_leads: 0,
  disqualified_reasons: [],
};

function renderClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LeadsClient />
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
  listLeads.mockResolvedValue({
    data: [],
    pagination: { total: 0, page: 1, limit: 25 },
  });
  getLeadMetrics.mockResolvedValue(NO_METRICS);
});

describe('LeadsClient', () => {
  it('opens on OPEN leads, because the list is a worklist and not an archive', async () => {
    renderClient();

    await waitFor(() =>
      expect(listLeads).toHaveBeenCalledWith(
        expect.objectContaining({ outcome: ['open'] }),
      ),
    );
  });

  it('asks the server for one page at a time, sorted newest first', async () => {
    renderClient();

    await waitFor(() =>
      expect(listLeads).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, limit: 25, sort: 'created_at', dir: 'desc' }),
      ),
    );
  });

  it('pins its own listing key rather than falling back to the pathname', async () => {
    renderClient();

    await waitFor(() => expect(listingKeys.length).toBeGreaterThan(0));
    expect(listingKeys).toContain('projects.projects.view::leads');
    expect(listingKeys).not.toContain('/project-sales/leads');
  });

  it('keeps the grid toolbar while loading, so the page does not jump', () => {
    listLeads.mockReturnValue(new Promise(() => {}));
    renderClient();

    expect(screen.getByRole('button', { name: /filters/i })).toBeInTheDocument();
  });

  it('carries no summary tiles above the grid, and never asks for the metrics', async () => {
    // The tiles pushed the rows below the fold to answer a reporting question on a
    // worklist. The client asked for them gone; the request that fed them goes too.
    renderClient();

    await waitFor(() => expect(listLeads).toHaveBeenCalled());
    expect(getLeadMetrics).not.toHaveBeenCalled();
    expect(screen.queryByText('Conversion')).not.toBeInTheDocument();
    expect(screen.queryByText('No decisions yet')).not.toBeInTheDocument();
  });

  it('lays search, filters and export out on the grid toolbar, in one row', async () => {
    renderClient();

    const toolbar = (await screen.findByPlaceholderText(/Search title or lead code/i))
      .closest('[data-slot="card-header"]');
    expect(toolbar).not.toBeNull();
    // Filters and Export sit beside the search box rather than in a strip of their own.
    expect(
      within(toolbar as HTMLElement).getByRole('button', { name: /filters/i }),
    ).toBeInTheDocument();
    expect(
      within(toolbar as HTMLElement).getByRole('button', { name: /^export$/i }),
    ).toBeInTheDocument();
    expect(
      within(toolbar as HTMLElement).getByRole('button', { name: /columns/i }),
    ).toBeInTheDocument();
  });

  it('surfaces a duplicate by naming the other owner, and never as a warning', async () => {
    listLeads.mockResolvedValue({
      data: [
        lead({
          possible_duplicates: [
            { lead_id: 'l2', lead_code: 'LEAD-000002', owner_name: 'Siti' },
          ],
        }),
      ],
      pagination: { total: 1, page: 1, limit: 25 },
    });

    renderClient();

    const hint = await screen.findByText(/Also recorded by Siti/);
    expect(hint).toBeInTheDocument();
    expect(hint).toHaveAttribute(
      'title',
      'Also recorded by Siti. Leads are not exclusive, so both stand.',
    );
    // No blocking language anywhere: recording a duplicate lead is allowed.
    expect(screen.queryByText(/already registered/i)).not.toBeInTheDocument();
  });

  it('explains that a lead claims nothing when there are none', async () => {
    renderClient();

    expect(await screen.findByText('No open leads')).toBeInTheDocument();
    expect(
      screen.getByText(/it becomes somebody's only when they accept it/i),
    ).toBeInTheDocument();
  });

  // #1335: one CTA per page. The header's "Record a lead" is the only one; the
  // empty state keeps its heading and hint, and no subtitle sits under the title.
  it('offers Record a lead once, in the header, and it opens the wizard', async () => {
    renderClient();

    await screen.findByText('No open leads');
    const buttons = screen.getAllByRole('button', { name: /record a lead/i });
    expect(buttons).toHaveLength(1);
    expect(buttons[0].closest('[data-slot="toolbar-actions"]')).not.toBeNull();
    expect(
      screen.queryByText(/Developments we have heard about/i),
    ).not.toBeInTheDocument();

    fireEvent.click(buttons[0]);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Record a lead')).toBeInTheDocument();
  });

  it('distinguishes an empty filter result from an empty pipeline', async () => {
    renderClient();
    await screen.findByText('No open leads');

    openFilters();
    fireEvent.change(await screen.findByLabelText('All outcomes'), {
      target: { value: 'qualified' },
    });

    expect(await screen.findByText('No leads match these filters')).toBeInTheDocument();
    await waitFor(() =>
      expect(listLeads).toHaveBeenCalledWith(
        expect.objectContaining({ outcome: ['qualified'] }),
      ),
    );
  });

  it('shows how many projects a lead produced, because one lead may produce several', async () => {
    listLeads.mockResolvedValue({
      data: [lead({ project_count: 3, outcome: 'qualified' })],
      pagination: { total: 1, page: 1, limit: 25 },
    });

    renderClient();

    expect(await screen.findByText(/3 projects/)).toBeInTheDocument();
  });

  it('shows who told us, that the buyer is unknown, and how long acceptance has waited', async () => {
    listLeads.mockResolvedValue({
      data: [
        lead({
          customer_id: null,
          customer_name: null,
          developer_name: 'Setia Land',
          location: 'Setia Alam, Selangor',
          estimated_value: '1800000',
          informant_source: 'bci',
          informant_party_label: 'Veritas Architects Sdn Bhd',
          informant_ref: 'BCI-778812',
          acceptance_state: 'assigned',
          owner_user_id: 'u-ali',
          owner_name: 'Ali',
          assigned_at: new Date(Date.now() - 50 * 3_600_000)
            .toISOString()
            .replace('Z', ''),
        }),
      ],
      pagination: { total: 1, page: 1, limit: 25 },
    });

    renderClient();

    expect(
      await screen.findByText('Veritas Architects Sdn Bhd · BCI · BCI-778812'),
    ).toBeInTheDocument();
    expect(screen.getByText('Not known yet')).toBeInTheDocument();
    expect(screen.getByText('Awaiting acceptance by Ali')).toBeInTheDocument();
    expect(screen.getByText('Waiting 2 days')).toBeInTheDocument();
    // Every fact the old cards carried is still a column.
    expect(screen.getByText('Setia Land')).toBeInTheDocument();
    expect(screen.getByText('Setia Alam, Selangor')).toBeInTheDocument();
    expect(screen.getByText('RM 1,800,000')).toBeInTheDocument();
    // No UUID reaches the screen.
    expect(screen.queryByText('l1')).not.toBeInTheDocument();
    expect(screen.queryByText('u-ali')).not.toBeInTheDocument();
  });

  it('offers assign and delete on the row, and only where they are allowed', async () => {
    listLeads.mockResolvedValue({
      data: [
        lead({ id: 'l1', lead_code: 'LEAD-000001', can_edit: true }),
        lead({
          id: 'l2',
          lead_code: 'LEAD-000002',
          outcome: 'qualified',
          can_edit: false,
        }),
      ],
      pagination: { total: 2, page: 1, limit: 25 },
    });

    renderClient();

    expect(
      await screen.findByRole('button', { name: 'Assign LEAD-000001' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Delete LEAD-000001' }),
    ).toBeInTheDocument();
    // A qualified lead nobody may edit is read only: no assign, no delete.
    expect(
      screen.queryByRole('button', { name: /LEAD-000002/ }),
    ).not.toBeInTheDocument();
  });

  it('confirms before deleting, and says it cannot be undone', async () => {
    listLeads.mockResolvedValue({
      data: [lead()],
      pagination: { total: 1, page: 1, limit: 25 },
    });

    renderClient();

    fireEvent.click(await screen.findByRole('button', { name: 'Delete LEAD-000001' }));

    expect(await screen.findByText('Confirm delete')).toBeInTheDocument();
    expect(
      screen.getByText(/This action cannot be undone/),
    ).toBeInTheDocument();
  });

  it('reports a load failure rather than looking empty', async () => {
    listLeads.mockRejectedValue(new Error('Backend is down'));

    renderClient();

    expect(await screen.findByText('Backend is down')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});
