'use client';

import { useEffect, useRef, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { RiCheckboxCircleFill, RiErrorWarningFill } from '@remixicon/react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useForm, type Resolver } from 'react-hook-form';
import { useSession } from 'next-auth/react';
import { toast } from '@/lib/toast';
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import { isSuperadminUser } from '@/lib/is-superadmin';
import { getCompaniesSelect } from '@/app/(protected)/system-management/companies/services/companyService';
import {
  Alert,
  AlertDescription,
  AlertIcon,
  AlertTitle,
} from '@/components/ui/alert';
import {
  Dialog,
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
import { Checkbox } from '@/components/ui/checkbox';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { Button } from '@/components/ui/button';
import { LoaderCircleIcon } from 'lucide-react';
import { User, UserRole } from '@/app/models/user';
import { useCompany } from '@/app/providers/CompanyProvider';
import { useRoleSelectQuery } from '../../../roles/hooks/use-role-select-query';
import { UserStatusProps } from '../../constants/status';
import {
  createAllScopeRow,
  rowsToScopePayload,
  scopeRowsHaveUnknownBrands,
  scopesToRows,
  type ScopeRow,
} from '../../lib/productDiscontinuedScopes';
import ProductDiscontinuedScopeEditor from './product-discontinued-scope-editor';
import {
  UserProfileSchema,
  UserProfileSchemaType,
} from '../forms/user-profile-schema';

const UserProfileEditDialog = ({
  open,
  closeDialog,
  user,
}: {
  open: boolean;
  closeDialog: () => void;
  user: User;
}) => {
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  const isSuperadmin = isSuperadminUser(session?.user);
  const [copyRolesBusy, setCopyRolesBusy] = useState(false);
  // Product-discontinued scopes are not part of the zod form: they are rows, not
  // fields, so they carry their own state + dirty flag and ride the same PUT.
  const [scopeRows, setScopeRows] = useState<ScopeRow[]>(() =>
    scopesToRows(user?.productDiscontinuedScopes),
  );
  const [scopesDirty, setScopesDirty] = useState(false);
  // A row whose brand list failed to load would otherwise save as "all brands in
  // that company", so saving waits until the admin resolves it.
  const scopeBrandsUnknown = scopeRowsHaveUnknownBrands(scopeRows);
  const { companies: scopeCompanies } = useCompany();

  // Fetch available roles
  const { data: roleList } = useRoleSelectQuery();

  const { data: userRoles = [] } = useQuery({
    queryKey: ['user-roles', user?.id],
    queryFn: async () => {
      const response = await apiFetch(`/api/user-management/users/${user!.id}/roles`);
      if (!response.ok) throw new Error('Failed to fetch user roles');
      return response.json() as Promise<{ id: string; name: string }[]>;
    },
    enabled: open && !!user?.id,
  });

  const { data: userCompanies = [], isFetched: companiesFetched } = useQuery({
    queryKey: ['user-companies', user?.id],
    queryFn: async () => {
      const response = await apiFetch(`/api/user-management/users/${user!.id}/companies`);
      if (!response.ok) throw new Error('Failed to fetch user companies');
      return response.json() as Promise<{ id: string; name: string; code: string }[]>;
    },
    enabled: open && !!user?.id && isSuperadmin,
    staleTime: 1000 * 60,
  });

  const { data: companyOptions = [] } = useQuery({
    queryKey: ['companies-select'],
    queryFn: getCompaniesSelect,
    enabled: open && isSuperadmin,
    staleTime: 1000 * 60 * 5,
  });

  const form = useForm<UserProfileSchemaType>({
    resolver: zodResolver(UserProfileSchema) as Resolver<UserProfileSchemaType>,
    defaultValues: {
      name: user?.name || '',
      email: user?.email || '',
      roleIds: user?.roles?.length ? user.roles.map((r) => r.id) : (user?.roleId ? [user.roleId] : []),
      companyIds: [],
      status: user?.status || '',
      respond_user_id: user?.respondUserId || '',
      contact_number: user?.contactNumber || '',
      tier: user?.tier ?? undefined,
      superior_id: user?.superiorId || '',
      respond_contact_id: user?.respondContactId ?? null,
      notify_whatsapp: Boolean(user?.notifyWhatsapp),
      notify_whatsapp_summary: Boolean(user?.notifyWhatsappSummary),
      notify_email_on_assignment: user?.notifyEmailOnAssignment ?? true,
      notify_email_on_escalation: user?.notifyEmailOnEscalation ?? true,
      notify_whatsapp_on_assignment: Boolean(user?.notifyWhatsappOnAssignment),
      notify_whatsapp_on_escalation: Boolean(user?.notifyWhatsappOnEscalation),
      notify_email_on_deadline_extended: user?.notifyEmailOnDeadlineExtended ?? true,
      notify_whatsapp_on_deadline_extended: Boolean(user?.notifyWhatsappOnDeadlineExtended),
      notify_email_on_handling: user?.notifyEmailOnHandling ?? true,
      notify_whatsapp_on_handling: Boolean(user?.notifyWhatsappOnHandling),
      notify_email_on_mention: user?.notifyEmailOnMention ?? true,
      notify_email_on_product_discontinued: Boolean(user?.notifyEmailOnProductDiscontinued),
      notify_whatsapp_on_product_discontinued: Boolean(user?.notifyWhatsappOnProductDiscontinued),
    },
    mode: 'onTouched',
  });

  const lastResetRef = useRef<{ userId: string } | null>(null);
  const companiesResetRef = useRef<string | null>(null);
  useEffect(() => {
    if (!open) {
      lastResetRef.current = null;
      companiesResetRef.current = null;
      return;
    }
    if (!user?.id) return;
    if (lastResetRef.current?.userId === user.id) return;
    lastResetRef.current = { userId: user.id };
    const roleIds = userRoles.length ? userRoles.map((r: { id: string }) => r.id) : (user?.roles?.length ? user.roles.map((r) => r.id) : (user?.roleId ? [user.roleId] : []));
    form.reset({
      name: user.name || '',
      email: user.email || '',
      roleIds,
      companyIds: [],
      status: user.status || '',
      respond_user_id: user.respondUserId || '',
      contact_number: user.contactNumber || '',
      tier: user.tier ?? undefined,
      superior_id: user.superiorId || '',
      respond_contact_id: user.respondContactId ?? null,
      notify_whatsapp: Boolean(user.notifyWhatsapp),
      notify_whatsapp_summary: Boolean(user.notifyWhatsappSummary),
      notify_email_on_assignment: user.notifyEmailOnAssignment ?? true,
      notify_email_on_escalation: user.notifyEmailOnEscalation ?? true,
      notify_whatsapp_on_assignment: Boolean(user.notifyWhatsappOnAssignment),
      notify_whatsapp_on_escalation: Boolean(user.notifyWhatsappOnEscalation),
      notify_email_on_deadline_extended: user.notifyEmailOnDeadlineExtended ?? true,
      notify_whatsapp_on_deadline_extended: Boolean(user.notifyWhatsappOnDeadlineExtended),
      notify_email_on_handling: user.notifyEmailOnHandling ?? true,
      notify_whatsapp_on_handling: Boolean(user.notifyWhatsappOnHandling),
      notify_email_on_mention: user.notifyEmailOnMention ?? true,
      notify_email_on_product_discontinued: Boolean(user.notifyEmailOnProductDiscontinued),
      notify_whatsapp_on_product_discontinued: Boolean(user.notifyWhatsappOnProductDiscontinued),
    });
    setScopeRows(scopesToRows(user.productDiscontinuedScopes));
    setScopesDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- form is stable; omit to avoid reset loop
  }, [open, user?.id, user?.name, user?.email, user?.status, user?.roles, user?.respondUserId, user?.tier, user?.superiorId, userRoles.length]);

  const discontinuedEmailOn = form.watch('notify_email_on_product_discontinued');
  const discontinuedWhatsappOn = form.watch('notify_whatsapp_on_product_discontinued');
  const discontinuedNotifyOn = Boolean(discontinuedEmailOn || discontinuedWhatsappOn);

  // Switching a discontinued channel on with no scopes at all would mean opting in
  // to silence, so the first row defaults to everything. Removing every row after
  // that is left alone - the editor says out loud that it means no notices.
  //
  // Only a real off -> on flick INSIDE this dialog pre-populates. Merely re-opening
  // on an already-on toggle must not resurrect a scope set the user deliberately
  // emptied and saved, which is what a plain "on and empty" condition would do.
  const discontinuedWasOnRef = useRef<boolean | null>(null);
  useEffect(() => {
    if (!open) {
      discontinuedWasOnRef.current = null;
      return;
    }
    const wasOn = discontinuedWasOnRef.current;
    discontinuedWasOnRef.current = discontinuedNotifyOn;
    if (wasOn !== false || !discontinuedNotifyOn) return;
    if (scopeRows.length) return;
    setScopeRows([createAllScopeRow()]);
    setScopesDirty(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs on the toggle transition, not on row edits
  }, [open, discontinuedNotifyOn]);

  // Companies aren't carried on the `user` prop, so apply them once the grant fetch
  // resolves. Guarded per user-open so a background refetch can't clobber edits.
  useEffect(() => {
    if (!open || !isSuperadmin || !user?.id || !companiesFetched) return;
    if (companiesResetRef.current === user.id) return;
    companiesResetRef.current = user.id;
    form.setValue('companyIds', userCompanies.map((c) => c.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- form is stable; guard prevents loops
  }, [open, isSuperadmin, user?.id, companiesFetched, userCompanies]);

  const {
    data: superiorUsers,
    error: superiorUsersError,
    refetch: refetchSuperiorUsers,
  } = useQuery({
    queryKey: ['users-select'],
    queryFn: async () => {
      const response = await apiFetch('/api/user-management/users/select');
      if (!response.ok) {
        throw new Error('Failed to fetch users.');
      }
      return response.json();
    },
    staleTime: 1000 * 60 * 5,
  });

  const { data: respondUsers } = useQuery({
    queryKey: ['respond-users'],
    queryFn: async () => {
      const response = await apiFetch('/api/user-management/users/respond-users');
      if (!response.ok) {
        // If endpoint fails, return empty array (Respond API might not be configured)
        return [];
      }
      return response.json();
    },
    staleTime: 1000 * 60 * 5,
  });

  const selectedContactId = form.watch('respond_contact_id');

  // The contact picker fetches its own page through SearchableSelect's async mode, so the
  // separate react-query list this component used to keep is gone.
  // Resolve the currently-linked contact so the trigger shows a human label, not the id.
  const { data: linkedContact } = useQuery({
    queryKey: ['respond-contact', selectedContactId],
    queryFn: async () => {
      if (!selectedContactId) return null;
      const response = await apiFetch(`/api/user-management/contacts/${selectedContactId}`);
      if (!response.ok) return null;
      return (await response.json()) as { id: string; name?: string | null; phone_number?: string | null };
    },
    enabled: open && !!selectedContactId,
    staleTime: 1000 * 60,
  });

  const contactLabel = (c?: { name?: string | null; phone_number?: string | null } | null) =>
    c ? [c.name, c.phone_number].filter(Boolean).join(' · ') || 'Linked contact' : 'No linked contact';

  // One-shot prefill: copy another user's roles into the checkbox list, which stays
  // fully editable afterwards. The picker resets to empty after each pick.
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
          <Alert variant="mono" icon="destructive">
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

  const mutation = useMutation({
    mutationFn: async (values: UserProfileSchemaType) => {
      const roleIds = values.roleIds?.length ? values.roleIds : [];
      const rolesResponse = await apiFetch(`/api/user-management/users/${user.id}/roles`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role_ids: roleIds }),
      });
      if (!rolesResponse.ok) {
        const err = await rolesResponse.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail || 'Failed to update roles');
      }

      const profileData: Record<string, unknown> = {
        name: values.name,
        email: values.email.trim().toLowerCase(),
        status: values.status,
      };
      profileData.contact_number = values.contact_number?.trim() || null;
      profileData.respond_user_id = values.respond_user_id?.trim() || null;
      profileData.tier = values.tier != null ? (typeof values.tier === 'number' ? values.tier : Number(values.tier)) : null;
      profileData.superior_id = (values.superior_id && values.superior_id !== '__none__' && values.superior_id.trim() !== '')
        ? values.superior_id
        : null;
      profileData.respond_contact_id = values.respond_contact_id || null;
      profileData.notify_whatsapp = Boolean(values.notify_whatsapp);
      profileData.notify_whatsapp_summary = Boolean(values.notify_whatsapp_summary);
      profileData.notify_email_on_assignment = Boolean(values.notify_email_on_assignment);
      profileData.notify_email_on_escalation = Boolean(values.notify_email_on_escalation);
      profileData.notify_whatsapp_on_assignment = Boolean(values.notify_whatsapp_on_assignment);
      profileData.notify_whatsapp_on_escalation = Boolean(values.notify_whatsapp_on_escalation);
      profileData.notify_email_on_deadline_extended = Boolean(values.notify_email_on_deadline_extended);
      profileData.notify_whatsapp_on_deadline_extended = Boolean(values.notify_whatsapp_on_deadline_extended);
      profileData.notify_email_on_handling = Boolean(values.notify_email_on_handling);
      profileData.notify_whatsapp_on_handling = Boolean(values.notify_whatsapp_on_handling);
      profileData.notify_email_on_mention = Boolean(values.notify_email_on_mention);
      profileData.notify_email_on_product_discontinued = Boolean(values.notify_email_on_product_discontinued);
      profileData.notify_whatsapp_on_product_discontinued = Boolean(values.notify_whatsapp_on_product_discontinued);
      // Replace-all: whatever the editor shows is the user's full scope set. Sent
      // only when the editor was actually touched - omitted means "leave scopes
      // alone", so an unrelated profile save can never rewrite them.
      if (scopesDirty) {
        profileData.product_discontinued_scopes = rowsToScopePayload(scopeRows);
      }

      const response = await apiFetch(`/api/user-management/users/${user.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profileData),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error((err as { message?: string }).message || 'Failed to update user');
      }

      const updatedUser = await response.json();

      // Superadmin-only: sync company grants (delete-all-then-reinsert server-side).
      if (isSuperadmin) {
        const companiesResponse = await apiFetch(`/api/user-management/users/${user.id}/companies`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ company_ids: values.companyIds ?? [] }),
        });
        if (!companiesResponse.ok) {
          throw new Error(await extractApiError(companiesResponse, 'Failed to update companies'));
        }
      }

      return { success: true, user: updatedUser };
    },
    onSuccess: () => {
      const message = 'User updated successfully';

      toast.custom(
        () => (
          <Alert variant="mono" icon="success">
            <AlertIcon>
              <RiCheckboxCircleFill />
            </AlertIcon>
            <AlertTitle>{message}</AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
        },
      );

      // Invalidate and refetch user data to get updated values
      queryClient.invalidateQueries({ queryKey: ['user-users'] });
      queryClient.invalidateQueries({ queryKey: ['user-user', user.id] });
      queryClient.invalidateQueries({ queryKey: ['user-roles', user.id] });
      queryClient.invalidateQueries({ queryKey: ['user-companies', user.id] });
      queryClient.refetchQueries({ queryKey: ['user-user', user.id] });
      closeDialog();
    },
    onError: (error: Error) => {
      toast.custom(
        () => (
          <Alert variant="mono" icon="destructive">
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>{error.message}</AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
        },
      );
    },
  });

  const respondSyncMutation = useMutation({
    mutationFn: async () => {
      // Get the current respond_user_id from the form
      const respondUserId = form.getValues('respond_user_id');
      const response = await apiFetch(`/api/v1/user-management/users/${user.id}/sync-respond`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ respond_user_id: respondUserId }),
      });

      if (!response.ok) {
        const { message } = await response.json().catch(() => ({ message: 'Failed to sync respond user.' }));
        throw new Error(message);
      }

      return response.json();
    },
    onSuccess: (data) => {
      toast.custom(
        () => (
          <Alert variant="mono" icon="success">
            <AlertIcon>
              <RiCheckboxCircleFill />
            </AlertIcon>
            <AlertTitle>{data.message || 'Respond user synced successfully'}</AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
        },
      );

      queryClient.invalidateQueries({ queryKey: ['user-user'] });
    },
    onError: (error: Error) => {
      toast.custom(
        () => (
          <Alert variant="mono" icon="destructive">
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>{error.message}</AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
        },
      );
    },
  });

  const isProcessing = mutation.status === 'pending';
  const isSyncing = respondSyncMutation.status === 'pending';

  const handleSubmit = (values: UserProfileSchemaType) => {
    mutation.mutate(values);
  };

  return (
    <Dialog open={open} onOpenChange={closeDialog}>
      <DialogContent className="max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader className="shrink-0">
          <DialogTitle>Edit User Details</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="flex flex-1 min-h-0 flex-col overflow-hidden"
          >
            <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden pr-1 space-y-6">
            {mutation.status === 'error' && (
              <Alert variant="destructive">
                <AlertDescription>{mutation.error.message}</AlertDescription>
              </Alert>
            )}
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input placeholder="Enter user name" {...field} />
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
                  <FormLabel>Email address</FormLabel>
                  <FormControl>
                    <Input
                      type="email"
                      autoComplete="email"
                      placeholder="name@company.com"
                      {...field}
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
                      value={field.value || ''}
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
                  emptyMessage="No user found."
                  triggerClassName="w-full"
                  loadError={superiorUsersError}
                  onRetry={() => void refetchSuperiorUsers()}
                  options={(superiorUsers || [])
                    .filter((u: { id: string }) => u.id !== user?.id)
                    .map((u: { id: string; name?: string | null; email: string }) => ({
                      value: u.id,
                      label: u.name || u.email,
                      searchText: `${u.name ?? ''} ${u.email}`.trim() || u.id,
                    }))}
                />
              </FormControl>
            </FormItem>
            <FormField
              control={form.control}
              name="roleIds"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Roles</FormLabel>
                  <FormControl>
                    <div className="flex max-h-48 flex-col gap-2 overflow-y-auto rounded-md border p-3">
                      {(roleList ?? []).map((role: UserRole) => (
                        <div
                          key={role.id}
                          className="flex flex-row items-center space-x-2"
                        >
                          <Checkbox
                            id={`role-${role.id}`}
                            checked={field.value?.includes(role.id)}
                            onCheckedChange={(checked) => {
                              const next = checked
                                ? [...(field.value ?? []), role.id]
                                : (field.value ?? []).filter((id) => id !== role.id);
                              field.onChange(next);
                            }}
                          />
                          <label
                            htmlFor={`role-${role.id}`}
                            className="font-normal cursor-pointer text-sm"
                          >
                            {role.name}
                          </label>
                        </div>
                      ))}
                    </div>
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
                    <FormLabel>Companies</FormLabel>
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
              name="status"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Status</FormLabel>
                  <FormControl>
                    <SearchableSelect
                      onChange={(value) => field.onChange(value)}
                      value={field.value || ''}
                      placeholder="Select a status"
                      options={Object.entries(UserStatusProps).map(([status, { label }]) => ({
                        value: status,
                        label,
                      }))}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            {/* RESPOND USER ID FIELD - ADDED */}
            <FormField
              control={form.control}
              name="respond_user_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-lg font-semibold">Respond User ID</FormLabel>
                  <FormControl>
                    <div className="space-y-2">
                      <Input 
                        placeholder="Enter Respond user ID (e.g., user_123)" 
                        {...field} 
                        value={field.value || ''} 
                        className="border-2"
                      />
                      {field.value && (
                        <div className="flex flex-wrap items-center gap-2 mt-2">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => respondSyncMutation.mutate()}
                            disabled={isSyncing}
                          >
                            {isSyncing && <LoaderCircleIcon className="animate-spin mr-2 h-4 w-4" />}
                            Sync Respond User
                          </Button>
                        </div>
                      )}
                    </div>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="tier"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-lg font-semibold">Tier</FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      min={1}
                      step={1}
                      placeholder="Conversation SLA policy tier (e.g. 1, 2)"
                      {...field}
                      value={field.value ?? ''}
                      onChange={(e) => {
                        const v = e.target.value;
                        field.onChange(v === '' ? undefined : (Number(v) || undefined));
                      }}
                      className="border-2"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="superior_id"
              render={({ field }) => {
                const value = field.value || '__none__';
                const filteredSuperiors = (superiorUsers || []).filter((s: { id: string }) => s.id !== user.id);
                return (
                  <FormItem>
                    <FormLabel>Superior</FormLabel>
                    <FormControl>
                      <SearchableSelect
                        value={value && value !== '__none__' ? value : '__none__'}
                        onChange={(v) => field.onChange(v === '__none__' ? null : v)}
                        placeholder="None"
                        emptyMessage="No user found."
                        triggerClassName="w-full"
                        options={[
                          { value: '__none__', label: 'None' },
                          ...filteredSuperiors.map(
                            (superior: { id: string; name?: string | null; email: string }) => ({
                              value: superior.id,
                              label: superior.name || superior.email,
                              searchText:
                                `${superior.name ?? ''} ${superior.email}`.trim() || superior.id,
                            }),
                          ),
                        ]}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                );
              }}
            />
            {/* WhatsApp contact link (TCK-31) - name + phone, never UUID */}
            <FormField
              control={form.control}
              name="respond_contact_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>WhatsApp Contact</FormLabel>
                  <FormControl>
                    <SearchableSelect
                      value={field.value ?? '__none__'}
                      onChange={(v) => field.onChange(v === '__none__' ? null : v)}
                      // Server-searched (the old picker set shouldFilter={false}); async mode
                      // owns the debounce and stale-response dropping. Typing drives the same
                      // react-query fetch via contactSearch.
                      fetchOptions={async (query) => {
                        const params = new URLSearchParams({ limit: '20' });
                        if (query.trim()) params.set('query', query.trim());
                        const response = await apiFetch(
                          `/api/user-management/contacts?${params.toString()}`,
                        );
                        const rows = response.ok
                          ? ((await response.json())?.data ?? [])
                          : [];
                        return [
                          { value: '__none__', label: 'No linked contact' },
                          ...(rows as Array<{ id: string; name?: string | null; phone_number?: string | null }>).map((c) => ({
                            value: c.id,
                            label: contactLabel(c),
                          })),
                        ];
                      }}
                      // Without this the trigger blanks on load: the linked contact is not in
                      // the first page of results.
                      selectedOption={
                        field.value && linkedContact
                          ? { value: field.value, label: contactLabel(linkedContact) }
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
            {([
              { name: 'notify_email_on_assignment', label: 'Email on assignment', hint: 'Emailed when an SLA stage is assigned to you.' },
              { name: 'notify_email_on_escalation', label: 'Email on escalation', hint: 'Emailed when an SLA escalates to you.' },
              { name: 'notify_whatsapp_on_assignment', label: 'WhatsApp on assignment', hint: 'Needs a linked WhatsApp contact.' },
              { name: 'notify_whatsapp_on_escalation', label: 'WhatsApp on escalation', hint: 'Needs a linked WhatsApp contact.' },
              { name: 'notify_email_on_deadline_extended', label: 'Email on deadline extended', hint: 'Emailed when an assignee extends a resolution deadline and you are the next escalation tier.' },
              { name: 'notify_whatsapp_on_deadline_extended', label: 'WhatsApp on deadline extended', hint: 'Needs a linked WhatsApp contact.' },
              { name: 'notify_email_on_handling', label: 'Email on handling', hint: 'Emailed when a handling lock is claimed / taken over / released on a form you are eligible for.' },
              { name: 'notify_whatsapp_on_handling', label: 'WhatsApp on handling', hint: 'Needs a linked WhatsApp contact.' },
              { name: 'notify_email_on_mention', label: 'Email on mention in a note', hint: 'Emailed when a teammate mentions you in an internal note.' },
              { name: 'notify_email_on_product_discontinued', label: 'Email on products discontinued', hint: 'Emailed when products are newly discontinued (batched).' },
              { name: 'notify_whatsapp_on_product_discontinued', label: 'WhatsApp on products discontinued', hint: 'Needs a linked WhatsApp contact.' },
            ] as const).map((t) => (
              <FormField
                key={t.name}
                control={form.control}
                name={t.name}
                render={({ field }) => (
                  <FormItem className="flex flex-row items-center justify-between rounded-md border p-3">
                    <div className="space-y-0.5">
                      <FormLabel>{t.label}</FormLabel>
                      <p className="text-xs text-muted-foreground">{t.hint}</p>
                    </div>
                    <FormControl>
                      <Checkbox checked={Boolean(field.value)} onCheckedChange={(v) => field.onChange(Boolean(v))} />
                    </FormControl>
                  </FormItem>
                )}
              />
            ))}
            <ProductDiscontinuedScopeEditor
              rows={scopeRows}
              companies={scopeCompanies}
              disabled={isProcessing}
              onChange={(rows) => {
                setScopeRows(rows);
                setScopesDirty(true);
              }}
              onBrandsLoadErrorChange={setScopeRows}
            />
            <FormField
              control={form.control}
              name="notify_whatsapp_summary"
              render={({ field }) => (
                <FormItem className="flex flex-row items-center justify-between rounded-md border p-3">
                  <div className="space-y-0.5">
                    <FormLabel>WhatsApp daily SLA summary</FormLabel>
                    <p className="text-xs text-muted-foreground">Needs a linked WhatsApp contact.</p>
                  </div>
                  <FormControl>
                    <Checkbox checked={Boolean(field.value)} onCheckedChange={(v) => field.onChange(Boolean(v))} />
                  </FormControl>
                </FormItem>
              )}
            />
            </div>
            <DialogFooter className="shrink-0">
              <Button type="button" variant="outline" onClick={closeDialog}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={
                  (!form.formState.isDirty && !scopesDirty) ||
                  isProcessing ||
                  scopeBrandsUnknown
                }
              >
                {isProcessing && <LoaderCircleIcon className="animate-spin" />}
                Save Changes
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
};

export default UserProfileEditDialog;
