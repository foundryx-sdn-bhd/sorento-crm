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
import { useIdeaMutations, useIdeasQuery } from '@/hooks/useIdeas';
import type { Idea } from '@/types/ideas';

/**
 * Merge this idea into another: the picked idea survives and this one becomes its merged child.
 * A survivor holds children and never becomes one (single level), so only plain ideas are offered.
 */
export function IdeaMergeModal({
  idea,
  open,
  onOpenChange,
}: {
  idea: Idea;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: list } = useIdeasQuery({});
  const { merge } = useIdeaMutations();
  const [survivorId, setSurvivorId] = useState('');

  useEffect(() => {
    if (open) setSurvivorId('');
  }, [open]);

  const options = useMemo(
    () =>
      (list ?? [])
        .filter((i) => i.id !== idea.id && !i.mergedIntoId)
        .map((i) => ({
          value: i.id,
          label: [i.ideaNumber, i.title ?? i.problem].filter(Boolean).join(' - '),
        })),
    [list, idea.id],
  );

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!survivorId || merge.isPending) return;
    try {
      await merge.mutateAsync({ survivorId, ideaIds: [idea.id, survivorId] });
      onOpenChange(false);
    } catch {
      // The hook toasted the reason; the modal stays open.
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Merge into another idea</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogBody className="flex flex-col gap-1.5">
            <Label htmlFor="idea-merge-target">Idea to keep</Label>
            <SearchableSelect
              id="idea-merge-target"
              value={survivorId}
              onChange={setSurvivorId}
              options={options}
              placeholder="Pick an idea"
              emptyMessage="No other ideas."
              wrapOptions
            />
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!survivorId || merge.isPending}>
              {merge.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
              Merge
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
