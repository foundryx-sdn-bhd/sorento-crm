'use client';

import { useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import AccessSummary from '@/components/chatbot-access/AccessSummary';
import HandoffAdvanced from '@/components/chatbot-access/HandoffAdvanced';
import AccessSwitches from '@/components/chatbot-access/AccessSwitches';
import SaveBar from '@/components/chatbot-access/SaveBar';
import {
  useChatbotRegistry,
  useChatbotRoles,
  useContactAccess,
  useSetContactAccess,
} from '@/hooks/useChatbotAccess';
import { tierLabel } from '@/lib/chatbot-access/labels';
import {
  buildRows,
  diffAccess,
  resetRow,
  resolveState,
  toggleKey,
  type GrantSet,
} from '@/lib/chatbot-access/tree';
import type {
  AccessOverride,
  ChatbotRole,
  ContactAccess,
  Registry,
} from '@/services/chatbotAccessService';
import { useHasPermission } from '@/hooks/usePermissions';
import { useContact } from '../components/contact-context';
import ContactMediaAccessSection from '../components/ContactMediaAccessSection';

function AccessEditor({
  contactId,
  access,
  roles,
  registry,
}: {
  contactId: string;
  access: ContactAccess;
  roles: ChatbotRole[];
  registry: Registry;
}) {
  const save = useSetContactAccess(contactId);
  const canGrant = useHasPermission('user_management.reference_data.manage');
  const initial = useMemo(
    () => ({ role_ids: access.roles.map((r) => r.id), overrides: access.overrides }),
    [access],
  );
  const [roleIds, setRoleIds] = useState<string[]>(initial.role_ids);
  const [overrides, setOverrides] = useState<AccessOverride[]>(initial.overrides);

  const held = roleIds
    .map((id) => roles.find((r) => r.id === id))
    .filter((r): r is ChatbotRole => !!r);
  const grants: GrantSet[] = held.map((r) => ({ domains: r.domains, fields: r.fields }));
  const groups = buildRows(registry, { roles: grants, overrides });
  const state = resolveState({ roles: grants, overrides });
  const seesAll = held.some((r) => r.sees_all_customers);
  const { count, payload } = diffAccess(initial, { role_ids: roleIds, overrides });
  // Details this contact gets on top of its roles: field add-overrides no held role ticks.
  const roleFields = new Set(grants.flatMap((g) => g.fields));
  const extras = Object.entries(
    overrides
      .filter((o) => o.granted && o.field_key && !roleFields.has(o.field_key))
      .reduce<Record<string, number>>((acc, o) => {
        acc[o.domain_name] = (acc[o.domain_name] ?? 0) + 1;
        return acc;
      }, {}),
  ).map(([domain, count]) => ({ domain, count }));
  const allCustomersFrom = held.filter((r) => r.sees_all_customers).map((r) => r.name);

  return (
    <div className="space-y-5">
      <AccessSummary
        mode="contact"
        registry={registry}
        effective={{
          domains: [...state.domains],
          attributes: [...state.fields],
          sees_all_customers: seesAll,
        }}
        customersLabel={seesAll ? 'all customers' : 'only their linked customers'}
        extras={extras}
      />

      <Card>
        <CardHeader>
          <CardTitle>Roles</CardTitle>
          {canGrant && (
          <div className="min-w-[200px]">
            <SearchableMultiSelect
              value={roleIds}
              onChange={setRoleIds}
              options={roles.map((r) => ({ value: r.id, label: r.name }))}
              placeholder="Add role..."
              renderTriggerLabel={() => 'Add role...'}
            />
          </div>
          )}
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {held.length === 0 && (
            <p className="text-sm text-muted-foreground">No role. This contact can ask nothing.</p>
          )}
          {held.map((r) => (
            <Badge key={r.id} variant="primary" appearance="light">
              {tierLabel(r.audience_tier) ? `${r.name} · ${tierLabel(r.audience_tier)}` : r.name}
              <button
                type="button"
                aria-label={`Remove role ${r.name}`}
                className="ml-1"
                onClick={() => setRoleIds((ids) => ids.filter((x) => x !== r.id))}
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Customers they can ask about</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          <span className="font-semibold">
            {seesAll ? 'All customers' : 'Only the customers linked to this contact'}
          </span>
          {seesAll && allCustomersFrom.length > 0 && (
            <span className="text-muted-foreground"> ({allCustomersFrom.join(', ')})</span>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>What they can ask</CardTitle>
        </CardHeader>
        <AccessSwitches
          groups={groups}
          mode="contact"
          canGrant={canGrant}
          onToggle={(key) => setOverrides((o) => toggleKey(o, key, registry, grants))}
          onToggleField={(key) => setOverrides((o) => toggleKey(o, key, registry, grants))}
          onResetRow={(key) => setOverrides((o) => resetRow(o, key, registry))}
        />
      </Card>

      <HandoffAdvanced registry={registry} domains={state.domains} />

      <SaveBar
        count={count}
        saving={save.isPending}
        onDiscard={() => {
          setRoleIds(initial.role_ids);
          setOverrides(initial.overrides);
        }}
        onSave={() => save.mutate(payload)}
      />
    </div>
  );
}

export default function ContactAccessPage() {
  const { isLoading, contactId } = useContact();
  const registry = useChatbotRegistry();
  const roles = useChatbotRoles();
  const access = useContactAccess(contactId);

  if (isLoading || registry.isLoading || roles.isLoading || access.isLoading) {
    return <Skeleton className="h-96 w-full" />;
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>Media Access</CardTitle>
        </CardHeader>
        <CardContent>
          <ContactMediaAccessSection contactId={contactId} />
        </CardContent>
      </Card>

      {registry.data && roles.data && access.data ? (
        <AccessEditor
          key={JSON.stringify([access.data.roles.map((r) => r.id), access.data.overrides])}
          contactId={contactId}
          access={access.data}
          roles={roles.data}
          registry={registry.data}
        />
      ) : (
        <p className="text-sm text-muted-foreground">The chatbot access could not be loaded.</p>
      )}
    </div>
  );
}
