'use client';

import * as React from 'react';
import {
  type CellContext,
  type ColumnDef,
  type ExpandedState,
  type VisibilityState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import type { DragEndEvent } from '@dnd-kit/core';
import { arrayMove } from '@dnd-kit/sortable';
import { Columns3, MoreHorizontal, Plus, Trash2, Undo2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridScroller } from '@/components/ui/data-grid-table';
import { DataGridColumnVisibility } from '@/components/ui/data-grid-column-visibility';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import {
  DataGridTableDndRowHandle,
  DataGridTableDndRows,
} from '@/components/ui/data-grid-table-dnd-rows';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import type {
  QuotationLine,
  StagedQuotationLine,
} from '../../_shared/types/project.types';
import {
  formatMyrExact,
  isDecimalString,
  multiplyMoney,
} from '../../_shared/lib/money';
import { useQuotationLineImages } from '../../_shared/hooks/useProjects';
import { formatMyr } from './QuotationsPanel';
import {
  emptyDraft,
  INLINE_CHECKED,
  isInlineChecked,
  isUntouched,
  lineErrors,
  PRICED_PER_OPTIONS,
  totalFromDrafts,
  trimAmount,
  type LineDraft,
} from './quotationLineDraft';
import {
  QuotationLineFileCell,
  resolveLineFile,
  type LineFileKind,
  type LineFileShown,
} from './QuotationLineFileCell';
import { QuotationStockChip } from './QuotationStockChip';
import { LineFlags } from './QuotationLineFlags';

/** One line of the grid: the staged (or stored) line, and its true position in the scope. */
interface GridRow {
  key: string;
  /** Position in the WHOLE scope, so a search hides rows without renumbering the rest. */
  index: number;
  staged: StagedQuotationLine;
}

type MenuAction = 'details' | 'below' | 'section' | 'remove';

/**
 * Everything a cell needs beyond its own row, handed down by context rather than closed over.
 *
 * The columns are a module constant on purpose: a column whose cell function is re-created on a
 * render is a different component to React, so every input in it would remount (and lose the
 * caret) on every keystroke. Context changes re-render the cells; they never remount them.
 */
interface GridContextValue {
  editing: boolean;
  quotationId: string | null;
  fetchProducts: (query: string) => Promise<SearchableSelectOption[]>;
  resolveProduct: (
    line: QuotationLine | null,
    draft: LineDraft,
  ) => SearchableSelectOption | undefined;
  uomOptions: SearchableSelectOption[];
  uomError: unknown;
  onUomRetry: () => void;
  brandOptions: SearchableSelectOption[];
  stockGroup: string | null;
  total: string | null;
  update: (key: string, patch: LineDraft) => void;
  selectProduct: (key: string, option: SearchableSelectOption | null) => void;
  toggleRemoved: (key: string) => void;
  act: (key: string, action: MenuAction) => void;
  bandOpen: Set<string>;
  closeEmptyBand: (key: string) => void;
  openPreview: (kind: LineFileKind, shown: LineFileShown) => void;
  /** The row's name in every label (`describeLine`). */
  labelOf: (row: GridRow) => string;
  /** Rows whose description was left at least once: an empty one is marked from then on. */
  visited: Set<string>;
  markVisited: (key: string) => void;
}

const GridContext = React.createContext<GridContextValue | null>(null);

function useGrid(): GridContextValue {
  const value = React.useContext(GridContext);
  if (!value)
    throw new Error('QuotationLinesGrid cell rendered outside the grid');
  return value;
}

/**
 * How a row is named in labels: the stored code, else its stored description; a line never saved
 * goes by the product picked on it, so its name does not change when it is moved; else its
 * position.
 */
export function describeLine(
  line: QuotationLine | null,
  index: number,
  pickedCode?: string | null,
): string {
  if (line) return line.product_code ?? line.description ?? `line ${index + 1}`;
  return pickedCode || `line ${index + 1}`;
}

/**
 * The stored line, but only while it still describes the row on screen: a product changed in this
 * session has no photo, drawing or candidates resolved for it until Save.
 */
function resolvedLine(row: GridRow): QuotationLine | null {
  const { line, draft } = row.staged;
  if (!line) return null;
  return (line.product_id ?? '') === (draft.product_id ?? '') ? line : null;
}

const PICK_FIELD: Record<
  LineFileKind,
  'image_attachment_id' | 'drawing_attachment_id'
> = {
  photo: 'image_attachment_id',
  drawing: 'drawing_attachment_id',
};

/**
 * The stored line a photo or drawing cell may draw from. Not once the draft has cleared a pick the
 * line had stored (a product change clears both, and changing back does not restore them): the
 * server's cell answered for that pick, so showing it would claim a picture the save will not send.
 */
function fileLine(row: GridRow, kind: LineFileKind): QuotationLine | null {
  const line = resolvedLine(row);
  const field = PICK_FIELD[kind];
  if (!line) return null;
  return line[field] && !(row.staged.draft[field] ?? '') ? null : line;
}

// ------------------------------------------------------------------------------ cells

function ItemCell({ row }: CellContext<GridRow, unknown>) {
  return (
    <span className="block text-sm font-medium tabular-nums">
      {row.original.index + 1}
    </span>
  );
}

function FileCell({ row, kind }: { row: GridRow; kind: LineFileKind }) {
  const grid = useGrid();
  const field = PICK_FIELD[kind];
  const { staged } = row;
  const stored = fileLine(row, kind);
  // No stored answer for this product (a new line, or a product picked in this session): ask for
  // the product's own cells, the same answer a saved line with no picks gets.
  const productId = staged.draft.product_id ?? '';
  const live = useQuotationLineImages(!stored && productId ? productId : null);
  const line = React.useMemo<QuotationLine | null>(() => {
    if (stored) return stored;
    if (!productId || !live.data) return null;
    return {
      ...(staged.line ?? {
        id: row.key,
        version_id: '',
        is_non_standard: false,
        is_below_floor: false,
      }),
      product_id: productId,
      product_code:
        grid.resolveProduct(staged.line, staged.draft)?.label ?? null,
      image_attachment_id: null,
      drawing_attachment_id: null,
      product_image: live.data.product_image,
      technical_drawing: live.data.technical_drawing,
      unit_price: staged.draft.unit_price ?? '',
      quantity: staged.draft.quantity ?? '',
      line_total: '0',
      sort_order: row.index,
    };
  }, [
    grid,
    live.data,
    productId,
    row.index,
    row.key,
    staged.draft,
    staged.line,
    stored,
  ]);
  return (
    <QuotationLineFileCell
      kind={kind}
      line={line}
      label={grid.labelOf(row)}
      pickedId={staged.draft[field] ?? ''}
      editable={grid.editing && !staged.removed}
      onPick={(attachmentId) => grid.update(row.key, { [field]: attachmentId })}
      onPreview={(shown) => grid.openPreview(kind, shown)}
    />
  );
}

function PhotoCell({ row }: CellContext<GridRow, unknown>) {
  return <FileCell row={row.original} kind="photo" />;
}

function DrawingCell({ row }: CellContext<GridRow, unknown>) {
  return <FileCell row={row.original} kind="drawing" />;
}

/** A labelled SearchableSelect in a cell: the label is what names it to a screen reader. */
function CellSelect({
  label,
  ...props
}: { label: string } & React.ComponentProps<typeof SearchableSelect>) {
  const id = React.useId();
  return (
    <>
      <label className="sr-only" htmlFor={id}>
        {label}
      </label>
      <SearchableSelect id={id} size="sm" truncateTriggerLabel {...props} />
    </>
  );
}

function ProductCell({ row: tableRow }: CellContext<GridRow, unknown>) {
  const grid = useGrid();
  const row = tableRow.original;
  const { staged } = row;
  const label = grid.labelOf(row);
  const editable = grid.editing && !staged.removed;

  return (
    <div className="min-w-0 space-y-1">
      {editable ? (
        <CellSelect
          label={`Product on ${label}`}
          clearable
          value={staged.draft.product_id ?? ''}
          // The pick and its fill are one write, made in `onOptionChange`.
          onChange={() => undefined}
          onOptionChange={(option) => grid.selectProduct(row.key, option)}
          fetchOptions={grid.fetchProducts}
          selectedOption={grid.resolveProduct(staged.line, staged.draft)}
          placeholder="Pick a product"
          emptyMessage="No matches"
        />
      ) : (
        <span
          className="block truncate text-sm font-medium"
          title={staged.line?.product_code ?? undefined}
        >
          {staged.draft.product_id ? (staged.line?.product_code ?? '-') : ''}
        </span>
      )}
      <LineFlags
        quotationId={grid.quotationId}
        line={staged.line}
        draft={staged.draft}
      />
      {staged.removed && (
        <Badge variant="outline" className="text-2xs">
          Removed on save
        </Badge>
      )}
    </div>
  );
}

function DescriptionCell({ row: tableRow }: CellContext<GridRow, unknown>) {
  const grid = useGrid();
  const row = tableRow.original;
  const { staged } = row;
  const value = staged.draft.description ?? '';

  if (!grid.editing || staged.removed) {
    return (
      <p className="text-xs whitespace-pre-line break-words">{value || '-'}</p>
    );
  }

  // Marked as it is typed, or once the cell has been left empty, but never on a row nobody has
  // reached yet: empty is not wrong until somebody has been there.
  const message =
    isUntouched(staged.draft) && !grid.visited.has(row.key)
      ? undefined
      : lineErrors(staged.draft).description;
  const errorId = `${row.key}-description-error`;
  return (
    <div className="min-w-0">
      <Textarea
        variant="sm"
        rows={1}
        value={value}
        aria-label={`Description on ${grid.labelOf(row)}`}
        aria-invalid={message ? true : undefined}
        aria-describedby={message ? errorId : undefined}
        placeholder="Type what the customer reads"
        className="field-sizing-content min-h-8 resize-none px-2 py-1.5"
        onChange={(event) =>
          grid.update(row.key, { description: event.target.value })
        }
        onBlur={() => grid.markVisited(row.key)}
      />
      {message && (
        <p id={errorId} className="mt-1 text-xs text-destructive">
          {message}
        </p>
      )}
    </div>
  );
}

function BrandCell({ row: tableRow }: CellContext<GridRow, unknown>) {
  const grid = useGrid();
  const row = tableRow.original;
  const { staged } = row;
  const brand = staged.draft.brand_snapshot ?? '';

  if (!grid.editing || staged.removed) {
    return (
      <span className="block truncate text-sm" title={brand || undefined}>
        {brand || '-'}
      </span>
    );
  }

  return (
    <CellSelect
      label={`Brand on ${grid.labelOf(row)}`}
      clearable
      value={brand}
      onChange={(next) => grid.update(row.key, { brand_snapshot: next })}
      options={grid.brandOptions}
      // A brand typed before the list existed still shows as itself.
      selectedOption={brand ? { value: brand, label: brand } : undefined}
      placeholder="Brand"
      emptyMessage="No matches"
    />
  );
}

function QtyCell({ row: tableRow }: CellContext<GridRow, unknown>) {
  const grid = useGrid();
  const row = tableRow.original;
  const { staged } = row;
  const label = grid.labelOf(row);
  const quantity = staged.draft.quantity ?? '';
  const uom = staged.draft.uom ?? '';
  const productId = staged.draft.product_id ?? '';
  const chip = productId ? (
    <QuotationStockChip
      productId={productId}
      group={grid.stockGroup}
      quantity={quantity}
      uom={uom}
      label={label}
    />
  ) : null;

  if (!grid.editing || staged.removed) {
    return (
      <div className="flex flex-col items-end gap-1">
        <span className="text-sm tabular-nums">
          {quantity ? trimAmount(quantity) : '-'}
        </span>
        {chip}
      </div>
    );
  }

  const invalid = quantity.trim() !== '' && !isDecimalString(quantity);
  return (
    <div className="flex min-w-0 flex-col items-end gap-1">
      <Input
        variant="sm"
        value={quantity}
        inputMode="decimal"
        aria-label={`Qty on ${label}`}
        aria-invalid={invalid || undefined}
        title={invalid ? 'Must be a number' : undefined}
        className="w-full px-1.5 text-end tabular-nums"
        onChange={(event) =>
          grid.update(row.key, { quantity: event.target.value })
        }
      />
      {chip}
    </div>
  );
}

/** The unit, in a column of its own beside Qty (owner hand test, point 5). */
function UomCell({ row: tableRow }: CellContext<GridRow, unknown>) {
  const grid = useGrid();
  const row = tableRow.original;
  const { staged } = row;
  const uom = staged.draft.uom ?? '';

  if (!grid.editing || staged.removed) {
    return (
      <span className="block truncate text-sm" title={uom || undefined}>
        {uom || '-'}
      </span>
    );
  }

  return (
    <CellSelect
      label={`UOM on ${grid.labelOf(row)}`}
      clearable
      value={uom}
      onChange={(next) => grid.update(row.key, { uom: next })}
      options={grid.uomOptions}
      loadError={grid.uomError}
      onRetry={grid.onUomRetry}
      selectedOption={grid.uomOptions.find((option) => option.value === uom)}
      placeholder="UOM"
      triggerClassName="px-1.5"
      emptyMessage="No matches"
    />
  );
}

function RateCell({ row: tableRow }: CellContext<GridRow, unknown>) {
  const grid = useGrid();
  const row = tableRow.original;
  const { staged } = row;
  const price = staged.draft.unit_price ?? '';
  // The draft's list price first: a product picked a moment ago shows its own at once.
  const listPrice = staged.draft.list_price || staged.line?.list_price;
  const list = listPrice ? (
    <span className="mt-0.5 block text-end text-xs text-muted-foreground">
      {`List ${formatMyr(listPrice)}`}
    </span>
  ) : null;

  if (!grid.editing || staged.removed) {
    return (
      <div className="text-end">
        <span className="block text-sm tabular-nums">
          {price ? formatMyr(price) : '-'}
        </span>
        {list}
      </div>
    );
  }

  const invalid = price.trim() !== '' && !isDecimalString(price);
  return (
    <div className="min-w-0">
      <Input
        variant="sm"
        value={price}
        inputMode="decimal"
        placeholder="0.00"
        aria-label={`Unit price on ${grid.labelOf(row)}`}
        aria-invalid={invalid || undefined}
        title={invalid ? 'Must be a number' : undefined}
        className="w-full text-end tabular-nums"
        onChange={(event) =>
          grid.update(row.key, { unit_price: event.target.value })
        }
      />
      {list}
    </div>
  );
}

function TotalCell({ row: tableRow }: CellContext<GridRow, unknown>) {
  const grid = useGrid();
  const row = tableRow.original;
  const { staged } = row;
  const rateOnly = isInlineChecked(staged.draft.is_rate_only);

  return (
    <div className="flex flex-col items-end gap-1">
      {/* Off the draft, so it moves while a quantity is typed. A rate-only line prints the
          words: blank reads as "we forgot" and RM 0.00 as "free". */}
      {rateOnly ? (
        <Badge variant="secondary" appearance="light">
          rate only
        </Badge>
      ) : (
        <span className="text-xs font-medium tabular-nums">
          {formatMyrExact(
            multiplyMoney(staged.draft.quantity, staged.draft.unit_price) ??
              '0',
          )}
        </span>
      )}
      {grid.editing && !staged.removed && (
        <label className="flex items-center gap-1 text-2xs whitespace-nowrap text-muted-foreground">
          <Checkbox
            size="sm"
            aria-label={`Rate only on ${grid.labelOf(row)}`}
            checked={rateOnly}
            onCheckedChange={(next) =>
              grid.update(row.key, {
                is_rate_only: next === true ? INLINE_CHECKED : '',
              })
            }
          />
          Rate only
        </label>
      )}
    </div>
  );
}

/** The scope total under the money column, off every live draft except removed lines. */
function TotalFooter() {
  const grid = useGrid();
  return (
    // 12px so a seven-figure scope total fits the column without clipping.
    <span className="block text-end text-xs tabular-nums">
      {grid.total === null ? '-' : formatMyrExact(grid.total)}
    </span>
  );
}

function ActionsCell({ row: tableRow }: CellContext<GridRow, unknown>) {
  const grid = useGrid();
  const row = tableRow.original;
  const { staged } = row;
  const label = grid.labelOf(row);
  const [open, setOpen] = React.useState(false);
  /** Set by an action that puts the caret somewhere itself, so the menu does not take it back. */
  const keepFocus = React.useRef(false);

  if (staged.removed) {
    return (
      <Button
        type="button"
        mode="icon"
        variant="ghost"
        size="sm"
        aria-label={`Restore ${label}`}
        onClick={() => grid.toggleRemoved(row.key)}
      >
        <Undo2 className="size-3.5 text-primary" />
      </Button>
    );
  }

  const run = (action: MenuAction) => {
    keepFocus.current = action === 'section' || action === 'below';
    grid.act(row.key, action);
  };

  return (
    <div className="flex flex-col items-center gap-0.5">
      <DropdownMenu open={open} onOpenChange={setOpen} modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            mode="icon"
            variant="ghost"
            size="sm"
            aria-label={`Line actions on ${label}`}
            title="Line actions"
            // Opens on click (and from the keyboard, which Radix handles): a pointerdown open
            // followed by the click's own toggle would close it again.
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => setOpen(true)}
          >
            <MoreHorizontal className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          onCloseAutoFocus={(event) => {
            if (keepFocus.current) event.preventDefault();
            keepFocus.current = false;
          }}
        >
          <DropdownMenuItem onSelect={() => run('below')}>
            Insert a line below
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => run('section')}>
            Insert a section above
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => run('remove')}
          >
            Remove line
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Button
        type="button"
        mode="icon"
        variant="ghost"
        size="sm"
        aria-label={`Remove ${label}`}
        title="Remove"
        onClick={() => grid.toggleRemoved(row.key)}
      >
        <Trash2 className="size-3.5 text-destructive" />
      </Button>
    </div>
  );
}

/** Line details, opened from the row menu: the fields that need no column (AC-QLG18). */
function LineDetails({ row }: { row: GridRow }) {
  const grid = useGrid();
  const { staged } = row;
  const label = grid.labelOf(row);
  const unitType = staged.draft.unit_type ?? '';
  return (
    <div className="grid gap-3 px-4 py-3 sm:grid-cols-[minmax(0,14rem)_minmax(0,16rem)_minmax(0,1fr)]">
      <div className="min-w-0 space-y-1">
        <p className="text-xs font-medium">Complete set</p>
        <Input
          variant="sm"
          value={staged.draft.complete_set ?? ''}
          maxLength={100}
          placeholder="c/w seat cover, flush valve"
          aria-label={`Complete set on ${label}`}
          onChange={(event) =>
            grid.update(row.key, { complete_set: event.target.value })
          }
        />
      </div>
      <div className="min-w-0 space-y-1">
        <p className="text-xs font-medium">Priced per</p>
        <CellSelect
          label={`Priced per on ${label}`}
          clearable
          value={unitType}
          onChange={(next) => grid.update(row.key, { unit_type: next })}
          options={PRICED_PER_OPTIONS}
          selectedOption={PRICED_PER_OPTIONS.find(
            (option) => option.value === unitType,
          )}
          placeholder="Not set"
          emptyMessage="No matches"
        />
      </div>
      <div className="min-w-0 space-y-1">
        <p className="text-xs font-medium">Notes</p>
        <Textarea
          variant="sm"
          rows={2}
          value={staged.draft.notes ?? ''}
          placeholder="Why this price, what was agreed, what it replaces"
          aria-label={`Notes on ${label}`}
          className="field-sizing-content min-h-14 resize-none"
          onChange={(event) =>
            grid.update(row.key, { notes: event.target.value })
          }
        />
      </div>
    </div>
  );
}

/** A section heading, drawn as a full-width row above the line that carries it (AC-QLG03). */
function SectionBar({ row }: { row: GridRow }) {
  const grid = useGrid();
  const { staged } = row;
  const value = staged.draft.band_label ?? '';
  if (!grid.editing || staged.removed) {
    return (
      <span
        className="block truncate text-sm font-semibold text-foreground uppercase"
        title={value}
      >
        {value}
      </span>
    );
  }
  return (
    // The whole bar answers a right-click (Rename, Remove section), not only its input.
    <div data-section-key={row.key} className="w-full">
      <Input
        variant="sm"
        value={value}
        data-band-key={row.key}
        aria-label={`Section heading on ${grid.labelOf(row)}`}
        title={value}
        placeholder="e.g. BILL NO 3 PAGE 15/4, OPTIONAL ITEMS"
        maxLength={150}
        className="max-w-md font-semibold"
        onChange={(event) =>
          grid.update(row.key, { band_label: event.target.value })
        }
        onBlur={() => grid.closeEmptyBand(row.key)}
      />
    </div>
  );
}

function ReorderHeader() {
  return <span className="sr-only">Reorder</span>;
}

function HandleCell({ row }: CellContext<GridRow, unknown>) {
  return <DataGridTableDndRowHandle rowId={row.original.key} />;
}

/** No header text: the column holds each row's own named menu and Remove buttons. */
function ActionsHeader() {
  return null;
}

const NARROW = 'px-1.5';
const TIGHT = 'px-1';
const FLUSH = 'px-0';

/**
 * The columns, per the approved mock (section 1) and the owner's hand test (UOM beside Qty):
 * the defaults sum to 876px, so the grid fits the 878px scroller at 1280 with the sidebar open. The handle and the row menu only exist while editing (column visibility), so the
 * column order a user saves holds in both views.
 */
const COLUMNS: ColumnDef<GridRow>[] = [
  {
    id: 'drag_handle',
    header: ReorderHeader,
    cell: HandleCell,
    size: 28,
    minSize: 28,
    enableResizing: false,
    enableHiding: false,
    meta: {
      headerTitle: 'Reorder',
      draggable: false,
      headerClassName: FLUSH,
      cellClassName: FLUSH,
    },
  },
  {
    id: 'item_no',
    header: '#',
    cell: ItemCell,
    size: 24,
    minSize: 24,
    enableHiding: false,
    meta: {
      headerTitle: '#',
      headerClassName: TIGHT,
      cellClassName: `${TIGHT} align-top`,
    },
  },
  // The reader's columns carry an accessor so the Columns menu lists them (it offers only
  // columns with one); the cells read the row themselves.
  {
    id: 'photo',
    accessorFn: (row) => row.staged.draft.image_attachment_id,
    header: 'Photo',
    cell: PhotoCell,
    size: 44,
    minSize: 40,
    meta: {
      headerTitle: 'Photo',
      headerClassName: TIGHT,
      cellClassName: `${TIGHT} align-top`,
    },
  },
  {
    id: 'drawing',
    accessorFn: (row) => row.staged.draft.drawing_attachment_id,
    header: 'Drawing',
    cell: DrawingCell,
    size: 56,
    minSize: 56,
    meta: {
      headerTitle: 'Drawing',
      headerClassName: TIGHT,
      cellClassName: `${TIGHT} align-top`,
    },
  },
  {
    id: 'product',
    accessorFn: (row) => row.staged.draft.product_id,
    header: 'Product',
    cell: ProductCell,
    size: 104,
    minSize: 88,
    meta: { headerTitle: 'Product', cellClassName: 'align-top' },
  },
  {
    id: 'description',
    accessorFn: (row) => row.staged.draft.description,
    header: 'Description',
    cell: DescriptionCell,
    size: 184,
    minSize: 140,
    meta: {
      headerTitle: 'Description',
      cellClassName: 'align-top whitespace-normal',
    },
  },
  {
    id: 'brand',
    accessorFn: (row) => row.staged.draft.brand_snapshot,
    header: 'Brand',
    cell: BrandCell,
    size: 84,
    minSize: 64,
    meta: { headerTitle: 'Brand', cellClassName: 'align-top' },
  },
  {
    id: 'qty',
    accessorFn: (row) => row.staged.draft.quantity,
    header: 'Qty',
    cell: QtyCell,
    size: 60,
    minSize: 52,
    meta: {
      headerTitle: 'Qty',
      headerClassName: `${NARROW} text-end`,
      cellClassName: `${NARROW} align-top`,
    },
  },
  {
    id: 'uom',
    accessorFn: (row) => row.staged.draft.uom,
    header: 'UOM',
    cell: UomCell,
    size: 72,
    minSize: 60,
    meta: {
      headerTitle: 'UOM',
      headerClassName: NARROW,
      cellClassName: `${NARROW} align-top`,
    },
  },
  {
    id: 'rate',
    accessorFn: (row) => row.staged.draft.unit_price,
    header: 'Rate (RM)',
    cell: RateCell,
    size: 80,
    minSize: 76,
    meta: {
      headerTitle: 'Rate (RM)',
      headerClassName: 'text-end',
      cellClassName: `${NARROW} align-top`,
    },
  },
  {
    id: 'total',
    accessorFn: (row) => row.staged.draft.is_rate_only,
    header: 'Total (RM)',
    cell: TotalCell,
    footer: TotalFooter,
    size: 112,
    minSize: 96,
    meta: {
      headerTitle: 'Total (RM)',
      headerClassName: 'text-end',
      cellClassName: `${NARROW} align-top text-end`,
    },
  },
  {
    id: 'row_actions',
    header: ActionsHeader,
    cell: ActionsCell,
    size: 28,
    minSize: 28,
    enableResizing: false,
    enableHiding: false,
    meta: {
      headerTitle: 'Row actions',
      draggable: false,
      headerClassName: FLUSH,
      cellClassName: `${FLUSH} align-top`,
      expandedContent: (row: GridRow) => <LineDetails row={row} />,
    },
  },
];

// ------------------------------------------------------------------------------- grid

export interface QuotationLinesGridProps {
  /** The lines in display order: the staged set while editing, the server's rows otherwise. */
  lines: StagedQuotationLine[];
  editing: boolean;
  quotationId: string | null;
  /** Live search: rows that fail it are hidden, never renumbered or left out of the total. */
  rowFilter?: (line: StagedQuotationLine) => boolean;
  emptyHint: string;
  filterEmptyHint: string;
  fetchProducts: GridContextValue['fetchProducts'];
  resolveProduct: GridContextValue['resolveProduct'];
  /** The code of a product picked in this session, or null when it is not known here. */
  productCodeOf: (productId: string) => string | null;
  /** What picking a product decides for the rest of its line (null for a cleared pick). */
  fillFromProduct: (option: SearchableSelectOption) => LineDraft;
  uomOptions: SearchableSelectOption[];
  uomError: unknown;
  onUomRetry: () => void;
  brandOptions: SearchableSelectOption[];
  stockGroup: string | null;
  /** The search and "Stock at" controls, drawn in the grid's own toolbar beside Columns. */
  toolbar?: React.ReactNode;
  /** Every edit, addition and reorder, as the whole staged set. Edit mode only. */
  onStage?: (lines: StagedQuotationLine[]) => void;
  onToggleRemoved?: (key: string) => void;
}

/**
 * The quotation's lines as the app's DataGrid (AC-QLG01 to AC-QLG04): rows drag by a handle,
 * columns resize and reorder and are remembered per user, a section heading is a full-width row
 * that moves with its line, and Line details open as an expanded row.
 *
 * It writes nothing: in an edit session every change goes up as the whole staged set, and the
 * document's one Save is what reaches the server.
 */
export function QuotationLinesGrid({
  lines,
  editing,
  quotationId,
  rowFilter,
  emptyHint,
  filterEmptyHint,
  fetchProducts,
  resolveProduct,
  productCodeOf,
  fillFromProduct,
  uomOptions,
  uomError,
  onUomRetry,
  brandOptions,
  stockGroup,
  onStage,
  onToggleRemoved,
  toolbar,
}: QuotationLinesGridProps) {
  const [expanded, setExpanded] = React.useState<ExpandedState>({});
  const [bandOpen, setBandOpen] = React.useState<Set<string>>(() => new Set());
  const [preview, setPreview] = React.useState<{
    items: AttachmentPreviewItem[];
    index: number;
  } | null>(null);
  const [visited, setVisited] = React.useState<Set<string>>(() => new Set());
  const markVisited = React.useCallback(
    (key: string) =>
      setVisited((previous) =>
        previous.has(key) ? previous : new Set(previous).add(key),
      ),
    [],
  );
  const instanceKey = React.useId();
  const nextNewId = React.useRef(0);

  /**
   * The newest set, ahead of the next render: a product pick is two writes in one event (the
   * pick and its fill), and each has to build on the other rather than on the props both saw.
   */
  const linesRef = React.useRef(lines);
  linesRef.current = lines;

  const commit = React.useCallback(
    (next: StagedQuotationLine[]) => {
      linesRef.current = next;
      onStage?.(next);
    },
    [onStage],
  );

  const update = React.useCallback(
    (key: string, patch: LineDraft) =>
      commit(
        linesRef.current.map((line) =>
          line.key === key
            ? { ...line, draft: { ...line.draft, ...patch } }
            : line,
        ),
      ),
    [commit],
  );

  const selectProduct = React.useCallback(
    (key: string, option: SearchableSelectOption | null) => {
      const held = linesRef.current.find((line) => line.key === key);
      if (!held) return;
      // A different product's photo or drawing cannot be this one's, so a change clears both.
      const clearPicks: LineDraft =
        (option?.value ?? '') !== (held.draft.product_id ?? '')
          ? { image_attachment_id: '', drawing_attachment_id: '' }
          : {};
      if (!option) {
        update(key, { product_id: '', ...clearPicks });
        return;
      }
      update(key, {
        product_id: option.value,
        ...fillFromProduct(option),
        ...clearPicks,
      });
    },
    [fillFromProduct, update],
  );

  const focusSoon = React.useCallback((selector: string) => {
    window.setTimeout(() => {
      document.querySelector<HTMLElement>(selector)?.focus();
    }, 0);
  }, []);

  const newLine = React.useCallback((): StagedQuotationLine => {
    nextNewId.current += 1;
    return {
      id: null,
      key: `new:${instanceKey}:${nextNewId.current}`,
      line: null,
      draft: emptyDraft(),
      removed: false,
    };
  }, [instanceKey]);

  const openBand = React.useCallback(
    (key: string) => {
      setBandOpen((previous) => new Set(previous).add(key));
      // Keys are line ids or `new:<id>:<n>`, so a quoted attribute value needs no escaping.
      focusSoon(`[data-band-key="${key}"]`);
    },
    [focusSoon],
  );

  const closeEmptyBand = React.useCallback((key: string) => {
    const held = linesRef.current.find((line) => line.key === key);
    if (held && (held.draft.band_label ?? '').trim() !== '') return;
    setBandOpen((previous) => {
      if (!previous.has(key)) return previous;
      const next = new Set(previous);
      next.delete(key);
      return next;
    });
  }, []);

  const addLine = React.useCallback(
    (withSection: boolean) => {
      const added = newLine();
      commit([...linesRef.current, added]);
      if (withSection) openBand(added.key);
    },
    [commit, newLine, openBand],
  );

  const act = React.useCallback(
    (key: string, action: MenuAction) => {
      const current = linesRef.current;
      const index = current.findIndex((line) => line.key === key);
      if (index < 0) return;
      switch (action) {
        case 'details':
          setExpanded((previous) => {
            const map = previous === true ? {} : previous;
            return { ...map, [key]: !map[key] };
          });
          return;
        case 'below': {
          const added = newLine();
          commit([
            ...current.slice(0, index + 1),
            added,
            ...current.slice(index + 1),
          ]);
          return;
        }
        case 'section':
          openBand(key);
          return;
        case 'remove':
          onToggleRemoved?.(key);
          return;
      }
    },
    [commit, newLine, onToggleRemoved, openBand],
  );

  const handleRowDragEnd = React.useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const current = linesRef.current;
      const from = current.findIndex((line) => line.key === String(active.id));
      const to = current.findIndex((line) => line.key === String(over.id));
      if (from < 0 || to < 0) return;
      commit(arrayMove(current, from, to));
    },
    [commit],
  );

  const allRows = React.useMemo<GridRow[]>(
    () => lines.map((staged, index) => ({ key: staged.key, index, staged })),
    [lines],
  );
  const visibleRows = React.useMemo(
    () =>
      rowFilter ? allRows.filter((row) => rowFilter(row.staged)) : allRows,
    [allRows, rowFilter],
  );
  // Out of the money the moment a line is staged for removal; never out of it for a search.
  const total = React.useMemo(
    () =>
      totalFromDrafts(
        lines.filter((line) => !line.removed).map((line) => line.draft),
      ),
    [lines],
  );

  /** Each viewer's gallery: every line that HAS that picture, in table order. */
  const galleries = React.useMemo(() => {
    const build = (
      kind: LineFileKind,
    ): (AttachmentPreviewItem & { attachmentId: string })[] =>
      allRows.flatMap((row) => {
        const line = fileLine(row, kind);
        const field = PICK_FIELD[kind];
        const shown = resolveLineFile(
          kind,
          line,
          row.staged.draft[field] ?? '',
        );
        if (!shown) return [];
        const code = line?.product_code ?? 'Product';
        return [
          {
            id: `${row.key}:${shown.attachmentId}`,
            attachmentId: shown.attachmentId,
            name: shown.filename ?? `${code} ${kind}`,
            url: shown.previewUrl,
            downloadUrl: `/api/v1/resource-management/attachments/${shown.attachmentId}/download`,
          },
        ];
      });
    return { photo: build('photo'), drawing: build('drawing') };
  }, [allRows]);

  const openPreview = React.useCallback(
    (kind: LineFileKind, shown: LineFileShown) => {
      const gallery = galleries[kind];
      const index = gallery.findIndex(
        (item) => item.attachmentId === shown.attachmentId,
      );
      if (index >= 0) {
        setPreview({ items: gallery, index });
        return;
      }
      // A line not saved yet is not in the gallery (its picture is not stored anywhere): its
      // own picture opens on its own.
      setPreview({
        items: [
          {
            id: shown.attachmentId,
            name: shown.filename ?? kind,
            url: shown.previewUrl,
            downloadUrl: `/api/v1/resource-management/attachments/${shown.attachmentId}/download`,
          },
        ],
        index: 0,
      });
    },
    [galleries],
  );

  const labelOf = React.useCallback(
    (row: GridRow) => {
      const productId = row.staged.draft.product_id ?? '';
      return describeLine(
        row.staged.line,
        row.index,
        !row.staged.line && productId ? productCodeOf(productId) : null,
      );
    },
    [productCodeOf],
  );

  const context = React.useMemo<GridContextValue>(
    () => ({
      editing,
      quotationId,
      fetchProducts,
      resolveProduct,
      uomOptions,
      uomError,
      onUomRetry,
      brandOptions,
      stockGroup,
      total,
      update,
      selectProduct,
      toggleRemoved: (key) => onToggleRemoved?.(key),
      act,
      bandOpen,
      closeEmptyBand,
      openPreview,
      visited,
      markVisited,
      labelOf,
    }),
    [
      act,
      bandOpen,
      brandOptions,
      closeEmptyBand,
      editing,
      fetchProducts,
      labelOf,
      markVisited,
      onToggleRemoved,
      onUomRetry,
      openPreview,
      quotationId,
      resolveProduct,
      selectProduct,
      stockGroup,
      total,
      uomError,
      uomOptions,
      update,
      visited,
    ],
  );

  /** The reader's own column choices (Columns menu, saved per user by the grid). */
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});

  const table = useReactTable({
    columns: COLUMNS,
    data: visibleRows,
    getRowId: (row) => row.key,
    getCoreRowModel: getCoreRowModel(),
    getRowCanExpand: () => true,
    state: {
      // The handle and the menu are edit-only; hidden rather than removed, so a saved column
      // order holds in both views. They cannot be hidden by the reader either.
      columnVisibility: {
        ...columnVisibility,
        drag_handle: editing,
        row_actions: editing,
      },
      expanded: editing ? expanded : {},
    },
    onColumnVisibilityChange: setColumnVisibility,
    onExpandedChange: setExpanded,
    enableSorting: false,
    columnResizeMode: 'onChange',
  });

  /**
   * What a right-click is on: a section bar, or a line row. Read off the DOM the event came from,
   * so one menu serves every row (owner hand test, points 2 and 3).
   */
  const [menuTarget, setMenuTarget] = React.useState<{
    kind: 'line' | 'section';
    key: string;
  } | null>(null);
  /** Off for a right-click outside the rows, so the browser's own menu shows there. */
  const [menuOff, setMenuOff] = React.useState(false);
  const targetOf = (element: Element | null) => {
    const bar = element?.closest<HTMLElement>('[data-section-key]');
    if (bar?.dataset.sectionKey)
      return { kind: 'section' as const, key: bar.dataset.sectionKey };
    const tr = element?.closest<HTMLElement>('tr[data-line-key]');
    if (tr?.dataset.lineKey)
      return { kind: 'line' as const, key: tr.dataset.lineKey };
    return null;
  };
  /** Set by an action that puts the caret somewhere itself, so the menu does not take it back. */
  const keepFocusAfterMenu = React.useRef(false);
  const menuLine = menuTarget
    ? (lines.find((line) => line.key === menuTarget.key) ?? null)
    : null;

  const renderSection = React.useCallback(
    (row: GridRow) => {
      const label = (row.staged.draft.band_label ?? '').trim();
      return label || (editing && bandOpen.has(row.key)) ? (
        <SectionBar row={row} />
      ) : null;
    },
    [bandOpen, editing],
  );

  const galleryItems = preview?.items ?? [];

  const renameSection = (key: string) => {
    keepFocusAfterMenu.current = true;
    window.setTimeout(() => {
      document.querySelector<HTMLElement>(`[data-band-key="${key}"]`)?.focus();
    }, 0);
  };
  const removeSection = (key: string) => {
    update(key, { band_label: '' });
    setBandOpen((previous) => {
      if (!previous.has(key)) return previous;
      const next = new Set(previous);
      next.delete(key);
      return next;
    });
  };

  return (
    <GridContext.Provider value={context}>
      <div className="min-w-0 space-y-2">
        <DataGrid
          table={table}
          recordCount={visibleRows.length}
          isLoading={false}
          // Rows render under the default layout while the saved one loads, rather than as
          // skeletons: an editor must never hide the lines somebody is about to type into.
          loadingMode="spinner"
          listingKey="projects.projects.view::quotation-lines"
          tableLayout={{ width: 'fixed', columnsResizable: true, dense: true }}
          tableClassNames={{ headerRow: 'text-xs' }}
          emptyMessage={lines.length === 0 ? emptyHint : filterEmptyHint}
          renderGroupHeader={renderSection}
          rowClassName={(row: GridRow) =>
            row.staged.removed
              ? 'text-muted-foreground line-through opacity-70'
              : undefined
          }
          rowAttributes={(row: GridRow) => ({ 'data-line-key': row.key })}
          // A click on the row, away from its inputs and buttons, opens or closes Line details
          // (the grid leaves every control's own click alone). Reorder is the handle only.
          onRowClick={
            editing ? (row: GridRow) => act(row.key, 'details') : undefined
          }
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            {toolbar}
            <div className="sm:ms-auto">
              <DataGridColumnVisibility
                table={table}
                trigger={
                  <Button variant="outline" size="sm" className="gap-1.5">
                    <Columns3 className="size-4" />
                    Columns
                  </Button>
                }
              />
            </div>
          </div>
          <ContextMenu modal={false}>
            <ContextMenuTrigger asChild disabled={!editing || menuOff}>
              <div
                className="min-w-0 overflow-hidden rounded-lg border border-border"
                onPointerDownCapture={(event) => {
                  if (event.button === 2)
                    setMenuOff(!targetOf(event.target as Element));
                }}
                onContextMenu={(event) => {
                  const target = targetOf(event.target as Element);
                  if (!target) {
                    event.preventDefault();
                    return;
                  }
                  setMenuTarget(target);
                }}
              >
                <DataGridScroller>
                  <DataGridTableDndRows
                    handleDragEnd={handleRowDragEnd}
                    dataIds={visibleRows.map((row) => row.key)}
                    columnsDraggable
                  />
                </DataGridScroller>
                {editing && (
                  <div className="flex flex-wrap items-center gap-1 border-t border-border px-2 py-1.5">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="text-primary hover:text-primary"
                      onClick={() => addLine(false)}
                    >
                      <Plus className="size-4" aria-hidden />
                      Add a line
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="text-primary hover:text-primary"
                      onClick={() => addLine(true)}
                    >
                      <Plus className="size-4" aria-hidden />
                      Add a section
                    </Button>
                  </div>
                )}
              </div>
            </ContextMenuTrigger>
            <ContextMenuContent
              className="w-52"
              onCloseAutoFocus={(event) => {
                if (keepFocusAfterMenu.current) event.preventDefault();
                keepFocusAfterMenu.current = false;
              }}
            >
              {menuTarget?.kind === 'section' ? (
                <>
                  <ContextMenuItem
                    onSelect={() => renameSection(menuTarget.key)}
                  >
                    Rename
                  </ContextMenuItem>
                  <ContextMenuItem
                    variant="destructive"
                    onSelect={() => removeSection(menuTarget.key)}
                  >
                    Remove section
                  </ContextMenuItem>
                </>
              ) : menuTarget ? (
                <>
                  <ContextMenuItem
                    disabled={Boolean(menuLine?.removed)}
                    onSelect={() => {
                      keepFocusAfterMenu.current = true;
                      act(menuTarget.key, 'below');
                    }}
                  >
                    Insert a line below
                  </ContextMenuItem>
                  <ContextMenuItem
                    disabled={Boolean(menuLine?.removed)}
                    onSelect={() => {
                      keepFocusAfterMenu.current = true;
                      act(menuTarget.key, 'section');
                    }}
                  >
                    Insert a section above
                  </ContextMenuItem>
                  <ContextMenuItem
                    variant={menuLine?.removed ? 'default' : 'destructive'}
                    onSelect={() => act(menuTarget.key, 'remove')}
                  >
                    {menuLine?.removed ? 'Restore line' : 'Remove line'}
                  </ContextMenuItem>
                </>
              ) : null}
            </ContextMenuContent>
          </ContextMenu>
        </DataGrid>

        {/* The same viewer Resource Management uses. Mounted only while open. */}
        {preview && galleryItems.length > 0 && (
          <AttachmentPreviewModal
            open
            onOpenChange={(next) => {
              if (!next) setPreview(null);
            }}
            items={galleryItems}
            startIndex={preview.index}
          />
        )}
      </div>
    </GridContext.Provider>
  );
}
