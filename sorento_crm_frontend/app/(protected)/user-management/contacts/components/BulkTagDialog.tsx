'use client';

import { useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
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
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { toast } from '@/lib/toast';
import {
  useBulkTagContacts,
  useContactTagOptions,
} from '../../contact-tags/hooks/useContactTags';
import type {
  BulkTagResult,
  BulkTagRow,
} from '../../contact-tags/types/contactTag.types';
import type { RespondContact } from '../types/contact.types';
import { ChangeLine } from './ApplyTagSettingsDialog';

/**
 * CONTACT-TAGS UAC T2.2/T2.3: "Add tag" / "Remove tag" for the selected contacts.
 * Pick tags -> the server's dry run is the preview (per-contact before / after) -> apply.
 * A rejected call shows its message and leaves the dialog open and usable.
 */
interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: 'add' | 'remove';
  targetContacts: RespondContact[];
  /** An apply answered: the list clears its selection then, never on Cancel. */
  onApplied?: () => void;
}

const STATUS: Record<
  BulkTagRow['status'],
  { label: string; variant: 'primary' | 'secondary' | 'destructive' }
> = {
  changed: { label: 'Will change', variant: 'primary' },
  unchanged: { label: 'No change', variant: 'secondary' },
  failed: { label: 'Failed', variant: 'destructive' },
};

function PreviewRow({ row, showSettings }: { row: BulkTagRow; showSettings: boolean }) {
  const status = STATUS[row.status];
  const before = new Set(row.tags_before);
  const after = new Set(row.tags_after);
  const added = row.tags_after.filter((t) => !before.has(t));
  const removed = row.tags_before.filter((t) => !after.has(t));
  return (
    <div
      className="rounded-md border px-3 py-2"
      data-testid={`bulk-tag-row-${row.contact_id}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="min-w-0 truncate font-medium" title={row.label}>
          {row.label}
        </span>
        <Badge variant={status.variant} appearance="light">
          {status.label}
        </Badge>
      </div>
      {row.error ? (
        <p className="mt-1 text-sm text-muted-foreground">{row.error}</p>
      ) : (
        <div className="mt-1 flex flex-wrap gap-1 text-sm">
          {row.tags_after.length === 0 && removed.length === 0 ? (
            <span className="text-muted-foreground">No tags</span>
          ) : null}
          {row.tags_after
            .filter((t) => before.has(t))
            .map((t) => (
              <span key={t} className="rounded bg-muted px-1">
                {t}
              </span>
            ))}
          {added.map((t) => (
            <span
              key={`+${t}`}
              className="rounded bg-success/10 px-1 text-success"
            >
              + {t}
            </span>
          ))}
          {removed.map((t) => (
            <span
              key={`-${t}`}
              className="rounded bg-destructive/10 px-1 text-destructive line-through"
            >
              {t}
            </span>
          ))}
        </div>
      )}
      {showSettings && row.settings?.changes?.length ? (
        <div className="mt-2 border-t pt-1">
          {row.settings.changes.map((c) => (
            <ChangeLine key={`${c.section}:${c.key}`} change={c} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function BulkTagDialog({
  open,
  onOpenChange,
  mode,
  targetContacts,
  onApplied,
}: Props) {
  const [tagIds, setTagIds] = useState<string[]>([]);
  const [preview, setPreview] = useState<BulkTagResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Add mode only: apply the added tags' settings after the tag change (default), or just link.
  const [applyNow, setApplyNow] = useState(true);
  const {
    data: options = [],
    isError: optionsFailed,
    refetch,
  } = useContactTagOptions(open);
  const run = useBulkTagContacts();

  const contactIds = targetContacts.map((c) => c.id);
  const payload = (dryRun: boolean) => ({
    contactIds,
    addTagIds: mode === 'add' ? tagIds : [],
    removeTagIds: mode === 'remove' ? tagIds : [],
    dryRun,
    ...(mode === 'add' ? { applySettings: applyNow } : {}),
  });

  const reset = () => {
    setTagIds([]);
    setPreview(null);
    setError(null);
    setApplyNow(true);
    run.reset();
  };
  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const runPreview = () => {
    setError(null);
    run.mutate(payload(true), {
      onSuccess: (result) => setPreview(result),
      onError: (e: Error) => setError(e.message),
    });
  };
  const runApply = () => {
    setError(null);
    run.mutate(payload(false), {
      onSuccess: (result) => {
        const { changed, failed } = result.counts;
        if (failed > 0) toast.warning(`Tagged ${changed}, ${failed} failed`);
        else
          toast.success(`Tagged ${changed} contact${changed === 1 ? '' : 's'}`);
        onApplied?.();
        handleOpenChange(false);
      },
      onError: (e: Error) => setError(e.message),
    });
  };

  const n = targetContacts.length;
  const verb = mode === 'add' ? 'Add tag to' : 'Remove tag from';
  const title = `${verb} ${n} contact${n === 1 ? '' : 's'}`;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {preview ? `Preview: ${title.toLowerCase()}` : title}
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          {!preview ? (
            <div className="space-y-1.5">
              <Label>{mode === 'add' ? 'Tags to add' : 'Tags to remove'}</Label>
              <SearchableMultiSelect
                value={tagIds}
                onChange={setTagIds}
                options={options}
                placeholder="Select tags"
                emptyMessage="No tags."
                loadError={
                  optionsFailed ? new Error('Failed to load tags') : undefined
                }
                onRetry={() => void refetch()}
                disabled={run.isPending}
              />
            </div>
          ) : (
            <div>
              <div className="mb-3 flex flex-wrap gap-2">
                <Badge variant="primary" appearance="light">
                  {preview.counts.changed} will change
                </Badge>
                <Badge variant="secondary" appearance="light">
                  {preview.counts.unchanged} no change
                </Badge>
                {preview.counts.failed ? (
                  <Badge variant="destructive" appearance="light">
                    {preview.counts.failed} failed
                  </Badge>
                ) : null}
              </div>
              {mode === 'add' ? (
                <div className="mb-3 space-y-1 text-sm" role="radiogroup" aria-label="Tag settings">
                  <label className="flex items-center gap-2">
                    <input type="radio" name="bulk-tag-apply" checked={applyNow} onChange={() => setApplyNow(true)} />
                    Apply tag settings now
                  </label>
                  <label className="flex items-center gap-2">
                    <input type="radio" name="bulk-tag-apply" checked={!applyNow} onChange={() => setApplyNow(false)} />
                    Don&apos;t apply now
                  </label>
                </div>
              ) : null}
              <div className="max-h-96 space-y-2 overflow-y-auto">
                {preview.results.map((row) => (
                  <PreviewRow key={row.contact_id} row={row} showSettings={mode === 'add' && applyNow} />
                ))}
              </div>
            </div>
          )}
          {error ? (
            <p className="mt-3 text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter className="flex-wrap">
          {!preview ? (
            <>
              <Button variant="outline" onClick={() => handleOpenChange(false)}>
                Cancel
              </Button>
              <Button
                disabled={run.isPending || tagIds.length === 0}
                onClick={runPreview}
              >
                {run.isPending ? (
                  <LoaderCircleIcon className="size-4 animate-spin" />
                ) : null}
                Preview changes
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                disabled={run.isPending}
                onClick={() => setPreview(null)}
              >
                Back
              </Button>
              <Button
                disabled={run.isPending || preview.counts.changed === 0}
                onClick={runApply}
              >
                {run.isPending ? (
                  <LoaderCircleIcon className="size-4 animate-spin" />
                ) : null}
                {mode === 'add'
                  ? `Add tag to ${n} contact${n === 1 ? '' : 's'}`
                  : `Apply to ${preview.counts.changed} contact${preview.counts.changed === 1 ? '' : 's'}`}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
