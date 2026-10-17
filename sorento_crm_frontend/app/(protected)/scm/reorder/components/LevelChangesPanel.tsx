'use client';

import { useMemo, useState } from 'react';
import { Download, ListChecks } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { EM_DASH } from '../../lib/format';
import {
  effectiveLevel,
  levelActionLabel,
  levelRowsForExport,
  type LevelSuggestion,
} from '../lib/levelSuggestion';
import { codeWithName, distinctName } from '@/lib/product-display';

/**
 * The run's AutoCount level changes, as one list to carry over (S13f, AC-S13f.3).
 *
 * AutoCount is where the level actually lives, and the buyer updates it there by hand.
 * Making them harvest the changes row by row off a 4,000-line grid is how the levels
 * stay six years stale - so the run offers the changed ones as a single list and a CSV.
 */
export function LevelChangesPanel({
  suggestions,
  onAmend,
}: {
  suggestions: Record<string, LevelSuggestion>;
  /** Record (or withdraw, with null) the buyer's own figure. Absent = read-only list. */
  onAmend?: (s: LevelSuggestion, amended: number | null) => Promise<void> | void;
}) {
  const [open, setOpen] = useState(false);
  // Keyed by suggestion map key, so an edit survives the list re-sorting under it.
  const entries = useMemo(
    () =>
      Object.entries(suggestions).filter(([, s]) => levelActionLabel(s).changed),
    [suggestions],
  );
  const changes = useMemo(() => levelRowsForExport(suggestions), [suggestions]);

  if (!changes.length) return null;

  const download = () => {
    const header =
      'Item Code,Item Name,Location,Current Level,Set Level To,Engine Suggested,ADU,Lead Days';
    const cell = (v: string | number | null) =>
      v === null ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replaceAll('"', '""')}"` : String(v);
    const lines = changes.map((c) =>
      [c.product_code, c.product_name, c.warehouse, c.current_level, c.suggested_level,
       c.engine_level, c.adu, c.lead_time_days].map(cell).join(','),
    );
    const blob = new Blob([[header, ...lines].join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'autocount-level-changes.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const noun = changes.length === 1 ? 'AutoCount level to update' : 'AutoCount levels to update';

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <ListChecks className="size-4" aria-hidden />
        {changes.length} {noun}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>AutoCount levels to update</DialogTitle>
            <DialogDescription>
              What this run suggests, beside what is set today. Changes are made in AutoCount.
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-background text-left text-xs text-muted-foreground">
                <tr>
                  <th className="py-1.5 pr-3 font-medium">Item</th>
                  <th className="py-1.5 pr-3 font-medium">Location</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Now</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Set to</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Engine</th>
                  <th className="py-1.5 font-medium">Lead time</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(([key, s]) => (
                  <LevelChangeRow key={key} suggestion={s} onAmend={onAmend} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex justify-end">
            <Button variant="outline" size="sm" onClick={download}>
              <Download className="size-4" aria-hidden />
              Download CSV
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** One change, with the "Set to" figure editable in place: this list IS the review. */
function LevelChangeRow({
  suggestion: s,
  onAmend,
}: {
  suggestion: LevelSuggestion;
  onAmend?: (s: LevelSuggestion, amended: number | null) => Promise<void> | void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  // Set when the box was left with a bad figure: the sentence then follows the field live.
  const [tried, setTried] = useState(false);
  // Blank is a real answer here (it clears the amendment); only a bad figure is told.
  const error = draft === null ? undefined : numberFieldError(draft, { min: 0, optional: true });
  const amended = s.amended_level !== null && s.amended_level !== s.suggested_level;

  const save = async () => {
    if (!onAmend || draft === null) return;
    if (error) {
      setTried(true);
      return;
    }
    const parsed = draft.trim() === '' ? null : Number(draft);
    await onAmend(s, parsed === s.suggested_level ? null : parsed);
    setDraft(null);
    setTried(false);
  };

  return (
    <tr className="border-t">
      <td className="max-w-64 py-1.5 pr-3">
        <span className="block truncate font-medium" title={codeWithName(s.product_code, s.product_name)}>
          {s.product_code}
        </span>
        {distinctName(s.product_code, s.product_name) ? (
          <span className="block truncate text-xs text-muted-foreground" title={s.product_name}>
            {s.product_name}
          </span>
        ) : null}
      </td>
      <td className="py-1.5 pr-3">{s.warehouse_code ?? EM_DASH}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums">{s.current_level ?? 'none'}</td>
      <td className="py-1.5 pr-3 text-right">
        {onAmend ? (
          <NumberInput
            min={0}
            inputMode="decimal"
            className="ml-auto h-7 w-24 text-right tabular-nums"
            aria-label={`Set level for ${s.product_code}`}
            error={tried ? error : undefined}
            errorClassName="text-right"
            value={draft ?? String(effectiveLevel(s))}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => void save()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
            }}
          />
        ) : (
          <span className="font-medium tabular-nums">{effectiveLevel(s)}</span>
        )}
      </td>
      {/* The engine's figure stays on the row once an amendment displaces it. */}
      <td className="py-1.5 pr-3 text-right tabular-nums text-muted-foreground">
        {amended ? s.suggested_level : EM_DASH}
      </td>
      {/* What SIZED the level, not a mood: the lead time it covers. A trend badge used
          to sit here, back when a trajectory verdict leaned the rounding. */}
      <td className="py-1.5">
        {s.basis.lead_time_days ? (
          <Badge variant="secondary" appearance="light" size="sm">
            {`${s.basis.lead_time_days} day lead`}
          </Badge>
        ) : (
          EM_DASH
        )}
      </td>
    </tr>
  );
}
