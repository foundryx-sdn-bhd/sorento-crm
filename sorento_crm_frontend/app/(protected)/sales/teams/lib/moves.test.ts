/**
 * Who a save would move out of another team (UAC S6-15, owner ruling 26 Sep 06:09, T2).
 * Moves on shows only when this list is not empty.
 */
import { describe, expect, it } from 'vitest';
import { agentsMovingIn, agentOptionLabel } from './moves';
import type { SalesTeamAgentOption } from '../types/salesTeam.types';

const options: SalesTeamAgentOption[] = [
  { id: 'ali', code: 'ALI', label: 'ALI - Ali Hassan', team_id: 'north', team_name: 'North' },
  { id: 'sean', code: 'SEAN I', label: 'SEAN I - CONTACT AE', team_id: 'central', team_name: 'Central' },
  { id: 'raj', code: 'RAJ', label: 'RAJ - Raj Kumar', team_id: null, team_name: null },
];

describe('agentsMovingIn', () => {
  it('is empty when every picked agent is new to teams or already here', () => {
    expect(agentsMovingIn(['ali', 'raj'], options, 'north')).toEqual([]);
  });

  it('names a picked agent who is in another team', () => {
    expect(agentsMovingIn(['ali', 'sean'], options, 'north').map((o) => o.id)).toEqual(['sean']);
  });

  it('treats every placed agent as moving when the team does not exist yet', () => {
    expect(agentsMovingIn(['ali', 'raj'], options, null).map((o) => o.id)).toEqual(['ali']);
  });
});

describe('agentOptionLabel', () => {
  it('says which team the agent is in now, or that they have none', () => {
    expect(agentOptionLabel(options[1], 'north')).toBe('SEAN I - CONTACT AE (now in Central)');
    expect(agentOptionLabel(options[2], 'north')).toBe('RAJ - Raj Kumar (no team)');
    expect(agentOptionLabel(options[0], 'north')).toBe('ALI - Ali Hassan');
  });
});
