'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import {
  createContainerSize,
  getContainerSizes,
  updateContainerSize,
  type ContainerSize,
} from '../../services/fulfilmentService';
import { numberFieldError } from '@/lib/number-field';
import { fmtTrimmedDecimal } from '../../lib/format';

/**
 * Container volumes (AC-E3).
 *
 * Configuration rather than a constant because the loadable volume of a 40HQ is a commercial
 * fact that differs by packing practice, and a client who ships in something else should edit
 * a row instead of waiting for a release.
 *
 * The figure asked for is the LOADABLE volume, not the nominal size printed on the box: a
 * 40HQ is sold as 76 cbm and is seeded here at 65, the figure Ms Tee actually loads to (Q3,
 * migration 428 - it was 68 until then). Planning to the brochure figure is how a container
 * arrives a pallet short of its manifest.
 */

const KEY = ['scm', 'fulfilment', 'container-sizes'] as const;

interface Draft {
  id: string | null;
  code: string;
  label: string;
  cbm: string;
  is_default: boolean;
  /** Set once Save was pressed with a bad volume: the message then follows the field live. */
  showErrors?: boolean;
}

/** A loadable volume must be a number above zero. */
function cbmError(raw: string) {
  return numberFieldError(raw, { min: 0, minExclusive: true });
}

const EMPTY: Draft = { id: null, code: '', label: '', cbm: '', is_default: false };

export function ContainerSizesPanel() {
  const qc = useQueryClient();
  const sizes = useQuery({ queryKey: KEY, queryFn: getContainerSizes });
  const [draft, setDraft] = useState<Draft | null>(null);
  // Delete asks nothing (D7): a toast counts down with Cancel.
  const deletion = useDeferredRowAction({
    actionKey: 'container_size.delete',
    entityType: 'container_size',
    verb: 'Deleting',
    successMessage: 'Container size deleted.',
    invalidateKeys: [[...KEY]],
  });

  const onSettled = (list: ContainerSize[]) => {
    qc.setQueryData(KEY, list);
    setDraft(null);
  };

  const save = useMutation({
    mutationFn: (d: Draft) => {
      const body = {
        code: d.code.trim(),
        label: d.label.trim() || null,
        cbm: Number(d.cbm),
        is_default: d.is_default,
        is_active: true,
      };
      return d.id ? updateContainerSize(d.id, body) : createContainerSize(body);
    },
    onSuccess: (list) => {
      onSettled(list);
      toast.success('Container size saved.');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const hasCode = !!draft && draft.code.trim().length > 0;
  const cbmMessage = draft ? cbmError(draft.cbm) : undefined;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">Container sizes</h3>
          <p className="text-2xs text-muted-foreground">
            The volume a container actually loads. Used by the loading plan.
          </p>
        </div>
        <Button size="sm" onClick={() => setDraft({ ...EMPTY })}>
          <Plus className="size-4" />
          Add size
        </Button>
      </div>

      {sizes.isLoading ? (
        <Skeleton className="h-32 w-full rounded-xl" />
      ) : !sizes.data?.length ? (
        <Card className="p-8 text-center">
          <p className="text-sm font-medium">No container size configured.</p>
          <p className="text-2xs text-muted-foreground">
            Add one and the loading plan can work out how much fits.
          </p>
        </Card>
      ) : (
        <Card className="divide-y divide-border">
          {sizes.data.map((size) => (
            <div key={size.id} className="flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{size.code}</span>
                  {size.is_default ? (
                    <Badge variant="primary" appearance="light">
                      Default
                    </Badge>
                  ) : null}
                </div>
                <p className="truncate text-2xs text-muted-foreground">
                  {size.label ?? 'No description'} · {fmtTrimmedDecimal(size.cbm, 3)} cbm
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  variant="ghost"
                  mode="icon"
                  size="sm"
                  aria-label={`Edit ${size.code}`}
                  onClick={() =>
                    setDraft({
                      id: size.id,
                      code: size.code,
                      label: size.label ?? '',
                      cbm: String(size.cbm),
                      is_default: size.is_default,
                    })
                  }
                >
                  <Pencil className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  mode="icon"
                  size="sm"
                  aria-label={`Delete ${size.code}`}
                  onClick={() => deletion.run({ id: size.id, subject: size.code })}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
        </Card>
      )}

      <Dialog open={!!draft} onOpenChange={(open) => (open ? null : setDraft(null))}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{draft?.id ? 'Edit container size' : 'Add container size'}</DialogTitle>
            <DialogDescription>
              The volume it loads in practice, not the size printed on it.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-3">
            <div>
              <Label htmlFor="container-code">Code</Label>
              <Input
                id="container-code"
                value={draft?.code ?? ''}
                placeholder="40HQ"
                onChange={(e) => setDraft((d) => (d ? { ...d, code: e.target.value } : d))}
              />
            </div>
            <div>
              <Label htmlFor="container-label">Description</Label>
              <Input
                id="container-label"
                value={draft?.label ?? ''}
                placeholder="40ft high cube"
                onChange={(e) => setDraft((d) => (d ? { ...d, label: e.target.value } : d))}
              />
            </div>
            <div>
              <Label htmlFor="container-cbm">Loadable volume (cbm)</Label>
              <NumberInput
                id="container-cbm"
                min={0}
                step="0.1"
                value={draft?.cbm ?? ''}
                error={draft?.showErrors ? cbmMessage : undefined}
                onChange={(e) => setDraft((d) => (d ? { ...d, cbm: e.target.value } : d))}
              />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-border p-3">
              <Label htmlFor="container-default" className="text-xs font-normal">
                Use this when no size is chosen
              </Label>
              <Switch
                id="container-default"
                checked={draft?.is_default ?? false}
                onCheckedChange={(next) =>
                  setDraft((d) => (d ? { ...d, is_default: next } : d))
                }
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDraft(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                if (!draft) return;
                if (cbmMessage) {
                  setDraft({ ...draft, showErrors: true });
                  return;
                }
                save.mutate(draft);
              }}
              disabled={!hasCode || save.isPending}
            >
              Save container size
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  );
}

export default ContainerSizesPanel;
