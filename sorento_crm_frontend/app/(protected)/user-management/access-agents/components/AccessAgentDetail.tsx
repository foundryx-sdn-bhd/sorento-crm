'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Check, Edit, ChevronDown, ChevronRight, RefreshCw, ContactRound, Copy } from 'lucide-react';
import AccessAgentFormModal from './AccessAgentFormModal';
import { useCompany } from '@/app/providers/CompanyProvider';
import { Button } from '@/components/ui/button';
import { useBackToListHref } from '@/components/common/BackToList';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { toast } from '@/lib/toast';
import {
  useAccessAgent,
  useAgentTeams,
  useTeams,
  accessAgentsPagerQuery,
} from '../hooks/useAccessAgents';
import { formatDate } from '@/lib/helpers';
import ContactAccessAgentsTable from './ContactAccessAgentsTable';
import DetailActions from '@/components/common/DetailActions';
import { useAccessAgentActions } from '../actions';
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard';
import MemberMarketSegmentEditor from './MemberMarketSegmentEditor';
import MemberBrandEditor from './MemberBrandEditor';
import type { AgentTeamMemberInfo, AgentTeamAssignment } from '../services/accessAgentService';

interface AccessAgentDetailProps {
  accessAgentId: string;
}

function respondSyncLabel(sync: string | null | undefined): string {
  const s = (sync || 'pending').toLowerCase();
  if (s === 'successful') return 'Successful';
  if (s === 'failed') return 'Failed';
  if (s === 'pending') return 'Pending';
  return sync || 'Unknown';
}

function TeamMemberRespondIoButton({ member }: { member: AgentTeamMemberInfo }) {
  const rid = member.respond_user_id?.trim() || '';
  const syncRaw = member.respond_synced;
  const sync = syncRaw != null && String(syncRaw).trim() ? String(syncRaw).trim().toLowerCase() : null;

  const dotClass =
    sync === 'successful'
      ? 'bg-emerald-500'
      : sync === 'failed'
        ? 'bg-destructive'
        : sync === 'pending'
          ? 'bg-amber-500'
          : 'bg-muted-foreground';

  // The tick on the button is the confirmation; only a refusal needs saying (S7-05).
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const copyId = async () => {
    if (!rid) {
      toast.error('No Respond user ID on file for this user.');
      return;
    }
    if (!(await copyToClipboard(rid))) toast.error('Could not copy to clipboard');
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="relative size-8 shrink-0 text-muted-foreground hover:text-foreground"
          aria-label="Respond.io user ID and sync status"
        >
          <ContactRound className="size-4" />
          <span
            className={`absolute end-0.5 top-0.5 size-2 rounded-full border-2 border-background ${dotClass}`}
            aria-hidden
          />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80" align="end">
        <div className="space-y-3">
          <div>
            <p className="text-xs font-medium text-muted-foreground">Respond user ID</p>
            <p className="mt-1 break-all font-mono text-sm">{rid || '-'}</p>
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">Respond.io sync</p>
            <p className="mt-1 flex items-center gap-2 text-sm">
              <span
                className={`inline-block size-2 rounded-full shrink-0 ${dotClass}`}
                aria-hidden
              />
              {sync == null ? 'Unknown' : respondSyncLabel(sync)}
            </p>
          </div>
          <Button type="button" variant="outline" size="sm" className="w-full gap-2" onClick={copyId} disabled={!rid}>
            {isCopied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
            {isCopied ? 'Copied' : 'Copy Respond user ID'}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export default function AccessAgentDetail({ accessAgentId }: AccessAgentDetailProps) {
  const router = useRouter();
  const backHref = useBackToListHref('/user-management/access-agents');
  const { data: accessAgent, isLoading } = useAccessAgent(accessAgentId);
  const {
    data: agentTeamsData,
    refetch: refetchAgentTeams,
    isRefetching: isRefetchingAgentTeams,
  } = useAgentTeams(accessAgentId);
  const { data: teamsList = [] } = useTeams();
  const { activeCompany } = useCompany();
  const { actions, pending } = useAccessAgentActions(accessAgent, {
    onDeleted: () => router.push(backHref),
  });
  const [editModalOpen, setEditModalOpen] = useState(false);

  const assignments = agentTeamsData?.assignments ?? [];
  const [expandedTeams, setExpandedTeams] = useState<Set<string>>(new Set());
  const teamNameMap = useMemo(() => {
    const m = new Map<string, string>();
    teamsList.forEach((t: { id: string; name: string }) => m.set(t.id, t.name));
    return m;
  }, [teamsList]);
  const groupedAssignments = useMemo(() => {
    const groups = new Map<string, AgentTeamAssignment[]>();
    for (const assignment of assignments) {
      const code = String(assignment.code ?? '').trim();
      const key = code || '(no code)';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)?.push(assignment);
    }
    return Array.from(groups.entries()).map(([code, rows]) => ({
      code,
      rows: [...rows].sort((a, b) => {
        const ta = typeof a.tier === 'number' ? a.tier : 999;
        const tb = typeof b.tier === 'number' ? b.tier : 999;
        return ta - tb;
      }),
    }));
  }, [assignments]);

  const toggleTeam = (assignmentKey: string) => {
    setExpandedTeams((prev) => {
      const next = new Set(prev);
      if (next.has(assignmentKey)) {
        next.delete(assignmentKey);
      } else {
        next.add(assignmentKey);
      }
      return next;
    });
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!accessAgent) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Access agent not found</p>
        <Button variant="outline" onClick={() => router.push('/user-management/access-agents')} className="mt-4">
          Back to Access Agents
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-bold">{accessAgent.name}</h2>
            <Badge variant={accessAgent.is_active ? 'success' : 'secondary'}>
              <BadgeDot />
              {accessAgent.is_active ? 'Active' : 'Inactive'}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            Code: {accessAgent.code}
          </p>
        </div>
        <DetailActions
          pager={{
            ...accessAgentsPagerQuery,
            detailPath: '/user-management/access-agents',
            currentId: accessAgentId,
            ariaLabel: 'access agent',
          }}
          actions={actions}
          pendingAction={pending}
          gearLabel="Access agent options"
          primary={
            <Button onClick={() => setEditModalOpen(true)}>
              <Edit className="size-4" />
              Edit
            </Button>
          }
        />
      </div>

      {/* Access Agent Information */}
      <Card>
        <CardHeader>
          <CardTitle>Access Agent Information</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <p className="text-sm text-muted-foreground">Code</p>
              <p className="font-medium">{accessAgent.code}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Name</p>
              <p className="font-medium">{accessAgent.name}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Status</p>
              <Badge variant={accessAgent.is_active ? 'success' : 'secondary'}>
                <BadgeDot />
                {accessAgent.is_active ? 'Active' : 'Inactive'}
              </Badge>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Assign to new internal users</p>
              <p className="font-medium">
                {accessAgent.assign_to_new_internal_contacts ? 'Yes' : 'No'}
              </p>
            </div>
            {accessAgent.description && (
              <div className="md:col-span-2">
                <p className="text-sm text-muted-foreground">Description</p>
                <p className="font-medium">{accessAgent.description}</p>
              </div>
            )}
            <div>
              <p className="text-sm text-muted-foreground">Created At</p>
              <p className="font-medium">{formatDate(new Date(accessAgent.created_at))}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Updated At</p>
              <p className="font-medium">{formatDate(new Date(accessAgent.updated_at))}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Team Assignments */}
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 space-y-0">
          <div className="min-w-0">
            <CardTitle>Team Assignments</CardTitle>
            {activeCompany?.name ? (
              // Team sets are per company: the same agent routes to a different
              // ladder in each. Without this label an admin reads an empty list as
              // "this agent has no teams" rather than "none in the company I am in".
              <p className="text-sm text-muted-foreground mt-1">
                Showing {activeCompany.name}. Switch company to see its team sets.
              </p>
            ) : null}
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={isRefetchingAgentTeams}
            onClick={() => refetchAgentTeams()}
            className="shrink-0 gap-2"
            aria-label="Refresh team assignments"
          >
            <RefreshCw
              className={`size-4 ${isRefetchingAgentTeams ? 'animate-spin' : ''}`}
              aria-hidden
            />
            Refresh
          </Button>
        </CardHeader>
        <CardContent>
          {assignments.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground border rounded-lg border-dashed">
              <p>
                No team assignments
                {activeCompany?.name ? ` for ${activeCompany.name}` : ''} yet.
              </p>
              <p className="text-sm mt-1">Edit agent to add assignments.</p>
              <Button variant="outline" size="sm" className="mt-3" onClick={() => setEditModalOpen(true)}>
                Edit Access Agent
              </Button>
            </div>
          ) : (
            <div className="space-y-4">
              {groupedAssignments.map((group) => (
                <div key={group.code} className="rounded-lg border p-3">
                  <div className="mb-3">
                    <span className="font-mono text-sm font-semibold">{group.code}</span>
                  </div>
                  <div className="space-y-2">
                    {group.rows.map((a) => {
                const assignmentKey = `${a.code}::${a.tier ?? 'none'}::${a.team_id}`;
                const isExpanded = expandedTeams.has(assignmentKey);
                const members = a.members ?? [];
                const lastAssignedId = a.last_assigned?.id;
                const nextInLineId = a.next_in_line?.id;

                return (
                  <Collapsible
                    key={assignmentKey}
                    open={isExpanded}
                    onOpenChange={() => toggleTeam(assignmentKey)}
                  >
                    <div className="border rounded-lg">
                      <CollapsibleTrigger className="w-full">
                        <div className="flex items-center justify-between p-4 hover:bg-accent/50 transition-colors">
                          <div className="flex items-center gap-3 flex-1">
                            <div className="flex items-center gap-2">
                              {isExpanded ? (
                                <ChevronDown className="size-4 text-muted-foreground" />
                              ) : (
                                <ChevronRight className="size-4 text-muted-foreground" />
                              )}
                            </div>
                            <div className="text-left flex-1">
                              <div className="flex items-center gap-3 flex-wrap">
                                {typeof (a as AgentTeamAssignment).tier === 'number' &&
                                  (a as AgentTeamAssignment).tier! >= 1 &&
                                  (a as AgentTeamAssignment).tier! <= 3 && (
                                  <Badge variant="secondary" className="text-xs font-normal">
                                    Tier {(a as AgentTeamAssignment).tier}
                                  </Badge>
                                )}
                                <span className="text-sm text-muted-foreground">•</span>
                                <span className="font-medium">
                                  {a.team_name ?? teamNameMap.get(a.team_id) ?? a.team_id}
                                </span>
                                {typeof (a as AgentTeamAssignment).tier === 'number' &&
                                  (a as AgentTeamAssignment).tier! >= 2 && (
                                  <Badge
                                    variant={
                                      (a as AgentTeamAssignment).notify_on_extension === false
                                        ? 'secondary'
                                        : 'primary'
                                    }
                                    className="text-xs font-normal"
                                  >
                                    {(a as AgentTeamAssignment).notify_on_extension === false
                                      ? 'No extension notice'
                                      : 'Notify on extension'}
                                  </Badge>
                                )}
                              </div>
                              <div className="flex items-center gap-4 mt-1 text-xs text-muted-foreground">
                                <span>{members.length} member{members.length !== 1 ? 's' : ''}</span>
                                {a.last_assigned && (
                                  <span>
                                    Last assigned: <span className="font-medium text-foreground">
                                      {a.last_assigned.name || a.last_assigned.email || a.last_assigned.id}
                                    </span>
                                  </span>
                                )}
                                {a.next_in_line && (
                                  <span>
                                    Next in line: <span className="font-medium text-primary">
                                      {a.next_in_line.name || a.next_in_line.email || a.next_in_line.id}
                                    </span>
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>
                        </div>
                      </CollapsibleTrigger>
                      <CollapsibleContent>
                        <div className="px-4 pb-4 border-t bg-accent/30">
                          {members.length === 0 ? (
                            <div className="py-4 text-sm text-muted-foreground text-center">
                              No members in this team
                            </div>
                          ) : (
                            <div className="py-3 space-y-2">
                              <div className="text-xs font-medium text-muted-foreground mb-2">
                                Team Members (in round-robin order):
                              </div>
                              <div className="space-y-1.5">
                                {members.map((member: AgentTeamMemberInfo, idx: number) => {
                                  const excluded = member.include_in_round_robin === false;
                                  const isLastAssigned = member.id === lastAssignedId;
                                  const isNextInLine = member.id === nextInLineId;
                                  const displayName = member.name || member.email || member.id;

                                  return (
                                    <div
                                      key={member.id}
                                      className={`
                                        flex flex-col gap-2 p-2 rounded-md text-sm
                                        sm:flex-row sm:items-center sm:justify-between
                                        ${isNextInLine ? 'bg-primary/10 border border-primary/20' : ''}
                                        ${isLastAssigned && !isNextInLine ? 'bg-muted/50' : ''}
                                        ${excluded ? 'opacity-60' : ''}
                                      `}
                                    >
                                      <div className="flex items-center gap-2 min-w-0 sm:flex-1">
                                        <span className="text-xs text-muted-foreground w-6 shrink-0">
                                          {idx + 1}.
                                        </span>
                                        <div className="flex flex-col min-w-0 flex-1 sm:flex-row sm:items-center sm:gap-2">
                                          <span className="font-medium truncate">{displayName}</span>
                                          {member.email && member.email !== displayName && (
                                            <span className="text-xs text-muted-foreground truncate">
                                              ({member.email})
                                            </span>
                                          )}
                                        </div>
                                        <TeamMemberRespondIoButton member={member} />
                                      </div>
                                      <div className="flex flex-wrap items-center gap-2 pl-8 sm:pl-0 sm:shrink-0">
                                        <MemberMarketSegmentEditor teamId={a.team_id} userId={member.id} />
                                        <MemberBrandEditor teamId={a.team_id} userId={member.id} />
                                        {excluded && (
                                          <Badge variant="outline" className="text-xs text-muted-foreground">
                                            Excluded from round robin
                                          </Badge>
                                        )}
                                        {isLastAssigned && (
                                          <Badge variant="secondary" className="text-xs">
                                            Last assigned
                                          </Badge>
                                        )}
                                        {isNextInLine && (
                                          <Badge variant="primary" className="text-xs">
                                            Next in line
                                          </Badge>
                                        )}
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}
                        </div>
                      </CollapsibleContent>
                    </div>
                  </Collapsible>
                    );
                  })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>


      {/* Contact Access Agents Table */}
      <Card>
        <CardHeader>
          <CardTitle>Contact Access Agents</CardTitle>
        </CardHeader>
        <ContactAccessAgentsTable accessAgentId={accessAgentId} />
      </Card>

      <AccessAgentFormModal
        open={editModalOpen}
        onOpenChange={setEditModalOpen}
        accessAgentId={accessAgentId}
      />

    </div>
  );
}
