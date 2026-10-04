'use client';

import { useEffect, useMemo, useRef } from 'react';
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
  AlertIcon,
  AlertTitle,
} from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
  FormDescription,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { LoaderCircleIcon } from 'lucide-react';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Switch } from '@/components/ui/switch';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { listRespondWorkspaceSelect } from '@/app/(protected)/system-management/respond-workspaces/services/respondWorkspaceService';
import { useContactAccessTypes } from '@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes';
import type { RespondContact } from '../../types/contact.types';
import {
  ContactEditSchema,
  ContactEditSchemaType,
} from '../../forms/contact-edit-schema';

interface ContactEditDialogProps {
  open: boolean;
  closeDialog: () => void;
  contact: RespondContact;
}

export default function ContactEditDialog({
  open,
  closeDialog,
  contact,
}: ContactEditDialogProps) {
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  const isSuperadmin = isSuperadminUser(session?.user);
  const { data: accessTypes = [] } = useContactAccessTypes();

  const { data: companyOptions = [] } = useQuery({
    queryKey: ['companies-select'],
    queryFn: getCompaniesSelect,
    enabled: open && isSuperadmin,
    staleTime: 1000 * 60 * 5,
  });

  const { data: contactCompanies = [], isFetched: companiesFetched } = useQuery({
    queryKey: ['contact-companies', contact?.id],
    queryFn: async () => {
      const response = await apiFetch(`/api/user-management/contacts/${contact!.id}/companies`);
      if (!response.ok) throw new Error('Failed to fetch contact companies');
      return response.json() as Promise<{ id: string; name: string; code: string }[]>;
    },
    enabled: open && !!contact?.id && isSuperadmin,
    staleTime: 1000 * 60,
  });

  const form = useForm<ContactEditSchemaType>({
    resolver: zodResolver(ContactEditSchema) as Resolver<ContactEditSchemaType>,
    defaultValues: {
      phone_number: contact?.phone_number || '',
      name: contact?.name || '',
      workspace_id: contact?.workspace_id || '',
      access_type_codes: contact?.access_type_codes ?? [],
      company_ids: [],
      requires_registered_project: contact?.requires_registered_project ?? false,
    },
    mode: 'onTouched',
  });

  const companiesResetRef = useRef<string | null>(null);
  useEffect(() => {
    if (open && contact) {
      form.reset({
        phone_number: contact.phone_number || '',
        name: contact.name || '',
        workspace_id: contact.workspace_id || '',
        access_type_codes: contact.access_type_codes ?? [],
        company_ids: [],
        requires_registered_project: contact.requires_registered_project ?? false,
      });
    }
    if (!open) {
      companiesResetRef.current = null;
    }
  }, [open, contact, form]);

  // Companies aren't carried on the `contact` prop, so apply them once the grant
  // fetch resolves. Guarded per contact-open so a background refetch can't clobber edits.
  useEffect(() => {
    if (!open || !isSuperadmin || !contact?.id || !companiesFetched) return;
    if (companiesResetRef.current === contact.id) return;
    companiesResetRef.current = contact.id;
    form.setValue('company_ids', contactCompanies.map((c) => c.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- form is stable; guard prevents loops
  }, [open, isSuperadmin, contact?.id, companiesFetched, contactCompanies]);

  const {
    data: workspaces = [],
    error: workspacesError,
    refetch: refetchWorkspaces,
  } = useQuery({
    queryKey: ['respond-workspace-select'],
    queryFn: listRespondWorkspaceSelect,
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });

  const workspaceOptions = useMemo(
    () =>
      workspaces.map((w) => ({
        value: w.id,
        label: w.name?.trim() ? w.name : `Workspace ${w.space_id}`,
        description: `Space ID: ${w.space_id}${w.is_default ? ' • default' : ''}`,
        searchText: `${w.name ?? ''} ${w.space_id}`.trim(),
      })),
    [workspaces],
  );

  const mutation = useMutation({
    mutationFn: async (values: ContactEditSchemaType) => {
      const response = await apiFetch(`/api/user-management/contacts/${contact.id}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          phone_number: values.phone_number,
          name: values.name || null,
          workspace_id: values.workspace_id ? values.workspace_id : null,
          access_type_codes: values.access_type_codes ?? [],
          requires_registered_project: values.requires_registered_project ?? false,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail?.message || error.message || 'Failed to update contact');
      }

      const updated = await response.json();

      // Superadmin-only: sync company grants (delete-all-then-reinsert server-side).
      if (isSuperadmin) {
        const companiesResponse = await apiFetch(`/api/user-management/contacts/${contact.id}/companies`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ company_ids: values.company_ids ?? [] }),
        });
        if (!companiesResponse.ok) {
          throw new Error(await extractApiError(companiesResponse, 'Failed to update companies'));
        }
      }

      return updated;
    },
    onSuccess: () => {
      const message = 'Contact updated successfully.';
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

      // Invalidate queries to refresh the data
      queryClient.invalidateQueries({ queryKey: ['respond-contact', contact.id] });
      queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
      queryClient.invalidateQueries({ queryKey: ['contact-companies', contact.id] });

      closeDialog();
    },
    onError: (error: Error) => {
      const message = error.message || 'Failed to update contact. Please try again.';
      toast.custom(
        () => (
          <Alert variant="mono" icon="destructive">
            <AlertIcon>
              <RiErrorWarningFill />
            </AlertIcon>
            <AlertTitle>{message}</AlertTitle>
          </Alert>
        ),
        {
          position: 'top-center',
        },
      );
    },
  });

  const handleSubmit = (values: ContactEditSchemaType) => {
    mutation.mutate(values);
  };

  return (
    <Dialog open={open} onOpenChange={closeDialog}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Edit Contact</DialogTitle>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-5">
            <FormField
              control={form.control}
              name="phone_number"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Phone Number</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      id="phone_number"
                      type="tel"
                      placeholder="+1234567890"
                      disabled={mutation.isPending}
                    />
                  </FormControl>
                  <FormDescription>
                    Enter phone number in E.164 format (e.g., +1234567890)
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      id="name"
                      type="text"
                      placeholder="Contact name"
                      value={field.value || ''}
                      disabled={mutation.isPending}
                    />
                  </FormControl>
                  <FormDescription>
                    Optional: Contact name from Respond.io
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="access_type_codes"
              render={({ field }) => {
                const selected = new Set(field.value ?? []);
                return (
                  <FormItem>
                    <FormLabel>Access types</FormLabel>
                    <FormControl>
                      <div className="flex flex-wrap gap-3">
                        {accessTypes.length === 0 ? (
                          <span className="text-xs text-muted-foreground">
                            No access types configured. Add some under User Management → Contact Access Types.
                          </span>
                        ) : (
                          accessTypes.map((opt) => {
                            const id = `edit-access-${opt.code}`;
                            return (
                              <label
                                key={opt.code}
                                htmlFor={id}
                                className="flex items-center gap-2 text-sm border rounded-md px-2 py-1 cursor-pointer hover:bg-accent"
                              >
                                <Checkbox
                                  id={id}
                                  checked={selected.has(opt.code)}
                                  onCheckedChange={(v) => {
                                    const next = new Set(selected);
                                    if (v === true) next.add(opt.code);
                                    else next.delete(opt.code);
                                    field.onChange(Array.from(next));
                                  }}
                                  disabled={mutation.isPending}
                                />
                                <span>{opt.name}</span>
                              </label>
                            );
                          })
                        )}
                      </div>
                    </FormControl>
                    <FormDescription>
                      Pick one or more access types. Promotions and attachments are visible to this contact when their access levels overlap.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                );
              }}
            />

            {isSuperadmin && (
              <FormField
                control={form.control}
                name="company_ids"
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
                    <FormDescription>
                      Which companies this contact belongs to (scopes their n8n/WhatsApp data access).
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <FormField
              control={form.control}
              name="requires_registered_project"
              render={({ field }) => (
                <FormItem className="flex items-start justify-between gap-3 rounded-lg border border-border px-3 py-2.5">
                  <div className="min-w-0 space-y-0.5">
                    <FormLabel>Sponsorship must name a registered project</FormLabel>
                    <FormDescription>
                      When on, this contact&apos;s sponsorship form shows a project picker
                      and cannot be submitted without one. Off keeps today&apos;s
                      free-text project title.
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Switch
                      checked={Boolean(field.value)}
                      onCheckedChange={(next) => field.onChange(next)}
                    />
                  </FormControl>
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="workspace_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Respond.io Workspace</FormLabel>
                  <FormControl>
                    <SearchableSelect
                      value={field.value ?? ''}
                      onChange={(v) => field.onChange(v || null)}
                      options={workspaceOptions}
                      loadError={workspacesError}
                      onRetry={() => void refetchWorkspaces()}
                      placeholder={
                        workspaceOptions.length === 0
                          ? 'No workspaces configured'
                          : 'Select workspace…'
                      }
                      emptyMessage="No matching workspaces"
                      clearable
                      disabled={mutation.isPending || workspaceOptions.length === 0}
                    />
                  </FormControl>
                  <FormDescription>
                    Workspace this contact belongs to. Configure workspaces under{' '}
                    <span className="font-medium">System Management → Respond.io Workspaces</span>.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={closeDialog}
                disabled={mutation.isPending}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending && <LoaderCircleIcon className="mr-2 h-4 w-4 animate-spin" />}
                Update Contact
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
