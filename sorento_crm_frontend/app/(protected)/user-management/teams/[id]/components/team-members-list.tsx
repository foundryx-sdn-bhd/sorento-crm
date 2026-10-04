'use client';

import { useCallback, useMemo, useState } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { LoaderCircleIcon, Trash2, UserPlus, X } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import type { TeamMember } from '../../types/team.types';
import type { UserSelectItem } from '../../services/teamService';
import {
  useTeamMembers,
  useRemoveTeamMember,
  useUpdateTeamMemberRoundRobin,
} from '../../hooks/use-team-members';
import { addTeamMember } from '../../services/teamService';

function displayUser(user: UserSelectItem | undefined, userId: string): string {
  if (!user) return userId;
  if (user.name) return user.name;
  return user.email || userId;
}

export default function TeamMembersList({
  teamId,
  users,
  usersError,
  onRetryUsers,
}: {
  teamId: string;
  users: UserSelectItem[];
  usersError?: unknown;
  onRetryUsers?: () => void;
}) {
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [selectedUserIds, setSelectedUserIds] = useState<Set<string>>(new Set());
  const [isAdding, setIsAdding] = useState(false);

  const { data: members = [], isLoading } = useTeamMembers(teamId);
  const removeMutation = useRemoveTeamMember(teamId);
  const roundRobinMutation = useUpdateTeamMemberRoundRobin(teamId);

  const userMap = useMemo(() => {
    const m = new Map<string, UserSelectItem>();
    users.forEach((u) => m.set(u.id, u));
    return m;
  }, [users]);

  const memberUserIds = useMemo(() => new Set(members.map((m) => m.user_id)), [members]);
  const availableUsers = useMemo(
    () => users.filter((u) => !memberUserIds.has(u.id)),
    [users, memberUserIds],
  );

  const toggleUser = useCallback((userId: string) => {
    setSelectedUserIds((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  }, []);

  const handleAdd = useCallback(async () => {
    if (selectedUserIds.size === 0) return;
    setIsAdding(true);
    const ids = Array.from(selectedUserIds);
    try {
      for (const userId of ids) {
        await addTeamMember(teamId, { user_id: userId });
      }
      queryClient.invalidateQueries({ queryKey: ['user-management-team-members', teamId] });
      toast.success(
        ids.length === 1 ? 'Member added' : `${ids.length} members added`,
      );
      setSelectedUserIds(new Set());
      setAddOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to add members');
    } finally {
      setIsAdding(false);
    }
  }, [teamId, selectedUserIds, queryClient]);

  const selectedCount = selectedUserIds.size;

  const columns = useMemo<ColumnDef<TeamMember>[]>(
    () => [
      {
        id: 'order',
        accessorFn: (row) => row.sort_order ?? 0,
        header: ({ column }) => <DataGridColumnHeader title="Order" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {row.original.sort_order ?? members.indexOf(row.original) + 1}
          </span>
        ),
        size: 90,
        meta: { headerTitle: 'Order' },
      },
      {
        id: 'user',
        accessorFn: (row) => displayUser(userMap.get(row.user_id), row.user_id),
        header: ({ column }) => <DataGridColumnHeader title="User" column={column} />,
        cell: ({ row }) => {
          const name = displayUser(userMap.get(row.original.user_id), row.original.user_id);
          return (
            <span className="block truncate" title={name}>
              {name}
            </span>
          );
        },
        size: 220,
        meta: { headerTitle: 'User' },
      },
      {
        id: 'round_robin',
        accessorFn: (row) => row.include_in_round_robin ?? true,
        header: ({ column }) => (
          <DataGridColumnHeader title="Auto-assign (round robin)" column={column} />
        ),
        cell: ({ row }) => (
          <Switch
            size="sm"
            checked={row.original.include_in_round_robin ?? true}
            onCheckedChange={(checked) =>
              roundRobinMutation.mutate({
                userId: row.original.user_id,
                includeInRoundRobin: checked,
              })
            }
            aria-label="Include in round robin"
          />
        ),
        size: 220,
        meta: { headerTitle: 'Auto-assign (round robin)' },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="icon"
            className="text-destructive hover:text-destructive"
            onClick={(e) => {
              e.stopPropagation();
              removeMutation.mutate(row.original.user_id);
            }}
            disabled={removeMutation.isPending}
            aria-label="Remove member"
          >
            <Trash2 className="size-4" />
          </Button>
        ),
        size: 80,
        enableResizing: false,
        meta: { headerTitle: 'Actions' },
      },
    ],
    [members, userMap, roundRobinMutation, removeMutation],
  );

  return (
    <>
      <PanelDataGrid<TeamMember>
        title="Members"
        toolbar={
          <Button onClick={() => setAddOpen(true)} disabled={availableUsers.length === 0}>
            <UserPlus className="me-2 size-4" />
            Add member
          </Button>
        }
        columns={columns}
        rows={members}
        getRowId={(row) => row.id}
        listingKey="user_management.teams.view::members"
        isLoading={isLoading}
        emptyTitle="No members."
        emptyBody="Add users to this team for round-robin assignment."
      />

      <Dialog
        open={addOpen}
        onOpenChange={(open) => {
          setAddOpen(open);
          if (!open) setSelectedUserIds(new Set());
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add members</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <SearchableMultiSelect
                value={Array.from(selectedUserIds)}
                onChange={(next) => setSelectedUserIds(new Set(next))}
                placeholder="Select users"
                emptyMessage="No user found."
                loadError={usersError}
                onRetry={onRetryUsers}
                // Chips below already list the picks, so the trigger keeps its terse summary.
                renderTriggerLabel={(sel) =>
                  sel.length === 0
                    ? 'Select users'
                    : sel.length === 1
                      ? displayUser(
                          users.find((u) => u.id === sel[0].value),
                          sel[0].value,
                        )
                      : `${sel.length} users selected`
                }
                options={availableUsers.map((u) => ({
                  value: u.id,
                  label: displayUser(u, u.id),
                  searchText: `${u.name ?? ''} ${u.email}`.trim(),
                }))}
              />
            </div>
            {selectedCount > 0 && (
              <div className="flex flex-wrap gap-2 rounded-md border px-3 py-2">
                {Array.from(selectedUserIds).map((userId) => {
                  const u = users.find((x) => x.id === userId);
                  return (
                    <Badge
                      key={userId}
                      variant="secondary"
                      className="gap-1 pr-1 font-normal"
                    >
                      {displayUser(u, userId)}
                      <button
                        type="button"
                        onClick={() => toggleUser(userId)}
                        className="ml-0.5 rounded-full hover:bg-muted p-0.5"
                        aria-label={'Remove ' + displayUser(u, userId)}
                      >
                        <X className="size-3" />
                      </button>
                    </Badge>
                  );
                })}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setAddOpen(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleAdd}
                disabled={selectedCount === 0 || isAdding}
              >
                {isAdding && <LoaderCircleIcon className="animate-spin me-2 size-4" />}
                Add {selectedCount > 0 ? selectedCount : ''} member{selectedCount !== 1 ? 's' : ''}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
