/**
 * The team modal (UAC S6-12, S6-15; owner rulings 26 Sep 06:01 (Lavish) N2, N8 and
 * 26 Sep 06:09 T2).
 *
 * The Add team modal (Edit is in place on the team page, S6-13): Name, Agents (our standard multi-select of active agents, each
 * option labelled with the team they are in now), Active. **Moves on** (a date, default today,
 * not clearable) appears only when a picked agent is in another team.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { todayMalaysiaYyyyMmDd } from '@/lib/helpers';

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    value: string[];
    onChange: (v: string[]) => void;
    options?: { value: string; label: string }[];
  }) => (
    <fieldset aria-label="Agents">
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

const save = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
vi.mock('../hooks/useSalesTeams', () => ({
  useSalesTeamAgentOptions: () => ({
    data: [
      { id: 'ali', code: 'ALI', label: 'ALI - Ali Hassan', team_id: 'north', team_name: 'North' },
      { id: 'sean', code: 'SEAN I', label: 'SEAN I - CONTACT AE', team_id: 'central', team_name: 'Central' },
      { id: 'raj', code: 'RAJ', label: 'RAJ - Raj Kumar', team_id: null, team_name: null },
    ],
    isLoading: false,
  }),
  useSaveSalesTeam: () => save,
}));

import SalesTeamModal from './SalesTeamModal';

beforeEach(() => {
  save.mutateAsync.mockReset();
  save.mutateAsync.mockResolvedValue({ id: 'new' });
});

describe('SalesTeamModal', () => {
  it('labels each agent option with the team they are in now', () => {
    render(<SalesTeamModal open onOpenChange={() => {}} />);
    expect(screen.getByText('SEAN I - CONTACT AE (now in Central)')).toBeTruthy();
    expect(screen.getByText('RAJ - Raj Kumar (no team)')).toBeTruthy();
  });

  it('shows Moves on, defaulting to today, only once a picked agent is in another team', () => {
    render(<SalesTeamModal open onOpenChange={() => {}} />);
    fireEvent.click(screen.getByLabelText('RAJ - Raj Kumar (no team)'));
    expect(screen.queryByLabelText('Moves on')).toBeNull();

    fireEvent.click(screen.getByLabelText('SEAN I - CONTACT AE (now in Central)'));
    const movesOn = screen.getByLabelText('Moves on') as HTMLInputElement;
    expect(movesOn.value).toBe(todayMalaysiaYyyyMmDd());
    expect(movesOn.max).toBe(todayMalaysiaYyyyMmDd());
    expect(movesOn.required).toBe(true);
  });

  it('creates the team with its agents and the move date', async () => {
    const onOpenChange = vi.fn();
    render(<SalesTeamModal open onOpenChange={onOpenChange} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: '  North  ' } });
    fireEvent.click(screen.getByLabelText('SEAN I - CONTACT AE (now in Central)'));
    fireEvent.change(screen.getByLabelText('Moves on'), { target: { value: '2026-09-20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync).toHaveBeenCalledWith({
      teamId: null,
      name: 'North',
      is_active: true,
      sales_agent_ids: ['sean'],
      moves_on: '2026-09-20',
      leader_sales_agent_id: null,
    });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('sends no move date when nobody is moving', async () => {
    render(<SalesTeamModal open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'West' } });
    fireEvent.click(screen.getByLabelText('RAJ - Raj Kumar (no team)'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync.mock.calls[0][0].moves_on).toBeUndefined();
  });

  it('offers only the picked agents as Leader, clearable, and saves the leader (W1)', async () => {
    render(<SalesTeamModal open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'West' } });
    const leader = screen.getByLabelText('Leader') as HTMLSelectElement;
    expect(leader.dataset.clearable).toBe('true');
    expect(Array.from(leader.options).map((o) => o.value)).toEqual(['']);

    fireEvent.click(screen.getByLabelText('RAJ - Raj Kumar (no team)'));
    fireEvent.click(screen.getByLabelText('SEAN I - CONTACT AE (now in Central)'));
    expect(Array.from(leader.options).map((o) => o.textContent)).toEqual([
      'No leader',
      'RAJ - Raj Kumar',
      'SEAN I - CONTACT AE',
    ]);
    fireEvent.change(leader, { target: { value: 'raj' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync.mock.calls[0][0]).toMatchObject({
      sales_agent_ids: ['raj', 'sean'],
      leader_sales_agent_id: 'raj',
    });
  });

  it('clears the leader when that agent is unpicked', async () => {
    render(<SalesTeamModal open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'West' } });
    fireEvent.click(screen.getByLabelText('RAJ - Raj Kumar (no team)'));
    fireEvent.change(screen.getByLabelText('Leader'), { target: { value: 'raj' } });
    fireEvent.click(screen.getByLabelText('RAJ - Raj Kumar (no team)'));
    expect((screen.getByLabelText('Leader') as HTMLSelectElement).value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync.mock.calls[0][0].leader_sales_agent_id).toBeNull();
  });

  it('will not save a blank name', () => {
    render(<SalesTeamModal open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: '   ' } });
    const saveButton = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(true);
  });
});
