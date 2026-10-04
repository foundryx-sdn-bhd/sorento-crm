'use client';

import { useState } from 'react';
import { Info, Pencil, RefreshCw } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { apiFetch } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardHeader,
  CardHeading,
  CardTitle,
  CardToolbar,
} from '@/components/ui/card';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { useContact } from './components/contact-context';
import ContactCustomersSection from './components/ContactCustomersSection';
import ContactUserAccountSection from './components/ContactUserAccountSection';
import ContactMarketSegmentSection from './components/ContactMarketSegmentSection';
import ContactAttachmentTypesSection from './components/ContactAttachmentTypesSection';
import ContactPortalFormsSection from './components/ContactPortalFormsSection';
import ContactEditDialog from './components/ContactEditDialog';
import ContactBrandScopeSection from '@/components/contacts/ContactBrandScopeSection';
import { StockVisibilitySection } from '@/components/stock-visibility/StockVisibilitySection';
import { SpecVisibilitySection } from '@/components/spec-visibility/SpecVisibilitySection';

export default function ContactProfilePage() {
  const { contact, isLoading, contactId } = useContact();
  const queryClient = useQueryClient();
  const [editDialogOpen, setEditDialogOpen] = useState(false);

  // Superadmin-only endpoint; a 403 for anyone else resolves to an empty list
  // rather than an error toast, so the section simply reads "None" for them.
  const { data: companies = [], isLoading: companiesLoading } = useQuery({
    queryKey: ['contact-companies', contactId],
    queryFn: async () => {
      const response = await apiFetch(`/api/user-management/contacts/${contactId}/companies`);
      if (!response.ok) return [] as { id: string; name: string }[];
      return (await response.json()) as { id: string; name: string }[];
    },
    enabled: !!contactId,
  });

  const syncContactMutation = useMutation({
    mutationFn: async (id: string) => {
      const response = await apiFetch(`/api/user-management/contacts/${id}/sync`, {
        method: 'POST',
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail?.message || error.message || 'Failed to sync contact');
      }
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['respond-contact', contactId] });
      queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
      toast.success('Contact synced successfully');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to sync contact');
    },
  });

  const handleSync = () => {
    if (contact) {
      syncContactMutation.mutate(contact.id);
    }
  };

  if (isLoading || !contact) {
    return <Skeleton className="h-96 w-full" />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardHeading>
            <CardTitle>Contact Information</CardTitle>
          </CardHeading>
          <CardToolbar>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditDialogOpen(true)}
              className="h-6 w-6 p-0"
              aria-label="Edit contact"
              title="Edit contact"
            >
              <Pencil className="h-3 w-3" />
            </Button>
          </CardToolbar>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Phone Number and Respond.io Workspace are editable, so they belong on
                the card the pencil opens - the hero only echoes the phone as the
                record's identity line. */}
            <div>
              <p className="text-sm text-muted-foreground">Phone Number</p>
              <p className="font-medium font-mono">{contact.phone_number}</p>
            </div>
            <div>
              <div className="flex items-center justify-between mb-1">
                <p className="text-sm text-muted-foreground">Name</p>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleSync}
                  disabled={syncContactMutation.isPending}
                  className="h-6 w-6 p-0"
                  aria-label="Sync from Respond.io"
                  title="Sync from Respond.io"
                >
                  <RefreshCw
                    className={`h-3 w-3 ${syncContactMutation.isPending ? 'animate-spin' : ''}`}
                  />
                </Button>
              </div>
              <p className="font-medium">
                {contact.name || <span className="text-muted-foreground">Not set</span>}
              </p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">First name</p>
              <p className="font-medium">
                {contact.first_name || <span className="text-muted-foreground">-</span>}
              </p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Last name</p>
              <p className="font-medium">
                {contact.last_name || <span className="text-muted-foreground">-</span>}
              </p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Access types</p>
              {contact.access_types && contact.access_types.length > 0 ? (
                <div className="flex flex-wrap gap-1 mt-1">
                  {contact.access_types.map((t) => (
                    <Badge key={t.code} variant="secondary" className="font-normal">
                      {t.name}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="font-medium text-muted-foreground">None assigned</p>
              )}
            </div>
            {/* Companies decide WHICH ROWS this contact can be told about, before
                any field-level access applies. A contact with none is answered
                with nothing at all - so when an agent replies "no incoming
                stock" for a container that plainly exists, this is the first
                field to look at. Worth showing beside the access types rather
                than only inside the edit dialog. */}
            <div>
              <p className="text-sm text-muted-foreground">Companies</p>
              {companiesLoading ? (
                <Skeleton className="h-5 w-24 mt-1" />
              ) : companies.length > 0 ? (
                <div className="flex flex-wrap gap-1 mt-1">
                  {companies.map((c) => (
                    <Badge key={c.id} variant="secondary" className="font-normal">
                      {c.name}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="font-medium text-destructive">
                  None - this contact receives no records
                </p>
              )}
            </div>
            <ContactMarketSegmentSection contactId={contactId} />
            <ContactAttachmentTypesSection contactId={contactId} />
            <ContactPortalFormsSection contactId={contactId} />
            {/* Which locations the chatbot may quote to this contact, and in which
                answer shape. Full width: the two pickers do not fit a grid cell at
                375px without clipping their chips. */}
            <div className="md:col-span-2">
              <StockVisibilitySection scope={{ kind: 'contact', contactId }} />
            </div>
            {/* Which brands the chatbot and MCP may tell this contact about. Empty = all. */}
            <div className="md:col-span-2">
              <ContactBrandScopeSection contactId={contactId} />
            </div>
            {/* Which product spec keys (Thickness, Material, ...) the chatbot may
                reveal to this contact. Sits directly under Stock visibility, same
                full-width reasoning. */}
            <div className="md:col-span-2">
              <SpecVisibilitySection scope={{ kind: 'contact', contactId }} />
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Respond.io Workspace</p>
              {contact.workspace_id ? (
                <div className="flex items-center gap-1">
                  <p className="font-medium">
                    {contact.workspace_name?.trim()
                      ? contact.workspace_name
                      : `Workspace ${contact.workspace_space_id ?? ''}`.trim() || '-'}
                  </p>
                  {contact.workspace_space_id ? (
                    <Popover>
                      <PopoverTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 w-6 p-0"
                          aria-label="Show space ID"
                        >
                          <Info className="h-3.5 w-3.5 text-muted-foreground" />
                        </Button>
                      </PopoverTrigger>
                      <PopoverContent align="start" className="w-auto px-3 py-2 text-xs">
                        <span className="text-muted-foreground">Space ID: </span>
                        <span className="font-mono">{contact.workspace_space_id}</span>
                      </PopoverContent>
                    </Popover>
                  ) : null}
                </div>
              ) : (
                <p className="font-medium text-muted-foreground">Not set</p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      <ContactCustomersSection contactId={contactId} />

      <ContactUserAccountSection contact={contact} />

      <ContactEditDialog
        open={editDialogOpen}
        closeDialog={() => setEditDialogOpen(false)}
        contact={contact}
      />
    </div>
  );
}
