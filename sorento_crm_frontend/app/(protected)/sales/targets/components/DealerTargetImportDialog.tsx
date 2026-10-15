'use client';

import { useState } from 'react';
import { LoaderCircle, TestTube } from 'lucide-react';
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
import { todayMalaysiaYyyyMmDd } from '@/lib/helpers';
import { useImportDealerTargets } from '../hooks/useSalesTargets';
import type { DealerImportResult } from '../types/salesTarget.types';

const ACCEPT = '.xlsx,.xlsm';
const MAX_SIZE_MB = 10;

/**
 * Import dealer targets from the Sorento / Mocha Excel (lane SALES-TARGET-DEALER, UAC D3, mock
 * frame D). Test always comes first and writes nothing; Import is enabled only after a Test of
 * the same file and year. The report lists every row of the file: mapped, or not representable
 * with the reason, so a row that cannot be imported is never silently dropped.
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
  const run = useImportDealerTargets();
  const file = files[0] ?? null;
  const key = file ? `${file.name}:${file.size}:${file.lastModified}:${year}` : '';
  const yearNumber = Number(year);
  const yearOk = Number.isInteger(yearNumber) && yearNumber >= 2000 && yearNumber <= 2100;

  const reset = () => {
    setFiles([]);
    setResult(null);
    setTestedKey('');
  };
  const close = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };
  const test = async () => {
    if (!file || !yearOk) return;
    try {
      const out = await run.mutateAsync({ file, dryRun: true, year: yearNumber });
      setResult(out);
      setTestedKey(key);
    } catch {
      // The hook toasted the reason.
    }
  };
  const apply = async () => {
    if (!file || testedKey !== key) return;
    try {
      const out = await run.mutateAsync({ file, dryRun: false, year: yearNumber });
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
            </div>
          </div>
          {run.isPending ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
              Reading the file...
            </div>
          ) : null}
          {result && !run.isPending ? <ImportReport result={result} /> : null}
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
            disabled={!file || testedKey !== key || run.isPending || !result?.summary.mapped}
          >
            {result ? `Import ${result.summary.mapped} rows` : 'Import'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The Test's report: counts, then one line per sheet row. */
export function ImportReport({ result }: { result: DealerImportResult }) {
  const { summary } = result;
  return (
    <div className="space-y-3" aria-label="Import report">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <CountTile label="Rows mapped" value={summary.mapped} />
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
                <td className="max-w-[12rem] truncate px-3 py-2" title={row.name_in_file ?? undefined}>
                  {row.name_in_file ?? '-'}
                </td>
                <td className="max-w-[12rem] truncate px-3 py-2" title={row.customer ?? undefined}>
                  {row.customer ?? '-'}
                </td>
                <td className="px-3 py-2">
                  {row.status === 'mapped' ? (
                    <div className="flex flex-col gap-1">
                      <Badge variant="success" appearance="light" size="sm" className="w-fit">
                        {row.target_nos.length ? row.target_nos.join(', ') : 'Mapped'}
                      </Badge>
                      {row.warnings.map((w) => (
                        <span key={w} className="text-xs text-warning">
                          {w}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-col gap-1">
                      <Badge variant="destructive" appearance="light" size="sm" className="w-fit">
                        Not representable
                      </Badge>
                      <span className="text-xs text-muted-foreground">{row.reason}</span>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
