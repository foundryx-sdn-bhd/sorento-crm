'use client';

import { useMemo, useState, useCallback } from 'react';
import Link from 'next/link';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  ChevronRight,
  Ellipsis,
  GripVertical,
  Users,
  CornerDownRight,
} from 'lucide-react';
import { RiCheckboxCircleFill, RiErrorWarningFill } from '@remixicon/react';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';
import type { Team } from '../types/team.types';
import { updateTeam } from '../services/teamService';
import TeamMemberPopover from './team-member-popover';

const INDENT = 24; // px per depth level

interface TreeNode {
  team: Team;
  depth: number;
  children: TreeNode[];
}

function toastAlert(message: string, ok: boolean) {
  toast.custom(
    () => (
      <Alert variant="mono" icon={ok ? 'success' : 'destructive'}>
        <AlertIcon>{ok ? <RiCheckboxCircleFill /> : <RiErrorWarningFill />}</AlertIcon>
        <AlertTitle>{message}</AlertTitle>
      </Alert>
    ),
    { position: 'top-center', duration: ok ? 4000 : 6000 },
  );
}

export default function TeamTree({
  teams,
  query,
  canEdit,
  canDelete,
  onEdit,
  onDelete,
}: {
  teams: Team[];
  query: string;
  /** user_management.teams.edit: Edit team, and drag a row to re-parent it. */
  canEdit: boolean;
  /** user_management.teams.delete: Delete team. */
  canDelete: boolean;
  onEdit: (team: Team) => void;
  onDelete: (team: Team) => void;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<Set<string> | null>(null); // null = all expanded
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null); // '' = root zone

  // ---- child adjacency + descendant lookup (cycle guard) ----
  const childrenMap = useMemo(() => {
    const ids = new Set(teams.map((t) => t.id));
    const map = new Map<string | null, Team[]>();
    for (const t of teams) {
      // treat dangling parent (not in set) as a root
      const parent = t.parent_team_id && ids.has(t.parent_team_id) ? t.parent_team_id : null;
      const arr = map.get(parent) ?? [];
      arr.push(t);
      map.set(parent, arr);
    }
    for (const arr of map.values()) arr.sort((a, b) => a.name.localeCompare(b.name));
    return map;
  }, [teams]);

  const descendantsOf = useCallback(
    (id: string): Set<string> => {
      const out = new Set<string>();
      const stack = [...(childrenMap.get(id) ?? [])];
      while (stack.length) {
        const t = stack.pop()!;
        if (out.has(t.id)) continue;
        out.add(t.id);
        stack.push(...(childrenMap.get(t.id) ?? []));
      }
      return out;
    },
    [childrenMap],
  );

  // ---- build flat visible rows respecting expand + search ----
  const { matchSet, forceExpand } = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return { matchSet: null as Set<string> | null, forceExpand: false };
    const byId = new Map(teams.map((t) => [t.id, t]));
    const keep = new Set<string>();
    for (const t of teams) {
      if (t.name.toLowerCase().includes(q)) {
        // keep the node and all ancestors so the path stays visible
        let cur: Team | undefined = t;
        while (cur) {
          keep.add(cur.id);
          cur = cur.parent_team_id ? byId.get(cur.parent_team_id) : undefined;
        }
      }
    }
    return { matchSet: keep, forceExpand: true };
  }, [teams, query]);

  const rows = useMemo(() => {
    const out: TreeNode[] = [];
    const walk = (parent: string | null, depth: number) => {
      for (const team of childrenMap.get(parent) ?? []) {
        if (matchSet && !matchSet.has(team.id)) continue;
        out.push({ team, depth, children: [] });
        const kids = childrenMap.get(team.id) ?? [];
        const isExpanded = forceExpand || expanded === null || expanded.has(team.id);
        if (kids.length && isExpanded) walk(team.id, depth + 1);
      }
    };
    walk(null, 0);
    return out;
  }, [childrenMap, expanded, matchSet, forceExpand]);

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const base = prev ?? new Set(teams.map((t) => t.id));
      const next = new Set(base);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const reparent = useMutation({
    mutationFn: ({ id, parent }: { id: string; parent: string | null }) =>
      updateTeam(id, { parent_team_id: parent }),
    onSuccess: (_d, vars) => {
      queryClient.invalidateQueries({ queryKey: ['user-management-teams'] });
      toastAlert(vars.parent ? 'Team moved under new parent' : 'Team moved to top level', true);
    },
    onError: (e: Error) => toastAlert(e.message, false),
  });

  // a drop is valid if target isn't the dragged node, its current parent, or a descendant.
  // Without teams.edit nothing drops: the Members link is natively draggable and its
  // dragstart bubbles to the row even though the row itself is not draggable.
  const canDrop = useCallback(
    (targetId: string | null): boolean => {
      if (!canEdit || !draggingId) return false;
      const dragged = teams.find((t) => t.id === draggingId);
      if (!dragged) return false;
      const currentParent = dragged.parent_team_id ?? null;
      if (targetId === currentParent) return false; // no-op
      if (targetId === null) return true; // detach to root
      if (targetId === draggingId) return false;
      if (descendantsOf(draggingId).has(targetId)) return false; // would create a cycle
      return true;
    },
    [canEdit, draggingId, teams, descendantsOf],
  );

  const handleDrop = (targetId: string | null) => {
    if (canDrop(targetId)) reparent.mutate({ id: draggingId!, parent: targetId });
    setDraggingId(null);
    setDropTargetId(null);
  };

  if (rows.length === 0) {
    return (
      <div className="text-muted-foreground px-4 py-12 text-center text-sm">
        {query ? 'No teams match your search.' : 'No teams yet. Create a team to get started.'}
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {/* Root drop zone - drag here to detach a team to the top level */}
      {canEdit && (
        <div
          onDragOver={(e) => {
            if (canDrop(null)) {
              e.preventDefault();
              setDropTargetId('');
            }
          }}
          onDragLeave={() => setDropTargetId((p) => (p === '' ? null : p))}
          onDrop={() => handleDrop(null)}
          className={cn(
            'flex items-center gap-2 border-b border-dashed px-4 py-1.5 text-xs transition-colors',
            draggingId ? 'text-muted-foreground' : 'text-transparent',
            dropTargetId === '' && 'bg-primary/10 text-primary',
          )}
        >
          <CornerDownRight className="size-3.5" />
          Drop here to move to top level
        </div>
      )}

      {rows.map(({ team, depth }) => {
        const kids = childrenMap.get(team.id) ?? [];
        const hasKids = kids.length > 0;
        const isExpanded = forceExpand || expanded === null || expanded.has(team.id);
        const isDragging = draggingId === team.id;
        const isDropTarget = dropTargetId === team.id;
        const count = team.member_count ?? team.members?.length ?? 0;

        return (
          <div
            key={team.id}
            draggable={canEdit || undefined}
            onDragStart={(e) => {
              if (!canEdit) return;
              setDraggingId(team.id);
              e.dataTransfer.effectAllowed = 'move';
            }}
            onDragEnd={() => {
              setDraggingId(null);
              setDropTargetId(null);
            }}
            onDragOver={(e) => {
              if (canDrop(team.id)) {
                e.preventDefault();
                setDropTargetId(team.id);
              }
            }}
            onDragLeave={() => setDropTargetId((p) => (p === team.id ? null : p))}
            onDrop={() => handleDrop(team.id)}
            className={cn(
              'group flex items-center gap-1 border-b px-2 py-2 transition-colors',
              'hover:bg-muted/40',
              isDragging && 'opacity-40',
              isDropTarget && 'bg-primary/10 ring-1 ring-inset ring-primary/40',
            )}
          >
            {canEdit && (
              <GripVertical className="text-muted-foreground/40 size-4 shrink-0 cursor-grab group-hover:text-muted-foreground" />
            )}

            <div style={{ width: depth * INDENT }} className="shrink-0" aria-hidden />

            {hasKids ? (
              <button
                type="button"
                onClick={() => toggle(team.id)}
                className="hover:bg-muted flex size-5 shrink-0 items-center justify-center rounded"
                aria-label={isExpanded ? 'Collapse' : 'Expand'}
              >
                <ChevronRight
                  className={cn('size-4 transition-transform', isExpanded && 'rotate-90')}
                />
              </button>
            ) : (
              <span className="size-5 shrink-0" aria-hidden />
            )}

            <span className="min-w-0 flex-1 truncate font-medium" title={team.name}>
              {team.name}
            </span>

            <div className="flex shrink-0 items-center gap-2">
              <TeamMemberPopover
                teamId={team.id}
                teamName={team.name}
                members={team.members ?? []}
                count={count}
              />
              <Button variant="outline" size="sm" asChild className="h-8">
                <Link href={`/user-management/teams/${team.id}`}>
                  <Users className="me-1 size-4" />
                  Members
                </Link>
              </Button>
              {(canEdit || canDelete) && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button className="size-8" mode="icon" variant="ghost" size="icon" aria-label="More actions">
                      <Ellipsis />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {canEdit && (
                      <DropdownMenuItem onClick={() => onEdit(team)}>Edit team</DropdownMenuItem>
                    )}
                    {canEdit && canDelete && <DropdownMenuSeparator />}
                    {canDelete && (
                      <DropdownMenuItem variant="destructive" onClick={() => onDelete(team)}>
                        Delete team
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
