'use client';

/**
 * Tag Size control (S1, lifted from `RequestTagDesigner.tsx` so the template
 * editor can show the same one - the one thing it could not do before this
 * round was change a tag's size after creating it).
 *
 * Preset dropdown (published templates' own sizes, then saved sizes, each
 * deletable via `onDeleteSavedSize`) + custom W/H committed on blur/Enter,
 * not per keystroke - the caller's `width_mm`/`height_mm` changing on every
 * digit would remount whatever canvas is mounted on it before the second
 * one lands.
 *
 * Deliberately react-query-FREE: "Saved sizes" (`useTagSizesQuery`),
 * deleting one (`useDeleteTagSizePreset`) and "Save as size"
 * (`SaveAsSizeDialog`'s own `useCreateTagSize`) all need a
 * `QueryClientProvider`, which the template editor - one of this
 * component's two mount points - does not always have in the tree it is
 * unit-tested in. The caller owns all three (data, delete, the dialog) and
 * hands this component plain data plus callbacks; absent, this simply does
 * not offer that affordance rather than reaching for the hook itself.
 *
 * `onResizeAll` absent hides "Apply to all lines" (a template has no other
 * lines); `bounds` absent means the `MIN_TAG_SIZE_MM` floor only, no ceiling
 * (a template has no sheet to fit, unlike a request's imposition).
 */

import { useEffect, useState } from 'react';
import { ChevronDown, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { cn } from '@/lib/utils';
import {
  MIN_TAG_SIZE_MM,
  resolveSizeGrid,
  resolveTagSize,
  type SheetGridConfig,
  type TagSizeBounds,
  type TagSizePreset,
} from '@/lib/dealer-kit/request-tags';
import type { TagSizeRecord } from '@/app/(protected)/dealer-kit/services/tagSizeService';

/** No ceiling, floor only - what an absent `bounds` prop means. */
const FLOOR_ONLY_BOUNDS: TagSizeBounds = {
  min_mm: MIN_TAG_SIZE_MM,
  max_width_mm: Infinity,
  max_height_mm: Infinity,
};

/**
 * Never a real option in the SELECT - a size nobody picked from the list has
 * nothing to select TO. `value` is set to this whenever the current size
 * matches no preset, so the trigger falls through to `placeholder="Custom"`
 * the same way every other unselected SearchableSelect shows its placeholder:
 * muted, and un-clickable in the list.
 */
const CUSTOM_SIZE_VALUE = '__custom__';

function sizeKey(width_mm: number, height_mm: number): string {
  return `${width_mm}x${height_mm}`;
}

/** D9: the panel's open/closed state, remembered per browser - one key, no
 *  server preference, no prop. Read/written inside try/catch so a browser
 *  with localStorage disabled just stays collapsed rather than crashing. */
const TAG_SIZE_OPEN_KEY = 'dealer-kit.tag-size.open';

function readTagSizeOpen(): boolean {
  try {
    return window.localStorage.getItem(TAG_SIZE_OPEN_KEY) === '1';
  } catch {
    return false;
  }
}

function writeTagSizeOpen(open: boolean): void {
  try {
    if (open) {
      window.localStorage.setItem(TAG_SIZE_OPEN_KEY, '1');
    } else {
      window.localStorage.removeItem(TAG_SIZE_OPEN_KEY);
    }
  } catch {
    // Best effort - a failed write just means the next mount collapses.
  }
}

export interface TagSizeControlProps {
  width_mm: number;
  height_mm: number;
  presets: TagSizePreset[];
  savedSizes?: TagSizeRecord[];
  bounds?: TagSizeBounds;
  onResize: (width_mm: number, height_mm: number) => void;
  onResizeAll?: (width_mm: number, height_mm: number) => void;
  /** Delete a saved size's `x` (absent hides it - no delete affordance without one). */
  onDeleteSavedSize?: (id: string, name: string) => void;
  /** Which saved size's delete is in flight, so its `x` can dim/disable. */
  deletingSavedSizeId?: string | null;
  /** "Save as size" button (absent hides it - the caller owns the dialog). */
  onSaveAsSize?: () => void;
  /**
   * The per-A4 grid CONFIGURED for this size (S7, AC-S7-14), or null/absent
   * for none - in which case the row shows what arrange would derive on its
   * own, greyed. Absent `onSheetGridChange` hides the whole row (a template
   * with no print size yet, say).
   */
  sheetGrid?: SheetGridConfig | null;
  /** Commits a typed grid - to the template's print size (template editor)
   *  or the size preset (request designer's Save as size). `null` clears it
   *  back to Auto (AC-S7-14). */
  onSheetGridChange?: (grid: SheetGridConfig | null) => void;
}

export function TagSizeControl({
  width_mm,
  height_mm,
  presets,
  savedSizes = [],
  bounds = FLOOR_ONLY_BOUNDS,
  onResize,
  onResizeAll,
  onDeleteSavedSize,
  deletingSavedSizeId,
  onSaveAsSize,
  sheetGrid = null,
  onSheetGridChange,
}: TagSizeControlProps) {
  // Held as TEXT and committed on blur/Enter, not on every keystroke: the
  // control used to call `onResize` per keystroke, which changed a doc key
  // the canvas was mounted on and remounted the whole editor (this control's
  // own DOM included) after the first digit, so "95" typed as fast as
  // anyone can type landed as "9". `null` means "nothing typed right now" -
  // the field shows the live value, which is what lets a preset pick update
  // the boxes without an effect fighting whatever is mid-typed in them.
  const [wDraft, setWDraft] = useState<string | null>(null);
  const [hDraft, setHDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which box the sentence belongs to (null: it is about a picked size, shown loose).
  const [errorAxis, setErrorAxis] = useState<'w' | 'h' | null>(null);
  // Per-A4 grid drafts (S7, AC-S7-14) - same "text until blur" pattern as
  // width/height above, kept separate so typing cols does not fight rows.
  const [colsDraft, setColsDraft] = useState<string | null>(null);
  const [rowsDraft, setRowsDraft] = useState<string | null>(null);
  const [gridError, setGridError] = useState<string | null>(null);
  const [gridAxis, setGridAxis] = useState<'cols' | 'rows' | null>(null);
  // D9: collapsed by default; a viewer who opens it once keeps it open on
  // their next request, on this browser.
  //
  // S5 (code review): starts collapsed and reads localStorage in an EFFECT,
  // not the `useState` initialiser - the template page renders this
  // server-side, where localStorage does not exist, so a value read at
  // mount time (during SSR) always disagrees with the client's real answer
  // and hydrates mismatched. An effect runs client-only, after hydration.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (readTagSizeOpen()) setOpen(true);
  }, []);
  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    writeTagSizeOpen(next);
  };

  const commit = (axis: 'w' | 'h') => {
    const draft = axis === 'w' ? wDraft : hDraft;
    const setDraft = axis === 'w' ? setWDraft : setHDraft;
    if (draft === null) return;
    // Blank, not a number or under the floor: keep what was typed, say why, send nothing.
    const message = numberFieldError(draft, { min: bounds.min_mm });
    if (message) {
      setError(message);
      setErrorAxis(axis);
      return;
    }
    const n = parseNumberDraft(draft) as number;
    const candidateW = axis === 'w' ? n : width_mm;
    const candidateH = axis === 'h' ? n : height_mm;
    const result = resolveTagSize(candidateW, candidateH, bounds);
    if (!result.ok) {
      // Keep the typed value on screen next to the reason - reverting it
      // silently would read as the edit never happened.
      setError(result.reason);
      setErrorAxis(axis);
      return;
    }
    setError(null);
    setErrorAxis(null);
    onResize(result.width_mm, result.height_mm);
    setDraft(null);
  };

  const onEnter = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') e.currentTarget.blur();
  };

  // Per-A4 grid (S7, AC-S7-14): what arrange resolves for THIS size, whether
  // or not `sheetGrid` names a configured one - `resolved.configured` is what
  // tells the boxes below whether to show it plain or greyed, and
  // `resolved.refusedCell` is the one-line refusal (AC-S7-13).
  const resolvedGrid = resolveSizeGrid(width_mm, height_mm, sheetGrid);
  const commitGrid = (axis: 'cols' | 'rows') => {
    const draft = axis === 'cols' ? colsDraft : rowsDraft;
    const setDraft = axis === 'cols' ? setColsDraft : setRowsDraft;
    if (draft === null) return;
    const message = numberFieldError(draft, { min: 1, integer: true });
    if (message) {
      setGridError(message);
      setGridAxis(axis);
      return;
    }
    setGridError(null);
    setGridAxis(null);
    const n = parseNumberDraft(draft) as number;
    setDraft(null);
    const cols = axis === 'cols' ? n : (sheetGrid?.cols ?? resolvedGrid.cols);
    const rows = axis === 'rows' ? n : (sheetGrid?.rows ?? resolvedGrid.rows);
    onSheetGridChange?.({ cols, rows, turn: sheetGrid?.turn ?? resolvedGrid.rotation === 90 });
  };
  const setTurn = (turn: boolean) => {
    onSheetGridChange?.({
      cols: sheetGrid?.cols ?? resolvedGrid.cols,
      rows: sheetGrid?.rows ?? resolvedGrid.rows,
      turn,
    });
  };

  // Every size choice, template-derived AND saved, keyed the same way:
  // "Template sizes" first (not deletable here), then "Saved sizes" (each
  // with an `x`, when `onDeleteSavedSize` is given). `savedByKey` is what
  // lets `renderOption` find the RECORD behind a saved row - the option
  // itself only carries the size.
  const savedByKey = new Map(
    savedSizes.map((s) => [sizeKey(s.width_mm, s.height_mm), s] as const),
  );
  const options: SearchableSelectOption[] = [
    ...presets.map((p) => ({
      value: sizeKey(p.width_mm, p.height_mm),
      label: p.label,
      group: 'Template sizes',
    })),
    ...savedSizes.map((s) => ({
      value: sizeKey(s.width_mm, s.height_mm),
      label: `${s.name} (${s.width_mm} x ${s.height_mm} mm)`,
      group: 'Saved sizes',
    })),
  ];
  const allSizes: { width_mm: number; height_mm: number }[] = [
    ...presets,
    ...savedSizes,
  ];
  const matchingPreset = allSizes.find(
    (p) => p.width_mm === width_mm && p.height_mm === height_mm,
  );

  const applySize = (nextWidth_mm: number, nextHeight_mm: number) => {
    const result = resolveTagSize(nextWidth_mm, nextHeight_mm, bounds);
    if (!result.ok) {
      setError(result.reason);
      setErrorAxis(null);
      return;
    }
    setError(null);
    setErrorAxis(null);
    onResize(result.width_mm, result.height_mm);
  };

  return (
    <div className="flex shrink-0 flex-col border-b border-r">
      <Collapsible open={open} onOpenChange={handleOpenChange}>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center justify-between gap-2 p-3 text-left hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          >
            <span className="flex items-center gap-1.5">
              <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Tag Size
              </span>
              {!open && (
                <span className="text-xs text-foreground">
                  {width_mm} x {height_mm} mm
                </span>
              )}
            </span>
            <ChevronDown
              className={cn(
                'size-4 shrink-0 text-muted-foreground transition-transform',
                open && 'rotate-180',
              )}
              aria-hidden
            />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="flex flex-col gap-2 p-3 pt-0">
            <SearchableSelect
              value={
                matchingPreset
                  ? sizeKey(matchingPreset.width_mm, matchingPreset.height_mm)
                  : CUSTOM_SIZE_VALUE
              }
              onChange={(value) => {
                const found = allSizes.find(
                  (p) => sizeKey(p.width_mm, p.height_mm) === value,
                );
                if (!found) return;
                applySize(found.width_mm, found.height_mm);
              }}
              options={options}
              placeholder="Custom"
              renderOption={(opt) => {
                const saved = savedByKey.get(opt.value);
                return (
                  <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                    <span className="truncate break-words">{opt.label}</span>
                    {saved &&
                      onDeleteSavedSize &&
                      (() => {
                        const pending = deletingSavedSizeId === saved.id;
                        return (
                          <button
                            type="button"
                            aria-label={`Delete saved size ${saved.name}`}
                            disabled={pending}
                            className={cn(
                              'shrink-0 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-destructive',
                              pending && 'pointer-events-none opacity-50',
                            )}
                            onPointerDown={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                            }}
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              onDeleteSavedSize(saved.id, saved.name);
                            }}
                          >
                            <X className="size-3.5" />
                          </button>
                        );
                      })()}
                  </div>
                );
              }}
            />
            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col gap-1">
                <Label className="text-xs text-muted-foreground">W (mm)</Label>
                <NumberInput
                  className="h-7 px-2 text-xs"
                  aria-label="Tag width (mm)"
                  value={wDraft ?? width_mm}
                  step={0.5}
                  error={errorAxis === 'w' ? (error ?? undefined) : undefined}
                  onChange={(e) => setWDraft(e.target.value)}
                  onBlur={() => commit('w')}
                  onKeyDown={onEnter}
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label className="text-xs text-muted-foreground">H (mm)</Label>
                <NumberInput
                  className="h-7 px-2 text-xs"
                  aria-label="Tag height (mm)"
                  value={hDraft ?? height_mm}
                  step={0.5}
                  error={errorAxis === 'h' ? (error ?? undefined) : undefined}
                  onChange={(e) => setHDraft(e.target.value)}
                  onBlur={() => commit('h')}
                  onKeyDown={onEnter}
                />
              </div>
            </div>
            {error && errorAxis === null && <p className="text-2xs text-destructive">{error}</p>}

            {/* Per A4 grid (S7, AC-S7-14): configured (typed) numbers show
                plain; a value nobody set shows the DERIVED fit, greyed. */}
            {onSheetGridChange && (
              <div className="flex flex-col gap-1 border-t pt-2">
                <div className="flex items-center justify-between">
                  <Label className="text-xs text-muted-foreground">Per A4</Label>
                  {sheetGrid && (
                    <button
                      type="button"
                      className="text-2xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                      onClick={() => onSheetGridChange(null)}
                    >
                      Auto
                    </button>
                  )}
                </div>
                <div className={cn('flex items-center gap-1.5', !sheetGrid && 'text-muted-foreground')}>
                  <NumberInput
                    className="h-7 w-14 px-2 text-xs"
                    aria-label="Grid columns per A4"
                    min={1}
                    value={colsDraft ?? (sheetGrid?.cols ?? resolvedGrid.cols)}
                    error={gridAxis === 'cols' ? (gridError ?? undefined) : undefined}
                    errorClassName="w-40"
                    onChange={(e) => setColsDraft(e.target.value)}
                    onBlur={() => commitGrid('cols')}
                    onKeyDown={onEnter}
                  />
                  <span className="text-xs">x</span>
                  <NumberInput
                    className="h-7 w-14 px-2 text-xs"
                    aria-label="Grid rows per A4"
                    min={1}
                    value={rowsDraft ?? (sheetGrid?.rows ?? resolvedGrid.rows)}
                    error={gridAxis === 'rows' ? (gridError ?? undefined) : undefined}
                    errorClassName="w-40"
                    onChange={(e) => setRowsDraft(e.target.value)}
                    onBlur={() => commitGrid('rows')}
                    onKeyDown={onEnter}
                  />
                  <label className="ml-1 flex items-center gap-1 text-xs">
                    <input
                      type="checkbox"
                      aria-label="Turn 90 degrees"
                      checked={sheetGrid?.turn ?? resolvedGrid.rotation === 90}
                      onChange={(e) => setTurn(e.target.checked)}
                    />
                    Turn
                  </label>
                </div>
                {gridError && gridAxis === null && <p className="text-2xs text-destructive">{gridError}</p>}
                {resolvedGrid.refusedCell && (
                  <p className="text-2xs text-destructive">
                    Cell is {resolvedGrid.refusedCell.width_mm} x {resolvedGrid.refusedCell.height_mm} mm -
                    too small for this tag, using the best fit instead
                  </p>
                )}
              </div>
            )}

            <div className="flex gap-2">
              {onResizeAll && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 flex-1 text-xs"
                  onClick={() => onResizeAll(width_mm, height_mm)}
                >
                  Apply to all lines
                </Button>
              )}
              {!matchingPreset && onSaveAsSize && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 flex-1 text-xs"
                  onClick={onSaveAsSize}
                >
                  Save as size
                </Button>
              )}
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
