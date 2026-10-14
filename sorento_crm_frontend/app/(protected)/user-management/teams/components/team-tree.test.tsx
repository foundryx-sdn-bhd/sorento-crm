import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import TeamTree from './team-tree';
import TeamMemberPopover from './team-member-popover';
import type { Team } from '../types/team.types';

const updateTeam = vi.fn();
vi.mock('../services/teamService', () => ({ updateTeam: (...a: unknown[]) => updateTeam(...a) }));
vi.mock('@/lib/toast', () => ({ toast: { custom: vi.fn() } }));

function render(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const teams: Team[] = [
  { id: 'gp', name: 'Grandparent', parent_team_id: null, created_at: '', member_count: 2,
    members: [{ user_id: 'u1', name: 'Magen' }, { user_id: 'u2', name: 'Ziv' }] },
  { id: 'p', name: 'Parent', parent_team_id: 'gp', created_at: '', member_count: 0, members: [] },
  { id: 'c', name: 'Child', parent_team_id: 'p', created_at: '', member_count: 1,
    members: [{ user_id: 'u3', name: 'Agnes' }] },
  { id: 'root2', name: 'Solo', parent_team_id: null, created_at: '', member_count: 0, members: [] },
];

beforeEach(() => updateTeam.mockReset());

describe('TeamTree hierarchy', () => {
  it('renders nested descendants when expanded (default)', () => {
    render(<TeamTree teams={teams} query="" canEdit canDelete onEdit={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText('Grandparent')).toBeInTheDocument();
    expect(screen.getByText('Parent')).toBeInTheDocument();
    expect(screen.getByText('Child')).toBeInTheDocument();
    expect(screen.getByText('Solo')).toBeInTheDocument();
  });

  it('collapsing a parent hides its descendants', () => {
    render(<TeamTree teams={teams} query="" canEdit canDelete onEdit={vi.fn()} onDelete={vi.fn()} />);
    // Grandparent row carries a Collapse control (it has children).
    fireEvent.click(screen.getAllByRole('button', { name: 'Collapse' })[0]);
    expect(screen.queryByText('Parent')).not.toBeInTheDocument();
    expect(screen.queryByText('Child')).not.toBeInTheDocument();
    expect(screen.getByText('Grandparent')).toBeInTheDocument();
  });

  it('search keeps the matched node and its ancestor path', () => {
    render(<TeamTree teams={teams} query="child" canEdit canDelete onEdit={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText('Child')).toBeInTheDocument();
    expect(screen.getByText('Parent')).toBeInTheDocument(); // ancestor kept
    expect(screen.getByText('Grandparent')).toBeInTheDocument(); // ancestor kept
    expect(screen.queryByText('Solo')).not.toBeInTheDocument(); // unrelated dropped
  });

  it('member count badge reflects member_count', () => {
    render(<TeamTree teams={teams} query="" canEdit canDelete onEdit={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByRole('button', { name: /2 members in Grandparent/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /0 members in Parent/ })).toBeInTheDocument();
  });
});

describe('TeamTree write gates (TEAMS-PERM)', () => {
  const solo = [teams[3]];

  it('a view-only caller gets no row actions and cannot drag a row', () => {
    render(<TeamTree teams={solo} query="" canEdit={false} canDelete={false} onEdit={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
    expect(screen.getByText('Solo').closest('[draggable]')).toBeNull();
  });

  it('teams.edit alone offers Edit team but not Delete team', async () => {
    render(<TeamTree teams={solo} query="" canEdit canDelete={false} onEdit={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.pointerDown(screen.getByRole('button', { name: 'More actions' }), { button: 0, ctrlKey: false });
    expect(await screen.findByRole('menuitem', { name: 'Edit team' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Delete team' })).not.toBeInTheDocument();
    expect(screen.getByText('Solo').closest('[draggable="true"]')).not.toBeNull();
  });

  it('teams.delete alone offers Delete team but not Edit team, and no drag', async () => {
    render(<TeamTree teams={solo} query="" canEdit={false} canDelete onEdit={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.pointerDown(screen.getByRole('button', { name: 'More actions' }), { button: 0, ctrlKey: false });
    expect(await screen.findByRole('menuitem', { name: 'Delete team' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Edit team' })).not.toBeInTheDocument();
    expect(screen.getByText('Solo').closest('[draggable]')).toBeNull();
  });
});

describe('TeamMemberPopover', () => {
  it('lists member names on open', () => {
    render(
      <TeamMemberPopover teamId="gp" teamName="Grandparent" count={2}
        members={[{ user_id: 'u1', name: 'Magen' }, { user_id: 'u2', name: 'Ziv' }]} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /2 members in Grandparent/ }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Magen')).toBeInTheDocument();
    expect(within(dialog).getByText('Ziv')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: /Manage members/ })).toHaveAttribute(
      'href',
      '/user-management/teams/gp',
    );
  });

  it('shows empty state when no members', () => {
    render(<TeamMemberPopover teamId="p" teamName="Parent" count={0} members={[]} />);
    fireEvent.click(screen.getByRole('button', { name: /0 members in Parent/ }));
    expect(screen.getByText('No members yet.')).toBeInTheDocument();
  });
});
