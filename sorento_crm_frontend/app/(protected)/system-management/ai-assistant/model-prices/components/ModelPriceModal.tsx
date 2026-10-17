'use client';

import { useEffect, useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useCreateModelPrice, useUpdateModelPrice } from '../../hooks/useModelPrices';
import { formatPrice } from '../../lib/formatUsd';
import type { ModelPrice, ModelPriceInput } from '../../services/modelPriceService';

interface Draft {
  model: string;
  input: string;
  cached: string;
  output: string;
  note: string;
}

const BLANK: Draft = { model: '', input: '', cached: '', output: '', note: '' };

export interface ModelPriceModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The row being edited; null = add. */
  price: ModelPrice | null;
  /** Add price from an unpriced row: the model is filled and locked. */
  lockedModel?: string | null;
}

function toDraft(price: ModelPrice | null, lockedModel?: string | null): Draft {
  if (price) {
    return {
      model: price.model,
      input: formatPrice(price.input_usd_per_1m),
      cached: price.cached_input_usd_per_1m == null ? '' : formatPrice(price.cached_input_usd_per_1m),
      output: formatPrice(price.output_usd_per_1m),
      note: price.note ?? '',
    };
  }
  return { ...BLANK, model: lockedModel ?? '' };
}

export default function ModelPriceModal({
  open,
  onOpenChange,
  price,
  lockedModel,
}: ModelPriceModalProps) {
  const [draft, setDraft] = useState<Draft>(BLANK);
  // Set by a Save that found a wrong price; the sentences then follow the typing.
  const [checked, setChecked] = useState(false);
  const create = useCreateModelPrice();
  const update = useUpdateModelPrice();
  const isEdit = price !== null;
  const modelLocked = isEdit || Boolean(lockedModel);
  const saving = create.isPending || update.isPending;
  const error = (isEdit ? update : create).error as Error | null | undefined;

  useEffect(() => {
    if (!open) return;
    setDraft(toDraft(price, lockedModel));
    setChecked(false);
    create.reset?.();
    update.reset?.();
    // Re-derive only when the modal opens on a different record.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, price?.id, lockedModel]);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((prev) => ({ ...prev, [key]: value }));

  const inputError = numberFieldError(draft.input, { min: 0 });
  const cachedError = numberFieldError(draft.cached, { min: 0, optional: true });
  const outputError = numberFieldError(draft.output, { min: 0 });
  const canSave = draft.model.trim() !== '' && !saving;

  const handleSave = () => {
    if (!canSave) return;
    if (inputError || cachedError || outputError) {
      setChecked(true);
      return;
    }
    const input: ModelPriceInput = {
      model: draft.model.trim(),
      input_usd_per_1m: Number(draft.input),
      cached_input_usd_per_1m: draft.cached.trim() === '' ? null : Number(draft.cached),
      output_usd_per_1m: Number(draft.output),
      note: draft.note.trim() === '' ? null : draft.note.trim(),
    };
    const onSuccess = () => onOpenChange(false);
    if (price) update.mutate({ id: price.id, input }, { onSuccess });
    else create.mutate(input, { onSuccess });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90dvh] max-w-xl flex-col overflow-hidden p-0">
        <DialogHeader className="border-b px-6 py-4 pr-12">
          <DialogTitle className="truncate">{isEdit ? price.model : 'Add price'}</DialogTitle>
        </DialogHeader>

        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
          <div className="space-y-1.5">
            <Label htmlFor="price-model">Model</Label>
            <Input
              id="price-model"
              value={draft.model}
              onChange={(e) => set('model', e.target.value)}
              disabled={modelLocked}
              placeholder="gpt-5.4-mini"
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="price-input">Input (USD per 1M)</Label>
              <NumberInput
                id="price-input"
                min={0}
                step="any"
                value={draft.input}
                onChange={(e) => set('input', e.target.value)}
                error={checked ? inputError : undefined}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="price-cached">Cached input (USD per 1M)</Label>
              <NumberInput
                id="price-cached"
                min={0}
                step="any"
                value={draft.cached}
                onChange={(e) => set('cached', e.target.value)}
                error={checked ? cachedError : undefined}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="price-output">Output (USD per 1M)</Label>
              <NumberInput
                id="price-output"
                min={0}
                step="any"
                value={draft.output}
                onChange={(e) => set('output', e.target.value)}
                error={checked ? outputError : undefined}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="price-note">Note</Label>
            <Textarea
              id="price-note"
              value={draft.note}
              onChange={(e) => set('note', e.target.value)}
              rows={2}
            />
          </div>
          {error ? <p className="text-sm text-destructive">{error.message}</p> : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t px-6 py-4">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={handleSave} disabled={!canSave}>
            {saving && <LoaderCircleIcon className="size-4 animate-spin" />}
            Save
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
