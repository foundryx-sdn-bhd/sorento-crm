'use client';

import { useCallback, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { Plus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Skeleton } from '@/components/ui/skeleton';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import RecordNavigation from '@/components/common/RecordNavigation';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import AccessSummary from '@/components/chatbot-access/AccessSummary';
import AccessSwitches from '@/components/chatbot-access/AccessSwitches';
import SaveBar from '@/components/chatbot-access/SaveBar';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import {
  useAddRoleContacts,
  useChatbotRegistry,
  useChatbotRole,
  useChatbotRoleContacts,
  useChatbotRoles,
  useSetRoleGrants,
  useUpdateChatbotRole,
} from '@/hooks/useChatbotAccess';
import { TIER_OPTIONS } from '@/lib/chatbot-access/labels';
import { buildRows, diffGrants, toggleGrant, type GrantSet } from '@/lib/chatbot-access/tree';
import { getContacts } from '@/app/(protected)/user-management/contacts/[id]/services/contactService';
import type { AudienceTier, ChatbotRole, Registry } from '@/services/chatbotAccessService';

const LIST_PATH = '/user-management/chatbot-roles';

function RoleEditor({ role, registry }: { role: ChatbotRole; registry: Registry }) {
  const router = useRouter();
  const contacts = useChatbotRoleContacts(role.id);
  const setGrants = useSetRoleGrants(role.id);
  const update = useUpdateChatbotRole(role.id);
  const addContacts = useAddRoleContacts(role.id);
  const roles = useChatbotRoles();
  const [addOpen, setAddOpen] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);

  // Delete asks nothing (D7): the countdown takes the button's place.
  const deletion = useDeferredAction({
    actionKey: 'chatbot_role.delete',
    entityType: 'chatbot_role',
    entityId: role.id,
    verb: 'Deleting',
    subject: role.name,
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Role deleted',
    invalidateKeys: [['chatbot-access']],
    onCommitted: () => router.push(LIST_PATH),
  });
  const unlink = useDeferredRowAction({
    actionKey: 'chatbot_role_contact.unlink',
    entityType: 'chatbot_role_contact',
    verb: 'Removing',
    successMessage: 'Contact removed from the role',
    invalidateKeys: [['chatbot-access']],
  });

  const roleIds = (roles.data ?? []).map((r) => r.id);
  const position = roleIds.indexOf(role.id);

  const held = useMemo(() => contacts.data ?? [], [contacts.data]);
  const fetchContactOptions = useCallback(
    async (query: string) => {
      const heldIds = new Set(held.map((c) => c.id));
      const page = await getContacts(
        { pageIndex: 0, pageSize: 50, sorting: [{ id: 'name', desc: false }], searchQuery: query },
        {},
      );
      return page.data
        .filter((c) => !heldIds.has(c.id))
        .map((c) => ({
          value: c.id,
          label: c.name ? `${c.name} (${c.phone_number})` : c.phone_number,
        }));
    },
    [held],
  );
  const submitAdd = () => {
    if (picked.length === 0) return;
    addContacts.mutate(
      { contact_ids: picked },
      {
        onSuccess: () => {
          setAddOpen(false);
          setPicked([]);
        },
      },
    );
  };

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
      <Container>
        <PageHeader
          title={role.name}
          crumbs={[
            { title: 'User management' },
            { title: 'Chatbot Roles', path: '/user-management/chatbot-roles' },
            { title: role.name },
          ]}
          actions={
            <div className="flex items-center gap-2">
              {position >= 0 ? (
                <RecordNavigation
                  index={position + 1}
                  total={roleIds.length}
                  hasPrevious={position > 0}
                  hasNext={position < roleIds.length - 1}
                  onPrevious={() => router.push(`${LIST_PATH}/${roleIds[position - 1]}`)}
                  onNext={() => router.push(`${LIST_PATH}/${roleIds[position + 1]}`)}
                  ariaLabel="role"
                />
              ) : null}
              {deletion.countdown ?? (
                <Button
                  variant="outline"
                  disabled={deletion.isPending || deletion.isBlocked}
                  onClick={() => deletion.start()}
                >
                  Delete
                </Button>
              )}
            </div>
          }
        />
      </Container>
      <Container className="space-y-5">
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
          <CardContent>
            <RadioGroup
              value={seesAll ? 'all' : 'linked'}
              onValueChange={(v) => setSeesAll(v === 'all')}
            >
              <div className="flex items-center gap-2 text-sm">
                <RadioGroupItem value="all" id="customers-all" />
                <Label htmlFor="customers-all">All customers</Label>
              </div>
              <div className="flex items-center gap-2 text-sm">
                <RadioGroupItem value="linked" id="customers-linked" />
                <Label htmlFor="customers-linked">Only the customers linked to the contact</Label>
              </div>
            </RadioGroup>
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
            <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
              <Plus className="size-4 mr-1" />
              Add contacts
            </Button>
          </CardHeader>
          <CardContent>
            {held.length > 0 ? (
              <ul className="divide-y text-sm">
                {held.map((c) => (
                  <li key={c.id} className="flex items-center justify-between gap-2 py-2">
                    <Link
                      href={`/user-management/contacts/${c.id}/access`}
                      className="truncate hover:underline"
                      title={c.name ?? ''}
                    >
                      {c.name}
                    </Link>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Unlink ${c.name ?? 'contact'}`}
                      disabled={unlink.targetId === `${role.id}:${c.id}`}
                      onClick={() => unlink.run({ id: `${role.id}:${c.id}`, subject: c.name ?? 'Contact' })}
                    >
                      <X className="size-4" />
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="py-4 text-center">
                <p className="text-sm font-medium">No contacts have this role</p>
                <p className="text-sm text-muted-foreground">Add contacts to give them this role.</p>
              </div>
            )}
          </CardContent>
        </Card>

        <SaveBar
          count={count}
          saving={setGrants.isPending || update.isPending}
          onDiscard={discard}
          onSave={save}
        />
      </Container>

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add contacts</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <SearchableMultiSelect
              value={picked}
              onChange={setPicked}
              fetchOptions={fetchContactOptions}
              placeholder="Search contacts"
              emptyMessage="No contacts match."
            />
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submitAdd} disabled={picked.length === 0 || addContacts.isPending}>
              Add
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
      <Container>
        <Skeleton className="h-96 w-full" />
      </Container>
    );
  }
  if (!registry.data || !role.data) {
    return (
      <Container>
        <p className="text-sm text-muted-foreground">This role could not be loaded.</p>
      </Container>
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
