'use client';

import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
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
import {
  useContactFieldReveals,
  useFieldRevealKeys,
  useSetContactFieldReveals,
} from '../hooks/useContactFieldReveals';
import type { FieldRevealKey } from '../services/contactFieldRevealService';

/**
 * Contact Details -> Access -> Field reveals (chatbot growth r1, Slice C2, AC-963, AC-965).
 *
 * A restricted field (sellable stock, a PO's supplier) is off for every contact
 * until granted here - D3/D4. Keys and labels come from the backend
 * (`GET field-reveal-keys`), never hardcoded, so a new restricted field a
 * presenter adds appears in this list after the next catalog sync with no FE
 * change (AC-964).
 *
 * Turning a key ON is one click - PUT the full granted list with the key added.
 * Turning one OFF stops the chatbot revealing it to this contact, so it goes
 * through an AlertDialog first, the same pattern the Media Access card above
 * uses for its own toggle-off.
 */
export default function ContactFieldRevealsSection({ contactId }: { contactId: string }) {
  const { data: keys, isLoading: keysLoading, isError: keysFailed } = useFieldRevealKeys();
  const {
    data: granted,
    isLoading: grantedLoading,
    isError: grantedFailed,
  } = useContactFieldReveals(contactId);
  const update = useSetContactFieldReveals(contactId);

  const [confirmingOff, setConfirmingOff] = useState<FieldRevealKey | null>(null);

  const grantedSet = new Set(granted ?? []);

  function setGranted(key: string, allowed: boolean) {
    const next = new Set(grantedSet);
    if (allowed) next.add(key);
    else next.delete(key);
    update.mutate([...next]);
  }

  function toggle(item: FieldRevealKey, checked: boolean) {
    if (!checked) {
      setConfirmingOff(item);
      return;
    }
    setGranted(item.key, true);
  }

  if (keysLoading || grantedLoading) {
    return <Skeleton className="h-24 w-full" />;
  }

  if (keysFailed) {
    return (
      <p className="text-sm text-destructive">
        Field reveals could not be loaded. Reload the page to try again.
      </p>
    );
  }

  // A failed grants read must never let a toggle fire off the wrong base state -
  // `grantedSet` below would silently read every key as ungranted and a save
  // would revert whatever this contact actually holds.
  if (grantedFailed) {
    return (
      <p className="text-sm text-destructive">
        This contact&apos;s field reveals could not be loaded. Reload the page to try again.
      </p>
    );
  }

  if (!keys || keys.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No restricted field exists yet - nothing here needs a grant.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {keys.map((item) => {
        const on = grantedSet.has(item.key);
        const switchId = `field-reveal-${item.key}`;
        return (
          <div
            key={item.key}
            className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium truncate">{item.label}</p>
              {item.prompt_blocks && item.prompt_blocks.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Also removes from the chatbot prompt: {item.prompt_blocks.join(', ')}
                </p>
              )}
              {on ? (
                <Badge variant="success" appearance="light" size="sm" className="mt-0.5">
                  Revealed
                </Badge>
              ) : (
                <Badge variant="secondary" appearance="light" size="sm" className="mt-0.5">
                  Hidden
                </Badge>
              )}
            </div>
            <Switch
              id={switchId}
              aria-label={`Reveal ${item.label.toLowerCase()}`}
              checked={on}
              // `granted === undefined` is belt and suspenders alongside the
              // `grantedFailed` early return above: no PUT can ever fire off an
              // unknown base state, even if that guard is ever refactored away.
              disabled={update.isPending || granted === undefined}
              onCheckedChange={(checked) => toggle(item, checked)}
            />
          </div>
        );
      })}

      <AlertDialog
        open={confirmingOff !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmingOff(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Hide {confirmingOff ? confirmingOff.label.toLowerCase() : ''}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              The chatbot will stop telling this contact{' '}
              {confirmingOff ? confirmingOff.label.toLowerCase() : ''} in its answers.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={update.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                if (!confirmingOff) return;
                setGranted(confirmingOff.key, false);
                setConfirmingOff(null);
              }}
              disabled={update.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Hide
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
