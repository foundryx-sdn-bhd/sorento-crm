'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { RiCheckboxCircleFill, RiErrorWarningFill } from '@remixicon/react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useForm, type Resolver } from 'react-hook-form';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { toast } from '@/lib/toast';
import { apiFetch } from '@/lib/api';
import type { CodedError } from '@/lib/api-client';
import { isSuperadminUser } from '@/lib/is-superadmin';
import { getCompaniesSelect } from '@/app/(protected)/system-management/companies/services/companyService';
import { Alert, AlertContent, AlertIcon, AlertTitle } from '@/components/ui/alert';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { Button } from '@/components/ui/button';
import { LoaderCircleIcon } from 'lucide-react';
import { UserRole } from '@/app/models/user';
import { useRoleSelectQuery } from '../../roles/hooks/use-role-select-query';
import { buildUserAddSchema, UserAddSchemaType } from '../forms/user-add-schema';
import { useCreateUserMutation } from '../hooks/use-create-user-mutation';
import { updateUserContactLink } from '../services/userService';
import { getUsersSelect } from '@/services/userSelectService';
import {
  getContact,
  getContactCompanies,
  getContacts,
} from '../../contacts/[id]/services/contactService';
import type { RespondContact } from '../../contacts/types/contact.types';

const contactLabel = (c?: { name?: string | null; phone_number?: string | null } | null) =>
  c ? [c.name, c.phone_number].filter(Boolean).join(' - ') || 'Linked contact' : 'No linked contact';

/** The 409s the Add user form has to branch on, inline, above the footer (S3
 *  contract 2.1) - never a toast, since each one carries its own next step. */
type LinkErrorCode = 'CONTACT_ALREADY_LINKED' | 'PHONE_BELONGS_TO_USER' | 'EMAIL_TAKEN';

const UserAddDialog = ({
  open,
  closeDialog,
  contact,
}: {
  open: boolean;
  closeDialog: () => void;
  /** Opened from a contact (Internal Users row, or its own User account
   *  section): the WhatsApp contact field is locked to this one. */
  contact?: { id: string };
}) => {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { data: session } = useSession();
  const isSuperadmin = isSuperadminUser(session?.user);
  const [copyRolesBusy, setCopyRolesBusy] = useState(false);
  const [linkError, setLinkError] = useState<{ code: LinkErrorCode; message: string } | null>(null);
  const [resolvedHolder, setResolvedHolder] = useState<{ id: string; name: string | null } | null>(
    null,
  );
  const [linkingHolder, setLinkingHolder] = useState(false);
  // Which contact/company set has already been applied to the form, so a
  // re-render (or the same contact resolving twice) never re-fills it.
  const appliedContactRef = useRef<string | null>(null);
  const appliedCompaniesRef = useRef<string | null>(null);
  // Separate from the contact fill: the suggestion can only land once roles
  // have loaded, which may be after the contact did (fix round 2, N4).
  const appliedRoleRef = useRef<string | null>(null);

  // Fetch available roles. Guarded against a non-array response (a test's
  // generic `apiFetch` stub, say) - this query has no `enabled: open` gate, so
  // it fires the moment the component mounts, dialog closed or not.
  const {
    data: roleListRaw,
    error: roleListError,
    refetch: refetchRoleList,
  } = useRoleSelectQuery();
  const roleList: UserRole[] = useMemo(
    () => (Array.isArray(roleListRaw) ? (roleListRaw as UserRole[]) : []),
    [roleListRaw],
  );

  // Companies are required with a contact, for the superadmin who sees them (S4).
  const schema = useMemo(
    () => buildUserAddSchema({ companiesRequired: isSuperadmin }),
    [isSuperadmin],
  );
  const form = useForm<UserAddSchemaType>({
    resolver: zodResolver(schema) as Resolver<UserAddSchemaType>,
    defaultValues: {
      name: '',
      email: '',
      contact_number: '',
      respond_contact_id: contact?.id ?? null,
      roleIds: [],
      superior_id: null,
      companyIds: [],
    },
    // Validate on submit: blurring the empty Name on the way to the WhatsApp contact
    // field (the owner's first step) inserted "Name is required" above it, and the
    // shifted trigger swallowed the click.
    mode: 'onSubmit',
    reValidateMode: 'onChange',
  });

  useEffect(() => {
    if (open) {
      form.reset({
        name: '',
        email: '',
        contact_number: '',
        respond_contact_id: contact?.id ?? null,
        roleIds: [],
        superior_id: null,
        companyIds: [],
      });
      setLinkError(null);
      setResolvedHolder(null);
      appliedContactRef.current = null;
      appliedCompaniesRef.current = null;
      appliedRoleRef.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- form is stable; contact.id read fresh on open
  }, [open, contact?.id]);

  const {
    data: superiorUsers,
    error: superiorUsersError,
    refetch: refetchSuperiorUsers,
  } = useQuery({
    queryKey: ['users-select', 'active'],
    queryFn: () => getUsersSelect({ status: 'ACTIVE' }),
    enabled: open,
    staleTime: 1000 * 60 * 5,
  });

  const { data: companyOptions } = useQuery({
    queryKey: ['companies-select'],
    queryFn: getCompaniesSelect,
    enabled: open && isSuperadmin,
    staleTime: 1000 * 60 * 5,
  });

  const selectedContactId = form.watch('respond_contact_id');
  // Stable across renders: an inline function re-keys the picker's fetch effect on every
  // render of this form, and the popover's first open was lost to it.
  const fetchContactOptions = useCallback(async (query: string, pageIndex: number) => {
    const page = await getContacts({ pageIndex, pageSize: 20, searchQuery: query });
    return page.data.map((c: RespondContact) => ({ value: c.id, label: contactLabel(c) }));
  }, []);
  const isLocked = !!contact?.id;

  // The picked (or locked) contact's own record, so Name / Contact Number /
  // Companies / the suggested role can be filled from it (S3 2.1).
  const { data: linkedContactDetail } = useQuery({
    queryKey: ['respond-contact', selectedContactId],
    queryFn: () => getContact(selectedContactId as string),
    enabled: open && !!selectedContactId,
    staleTime: 1000 * 60,
  });

  const { data: contactCompanies } = useQuery({
    queryKey: ['contact-companies', selectedContactId],
    queryFn: () => getContactCompanies(selectedContactId as string),
    enabled: open && !!selectedContactId && isSuperadmin,
    staleTime: 1000 * 60,
  });

  // Only into fields the owner has not typed in (react-hook-form dirtyFields) -
  // picking a contact never overwrites something already entered. Contact
  // Number is the exception: it is read-only while a contact is set, so it is
  // always the contact's own number.
  useEffect(() => {
    if (!linkedContactDetail) return;
    if (appliedContactRef.current === linkedContactDetail.id) return;
    appliedContactRef.current = linkedContactDetail.id;
    const dirty = form.formState.dirtyFields;
    if (!dirty.name && linkedContactDetail.name) {
      form.setValue('name', linkedContactDetail.name);
    }
    form.setValue('contact_number', linkedContactDetail.phone_number ?? '');
  }, [linkedContactDetail, form]);

  useEffect(() => {
    if (!linkedContactDetail?.suggested_role_slug || !roleList.length) return;
    if (appliedRoleRef.current === linkedContactDetail.id) return;
    appliedRoleRef.current = linkedContactDetail.id;
    if (form.formState.dirtyFields.roleIds) return;
    const suggested = roleList.find((r) => r.slug === linkedContactDetail.suggested_role_slug);
    if (suggested) form.setValue('roleIds', [suggested.id]);
  }, [linkedContactDetail, form, roleList]);

  useEffect(() => {
    if (!selectedContactId || !contactCompanies || !isSuperadmin) return;
    if (appliedCompaniesRef.current === selectedContactId) return;
    if (form.formState.dirtyFields.companyIds) return;
    appliedCompaniesRef.current = selectedContactId;
    form.setValue(
      'companyIds',
      contactCompanies.map((c) => c.id),
    );
  }, [selectedContactId, contactCompanies, isSuperadmin, form]);

  // Cleared: Contact Number goes editable again, the rest is left as it is.
  useEffect(() => {
    if (!selectedContactId) {
      appliedContactRef.current = null;
      appliedRoleRef.current = null;
    }
  }, [selectedContactId]);

  const suggestedRole = roleList.find(
    (r) => r.slug === linkedContactDetail?.suggested_role_slug,
  );

  // One-shot prefill: copy another user's roles into the multi-select, which
  // stays fully editable afterwards. The picker resets to empty after each pick.
  const handleCopyRoles = async (pickedId: string) => {
    if (!pickedId) return;
    setCopyRolesBusy(true);
    try {
      const response = await apiFetch(`/api/user-management/users/${pickedId}/roles`);
      if (!response.ok) throw new Error('Failed to load roles for that user.');
      const roles = (await response.json()) as { id: string; name: string }[];
      form.setValue('roleIds', roles.map((r) => r.id), {
        shouldValidate: true,
        shouldDirty: true,
      });
    } catch (error) {
      toast.custom(
        () => (
          <Alert variant="mono" icon="destructive" close={false}>
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>{(error as Error).message}</AlertTitle>
          </Alert>
        ),
        { position: 'top-center' },
      );
    } finally {
      setCopyRolesBusy(false);
    }
  };

  const createMutation = useCreateUserMutation();

  const isProcessing = createMutation.isPending;

  const handleSubmit = (values: UserAddSchemaType) => {
    setLinkError(null);
    setResolvedHolder(null);
    const contactNumber = typeof values.contact_number === 'string' ? values.contact_number.trim() : '';
    const email = typeof values.email === 'string' ? values.email.trim() : '';
    createMutation.mutate(
      {
        name: values.name,
        email: email ? email.toLowerCase() : null,
        contact_number: contactNumber || null,
        respond_contact_id: values.respond_contact_id || null,
        role_ids: values.roleIds,
        superior_id: values.superior_id === '__none__' || !values.superior_id ? null : values.superior_id,
        company_ids: isSuperadmin ? (values.companyIds ?? []) : [],
      },
      {
        onSuccess: () => {
          toast.custom(
            () => (
              <Alert variant="mono" icon="success" close={false}>
                <AlertIcon>
                  <RiCheckboxCircleFill />
                </AlertIcon>
                <AlertTitle>User added</AlertTitle>
              </Alert>
            ),
            { position: 'top-center' },
          );
          closeDialog();
        },
        onError: (error) => {
          const coded = error as CodedError;
          if (
            coded.code === 'CONTACT_ALREADY_LINKED' ||
            coded.code === 'PHONE_BELONGS_TO_USER' ||
            coded.code === 'EMAIL_TAKEN'
          ) {
            setLinkError({ code: coded.code, message: coded.message });
            return;
          }
          toast.custom(
            () => (
              <Alert variant="mono" icon="destructive" close={false}>
                <AlertIcon>
                  <RiErrorWarningFill />
                </AlertIcon>
                <AlertTitle>{coded.message || 'Failed to add user'}</AlertTitle>
              </Alert>
            ),
            { position: 'top-center' },
          );
        },
      },
    );
  };

  // Resolve the OTHER user a 409 named, so "Open user" / "Link this contact to
  // <name> instead" have somewhere to go (AC-42, AC-43).
  useEffect(() => {
    if (!linkError) {
      setResolvedHolder(null);
      return;
    }
    let cancelled = false;
    (async () => {
      let holder: { id: string; name: string | null } | null = null;
      // Null when it cannot be resolved: the message already names them.
      try {
        if (linkError.code === 'CONTACT_ALREADY_LINKED' && selectedContactId) {
          holder = (await getUsersSelect({ respond_contact_id: selectedContactId }))[0] ?? null;
        } else if (linkError.code === 'PHONE_BELONGS_TO_USER') {
          const phone = form.getValues('contact_number');
          if (phone) holder = (await getUsersSelect({ phone }))[0] ?? null;
        }
      } catch {
        holder = null;
      }
      if (!cancelled) setResolvedHolder(holder);
    })();
    return () => {
      cancelled = true;
    };
  }, [linkError, selectedContactId, form]);

  const handleLinkHolderInstead = async () => {
    if (!resolvedHolder || !selectedContactId) return;
    setLinkingHolder(true);
    try {
      await updateUserContactLink(resolvedHolder.id, selectedContactId);
      toast.success(`Linked to ${resolvedHolder.name ?? 'that user'}`);
      queryClient.invalidateQueries({ queryKey: ['user-users'] });
      queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
      queryClient.invalidateQueries({ queryKey: ['respond-contact', selectedContactId] });
      closeDialog();
    } catch (error) {
      const coded = error as CodedError;
      setLinkError({
        code: (coded.code as LinkErrorCode) ?? 'CONTACT_ALREADY_LINKED',
        message: coded.message,
      });
    } finally {
      setLinkingHolder(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={closeDialog}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add User</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)}>
            <DialogBody className="pt-2.5 space-y-6">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input placeholder="Enter name" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Optional when there is a phone"
                        {...field}
                        value={field.value ?? ''}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="contact_number"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Contact Number</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Enter contact number"
                        {...field}
                        value={field.value ?? ''}
                        disabled={!!selectedContactId}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="respond_contact_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>WhatsApp contact</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        value={field.value ?? ''}
                        onChange={(v) => field.onChange(v || null)}
                        disabled={isLocked}
                        clearable={!isLocked}
                        fetchOptions={fetchContactOptions}
                        selectedOption={
                          field.value && linkedContactDetail
                            ? { value: field.value, label: contactLabel(linkedContactDetail) }
                            : undefined
                        }
                        placeholder="Link a WhatsApp contact (optional)"
                        emptyMessage="No contact found."
                        triggerClassName="w-full"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormItem>
                <FormLabel>Copy roles from another user (optional)</FormLabel>
                <FormControl>
                  <SearchableSelect
                    value=""
                    onChange={(v) => handleCopyRoles(v)}
                    disabled={copyRolesBusy}
                    placeholder="Copy roles from another user (optional)"
                    emptyMessage="No active user found."
                    triggerClassName="w-full"
                    loadError={superiorUsersError}
                    onRetry={() => void refetchSuperiorUsers()}
                    options={(superiorUsers || []).map((u) => ({
                      value: u.id,
                      label: u.name || u.email || 'Unnamed user',
                      searchText: `${u.name ?? ''} ${u.email ?? ''}`.trim() || u.id,
                    }))}
                  />
                </FormControl>
              </FormItem>
              <FormField
                control={form.control}
                name="roleIds"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="flex items-center gap-2">
                      Roles
                      {suggestedRole && (
                        <span className="text-xs font-normal text-muted-foreground">
                          Suggested: {suggestedRole.name}
                        </span>
                      )}
                    </FormLabel>
                    <FormControl>
                      <SearchableMultiSelect
                        value={field.value ?? []}
                        onChange={(v) => field.onChange(v)}
                        loadError={roleListError}
                        onRetry={() => void refetchRoleList()}
                        options={roleList.map((role: UserRole) => ({
                          value: role.id,
                          label: role.name,
                        }))}
                        placeholder="Select roles"
                        emptyMessage="No role found."
                        triggerClassName="w-full"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {isSuperadmin && (
                <FormField
                  control={form.control}
                  name="companyIds"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        Companies
                        {selectedContactId && (
                          <>
                            {' '}
                            <span className="text-destructive">*</span>
                          </>
                        )}
                      </FormLabel>
                      <FormControl>
                        <SearchableMultiSelect
                          value={field.value ?? []}
                          onChange={(v) => field.onChange(v)}
                          options={(companyOptions || []).map((c) => ({
                            value: c.id,
                            label: c.name,
                            searchText: `${c.name} ${c.code}`,
                          }))}
                          placeholder="Select companies"
                          emptyMessage="No company found."
                          triggerClassName="w-full"
                        />
                      </FormControl>
                      <p className="text-xs text-muted-foreground">
                        Which companies this user can access &amp; switch between.
                      </p>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              <FormField
                control={form.control}
                name="superior_id"
                render={({ field }) => {
                  const value = field.value || '__none__';
                  return (
                    <FormItem>
                      <FormLabel>Superior</FormLabel>
                      <FormControl>
                        <SearchableSelect
                          value={value && value !== '__none__' ? value : '__none__'}
                          onChange={(v) => field.onChange(v === '__none__' ? null : v)}
                          placeholder="None"
                          emptyMessage="No active user found."
                          triggerClassName="w-full"
                          loadError={superiorUsersError}
                          onRetry={() => void refetchSuperiorUsers()}
                          options={[
                            { value: '__none__', label: 'None' },
                            ...(superiorUsers || []).map((superior) => ({
                              value: superior.id,
                              label: superior.name || superior.email || 'Unnamed user',
                              // Searchable by name AND email, as the hand-rolled picker was.
                              searchText:
                                `${superior.name ?? ''} ${superior.email ?? ''}`.trim() ||
                                superior.id,
                            })),
                          ]}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  );
                }}
              />

              {linkError && (
                <Alert variant="destructive" appearance="light">
                  <AlertIcon>
                    <RiErrorWarningFill />
                  </AlertIcon>
                  <AlertContent>
                    <AlertTitle>{linkError.message}</AlertTitle>
                    {linkError.code === 'CONTACT_ALREADY_LINKED' && resolvedHolder && (
                      <Button
                        type="button"
                        variant="link"
                        size="sm"
                        className="h-auto p-0"
                        onClick={() => router.push(`/user-management/users/${resolvedHolder.id}`)}
                      >
                        Open user
                      </Button>
                    )}
                    {linkError.code === 'PHONE_BELONGS_TO_USER' && resolvedHolder && (
                      selectedContactId ? (
                        <Button
                          type="button"
                          variant="link"
                          size="sm"
                          className="h-auto p-0"
                          disabled={linkingHolder}
                          onClick={() => void handleLinkHolderInstead()}
                        >
                          {linkingHolder
                            ? 'Linking…'
                            : `Link this contact to ${resolvedHolder.name ?? 'that user'} instead`}
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          variant="link"
                          size="sm"
                          className="h-auto p-0"
                          onClick={() => router.push(`/user-management/users/${resolvedHolder.id}`)}
                        >
                          Open user
                        </Button>
                      )
                    )}
                  </AlertContent>
                </Alert>
              )}
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={closeDialog}>
                Cancel
              </Button>
              <Button
                type="submit"
                // A contact prefills the whole form, so the quick create is one
                // click with nothing typed (plan 6.2).
                disabled={(!form.formState.isDirty && !selectedContactId) || isProcessing}
              >
                {isProcessing && <LoaderCircleIcon className="animate-spin" />}
                Add user
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>

    </Dialog>
  );
};

export default UserAddDialog;
