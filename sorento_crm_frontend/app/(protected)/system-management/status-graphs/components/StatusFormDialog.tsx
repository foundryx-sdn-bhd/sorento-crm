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
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useCreateStatus, useUpdateStatus } from '../hooks/useStatusGraphs';
import type { Status } from '../types/statusGraph.types';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  entityType: string;
  scopeId: string | null;
  /** Undefined = create. */
  status?: Status;
}

const BLANK = {
  key: '',
  label: '',
  category: '',
  color_hex: '',
  description: '',
  sort_order: 0,
  is_initial: false,
  is_terminal: false,
  is_active: true,
  is_default: false,
  is_archived: false,
};

export default function StatusFormDialog({
  open,
  onOpenChange,
  entityType,
  scopeId,
  status,
}: Props) {
  const isEdit = Boolean(status);
  // The order box holds what was typed, so it may be empty until Save checks it.
  type FormState = Omit<typeof BLANK, 'sort_order'> & { sort_order: number | null };
  const [form, setForm] = useState<FormState>(BLANK);
  const [checked, setChecked] = useState(false);
  const sortError = numberFieldError(form.sort_order, { integer: true });

  const createMutation = useCreateStatus(entityType, scopeId);
  const updateMutation = useUpdateStatus(entityType, scopeId);
  const saving = createMutation.isPending || updateMutation.isPending;

  useEffect(() => {
    if (!open) return;
    setChecked(false);
    setForm(
      status
        ? {
            key: status.key,
            label: status.label,
            category: status.category ?? '',
            color_hex: status.color_hex ?? '',
            description: status.description ?? '',
            sort_order: status.sort_order,
            is_initial: status.is_initial,
            is_terminal: status.is_terminal,
            is_active: status.is_active,
            is_default: status.is_default,
            is_archived: status.is_archived,
          }
        : BLANK,
    );
  }, [open, status]);

  const set = <K extends keyof FormState>(field: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [field]: value }));

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (sortError) {
      setChecked(true);
      return;
    }
    const payload = {
      key: form.key.trim(),
      label: form.label.trim(),
      category: form.category.trim() || null,
      color_hex: form.color_hex.trim() || null,
      description: form.description.trim() || null,
      sort_order: Number(form.sort_order),
      is_initial: form.is_initial,
      is_terminal: form.is_terminal,
      is_active: form.is_active,
      is_default: form.is_default,
      is_archived: form.is_archived,
    };
    const done = { onSuccess: () => onOpenChange(false) };
    if (status) {
      updateMutation.mutate({ id: status.id, body: payload }, done);
    } else {
      createMutation.mutate({ entity_type: entityType, scope_id: scopeId, ...payload }, done);
    }
  };

  // A system status is a seeded machine contract: code looks it up by key and
  // branches on these two flags, so both are frozen. Cosmetics stay editable.
  const lockedBySystem = Boolean(status?.is_system);
  const canSubmit = form.key.trim().length > 0 && form.label.trim().length > 0 && !saving;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit status' : 'Add status'}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {lockedBySystem && (
            <p className="rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground">
              This is a system status. Its key and its starting/final flags are fixed because
              code depends on them; you can still change how it is labelled and ordered.
            </p>
          )}

          <div className="grid gap-2">
            <Label htmlFor="status-key">Key</Label>
            <Input
              id="status-key"
              value={form.key}
              onChange={(e) => set('key', e.target.value)}
              placeholder="po_received"
              disabled={lockedBySystem}
              required
            />
            <p className="text-xs text-muted-foreground">
              The machine name, stable across graphs. Reports group by this, so a template
              that copies this graph must keep the same key for the same step.
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="status-label">Label</Label>
            <Input
              id="status-label"
              value={form.label}
              onChange={(e) => set('label', e.target.value)}
              placeholder="PO Received"
              required
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="status-order">Order</Label>
              <NumberInput
                id="status-order"
                value={form.sort_order}
                onValueChange={(v) => set('sort_order', v)}
                error={checked ? sortError : undefined}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="status-colour">Colour</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="status-colour"
                  type="color"
                  value={form.color_hex || '#71717a'}
                  onChange={(e) => set('color_hex', e.target.value)}
                  className="h-9 w-14 p-1"
                />
                <Input
                  aria-label="Colour hex value"
                  value={form.color_hex}
                  onChange={(e) => set('color_hex', e.target.value)}
                  placeholder="#71717a"
                />
              </div>
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="status-description">Description</Label>
            <Textarea
              id="status-description"
              value={form.description}
              onChange={(e) => set('description', e.target.value)}
              rows={2}
              placeholder="What this step means, for whoever configures it next."
            />
          </div>

          <div className="space-y-3 rounded-md border p-3">
            <ToggleRow
              id="status-initial"
              label="Starting state"
              hint="New records begin here. Exactly one per graph."
              checked={form.is_initial}
              disabled={lockedBySystem || form.is_terminal}
              onChange={(v) => set('is_initial', v)}
            />
            <ToggleRow
              id="status-terminal"
              label="Final state"
              hint="Nothing moves on from here."
              checked={form.is_terminal}
              disabled={lockedBySystem || form.is_initial}
              onChange={(v) => set('is_terminal', v)}
            />
            <ToggleRow
              id="status-active"
              label="Active"
              hint="Inactive statuses stay on existing records but accept no new ones."
              checked={form.is_active}
              onChange={(v) => set('is_active', v)}
            />
            <ToggleRow
              id="status-default"
              label="Pre-selected"
              hint="Highlighted first in a status picker."
              checked={form.is_default}
              onChange={(v) => set('is_default', v)}
            />
            <ToggleRow
              id="status-archived"
              label="Archived"
              hint="Records here drop out of the default list view."
              checked={form.is_archived}
              onChange={(v) => set('is_archived', v)}
            />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {saving && <LoaderCircleIcon className="size-4 animate-spin" />}
              {isEdit ? 'Save changes' : 'Add status'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ToggleRow({
  id,
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <Label htmlFor={id} className="text-sm">
          {label}
        </Label>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      <Switch id={id} checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </div>
  );
}
