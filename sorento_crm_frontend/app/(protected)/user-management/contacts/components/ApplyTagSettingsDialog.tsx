'use client';

import { useEffect, useRef, useState } from 'react';
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
import { toast } from '@/lib/toast';
import { useApplyTagSettings } from '../../contact-tags/hooks/useContactTags';
import type {
  ApplyChange,
  ApplyResult,
  ApplyRow,
} from '../../contact-tags/types/contactTag.types';

/**
 * CONTACT-TAGS S3: "Apply tag settings". Opens with the server's dry run as the preview, "Apply to N
 * contacts" sends the real call, then the result is shown. Any error shows its message with the
 * dialog still usable (retry / close).
 */
interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  contactIds: string[];
  /** Apply every holder of this tag (route `/contact-tags/{id}/apply`) instead of `contactIds`. */
  tagId?: string;
  onApplied?: () => void;
}

export function showValue(value: unknown): string {
  if (value === true) return 'on';
  if (value === false) return 'off';
  if (value === null || value === undefined) return 'not set';
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'none';
  return String(value);
}

const STATUS: Record<
  ApplyRow['status'],
  { label: string; variant: 'primary' | 'secondary' | 'destructive' }
> = {
  changed: { label: 'Will change', variant: 'primary' },
  unchanged: { label: 'No change', variant: 'secondary' },
  skipped: { label: 'Skipped', variant: 'secondary' },
  failed: { label: 'Failed', variant: 'destructive' },
};
const DONE: typeof STATUS = {
  ...STATUS,
  changed: { label: 'Updated', variant: 'primary' },
};

export function ChangeLine({ change }: { change: ApplyChange }) {
  const isList = change.added.length > 0 || change.removed.length > 0;
  return (
    <div className="grid grid-cols-1 gap-1 py-1 text-sm sm:grid-cols-[160px_minmax(0,1fr)] sm:gap-3">
      <span className="text-muted-foreground">{change.label}</span>
      <span className="flex flex-wrap items-center gap-1">
        {isList ? (
          <>
            {change.added.map((v) => (
              <span
                key={`+${v}`}
                className="rounded bg-success/10 px-1 text-success"
              >
                + {v}
              </span>
            ))}
            {change.removed.map((v) => (
              <span
                key={`-${v}`}
                className="rounded bg-destructive/10 px-1 text-destructive line-through"
              >
                {v}
              </span>
            ))}
          </>
        ) : (
          <>
            <span className="rounded bg-destructive/10 px-1 text-destructive line-through">
              {showValue(change.before)}
            </span>
            <span className="rounded bg-success/10 px-1 text-success">
              {showValue(change.after)}
            </span>
          </>
        )}
        {change.decided_by.length ? (
          <span className="text-xs text-muted-foreground">{`by ${change.decided_by.join(', ')}`}</span>
        ) : null}
      </span>
    </div>
  );
}

export function ApplyRowCard({
  row,
  done,
}: {
  row: ApplyRow & { contact_id: string };
  done: boolean;
}) {
  const badge = (done ? DONE : STATUS)[row.status];
  return (
    <div
      className="rounded-md border px-3 py-2"
      data-testid={`apply-tag-row-${row.contact_id}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span
          className="min-w-0 truncate font-medium"
          title={row.label ?? undefined}
        >
          {row.label ?? 'Contact'}
        </span>
        <Badge variant={badge.variant} appearance="light">
          {badge.label}
        </Badge>
      </div>
      {row.error ? (
        <p className="mt-1 text-sm text-muted-foreground">{row.error}</p>
      ) : null}
      {row.changes.map((c) => (
        <ChangeLine key={`${c.section}:${c.key}`} change={c} />
      ))}
      {row.conflicts.map((c) => (
        <div
          key={c.label}
          className="mt-1 rounded bg-warning/10 px-2 py-1 text-sm"
        >
          <span className="font-medium">{c.label}</span>
          <span className="text-muted-foreground">
            {' '}
            conflict, not applied:{' '}
          </span>
          {c.tags.map((t) => `${t.name}: ${showValue(t.value)}`).join(', ')}
        </div>
      ))}
      {row.not_applied.map((n) => (
        <div key={n.label} className="mt-1 text-sm text-muted-foreground">
          <span className="font-medium">{n.label}</span>
          {` not applied: ${n.reason}`}
        </div>
      ))}
    </div>
  );
}

export function ApplyCounts({
  counts,
  done,
}: {
  counts: ApplyResult['counts'];
  done: boolean;
}) {
  return (
    <div className="mb-3 flex flex-wrap gap-2" data-testid="apply-tag-counts">
      <Badge
        variant="primary"
        appearance="light"
      >{`${counts.changed} ${done ? 'updated' : 'will change'}`}</Badge>
      <Badge
        variant="secondary"
        appearance="light"
      >{`${counts.unchanged} no change`}</Badge>
      {counts.skipped ? (
        <Badge
          variant="secondary"
          appearance="light"
        >{`${counts.skipped} skipped`}</Badge>
      ) : null}
      {counts.conflicts ? (
        <Badge
          variant="warning"
          appearance="light"
        >{`${counts.conflicts} conflict${counts.conflicts === 1 ? '' : 's'}`}</Badge>
      ) : null}
      {counts.failed ? (
        <Badge
          variant="destructive"
          appearance="light"
        >{`${counts.failed} failed`}</Badge>
      ) : null}
    </div>
  );
}

export default function ApplyTagSettingsDialog({
  open,
  onOpenChange,
  contactIds,
  tagId,
  onApplied,
}: Props) {
  const run = useApplyTagSettings({ contactIds, tagId });
  const [preview, setPreview] = useState<ApplyResult | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  const doPreview = () => {
    setError(null);
    run.mutate(true, {
      onSuccess: (r) => setPreview(r),
      onError: (e: Error) => setError(e.message),
    });
  };

  useEffect(() => {
    if (!open) {
      started.current = false;
      setPreview(null);
      setResult(null);
      setError(null);
      return;
    }
    if (started.current) return;
    started.current = true;
    doPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const runApply = () => {
    setError(null);
    run.mutate(false, {
      onSuccess: (r) => {
        setResult(r);
        const { changed, failed } = r.counts;
        if (failed > 0)
          toast.warning(`Applied to ${changed}, ${failed} failed`);
        else
          toast.success(
            `Applied tag settings to ${changed} contact${changed === 1 ? '' : 's'}`,
          );
        onApplied?.();
      },
      onError: (e: Error) => setError(e.message),
    });
  };

  const shown = result ?? preview;
  const n = preview?.results.length ?? 0;
  const done = result !== null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {done ? 'Tag settings applied' : 'Apply tag settings'}
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          {!shown && !error ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircleIcon className="size-4 animate-spin" /> Reading the
              tag settings...
            </p>
          ) : null}
          {shown ? (
            <div>
              <ApplyCounts counts={shown.counts} done={done} />
              <div className="max-h-96 space-y-2 overflow-y-auto">
                {shown.results.map((row) => (
                  <ApplyRowCard key={row.contact_id} row={row} done={done} />
                ))}
              </div>
            </div>
          ) : null}
          {error ? (
            <p className="mt-3 text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter className="flex-wrap">
          {done ? (
            <Button onClick={() => onOpenChange(false)}>Done</Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              {!preview ? (
                <Button
                  variant="outline"
                  disabled={run.isPending}
                  onClick={doPreview}
                >
                  Retry
                </Button>
              ) : (
                <Button
                  disabled={run.isPending || preview.counts.changed === 0}
                  onClick={runApply}
                >
                  {run.isPending ? (
                    <LoaderCircleIcon className="size-4 animate-spin" />
                  ) : null}
                  {`Apply to ${n} contact${n === 1 ? '' : 's'}`}
                </Button>
              )}
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
