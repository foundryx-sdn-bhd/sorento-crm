'use client';

import { useEffect, useMemo, useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import type { Idea } from '@/types/ideas';

/**
 * Merge the selected ideas: the survivor is picked among the selected ideas themselves, and the
 * rest become its merged children (ss `survivorId` must be one of `ideaIds`).
 */
export function IdeasBulkMergeDialog({
  ideas,
  open,
  onOpenChange,
  onMerge,
}: {
  ideas: Idea[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMerge: (survivorId: string, ideas: Idea[]) => Promise<void>;
}) {
  const [survivorId, setSurvivorId] = useState('');
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (open) setSurvivorId('');
  }, [open]);

  const options = useMemo(
    () =>
      ideas.map((i) => ({
        value: i.id,
        label: [i.ideaNumber, i.title ?? i.problem].filter(Boolean).join(' - '),
      })),
    [ideas],
  );

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!survivorId || pending) return;
    setPending(true);
    try {
      await onMerge(survivorId, ideas);
      onOpenChange(false);
    } catch {
      // The caller toasted the reason; the dialog stays open.
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Merge ideas</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogBody className="flex flex-col gap-1.5">
            <Label htmlFor="ideas-merge-survivor">Idea to keep</Label>
            <SearchableSelect
              id="ideas-merge-survivor"
              value={survivorId}
              onChange={setSurvivorId}
              options={options}
              placeholder="Pick an idea"
              emptyMessage="No ideas."
              wrapOptions
            />
          </DialogBody>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={!survivorId || pending}
            >
              {pending ? (
                <LoaderCircleIcon className="size-4 animate-spin" />
              ) : null}
              Merge
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
