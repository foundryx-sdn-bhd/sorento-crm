'use client';

import { useCallback, useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { getContacts } from '../[id]/services/contactService';
import { useBulkCopyContactAccess } from '../[id]/hooks/useBulkCopyContactAccess';
import {
  type AccessCopyChange,
  type AccessCopyResponse,
  type AccessCopyResult,
  type AccessCopyStatus,
} from '../[id]/services/contactAccessCopyService';
import type { RespondContact } from '../types/contact.types';

/**
 * CONTACT-BULK-ACCESS (UAC A3): "Copy access from contact" for the selected contacts.
 *
 * Pick a source -> the server's dry run is the preview -> one apply call -> a result row per
 * contact. Every path ends on a readable state: a failed preview shows its error with Retry,
 * a failed or timed-out apply ends on the result step with "nothing confirmed", never on a
 * spinner (apiFetch carries the write deadline).
 */

type Step = 'pick' | 'preview' | 'result';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  targetContacts: RespondContact[];
  /** "Check who still differs": the list filters to `access_differs_from=<source>`. */
  onCheckDiffers: (source: { id: string; label: string }) => void;
  /** An apply answered (fully, partly or not at all): the list clears its selection then,
   *  never on Cancel, so a wrong source pick does not cost the selection. */
  onApplied?: () => void;
}

const contactLabel = (c: { name?: string | null; phone_number: string }) =>
  c.name ? `${c.name} (${c.phone_number})` : c.phone_number;

const PREVIEW_BADGE: Record<AccessCopyStatus, { label: string; variant: 'primary' | 'secondary' | 'destructive' | 'success' }> = {
  changed: { label: 'Will change', variant: 'primary' },
  unchanged: { label: 'No change', variant: 'secondary' },
  skipped: { label: 'Skipped', variant: 'secondary' },
  failed: { label: 'Failed', variant: 'destructive' },
};

const RESULT_BADGE: Record<AccessCopyStatus, { label: string; variant: 'primary' | 'secondary' | 'destructive' | 'success' }> = {
  ...PREVIEW_BADGE,
  changed: { label: 'Updated', variant: 'success' },
};

const TIER_LABEL: Record<string, string> = { dealer: 'Dealer', office: 'Office', end_user: 'End user' };

function scalarText(value: unknown): string {
  if (value === true) return 'on';
  if (value === false) return 'off';
  if (value == null || value === '') return '(none)';
  return TIER_LABEL[String(value)] ?? String(value);
}

function ChangeLine({ change }: { change: AccessCopyChange }) {
  const isList = change.added.length > 0 || change.removed.length > 0;
  return (
    <div className="grid grid-cols-1 gap-1 py-1 text-sm sm:grid-cols-[130px_minmax(0,1fr)] sm:gap-3">
      <span className="text-muted-foreground">{change.label}</span>
      <span className="flex flex-wrap gap-1">
        {isList ? (
          <>
            {change.added.map((label) => (
              <span key={`+${label}`} className="rounded bg-success/10 px-1 text-success">
                + {label}
              </span>
            ))}
            {change.removed.map((label) => (
              <span key={`-${label}`} className="rounded bg-destructive/10 px-1 text-destructive line-through">
                {label}
              </span>
            ))}
          </>
        ) : (
          <>
            <span className="rounded bg-destructive/10 px-1 text-destructive line-through">{scalarText(change.before)}</span>
            <span className="rounded bg-success/10 px-1 text-success">{scalarText(change.after)}</span>
          </>
        )}
      </span>
    </div>
  );
}

function TargetRow({
  row,
  fallbackLabel,
  mode,
  defaultOpen,
}: {
  row: AccessCopyResult;
  fallbackLabel: string;
  mode: 'preview' | 'result';
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const badge = (mode === 'result' ? RESULT_BADGE : PREVIEW_BADGE)[row.status];
  return (
    <div className="rounded-md border" data-testid={`copy-access-row-${row.contact_id}`}>
      <button
        type="button"
        className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="min-w-0 truncate font-medium" title={row.label ?? fallbackLabel}>
          {row.label ?? fallbackLabel}
        </span>
        <span className="flex items-center gap-2">
          <Badge variant={badge.variant} appearance="light">
            {badge.label}
          </Badge>
          {row.status === 'changed' ? (
            <span className="text-xs text-muted-foreground">
              {row.changes.length} change{row.changes.length === 1 ? '' : 's'}
            </span>
          ) : null}
        </span>
      </button>
      {open ? (
        <div className="border-t px-3 py-2">
          {row.error ? (
            <p className="text-sm text-muted-foreground">{row.error}</p>
          ) : row.status === 'unchanged' ? (
            <p className="text-sm text-muted-foreground">
              Already has the same access. Linked customers are never copied.
            </p>
          ) : (
            row.changes.map((change) => <ChangeLine key={change.facet} change={change} />)
          )}
        </div>
      ) : null}
    </div>
  );
}

function Counts({ data, mode }: { data: AccessCopyResponse; mode: 'preview' | 'result' }) {
  const c = data.counts;
  return (
    <div className="mb-3 flex flex-wrap gap-2" data-testid="copy-access-counts">
      {mode === 'preview' ? (
        <>
          <Badge variant="primary" appearance="light">{c.changed} will change</Badge>
          <Badge variant="secondary" appearance="light">{c.unchanged} already the same</Badge>
        </>
      ) : (
        <>
          <Badge variant="success" appearance="light">{c.changed} updated</Badge>
          <Badge variant="secondary" appearance="light">{c.unchanged} no change</Badge>
        </>
      )}
      {c.skipped ? <Badge variant="secondary" appearance="light">{c.skipped} skipped</Badge> : null}
      {c.failed ? <Badge variant="destructive" appearance="light">{c.failed} failed</Badge> : null}
    </div>
  );
}

export default function BulkCopyAccessDialog({
  open,
  onOpenChange,
  targetContacts,
  onCheckDiffers,
  onApplied,
}: Props) {
  const [step, setStep] = useState<Step>('pick');
  const [sourceId, setSourceId] = useState('');
  const [sourceOption, setSourceOption] = useState<SearchableSelectOption | undefined>();
  const [applyError, setApplyError] = useState<string | null>(null);

  const targetIds = targetContacts.map((c) => c.id);
  const labelById = new Map(targetContacts.map((c) => [c.id, contactLabel(c)]));

  const { preview, apply } = useBulkCopyContactAccess(targetIds);

  const reset = () => {
    setStep('pick');
    setSourceId('');
    setSourceOption(undefined);
    setApplyError(null);
    preview.reset();
    apply.reset();
  };

  const handleOpenChange = (next: boolean) => {
    // Closing mid-apply is allowed: the server finishes on its own and the list refetches.
    if (!next) reset();
    onOpenChange(next);
  };

  const excluded = new Set(targetIds);
  const fetchSources = useCallback(
    async (query: string) => {
      const page = await getContacts(
        { pageIndex: 0, pageSize: 50, sorting: [{ id: 'name', desc: false }], searchQuery: query },
        {},
      );
      return page.data
        .filter((c) => !excluded.has(c.id))
        .map((c) => ({ value: c.id, label: contactLabel(c) }));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [targetIds.join(',')],
  );

  const pickSource = (id: string) => {
    setSourceId(id);
    preview.reset();
    if (id) preview.mutate(id);
  };

  const runApply = () => {
    setApplyError(null);
    apply.mutate(sourceId, {
      onSuccess: () => setStep('result'),
      onError: (error: Error) => {
        setApplyError(error.message);
        setStep('result');
      },
      onSettled: () => onApplied?.(),
    });
  };

  const n = targetContacts.length;
  const sourceLabel = preview.data?.source.label ?? sourceOption?.label ?? '';

  let title = `Copy access to ${n} contact${n === 1 ? '' : 's'}`;
  if (step === 'preview') title = `Preview: copy access from ${sourceLabel}`;
  if (step === 'result') title = applyError ? 'Copy not confirmed' : 'Copy finished';

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {step === 'preview' ? (
            <DialogDescription>
              Each contact ends with exactly the source&apos;s access. Struck red is removed, green is added.
            </DialogDescription>
          ) : null}
        </DialogHeader>
        <DialogBody>
          {step === 'pick' ? (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>Copy access from</Label>
                <SearchableSelect
                  value={sourceId}
                  onChange={pickSource}
                  onOptionChange={(opt) => setSourceOption(opt ?? undefined)}
                  selectedOption={sourceOption}
                  fetchOptions={fetchSources}
                  placeholder="Search a contact"
                  emptyMessage="No contacts match."
                  clearable
                  disabled={apply.isPending}
                />
              </div>
              {preview.isPending ? (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <LoaderCircleIcon className="size-4 animate-spin" /> Reading the source&apos;s access...
                </p>
              ) : null}
              {preview.isError ? (
                <div className="flex flex-wrap items-center gap-2 text-sm text-destructive">
                  <span>{preview.error.message}</span>
                  <Button size="sm" variant="outline" onClick={() => preview.mutate(sourceId)}>
                    Retry
                  </Button>
                </div>
              ) : null}
              {preview.data ? (
                <dl
                  className="grid grid-cols-1 gap-x-3 gap-y-1 rounded-md border p-3 text-sm sm:grid-cols-[150px_minmax(0,1fr)]"
                  data-testid="copy-access-source-summary"
                >
                  {preview.data.source.summary.map((line) => (
                    <div key={line.label} className="contents">
                      <dt className="text-muted-foreground">{line.label}</dt>
                      <dd className="m-0">{line.value || '(none)'}</dd>
                    </div>
                  ))}
                  <dt className="text-muted-foreground">Not copied</dt>
                  <dd className="m-0 text-muted-foreground">
                    Linked customers, companies, CS routing, media limits, memory
                  </dd>
                </dl>
              ) : null}
            </div>
          ) : null}

          {step === 'preview' && preview.data ? (
            <div>
              <Counts data={preview.data} mode="preview" />
              <div className="max-h-96 space-y-2 overflow-y-auto">
                {preview.data.results.map((row, i) => (
                  <TargetRow
                    key={row.contact_id}
                    row={row}
                    fallbackLabel={labelById.get(row.contact_id) ?? 'Contact'}
                    mode="preview"
                    defaultOpen={i < 2}
                  />
                ))}
              </div>
            </div>
          ) : null}

          {step === 'result' ? (
            applyError ? (
              <div className="space-y-2 text-sm" data-testid="copy-access-apply-error">
                <p className="text-destructive">{applyError}</p>
                <p>
                  Nothing confirmed. Contacts already written stay written; check the list with
                  &quot;Access differs from&quot; before trying again.
                </p>
              </div>
            ) : apply.data ? (
              <div>
                <Counts data={apply.data} mode="result" />
                <div className="max-h-96 space-y-2 overflow-y-auto">
                  {apply.data.results.map((row, i) => (
                    <TargetRow
                      key={row.contact_id}
                      row={row}
                      fallbackLabel={labelById.get(row.contact_id) ?? 'Contact'}
                      mode="result"
                      defaultOpen={i < 2}
                    />
                  ))}
                </div>
              </div>
            ) : null
          ) : null}
        </DialogBody>
        <DialogFooter className="flex-wrap">
          {step === 'pick' ? (
            <>
              <Button variant="outline" onClick={() => handleOpenChange(false)}>
                Cancel
              </Button>
              <Button disabled={!preview.data} onClick={() => setStep('preview')}>
                Preview changes
              </Button>
            </>
          ) : null}
          {step === 'preview' ? (
            <>
              <Button variant="outline" disabled={apply.isPending} onClick={() => setStep('pick')}>
                Back
              </Button>
              <Button disabled={apply.isPending || !preview.data?.counts.changed} onClick={runApply}>
                {apply.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
                {(() => {
                  const k = preview.data?.counts.changed ?? 0;
                  return `Apply to ${k} contact${k === 1 ? '' : 's'}`;
                })()}
              </Button>
            </>
          ) : null}
          {step === 'result' ? (
            <>
              <Button
                variant="outline"
                onClick={() => {
                  onCheckDiffers({ id: sourceId, label: sourceLabel });
                  handleOpenChange(false);
                }}
              >
                Check who still differs
              </Button>
              <Button onClick={() => handleOpenChange(false)}>Done</Button>
            </>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
