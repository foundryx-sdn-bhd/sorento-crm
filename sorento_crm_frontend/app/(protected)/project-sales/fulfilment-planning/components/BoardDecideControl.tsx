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
  isOwnLocationBorrow,
  suggestedDecisionFor,
  type DecideComposition,
  type DecideWay,
} from '../../_shared/lib/boardAmend';
import { matchesSuggestion } from '../../_shared/lib/fulfilmentBoard';
import { useLineSplitOpener } from './lineSplitOpener';
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
  { way: 'cancel_so' },
];

/** "As suggested" is not one of `supplyVocabulary`'s seven words - it names no supply kind. */
const AS_SUGGESTED_LABEL = 'As suggested';

/** Not a supply kind either: the whole line goes to the Cancel SO document (SO-CANCEL-LINE). */
const CANCEL_SO_LABEL = 'Transfer to Cancel SO';

function labelOf(way: DecideWay): string {
  if (way === 'suggested') return AS_SUGGESTED_LABEL;
  if (way === 'cancel_so') return CANCEL_SO_LABEL;
  return LABELS[way];
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
      } else if (
        candidate.source === 'other_location' &&
        !candidate.donor_so_number &&
        // The line's own location is always listed, at 0 free and never capped (owner, 14
        // Oct, overriding BORROW-OWN-LOC review S5); any other location only with stock.
        (toMinor(candidate.free_qty) > 0 || isOwnLocationBorrow(row, candidate))
      ) {
        // K8: a donor-named candidate is an order borrow, never a location's.
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
  /** The row's own key, when the entry is about a row this press saved. */
  key?: string;
}

/**
 * "7 saved as Buy · 2 skipped: CB6633 line 4 (only 3 free at BRW), ... and 1 more", or, when
 * nothing was saved at all (AC-17), the neutral "0 saved · n skipped: ..." with no Label to
 * name - saving nothing "as Buy" reads like a Buy that landed.
 */
function toastMessage(
  saved: number,
  label: string,
  skipped: SkippedEntry[],
  short: SkippedEntry[] = [],
): string {
  const head = saved > 0 ? `${saved} saved as ${label}` : `${saved} saved`;
  const listed = (entries: SkippedEntry[], word: string) => {
    if (entries.length === 0) return '';
    const named = entries.slice(0, 3).map((entry) => `${entry.label} (${entry.why})`);
    const more = entries.length > 3 ? `, and ${entries.length - 3} more` : '';
    return ` · ${entries.length} ${word}: ${named.join(', ')}${more}`;
  };
  // OI-CLOSED-LINE: a short line is SAVED as decided, so it is named as short, never skipped.
  return `${head}${listed(short, 'short')}${listed(skipped, 'skipped')}`;
}

export function BoardDecideControl({
  contributions,
  selectedKeys,
  draft,
  onSave,
  onSaved,
  onClear,
  embedded = false,
  borrowFromOrderEnabled = false,
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
  /** The board's "Borrow from another order" switch (BORROW-LOCATION-ONLY BR1). */
  borrowFromOrderEnabled?: boolean;
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
  // OI-LINE-SPLIT (UAC A1): every ticked line on ONE sales order opens that order's split
  // view, zoomed to the first ticked line.
  const splitOpener = useLineSplitOpener();
  const splitRow =
    splitOpener &&
    tickedRows.length > 0 &&
    tickedRows.every((row) => row.sales_order_id === tickedRows[0].sales_order_id) &&
    splitOpener.canOpen(tickedRows[0])
      ? tickedRows[0]
      : null;

  // "Borrow from another order" is offered exactly when the setting is on (BORROW-LOCATION-
  // ONLY BR1); off, every borrow is a location's.
  const menu = React.useMemo(
    () =>
      MENU_ORDER.filter((entry) => entry.way !== 'borrow_order' || borrowFromOrderEnabled),
    [borrowFromOrderEnabled],
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
    if (!way)
      return {
        coverable: 0,
        skipped: [] as [string, number][],
        short: [] as [string, number][],
      };
    const claimed = new Map<string, number>();
    let coverable = 0;
    const skippedBy = new Map<string, number>();
    const shortBy = new Map<string, number>();
    for (const row of tickedRows) {
      const result = decideComposition(row, way, pick || undefined, claimed);
      if (!result.skip) coverable += 1;
      else skippedBy.set(result.skip, (skippedBy.get(result.skip) ?? 0) + 1);
      if (result.warning)
        shortBy.set(result.warning, (shortBy.get(result.warning) ?? 0) + 1);
    }
    return {
      coverable,
      skipped: [...skippedBy.entries()],
      short: [...shortBy.entries()],
    };
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
        const short: SkippedEntry[] = [];
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
          if (result.warning)
            short.push({ label: labelFor(row), why: result.warning, key: row.key });
          const trimmedReason = chosenReason.trim();
          if (chosenWay === 'cancel_so') {
            saves.push({
              key: row.key,
              decision: {
                verdict: 'cancelled',
                reserve: [],
                borrow: [],
                timely_spo_qty: '0',
                buy_qty: '0',
                order_back: false,
                reason: trimmedReason,
              },
            });
            continue;
          }
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
        const savedSet = new Set(savedKeys);
        const savedShort = short.filter((entry) => entry.key && savedSet.has(entry.key));
        const message = toastMessage(
          savedKeys.length,
          labelOf(chosenWay),
          skipped,
          savedShort,
        );
        // A line saved short of stock reads amber, the way the board's Confirm summary does.
        if (savedKeys.length > 0 && savedShort.length > 0) toast.warning(message);
        else if (savedKeys.length > 0) toast.success(message);
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
    if (!borrow && chosen !== 'cancel_so') {
      // AC-12: every ticked (uncovered) row's suggestion already IS this pick -> no dialog.
      const claimed = new Map<string, number>();
      const allMatch = tickedRows.every((row) => {
        if (row.covered) return false;
        const result = decideComposition(row, chosen, undefined, claimed);
        if (result.skip || result.warning) return false;
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
  const nothingToSave = preview.coverable === 0;
  const valid = nothingToSave
    ? false
    : isBorrow
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
          {menu.map((entry) => (
            <React.Fragment key={entry.way}>
              <DropdownMenuItem onSelect={() => chooseWay(entry.way)}>
                {labelOf(entry.way)}
              </DropdownMenuItem>
              {entry.separatorAfter && <DropdownMenuSeparator />}
            </React.Fragment>
          ))}
          {splitOpener ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                disabled={!splitRow}
                onSelect={() => splitRow && splitOpener.open(splitRow)}
              >
                Split delivery…
              </DropdownMenuItem>
            </>
          ) : null}
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
            {(isBorrow && !pick ? [] : preview.short).map(([why, count]) => (
              <p key={`short-${why}`} className="text-sm text-warning">
                {`${count} line${count === 1 ? '' : 's'} short: ${why}`}
              </p>
            ))}
            {(isBorrow && !pick ? [] : preview.skipped).map(([why, count]) => (
              <p key={why} className="text-sm text-muted-foreground">
                {`${count} line${count === 1 ? '' : 's'} will be skipped: ${why}`}
              </p>
            ))}
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
