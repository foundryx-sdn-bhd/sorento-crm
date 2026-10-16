'use client';

import { invalidateContactTagSettings } from '@/app/(protected)/user-management/contacts/[id]/hooks/useContactTagSettings';
import { useEffect, useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { Loader2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  getAgentFieldAccess,
  setAgentFieldAccess,
  type AgentFieldAccessRow,
} from '../services/accessAgentService';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentId: string;
  agentName: string;
  contactId: string;
  contactName?: string | null;
}

/** null = follow the agent, true = allow for this contact, false = deny for this contact. */
type Choice = boolean | null;

const CYCLE: Choice[] = [null, true, false];

function label(choice: Choice, inherited: boolean): string {
  if (choice === null) return inherited ? 'Follows agent (allowed)' : 'Follows agent (denied)';
  return choice ? 'Allowed for this contact' : 'Denied for this contact';
}

/**
 * Per-contact exceptions to an agent's field list.
 *
 * Three states, not two. "Denied because the agent denies it" and "denied for this
 * contact specifically" are different intentions: change the agent later and the
 * first follows, the second does not. A two-state checkbox would silently convert
 * every inherited value into an explicit one the moment an admin opened the dialog.
 */
export default function ContactFieldAccessDialog({
  open,
  onOpenChange,
  agentId,
  agentName,
  contactId,
  contactName,
}: Props) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Record<string, Choice>>({});

  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-field-access', agentId, contactId],
    queryFn: () => getAgentFieldAccess(agentId, contactId),
    enabled: open && !!agentId && !!contactId,
  });

  const serverState = useMemo(() => {
    const map: Record<string, Choice> = {};
    for (const f of data?.fields ?? []) {
      map[`${f.resource}.${f.field_key}`] = f.override ?? null;
    }
    return map;
  }, [data]);

  useEffect(() => setDraft(serverState), [serverState]);

  const dirty = useMemo(
    () => Object.keys(serverState).filter((k) => draft[k] !== serverState[k]),
    [draft, serverState],
  );

  const save = useMutation({
    mutationFn: () =>
      setAgentFieldAccess(
        agentId,
        dirty.map((key) => {
          const [resource, ...rest] = key.split('.');
          return {
            resource,
            field_key: rest.join('.'),
            // null clears the override so this contact follows the agent again.
            is_allowed: draft[key],
            contact_id: contactId,
          };
        }),
        contactId,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-field-access', agentId] });
      if (contactId) invalidateContactTagSettings(queryClient, contactId);
      toast.success('Field access updated for this contact');
      onOpenChange(false);
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to update field access'),
  });

  const rows: AgentFieldAccessRow[] = data?.fields ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="break-words">Fields for {contactName || 'this contact'}</DialogTitle>
          <DialogDescription>
            Exceptions to what <span className="font-medium">{agentName}</span> reveals. Leave a
            field on &quot;Follows agent&quot; unless this contact genuinely differs from the rest.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : isError ? (
          <p className="text-sm text-destructive">
            Could not load the field list. Close and reopen to try again.
          </p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            This agent owns no restricted fields, so there is nothing to make an exception to.
          </p>
        ) : (
          <div className="max-h-[55vh] overflow-y-auto pr-1">
            <ul className="flex flex-col gap-1">
              {rows.map((f) => {
                const key = `${f.resource}.${f.field_key}`;
                const choice = draft[key] ?? null;
                const next = CYCLE[(CYCLE.indexOf(choice) + 1) % CYCLE.length];
                return (
                  <li
                    key={key}
                    className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-muted/60"
                  >
                    <span className="min-w-0 truncate text-sm" title={f.field_key}>
                      {f.label}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant={choice === null ? 'outline' : choice ? 'primary' : 'destructive'}
                      // Named per field: twenty-one buttons all reading "Allowed
                      // for this contact" are indistinguishable to a screen reader
                      // (and to anything else driving the page).
                      aria-label={`${f.label}: ${label(choice, f.is_allowed)}`}
                      onClick={() => setDraft((prev) => ({ ...prev, [key]: next }))}
                    >
                      {label(choice, f.is_allowed)}
                    </Button>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        <DialogFooter className="flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <Badge variant="secondary">
            {Object.values(draft).filter((c) => c !== null).length} exception(s)
          </Badge>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={() => save.mutate()} disabled={!dirty.length || save.isPending}>
              {save.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                `Save${dirty.length ? ` (${dirty.length})` : ''}`
              )}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
