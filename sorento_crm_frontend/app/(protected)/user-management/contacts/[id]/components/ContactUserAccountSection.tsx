'use client';

/**
 * "User account" (S3 2.4, AC-53): a Card directly under Contact Information,
 * always rendered with an explicit empty state - "No user yet" with Create
 * user / Link existing user, or the linked user's name, roles and sign-in
 * methods with Unlink as a 5-second deferred action (D7, no dialog).
 *
 * Without `users.view` the section is not rendered at all (fix round 2, S5):
 * the API then sends no linked user, so "No user yet" would be a false
 * statement for a contact that has one. The list hides its User column the
 * same way.
 */

import { useState } from 'react';
import Link from 'next/link';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardHeading, CardTitle } from '@/components/ui/card';
import DeferredActionButton from '@/components/common/DeferredActionButton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { toast } from '@/lib/toast';
import type { CodedError } from '@/lib/api-client';
import type { RespondContact } from '../../types/contact.types';
import { getUsersSelect } from '@/services/userSelectService';
import { updateUserContactLink } from '../../../users/services/userService';
import UserAddDialog from '../../../users/components/user-add-dialog';

type LinkedUser = NonNullable<RespondContact['linked_user']>;

function signInMethods(user: LinkedUser): string[] {
  const methods: string[] = [];
  if (user.has_password) methods.push('Email and password');
  // The code goes to the USER's phone, so it is a way in only while that phone
  // is the contact's (fix round 2, S3). Unknown reads as "differs".
  if (user.phone_differs_from_contact === false) methods.push('WhatsApp code');
  methods.push('Portal link');
  return methods;
}

export default function ContactUserAccountSection({ contact }: { contact: RespondContact }) {
  const queryClient = useQueryClient();
  const canViewUsers = useHasPermission('user_management.users.view');
  const canAddUsers = useHasPermission('user_management.users.add');
  const canEditUsers = useHasPermission('user_management.users.edit');

  const [createUserOpen, setCreateUserOpen] = useState(false);
  const [linkPickerOpen, setLinkPickerOpen] = useState(false);
  const [pickedUserId, setPickedUserId] = useState('');
  const [linking, setLinking] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);

  const {
    data: unlinkedUsers,
    error: unlinkedUsersError,
    refetch: refetchUnlinkedUsers,
  } = useQuery({
    queryKey: ['unlinked-users'],
    queryFn: () => getUsersSelect({ status: 'ACTIVE', unlinked: true }),
    enabled: linkPickerOpen,
    staleTime: 1000 * 60,
  });

  const linkedUser = contact.linked_user ?? null;

  // Same registered action a user's own Sign-in section runs (S3 1.4), just
  // pointed at the user from the OTHER side of the link.
  const unlink = useDeferredAction({
    actionKey: 'user.unlink_contact',
    entityType: 'user',
    entityId: linkedUser?.id,
    verb: 'Unlinking',
    subject: linkedUser?.name || '',
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'WhatsApp contact unlinked',
    invalidateKeys: [
      ['respond-contact', contact.id],
      ['respond-contacts'],
      ['user-user', linkedUser?.id],
    ],
  });

  const handleLink = async () => {
    if (!pickedUserId) return;
    setLinking(true);
    setLinkError(null);
    try {
      await updateUserContactLink(pickedUserId, contact.id);
      toast.success('User linked');
      queryClient.invalidateQueries({ queryKey: ['respond-contact', contact.id] });
      queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
      // The picked user is no longer unlinked, and its own page now has a
      // contact (fix round 2, N2).
      queryClient.invalidateQueries({ queryKey: ['unlinked-users'] });
      queryClient.invalidateQueries({ queryKey: ['user-user', pickedUserId] });
      setLinkPickerOpen(false);
      setPickedUserId('');
    } catch (error) {
      setLinkError((error as CodedError).message);
    } finally {
      setLinking(false);
    }
  };

  if (!canViewUsers) return null;

  return (
    <Card>
      <CardHeader>
        <CardHeading>
          <CardTitle>User account</CardTitle>
        </CardHeading>
      </CardHeader>
      <CardContent className="space-y-3">
        {!linkedUser ? (
          <>
            <p className="text-sm text-muted-foreground">No user yet</p>
            <div className="flex flex-wrap gap-2">
              {canAddUsers && <Button onClick={() => setCreateUserOpen(true)}>Create user</Button>}
              {canEditUsers && (
                <Button variant="outline" onClick={() => setLinkPickerOpen((v) => !v)}>
                  Link existing user
                </Button>
              )}
            </div>
            {linkPickerOpen && (
              <div className="flex flex-wrap items-center gap-2 pt-2">
                <SearchableSelect
                  value={pickedUserId}
                  onChange={setPickedUserId}
                  clearable
                  loadError={unlinkedUsersError}
                  onRetry={() => void refetchUnlinkedUsers()}
                  options={(unlinkedUsers ?? []).map((u) => ({
                    value: u.id,
                    label: u.name || u.email || 'Unnamed user',
                  }))}
                  placeholder="Pick a user"
                  emptyMessage="No unlinked user found."
                  triggerClassName="w-64"
                />
                <Button disabled={!pickedUserId || linking} onClick={() => void handleLink()}>
                  {linking ? 'Linking…' : 'Link'}
                </Button>
              </div>
            )}
            {linkError && <p className="text-sm text-destructive">{linkError}</p>}
          </>
        ) : (
          <div className="space-y-3">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm [&_dt]:text-muted-foreground">
              <dt>User</dt>
              <dd>
                <Link
                  href={`/user-management/users/${linkedUser.id}`}
                  className="text-primary hover:underline"
                >
                  {linkedUser.name || 'Unnamed user'}
                </Link>
              </dd>
              <dt>Roles</dt>
              <dd>
                <span className="inline-flex flex-wrap items-center gap-1.5">
                  {linkedUser.roles.length ? (
                    linkedUser.roles.map((r) => (
                      <Badge key={r.id} variant="secondary" appearance="light">
                        {r.name}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-muted-foreground">No roles assigned</span>
                  )}
                </span>
              </dd>
              <dt>Signs in by</dt>
              <dd>{signInMethods(linkedUser).join(', ')}</dd>
            </dl>
            {canEditUsers && (
              <DeferredActionButton
                pending={unlink.pending}
                verb="Unlinking"
                onCancel={unlink.cancel}
                idle={
                  <Button
                    variant="destructive"
                    onClick={() => unlink.start()}
                    disabled={unlink.isBlocked}
                  >
                    Unlink WhatsApp contact
                  </Button>
                }
              />
            )}
          </div>
        )}
      </CardContent>

      {canAddUsers && (
        <UserAddDialog
          open={createUserOpen}
          closeDialog={() => setCreateUserOpen(false)}
          contact={{ id: contact.id }}
        />
      )}
    </Card>
  );
}
