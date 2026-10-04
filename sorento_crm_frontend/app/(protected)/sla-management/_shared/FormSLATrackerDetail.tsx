'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  AlertCircle,
  ArrowUpCircle,
  CalendarClock,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Clock,
  History,
  Info,
  RefreshCw,
  Settings,
  UserCog,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  formatDateTime,
  formatDuration,
  formatDurationWithSeconds,
  parseDateTimeAsUTC,
} from '@/lib/helpers';
import { useHasPermission } from '@/hooks/usePermissions';
import { useQuery } from '@tanstack/react-query';
import {
  useConversationSLATrackingDetail,
  useConversationSLATestOverrides,
} from '@/app/(protected)/sla-management/conversation-sla-tracking/hooks/useConversationSLATracking';
import EventLogTable from '@/app/(protected)/sla-management/conversation-sla-tracking/components/EventLogTable';
import { getUserLookup } from '@/services/userSelectService';
import { escalateFormTracking } from './formSLAService';
import { toast } from '@/lib/toast';

const SLA_TEST_OVERRIDE_PERMISSION =
  'sla_management.conversation_sla_tracking.test_override';

interface FormSLATrackerDetailProps {
  trackingId: string;
  onBack: () => void;
}

function toDatetimeLocalInputValue(
  isoLike: string | Date | undefined | null,
): string {
  if (!isoLike) return '';
  const d = isoLike instanceof Date ? isoLike : parseDateTimeAsUTC(isoLike);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function datetimeLocalToISO(local: string): string {
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) throw new Error('Invalid date');
  return d.toISOString();
}

export default function FormSLATrackerDetail({
  trackingId,
  onBack,
}: FormSLATrackerDetailProps) {
  const queryClient = useQueryClient();
  const canTestOverride = useHasPermission(SLA_TEST_OVERRIDE_PERMISSION);
  const { data: tracking, isLoading } =
    useConversationSLATrackingDetail(trackingId);
  const overrideMut = useConversationSLATestOverrides(trackingId);

  const [trackingOpen, setTrackingOpen] = useState(true);
  const [responseOpen, setResponseOpen] = useState(false);
  const [resolutionOpen, setResolutionOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const [escalateOpen, setEscalateOpen] = useState(false);
  const [escalateReason, setEscalateReason] = useState('');
  const escalateMut = useMutation({
    mutationFn: () => escalateFormTracking(trackingId, escalateReason.trim()),
    onSuccess: (res) => {
      toast.success(`Escalated to tier ${res.current_tier}`);
      setEscalateOpen(false);
      setEscalateReason('');
      void queryClient.invalidateQueries({
        queryKey: ['conversation-sla-tracking-detail', trackingId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['conversation-sla-event-logs', trackingId],
      });
      void queryClient.invalidateQueries({ queryKey: ['form-sla-trackers'] });
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to escalate'),
  });

  const [assigneeDialogOpen, setAssigneeDialogOpen] = useState(false);
  const [tierStartedDialogOpen, setTierStartedDialogOpen] = useState(false);
  const [initiatedDialogOpen, setInitiatedDialogOpen] = useState(false);
  const [selectedAssigneeId, setSelectedAssigneeId] = useState('');
  const [tierStartedLocal, setTierStartedLocal] = useState('');
  const [initiatedLocal, setInitiatedLocal] = useState('');

  const {
    data: usersSelect = [],
    error: usersSelectError,
    refetch: refetchUsersSelect,
  } = useQuery({
    queryKey: ['user-lookup', 'form-sla-test-override'],
    queryFn: () => getUserLookup(),
    enabled: assigneeDialogOpen && canTestOverride,
    staleTime: 1000 * 60 * 5,
  });

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ['conversation-sla-tracking-detail', trackingId],
          refetchType: 'active',
        }),
        queryClient.invalidateQueries({
          queryKey: ['conversation-sla-event-logs', trackingId],
          refetchType: 'active',
        }),
      ]);
    } finally {
      setRefreshing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }
  if (!tracking) {
    return (
      <div className="space-y-4">
        <Button variant="outline" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-2 size-4" /> Back to list
        </Button>
        <p className="text-sm text-muted-foreground">SLA tracker not found.</p>
      </div>
    );
  }

  const getTimeElapsed = (): string => {
    if (tracking.is_resolved && tracking.resolved_at && tracking.initiated_at) {
      const initiated = parseDateTimeAsUTC(tracking.initiated_at);
      const resolved = parseDateTimeAsUTC(tracking.resolved_at);
      return formatDurationWithSeconds(resolved.getTime() - initiated.getTime());
    }
    const now = new Date();
    const started = parseDateTimeAsUTC(tracking.initiated_at);
    return formatDurationWithSeconds(now.getTime() - started.getTime());
  };

  const getTimeRemainingResponse = (): string | null => {
    if (tracking.is_responded) return null;
    if (tracking.time_remaining_response_seconds != null) {
      const s = tracking.time_remaining_response_seconds;
      const str = formatDuration(Math.abs(s) * 1000);
      return s < 0 ? `${str} overdue` : `${str} left`;
    }
    const now = new Date();
    const due = parseDateTimeAsUTC(tracking.due_at);
    const diff = due.getTime() - now.getTime();
    const str = formatDuration(Math.abs(diff));
    return diff < 0 ? `${str} overdue` : `${str} left`;
  };

  const getTimeRemainingResolution = (): string | null => {
    if (tracking.is_resolved) return null;
    if (tracking.time_remaining_resolution_seconds != null) {
      const s = tracking.time_remaining_resolution_seconds;
      const str = formatDuration(Math.abs(s) * 1000);
      return s < 0 ? `${str} overdue` : `${str} left`;
    }
    const dueRes = tracking.due_at_resolution ?? tracking.resolution_due_at;
    if (dueRes) {
      const now = new Date();
      const due = parseDateTimeAsUTC(dueRes);
      const diff = due.getTime() - now.getTime();
      const str = formatDuration(Math.abs(diff));
      return diff < 0 ? `${str} overdue` : `${str} left`;
    }
    return null;
  };

  const getResponseDuration = (): string | null => {
    if (tracking.is_responded && tracking.responded_at && tracking.initiated_at) {
      const i = parseDateTimeAsUTC(tracking.initiated_at).getTime();
      const r = parseDateTimeAsUTC(tracking.responded_at).getTime();
      return formatDuration(r - i);
    }
    return null;
  };
  const getResolutionDuration = (): string | null => {
    if (tracking.is_resolved && tracking.resolved_at && tracking.initiated_at) {
      const i = parseDateTimeAsUTC(tracking.initiated_at).getTime();
      const r = parseDateTimeAsUTC(tracking.resolved_at).getTime();
      return formatDuration(r - i);
    }
    return null;
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Button variant="ghost" size="sm" onClick={onBack} className="-ml-2">
            <ArrowLeft className="mr-2 size-4" /> Back to list
          </Button>
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-semibold">
              {tracking.team_set_code || 'SLA Tracker'} · Tier{' '}
              {tracking.current_tier}
            </h2>
            {tracking.is_resolved ? (
              <Badge variant="success">
                <CheckCircle className="mr-1 size-3" />
                Resolved
              </Badge>
            ) : tracking.escalated_at ? (
              <Badge variant="warning">
                <AlertCircle className="mr-1 size-3" />
                Escalated
              </Badge>
            ) : tracking.is_responded ? (
              <Badge variant="info">
                <Clock className="mr-1 size-3" />
                Responded
              </Badge>
            ) : (
              <Badge variant="info">
                <Clock className="mr-1 size-3" />
                Pending
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            Policy:{' '}
            {tracking.policy?.name ||
              tracking.policy_name ||
              tracking.policy?.code ||
              tracking.policy_code ||
              '-'}{' '}
            • Agent: {tracking.agent_code || '-'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={handleRefresh}
            disabled={refreshing || isLoading}
          >
            <RefreshCw
              className={`mr-2 size-4 ${refreshing ? 'animate-spin' : ''}`}
            />
            Refresh
          </Button>
          {!tracking.is_resolved ? (
            <Button onClick={() => setEscalateOpen(true)}>
              <ArrowUpCircle className="mr-2 size-4" />
              Escalate
            </Button>
          ) : null}
          {canTestOverride ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="icon" title="Test overrides" aria-label="Test overrides">
                  <Settings className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onSelect={() => {
                    setSelectedAssigneeId(tracking.assigned_to_id ?? '');
                    setAssigneeDialogOpen(true);
                  }}
                >
                  <UserCog className="mr-2 size-4" />
                  Overwrite assignee
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={() => {
                    setTierStartedLocal(
                      toDatetimeLocalInputValue(tracking.current_tier_started_at),
                    );
                    setTierStartedDialogOpen(true);
                  }}
                >
                  <CalendarClock className="mr-2 size-4" />
                  Set current tier started at
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={() => {
                    setInitiatedLocal(
                      toDatetimeLocalInputValue(tracking.initiated_at),
                    );
                    setInitiatedDialogOpen(true);
                  }}
                >
                  <CalendarClock className="mr-2 size-4" />
                  Set initiated at
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                {!tracking.is_responded && (
                  <DropdownMenuItem
                    onSelect={() => overrideMut.mutate({ is_responded: true })}
                  >
                    <CheckCircle className="mr-2 size-4" />
                    Mark as responded
                  </DropdownMenuItem>
                )}
                {!tracking.is_resolved && (
                  <DropdownMenuItem
                    onSelect={() => overrideMut.mutate({ is_resolved: true })}
                  >
                    <CheckCircle className="mr-2 size-4" />
                    Mark as resolved
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList className="mb-4">
          <TabsTrigger value="overview">
            <Info />
            <span>Overview</span>
          </TabsTrigger>
          <TabsTrigger value="event-log">
            <History />
            <span>Event Log</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <div className="space-y-4">
            <Collapsible open={trackingOpen} onOpenChange={setTrackingOpen}>
              <Card>
                <CollapsibleTrigger asChild>
                  <CardHeader className="flex cursor-pointer flex-row items-center justify-between space-y-0 rounded-t-lg transition-colors hover:bg-muted/50">
                    <CardTitle className="text-base">Tracking Information</CardTitle>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-muted-foreground">
                        Time elapsed: {getTimeElapsed()}
                      </span>
                      {trackingOpen ? (
                        <ChevronDown className="size-4 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="size-4 text-muted-foreground" />
                      )}
                    </div>
                  </CardHeader>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <CardContent className="px-6 pt-0 pb-6">
                    <div className="grid grid-cols-1 gap-4 pt-4 md:grid-cols-2">
                      <div>
                        <p className="text-sm text-muted-foreground">Assigned To</p>
                        <p className="font-medium">
                          {tracking.assigned_user_name ||
                            tracking.assigned_user?.name ||
                            tracking.assigned_user?.email ||
                            tracking.assigned_to ||
                            '-'}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Initiated At</p>
                        <p className="font-medium">
                          {formatDateTime(parseDateTimeAsUTC(tracking.initiated_at))}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">
                          Current Tier Started At
                        </p>
                        <p className="font-medium">
                          {formatDateTime(
                            parseDateTimeAsUTC(tracking.current_tier_started_at),
                          )}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">
                          Due at (response)
                        </p>
                        <p className="font-medium">
                          {formatDateTime(parseDateTimeAsUTC(tracking.due_at))}
                        </p>
                      </div>
                      {(tracking.due_at_resolution ?? tracking.resolution_due_at) && (
                        <div>
                          <p className="text-sm text-muted-foreground">
                            Due at (resolution)
                          </p>
                          <p className="font-medium">
                            {formatDateTime(
                              parseDateTimeAsUTC(
                                tracking.due_at_resolution ??
                                  tracking.resolution_due_at!,
                              ),
                            )}
                          </p>
                        </div>
                      )}
                      {tracking.escalated_at && (
                        <div>
                          <p className="text-sm text-muted-foreground">Escalated At</p>
                          <p className="font-medium">
                            {formatDateTime(parseDateTimeAsUTC(tracking.escalated_at))}
                          </p>
                        </div>
                      )}
                      {tracking.escalation_reason && (
                        <div className="md:col-span-2">
                          <p className="text-sm text-muted-foreground">
                            Escalation Reason
                          </p>
                          <p className="font-medium">{tracking.escalation_reason}</p>
                        </div>
                      )}
                      <div>
                        <p className="text-sm text-muted-foreground">Agent Code</p>
                        <p className="font-medium">{tracking.agent_code || '-'}</p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Stage</p>
                        <p className="font-medium">{tracking.team_set_code || '-'}</p>
                      </div>
                    </div>
                  </CardContent>
                </CollapsibleContent>
              </Card>
            </Collapsible>

            <Collapsible open={responseOpen} onOpenChange={setResponseOpen}>
              <Card>
                <CollapsibleTrigger asChild>
                  <CardHeader className="flex cursor-pointer flex-row items-center justify-between space-y-0 rounded-t-lg transition-colors hover:bg-muted/50">
                    <CardTitle className="text-base">Response time</CardTitle>
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-sm font-medium ${
                          getTimeRemainingResponse()?.includes('overdue')
                            ? 'text-destructive'
                            : ''
                        }`}
                      >
                        {tracking.is_responded
                          ? getResponseDuration() ??
                            (tracking.response_time != null
                              ? formatDuration(
                                  Number(tracking.response_time) * 3600 * 1000,
                                )
                              : '-')
                          : getTimeRemainingResponse() ?? '-'}
                      </span>
                      {responseOpen ? (
                        <ChevronDown className="size-4 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="size-4 text-muted-foreground" />
                      )}
                    </div>
                  </CardHeader>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <CardContent className="px-6 pt-0 pb-6">
                    <div className="grid grid-cols-1 gap-4 pt-4 md:grid-cols-2">
                      <div>
                        <p className="text-sm text-muted-foreground">Is Responded</p>
                        <p className="font-medium">
                          {tracking.is_responded ? (
                            <Badge variant="success">
                              Yes
                            </Badge>
                          ) : (
                            <Badge variant="secondary">
                              No
                            </Badge>
                          )}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Responded At</p>
                        <p className="font-medium">
                          {tracking.responded_at
                            ? formatDateTime(
                                parseDateTimeAsUTC(tracking.responded_at),
                              )
                            : '-'}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">
                          Response Duration
                        </p>
                        <p className="font-medium">{getResponseDuration() || '-'}</p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Responded By</p>
                        <p className="font-medium">
                          {tracking.responded_by_user_name || '-'}
                        </p>
                      </div>
                      {tracking.average_response_time != null && (
                        <div>
                          <p className="text-sm text-muted-foreground">
                            Average Response Time
                          </p>
                          <p className="font-medium">
                            {formatDuration(
                              Number(tracking.average_response_time) * 3600 * 1000,
                            )}
                          </p>
                        </div>
                      )}
                    </div>
                  </CardContent>
                </CollapsibleContent>
              </Card>
            </Collapsible>

            <Collapsible open={resolutionOpen} onOpenChange={setResolutionOpen}>
              <Card>
                <CollapsibleTrigger asChild>
                  <CardHeader className="flex cursor-pointer flex-row items-center justify-between space-y-0 rounded-t-lg transition-colors hover:bg-muted/50">
                    <CardTitle className="text-base">Resolution time</CardTitle>
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-sm font-medium ${
                          getTimeRemainingResolution()?.includes('overdue')
                            ? 'text-destructive'
                            : ''
                        }`}
                      >
                        {tracking.is_resolved
                          ? getResolutionDuration() ??
                            (tracking.resolution_duration != null
                              ? formatDuration(
                                  Number(tracking.resolution_duration) *
                                    3600 *
                                    1000,
                                )
                              : '-')
                          : getTimeRemainingResolution() ?? '-'}
                      </span>
                      {resolutionOpen ? (
                        <ChevronDown className="size-4 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="size-4 text-muted-foreground" />
                      )}
                    </div>
                  </CardHeader>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <CardContent className="px-6 pt-0 pb-6">
                    <div className="grid grid-cols-1 gap-4 pt-4 md:grid-cols-2">
                      <div>
                        <p className="text-sm text-muted-foreground">Is Resolved</p>
                        <p className="font-medium">
                          {tracking.is_resolved ? (
                            <Badge variant="success">
                              Yes
                            </Badge>
                          ) : (
                            <Badge variant="secondary">
                              No
                            </Badge>
                          )}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Resolved At</p>
                        <p className="font-medium">
                          {tracking.resolved_at
                            ? formatDateTime(parseDateTimeAsUTC(tracking.resolved_at))
                            : '-'}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">
                          Resolution Duration
                        </p>
                        <p className="font-medium">
                          {getResolutionDuration() || '-'}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Resolved By</p>
                        <p className="font-medium">
                          {tracking.resolved_by_user_name ||
                            tracking.resolved_by ||
                            '-'}
                        </p>
                      </div>
                      {tracking.average_resolution_time != null && (
                        <div>
                          <p className="text-sm text-muted-foreground">
                            Average Resolution Time
                          </p>
                          <p className="font-medium">
                            {formatDuration(
                              Number(tracking.average_resolution_time) *
                                3600 *
                                1000,
                            )}
                          </p>
                        </div>
                      )}
                    </div>
                  </CardContent>
                </CollapsibleContent>
              </Card>
            </Collapsible>
          </div>
        </TabsContent>

        <TabsContent value="event-log">
          <EventLogTable
            trackingId={trackingId}
            agentCode={tracking.agent_code}
            teamSetCode={tracking.team_set_code}
          />
        </TabsContent>
      </Tabs>

      <Dialog open={escalateOpen} onOpenChange={setEscalateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              Escalate to tier {(tracking.current_tier ?? 1) + 1}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-2 py-1">
            <Label htmlFor="escalate-reason">Reason</Label>
            <Input
              id="escalate-reason"
              placeholder="Why escalate now?"
              value={escalateReason}
              onChange={(e) => setEscalateReason(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Moves this stage to the next tier and reassigns per policy. The new
              assignee is notified.
            </p>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setEscalateOpen(false)}
              disabled={escalateMut.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => escalateMut.mutate()}
              disabled={escalateMut.isPending || !escalateReason.trim()}
            >
              {escalateMut.isPending ? 'Escalating…' : 'Escalate'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={assigneeDialogOpen} onOpenChange={setAssigneeDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Overwrite assignee</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-1">
            <Label>User</Label>
            <SearchableSelect
              value={selectedAssigneeId}
              onChange={setSelectedAssigneeId}
              loadError={usersSelectError}
              onRetry={() => void refetchUsersSelect()}
              options={[
                { value: '', label: 'No assignee' },
                ...usersSelect.map((user) => ({
                  value: user.id,
                  label: user.name || 'Unnamed user',
                  searchText: user.name ?? '',
                })),
              ]}
              placeholder="No assignee"
              emptyMessage="No user found."
              triggerClassName="w-full"
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setAssigneeDialogOpen(false)}
              disabled={overrideMut.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                overrideMut.mutate(
                  { assigned_to_id: selectedAssigneeId || null },
                  {
                    onSuccess: () => setAssigneeDialogOpen(false),
                  },
                );
              }}
              disabled={overrideMut.isPending}
            >
              Save assignee
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={tierStartedDialogOpen}
        onOpenChange={setTierStartedDialogOpen}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Set current tier started at</DialogTitle>
          </DialogHeader>
          <div className="grid gap-2">
            <Label>Tier started at</Label>
            <Input
              type="datetime-local"
              value={tierStartedLocal}
              onChange={(e) => setTierStartedLocal(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setTierStartedDialogOpen(false)}
              disabled={overrideMut.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                try {
                  overrideMut.mutate(
                    {
                      current_tier_started_at: datetimeLocalToISO(tierStartedLocal),
                    },
                    {
                      onSuccess: () => setTierStartedDialogOpen(false),
                    },
                  );
                } catch {
                  toast.error('Invalid date');
                }
              }}
              disabled={overrideMut.isPending}
            >
              Save tier start
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={initiatedDialogOpen} onOpenChange={setInitiatedDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Set initiated at</DialogTitle>
          </DialogHeader>
          <div className="grid gap-2">
            <Label>Initiated at</Label>
            <Input
              type="datetime-local"
              value={initiatedLocal}
              onChange={(e) => setInitiatedLocal(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setInitiatedDialogOpen(false)}
              disabled={overrideMut.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                try {
                  overrideMut.mutate(
                    { initiated_at: datetimeLocalToISO(initiatedLocal) },
                    {
                      onSuccess: () => setInitiatedDialogOpen(false),
                    },
                  );
                } catch {
                  toast.error('Invalid date');
                }
              }}
              disabled={overrideMut.isPending}
            >
              Save initiated time
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
