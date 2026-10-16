'use client';

import * as React from 'react';
import { AlertTriangle, Check, CheckCircle2, Copy, LoaderCircle } from 'lucide-react';
import { toast } from '@/lib/toast';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { FileDropzone } from '@/components/common/FileDropzone';
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard';
import { Textarea } from '@/components/ui/textarea';
import { Progress } from '@/components/ui/progress';
import {
  useImportJobStatus,
  useSeriesProductMutations,
} from '../../../_shared/hooks/useProjects';
import {
  importJobPhase,
  type ProjectSeries,
  type SeriesProductImportBody,
  type SeriesProductImportResult,
} from '../../../_shared/types/project.types';

/**
 * Load the sheet, on the page.
 *
 * This used to be a dialog. It is inline now because the client does not want popups, and
 * because the part that matters - the list of codes the catalogue does not carry - is
 * something they read against their spreadsheet rather than glance at and dismiss.
 *
 * The file path reads THREE columns (code, DEVELOPERS price, DISTRIBUTORS percentage), so
 * uploading the workbook is what actually fills the price and discount cells in the table
 * below. Pasting codes only nominates products; it deliberately leaves prices alone.
 */
export function SeriesSheetLoader({ series }: { series: ProjectSeries }) {
  const { importCodes, importFile, invalidateSeries } = useSeriesProductMutations();
  const [files, setFiles] = React.useState<File[]>([]);
  const [pasted, setPasted] = React.useState('');
  const [mode, setMode] = React.useState<SeriesProductImportBody['mode']>('append');
  const [result, setResult] = React.useState<SeriesProductImportResult | null>(null);
  const [confirmingReplace, setConfirmingReplace] = React.useState(false);
  const [jobId, setJobId] = React.useState<string | null>(null);

  const job = useImportJobStatus(jobId);
  const phase = job.data ? importJobPhase(job.data.status) : null;

  /**
   * The queued read, once it stops.
   *
   * The report is copied into local state and the job dropped, so a finished import reads
   * exactly like a pasted one: one renderer, one shape, and no way for the two paths to
   * start disagreeing about what a load did.
   */
  React.useEffect(() => {
    if (!job.data || !phase || phase === 'running') return;
    if (phase === 'done' && job.data.result) {
      setResult(job.data.result);
      // Only NOW are there new products to draw. Refreshing at upload time would have
      // redrawn the same rows and looked like the sheet did nothing.
      invalidateSeries();
    } else {
      toast.error(job.data.error || 'That sheet could not be read.');
    }
    setJobId(null);
    // `invalidateSeries` is stable for the life of the hook; listing it would re-run this
    // on every render of the parent and re-toast a failure that already happened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.data, phase]);

  const file = files[0] ?? null;
  const codes = splitCodes(pasted);
  // A queued job is still "loading" to the person watching, even though our request
  // finished the moment the file left the browser.
  const pending = importCodes.isPending || importFile.isPending || Boolean(jobId);
  const canLoad = Boolean(file) || codes.length > 0;

  const run = async () => {
    try {
      setResult(null);
      if (file) {
        // Queued: the answer arrives by polling, not from this call.
        const queued = await importFile.mutateAsync({ id: series.id, file, mode });
        setJobId(queued.job_id);
      } else {
        setResult(await importCodes.mutateAsync({ id: series.id, body: { codes, mode } }));
      }
      setFiles([]);
      setPasted('');
    } catch {
      // The mutation hook has already surfaced the message; nothing to add here.
    }
  };

  const start = () => {
    // Replace can delete products somebody else nominated, so it asks first. Append cannot.
    if (mode === 'replace' && series.product_count > 0) {
      setConfirmingReplace(true);
      return;
    }
    void run();
  };

  return (
    <div className="space-y-4">
      <FileDropzone
        files={files}
        onFilesChange={setFiles}
        accept=".xlsx,.xlsm,.csv"
        maxSizeMb={10}
        title="Drop the products sheet"
        hint="xlsx or csv, up to 10 MB"
        disabled={pending}
        onReject={(rejected, reason) =>
          toast.error(
            reason === 'size'
              ? `${rejected.name} is larger than 10 MB`
              : reason === 'type'
                ? `${rejected.name} is not a spreadsheet`
                : `${rejected.name} was not used - one file at a time`,
          )
        }
      />

      {!file && (
        <Textarea
          value={pasted}
          onChange={(event) => setPasted(event.target.value)}
          rows={3}
          disabled={pending}
          placeholder="…or paste product codes, one per line"
        />
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="series-sheet-mode"
              value="append"
              checked={mode === 'append'}
              onChange={() => setMode('append')}
              disabled={pending}
            />
            Add to the series
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="series-sheet-mode"
              value="replace"
              checked={mode === 'replace'}
              onChange={() => setMode('replace')}
              disabled={pending}
            />
            Replace the series
          </label>
        </div>
        <Button onClick={start} disabled={!canLoad || pending}>
          {pending ? (
            <LoaderCircle className="size-4 animate-spin" aria-label="Loading" />
          ) : (
            'Load'
          )}
        </Button>
      </div>

      {jobId && <ImportProgress progress={job.data?.progress ?? null} />}

      {result && <ImportReport result={result} />}

      <AlertDialog open={confirmingReplace} onOpenChange={setConfirmingReplace}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Replace the products in this series?</AlertDialogTitle>
            <AlertDialogDescription>
              {`This series names ${series.product_count} ${
                series.product_count === 1 ? 'product' : 'products'
              }. Anything not in what you are loading will be taken off it. This action cannot be undone.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => void run()}
            >
              Replace
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/**
 * A sheet being read somewhere else.
 *
 * The upload returns the instant the bytes leave the browser, so without this the screen
 * would look finished while nothing had happened yet. Two states rather than one, because
 * they are genuinely different and conflating them is what makes a queue feel broken: the
 * job has not started (nothing to count, and an indeterminate bar is the honest picture),
 * or it is counting codes and can say how far it has got.
 */
function ImportProgress({
  progress,
}: {
  progress: { total: number; processed: number; percentage: number } | null;
}) {
  const counting = Boolean(progress && progress.total > 0);
  return (
    <div className="space-y-2 rounded-lg border border-border p-3" role="status">
      <p className="text-sm font-medium">
        {counting
          ? `Reading the sheet - ${progress!.processed} of ${progress!.total} codes`
          : 'Reading the sheet'}
      </p>
      {/* The shared Progress has no indeterminate mode, and feeding it 0 would say
          "no progress" rather than "not started" - a stalled-looking bar on a job that is
          simply still queued. A pulsing track says the honest thing instead. */}
      {counting ? (
        <Progress value={progress!.percentage} className="h-1.5" />
      ) : (
        <div className="h-1.5 w-full animate-pulse rounded-full bg-secondary" aria-hidden />
      )}
      <p className="text-xs text-muted-foreground">
        You can leave this page. The load finishes on its own.
      </p>
    </div>
  );
}

/**
 * What the load did, including what it could not do.
 *
 * The unmatched block is deliberately the loudest thing here. It is not an error - nothing
 * failed - but it is the only part of the answer the admin has to act on, and it is the part
 * a success toast would have thrown away. Measured on the client's own sheet: 49 of 141 codes
 * missed, because they quote base codes the catalogue stocks only as suffixed variants.
 */
function ImportReport({ result }: { result: SeriesProductImportResult }) {
  const misses = result.unmatched_codes;
  const resolved = result.resolved ?? [];

  // The tick on the button is the confirmation; only a refusal needs saying (S7-05).
  const { isCopied, copyToClipboard } = useCopyToClipboard();

  async function copyMisses() {
    if (!(await copyToClipboard(misses.join('\n')))) {
      toast.error('Copying is blocked in this browser. Select the list and copy it by hand.');
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-border p-3" role="status">
      <div className="flex items-start gap-2">
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 text-sm">
          <p className="font-medium">
            {result.added === 0
              ? 'Nothing new was added'
              : `${result.added} ${result.added === 1 ? 'product' : 'products'} added`}
          </p>
          <p className="text-xs text-muted-foreground">
            {[
              `${result.submitted} ${result.submitted === 1 ? 'code' : 'codes'} read`,
              `${result.unique_codes} unique`,
              result.already_present > 0 ? `${result.already_present} already named` : null,
              result.removed > 0 ? `${result.removed} removed` : null,
              `${result.product_count} now in the series`,
            ]
              .filter(Boolean)
              .join(' - ')}
          </p>
        </div>
      </div>

      {resolved.length > 0 ? (
        <div className="max-h-48 space-y-2 overflow-y-auto rounded-md border border-border p-2.5">
          {resolved.map((item) => (
            <div key={item.code} className="space-y-1">
              <p className="flex min-w-0 items-center gap-2 text-sm">
                <span className="min-w-0 truncate font-medium" title={item.code}>
                  {item.code}
                </span>
                <Badge variant="outline" className="shrink-0">
                  {item.via}
                </Badge>
              </p>
              <div className="flex flex-wrap gap-1">
                {item.members.map((member) => (
                  <Badge key={member} variant="secondary" className="max-w-full truncate" title={member}>
                    {member}
                  </Badge>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {misses.length > 0 ? (
        <div className="rounded-md border border-amber-300/60 bg-amber-50/60 p-2.5 dark:border-amber-500/30 dark:bg-amber-500/10">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <p className="flex min-w-0 items-start gap-2 text-sm font-medium">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span className="min-w-0">
                {`${misses.length} ${misses.length === 1 ? 'code is' : 'codes are'} not in the catalogue`}
              </span>
            </p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="shrink-0"
              onClick={() => void copyMisses()}
            >
              {isCopied ? (
                <Check className="size-4" aria-hidden />
              ) : (
                <Copy className="size-4" aria-hidden />
              )}
              {isCopied ? 'Copied' : 'Copy'}
            </Button>
          </div>
          <div className="mt-2 flex max-h-40 flex-wrap gap-1 overflow-y-auto">
            {misses.map((code) => (
              <Badge key={code} variant="secondary" className="max-w-full truncate" title={code}>
                {code}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** A paste as a list of codes, split on every plausible separator at once. */
export function splitCodes(text: string): string[] {
  return text
    .split(/[\r\n,;\t]+/)
    .map((part) => part.trim())
    .filter(Boolean);
}
