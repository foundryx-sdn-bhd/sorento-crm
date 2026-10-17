'use client';

import { useEffect, useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useCreateTransition, useUpdateTransition } from '../hooks/useStatusGraphs';
import type { Status, StatusTransition } from '../types/statusGraph.types';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  entityType: string;
  scopeId: string | null;
  statuses: Status[];
  /** Undefined = create. */
  transition?: StatusTransition;
}

/**
 * Manual transitions only.
 *
 * Automatic edges carry a rule-engine condition tree, and authoring those needs the
 * RuleBuilder. v1 ships exactly one automatic edge (first project PO recorded, which
 * moves the project to PO Received) and it is seeded in code, so a half-built
 * condition editor here would be dead UI. Automatic edges are listed read-only.
 */
export default function TransitionFormDialog({
  open,
  onOpenChange,
  entityType,
  scopeId,
  statuses,
  transition,
}: Props) {
  const isEdit = Boolean(transition);
  const [fromId, setFromId] = useState('');
  const [toId, setToId] = useState('');
  const [label, setLabel] = useState('');
  const [sortOrder, setSortOrder] = useState<number | null>(0);
  const [checked, setChecked] = useState(false);
  const sortError = numberFieldError(sortOrder, { integer: true });

  const createMutation = useCreateTransition(entityType, scopeId);
  const updateMutation = useUpdateTransition(entityType, scopeId);
  const saving = createMutation.isPending || updateMutation.isPending;

  useEffect(() => {
    if (!open) return;
    setChecked(false);
    setFromId(transition?.from_status_id ?? '');
    setToId(transition?.to_status_id ?? '');
    setLabel(transition?.label ?? '');
    setSortOrder(transition?.sort_order ?? 0);
  }, [open, transition]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (sortError) {
      setChecked(true);
      return;
    }
    const done = { onSuccess: () => onOpenChange(false) };
    if (transition) {
      // From/to are the edge's identity; changing them means a different edge.
      updateMutation.mutate(
        { id: transition.id, body: { label: label.trim(), sort_order: Number(sortOrder) } },
        done,
      );
    } else {
      createMutation.mutate(
        {
          entity_type: entityType,
          from_status_id: fromId,
          to_status_id: toId,
          label: label.trim(),
          sort_order: Number(sortOrder),
          trigger_mode: 'manual',
        },
        done,
      );
    }
  };

  // A final status has no outgoing edges by definition, so it is not offered as a
  // source. The server enforces this too; excluding it here avoids a pointless 422.
  const sourceOptions = statuses
    .filter((s) => !s.is_terminal)
    .map((s) => ({ value: s.id, label: s.label }));
  const targetOptions = statuses
    .filter((s) => s.id !== fromId && s.is_active)
    .map((s) => ({ value: s.id, label: s.label }));

  const canSubmit = Boolean(fromId && toId && label.trim()) && !saving;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit transition' : 'Add transition'}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-2">
            <Label>From</Label>
            <SearchableSelect
              value={fromId}
              onChange={(value) => {
                setFromId(value);
                if (value === toId) setToId('');
              }}
              options={sourceOptions}
              placeholder="Pick a status"
              triggerClassName="w-full"
              disabled={isEdit}
            />
          </div>

          <div className="grid gap-2">
            <Label>To</Label>
            <SearchableSelect
              value={toId}
              onChange={setToId}
              options={targetOptions}
              placeholder="Pick a status"
              triggerClassName="w-full"
              disabled={isEdit}
            />
          </div>

          {isEdit && (
            <p className="text-xs text-muted-foreground">
              The two ends are what identify a transition, so they cannot be changed. Delete
              this one and add another instead.
            </p>
          )}

          <div className="grid gap-2">
            <Label htmlFor="transition-label">Button label</Label>
            <Input
              id="transition-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Quote issued"
              required
            />
            <p className="text-xs text-muted-foreground">
              What the person doing the work sees, so name it as an action.
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="transition-order">Order</Label>
            <NumberInput
              id="transition-order"
              value={sortOrder}
              onValueChange={setSortOrder}
              error={checked ? sortError : undefined}
            />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {saving && <LoaderCircleIcon className="size-4 animate-spin" />}
              {isEdit ? 'Save changes' : 'Add transition'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
