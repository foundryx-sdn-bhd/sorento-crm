/**
 * P1 - LeadDetailClient (AC-A1, AC-A2, AC-A4, AC-A5) as URL-routed tabs.
 *
 * What is pinned here:
 * - one concern per tab, the tab read from `?tab=`, and an unknown tab falling back
 * - every tab renders its own loading, empty, error and data states
 * - the informant renders as names, never as an id, and never as the buyer
 * - a lead with no buyer says so deliberately and offers the next step
 * - Accept and Decline belong to the person holding the lead and nobody else
 * - a decline cannot be sent without a reason
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProjectLead } from '../../../_shared/types/project.types';
import type { LeadWithAcceptance } from '../../../_shared/types/leadAcceptance.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const getLead = vi.fn();
const updateLead = vi.fn();
const getCustomerPortfolio = vi.fn();
const acceptLead = vi.fn();
const declineLead = vi.fn();
const assignLead = vi.fn();
const getUserLookup = vi.fn();

let sessionUserId: string | undefined = 'u-ali';

/**
 * The router mock is reactive on purpose. The component reads the active tab from the
 * URL and writes it back with `replace`, so a mock that only records the call would let
 * a broken read pass: clicking a tab has to actually change what is on screen.
 */
let currentSearch = '';
const searchListeners = new Set<() => void>();
const subscribeSearch = (listener: () => void) => {
  searchListeners.add(listener);
  return () => {
    searchListeners.delete(listener);
  };
};
const readSearch = () => currentSearch;
const routerPush = vi.fn();
const routerReplace = vi.fn((url: string) => {
  const marker = url.indexOf('?');
  currentSearch = marker === -1 ? '' : url.slice(marker + 1);
  searchListeners.forEach((listener) => listener());
});

// The pager has its own tests; here it is only the first slot in the group.
vi.mock('@/components/common/ListPager', () => ({
  __esModule: true,
  default: () => <div data-testid="pager-slot" />,
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  // Without this the grid never leaves its skeleton: the real hook fetches saved column
  // order and `isLoading` gates the body rows, and nothing answers that call under jsdom.
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush, replace: routerReplace }),
  // The shared DataGrid falls back to the pathname when no listingKey is given, so every
  // tab that now renders a grid needs this present on the mock.
  usePathname: () => '/project-sales/leads/lead-1',
  useSearchParams: () =>
    new URLSearchParams(
      React.useSyncExternalStore(subscribeSearch, readSearch, readSearch),
    ),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: sessionUserId ? { user: { id: sessionUserId } } : null,
    status: 'authenticated',
  }),
}));

vi.mock(
  '@/app/(protected)/project-sales/_shared/hooks/useProjectStatusGraph',
  () => ({ useProjectStatusGraph: () => ({ data: { statuses: [] }, isLoading: false }) }),
);

vi.mock('../../../_shared/services/projectService', () => ({
  getLead: (...args: unknown[]) => getLead(...args),
  updateLead: (...args: unknown[]) => updateLead(...args),
  getCustomerPortfolio: (...args: unknown[]) => getCustomerPortfolio(...args),
  listParties: vi.fn(async () => ({
    data: [],
    pagination: { total: 0, page: 1, limit: 200 },
  })),
  listProjectTypes: vi.fn(async () => []),
  listProjectTemplates: vi.fn(async () => []),
  listLeads: vi.fn(),
  createLead: vi.fn(),
  changeLeadStatus: vi.fn(),
  previewQualify: vi.fn(),
  qualifyLead: vi.fn(),
  disqualifyLead: vi.fn(),
  reopenLead: vi.fn(),
  deleteLead: vi.fn(),
  listDisqualifyReasons: vi.fn(async () => []),
  getLeadMetrics: vi.fn(),
  listProjects: vi.fn(),
}));

vi.mock('../../../_shared/services/leadAcceptanceService', () => ({
  listAwaitingAcceptance: vi.fn(),
  assignLead: (...args: unknown[]) => assignLead(...args),
  acceptLead: (...args: unknown[]) => acceptLead(...args),
  declineLead: (...args: unknown[]) => declineLead(...args),
  nudgeLeadAssignee: vi.fn(),
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

vi.mock(
  '@/app/(protected)/order-management/shared/hooks/use-customer-select-query',
  () => ({
    useCustomerSelectQuery: () => ({
      data: [{ id: 'c1', customer_name: 'Sunway Construction', customer_code: 'C-001' }],
      isLoading: false,
    }),
  }),
);

import { LeadDetailClient } from './LeadDetailClient';

function lead(overrides: Partial<LeadWithAcceptance> = {}): ProjectLead {
  const row: LeadWithAcceptance = {
    id: 'l1',
    lead_code: 'LEAD-000001',
    title: 'Tower behind the showroom',
    customer_id: null,
    customer_name: null,
    developer_name: 'Setia Land',
    location: 'Setia Alam',
    outcome: 'open',
    // The header pill shows the RUNG now, not the derived outcome, so the fixture carries one.
    status_id: 's-new',
    status_key: 'new',
    status_label: 'New',
    project_count: 0,
    possible_duplicates: [],
    can_edit: true,
    informant_source: 'bci',
    informant_ref: 'BCI-778812',
    informant_party_id: '11111111-1111-1111-1111-111111111111',
    informant_party_label: 'Veritas Architects Sdn Bhd',
    informant_contact_name: 'Lim, QS',
    acceptance_state: 'assigned',
    owner_user_id: 'u-ali',
    owner_name: 'Ali',
    assigned_at: new Date(Date.now() - 50 * 3_600_000).toISOString().replace('Z', ''),
    ...overrides,
  };
  return row as ProjectLead;
}

function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LeadDetailClient leadId="l1" />
    </QueryClientProvider>,
  );
}

/**
 * Tabs are the shared `Tabs` strip since S4, exactly as on the project detail
 * page: `role="tablist"` / `role="tab"`, and the open one is `aria-selected`
 * rather than the `aria-current="page"` the hand-rolled `<nav>` set.
 */
/** Secondary actions live behind the gear now. Radix opens on pointerdown, not click. */
function openGearMenu() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Lead actions' }), {
    button: 0,
    ctrlKey: false,
  });
}

async function openTab(name: string) {
  // Radix activates a tab on mousedown, which jsdom does not synthesize
  // from a click.
  fireEvent.mouseDown(await screen.findByRole('tab', { name }), {
    button: 0,
    ctrlKey: false,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  currentSearch = '';
  sessionUserId = 'u-ali';
  getLead.mockResolvedValue(lead());
  updateLead.mockResolvedValue(lead());
  acceptLead.mockResolvedValue(lead({ acceptance_state: 'accepted' }));
  declineLead.mockResolvedValue(
    lead({ acceptance_state: 'declined', owner_user_id: null, owner_name: null }),
  );
  assignLead.mockResolvedValue(lead());
  getCustomerPortfolio.mockResolvedValue({ leads: [], projects: [] });
  getUserLookup.mockResolvedValue([
    { id: 'u-siti', name: 'Siti' },
  ]);
});

describe('LeadDetailClient page states', () => {
  it('shows a skeleton while the lead loads', () => {
    getLead.mockReturnValue(new Promise(() => {}));
    const { container } = renderDetail();
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  });

  it('reports a load failure instead of looking empty', async () => {
    getLead.mockRejectedValue(new Error('Lead is gone'));
    renderDetail();
    expect(await screen.findByText('Lead is gone')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to leads' })).toBeInTheDocument();
  });

  it('keeps the header above the tab strip, with the code, status and the handshake', async () => {
    renderDetail();

    expect(await screen.findByText('LEAD-000001')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Tower behind the showroom' })).toBeInTheDocument();
    // ONE pill, for the RUNG. The derived outcome used to render beside it, which read
    // "Open New" on a fresh lead and "Qualified Qualified" on a qualified one.
    expect(screen.getByText('New')).toBeInTheDocument();
    expect(screen.queryByText('open')).not.toBeInTheDocument();
    expect((await screen.findAllByText('Awaiting acceptance by Ali')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Waiting 2 days').length).toBeGreaterThan(0);
  });
});

describe('LeadDetailClient tabs', () => {
  it('offers one tab per concern and opens on Overview', async () => {
    renderDetail();

    const strip = within(await screen.findByRole('tablist', { name: 'Lead sections' }));
    for (const label of [
      'Overview',
      'Who told us',
      'Handover',
      'Buyer',
      'Projects',
      'Activity',
    ]) {
      expect(strip.getByRole('tab', { name: label })).toBeInTheDocument();
    }

    expect(strip.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByText('What we heard')).toBeInTheDocument();
  });

  it('renders one panel at a time, so the page never stacks into a scroll', async () => {
    renderDetail();

    await screen.findByText('What we heard');
    // The strip always names every tab, so a second match is the panel: while Overview
    // is open the other panels must contribute nothing beyond their tab button.
    expect(screen.getAllByText('Who told us')).toHaveLength(1);
    expect(screen.getAllByText('Handover')).toHaveLength(1);

    await openTab('Handover');
    await waitFor(() => expect(screen.getAllByText('Handover')).toHaveLength(2));
    expect(screen.queryByText('What we heard')).not.toBeInTheDocument();
  });

  it('writes the chosen tab to the url and marks it current', async () => {
    renderDetail();

    await openTab('Projects');

    expect(routerReplace).toHaveBeenCalledWith('/project-sales/leads/l1?tab=projects', {
      scroll: false,
    });
    const strip = within(screen.getByRole('tablist', { name: 'Lead sections' }));
    expect(strip.getByRole('tab', { name: 'Projects' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('drops the parameter again on the way back to Overview, so the plain url is the plain page', async () => {
    renderDetail();

    await openTab('Buyer');
    await openTab('Overview');

    expect(routerReplace).toHaveBeenLastCalledWith('/project-sales/leads/l1', {
      scroll: false,
    });
    expect(await screen.findByText('What we heard')).toBeInTheDocument();
  });

  it('opens on the tab named in the url', async () => {
    currentSearch = 'tab=buyer';
    renderDetail();

    expect(await screen.findByText('No buyer yet')).toBeInTheDocument();
    expect(screen.queryByText('What we heard')).not.toBeInTheDocument();
  });

  it('falls back to Overview when the url names a tab that does not exist', async () => {
    currentSearch = 'tab=invoices';
    renderDetail();

    expect(await screen.findByText('What we heard')).toBeInTheDocument();
  });

  it('keeps any other query parameter when switching tabs', async () => {
    currentSearch = 'from=worklist';
    renderDetail();

    await openTab('Activity');

    expect(routerReplace).toHaveBeenCalledWith(
      '/project-sales/leads/l1?from=worklist&tab=activity',
      { scroll: false },
    );
  });
});

describe('LeadDetailClient Overview tab', () => {
  it('shows what was heard, and stays silent about a note nobody wrote', async () => {
    renderDetail();

    expect(await screen.findByText('Setia Land')).toBeInTheDocument();
    expect(screen.getByText('Setia Alam')).toBeInTheDocument();
    // No coaching paragraph where the note would be: an unwritten note is an absence, and
    // the tab is not the place to teach what notes are for (ADR 1e).
    expect(screen.queryByText(/No notes yet/)).not.toBeInTheDocument();
  });

  it('shows the note and the rough value when there are some', async () => {
    getLead.mockResolvedValue(
      lead({ notes: 'Piling started, tender closes March', estimated_value: '2500000' }),
    );
    renderDetail();

    expect(await screen.findByText('Piling started, tender closes March')).toBeInTheDocument();
    expect(screen.getByText('RM 2,500,000')).toBeInTheDocument();
  });
});

describe('LeadDetailClient Who told us tab', () => {
  it('renders the informant by name and never by id', async () => {
    renderDetail();
    await openTab('Who told us');

    expect(await screen.findByText('Veritas Architects Sdn Bhd')).toBeInTheDocument();
    expect(screen.getByText('Lim, QS')).toBeInTheDocument();
    expect(screen.getByText('BCI-778812')).toBeInTheDocument();
    expect(screen.getByText('BCI')).toBeInTheDocument();
    expect(
      screen.queryByText('11111111-1111-1111-1111-111111111111'),
    ).not.toBeInTheDocument();
  });

  it('answers an unknown buyer with a dash, not a sentence', async () => {
    renderDetail();
    await openTab('Who told us');

    // "Buyer" and "Who told us" are both TAB labels too, so the term has to be matched
    // inside the card itself.
    await screen.findByText('Their reference');
    const card = document
      .querySelector('[data-slot="card-title"]')!
      .closest('[data-slot="card"]')!;
    expect(within(card as HTMLElement).getByText('Buyer')).toBeInTheDocument();
    expect(screen.queryByText('Not known yet')).not.toBeInTheDocument();
    expect(within(card as HTMLElement).getAllByText('-').length).toBeGreaterThan(0);
  });

  it('asks for a source when the lead carries none', async () => {
    getLead.mockResolvedValue(
      lead({
        informant_source: null,
        informant_ref: null,
        informant_party_id: null,
        informant_party_label: null,
        informant_contact_name: null,
      }),
    );
    renderDetail();
    await openTab('Who told us');

    // Dashes across the whole card ARE the answer; the paragraph that used to explain
    // why naming a source matters is gone.
    expect(await screen.findByText('Source')).toBeInTheDocument();
    expect(screen.queryByText(/Nobody is recorded as the source/)).not.toBeInTheDocument();
    expect(screen.getAllByText('-').length).toBeGreaterThan(2);
  });

  it('never sends an owner through the edit form, because that would skip the handshake', async () => {
    renderDetail();
    await openTab('Who told us');

    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(screen.queryByLabelText('Search people')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save informant' }));

    await waitFor(() => expect(updateLead).toHaveBeenCalled());
    const body = updateLead.mock.calls[0][1] as Record<string, unknown>;
    expect(body).not.toHaveProperty('owner_user_id');
  });

  it('saves the informant and the buyer together', async () => {
    renderDetail();
    await openTab('Who told us');

    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    // The dialog's own placeholder, which the SearchableSelect mock turns into the
    // accessible name. Unrelated to the detail card, where an unknown buyer now reads "-".
    fireEvent.change(await screen.findByLabelText('Not known yet'), {
      target: { value: 'c1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save informant' }));

    await waitFor(() =>
      expect(updateLead).toHaveBeenCalledWith(
        'l1',
        expect.objectContaining({
          customer_id: 'c1',
          informant_source: 'bci',
          informant_ref: 'BCI-778812',
          informant_contact_name: 'Lim, QS',
          informant_party_id: '11111111-1111-1111-1111-111111111111',
        }),
      ),
    );
  });
});

describe('LeadDetailClient Handover tab', () => {
  it('shows who holds it and since when', async () => {
    renderDetail();
    await openTab('Handover');

    expect(await screen.findByText('Ali')).toBeInTheDocument();
    expect(screen.getByText('Accepted')).toBeInTheDocument();
    expect(screen.queryByText('Not accepted yet')).not.toBeInTheDocument();
  });

  it('records the decline reason on the lead once declined', async () => {
    getLead.mockResolvedValue(
      lead({
        acceptance_state: 'declined',
        owner_user_id: null,
        owner_name: null,
        declined_reason: 'Johor team covers Nusajaya',
        declined_at: '2026-08-01T02:00:00',
      }),
    );
    renderDetail();
    await openTab('Handover');

    expect(await screen.findByText('Johor team covers Nusajaya')).toBeInTheDocument();
    expect(screen.getAllByText('Declined').length).toBeGreaterThan(0);
  });

  it('says nobody holds it, and offers the way out of that', async () => {
    sessionUserId = 'u-marketing';
    getLead.mockResolvedValue(
      lead({ acceptance_state: null, owner_user_id: null, owner_name: null }),
    );
    renderDetail();
    await openTab('Handover');

    // "Assigned to -" says it. What matters is that the way OUT is still offered.
    expect(screen.queryByText(/Nobody holds this lead/)).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /Assign it/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Assign it/ }));
    expect(await screen.findByLabelText('Search people')).toBeInTheDocument();
  });
});

describe('LeadDetailClient Buyer tab', () => {
  it('says there is no buyer yet, and offers to set it', async () => {
    renderDetail();
    await openTab('Buyer');

    expect(await screen.findByText('No buyer yet')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Set the buyer' }));
    expect(await screen.findByText('Who told us, and who buys')).toBeInTheDocument();
  });

  it('shows a skeleton while the account loads', async () => {
    getLead.mockResolvedValue(lead({ customer_id: 'c1', customer_name: 'Sunway' }));
    getCustomerPortfolio.mockReturnValue(new Promise(() => {}));
    const { container } = renderDetail();
    await openTab('Buyer');

    await waitFor(() =>
      expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0),
    );
  });

  it('reports an account that could not be loaded', async () => {
    getLead.mockResolvedValue(lead({ customer_id: 'c1', customer_name: 'Sunway' }));
    getCustomerPortfolio.mockRejectedValue(new Error('Account service is down'));
    renderDetail();
    await openTab('Buyer');

    // Both account lists report it: they are two independent grids off one query.
    expect((await screen.findAllByText('Account service is down')).length).toBe(2);
  });

  it('lists the rest of the account once it loads', async () => {
    getLead.mockResolvedValue(lead({ customer_id: 'c1', customer_name: 'Sunway' }));
    getCustomerPortfolio.mockResolvedValue({
      leads: [
        { id: 'l1', lead_code: 'LEAD-000001', title: 'Tower behind the showroom' },
        { id: 'l2', lead_code: 'LEAD-000002', title: 'Second phase' },
      ],
      projects: [
        {
          id: 'p1',
          project_code: 'PRJ-000009',
          title: 'Phase one podium',
          lead_id: 'l1',
          outcome: 'open',
          status_label: 'Tendering',
        },
      ],
    });
    renderDetail();
    await openTab('Buyer');

    expect(await screen.findByText(/LEAD-000002/)).toBeInTheDocument();
    expect(screen.getByText(/PRJ-000009/)).toBeInTheDocument();
    // Its own lead is never listed back at it as another lead.
    expect(screen.queryByText(/LEAD-000001 ·/)).not.toBeInTheDocument();
  });

  it('renders both account lists when empty, without a sentence in either', async () => {
    getLead.mockResolvedValue(lead({ customer_id: 'c1', customer_name: 'Sunway' }));
    renderDetail();
    await openTab('Buyer');

    // Both lists still render (CRUD standard), each stating its own emptiness as a heading
    // rather than as prose.
    expect(await screen.findByText('No other leads on this buyer')).toBeInTheDocument();
    expect(screen.getByText('No projects on this buyer')).toBeInTheDocument();
    expect(screen.queryByText('This is their only lead so far.')).not.toBeInTheDocument();
    expect(
      screen.queryByText('Nothing registered against this account yet.'),
    ).not.toBeInTheDocument();
  });
});

describe('LeadDetailClient Projects tab', () => {
  it('names the next step when the lead has produced nothing', async () => {
    renderDetail();
    await openTab('Projects');

    expect(await screen.findByText('Nothing registered from this lead yet')).toBeInTheDocument();
    // The paragraph about what qualifying does is gone.
    expect(screen.queryByText(/runs the duplicate check/)).not.toBeInTheDocument();
  });

  it('lists the projects the lead produced', async () => {
    getLead.mockResolvedValue(
      lead({ customer_id: 'c1', customer_name: 'Sunway', project_count: 1 }),
    );
    getCustomerPortfolio.mockResolvedValue({
      leads: [],
      projects: [
        {
          id: 'p1',
          project_code: 'PRJ-000009',
          title: 'Phase one podium',
          lead_id: 'l1',
          outcome: 'open',
          status_label: 'Tendering',
        },
        {
          id: 'p2',
          project_code: 'PRJ-000010',
          title: 'Another account project',
          lead_id: 'l9',
          outcome: 'open',
          status_label: 'Tendering',
        },
      ],
    });
    renderDetail();
    await openTab('Projects');

    expect(await screen.findByText(/PRJ-000009/)).toBeInTheDocument();
    expect(screen.queryByText(/PRJ-000010/)).not.toBeInTheDocument();
  });

  it('reports a failure to load them', async () => {
    getLead.mockResolvedValue(lead({ customer_id: 'c1', customer_name: 'Sunway' }));
    getCustomerPortfolio.mockRejectedValue(new Error('Account service is down'));
    renderDetail();
    await openTab('Projects');

    expect(await screen.findByText('Account service is down')).toBeInTheDocument();
  });

  it('shows a skeleton while they load', async () => {
    getLead.mockResolvedValue(lead({ customer_id: 'c1', customer_name: 'Sunway' }));
    getCustomerPortfolio.mockReturnValue(new Promise(() => {}));
    const { container } = renderDetail();
    await openTab('Projects');

    await waitFor(() =>
      expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0),
    );
  });
});

describe('LeadDetailClient Activity tab', () => {
  it('builds the history out of the stamps the lead already carries', async () => {
    getLead.mockResolvedValue(
      lead({
        created_at: '2026-07-01T02:00:00',
        accepted_at: '2026-07-03T02:00:00',
        qualified_at: '2026-07-09T02:00:00',
        project_count: 2,
      }),
    );
    renderDetail();
    await openTab('Activity');

    expect(await screen.findByText('Lead recorded')).toBeInTheDocument();
    expect(screen.getByText('Assigned to Ali')).toBeInTheDocument();
    expect(screen.getByText('Accepted by Ali')).toBeInTheDocument();
    expect(screen.getByText('Qualified')).toBeInTheDocument();
    expect(screen.getByText('2 projects registered')).toBeInTheDocument();
  });

  it('keeps an undated entry rather than dropping it', async () => {
    getLead.mockResolvedValue(
      lead({
        outcome: 'disqualified',
        disqualified_reason: 'no_budget',
        acceptance_state: null,
        assigned_at: null,
        owner_user_id: null,
        owner_name: null,
      }),
    );
    renderDetail();
    await openTab('Activity');

    expect(await screen.findByText('Disqualified')).toBeInTheDocument();
    expect(screen.getByText('no budget')).toBeInTheDocument();
    // The grid answers a missing stamp with "-", like every other unknown value.
    expect(screen.getAllByText('-').length).toBeGreaterThan(0);
  });

  it('says nothing has happened yet instead of showing an empty box', async () => {
    getLead.mockResolvedValue(
      lead({
        acceptance_state: null,
        assigned_at: null,
        owner_user_id: null,
        owner_name: null,
      }),
    );
    renderDetail();
    await openTab('Activity');

    expect(await screen.findByText(/Nothing has happened to this lead yet/)).toBeInTheDocument();
  });
});

describe('LeadDetailClient header actions', () => {
  it('reads pager, gear, primary from left to right (D6)', async () => {
    renderDetail();

    const group = await screen.findByTestId('lead-header-actions');
    const rendered = Array.from(group.children).map(
      (el) =>
        el.getAttribute('data-testid') ??
        el.getAttribute('aria-label') ??
        (el.textContent || '').trim(),
    );

    expect(rendered[0]).toBe('pager-slot');
    expect(rendered[1]).toBe('Lead actions');
    expect(rendered[2]).toContain('Accept');
  });

  it('offers Accept and Decline to the person holding it', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /Accept/ }));
    await waitFor(() => expect(acceptLead).toHaveBeenCalledWith('l1'));
  });

  it('hides Accept and Decline from everybody else', async () => {
    sessionUserId = 'u-marketing';
    renderDetail();

    await screen.findByText('What we heard');
    expect(screen.queryByRole('button', { name: /Accept/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Decline$/ })).not.toBeInTheDocument();
    // Marketing can still hand it to somebody else - behind the gear, since accepting is
    // not their move and the header carries exactly one action.
    openGearMenu();
    expect(await screen.findByRole('menuitem', { name: /Reassign/ })).toBeInTheDocument();
  });

  it('refuses to decline without a reason, and sends the reason when there is one', async () => {
    renderDetail();

    await screen.findByText('What we heard');
    // Accept is the primary action on an unanswered lead; Decline sits with the rest.
    openGearMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /^Decline$/ }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByRole('button', { name: 'Decline' })).toBeDisabled();

    fireEvent.change(dialog.getByLabelText(/Reason/), {
      target: { value: 'Outside my area' },
    });
    expect(dialog.getByRole('button', { name: 'Decline' })).toBeEnabled();
    fireEvent.click(dialog.getByRole('button', { name: 'Decline' }));

    await waitFor(() => expect(declineLead).toHaveBeenCalledWith('l1', 'Outside my area'));
  });

  it('assigns to somebody else from the gear menu', async () => {
    sessionUserId = 'u-marketing';
    renderDetail();

    await screen.findByText('What we heard');
    openGearMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Reassign/ }));

    const picker = await screen.findByLabelText('Search people');
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'Siti' })).toBeInTheDocument(),
    );
    fireEvent.change(picker, { target: { value: 'u-siti' } });
    fireEvent.click(screen.getByRole('button', { name: 'Assign' }));

    await waitFor(() =>
      expect(assignLead).toHaveBeenCalledWith('l1', {
        owner_user_id: 'u-siti',
        note: null,
      }),
    );
  });

  it('offers Assign on a declined lead, which is where marketing picks it back up', async () => {
    sessionUserId = 'u-marketing';
    getLead.mockResolvedValue(
      lead({
        acceptance_state: 'declined',
        owner_user_id: null,
        owner_name: null,
        can_edit: false,
        declined_reason: 'Johor team covers Nusajaya',
      }),
    );
    renderDetail();

    // Not "Reassign": nobody holds it. And it is the PRIMARY action, because an unheld
    // lead needs an owner before anything else can happen to it.
    expect(await screen.findByRole('button', { name: /^Assign$/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Reassign/ })).not.toBeInTheDocument();
  });
});
