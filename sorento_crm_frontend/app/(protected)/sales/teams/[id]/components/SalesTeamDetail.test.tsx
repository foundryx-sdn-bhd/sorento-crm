/**
 * The team's own page, `/sales/teams/{id}` (UAC S6-13, S6-15; owner ruling 26 Sep 06:01
 * (Lavish) N5 and 26 Sep 06:09 T2; the S1 hand test of 27 Sep, F2).
 *
 * The Users record pattern: a header card (name, Active badge, agent count, Created, Updated;
 * prev/next, the gear, Set target), then line Tabs in the order Details, Targets, Agents. Edit
 * is an item in the gear dropdown, never a standalone button; editing keeps the same tabs and
 * swaps values for inputs in place. An agent who left this month keeps a muted line with a
 * "Left 14 Oct" pill.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({
  usePathname: () => '/sales/teams/north',
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
}));

// The gear renders its items as buttons inside `data-testid="gear"`, so a test can tell an
// item in the dropdown from a standalone button on the page.
vi.mock('@/components/common/DetailActions', () => ({
  default: ({
    pagerNode,
    actions,
    primary,
    pendingAction,
  }: {
    pagerNode?: React.ReactNode;
    actions?: { key: string; label: string; run: () => void }[];
    primary?: React.ReactNode;
    pendingAction?: React.ReactNode;
  }) => (
    <div data-testid="detail-actions">
      {pagerNode}
      <div data-testid="gear">
        {(actions ?? []).map((a) => (
          <button key={a.key} type="button" onClick={() => a.run()}>
            {a.label}
          </button>
        ))}
      </div>
      {pendingAction ?? primary}
    </div>
  ),
}));

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    value: string[];
    onChange: (v: string[]) => void;
    options?: { value: string; label: string }[];
  }) => (
    <fieldset aria-label="Add agents">
      {(props.options ?? []).map((o) => (
        <label key={o.value}>
          <input
            type="checkbox"
            checked={props.value.includes(o.value)}
            onChange={(e) =>
              props.onChange(
                e.target.checked
                  ? [...props.value, o.value]
                  : props.value.filter((v) => v !== o.value),
              )
            }
          />
          {o.label}
        </label>
      ))}
    </fieldset>
  ),
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    value: string;
    onChange: (v: string) => void;
    options?: { value: string; label: string }[];
    clearable?: boolean;
  }) => (
    <select
      id={props.id}
      data-clearable={props.clearable ? 'true' : 'false'}
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
    >
      <option value="">No leader</option>
      {(props.options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

const perms = vi.hoisted(() => ({ granted: new Set<string>() }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => perms.granted.has(slug),
}));

const save = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const hooks = vi.hoisted(() => ({
  useSalesTeam: vi.fn(),
  useSalesTeams: vi.fn(),
  useSalesTeamAgentOptions: vi.fn(),
  useSaveSalesTeam: () => save,
}));
vi.mock('../../hooks/useSalesTeams', () => hooks);
vi.mock('../../actions', () => ({
  useSalesTeamActions: () => ({ actions: [], dialogs: null, pending: null }),
}));

// S1: the Targets tab reads every team target of the team (`all`), and each agent row's
// Targets/Target/Achieved/% cells read `subject=agent&sales_team_id=` on the date.
const targetsHooks = vi.hoisted(() => ({ useSalesTargets: vi.fn() }));
vi.mock('../../../targets/hooks/useSalesTargets', () => targetsHooks);

import { SalesTeamDetail } from './SalesTeamDetail';
import type { SalesTeamDetail as Detail } from '../../types/salesTeam.types';

function detail(over: Partial<Detail> = {}): Detail {
  return {
    id: 'north',
    name: 'North',
    is_active: true,
    leader_sales_agent_id: null,
    leader_label: null,
    member_count: 2,
    on: '2026-10-20',
    members: [
      member('ali', 'ALI - Ali Hassan'),
      member('mei', 'MEI - Tan Mei Ling'),
      { ...member('kim', 'KIM - Kim Tan'), valid_to: '2026-10-14', left: true },
    ],
    moved: [],
    created_at: '2026-09-26T01:00:00',
    updated_at: '2026-09-30T01:00:00',
    ...over,
  };
}

function member(id: string, label: string) {
  return {
    sales_agent_id: id,
    code: label.split(' - ')[0],
    name: label.split(' - ')[1],
    label,
    valid_from: null,
    valid_to: null,
    left: false,
  };
}

beforeEach(() => {
  router.push.mockReset();
  perms.granted = new Set(['sales.teams.view', 'sales.teams.edit', 'sales.teams.delete']);
  save.mutateAsync.mockReset();
  save.mutateAsync.mockResolvedValue({});
  hooks.useSalesTeam.mockReturnValue({ data: detail(), isLoading: false, isError: false });
  hooks.useSalesTeams.mockReturnValue({
    data: { data: [{ id: 'central' }, { id: 'north' }, { id: 'south' }] },
    isLoading: false,
  });
  hooks.useSalesTeamAgentOptions.mockReturnValue({
    data: [
      { id: 'ali', code: 'ALI', label: 'ALI - Ali Hassan', team_id: 'north', team_name: 'North' },
      { id: 'sean', code: 'SEAN I', label: 'SEAN I - CONTACT AE', team_id: 'central', team_name: 'Central' },
      { id: 'raj', code: 'RAJ', label: 'RAJ - Raj Kumar', team_id: null, team_name: null },
    ],
    isLoading: false,
  });
  targetsHooks.useSalesTargets.mockReturnValue({
    data: { on: '2026-10-20', rows: [], unassigned_amount: 0, no_team_count: 0 },
    isLoading: false, isFetching: false, isError: false,
  });
});

function openTab(name: string) {
  const tab = screen.getByRole('tab', { name });
  fireEvent.mouseDown(tab);
  fireEvent.click(tab);
}

function editFromGear() {
  fireEvent.click(within(screen.getByTestId('gear')).getByRole('button', { name: 'Edit' }));
}

const TEAM_TARGET_ROW = {
  target_id: 'tt1', target_no: 'TGT-000001', name: 'North FY26 H2', subject_kind: 'team',
  sales_agent_id: null, sales_team_id: 'north', subject_label: 'North', team_id: 'north',
  team_name: 'North', left_on: null, members: null,
  metric: 'amount', basis: 'ordered', product_scope: 'brands', scope_labels: ['MOC - Mocha'],
  period_id: null, period_start: null, period_end: null, start_date: '2026-10-01',
  end_date: '2026-12-31', target_value: 3000, achieved_value: 1500, achieved_pct: 50,
  parent_target_id: null,
};

function answerTargets() {
  targetsHooks.useSalesTargets.mockImplementation(
    ({ subject }: { subject: 'team' | 'agent' }) => {
      const agentRow = {
        ...TEAM_TARGET_ROW, target_id: 'ta1', target_no: 'TGT-000002', subject_kind: 'agent',
        sales_agent_id: 'ali', sales_team_id: null, subject_label: 'ALI - Ali Hassan',
        period_id: 'p1', period_start: '2026-10-01', period_end: '2026-10-31',
        target_value: 1000, achieved_value: 500, achieved_pct: 40, parent_target_id: 'tt1',
      };
      return {
        data: {
          on: '2026-10-20',
          rows: subject === 'team' ? [TEAM_TARGET_ROW] : [agentRow],
          unassigned_amount: 0, no_team_count: 0,
        },
        isLoading: false, isFetching: false, isError: false,
      };
    },
  );
}

describe('SalesTeamDetail', () => {
  it('heads the record with the name, Active badge and agent count, plus prev/next', () => {
    render(<SalesTeamDetail id="north" />);
    expect(screen.getByRole('heading', { name: 'North' })).toBeTruthy();
    expect(screen.getByText('Active')).toBeTruthy();
    expect(screen.getByText('2 agents')).toBeTruthy();
    expect(screen.getByText('1 left this month')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Previous sales team' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Next sales team' })).toBeTruthy();
    // RecordNavigation's index is 1-based: North is 2nd of central/north/south.
    expect(screen.getByText('2 / 3')).toBeTruthy();
  });

  it('shows the first team as "1 / 3", not "- / 3"', () => {
    hooks.useSalesTeam.mockReturnValue({
      data: detail({ id: 'central', name: 'Central' }),
      isLoading: false,
      isError: false,
    });
    render(<SalesTeamDetail id="central" />);
    expect(screen.getByText('1 / 3')).toBeTruthy();
  });

  it('lays the record out in line tabs, in the order Details, Targets, Agents (F2)', () => {
    render(<SalesTeamDetail id="north" />);
    const tabs = screen.getAllByRole('tab').map((t) => t.textContent?.trim());
    expect(tabs).toEqual(['Details', 'Targets', 'Agents']);
    // Details is open first.
    expect(screen.getByRole('region', { name: 'Details' })).toBeTruthy();
  });

  it('puts Edit in the gear dropdown, never as a standalone button (F2)', () => {
    render(<SalesTeamDetail id="north" />);
    const gear = screen.getByTestId('gear');
    expect(within(gear).getByRole('button', { name: 'Edit' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1);
  });

  it('names the leader once, in Details, not as a second summary line in the header (F2)', () => {
    hooks.useSalesTeam.mockReturnValue({
      data: detail({ leader_sales_agent_id: 'mei', leader_label: 'MEI - Tan Mei Ling' }),
      isLoading: false,
      isError: false,
    });
    render(<SalesTeamDetail id="north" />);
    const details = screen.getByRole('region', { name: 'Details' });
    expect(within(details).getByText('MEI - Tan Mei Ling')).toBeTruthy();
    expect(screen.queryByText('Leader: MEI - Tan Mei Ling')).toBeNull();
    expect(screen.getAllByText('MEI - Tan Mei Ling')).toHaveLength(1);
  });

  it('says "No leader" in Details when none is picked', () => {
    render(<SalesTeamDetail id="north" />);
    const details = screen.getByRole('region', { name: 'Details' });
    expect(within(details).getByText('No leader')).toBeTruthy();
  });

  it('Targets lists every team target of the team, with its number, dates and % (F2, F3)', () => {
    answerTargets();
    render(<SalesTeamDetail id="north" />);
    openTab('Targets');
    expect(targetsHooks.useSalesTargets).toHaveBeenCalledWith(
      { all: true, subject: 'team', salesTeamId: 'north' },
    );
    const targets = screen.getByRole('region', { name: 'Team targets' });
    expect(within(targets).getByText('TGT-000001')).toBeTruthy();
    expect(within(targets).getByText('North FY26 H2')).toBeTruthy();
    expect(within(targets).getByText('1 Oct to 31 Dec 2026')).toBeTruthy();
    expect(within(targets).getByText('50%')).toBeTruthy();
    expect(within(targets).getByText('1 brand')).toBeTruthy();
  });

  it('Targets shows "No team target" with Set target, which opens the new target record (F4)', () => {
    perms.granted = new Set(['sales.teams.view', 'sales.teams.edit', 'sales.targets.add']);
    render(<SalesTeamDetail id="north" />);
    openTab('Targets');
    const targets = screen.getByRole('region', { name: 'Team targets' });
    expect(within(targets).getByText('No team target')).toBeTruthy();
    fireEvent.click(within(targets).getByRole('button', { name: /set target/i }));
    expect(router.push).toHaveBeenLastCalledWith('/sales/targets/new?kind=team&subject=north');
  });

  it('shows no Set target without sales.targets.add', () => {
    render(<SalesTeamDetail id="north" />);
    expect(screen.queryByRole('button', { name: /set target/i })).toBeNull();
    openTab('Targets');
    const targets = screen.getByRole('region', { name: 'Team targets' });
    expect(within(targets).queryByRole('button', { name: /set target/i })).toBeNull();
  });

  it('Agents lists the agents with their %, and keeps a muted "Left 14 Oct" line', () => {
    answerTargets();
    render(<SalesTeamDetail id="north" />);
    openTab('Agents');
    const agents = screen.getByRole('region', { name: 'Agents' });
    const ali = within(agents).getByText('ALI - Ali Hassan').closest('li')!;
    expect(within(ali).getByText('40%')).toBeTruthy();
    expect(within(agents).getByText('MEI - Tan Mei Ling')).toBeTruthy();
    const kim = within(agents).getByText('KIM - Kim Tan');
    expect(within(agents).getByText('Left 14 Oct')).toBeTruthy();
    expect(kim.closest('[data-left="true"]')).not.toBeNull();
  });

  it('shows "No agents in this team" with Add agents when empty', () => {
    hooks.useSalesTeam.mockReturnValue({
      data: detail({ member_count: 0, members: [] }),
      isLoading: false,
      isError: false,
    });
    render(<SalesTeamDetail id="north" />);
    openTab('Agents');
    const agents = screen.getByRole('region', { name: 'Agents' });
    expect(within(agents).getByText('No agents in this team')).toBeTruthy();
    fireEvent.click(within(agents).getByRole('button', { name: /add agents/i }));
    // Add agents opens the edit session with the picker in the tab, in place.
    expect(screen.getByRole('group', { name: 'Add agents' })).toBeTruthy();
  });

  it('edits in place in the same tabs: the name becomes an input, rows gain a remove control, save sends the new list', async () => {
    render(<SalesTeamDetail id="north" />);
    editFromGear();
    expect(screen.getAllByRole('tab').map((t) => t.textContent?.trim())).toEqual([
      'Details', 'Targets', 'Agents',
    ]);

    const name = screen.getByLabelText('Team name') as HTMLInputElement;
    expect(name.value).toBe('North');
    fireEvent.change(name, { target: { value: 'North East' } });

    openTab('Agents');
    fireEvent.click(screen.getByRole('button', { name: 'Remove MEI - Tan Mei Ling' }));
    // A member who already left has nothing to remove.
    expect(screen.queryByRole('button', { name: 'Remove KIM - Kim Tan' })).toBeNull();

    fireEvent.click(screen.getByLabelText('SEAN I - CONTACT AE (now in Central)'));
    const movesOn = screen.getByLabelText('Moves on') as HTMLInputElement;
    fireEvent.change(movesOn, { target: { value: '2026-10-15' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync).toHaveBeenCalledWith({
      teamId: 'north',
      name: 'North East',
      is_active: true,
      sales_agent_ids: ['ali', 'sean'],
      moves_on: '2026-10-15',
      leader_sales_agent_id: null,
    });
  });

  it('tags the leader\'s row in Agents (W1)', () => {
    hooks.useSalesTeam.mockReturnValue({
      data: detail({ leader_sales_agent_id: 'mei', leader_label: 'MEI - Tan Mei Ling' }),
      isLoading: false,
      isError: false,
    });
    render(<SalesTeamDetail id="north" />);
    openTab('Agents');
    const agents = screen.getByRole('region', { name: 'Agents' });
    const mei = within(agents).getByText('MEI - Tan Mei Ling').closest('li')!;
    expect(within(mei).getByText('Leader')).toBeTruthy();
    const ali = within(agents).getByText('ALI - Ali Hassan').closest('li')!;
    expect(within(ali).queryByText('Leader')).toBeNull();
  });

  it('edits the leader in place, limited to the team\'s agents, and a new pick joins the team', async () => {
    hooks.useSalesTeam.mockReturnValue({
      data: detail({ leader_sales_agent_id: 'mei', leader_label: 'MEI - Tan Mei Ling' }),
      isLoading: false,
      isError: false,
    });
    render(<SalesTeamDetail id="north" />);
    editFromGear();

    const leader = screen.getByLabelText('Leader') as HTMLSelectElement;
    expect(leader.value).toBe('mei');
    expect(leader.dataset.clearable).toBe('true');
    // The current agents only: not Kim, who left, and nobody outside the team.
    expect(Array.from(leader.options).map((o) => o.value)).toEqual(['', 'ali', 'mei']);

    openTab('Agents');
    fireEvent.click(screen.getByLabelText('RAJ - Raj Kumar (no team)'));
    openTab('Details');
    const leaderAgain = screen.getByLabelText('Leader') as HTMLSelectElement;
    expect(Array.from(leaderAgain.options).map((o) => o.value)).toEqual(['', 'ali', 'mei', 'raj']);
    fireEvent.change(leaderAgain, { target: { value: 'raj' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync.mock.calls[0][0]).toMatchObject({
      sales_agent_ids: ['ali', 'mei', 'raj'],
      leader_sales_agent_id: 'raj',
    });
  });

  it('removing the leader in edit clears the leader', async () => {
    hooks.useSalesTeam.mockReturnValue({
      data: detail({ leader_sales_agent_id: 'mei', leader_label: 'MEI - Tan Mei Ling' }),
      isLoading: false,
      isError: false,
    });
    render(<SalesTeamDetail id="north" />);
    editFromGear();
    openTab('Agents');
    fireEvent.click(screen.getByRole('button', { name: 'Remove MEI - Tan Mei Ling' }));
    openTab('Details');
    expect((screen.getByLabelText('Leader') as HTMLSelectElement).value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync.mock.calls[0][0].leader_sales_agent_id).toBeNull();
  });

  it('offers no Edit to a role without sales.teams.edit', () => {
    perms.granted = new Set(['sales.teams.view']);
    render(<SalesTeamDetail id="north" />);
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    openTab('Agents');
    expect(screen.queryByRole('button', { name: /add agents/i })).toBeNull();
  });
});
