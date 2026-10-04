'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { useConversationSLATrackingDetail, useConversationSLATestOverrides } from '../hooks/useConversationSLATracking';
import DetailActions from '@/components/common/DetailActions';
import { useBackToListHref } from '@/components/common/BackToList';
import { recordActionItems } from '@/components/common/recordActions';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import { useConversationSlaActions } from '../actions';
import { conversationSlaPagerQuery } from '../hooks/useConversationSLATracking';
import { escalateConversationSLATracking, type ConversationSLATestOverridesBody } from '../services/conversationSLATrackingService';
import { formatDateTime, formatDuration, formatDurationWithSeconds, parseDateTimeAsUTC } from '@/lib/helpers';
import EventLogTable from './EventLogTable';
import { CheckCircle, Clock, AlertCircle, RefreshCw, ChevronDown, ChevronRight, History, Info, ExternalLink, CalendarClock, UserCog, MessageSquare, TrendingUp } from 'lucide-react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import { getUserLookup } from '@/services/userSelectService';
import { toast } from '@/lib/toast';
import { Sheet, SheetBody, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import SlaTrackingChatRecords from './SlaTrackingChatRecords';
import PortalLinkButton from '@/components/contacts/PortalLinkButton';
import { humanName, slaHandler } from '../lib/slaHandler';

const RESPOND_IO_INBOX_BASE_URL = 'https://app.respond.io/space/364817/inbox';

const SLA_TEST_OVERRIDE_PERMISSION = 'sla_management.conversation_sla_tracking.test_override';

function toDatetimeLocalInputValue(isoLike: string | Date | undefined | null): string {
  if (!isoLike) return '';
  const d = isoLike instanceof Date ? isoLike : parseDateTimeAsUTC(isoLike);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function datetimeLocalToISO(local: string): string {
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) {
    throw new Error('Invalid date');
  }
  return d.toISOString();
}

interface ConversationSLATrackingDetailProps {
  trackingId: string;
  /** Where to return after delete / not-found. Defaults to the conversation list;
   *  the form SLA tracking page passes its own list so back-navigation stays in-context. */
  backHref?: string;
  backLabel?: string;
}

export default function ConversationSLATrackingDetail({
  trackingId,
  backHref = '/sla-management/conversation-sla-tracking',
  backLabel = 'Conversation SLA Tracking',
}: ConversationSLATrackingDetailProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const canSlaTestOverride = useHasPermission(SLA_TEST_OVERRIDE_PERMISSION);
  const { data: tracking, isLoading } = useConversationSLATrackingDetail(trackingId);
  // Only the conversation-context detail carries the pager. The form SLA tracking
  // page reuses this component with its own backHref; its records live in the form
  // scope, so the conversation list page it would walk is not the one it came from.
  const isConversationContext =
    backHref === '/sla-management/conversation-sla-tracking';
  // Delete lands where Back lands: the list page, sort, search and filters the
  // reader left, which the row click wrote into this URL.
  const backListHref = useBackToListHref(backHref);
  // Everything a list row can do too, declared once (D15). Delete asks nothing
  // and hands its countdown back for the primary slot instead (D7).
  const { actions: sharedActions, pending: sharedPending } = useConversationSlaActions(
    tracking,
    { onDeleted: () => router.push(backListHref) },
  );
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [trackingOpen, setTrackingOpen] = useState(false);
  const [responseOpen, setResponseOpen] = useState(false);
  const [resolutionOpen, setResolutionOpen] = useState(false);
  const testOverrideMutation = useConversationSLATestOverrides(trackingId);
  const escalateMutation = useMutation({
    mutationFn: (reason: string) => escalateConversationSLATracking(trackingId, reason),
    onSuccess: () => {
      toast.success('Escalated to the next tier.');
      setEscalateDialogOpen(false);
      setEscalateReason('');
      queryClient.invalidateQueries({
        queryKey: ['conversation-sla-tracking-detail', trackingId],
        refetchType: 'active',
      });
      queryClient.invalidateQueries({
        queryKey: ['conversation-sla-event-logs', trackingId],
        refetchType: 'active',
      });
    },
    onError: (e: Error) => toast.error(e.message || 'Failed to escalate'),
  });

  const [assigneeDialogOpen, setAssigneeDialogOpen] = useState(false);
  const [tierStartedDialogOpen, setTierStartedDialogOpen] = useState(false);
  const [initiatedDialogOpen, setInitiatedDialogOpen] = useState(false);
  const [markRespondedDialogOpen, setMarkRespondedDialogOpen] = useState(false);
  const [markResolvedDialogOpen, setMarkResolvedDialogOpen] = useState(false);
  const [escalateDialogOpen, setEscalateDialogOpen] = useState(false);
  const [escalateReason, setEscalateReason] = useState('');
  const [conversationSheetOpen, setConversationSheetOpen] = useState(false);
  const [selectedAssigneeId, setSelectedAssigneeId] = useState('');
  const [tierStartedLocal, setTierStartedLocal] = useState('');
  const [initiatedLocal, setInitiatedLocal] = useState('');
  const [reopenDialogOpen, setReopenDialogOpen] = useState(false);
  const [reopenAgentCode, setReopenAgentCode] = useState('');
  const [reopenTeamSetCode, setReopenTeamSetCode] = useState('');
  const [reopenTierStarted, setReopenTierStarted] = useState('');
  const [reopenInitiated, setReopenInitiated] = useState('');
  const [reopenResetResponded, setReopenResetResponded] = useState(true);

  const {
    data: usersSelect = [],
    error: usersSelectError,
    refetch: refetchUsersSelect,
  } = useQuery({
    queryKey: ['user-lookup', 'sla-test-override'],
    queryFn: () => getUserLookup(),
    enabled: assigneeDialogOpen && canSlaTestOverride,
    staleTime: 1000 * 60 * 5,
  });

  useEffect(() => {
    if (assigneeDialogOpen && tracking) {
      setSelectedAssigneeId(tracking.assigned_to_id ?? '');
    }
  }, [assigneeDialogOpen, tracking]);

  useEffect(() => {
    if (tierStartedDialogOpen && tracking) {
      setTierStartedLocal(toDatetimeLocalInputValue(tracking.current_tier_started_at));
    }
  }, [tierStartedDialogOpen, tracking]);

  useEffect(() => {
    if (initiatedDialogOpen && tracking) {
      setInitiatedLocal(toDatetimeLocalInputValue(tracking.initiated_at));
    }
  }, [initiatedDialogOpen, tracking]);

  useEffect(() => {
    if (reopenDialogOpen && tracking) {
      setReopenAgentCode(tracking.agent_code ?? '');
      setReopenTeamSetCode(tracking.team_set_code ?? '');
      setReopenTierStarted(toDatetimeLocalInputValue(tracking.current_tier_started_at));
      setReopenInitiated(toDatetimeLocalInputValue(tracking.initiated_at));
      setReopenResetResponded(true);
    }
  }, [reopenDialogOpen, tracking]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
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
      setIsRefreshing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!tracking) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">SLA tracking not found</p>
        <Button variant="outline" onClick={() => router.push(backListHref)} className="mt-4">
          Back to {backLabel}
        </Button>
      </div>
    );
  }

  /** Time elapsed: stops only when resolved (resolved_at - initiated_at). Until then, keeps running (now - initiated_at). */
  const getTimeElapsed = (): string | null => {
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
      const str = formatDuration(Math.abs(tracking.time_remaining_response_seconds) * 1000);
      return tracking.time_remaining_response_seconds < 0 ? `${str} overdue` : `${str} left`;
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
    if (tracking.resolution_due_at) {
      const now = new Date();
      const due = parseDateTimeAsUTC(tracking.resolution_due_at);
      const diff = due.getTime() - now.getTime();
      const str = formatDuration(Math.abs(diff));
      return diff < 0 ? `${str} overdue` : `${str} left`;
    }
    return null;
  };

  const getResponseDuration = () => {
    if (tracking.is_responded && tracking.responded_at && tracking.initiated_at) {
      const initiated = parseDateTimeAsUTC(tracking.initiated_at);
      const responded = parseDateTimeAsUTC(tracking.responded_at);
      const diff = responded.getTime() - initiated.getTime();
      return formatDuration(diff);
    }
    return null;
  };

  const getResolutionDuration = () => {
    if (tracking.is_resolved && tracking.resolved_at && tracking.initiated_at) {
      const initiated = parseDateTimeAsUTC(tracking.initiated_at);
      const resolved = parseDateTimeAsUTC(tracking.resolved_at);
      const diff = resolved.getTime() - initiated.getTime();
      return formatDuration(diff);
    }
    return null;
  };

  // Owner ruling S4 (23 Sep 2026): resolve keeps the assignee for audit now,
  // so the header names the assignee whether the row is open or resolved -
  // same helper the listing cell uses, so they cannot disagree. Who resolved
  // it is a separate fact, shown alongside it below when the row is resolved.
  const handler = slaHandler(tracking);
  const resolverName = tracking.is_resolved
    ? humanName(tracking.resolved_by_user_name, tracking.resolved_by)
    : null;

  const respondIoId = tracking.respond_io_id ?? tracking.contact?.respond_io_id ?? null;
  const contactId = tracking.contact?.id ?? tracking.respond_contact_id ?? null;
  const contactLabel =
    tracking.contact_name ??
    tracking.contact?.name ??
    tracking.contact_phone ??
    tracking.contact?.phone_number ??
    contactId ??
    'contact';
  const respondInboxUrl = respondIoId ? `${RESPOND_IO_INBOX_BASE_URL}/${respondIoId}` : null;
  const messageIdText =
    tracking.message_id != null && tracking.message_id !== undefined
      ? String(tracking.message_id)
      : null;
  const respondMessageUrl =
    respondInboxUrl && messageIdText ? `${respondInboxUrl}#${encodeURIComponent(messageIdText)}` : null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-bold">
              Contact: {tracking.contact_phone || tracking.contact?.phone_number || '-'}
              {tracking.contact_name || tracking.contact?.name 
                ? ` (${tracking.contact_name || tracking.contact?.name})` 
                : ''}
            </h2>
            {tracking.is_resolved ? (
              <Badge variant="success">
                <CheckCircle className="size-3 mr-1" />
                Resolved
              </Badge>
            ) : tracking.escalated_at ? (
              <Badge variant="warning">
                <AlertCircle className="size-3 mr-1" />
                Escalated
              </Badge>
            ) : (
              <Badge variant="info">
                <Clock className="size-3 mr-1" />
                Pending
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            Policy: {tracking.policy?.name || tracking.policy_name || tracking.policy?.code || tracking.policy_code || '-'} • Current Tier: {tracking.current_tier} •{' '}
            {/* Who is assigned, in the header rather than only inside a
                collapsed section - the captain landed here from "Recently
                resolved" and needs it without opening a section. Resolve
                keeps the assignee now (owner ruling S4), so this reads the
                same on an open or a resolved row. */}
            <span data-testid="tracking-handler">
              {handler.prefix}: {handler.name ?? '-'}
            </span>
            {tracking.is_resolved && (
              <>
                {' '}•{' '}
                <span data-testid="tracking-resolver">
                  Resolved by: {resolverName ?? '-'}
                </span>
              </>
            )}
          </p>
        </div>
        <DetailActions
          pager={
            isConversationContext
              ? {
                  ...conversationSlaPagerQuery,
                  detailPath: '/sla-management/conversation-sla-tracking',
                  currentId: trackingId,
                  ariaLabel: 'conversation',
                }
              : undefined
          }
          pendingAction={sharedPending}
          gear={
            /* Through the shared menu, not a raw DropdownMenu: `DetailActions`
               renders a `gear` node verbatim, so a raw one keeps whatever order
               it was written in, and Delete sat fourth with nothing setting it
               apart. The menu moves the destructive item last, behind a
               separator, for every workflow gear alike (D6). */
            <DetailActionsMenu ariaLabel="Conversation SLA actions">
              <DropdownMenuItem
                onClick={handleRefresh}
                disabled={isRefreshing || isLoading}
              >
                <RefreshCw className={`size-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
                Refresh
              </DropdownMenuItem>
              {/* The set the list row renders too (D15) - Sync assignee, Open
                  conversation, Delete tracking. Spliced in as an array so the menu
                  can see the destructive one and move it last by itself. */}
              {recordActionItems(sharedActions)}
              {contactId && (
                <PortalLinkButton
                  contactId={contactId}
                  contactLabel={contactLabel}
                  canSendViaRespondIo={!!respondIoId}
                  variant="menu-item"
                />
              )}
              {!tracking.is_resolved && (tracking.current_tier ?? 1) < 3 && (
                <DropdownMenuItem
                  onSelect={() => {
                    setEscalateReason('');
                    setEscalateDialogOpen(true);
                  }}
                >
                  <TrendingUp className="size-4 mr-2" />
                  Escalate
                </DropdownMenuItem>
              )}
              {canSlaTestOverride && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => setAssigneeDialogOpen(true)}>
                    <UserCog className="size-4 mr-2" />
                    Overwrite assignee
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setTierStartedDialogOpen(true)}>
                    <CalendarClock className="size-4 mr-2" />
                    Set current tier started at
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setInitiatedDialogOpen(true)}>
                    <CalendarClock className="size-4 mr-2" />
                    Set initiated at
                  </DropdownMenuItem>
                  {!tracking.is_responded && (
                    <DropdownMenuItem onSelect={() => setMarkRespondedDialogOpen(true)}>
                      <CheckCircle className="size-4 mr-2" />
                      Mark as responded
                    </DropdownMenuItem>
                  )}
                  {!tracking.is_resolved && (
                    <DropdownMenuItem onSelect={() => setMarkResolvedDialogOpen(true)}>
                      <CheckCircle className="size-4 mr-2" />
                      Mark as resolved
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => setReopenDialogOpen(true)}>
                    <RefreshCw className="size-4 mr-2" />
                    Reopen for retest
                  </DropdownMenuItem>
                </>
              )}
            </DetailActionsMenu>
          }
          primary={
            respondInboxUrl ? (
              <Button onClick={() => setConversationSheetOpen(true)}>
                <MessageSquare className="size-4 mr-2" />
                Chat Records
              </Button>
            ) : null
          }
        />
      </div>

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
            <Button type="button" variant="outline" onClick={() => setAssigneeDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={testOverrideMutation.isPending}
              onClick={() => {
                testOverrideMutation.mutate(
                  { assigned_to_id: selectedAssigneeId === '' ? null : selectedAssigneeId },
                  { onSuccess: () => setAssigneeDialogOpen(false) },
                );
              }}
            >
              Save assignee
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={tierStartedDialogOpen} onOpenChange={setTierStartedDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Set current tier started at</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 py-1">
            <Label htmlFor="tier-started-local">Date and time</Label>
            <Input
              id="tier-started-local"
              type="datetime-local"
              value={tierStartedLocal}
              onChange={(e) => setTierStartedLocal(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setTierStartedDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={!tierStartedLocal || testOverrideMutation.isPending}
              onClick={() => {
                try {
                  const iso = datetimeLocalToISO(tierStartedLocal);
                  testOverrideMutation.mutate(
                    { current_tier_started_at: iso },
                    { onSuccess: () => setTierStartedDialogOpen(false) },
                  );
                } catch {
                  toast.error('Invalid date and time.');
                }
              }}
            >
              Save tier start
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={initiatedDialogOpen} onOpenChange={setInitiatedDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Set initiated at</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 py-1">
            <Label htmlFor="initiated-local">Date and time</Label>
            <Input
              id="initiated-local"
              type="datetime-local"
              value={initiatedLocal}
              onChange={(e) => setInitiatedLocal(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setInitiatedDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={!initiatedLocal || testOverrideMutation.isPending}
              onClick={() => {
                try {
                  const iso = datetimeLocalToISO(initiatedLocal);
                  testOverrideMutation.mutate(
                    { initiated_at: iso },
                    { onSuccess: () => setInitiatedDialogOpen(false) },
                  );
                } catch {
                  toast.error('Invalid date and time.');
                }
              }}
            >
              Save initiated time
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={reopenDialogOpen} onOpenChange={setReopenDialogOpen}>
        <DialogContent className="sm:max-w-md max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Reopen for retest</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-1">
            <p className="text-xs text-muted-foreground">
              Clears the resolved state, applies the routing fields below, and recomputes the
              response/resolution due dates from the tier start + policy hours.
            </p>
            <div className="space-y-2">
              <Label htmlFor="reopen-agent-code">Agent code</Label>
              <Input
                id="reopen-agent-code"
                placeholder="e.g. TECH"
                value={reopenAgentCode}
                onChange={(e) => setReopenAgentCode(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="reopen-team-set-code">Team set code</Label>
              <Input
                id="reopen-team-set-code"
                placeholder="e.g. TECH_TEAM"
                value={reopenTeamSetCode}
                onChange={(e) => setReopenTeamSetCode(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="reopen-initiated">Initiated at</Label>
              <Input
                id="reopen-initiated"
                type="datetime-local"
                value={reopenInitiated}
                onChange={(e) => setReopenInitiated(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="reopen-tier-started">Current tier started at</Label>
              <Input
                id="reopen-tier-started"
                type="datetime-local"
                value={reopenTierStarted}
                onChange={(e) => setReopenTierStarted(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">Due dates are derived from this.</p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4"
                checked={reopenResetResponded}
                onChange={(e) => setReopenResetResponded(e.target.checked)}
              />
              Also reset responded state
            </label>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setReopenDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={testOverrideMutation.isPending}
              onClick={() => {
                try {
                  const body: ConversationSLATestOverridesBody = { is_resolved: false };
                  if (reopenResetResponded) body.is_responded = false;
                  body.agent_code = reopenAgentCode.trim() === '' ? null : reopenAgentCode.trim();
                  body.team_set_code =
                    reopenTeamSetCode.trim() === '' ? null : reopenTeamSetCode.trim();
                  if (reopenInitiated) body.initiated_at = datetimeLocalToISO(reopenInitiated);
                  if (reopenTierStarted)
                    body.current_tier_started_at = datetimeLocalToISO(reopenTierStarted);
                  testOverrideMutation.mutate(body, {
                    onSuccess: () => setReopenDialogOpen(false),
                  });
                } catch {
                  toast.error('Invalid date and time.');
                }
              }}
            >
              {testOverrideMutation.isPending ? 'Reopening…' : 'Reopen'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={markRespondedDialogOpen} onOpenChange={setMarkRespondedDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Mark as responded</AlertDialogTitle>
            <AlertDialogDescription>
              This will mark the conversation as responded and record the current time as the response time. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                testOverrideMutation.mutate(
                  { is_responded: true },
                  { onSuccess: () => setMarkRespondedDialogOpen(false) },
                );
              }}
            >
              Mark responded
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog
        open={escalateDialogOpen}
        onOpenChange={(o) => {
          if (!o) {
            setEscalateDialogOpen(false);
            setEscalateReason('');
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Escalate to tier {(tracking.current_tier ?? 1) + 1}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 py-1">
            <Label htmlFor="convo-escalate-reason">Reason</Label>
            <Input
              id="convo-escalate-reason"
              placeholder="Why escalate now?"
              value={escalateReason}
              onChange={(e) => setEscalateReason(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && escalateReason.trim() && !escalateMutation.isPending) {
                  e.preventDefault();
                  escalateMutation.mutate(escalateReason.trim());
                }
              }}
              autoFocus
            />
            <p className="text-xs text-muted-foreground">
              Moves this conversation to the next tier and reassigns per policy. The new assignee is
              notified.
            </p>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setEscalateDialogOpen(false);
                setEscalateReason('');
              }}
              disabled={escalateMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => escalateMutation.mutate(escalateReason.trim())}
              disabled={escalateMutation.isPending || !escalateReason.trim()}
            >
              {escalateMutation.isPending ? 'Escalating…' : 'Escalate'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={markResolvedDialogOpen} onOpenChange={setMarkResolvedDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Mark as resolved</AlertDialogTitle>
            <AlertDialogDescription>
              This will mark the conversation as resolved and record the current time as the resolution time. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                testOverrideMutation.mutate(
                  { is_resolved: true },
                  { onSuccess: () => setMarkResolvedDialogOpen(false) },
                );
              }}
            >
              Mark resolved
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>


      <Tabs defaultValue="overview" className="w-full">
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

        {/* Tab 1: Overview */}
        <TabsContent value="overview">
          <div className="space-y-6">
          {/* Collapsible: Tracking Information */}
          <Collapsible open={trackingOpen} onOpenChange={setTrackingOpen}>
            <Card>
              <CollapsibleTrigger asChild>
                <CardHeader className="cursor-pointer hover:bg-muted/50 transition-colors rounded-t-lg flex flex-row items-center justify-between space-y-0">
                  <CardTitle className="text-base">Tracking Information</CardTitle>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-muted-foreground">
                      Time elapsed: {getTimeElapsed() ?? '-'}
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
                <CardContent className="pt-0 pb-6 px-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4">
                    <div>
                      <div className="text-sm text-muted-foreground flex items-center gap-1.5">
                        Assigned To
                        {(tracking.assigned_user_name ?? tracking.assigned_user?.name ?? tracking.assigned_user?.email ?? tracking.assigned_to) && (
                          <Popover>
                            <PopoverTrigger asChild>
                              <button
                                type="button"
                                className="inline-flex text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                                aria-label="Show assignee email"
                              >
                                <Info className="size-4" />
                              </button>
                            </PopoverTrigger>
                            <PopoverContent className="w-auto p-3 space-y-3" align="start">
                              <div>
                                <div className="text-sm font-medium text-muted-foreground">Email</div>
                                <div className="text-sm font-medium break-all">
                                  {tracking.assigned_user_email ?? tracking.assigned_user?.email ?? 'No email available'}
                                </div>
                              </div>
                              {(tracking.assigned_user_superior_name ?? tracking.assigned_user_superior_email ?? tracking.assigned_user?.superior?.name ?? tracking.assigned_user?.superior?.email) && (
                                <div>
                                  <div className="text-sm font-medium text-muted-foreground">Superior</div>
                                  <div className="text-sm font-medium break-all">
                                    {[
                                      tracking.assigned_user_superior_name ?? tracking.assigned_user?.superior?.name,
                                      (tracking.assigned_user_superior_email ?? tracking.assigned_user?.superior?.email)
                                        ? `(${tracking.assigned_user_superior_email ?? tracking.assigned_user?.superior?.email})`
                                        : null,
                                    ]
                                      .filter(Boolean)
                                      .join(' ')}
                                  </div>
                                </div>
                              )}
                            </PopoverContent>
                          </Popover>
                        )}
                      </div>
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
                      <p className="font-medium">{formatDateTime(parseDateTimeAsUTC(tracking.initiated_at))}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Current Tier Started At</p>
                      <p className="font-medium">{formatDateTime(parseDateTimeAsUTC(tracking.current_tier_started_at))}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Due at (response)</p>
                      <p className="font-medium">
                        {formatDateTime(parseDateTimeAsUTC(tracking.due_at))}
                        {(() => {
                          const due = parseDateTimeAsUTC(tracking.due_at).getTime();
                          const now = Date.now();
                          const overdue = tracking.is_responded && tracking.responded_at
                            ? parseDateTimeAsUTC(tracking.responded_at).getTime() > due
                            : now > due;
                          return overdue ? (
                            <span className="ml-2 text-destructive text-sm font-medium">Overdue</span>
                          ) : null;
                        })()}
                      </p>
                    </div>
                    {(tracking.due_at_resolution ?? tracking.resolution_due_at) && (
                      <div>
                        <p className="text-sm text-muted-foreground">Due at (resolution)</p>
                        <p className="font-medium">
                          {formatDateTime(parseDateTimeAsUTC(tracking.due_at_resolution ?? tracking.resolution_due_at!))}
                          {(() => {
                            const dueRes = tracking.due_at_resolution ?? tracking.resolution_due_at;
                            if (!dueRes) return null;
                            const due = parseDateTimeAsUTC(dueRes).getTime();
                            const now = Date.now();
                            const overdue = tracking.is_resolved && tracking.resolved_at
                              ? parseDateTimeAsUTC(tracking.resolved_at).getTime() > due
                              : now > due;
                            return overdue ? (
                              <span className="ml-2 text-destructive text-sm font-medium">Overdue</span>
                            ) : null;
                          })()}
                        </p>
                      </div>
                    )}
                    {tracking.escalated_at && (
                      <div>
                        <p className="text-sm text-muted-foreground">Escalated At</p>
                        <p className="font-medium">{formatDateTime(parseDateTimeAsUTC(tracking.escalated_at))}</p>
                      </div>
                    )}
                    {tracking.escalation_reason && (
                      <div className="md:col-span-2">
                        <p className="text-sm text-muted-foreground">Escalation Reason</p>
                        <p className="font-medium">{tracking.escalation_reason}</p>
                      </div>
                    )}
                    <div>
                      <p className="text-sm text-muted-foreground">Agent Code</p>
                      <p className="font-medium">{tracking.agent_code || '-'}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Team Set Code</p>
                      <p className="font-medium">{tracking.team_set_code || '-'}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Message</p>
                      <p className="font-medium">
                        {respondMessageUrl ? (
                          <a
                            href={respondMessageUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex text-primary hover:opacity-80"
                            title="Open message in Respond.io"
                            aria-label="Open message in Respond.io"
                          >
                            <ExternalLink className="size-4" />
                          </a>
                        ) : (
                          '-'
                        )}
                      </p>
                    </div>
                  </div>
                </CardContent>
              </CollapsibleContent>
            </Card>
          </Collapsible>

          {/* Collapsible: Response time */}
          <Collapsible open={responseOpen} onOpenChange={setResponseOpen}>
            <Card>
              <CollapsibleTrigger asChild>
                <CardHeader className="cursor-pointer hover:bg-muted/50 transition-colors rounded-t-lg flex flex-row items-center justify-between space-y-0">
                  <CardTitle className="text-base">Response time</CardTitle>
                  <div className="flex items-center gap-2">
                    <span className={`text-sm font-medium ${getTimeRemainingResponse()?.includes('overdue') ? 'text-destructive' : ''}`}>
                      {tracking.is_responded
                        ? (getResponseDuration() ?? (tracking.response_time != null ? formatDuration(tracking.response_time * 3600 * 1000) : '-'))
                        : (getTimeRemainingResponse() ?? '-')}
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
                <CardContent className="pt-0 pb-6 px-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4">
                    <div>
                      <p className="text-sm text-muted-foreground">Is Responded</p>
                      <p className="font-medium">
                        {tracking.is_responded ? (
                          <Badge variant="success">Yes</Badge>
                        ) : (
                          <Badge variant="secondary">No</Badge>
                        )}
                      </p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Responded At</p>
                      <p className="font-medium">
                        {tracking.responded_at
                          ? formatDateTime(parseDateTimeAsUTC(tracking.responded_at))
                          : '-'}
                      </p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Response Duration</p>
                      <p className="font-medium">{getResponseDuration() || '-'}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Responded By</p>
                      <p className="font-medium">{tracking.responded_by_user_name || '-'}</p>
                    </div>
                    {tracking.average_response_time !== null && tracking.average_response_time !== undefined && (
                      <div>
                        <p className="text-sm text-muted-foreground">Average Response Time</p>
                        <p className="font-medium">
                          {formatDuration(tracking.average_response_time * 3600 * 1000)}
                        </p>
                      </div>
                    )}
                  </div>
                </CardContent>
              </CollapsibleContent>
            </Card>
          </Collapsible>

          {/* Collapsible: Resolution time */}
          <Collapsible open={resolutionOpen} onOpenChange={setResolutionOpen}>
            <Card>
              <CollapsibleTrigger asChild>
                <CardHeader className="cursor-pointer hover:bg-muted/50 transition-colors rounded-t-lg flex flex-row items-center justify-between space-y-0">
                  <CardTitle className="text-base">Resolution time</CardTitle>
                  <div className="flex items-center gap-2">
                    <span className={`text-sm font-medium ${getTimeRemainingResolution()?.includes('overdue') ? 'text-destructive' : ''}`}>
                      {tracking.is_resolved
                        ? (getResolutionDuration() ?? (tracking.resolution_duration != null ? formatDuration(tracking.resolution_duration * 3600 * 1000) : '-'))
                        : (getTimeRemainingResolution() ?? '-')}
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
                <CardContent className="pt-0 pb-6 px-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4">
                    <div>
                      <p className="text-sm text-muted-foreground">Is Resolved</p>
                      <p className="font-medium">
                        {tracking.is_resolved ? (
                          <Badge variant="success">Yes</Badge>
                        ) : (
                          <Badge variant="secondary">No</Badge>
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
                      <p className="text-sm text-muted-foreground">Resolution Duration</p>
                      <p className="font-medium">{getResolutionDuration() || '-'}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Resolved By</p>
                      <p className="font-medium">
                        {humanName(tracking.resolved_by_user_name, tracking.resolved_by) || '-'}
                      </p>
                    </div>
                    {tracking.average_resolution_time !== null && tracking.average_resolution_time !== undefined && (
                      <div>
                        <p className="text-sm text-muted-foreground">Average Resolution Time</p>
                        <p className="font-medium">
                          {formatDuration(tracking.average_resolution_time * 3600 * 1000)}
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

        {/* Tab 2: Event Log */}
        <TabsContent value="event-log">
          <EventLogTable
            trackingId={trackingId}
            agentCode={tracking.agent_code}
            teamSetCode={tracking.team_set_code}
          />
        </TabsContent>
      </Tabs>

      {respondInboxUrl && (
        <Sheet open={conversationSheetOpen} onOpenChange={setConversationSheetOpen}>
          {/* Fix round 4: `overflow-hidden`, not `-y-auto` - `SheetBody`
              below is the ONE scroll container. Same shape as the ticket
              drawer's Sheet (InterventionTicketDrawer.tsx), same fix -
              see its comment for why two scrollable ancestors around a
              flex-fill thread is the defect. */}
          <SheetContent side="right" className="flex w-full flex-col overflow-hidden sm:max-w-lg">
            <SheetHeader className="sr-only">
              <SheetTitle>Chat Records</SheetTitle>
            </SheetHeader>
            {/* R3: the thread flex-fills the sheet like the worklist drawer's
                SheetBody already does, instead of stopping at a fixed cap. */}
            <SheetBody className="flex min-h-0 flex-1 flex-col pt-2">
              <SlaTrackingChatRecords
                trackingId={trackingId}
                respondInboxUrl={respondInboxUrl}
                showAsPopup
              />
            </SheetBody>
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
