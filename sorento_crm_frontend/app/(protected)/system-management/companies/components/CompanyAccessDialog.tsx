'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Users, Contact, Trash2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { getUsersSelect } from '@/services/userSelectService';
import {
  useAddCompanyContact,
  useAddCompanyUser,
  useAvailableContacts,
  useCompanyContacts,
  useCompanyUsers,
  useRemoveCompanyContact,
  useRemoveCompanyUser,
} from '../hooks/useCompanies';
import type { Company, CompanyUser } from '../types/company.types';

interface CompanyAccessDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  company: Company;
}

export default function CompanyAccessDialog({
  open,
  onOpenChange,
  company,
}: CompanyAccessDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Manage access - {company.name}</DialogTitle>
          <DialogDescription>
            Grant users the ability to switch into this company, and tag the contacts that belong
            to it.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="users" className="w-full">
          <TabsList variant="default">
            <TabsTrigger value="users">
              <Users className="size-4" />
              Users
            </TabsTrigger>
            <TabsTrigger value="contacts">
              <Contact className="size-4" />
              Contacts
            </TabsTrigger>
          </TabsList>

          <TabsContent value="users" className="mt-4">
            {open && <UsersTab companyId={company.id} />}
          </TabsContent>

          <TabsContent value="contacts" className="mt-4">
            {open && <ContactsTab companyId={company.id} />}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

// ── Confirm-before-unlink ─────────────────────────────────────────────────────
// Removing access is a destructive/detach action - gate it behind a confirm per
// the project rule (confirm before delete OR unlink), never one-click.

function ConfirmRemoveButton({
  onConfirm,
  disabled,
  title,
  description,
}: {
  onConfirm: () => void;
  disabled?: boolean;
  title: string;
  description: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button mode="icon" variant="ghost" size="sm" title="Remove" disabled={disabled} aria-label="Remove">
          <Trash2 className="size-4" />
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={disabled}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            disabled={disabled}
            onClick={(e) => {
              e.preventDefault();
              setOpen(false);
              onConfirm();
            }}
          >
            Remove
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// ── Users tab ─────────────────────────────────────────────────────────────────

function UsersTab({ companyId }: { companyId: string }) {
  const { data: assigned = [], isLoading } = useCompanyUsers(companyId);
  const addUser = useAddCompanyUser(companyId);
  const removeUser = useRemoveCompanyUser(companyId);
  const [selected, setSelected] = useState('');

  // Full user catalogue for the picker (shared user-select service).
  const {
    data: allUsers = [],
    error: allUsersError,
    refetch: refetchAllUsers,
  } = useQuery({
    queryKey: ['users-select', 'company-access'],
    queryFn: () => getUsersSelect(),
  });

  const assignedIds = useMemo(() => new Set(assigned.map((u) => u.id)), [assigned]);

  const options = useMemo(
    () =>
      allUsers
        .filter((u) => !assignedIds.has(u.id))
        .map((u) => ({
          value: u.id,
          label: u.name || u.email || 'Unnamed user',
          description: u.email ?? undefined,
        })),
    [allUsers, assignedIds],
  );

  const handleAdd = (userId: string) => {
    const user = allUsers.find((u) => u.id === userId);
    if (!user) return;
    const payload: CompanyUser = { id: user.id, name: user.name, email: user.email ?? '' };
    addUser.mutate(payload, { onSuccess: () => setSelected('') });
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="text-sm font-medium">Add user</label>
        <SearchableSelect
          value={selected}
          onChange={handleAdd}
          options={options}
          loadError={allUsersError}
          onRetry={() => void refetchAllUsers()}
          placeholder="Select a user to grant access…"
          emptyMessage="No users available"
          triggerClassName="mt-1"
        />
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium">
          Assigned users
          <span className="text-muted-foreground font-normal"> ({assigned.length})</span>
        </p>
        {isLoading ? (
          <SectionSkeleton rows={3} className="py-2" />
        ) : assigned.length === 0 ? (
          <div className="rounded-lg border border-dashed p-6 text-center">
            <p className="text-sm text-muted-foreground">No users can switch into this company yet.</p>
            <p className="text-xs text-muted-foreground mt-1">
              Use the picker above to grant a user access.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border rounded-lg border">
            {assigned.map((u) => (
              <li key={u.id} className="flex items-center justify-between gap-3 px-3 py-2">
                <div className="min-w-0">
                  <span className="text-sm font-medium truncate block">{u.name || u.email}</span>
                  <span className="text-xs text-muted-foreground truncate block">{u.email}</span>
                </div>
                <ConfirmRemoveButton
                  disabled={removeUser.isPending}
                  onConfirm={() => removeUser.mutate(u.id)}
                  title="Remove access?"
                  description={`This removes ${u.name || u.email}'s access to switch into this company. You can grant it again later.`}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ── Contacts tab ──────────────────────────────────────────────────────────────

function ContactsTab({ companyId }: { companyId: string }) {
  const { data: assigned = [], isLoading } = useCompanyContacts(companyId);
  const { data: available = [] } = useAvailableContacts(companyId);
  const addContact = useAddCompanyContact(companyId);
  const removeContact = useRemoveCompanyContact(companyId);
  const [selected, setSelected] = useState('');

  const options = useMemo(
    () =>
      available.map((c) => ({
        value: c.id,
        label: c.name,
        description: c.phone,
      })),
    [available],
  );

  const handleAdd = (contactId: string) => {
    addContact.mutate(contactId, { onSuccess: () => setSelected('') });
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="text-sm font-medium">Add contact</label>
        <SearchableSelect
          value={selected}
          onChange={handleAdd}
          options={options}
          placeholder="Select a contact to tag…"
          emptyMessage="No contacts available"
          triggerClassName="mt-1"
        />
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium">
          Tagged contacts
          <span className="text-muted-foreground font-normal"> ({assigned.length})</span>
        </p>
        {isLoading ? (
          <SectionSkeleton rows={3} className="py-2" />
        ) : assigned.length === 0 ? (
          <div className="rounded-lg border border-dashed p-6 text-center">
            <p className="text-sm text-muted-foreground">No contacts belong to this company yet.</p>
            <p className="text-xs text-muted-foreground mt-1">
              Use the picker above to tag a contact.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border rounded-lg border">
            {assigned.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 px-3 py-2">
                <div className="min-w-0 flex items-center gap-2">
                  <span className="text-sm font-medium truncate">{c.name}</span>
                  <Badge variant="secondary" size="sm" className="shrink-0 font-mono">
                    {c.phone}
                  </Badge>
                </div>
                <ConfirmRemoveButton
                  disabled={removeContact.isPending}
                  onConfirm={() => removeContact.mutate(c.id)}
                  title="Remove access?"
                  description={`This removes ${c.name}'s access to this company. You can tag the contact again later.`}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
