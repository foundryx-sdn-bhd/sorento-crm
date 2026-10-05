'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { PageHeader } from '@/components/common/PageHeader';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import AccessSummary from '@/components/chatbot-access/AccessSummary';
import AccessSwitches from '@/components/chatbot-access/AccessSwitches';
import SaveBar from '@/components/chatbot-access/SaveBar';
import {
  useChatbotRegistry,
  useChatbotRole,
  useChatbotRoleContacts,
  useDeleteChatbotRole,
  useSetRoleGrants,
  useUpdateChatbotRole,
} from '@/hooks/useChatbotAccess';
import { TIER_OPTIONS } from '@/lib/chatbot-access/labels';
import { buildRows, diffGrants, toggleGrant, type GrantSet } from '@/lib/chatbot-access/tree';
import type { AudienceTier, ChatbotRole, Registry } from '@/services/chatbotAccessService';

const WRAP = 'mx-auto w-full max-w-[1920px] px-4 lg:px-6';

function RoleEditor({ role, registry }: { role: ChatbotRole; registry: Registry }) {
  const router = useRouter();
  const contacts = useChatbotRoleContacts(role.id);
  const setGrants = useSetRoleGrants(role.id);
  const update = useUpdateChatbotRole(role.id);
  const remove = useDeleteChatbotRole();

  const saved: GrantSet = useMemo(
    () => ({ domains: role.domains, fields: role.fields }),
    [role.domains, role.fields],
  );
  const [grants, setLocalGrants] = useState<GrantSet>(saved);
  const [name, setName] = useState(role.name);
  const [tier, setTier] = useState<string>(role.audience_tier ?? '');
  const [seesAll, setSeesAll] = useState(role.sees_all_customers);

  const groups = buildRows(registry, { roles: [grants], overrides: [] });
  const grantChanges = diffGrants(saved, grants);
  const metaChanges =
    Number(name.trim() !== role.name) +
    Number(tier !== (role.audience_tier ?? '')) +
    Number(seesAll !== role.sees_all_customers);
  const count = grantChanges + metaChanges;

  const save = () => {
    if (metaChanges > 0) {
      update.mutate({
        name: name.trim(),
        sees_all_customers: seesAll,
        ...(tier ? { audience_tier: tier as AudienceTier } : { clear_audience_tier: true }),
      });
    }
    if (grantChanges > 0) setGrants.mutate({ domains: grants.domains, fields: grants.fields });
  };
  const discard = () => {
    setLocalGrants(saved);
    setName(role.name);
    setTier(role.audience_tier ?? '');
    setSeesAll(role.sees_all_customers);
  };

  return (
    <>
      <div className={WRAP}>
        <PageHeader
          title={role.name}
          crumbs={[
            { title: 'User management' },
            { title: 'Chatbot Roles', path: '/user-management/chatbot-roles' },
            { title: role.name },
          ]}
          actions={
            <Button
              variant="outline"
              disabled={remove.isPending}
              onClick={() =>
                remove.mutate(role.id, {
                  onSuccess: () => router.push('/user-management/chatbot-roles'),
                })
              }
            >
              Delete
            </Button>
          }
        />
      </div>
      <div className={`${WRAP} space-y-5`}>
        <div className="text-sm text-muted-foreground">
          {role.contact_count} contact{role.contact_count === 1 ? '' : 's'}
        </div>
        <AccessSummary
          mode="role"
          registry={registry}
          effective={{
            domains: grants.domains,
            attributes: grants.fields,
            sees_all_customers: seesAll,
          }}
          customersLabel={seesAll ? 'all customers' : 'only the customers linked to the contact'}
        />

        <Card>
          <CardHeader>
            <CardTitle>Name and tier</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="role-name">Name</Label>
              <Input id="role-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>Tier</Label>
              <SearchableSelect value={tier} onChange={setTier} options={TIER_OPTIONS} clearable />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Customers they can ask about</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="customers"
                aria-label="All customers"
                checked={seesAll}
                onChange={() => setSeesAll(true)}
              />
              All customers
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="customers"
                aria-label="Only the customers linked to the contact"
                checked={!seesAll}
                onChange={() => setSeesAll(false)}
              />
              Only the customers linked to the contact
            </label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>What they can ask</CardTitle>
          </CardHeader>
          <AccessSwitches
            groups={groups}
            mode="role"
            onToggle={(key) => setLocalGrants((g) => toggleGrant(g, key, registry))}
            onToggleField={(key) => setLocalGrants((g) => toggleGrant(g, key, registry))}
            onResetRow={() => undefined}
          />
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Contacts with this role</CardTitle>
          </CardHeader>
          <CardContent>
            {contacts.data && contacts.data.length > 0 ? (
              <ul className="divide-y text-sm">
                {contacts.data.map((c) => (
                  <li key={c.id} className="py-2">
                    <Link
                      href={`/user-management/contacts/${c.id}/access`}
                      className="hover:underline"
                    >
                      {c.name}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No contacts have this role.</p>
            )}
          </CardContent>
        </Card>

        <SaveBar
          count={count}
          saving={setGrants.isPending || update.isPending}
          onDiscard={discard}
          onSave={save}
        />
      </div>
    </>
  );
}

export default function ChatbotRolePage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? '';
  const registry = useChatbotRegistry();
  const role = useChatbotRole(id);

  if (registry.isLoading || role.isLoading) {
    return (
      <div className={WRAP}>
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }
  if (!registry.data || !role.data) {
    return (
      <div className={WRAP}>
        <p className="text-sm text-muted-foreground">This role could not be loaded.</p>
      </div>
    );
  }
  const r = role.data;
  const signature = [
    r.id,
    r.name,
    r.audience_tier ?? '',
    r.sees_all_customers,
    [...r.domains].sort().join(','),
    [...r.fields].sort().join(','),
  ].join('|');
  return <RoleEditor key={signature} role={r} registry={registry.data} />;
}
