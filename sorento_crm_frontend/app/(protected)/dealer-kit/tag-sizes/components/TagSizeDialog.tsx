'use client';

/**
 * Create/edit modal for a saved tag size (S4, AC-S4-5).
 *
 * Name, width (mm), height (mm). `size` is null for create, the row for edit -
 * same dual-mode shape `CollectionDialog` and `TileDesignDialog` already use.
 */

import { useEffect, useState } from 'react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { useCreateTagSize, useUpdateTagSize } from '../hooks/useTagSizes';
import type { TagSizeRecord } from '../../services/tagSizeService';

const MIN_MM = 10;

export function TagSizeDialog({
  open,
  onOpenChange,
  size,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Null creates a new size. */
  size: TagSizeRecord | null;
}) {
  const [name, setName] = useState('');
  const [widthMm, setWidthMm] = useState<number | null>(MIN_MM);
  const [heightMm, setHeightMm] = useState<number | null>(MIN_MM);
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const createMutation = useCreateTagSize();
  const updateMutation = useUpdateTagSize();
  const saving = createMutation.isPending || updateMutation.isPending;

  useEffect(() => {
    if (!open) return;
    setName(size?.name ?? '');
    setWidthMm(size?.width_mm ?? MIN_MM);
    setHeightMm(size?.height_mm ?? MIN_MM);
    setError(null);
    setChecked(false);
  }, [open, size]);

  const canSubmit = name.trim().length > 0 && !saving;
  const widthError = checked ? numberFieldError(widthMm, { min: MIN_MM }) : undefined;
  const heightError = checked ? numberFieldError(heightMm, { min: MIN_MM }) : undefined;

  const submit = async () => {
    setError(null);
    setChecked(true);
    if (numberFieldError(widthMm, { min: MIN_MM }) || numberFieldError(heightMm, { min: MIN_MM })) return;
    const width = parseNumberDraft(widthMm) as number;
    const height = parseNumberDraft(heightMm) as number;
    try {
      if (size) {
        await updateMutation.mutateAsync({
          id: size.id,
          input: { name: name.trim(), width_mm: width, height_mm: height },
        });
        toast.success('Tag size updated');
      } else {
        await createMutation.mutateAsync({
          name: name.trim(),
          width_mm: width,
          height_mm: height,
        });
        toast.success('Tag size created');
      }
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this tag size');
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{size ? 'Edit tag size' : 'New tag size'}</DialogTitle>
          <DialogDescription>
            A named width x height offered in every request&apos;s Tag Size dropdown.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor="ts-name">Name</Label>
            <Input
              id="ts-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Shelf rail"
              autoFocus
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-2">
              <Label htmlFor="ts-width">Width (mm)</Label>
              <NumberInput
                id="ts-width"
                value={widthMm}
                min={MIN_MM}
                onValueChange={setWidthMm}
                error={widthError}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="ts-height">Height (mm)</Label>
              <NumberInput
                id="ts-height"
                value={heightMm}
                min={MIN_MM}
                onValueChange={setHeightMm}
                error={heightError}
              />
            </div>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {saving ? 'Saving...' : size ? 'Save' : 'Create size'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
