'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { getUsersSelect } from '@/services/userSelectService';

import { useCreateIntegration, useUpdateIntegration } from '../hooks/useIntegrations';
import type { Integration } from '../types/integration.types';

const TYPE_OPTIONS = [
  { value: 'autocount_esb', label: 'AutoCount ESB' },
  { value: 'automation', label: 'Automation (n8n)' },
  { value: 'mcp', label: 'MCP server' },
  { value: 'legacy', label: 'Legacy shared key' },
  { value: 'other', label: 'Other' },
];

export function IntegrationFormDialog({
  open,
  onOpenChange,
  integration,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  integration?: Integration | null;
}) {
  const isEdit = !!integration;
  const create = useCreateIntegration();
  const update = useUpdateIntegration();

  const [name, setName] = useState('');
  const [type, setType] = useState('autocount_esb');
  const [actAsUserId, setActAsUserId] = useState<string | null>(null);
  const [isActive, setIsActive] = useState(true);
  // Outbound half of the integration: where Sorento pushes to, and the
  // credential it presents. Required for Group F (document lifecycle events
  // and PO/SQ/SO writes) -- without a base URL there is nowhere to push.
  const [baseUrl, setBaseUrl] = useState('');
  const [outboundKey, setOutboundKey] = useState('');
  // Abuse ceiling, not a quota. Blank uses the platform default.
  const [rateLimit, setRateLimit] = useState('');

  // This SearchableSelect is static-options only, so the roster is fetched
  // once and filtered client-side rather than searched server-side.
  const {
    data: users,
    isLoading: usersLoading,
    error: usersError,
    refetch: refetchUsers,
  } = useQuery({
    queryKey: ['users-select', 'integration-principal'],
    queryFn: () => getUsersSelect({ status: 'ACTIVE' }),
    enabled: open,
  });
  const userOptions = useMemo(
    () =>
      (users ?? []).map((u) => ({
        value: u.id,
        label: u.name || u.email || 'Unnamed user',
        description: u.name ? (u.email ?? undefined) : undefined,
        searchText: `${u.name ?? ''} ${u.email ?? ''}`,
      })),
    [users],
  );

  useEffect(() => {
    if (!open) return;
    setName(integration?.name ?? '');
    setType(integration?.type ?? 'autocount_esb');
    setActAsUserId(integration?.act_as_user_id ?? null);
    setIsActive(integration?.is_active ?? true);
    setBaseUrl(((integration?.config_json ?? {}) as Record<string, unknown>).base_url as string ?? '');
    // Never prefilled: the server does not return it, and a blank field on
    // save means "keep existing" rather than "clear".
    setOutboundKey('');
    setRateLimit(
      String(
        ((integration?.config_json ?? {}) as Record<string, unknown>)
          .rate_limit_per_minute ?? '',
      ),
    );
  }, [open, integration]);

  const submit = async () => {
    if (isEdit && integration) {
      // credentials_json is deliberately not sent: omitting it means "keep the
      // existing credential". Sending an empty object here would silently wipe
      // it, which reads to an operator as an outage rather than an edit.
      await update.mutateAsync({
        id: integration.id,
        payload: {
          name,
          type,
          act_as_user_id: actAsUserId,
          is_active: isActive,
          config_json: {
            ...(integration.config_json ?? {}),
            base_url: baseUrl || undefined,
            rate_limit_per_minute: rateLimit === '' ? undefined : Number(rateLimit),
          },
          // Omitted when blank so the stored credential is kept. Sending an
          // empty object would clear it, which reads as an outage, not an edit.
          ...(outboundKey ? { credentials_json: { api_key: outboundKey } } : {}),
        },
      });
    } else {
      await create.mutateAsync({
        name,
        type,
        act_as_user_id: actAsUserId,
        is_active: isActive,
        ...(baseUrl || rateLimit
          ? {
              config_json: {
                ...(baseUrl ? { base_url: baseUrl } : {}),
                ...(rateLimit ? { rate_limit_per_minute: Number(rateLimit) } : {}),
              },
            }
          : {}),
        ...(outboundKey ? { credentials_json: { api_key: outboundKey } } : {}),
      });
    }
    onOpenChange(false);
  };

  const busy = create.isPending || update.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* max-h + overflow so the submit button stays reachable at phone width */}
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit integration' : 'Add integration'}</DialogTitle>
          <DialogDescription>
            An integration acts as a user, so what it can reach is controlled by that
            user&apos;s role.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="integration-name">Name</Label>
            <Input
              id="integration-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="foundryx-esb"
            />
          </div>

          <div className="space-y-2">
            <Label>Type</Label>
            <SearchableSelect
              options={TYPE_OPTIONS}
              value={type}
              onChange={setType}
              placeholder="Select a type"
            />
          </div>

          <div className="space-y-2">
            <Label>Acts as user</Label>
            <SearchableSelect
              options={userOptions}
              loadError={usersError}
              onRetry={() => void refetchUsers()}
              value={actAsUserId ?? ''}
              onChange={(v) => setActAsUserId(v || null)}
              placeholder={usersLoading ? 'Loading users…' : 'Select the principal'}
              disabled={usersLoading}
            />
            <p className="text-xs text-muted-foreground">
              Every record this integration writes is attributed to this user, and its
              role decides what the integration may reach.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="integration-base-url">Outbound base URL</Label>
            <Input
              id="integration-base-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://esb.foundryx.my"
            />
            <p className="text-xs text-muted-foreground">
              Where Sorento pushes documents and lifecycle events. Leave blank for
              systems that only call in, such as n8n.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="integration-outbound-key">Outbound credential</Label>
            <Input
              id="integration-outbound-key"
              type="password"
              value={outboundKey}
              onChange={(e) => setOutboundKey(e.target.value)}
              placeholder={
                integration?.has_credentials ? 'Stored - leave blank to keep' : 'API key for the target system'
              }
            />
            <p className="text-xs text-muted-foreground">
              The key Sorento presents when calling out. Encrypted at rest and never
              shown again. Leave blank to keep the existing one.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="integration-rate-limit">Rate limit (requests/minute)</Label>
            <Input
              id="integration-rate-limit"
              type="number"
              min={0}
              value={rateLimit}
              onChange={(e) => setRateLimit(e.target.value)}
              placeholder="Default (600)"
            />
            <p className="text-xs text-muted-foreground">
              Per-integration abuse ceiling - one noisy caller cannot throttle the
              others. Blank uses the default; 0 disables limiting.
            </p>
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <Label htmlFor="integration-active">Active</Label>
              <p className="text-xs text-muted-foreground">
                Inactive integrations are refused even with a valid key.
              </p>
            </div>
            <Switch
              id="integration-active"
              checked={isActive}
              onCheckedChange={setIsActive}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !name.trim()}>
            {isEdit ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
