'use client';

import * as React from 'react';
import { ChevronDown } from 'lucide-react';
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { toast } from '@/lib/toast';
import {
  borrowCandidatesOf,
  canQuickSave,
  decideComposition,
  suggestedDecisionFor,
  type DecideComposition,
  type DecideWay,
} from '../../_shared/lib/boardAmend';
import { matchesSuggestion } from '../../_shared/lib/fulfilmentBoard';
import { toMinor } from '../../_shared/lib/supplyComposition';
import { LABELS } from '../../_shared/lib/supplyVocabulary';
import type {
  BoardContribution,
  BoardDecision,
  BoardDraft,
} from '../../_shared/types/fulfilmentPlanning.types';

/**
 * S3 (D1): the strip's Decide button, its menu and its lightbox dialog, for the two Borrow
 * items only (R13: always the app's own lightbox `Dialog`, never inline and never a page).
 *
 * Rendered ALWAYS (R4), whether or not anything is ticked - the disabled state and its
 * tooltip are what say there is nothing to decide yet, not the control's own absence.
 */

const MENU_ORDER: { way: DecideWay; separatorAfter?: boolean }[] = [
  { way: 'suggested', separatorAfter: true },
  { way: 'own' },
  { way: 'borrow_order' },
  { way: 'borrow_other' },
  { way: 'shared' },
  { way: 'buy' },
];

/** "As suggested" is not one of `supplyVocabulary`'s seven words - it names no supply kind. */
const AS_SUGGESTED_LABEL = 'As suggested';

function labelOf(way: DecideWay): string {
  return way === 'suggested' ? AS_SUGGESTED_LABEL : LABELS[way];
}

/**
 * Whether a composition `decideComposition` returned is the SAME one the frozen decision
 * already holds (R3/AC-52) - a covered row already decided exactly this way has nothing left
 * to save. Compared by warehouse/donor and quantity, the same granularity `amendNeedsReason`
 * compares a covered line's baseline at.
 */
function matchesFrozen(
  contribution: BoardContribution,
  result: DecideComposition,
): boolean {
  const frozen = contribution.decision;
  if (!frozen) return false;
  const sameTotal = (a: string | undefined, b: string | undefined) =>
    toMinor(a ?? '0') === toMinor(b ?? '0');
  const sameRows = <T extends { qty: string }>(
    left: T[],
    right: T[],
    key: (row: T) => string,
  ) => {
    const byKey = (rows: T[]) => {
      const map = new Map<string, number>();
      for (const row of rows)
        map.set(key(row), (map.get(key(row)) ?? 0) + toMinor(row.qty));
      return map;
    };
    const a = byKey(left);
    const b = byKey(right);
    if (a.size !== b.size) return false;
    for (const [k, v] of a) if (b.get(k) !== v) return false;
    return true;
  };
  return (
    sameTotal(frozen.buy_qty, result.buy_qty) &&
    sameTotal(frozen.timely_spo_qty, result.timely_spo_qty) &&
    sameRows(
      frozen.reserve,
      result.reserve ?? [],
      (row) => row.warehouse_id ?? '',
    ) &&
    sameRows(
      frozen.borrow,
      result.borrow ?? [],
      (row) => `${row.warehouse_id ?? ''}|${row.donor_project_id ?? ''}`,
    )
  );
}

/** The donor order / location options the picker offers, sorted by coverage (R12). */
function pickerOptions(
  rows: BoardContribution[],
  way: 'borrow_order' | 'borrow_other',
): SearchableSelectOption[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const seen = new Set<string>();
    for (const candidate of borrowCandidatesOf(row)) {
      if (way === 'borrow_order') {
        if (candidate.donor_so_number) seen.add(candidate.donor_so_number);
      } else if (candidate.source === 'other_location') {
        seen.add(candidate.warehouse_code);
      }
    }
    for (const key of seen) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([key]) => ({ value: key, label: key }));
}

/** A same-agent donor for the picked order, so the "Who authorised it" field can appear. */
function sameAgentCandidate(rows: BoardContribution[], donorSo: string) {
  for (const row of rows) {
    const found = borrowCandidatesOf(row).find(
      (candidate) =>
        candidate.donor_so_number === donorSo && candidate.same_agent,
    );
    if (found) return found;
  }
  return undefined;
}

interface SkippedEntry {
  label: string;
  why: string;
}

/**
 * "7 saved as Buy · 2 skipped: CB6633 line 4 (only 3 free at BRW), ... and 1 more", or, when
 * nothing was saved at all (AC-17), the neutral "0 saved · n skipped: ..." with no Label to
 * name - saving nothing "as Buy" reads like a Buy that landed.
 */
function toastMessage(saved: number, label: string, skipped: SkippedEntry[]): string {
  const head = saved > 0 ? `${saved} saved as ${label}` : `${saved} saved`;
  if (skipped.length === 0) return head;
  const named = skipped
    .slice(0, 3)
    .map((entry) => `${entry.label} (${entry.why})`);
  const more = skipped.length > 3 ? `, and ${skipped.length - 3} more` : '';
  return `${head} · ${skipped.length} skipped: ${named.join(', ')}${more}`;
}

export function BoardDecideControl({
  contributions,
  selectedKeys,
  draft,
  onSave,
  onSaved,
  onClear,
  embedded = false,
}: {
  /** Every row on screen, in the list's current sort order (R9). */
  contributions: BoardContribution[];
  selectedKeys: string[];
  draft: BoardDraft;
  /** Panel's chunked-PUT loop (D15's own, reused): one PUT per row, quiet, chunks of 5. */
  onSave: (
    entries: { key: string; decision: BoardDecision }[],
  ) => Promise<{ savedKeys: string[]; failed: { key: string; why: string }[] }>;
  /** Saved rows untick (R9); skipped rows stay ticked. */
  onSaved: (savedKeys: string[]) => void;
  onClear: () => void;
  /**
   * Placed in the shared list toolbar, whose bulk strip already says "N selected" and carries
   * Clear: this control then draws only the Decide button.
   */
  embedded?: boolean;
}) {
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [way, setWay] = React.useState<DecideWay | null>(null);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [pick, setPick] = React.useState('');
  const [reason, setReason] = React.useState('');
  const [authorisation, setAuthorisation] = React.useState('');
  const [saving, setSaving] = React.useState(false);

  const tickedRows = React.useMemo(
    () => contributions.filter((row) => selectedKeys.includes(row.key)),
    [contributions, selectedKeys],
  );

  const isBorrow = way === 'borrow_order' || way === 'borrow_other';
  const options = React.useMemo(
    () =>
      isBorrow
        ? pickerOptions(tickedRows, way as 'borrow_order' | 'borrow_other')
        : [],
    [isBorrow, tickedRows, way],
  );
  const sameAgent = React.useMemo(
    () =>
      way === 'borrow_order' && pick
        ? sameAgentCandidate(tickedRows, pick)
        : undefined,
    [way, pick, tickedRows],
  );

  // Preview only, for the Save button's own count and disabled state - a fresh tally each
  // render, never the tally the actual save below commits to.
  const preview = React.useMemo(() => {
    if (!way) return { coverable: 0 };
    const claimed = new Map<string, number>();
    let coverable = 0;
    for (const row of tickedRows) {
      const result = decideComposition(row, way, pick || undefined, claimed);
      if (!result.skip) coverable += 1;
    }
    return { coverable };
  }, [way, pick, tickedRows]);

  // Should fix 5 (review round 1): only `dialogOpen` closes the dialog now - `way`, `pick`,
  // `reason` and `authorisation` are left as they were rather than nulled out here, because
  // `<Dialog>` below stays mounted through the close and its own `AnimatePresence` needs
  // something to keep rendering while it plays the exit spring. `chooseWay` already
  // overwrites every one of them the next time the dialog opens, so nothing here is read
  // stale.
  // Nit (review round 1): `useCallback` here, not a plain function, so `runSave` below can
  // name it in its own deps instead of an `eslint-disable` that does not match the rest of
  // this file's idiom.
  const reset = React.useCallback(() => {
    setDialogOpen(false);
  }, []);

  const runSave = React.useCallback(
    async (chosenWay: DecideWay, chosenPick: string, chosenReason: string) => {
      setSaving(true);
      try {
        const claimed = new Map<string, number>();
        const saves: { key: string; decision: BoardDecision }[] = [];
        const skipped: SkippedEntry[] = [];
        const labelFor = (row: BoardContribution) =>
          `${row.item_code} line ${row.line_no}`;

        for (const row of tickedRows) {
          if (chosenWay === 'suggested') {
            if (!canQuickSave(row, draft)) {
              skipped.push({
                label: labelFor(row),
                why: row.covered ? 'already confirmed' : 'already saved',
              });
              continue;
            }
            saves.push({ key: row.key, decision: suggestedDecisionFor(row) });
            continue;
          }

          // Should fix 4 (review round 1): a covered row already decided exactly this way is
          // skipped for taking NOTHING new, so it must not claim a pile first and starve the
          // next ticked row on it. Probed against a throwaway, empty tally rather than the
          // real one - this row already owns whatever it is frozen at, so checking the SHAPE
          // costs the real tally nothing.
          if (row.covered) {
            const probe = decideComposition(
              row,
              chosenWay,
              chosenPick || undefined,
              new Map(),
            );
            if (!probe.skip && matchesFrozen(row, probe)) {
              skipped.push({
                label: labelFor(row),
                why: 'already decided that way',
              });
              continue;
            }
          }

          const result = decideComposition(
            row,
            chosenWay,
            chosenPick || undefined,
            claimed,
          );
          if (result.skip) {
            skipped.push({ label: labelFor(row), why: result.skip });
            continue;
          }
          const trimmedReason = chosenReason.trim();
          const borrow = (result.borrow ?? []).map((entry) => ({
            ...entry,
            reason: trimmedReason,
          }));
          const discontinued = Boolean(row.item_flags?.discontinued);
          const buyReason =
            discontinued && toMinor(result.buy_qty ?? '0') > 0
              ? trimmedReason
              : undefined;

          if (row.covered) {
            saves.push({
              key: row.key,
              decision: {
                verdict: 'amended',
                reserve: result.reserve,
                borrow,
                timely_spo_qty: result.timely_spo_qty,
                buy_qty: result.buy_qty,
                buy_reason: buyReason,
                order_back: result.order_back,
                reason: trimmedReason || undefined,
              },
            });
            continue;
          }

          const approved = matchesSuggestion(row, {
            timely_spo_qty: result.timely_spo_qty ?? '0',
            reserve: (result.reserve ?? []).map((entry) => ({
              qty: entry.qty,
              warehouse_id: entry.warehouse_id,
            })),
            borrow: (result.borrow ?? []).map((entry) => ({
              qty: entry.qty,
              warehouse_id: entry.warehouse_id,
              donor_project_id: entry.donor_project_id ?? null,
            })),
            buy_qty: result.buy_qty ?? '0',
          });
          saves.push({
            key: row.key,
            decision: {
              verdict: approved ? 'approved' : 'amended',
              reserve: result.reserve,
              borrow,
              timely_spo_qty: result.timely_spo_qty,
              buy_qty: result.buy_qty,
              buy_reason: buyReason,
              order_back: result.order_back,
              reason: approved ? undefined : trimmedReason || undefined,
            },
          });
        }

        const { savedKeys, failed } = await onSave(saves);
        for (const entry of failed) {
          const row = tickedRows.find((r) => r.key === entry.key);
          skipped.push({
            label: row ? labelFor(row) : entry.key,
            why: entry.why,
          });
        }
        onSaved(savedKeys);
        const message = toastMessage(savedKeys.length, labelOf(chosenWay), skipped);
        if (savedKeys.length > 0) toast.success(message);
        else toast(message);
      } finally {
        setSaving(false);
        reset();
      }
    },
    [tickedRows, draft, onSave, onSaved, reset],
  );

  const chooseWay = (chosen: DecideWay) => {
    setMenuOpen(false);
    if (chosen === 'suggested') {
      void runSave('suggested', '', '');
      return;
    }
    setWay(chosen);
    setPick('');
    setReason('');
    setAuthorisation('');
    const borrow = chosen === 'borrow_order' || chosen === 'borrow_other';
    if (!borrow) {
      // AC-12: every ticked (uncovered) row's suggestion already IS this pick -> no dialog.
      const claimed = new Map<string, number>();
      const allMatch = tickedRows.every((row) => {
        if (row.covered) return false;
        const result = decideComposition(row, chosen, undefined, claimed);
        if (result.skip) return false;
        return matchesSuggestion(row, {
          timely_spo_qty: result.timely_spo_qty ?? '0',
          reserve: (result.reserve ?? []).map((entry) => ({
            qty: entry.qty,
            warehouse_id: entry.warehouse_id,
          })),
          borrow: (result.borrow ?? []).map((entry) => ({
            qty: entry.qty,
            warehouse_id: entry.warehouse_id,
            donor_project_id: entry.donor_project_id ?? null,
          })),
          buy_qty: result.buy_qty ?? '0',
        });
      });
      if (allMatch) {
        void runSave(chosen, '', '');
        return;
      }
    }
    setDialogOpen(true);
  };

  const needsAuthorisation = Boolean(sameAgent);
  const valid = isBorrow
    ? Boolean(pick) &&
      reason.trim().length > 0 &&
      (!needsAuthorisation || authorisation.trim())
    : reason.trim().length > 0;

  return (
    <>
      {!embedded && selectedKeys.length > 0 ? (
        <Badge variant="secondary" className="h-8 gap-1 px-2.5 text-sm">
          {`${selectedKeys.length} selected`}
        </Badge>
      ) : null}
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        {selectedKeys.length === 0 ? (
          // Nit (review round 1): the `Tooltip` wraps the WHOLE trigger, outside
          // `DropdownMenuTrigger`, rather than sitting inside its `asChild` slot - `Tooltip`
          // renders no DOM of its own, so nested the other way `DropdownMenuTrigger`'s props
          // land on nothing (harmless only because the button is disabled either way).
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className="inline-flex">
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    size="sm"
                    disabled
                    data-testid="board-decide-button"
                  >
                    Decide
                    <ChevronDown className="size-4" aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
              </span>
            </TooltipTrigger>
            <TooltipContent>Tick the lines to decide</TooltipContent>
          </Tooltip>
        ) : (
          <DropdownMenuTrigger asChild>
            <Button type="button" size="sm" data-testid="board-decide-button">
              Decide
              <ChevronDown className="size-4" aria-hidden />
            </Button>
          </DropdownMenuTrigger>
        )}
        <DropdownMenuContent align="end">
          {MENU_ORDER.map((entry) => (
            <React.Fragment key={entry.way}>
              <DropdownMenuItem onSelect={() => chooseWay(entry.way)}>
                {labelOf(entry.way)}
              </DropdownMenuItem>
              {entry.separatorAfter && <DropdownMenuSeparator />}
            </React.Fragment>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {!embedded && selectedKeys.length > 0 ? (
        <Button type="button" size="sm" variant="ghost" onClick={onClear}>
          Clear
        </Button>
      ) : null}

      {
        // Should fix 5 (review round 1): mounted ALWAYS, never gated on `dialogOpen && way` -
        // `Dialog`'s own `AnimatePresence` (`components/ui/dialog.tsx`) is what plays
        // `SURFACE_SPRING_EXIT`, and it needs this element to still be in the tree while that
        // runs. Unmounting it the instant `dialogOpen` flips is what skipped the exit spring.
      }
      <Dialog open={dialogOpen} onOpenChange={(next) => !next && reset()}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{`Decide ${selectedKeys.length} lines: ${way ? labelOf(way) : ''}`}</DialogTitle>
          </DialogHeader>
          <DialogBody className="space-y-4">
            {isBorrow && (
              <div className="space-y-1.5">
                <Label htmlFor="decide-picker">
                  {way === 'borrow_order' ? 'Donor order' : 'Location'}
                </Label>
                <SearchableSelect
                  id="decide-picker"
                  value={pick}
                  onChange={setPick}
                  options={options}
                />
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="decide-reason">
                Reason<span className="text-destructive"> *</span>
              </Label>
              <Textarea
                id="decide-reason"
                rows={3}
                value={reason}
                placeholder="In your own words"
                onChange={(event) => setReason(event.target.value)}
              />
            </div>
            {needsAuthorisation && (
              <div className="space-y-1.5">
                <Label htmlFor="decide-authorisation">Who authorised it</Label>
                <Input
                  id="decide-authorisation"
                  value={authorisation}
                  onChange={(event) => setAuthorisation(event.target.value)}
                  placeholder="When, and how"
                />
              </div>
            )}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={reset}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={!valid || saving}
              onClick={() => {
                if (!way) return;
                const foldedReason =
                  sameAgent && authorisation.trim()
                    ? `Authorised by ${sameAgent.donor_agent_code ? `agent ${sameAgent.donor_agent_code}` : 'the sales agent'}: ${authorisation.trim()}. ${reason.trim()}`
                    : reason;
                void runSave(way, pick, foldedReason);
              }}
            >
              {`Save ${preview.coverable} line${preview.coverable === 1 ? '' : 's'}`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
