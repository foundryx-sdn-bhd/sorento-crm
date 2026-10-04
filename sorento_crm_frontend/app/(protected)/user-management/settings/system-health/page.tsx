'use client';

import { useMemo, useState } from 'react';
import { RiCheckboxCircleFill, RiErrorWarningFill } from '@remixicon/react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoaderCircleIcon, UserPlus } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { getUsersSelect } from '@/services/userSelectService';
import { extractApiError } from '@/lib/api-client';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { Switch } from '@/components/ui/switch';
import { useSettings } from '../components/settings-context';

/** Clamp a free-text number field to a positive integer (>= 1).
 *  These settings are durations and counts - 0 is not a meaningful value and
 *  would either disable the check silently or divide the window to nothing. */
function toPositiveInt(raw: string, fallback: number): number {
  const n = parseInt(raw, 10);
  if (Number.isNaN(n) || n < 1) return fallback;
  return n;
}

/** Clamp a free-text number field to a non-negative integer. */
function toNonNegativeInt(raw: string, fallback: number): number {
  const n = parseInt(raw, 10);
  if (Number.isNaN(n) || n < 0) return fallback;
  return n;
}

const SystemHealthSettingsPage = () => {
  const queryClient = useQueryClient();
  const { settings, roles } = useSettings();

  const initialRoleIds = useMemo(
    () => settings.healthNotifyRoleIds ?? [],
    [settings.healthNotifyRoleIds],
  );
  const initialUserIds = useMemo(
    () => settings.healthNotifyUserIds ?? [],
    [settings.healthNotifyUserIds],
  );

  const {
    data: users,
    error: usersError,
    refetch: refetchUsers,
  } = useQuery({
    queryKey: ['health-notify-user-select'],
    queryFn: () => getUsersSelect(),
    staleTime: 5 * 60 * 1000,
  });

  const [digestEnabled, setDigestEnabled] = useState<boolean>(settings.healthDigestEnabled);
  const [alertsEnabled, setAlertsEnabled] = useState<boolean>(settings.healthAlertsEnabled);
  const [roleIds, setRoleIds] = useState<string[]>([...initialRoleIds]);
  const [userIds, setUserIds] = useState<string[]>([...initialUserIds]);
  const [failThreshold, setFailThreshold] = useState<string>(
    String(settings.healthIntegrationFailThreshold ?? 10),
  );
  const [auditFloor, setAuditFloor] = useState<string>(
    String(settings.healthAuditVolumeFloor ?? 0),
  );
  // WhatsApp round-trip SLA. These drive the chat_latency_watchdog task; until
  // now they were only reachable by editing system_settings directly.
  const [latencyTarget, setLatencyTarget] = useState<string>(
    String(settings.chatLatencyTargetSeconds ?? 10),
  );
  const [latencyPercentile, setLatencyPercentile] = useState<string>(
    String(settings.chatLatencyPercentile ?? 99),
  );
  const [latencyMultiplier, setLatencyMultiplier] = useState<string>(
    String(settings.chatLatencyCeilingMultiplier ?? 3),
  );
  const [latencyNoReply, setLatencyNoReply] = useState<string>(
    String(settings.chatLatencyNoReplyMinutes ?? 5),
  );
  const [latencyMinSample, setLatencyMinSample] = useState<string>(
    String(settings.chatLatencyMinSample ?? 30),
  );



  const mutation = useMutation({
    mutationFn: async () => {
      // apiFetch maps /api/... straight to the FastAPI backend; POST /general
      // setattrs any provided snake_case column.
      const response = await apiFetch('/api/user-management/settings/general', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          health_digest_enabled: digestEnabled,
          health_alerts_enabled: alertsEnabled,
          health_notify_role_ids: roleIds,
          health_notify_user_ids: userIds,
          health_integration_fail_threshold: toNonNegativeInt(
            failThreshold,
            settings.healthIntegrationFailThreshold ?? 10,
          ),
          health_audit_volume_floor: toNonNegativeInt(
            auditFloor,
            settings.healthAuditVolumeFloor ?? 0,
          ),
          chat_latency_p99_target_seconds: toPositiveInt(
            latencyTarget,
            settings.chatLatencyTargetSeconds ?? 10,
          ),
          chat_latency_percentile: Number(latencyPercentile),
          chat_latency_ceiling_multiplier: toPositiveInt(
            latencyMultiplier,
            settings.chatLatencyCeilingMultiplier ?? 3,
          ),
          chat_latency_no_reply_minutes: toPositiveInt(
            latencyNoReply,
            settings.chatLatencyNoReplyMinutes ?? 5,
          ),
          chat_latency_min_sample: toPositiveInt(
            latencyMinSample,
            settings.chatLatencyMinSample ?? 30,
          ),
        }),
      });
      if (!response.ok) {
        throw new Error(await extractApiError(response, 'Failed to save'));
      }
      return response.json();
    },
    onSuccess: () => {
      toast('success', 'System health settings updated');
      queryClient.invalidateQueries({ queryKey: ['system-settings'] });
    },
    onError: (error: Error) => toast('destructive', error.message),
  });

  const isProcessing = mutation.status === 'pending';

  const handleReset = () => {
    setDigestEnabled(settings.healthDigestEnabled);
    setAlertsEnabled(settings.healthAlertsEnabled);
    setRoleIds([...initialRoleIds]);
    setUserIds([...initialUserIds]);
    setFailThreshold(String(settings.healthIntegrationFailThreshold ?? 10));
    setLatencyTarget(String(settings.chatLatencyTargetSeconds ?? 10));
    setLatencyPercentile(String(settings.chatLatencyPercentile ?? 99));
    setLatencyMultiplier(String(settings.chatLatencyCeilingMultiplier ?? 3));
    setLatencyNoReply(String(settings.chatLatencyNoReplyMinutes ?? 5));
    setLatencyMinSample(String(settings.chatLatencyMinSample ?? 30));
    setAuditFloor(String(settings.healthAuditVolumeFloor ?? 0));
  };

  const noRecipients =
    (digestEnabled || alertsEnabled) && roleIds.length === 0 && userIds.length === 0;

  return (
    <Card>
      <CardHeader className="border-b border-border">
        <CardTitle>System Health Settings</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6 py-5">
        {/* Digest toggle */}
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="text-md font-semibold">Daily health digest</div>
            <div className="text-muted-foreground text-2sm">
              Send a once-daily in-app and email summary of email queue, imports,
              scheduled tasks, integrations and audit activity to the notify roles.
            </div>
          </div>
          <Switch
            data-testid="health-digest-enabled"
            checked={digestEnabled}
            onCheckedChange={setDigestEnabled}
          />
        </div>

        {/* Alerts toggle */}
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="text-md font-semibold">Immediate anomaly alerts</div>
            <div className="text-muted-foreground text-2sm">
              Alert off-cycle the moment a watchdog condition trips (n8n liveness
              failure, a scheduled task failing or running overdue, or an
              integration failure spike).
            </div>
          </div>
          <Switch
            data-testid="health-alerts-enabled"
            checked={alertsEnabled}
            onCheckedChange={setAlertsEnabled}
          />
        </div>

        {/* Notify roles */}
        <div className="space-y-2">
          <div className="space-y-1">
            <div className="text-md font-semibold">Notify roles</div>
            <div className="text-muted-foreground text-2sm">
              Members of these roles receive the digest and alerts.
            </div>
          </div>
          <div className="flex items-center gap-3">
            <SearchableMultiSelect
              value={roleIds}
              onChange={setRoleIds}
              emptyMessage="No roles found."
              className="w-[220px]"
              // Selections are shown as badges beside this control, so the trigger stays the
              // compact icon button it has always been rather than a select box.
              renderTrigger={() => (
                <Button
                  variant="outline"
                  mode="icon"
                  className="h-7! w-7!"
                  data-testid="notify-roles-trigger"
                  aria-label="Add notify roles"
                >
                  <UserPlus className="size-3.5!" />
                </Button>
              )}
              options={(roles ?? []).map((role) => ({ value: role.id, label: role.name }))}
            />
            <div className="flex flex-wrap items-center gap-2">
              {roleIds.length > 0 ? (
                roleIds.map((roleId) => {
                  const role = roles.find((r) => r.id === roleId);
                  return (
                    <Badge key={roleId} variant="secondary">
                      {role?.name ?? 'Unknown role'}
                    </Badge>
                  );
                })
              ) : (
                <span className="text-muted-foreground text-sm">Not set</span>
              )}
            </div>
          </div>
        </div>

        {/* Notify individual users */}
        <div className="space-y-2">
          <div className="space-y-1">
            <div className="text-md font-semibold">Notify users</div>
            <div className="text-muted-foreground text-2sm">
              Individual users who receive the digest and alerts, in addition to
              members of the notify roles above.
            </div>
          </div>
          <div className="flex items-center gap-3">
            <SearchableMultiSelect
              value={userIds}
              onChange={setUserIds}
              emptyMessage="No users found."
              loadError={usersError}
              onRetry={() => void refetchUsers()}
              className="w-[260px]"
              renderTrigger={() => (
                <Button
                  variant="outline"
                  mode="icon"
                  className="h-7! w-7!"
                  data-testid="notify-users-trigger"
                  aria-label="Add notify users"
                >
                  <UserPlus className="size-3.5!" />
                </Button>
              )}
              options={(users ?? []).map((user) => ({
                value: user.id,
                label: user.name || user.email || 'Unnamed user',
                searchText: `${user.name ?? ''} ${user.email ?? ''}`,
              }))}
            />
            <div className="flex flex-wrap items-center gap-2">
              {userIds.length > 0 ? (
                userIds.map((userId) => {
                  const user = users?.find((u) => u.id === userId);
                  return (
                    <Badge key={userId} variant="secondary">
                      {user ? user.name || user.email : 'Selected user'}
                    </Badge>
                  );
                })
              ) : (
                <span className="text-muted-foreground text-sm">Not set</span>
              )}
            </div>
          </div>
        </div>

        {noRecipients && (
          <Alert variant="mono" icon="warning">
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>
              No roles or users selected - the digest and alerts fall back to superadmin and admin users.
            </AlertTitle>
          </Alert>
        )}

        {/* Thresholds */}
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="health-fail-threshold">Integration failure alert threshold</Label>
            <Input
              id="health-fail-threshold"
              data-testid="health-integration-fail-threshold"
              type="number"
              min={0}
              value={failThreshold}
              onChange={(e) => setFailThreshold(e.target.value)}
            />
            <div className="text-muted-foreground text-2sm">
              Failed integration logs on a single channel within the window that
              trip an immediate alert.
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="health-audit-floor">Daily audit volume floor</Label>
            <Input
              id="health-audit-floor"
              data-testid="health-audit-volume-floor"
              type="number"
              min={0}
              value={auditFloor}
              onChange={(e) => setAuditFloor(e.target.value)}
            />
            <div className="text-muted-foreground text-2sm">
              Warn in the daily digest when audit events fall below this count. Set
              to 0 to disable the low-volume check.
            </div>
          </div>
        </div>

        {/* WhatsApp round-trip latency SLA */}
        <div className="space-y-4 border-t pt-6">
          <div>
            <h3 className="text-sm font-medium">WhatsApp round-trip latency</h3>
            <p className="text-muted-foreground text-2sm mt-1">
              Measures the user pressing send to our reply being accepted by Respond,
              on Respond&apos;s clock. Three independent triggers: the percentile below,
              a per-turn hard ceiling, and turns that never get a reply at all - a
              percentile alone cannot see a turn that never completes.
            </p>
          </div>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="chat-latency-percentile">Alert on percentile</Label>
              <SearchableSelect
                id="chat-latency-percentile"
                value={latencyPercentile}
                onChange={setLatencyPercentile}
                options={[
                  { value: '50', label: 'p50 (median)' },
                  { value: '95', label: 'p95' },
                  { value: '99', label: 'p99' },
                ]}
              />
              <div className="text-muted-foreground text-2sm">
                p50, p95 and p99 are always computed and shown; this picks which one
                is held to the target.
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="chat-latency-target">Target (seconds)</Label>
              <Input
                id="chat-latency-target"
                data-testid="chat-latency-target"
                type="number"
                min={1}
                value={latencyTarget}
                onChange={(e) => setLatencyTarget(e.target.value)}
              />
              <div className="text-muted-foreground text-2sm">
                The SLA the chosen percentile must stay under.
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="chat-latency-multiplier">Hard ceiling multiplier</Label>
              <Input
                id="chat-latency-multiplier"
                data-testid="chat-latency-multiplier"
                type="number"
                min={1}
                value={latencyMultiplier}
                onChange={(e) => setLatencyMultiplier(e.target.value)}
              />
              <div className="text-muted-foreground text-2sm">
                Any single turn slower than target x this alerts immediately, with no
                minimum sample.
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="chat-latency-no-reply">No-reply alert (minutes)</Label>
              <Input
                id="chat-latency-no-reply"
                data-testid="chat-latency-no-reply"
                type="number"
                min={1}
                value={latencyNoReply}
                onChange={(e) => setLatencyNoReply(e.target.value)}
              />
              <div className="text-muted-foreground text-2sm">
                An incoming message with no reply after this long is reported.
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="chat-latency-min-sample">Minimum sample</Label>
              <Input
                id="chat-latency-min-sample"
                data-testid="chat-latency-min-sample"
                type="number"
                min={1}
                value={latencyMinSample}
                onChange={(e) => setLatencyMinSample(e.target.value)}
              />
              <div className="text-muted-foreground text-2sm">
                Below this many turns in the hour, no percentile is claimed - a p99
                over six turns is just the slowest of six.
              </div>
            </div>
          </div>

          <Alert appearance="light">
            <AlertTitle data-testid="chat-latency-summary">
              Alerts when p{latencyPercentile} exceeds {latencyTarget || '?'}s over at least{' '}
              {latencyMinSample || '?'} turns in the last hour, when any single turn exceeds{' '}
              {Number(latencyTarget) * Number(latencyMultiplier) || '?'}s, or when a message
              goes {latencyNoReply || '?'} minutes without a reply.
            </AlertTitle>
          </Alert>
        </div>
      </CardContent>
      <CardFooter className="flex justify-end gap-4 py-5 px-10">
        <Button type="button" variant="outline" onClick={handleReset}>
          Reset
        </Button>
        <Button type="button" onClick={() => mutation.mutate()} disabled={isProcessing}>
          {isProcessing && <LoaderCircleIcon className="animate-spin" />}
          Save Settings
        </Button>
      </CardFooter>
    </Card>
  );
};

function toast(icon: 'success' | 'destructive', title: string) {
  // Lazy import to keep parity with the rest of the settings pages.
  import('@/lib/toast').then(({ toast: sonner }) =>
    sonner.custom(
      () => (
        <Alert variant="mono" icon={icon}>
          <AlertIcon>
            {icon === 'success' ? <RiCheckboxCircleFill /> : <RiErrorWarningFill />}
          </AlertIcon>
          <AlertTitle>{title}</AlertTitle>
        </Alert>
      ),
    ),
  );
}

export default SystemHealthSettingsPage;
