'use client';

import { useState } from 'react';
import { Download, LoaderCircle, TestTube } from 'lucide-react';
import { toast } from '@/lib/toast';
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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { CountTile } from '@/components/common/CountTile';
import { FileDropzone } from '@/components/common/FileDropzone';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { todayMalaysiaYyyyMmDd } from '@/lib/helpers';
import { useDownloadDealerTemplate, useImportDealerTargets } from '../hooks/useSalesTargets';
import type { DealerImportPicks, DealerImportResult, DealerImportRow } from '../types/salesTarget.types';

const SKIP = 'skip';

const ACCEPT = '.xlsx,.xlsm';
const MAX_SIZE_MB = 10;

/**
 * Import dealer targets from the Sorento / Mocha Excel (lane SALES-TARGET-DEALER, UAC D3, mock
 * frame D). Test always comes first and writes nothing; Import is enabled only after a Test of
 * the same file and year. The report lists every row of the file: mapped, waiting on a pick,
 * skipped, or not representable with the reason, so a row is never silently dropped. Rows the
 * name match left open (owner ruling 16 Oct 09:12) get a pick from their suggestions, or Skip;
 * the picks go with the next Test and with Import.
 */
export default function DealerTargetImportDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [year, setYear] = useState(() => todayMalaysiaYyyyMmDd().slice(0, 4));
  const [result, setResult] = useState<DealerImportResult | null>(null);
  const [testedKey, setTestedKey] = useState('');
  const [picks, setPicks] = useState<DealerImportPicks>({});
  const run = useImportDealerTargets();
  const template = useDownloadDealerTemplate();
  const file = files[0] ?? null;
  const key = file ? `${file.name}:${file.size}:${file.lastModified}:${year}` : '';
  const yearNumber = Number(year);
  const yearOk = Number.isInteger(yearNumber) && yearNumber >= 2000 && yearNumber <= 2100;

  const reset = () => {
    setFiles([]);
    setResult(null);
    setTestedKey('');
    setPicks({});
  };
  const close = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };
  const test = async () => {
    if (!file || !yearOk) return;
    try {
      const out = await run.mutateAsync({ file, dryRun: true, year: yearNumber, picks });
      setResult(out);
      setTestedKey(key);
    } catch {
      // The hook toasted the reason.
    }
  };
  // Mapped rows plus the rows picked since the Test (a pick is checked again on Import).
  const importCount =
    (result?.summary.mapped ?? 0) +
    (result?.rows.filter((r) => r.status === 'needs_pick' && picks[r.pick_key] && picks[r.pick_key] !== SKIP)
      .length ?? 0);
  const apply = async () => {
    if (!file || testedKey !== key) return;
    try {
      const out = await run.mutateAsync({ file, dryRun: false, year: yearNumber, picks });
      setResult(out);
      close(false);
    } catch {
      // The hook toasted the reason; the dialog stays open with the last Test.
    }
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>Import dealer targets</DialogTitle>
          <DialogDescription>Dealer targets and their tiers from the targets workbook.</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_8rem]">
            <FileDropzone
              files={files}
              onFilesChange={(next) => {
                setFiles(next);
                setResult(null);
                setPicks({});
              }}
              onReject={(rejected, reason) =>
                toast.error(
                  reason === 'size'
                    ? `${rejected.name} is larger than ${MAX_SIZE_MB} MB.`
                    : reason === 'extra'
                      ? `${rejected.name} was not used - one file at a time.`
                      : `${rejected.name} is not an Excel file.`,
                )
              }
              accept={ACCEPT}
              maxSizeMb={MAX_SIZE_MB}
              disabled={run.isPending}
              inputTestId="dealer-target-import-file"
              aria-label="Dealer targets workbook"
            />
            <div className="flex flex-col gap-1">
              <Label htmlFor="dealer-import-year" className="text-xs text-muted-foreground">
                Year
              </Label>
              <Input
                id="dealer-import-year"
                type="number"
                min={2000}
                max={2100}
                value={year}
                onChange={(e) => {
                  setYear(e.target.value);
                  setResult(null);
                }}
                className="h-8"
              />
              <Button
                variant="ghost"
                size="sm"
                className="mt-1 w-fit px-0"
                onClick={() => yearOk && template.mutate(yearNumber)}
                disabled={!yearOk || template.isPending}
              >
                <Download className="size-4" />
                Template
              </Button>
            </div>
          </div>
          {run.isPending ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
              Reading the file...
            </div>
          ) : null}
          {result && !run.isPending ? (
            <ImportReport
              result={result}
              picks={picks}
              onPick={(key, value) =>
                setPicks((prev) => {
                  const next = { ...prev };
                  if (value) next[key] = value;
                  else delete next[key];
                  return next;
                })
              }
            />
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            Cancel
          </Button>
          <Button variant="outline" onClick={() => void test()} disabled={!file || !yearOk || run.isPending}>
            <TestTube className="size-4" />
            Test
          </Button>
          <Button
            onClick={() => void apply()}
            disabled={!file || testedKey !== key || run.isPending || !importCount}
          >
            {result ? `Import ${importCount} rows` : 'Import'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The Test's report: counts, then one line per sheet row. */
export function ImportReport({
  result,
  picks = {},
  onPick,
}: {
  result: DealerImportResult;
  picks?: DealerImportPicks;
  onPick?: (key: string, value: string) => void;
}) {
  const { summary } = result;
  return (
    <div className="space-y-3" aria-label="Import report">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <CountTile label="Rows mapped" value={summary.mapped} />
        <CountTile label="Matched by name" value={summary.auto_mapped} />
        <CountTile label="Needs a pick" value={summary.needs_pick} />
        <CountTile label="Not representable" value={summary.not_representable} />
        <CountTile label="New targets" value={summary.targets_new} />
        <CountTile label="Targets updated" value={summary.targets_updated} />
      </div>
      {result.notes.length ? (
        <ul className="list-disc space-y-0.5 ps-4 text-sm text-muted-foreground">
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      <div className="max-h-80 overflow-auto rounded-lg border">
        <table className="w-full min-w-[44rem] text-sm">
          <thead className="sticky top-0 bg-muted/60 text-xs text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-start font-medium">Row</th>
              <th className="px-3 py-2 text-start font-medium">Code</th>
              <th className="px-3 py-2 text-start font-medium">Name in file</th>
              <th className="px-3 py-2 text-start font-medium">Customer</th>
              <th className="px-3 py-2 text-start font-medium">Result</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {result.rows.map((row) => (
              <tr key={`${row.sheet}:${row.row}`}>
                <td className="whitespace-nowrap px-3 py-2 tabular-nums" title={row.sheet}>
                  {`${row.sheet} ${row.row}`}
                </td>
                <td className="whitespace-nowrap px-3 py-2">{row.code ?? '-'}</td>
                <td className="max-w-[12rem] px-3 py-2">
                  <div className="truncate" title={row.name_in_file ?? undefined}>
                    {row.name_in_file ?? '-'}
                  </div>
                  {row.account_in_file ? (
                    <div className="truncate text-xs text-muted-foreground" title={row.account_in_file}>
                      {row.account_in_file}
                    </div>
                  ) : null}
                </td>
                <td className="max-w-[14rem] px-3 py-2">
                  {row.status === 'needs_pick' && onPick ? (
                    <SearchableSelect
                      aria-label={`Customer for ${row.sheet} row ${row.row}`}
                      value={picks[row.pick_key] ?? ''}
                      onChange={(value) => onPick(row.pick_key, value)}
                      options={pickOptions(row)}
                      placeholder="Pick a customer"
                      size="sm"
                      clearable
                      wrapOptions
                    />
                  ) : (
                    <div className="truncate" title={row.customer ?? undefined}>
                      {row.customer ?? '-'}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2">
                  <RowResult row={row} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function pickOptions(row: DealerImportRow) {
  return [
    ...row.suggestions.map((s) => ({ value: s.customer_id, label: `${s.code} - ${s.name}` })),
    { value: SKIP, label: 'Skip this row' },
  ];
}

function RowResult({ row }: { row: DealerImportRow }) {
  if (row.status === 'mapped') {
    return (
      <div className="flex flex-col gap-1">
        <Badge variant="success" appearance="light" size="sm" className="w-fit">
          {row.target_nos.length ? row.target_nos.join(', ') : row.match === 'picked' ? 'Picked' : 'Mapped'}
        </Badge>
        {row.warnings.map((w) => (
          <span key={w} className="text-xs text-warning">
            {w}
          </span>
        ))}
      </div>
    );
  }
  const badge =
    row.status === 'needs_pick'
      ? { variant: 'warning' as const, label: 'Needs a pick' }
      : row.status === 'skipped'
        ? { variant: 'secondary' as const, label: 'Skipped' }
        : { variant: 'destructive' as const, label: 'Not representable' };
  return (
    <div className="flex flex-col gap-1">
      <Badge variant={badge.variant} appearance="light" size="sm" className="w-fit">
        {badge.label}
      </Badge>
      <span className="text-xs text-muted-foreground">{row.reason}</span>
    </div>
  );
}
