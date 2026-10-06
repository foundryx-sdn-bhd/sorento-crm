/**
 * AccessAgentDetail - the member row carries both routing editors
 * (brand-aware routing revision 2, AC2-F1).
 *
 * Brand tags live on the PERSON, next to their market segments, so the roster
 * inside a team assignment is the one surface that has to render both. The
 * editors themselves are covered by their own suites; what this asserts is the
 * wiring - every member row gets a segment editor AND a brand editor, keyed by
 * that row's (team_id, user_id).
 *
 * Every other collaborator of AccessAgentDetail is stubbed so the assertions are
 * about that wiring, not about field access / contact tables / delete dialogs.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Radix components (Collapsible/Popover) read element size via ResizeObserver,
// absent in jsdom.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
  ResizeObserverStub;

import AccessAgentDetail from './AccessAgentDetail';

// --- hook mocks ------------------------------------------------------------
type Member = { id: string; name?: string; email?: string };
type Assignment = {
  code: string;
  team_id: string;
  tier?: number | null;
  team_name?: string;
  members?: Member[];
};

let agentTeamsAssignments: Assignment[] = [];

const STABLE_AGENT = {
  id: 'agent-1',
  code: 'AGENT1',
  name: 'Agent 1',
  description: '',
  is_active: true,
  assign_to_new_internal_contacts: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
};

// The gear resolves permissions; this test has no session or query client, and
// RBAC has its own tests.
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

// The pager has its own tests (hooks/useListPager.test.ts).
vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

vi.mock('../hooks/useAccessAgents', () => ({
  useAccessAgent: () => ({ data: STABLE_AGENT, isLoading: false }),
  accessAgentsPagerQuery: {
    listQueryKey: () => ['access-agents'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  useAgentTeams: () => ({
    data: { assignments: agentTeamsAssignments },
    refetch: vi.fn(),
    isRefetching: false,
  }),
  useTeams: () => ({
    data: [
      { id: 'team-1', name: 'Team One' },
      { id: 'team-2', name: 'Team Two' },
    ],
  }),
}));

vi.mock('@/app/providers/CompanyProvider', () => ({
  useCompany: () => ({
    activeCompany: {
      id: 'company-1',
      name: 'Sorento',
      code: 'SORENTO',
      is_active: true,
    },
    companies: [],
    grants: [],
    setActiveCompany: vi.fn(),
    isLoading: false,
  }),
}));

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// Children unrelated to the roster wiring - stub so this file tests only
// AccessAgentDetail's own rendering logic.
vi.mock('./AccessAgentFormModal', () => ({
  default: () => <div data-testid="edit-modal-stub" />,
}));
vi.mock('./AccessAgentNavigation', () => ({
  default: () => <div data-testid="nav-stub" />,
}));
vi.mock('./AgentFieldAccessCard', () => ({
  default: () => <div data-testid="field-access-stub" />,
}));
vi.mock('./ContactAccessAgentsTable', () => ({
  default: () => <div data-testid="contact-agents-stub" />,
}));
vi.mock('./access-agent-delete-dialog', () => ({
  default: () => <div data-testid="delete-dialog-stub" />,
}));
vi.mock('./MemberMarketSegmentEditor', () => ({
  default: ({ teamId, userId }: { teamId: string; userId: string }) => (
    <div data-testid="segment-editor-stub">{`${teamId}:${userId}`}</div>
  ),
}));
vi.mock('./MemberBrandEditor', () => ({
  default: ({ teamId, userId }: { teamId: string; userId: string }) => (
    <div data-testid="brand-editor-stub">{`${teamId}:${userId}`}</div>
  ),
}));

function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AccessAgentDetail accessAgentId="agent-1" />
    </QueryClientProvider>,
  );
}

/** The roster lives inside a collapsible; open the one team assignment row. */
function expandTheTeam() {
  fireEvent.click(screen.getByText('Team One'));
}

describe('AccessAgentDetail team roster', () => {
  beforeEach(() => {
    push.mockReset();
    agentTeamsAssignments = [];
  });

  it('renders a brand editor next to the segment editor for every member', () => {
    agentTeamsAssignments = [
      {
        code: 'marketing_promotion',
        team_id: 'team-1',
        tier: 1,
        team_name: 'Team One',
        members: [
          { id: 'user-1', name: 'Am' },
          { id: 'user-2', name: 'Kia Yee' },
        ],
      },
    ];
    renderDetail();
    expandTheTeam();

    expect(
      screen.getAllByTestId('segment-editor-stub').map((n) => n.textContent),
    ).toEqual(['team-1:user-1', 'team-1:user-2']);
    expect(
      screen.getAllByTestId('brand-editor-stub').map((n) => n.textContent),
    ).toEqual(['team-1:user-1', 'team-1:user-2']);
  });

  it('renders the empty state instead of editors for a team with no members', () => {
    agentTeamsAssignments = [
      {
        code: 'marketing_promotion',
        team_id: 'team-1',
        tier: 1,
        team_name: 'Team One',
        members: [],
      },
    ];
    renderDetail();
    expandTheTeam();

    expect(screen.getByText(/no members in this team/i)).toBeInTheDocument();
    expect(screen.queryByTestId('brand-editor-stub')).not.toBeInTheDocument();
  });

  it('no longer renders the legacy field access card (the access tree owns incoming fields)', () => {
    renderDetail();
    expect(screen.queryByTestId('field-access-stub')).not.toBeInTheDocument();
  });
});
