import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import TeamList from './team-list';
import type { Team } from '../types/team.types';

const perms = { granted: new Set<string>() };
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => perms.granted.has(slug),
}));

const teams: Team[] = [
  { id: 'gp', name: 'Grandparent', parent_team_id: null, created_at: '', member_count: 0, members: [] },
];
vi.mock('../services/teamService', () => ({
  getTeams: () => Promise.resolve(teams),
  updateTeam: vi.fn(),
}));
vi.mock('@/hooks/useDeferredRowAction', () => ({ useDeferredRowAction: () => ({ run: vi.fn() }) }));
vi.mock('./team-edit-dialog', () => ({ default: () => null }));
vi.mock('@/lib/toast', () => ({ toast: { custom: vi.fn() } }));

function render(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  perms.granted = new Set(['user_management.teams.view']);
});

describe('TeamList write gates (TEAMS-PERM)', () => {
  it('a view-only caller sees the teams but no Create team and no row actions', async () => {
    render(<TeamList />);
    expect(await screen.findByText('Grandparent')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Create team/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Drag a row by its handle/)).not.toBeInTheDocument();
  });

  it('teams.add shows Create team', async () => {
    perms.granted.add('user_management.teams.add');
    render(<TeamList />);
    expect(await screen.findByText('Grandparent')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Create team/ })).toBeInTheDocument();
  });

  it('teams.edit and teams.delete open the row actions menu', async () => {
    perms.granted.add('user_management.teams.edit');
    perms.granted.add('user_management.teams.delete');
    render(<TeamList />);
    expect(await screen.findByText('Grandparent')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More actions' })).toBeInTheDocument();
    expect(screen.getByText(/Drag a row by its handle/)).toBeInTheDocument();
  });
});
