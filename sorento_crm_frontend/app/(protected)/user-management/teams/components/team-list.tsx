'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import type { Team } from '../types/team.types';
import { getTeams } from '../services/teamService';
import TeamEditDialog from './team-edit-dialog';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import TeamTree from './team-tree';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useHasPermission } from '@/hooks/usePermissions';

export default function TeamList() {
  const canAdd = useHasPermission('user_management.teams.add');
  const canEdit = useHasPermission('user_management.teams.edit');
  const canDelete = useHasPermission('user_management.teams.delete');
  const [editOpen, setEditOpen] = useState(false);
  const [selectedTeam, setSelectedTeam] = useState<Team | null>(null);
  const {
    value: queryInput,
    setValue: setQueryInput,
    debouncedValue: query,
  } = useDebouncedSearch();
  // Delete asks nothing (D7): a toast counts down with Cancel.
  const deletion = useDeferredRowAction({
    actionKey: 'team.delete',
    entityType: 'team',
    successMessage: 'Team deleted',
    invalidateKeys: [['user-management-teams']],
  });

  const { data: teams = [], isLoading } = useQuery({
    queryKey: ['user-management-teams'],
    queryFn: getTeams,
    staleTime: 60 * 1000,
  });

  return (
    <>
      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <ListSearchInput
            value={queryInput}
            onChange={setQueryInput}
            placeholder="Search teams…"
            className="w-full sm:max-w-xs"
          />
          {canAdd && (
            <Button
              onClick={() => {
                setSelectedTeam(null);
                setEditOpen(true);
              }}
            >
              <Plus className="me-2 size-4" />
              Create team
            </Button>
          )}
        </CardHeader>

        <div className="border-t">
          {canEdit && (
            <p className="text-muted-foreground border-b px-4 py-2 text-xs">
              Drag a row by its handle onto another team to nest it; drop on the top zone to detach.
            </p>
          )}
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : (
            <TeamTree
              teams={teams}
              query={query}
              canEdit={canEdit}
              canDelete={canDelete}
              onEdit={(t) => {
                setSelectedTeam(t);
                setEditOpen(true);
              }}
              onDelete={(t) => deletion.run({ id: t.id, subject: t.name })}
            />
          )}
        </div>

        {!isLoading && teams.length > 0 && (
          <CardFooter className="text-muted-foreground text-sm">
            {teams.length} team{teams.length !== 1 ? 's' : ''}
          </CardFooter>
        )}
      </Card>

      <TeamEditDialog open={editOpen} closeDialog={() => setEditOpen(false)} team={selectedTeam} />
    </>
  );
}
