'use client';

import { useTeam, useUsersSelect } from '../../hooks/use-team-members';
import { Skeleton } from '@/components/ui/skeleton';
import TeamMembersList from './team-members-list';

export default function TeamMembersClient({ teamId }: { teamId: string }) {
  const { data: team, isLoading: teamLoading, error: teamError } = useTeam(teamId);
  const {
    data: users = [],
    isLoading: usersLoading,
    error: usersError,
    refetch: refetchUsers,
  } = useUsersSelect();

  if (teamLoading || !team) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (teamError) {
    return (
      <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-destructive">
        {teamError.message || 'Failed to load team'}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold">{team.name}</h2>
        {team.description && (
          <p className="text-muted-foreground text-sm mt-1">{team.description}</p>
        )}
      </div>
      <TeamMembersList
        teamId={teamId}
        users={users}
        usersError={usersError}
        onRetryUsers={() => void refetchUsers()}
      />
    </div>
  );
}
