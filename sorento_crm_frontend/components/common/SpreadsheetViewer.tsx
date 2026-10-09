'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { observeElementRect, useVirtualizer } from '@tanstack/react-virtual';
import { ChevronDown, ChevronUp, Ellipsis } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverPortal, PopoverTrigger } from '@/components/ui/popover';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type {
  CellStyle,
  SheetMerge,
  SpreadsheetCell,
  SpreadsheetSheet,
  SpreadsheetWorkbook,
} from '@/lib/sheet-viewer/types';
import { cn } from '@/lib/utils';
import {
  cellText,
  columnCount,
  columnLetters,
  findMatches,
  formatCount,
  indexMerges,
  looksNumeric,
  numericColumns,
  sizeColumns,
  splitHighlightSegments,
} from './spreadsheet-viewer/helpers';

/**
 * A workbook shown in our own grid style (PLAN-excel-preview-26sep, section 3): one line tab
 * per sheet, a "..." at the end of the tab strip that searches every sheet and jumps to the one
 * picked (Excel's own sheet list; owner hand test 26 Sep), an optional row search, the header
 * row frozen, the first column pinned, numbers right-aligned, and only
 * the rows in view in the DOM, so a 50,000-row sheet scrolls like a 50-row one.
 *
 * It renders a normalised model, never a file: S1 feeds it the low stock report's view (the
 * workbook before it exists); S2 adds a SheetJS feeder for stored files. Not a `DataGrid`:
 * a sheet is not a record list (no row click, no column config, no pagination), and a
 * workbook can hold hundreds of sheets of different columns.
 */

export type { CellStyle, SheetMerge, SpreadsheetCell, SpreadsheetSheet, SpreadsheetWorkbook };

export interface SpreadsheetViewerProps {
  workbook: SpreadsheetWorkbook;
  /** The open sheet, by title. Unknown or omitted opens the first sheet. */
  activeSheet?: string | null;
  onActiveSheetChange?: (title: string) => void;
  /** Previous content kept on screen while the next arrives. */
  busy?: boolean;
  /** Headers of the columns the row search reads; omitted, there is no search box. */
  searchColumns?: string[];
  searchPlaceholder?: string;
  /** `filter` hides rows that do not match; `highlight` marks every match and steps through them. */
  searchMode?: 'filter' | 'highlight';
  tabsPosition?: 'top' | 'bottom';
  className?: string;
}

const ROW_HEIGHT = 32;
/** The Excel-style grid is denser, and its row-number column is narrow. */
const CHROME_ROW_HEIGHT = 24;
const ROW_NUMBER_WIDTH = 44;

/**
 * What the virtualiser assumes when the scroller measures 0px tall: a container that is not
 * laid out yet (or a test DOM, which lays nothing out). A laid-out scroller holding rows is
 * never 0px, so this only ever stands in for a real measurement that does not exist.
 */
const FALLBACK_RECT = { width: 1280, height: 640 };

const EMPTY_STYLES: (CellStyle | null)[] = [];

function highlightCell(text: string, query: string) {
  return splitHighlightSegments(text, query).map((segment, i) =>
    segment.match ? (
      <mark key={i} className="rounded-sm bg-amber-300 px-0.5 text-zinc-900">
        {segment.text}
      </mark>
    ) : (
      <span key={i}>{segment.text}</span>
    ),
  );
}

export function SpreadsheetViewer({
  workbook,
  activeSheet,
  onActiveSheetChange,
  busy = false,
  searchColumns,
  searchPlaceholder = 'Search',
  searchMode = 'filter',
  tabsPosition = 'top',
  className,
}: SpreadsheetViewerProps) {
  const [localSheet, setLocalSheet] = useState<string | null>(null);
  const [sheetListOpen, setSheetListOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [activeMatch, setActiveMatch] = useState(0);
  const requested = activeSheet !== undefined ? activeSheet : localSheet;
  const sheets = workbook.sheets;
  const sheet = sheets.find((s) => s.title === requested) ?? sheets[0];
  const chrome = Boolean(sheet?.excelChrome);
  const highlight = searchMode === 'highlight';
  const rowHeight = chrome ? CHROME_ROW_HEIGHT : ROW_HEIGHT;

  // Opening a tab scrolls the strip to it: TabsList keeps the active tab in view.
  const select = (title: string) => {
    setLocalSheet(title);
    setActiveMatch(0);
    onActiveSheetChange?.(title);
  };

  // The search stays as typed across sheets, so one code can be looked up sheet after sheet.
  const needle = search.trim().toLowerCase();
  const rows = useMemo(() => {
    if (!sheet || highlight || !needle || !searchColumns) return sheet?.rows ?? [];
    const cols = searchColumns
      .map((header) => sheet.columns.indexOf(header))
      .filter((col) => col >= 0);
    return sheet.rows.filter((row) =>
      cols.some((col) => cellText(row[col] ?? null).toLowerCase().includes(needle)),
    );
  }, [sheet, needle, searchColumns, highlight]);

  const matches = useMemo(
    () => (sheet && highlight ? findMatches(sheet, needle) : []),
    [sheet, highlight, needle],
  );
  const current = matches.length > 0 ? matches[Math.min(activeMatch, matches.length - 1)] : null;

  const cols = useMemo(() => (sheet ? columnCount(sheet) : 0), [sheet]);
  const numeric = useMemo(() => (sheet ? numericColumns(sheet) : []), [sheet]);
  const widths = useMemo(() => (sheet ? sizeColumns(sheet) : []), [sheet]);
  const merges = useMemo(
    () => (sheet ? indexMerges(sheet, cols) : { heads: new Map(), covered: new Set<string>() }),
    [sheet, cols],
  );
  const frozen = sheet?.frozenCols ?? (chrome ? 0 : 1);
  const pinned = useMemo(
    () => [...(sheet?.pinnedRows ?? [])].filter((r) => r < rows.length).sort((a, b) => a - b),
    [sheet, rows.length],
  );
  const lead = chrome ? [`${ROW_NUMBER_WIDTH}px`] : [];
  const template = [...lead, ...widths.map((w) => `${w}px`)].join(' ');
  const totalWidth = widths.reduce((sum, w) => sum + w, 0) + (chrome ? ROW_NUMBER_WIDTH : 0);
  const frozenLeft = (col: number) =>
    (chrome ? ROW_NUMBER_WIDTH : 0) + widths.slice(0, col).reduce((sum, w) => sum + w, 0);

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
    initialRect: FALLBACK_RECT,
    observeElementRect: (instance, callback) =>
      observeElementRect(instance, (rect) => callback(rect.height > 0 ? rect : FALLBACK_RECT)),
  });
  const scrollOffset = virtualizer.scrollOffset ?? 0;

  // Step to a match: bring its row to the middle and its column into view.
  const stepTo = (index: number) => {
    if (matches.length === 0) return;
    const next = (index + matches.length) % matches.length;
    setActiveMatch(next);
    const match = matches[next];
    virtualizer.scrollToIndex(match.r, { align: 'center' });
    const scroller = scrollRef.current;
    if (scroller && match.c >= frozen) {
      const left = frozenLeft(match.c);
      const right = left + (widths[match.c] ?? 0);
      const visibleLeft = scroller.scrollLeft + frozenLeft(frozen);
      if (left < visibleLeft) scroller.scrollLeft = Math.max(0, left - frozenLeft(frozen));
      else if (right > scroller.scrollLeft + scroller.clientWidth) {
        scroller.scrollLeft = right - scroller.clientWidth;
      }
    }
  };

  // A new term starts again at the first match and shows it.
  useEffect(() => {
    setActiveMatch(0);
  }, [needle]);
  useEffect(() => {
    if (highlight && needle && matches.length > 0) {
      virtualizer.scrollToIndex(matches[0].r, { align: 'center' });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- jump on a new term or sheet only
  }, [needle, sheet?.title]);

  if (!sheet) return null;

  const cellClass = (col: number, style: CellStyle | null, text: string) => {
    const right =
      style?.align === 'right' ||
      (!style?.align && (chrome ? looksNumeric(text) : numeric[col]));
    return cn(
      'min-w-0 overflow-hidden border-e border-border px-2.5',
      chrome ? 'leading-6' : 'leading-8',
      style?.wrap ? 'whitespace-normal break-words' : 'truncate',
      right && 'text-end tabular-nums',
      style?.align === 'center' && 'text-center',
      style?.bold && 'font-bold',
      style?.italic && 'italic',
      col < frozen && 'sticky z-10 bg-background',
      chrome && 'border-b',
    );
  };

  const cellStyle = (col: number, style: CellStyle | null): React.CSSProperties => ({
    ...(style?.fill ? { backgroundColor: style.fill } : null),
    ...(style?.color ? { color: style.color } : null),
    ...(col < frozen ? { insetInlineStart: frozenLeft(col) } : null),
  });

  const renderRow = (index: number, extra: React.CSSProperties, rowClass: string) => {
    const row = rows[index];
    const styles = sheet.cells?.[index] ?? EMPTY_STYLES;
    const hasTall = chrome
      ? Array.from(merges.heads.entries()).some(([k, v]) => v.rowSpan > 1 && k.startsWith(`${index}:`))
      : false;
    return (
      <div
        key={`row-${index}`}
        role="row"
        aria-rowindex={index + 2}
        className={cn('grid', chrome ? '' : 'border-b border-border', rowClass)}
        style={{
          gridTemplateColumns: template,
          height: rowHeight,
          ...(hasTall ? { zIndex: 1 } : null),
          ...extra,
        }}
      >
        {chrome ? (
          <div
            role="rowheader"
            className="sticky start-0 z-10 border-b border-e border-border bg-muted text-center text-xs leading-6 text-muted-foreground"
          >
            {index + 1}
          </div>
        ) : null}
        {Array.from({ length: cols }, (_, col) => {
          const key = `${index}:${col}`;
          if (merges.covered.has(key)) return null;
          const span = merges.heads.get(key);
          const style = styles[col] ?? null;
          const text = cellText(row[col] ?? null);
          const isMatch = highlight && needle && text.toLowerCase().includes(needle);
          const isCurrent = current?.r === index && current?.c === col;
          const position: React.CSSProperties = span
            ? {
                gridColumn: `${col + 1 + (chrome ? 1 : 0)} / span ${span.colSpan}`,
                gridRow: `1 / span ${span.rowSpan}`,
                height: span.rowSpan * rowHeight,
              }
            : {};
          return (
            <div
              key={col}
              role="gridcell"
              title={text}
              aria-colspan={span && span.colSpan > 1 ? span.colSpan : undefined}
              aria-rowspan={span && span.rowSpan > 1 ? span.rowSpan : undefined}
              className={cn(
                cellClass(col, style, text),
                span && 'bg-background',
                isCurrent && 'outline-2 -outline-offset-2 outline-primary',
              )}
              style={{ ...cellStyle(col, style), ...position }}
            >
              {isMatch ? highlightCell(text, needle) : text}
            </div>
          );
        })}
      </div>
    );
  };

  const sheetBar = (
    <div
      data-slot="spreadsheet-sheet-bar"
      className={cn(
        'flex min-w-0 items-center gap-1 pe-2',
        tabsPosition === 'bottom' && 'border-t border-border',
      )}
    >
      <Tabs value={sheet.title} onValueChange={select} className="min-w-0 flex-1">
        <TabsList variant="line" size="sm" className="px-2" aria-label="Sheets">
          {sheets.map((s) => (
            <TabsTrigger key={s.title} value={s.title}>
              {s.flagged ? (
                <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-warning" />
              ) : null}
              {s.title}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <Popover open={sheetListOpen} onOpenChange={setSheetListOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="All sheets"
            title="All sheets"
            className="size-7 shrink-0"
          >
            <Ellipsis />
          </Button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            align="end"
            className="flex max-h-(--radix-popper-available-height) w-72 max-w-(--radix-popper-available-width) flex-col p-0"
          >
            <Command className="flex max-h-full min-h-0 flex-col">
              <CommandInput placeholder="Search sheets" />
              <CommandList className="min-h-0 flex-1 overflow-y-auto">
                <CommandEmpty>No sheet found</CommandEmpty>
                {sheets.map((s) => (
                  <CommandItem
                    key={s.title}
                    value={s.title}
                    onSelect={() => {
                      select(s.title);
                      setSheetListOpen(false);
                    }}
                    className={cn(s.title === sheet.title && 'font-medium')}
                  >
                    {s.flagged ? (
                      <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-warning" />
                    ) : null}
                    <span className="min-w-0 break-words">{s.title}</span>
                  </CommandItem>
                ))}
              </CommandList>
            </Command>
          </PopoverContent>
        </PopoverPortal>
      </Popover>
    </div>
  );

  const stuck = (k: number) => scrollOffset >= (pinned[k] - k) * rowHeight;
  const stuckRows = pinned.filter((_, k) => stuck(k));

  return (
    <div className={cn('flex min-w-0 flex-col', className)} data-slot="spreadsheet-viewer">
      {tabsPosition === 'top' ? sheetBar : null}

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-2 py-1.5">
        {searchColumns || highlight ? (
          <Input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => {
              if (highlight && event.key === 'Enter') {
                event.preventDefault();
                stepTo(activeMatch + (event.shiftKey ? -1 : 1));
              }
            }}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            className="h-8 w-full sm:w-64"
          />
        ) : null}
        {highlight && needle ? (
          <>
            <span className="text-xs tabular-nums text-muted-foreground">
              {matches.length > 0
                ? `${Math.min(activeMatch, matches.length - 1) + 1} of ${matches.length}`
                : 'No matches'}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label="Previous match"
              title="Previous match"
              disabled={matches.length === 0}
              onClick={() => stepTo(activeMatch - 1)}
            >
              <ChevronUp />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label="Next match"
              title="Next match"
              disabled={matches.length === 0}
              onClick={() => stepTo(activeMatch + 1)}
            >
              <ChevronDown />
            </Button>
          </>
        ) : null}
        {chrome ? null : (
          <span className="ms-auto text-xs tabular-nums text-muted-foreground">
            {formatCount(rows.length)} {rows.length === 1 ? 'row' : 'rows'}
          </span>
        )}
      </div>

      <div
        ref={scrollRef}
        data-testid="spreadsheet-scroller"
        className={cn(
          'relative min-h-0 min-w-0 max-h-(--grid-max-h) flex-1 overflow-auto',
          busy && 'opacity-60',
        )}
        style={{ touchAction: 'pan-x pan-y pinch-zoom' }}
        aria-busy={busy || undefined}
      >
        <div
          role="grid"
          aria-label={sheet.title}
          aria-rowcount={rows.length + 1}
          aria-colcount={cols}
          className="text-2sm"
          style={{ width: totalWidth, minWidth: '100%' }}
        >
          <div
            role="row"
            aria-rowindex={1}
            className={cn(
              'sticky top-0 z-20 grid border-b border-border',
              chrome ? 'bg-muted' : 'bg-background/90 backdrop-blur-xs',
            )}
            style={{ gridTemplateColumns: template, height: rowHeight }}
          >
            {chrome ? (
              <div
                aria-hidden
                className="sticky start-0 z-10 border-e border-border bg-muted"
              />
            ) : null}
            {Array.from({ length: cols }, (_, col) => (
              <div
                key={col}
                role="columnheader"
                title={chrome ? columnLetters(col) : sheet.columns[col]}
                className={cn(
                  cellClass(col, null, ''),
                  chrome
                    ? 'bg-muted text-center text-xs font-medium text-muted-foreground'
                    : 'text-xs font-medium text-muted-foreground',
                )}
                style={col < frozen ? { insetInlineStart: frozenLeft(col) } : undefined}
              >
                {chrome ? columnLetters(col) : sheet.columns[col]}
              </div>
            ))}
          </div>

          {stuckRows.map((index, k) =>
            renderRow(
              index,
              { top: rowHeight * (k + 1), marginBottom: -rowHeight },
              'sticky z-20 bg-background',
            ),
          )}

          {rows.length === 0 ? (
            <div className="px-4 py-12 text-center text-sm text-muted-foreground">
              {sheet.rows.length > 0 ? 'No matching rows' : (sheet.emptyText ?? 'No rows')}
            </div>
          ) : (
            <div className="relative" style={{ height: virtualizer.getTotalSize() }}>
              {virtualizer.getVirtualItems().map((item) =>
                stuckRows.includes(item.index)
                  ? null
                  : renderRow(
                      item.index,
                      { transform: `translateY(${item.start}px)` },
                      'absolute start-0 top-0 w-full bg-background',
                    ),
              )}
            </div>
          )}
        </div>
      </div>

      {tabsPosition === 'bottom' ? sheetBar : null}
    </div>
  );
}
