'use client';

import { useEffect, useMemo, useState } from 'react';
import { Pencil } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { apiFetch } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
} from '@/components/ui/alert-dialog';

type AttachmentTypeGrant = {
  id: string;
  type_name: string;
  code: string | null;
  is_direct_access: boolean;
  granted: boolean;
};

/**
 * Contact Details → Document types section.
 *
 * The WhatsApp agent can only hand over documents whose type is dealer-facing
 * (`is_direct_access`) - one global flag, so a document is public to every
 * contact or reachable by none. This section adds the per-contact axis: granting
 * a type here lets THIS contact retrieve it, without opening it to everyone.
 *
 * Baseline types are shown ticked and disabled. They are not a grant we made and
 * not one we can withdraw here, but hiding them would leave an admin wondering
 * why a contact receives files it was never granted.
 */
export default function ContactAttachmentTypesSection({ contactId }: { contactId: string }) {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>([]);

  const { data, isLoading } = useQuery({
    queryKey: ['contact-attachment-types', contactId],
    queryFn: async () => {
      const response = await apiFetch(
        `/api/user-management/contacts/${contactId}/attachment-types`,
      );
      if (!response.ok) throw new Error('Failed to load document types');
      return (await response.json()) as { data: AttachmentTypeGrant[]; granted_ids: string[] };
    },
    enabled: !!contactId,
  });

  const catalog = useMemo(() => data?.data ?? [], [data]);
  const granted = useMemo(() => catalog.filter((t) => t.granted), [catalog]);

  const save = useMutation({
    mutationFn: async (ids: string[]) => {
      const response = await apiFetch(
        `/api/user-management/contacts/${contactId}/attachment-types`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ attachment_type_ids: ids }),
        },
      );
      if (!response.ok) throw new Error('Failed to save document types');
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contact-attachment-types', contactId] });
      queryClient.invalidateQueries({ queryKey: ['contact-tag-settings', contactId] });
      toast.success('Document types updated');
      setConfirmOpen(false);
      setDialogOpen(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  useEffect(() => {
    if (dialogOpen) setDraft(granted.map((t) => t.id));
  }, [dialogOpen, granted]);

  const removed = useMemo(
    () => granted.filter((t) => !draft.includes(t.id)),
    [granted, draft],
  );

  function toggle(id: string, checked: boolean) {
    setDraft((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return Array.from(next);
    });
  }

  function onSave() {
    if (removed.length > 0) {
      setConfirmOpen(true);
      return;
    }
    save.mutate(draft);
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <p className="text-sm text-muted-foreground">Document types</p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setDialogOpen(true)}
          className="h-6 w-6 p-0"
          aria-label="Edit document types"
        >
          <Pencil className="h-3 w-3" />
        </Button>
      </div>

      {isLoading ? (
        <Skeleton className="h-6 w-40" />
      ) : granted.length > 0 ? (
        <div className="flex flex-wrap gap-1 mt-1">
          {granted.map((t) => (
            <Badge key={t.id} variant="secondary" className="font-normal">
              {t.type_name}
            </Badge>
          ))}
        </div>
      ) : (
        <p className="font-medium text-muted-foreground">
          Dealer-facing documents only
        </p>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit document types</DialogTitle>
            <DialogDescription>
              Types marked &quot;All contacts&quot; reach everyone already and cannot be revoked
              here. Anything else you tick is retrievable by this contact alone.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2 py-2">
            {catalog.length === 0 ? (
              <span className="text-xs text-muted-foreground">
                No document types configured.
              </span>
            ) : (
              catalog.map((opt) => {
                const inputId = `contact-attachment-type-${opt.id}`;
                return (
                  <label
                    key={opt.id}
                    htmlFor={inputId}
                    className="flex items-center gap-2 text-sm border rounded-md px-2 py-1.5 cursor-pointer hover:bg-accent"
                  >
                    <Checkbox
                      id={inputId}
                      checked={opt.is_direct_access || draft.includes(opt.id)}
                      onCheckedChange={(v) => toggle(opt.id, v === true)}
                      disabled={opt.is_direct_access || save.isPending}
                    />
                    <span className="min-w-0 break-words">{opt.type_name}</span>
                    {opt.is_direct_access ? (
                      <Badge variant="outline" className="ml-auto font-normal shrink-0">
                        All contacts
                      </Badge>
                    ) : null}
                  </label>
                );
              })
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={onSave} disabled={save.isPending}>
              Save attachment types
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm changes</AlertDialogTitle>
            <AlertDialogDescription>
              This will stop this contact receiving{' '}
              {removed.map((t) => t.type_name).join(', ')}. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={save.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                save.mutate(draft);
              }}
              disabled={save.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Remove attachment types
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
