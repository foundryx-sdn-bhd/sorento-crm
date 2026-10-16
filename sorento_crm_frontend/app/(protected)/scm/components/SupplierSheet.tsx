'use client';

/**
 * The supplier document, ONE renderer (R9/R12). Extracted off the public page's own table
 * (`app/(public)/c/[company]/supplier-request/[token]/page.tsx`), which now imports this too,
 * so the read-only public page, the PDF-shaped preview inside the loading plan and any future
 * caller draw the same `SheetModel` the same way rather than three copies of one table.
 *
 * `editable` (R11) turns the qty-to-load and remark cells into inputs, writing back through
 * `onQtyChange` / `onRemarkChange`. Which columns those are is read off `column.field`
 * (`'qty_to_load'` / `'line_remark'`, review round 1) rather than assumed positionally - a
 * document frozen before `field` existed on the wire falls back to the last two columns, the
 * position they have always been in. A row with no `row_key` (an unmatched line on the
 * supplier's own sheet, or a document frozen before remarks existed) has nothing to write an
 * edit against, so it renders those two cells as plain text even in edit mode.
 *
 * `qtyFor` / `remarkFor` (review round 1, S2) make the two editable cells CONTROLLED: their
 * value comes from the SAME map the plan table's own Remarks/Qty cells read
 * (`LoadingPlanView`'s `edits`), keyed by `row.row_key`, rather than from this render's own
 * `sheet` prop. Without them the cell was `defaultValue` off whatever the last debounced
 * preview fetch echoed back - so a remark typed in the plan table never appeared here until
 * the next refetch landed, and a row a caller passes no accessor for (read-only viewers,
 * where `editable` is never true anyway) simply falls back to the sheet's own cell value.
 *
 * R10: a cell's `fill` is either `'highlight'` (our own mark, painted on rows whose qty to
 * load is > 0 - AC-E3) or the legacy `'yellow'` a notice sent before this batch still carries
 * in its frozen `sheet_json`. Both are supported so an old sent document does not lose the
 * marks it went out with; every NEW document the backend builds only ever emits `'highlight'`.
 *
 * R10 feedback (captain, 7 Sep): a `rowSpan` cell never draws the highlight class even when
 * the backend sent one on it - the backend model already withholds `fill` from a family's
 * shared cells (`supplier_document_model._apply_highlight`), but this stays a second guard
 * against a `sheet_json` frozen before that fix, same reason `container_request_xlsx` checks
 * the cell's own span rather than trusting the model to have done it upstream. A `covered`
 * cell needs no guard of its own - `cell()` below returns before `fillClass` ever runs on one.
 */

import { cn } from '@/lib/utils';
import { fmtInt } from '../lib/format';
import { LiveNumberInput } from '@/components/ui/number-input';

export interface SupplierSheetColumn {
  /** Their heading, in their own words (or ours, on a no-file document). */
  label: string;
  /** Ours, as a second line under it. Null for a column we cannot name. */
  label_en: string | null;
  /** The canonical field this column resolves to (`'qty_to_load'`, `'line_remark'`, ...),
   *  null for a column of theirs we cannot name. Absent on a sheet frozen before this field
   *  existed on the wire (review round 1) - `qtyAt`/`remarkAt` below fall back to position
   *  for exactly that case. */
  field?: string | null;
}

export interface SupplierSheetCell {
  value: string | number | null;
  rowspan: number;
  colspan: number;
  /** True when a merge starting above or to the left covers this position: draw nothing. */
  covered: boolean;
  fill: 'yellow' | 'highlight' | null;
  red: boolean;
}

export interface SupplierSheetRow {
  cells: SupplierSheetCell[];
  /** How many of the sheet's rows this product family covers; 0 on a row that continues one. */
  family_span: number;
  /** True for a line we added because the supplier's own list never named the product. */
  appended: boolean;
  /** The plan row this line came from (R11), so an edit here can write back to
   *  `line_edits[row_key]`. Null on a row this sheet cannot attribute an edit to. */
  row_key?: string | null;
}

export interface SupplierSheetModel {
  title: string | null;
  columns: SupplierSheetColumn[];
  rows: SupplierSheetRow[];
  totals: SupplierSheetRow | null;
}

/** Empty stays empty: a dash would read as a value the supplier wrote (the reason `fmtInt`'s
 *  own em-dash-for-null is wrong here, so this is not a bare `fmtInt` call). */
function cellText(value: string | number | null): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'number' ? fmtInt(value) : value;
}

/** `spansRows` is `rowCell.rowspan > 1`: this row's own highlight never paints a family's
 *  shared cell (R10 feedback, 7 Sep) - a legacy `'yellow'` mark is theirs, not ours, and is
 *  drawn regardless, same as it always was. */
function fillClass(fill: SupplierSheetCell['fill'], spansRows: boolean): string | undefined {
  if (fill === 'highlight') return spansRows ? undefined : 'bg-[#fff2cc]';
  if (fill === 'yellow') return 'bg-[#ffff00] text-black';
  return undefined;
}

/** `column.field` when the sheet carries one, else the position this column has always sat
 *  at - so a sheet frozen before `field` existed on the wire still finds its qty/remark
 *  columns. */
function columnAt(columns: SupplierSheetColumn[], field: string, fallback: number): number {
  const byField = columns.findIndex((c) => c.field === field);
  return byField >= 0 ? byField : fallback;
}

export function SupplierSheet({
  sheet,
  editable = false,
  qtyFor,
  onQtyChange,
  remarkFor,
  onRemarkChange,
}: {
  sheet: SupplierSheetModel;
  /** R11: the qty-to-load and remark columns become inputs. */
  editable?: boolean;
  /** Controlled value for the qty input, keyed by `row_key` (review round 1, S2). Falls back
   *  to the sheet's own cell value when not supplied - the public page's read-only render,
   *  where `editable` is never true, never passes it. */
  qtyFor?: (rowKey: string) => number;
  onQtyChange?: (rowKey: string, qty: number) => void;
  /** Controlled value for the remark input, keyed by `row_key`. Same fallback as `qtyFor`. */
  remarkFor?: (rowKey: string) => string;
  onRemarkChange?: (rowKey: string, remark: string) => void;
}) {
  const qtyAt = columnAt(sheet.columns, 'qty_to_load', sheet.columns.length - 2);
  const remarkAt = columnAt(sheet.columns, 'line_remark', sheet.columns.length - 1);

  function cell(rowCell: SupplierSheetCell, colIndex: number, row: SupplierSheetRow) {
    if (rowCell.covered) return null;
    const isEditableCell = editable && !!row.row_key && (colIndex === qtyAt || colIndex === remarkAt);
    return (
      <td
        key={colIndex}
        rowSpan={rowCell.rowspan > 1 ? rowCell.rowspan : undefined}
        colSpan={rowCell.colspan > 1 ? rowCell.colspan : undefined}
        className={cn(
          'border border-border px-2 py-1.5 align-middle',
          fillClass(rowCell.fill, rowCell.rowspan > 1),
          rowCell.red && 'text-red-600',
        )}
      >
        {isEditableCell && colIndex === qtyAt ? (
          <LiveNumberInput
            min={0}
            rules={{ min: 0 }}
            aria-label="Qty to load"
            className="h-8 w-20 rounded-sm border border-input bg-background px-1.5 text-center tabular-nums"
            errorClassName="text-center"
            value={
              qtyFor
                ? qtyFor(row.row_key as string)
                : (typeof rowCell.value === 'number' ? rowCell.value : 0)
            }
            onCommit={(next) => onQtyChange?.(row.row_key as string, next)}
          />
        ) : isEditableCell && colIndex === remarkAt ? (
          <input
            type="text"
            aria-label="Remarks"
            className="h-8 w-full rounded-sm border border-input bg-background px-1.5"
            value={
              remarkFor
                ? remarkFor(row.row_key as string)
                : (typeof rowCell.value === 'string' ? rowCell.value : '')
            }
            onChange={(e) => onRemarkChange?.(row.row_key as string, e.target.value)}
          />
        ) : (
          cellText(rowCell.value)
        )}
      </td>
    );
  }

  return (
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <table className="w-full min-w-[820px] border-collapse text-center text-xs">
        <thead>
          {sheet.title ? (
            <tr>
              <th
                colSpan={sheet.columns.length}
                className="border border-border px-2 py-2 text-center text-base font-semibold"
              >
                {sheet.title}
              </th>
            </tr>
          ) : null}
          <tr>
            {sheet.columns.map((column, index) => (
              <th
                key={`${column.label}-${index}`}
                className="border border-border px-2 py-1.5 text-center font-semibold"
              >
                {column.label}
                {column.label_en ? (
                  <span className="block text-2xs font-normal text-muted-foreground">
                    {column.label_en}
                  </span>
                ) : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sheet.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.cells.map((rowCell, colIndex) => cell(rowCell, colIndex, row))}
            </tr>
          ))}
        </tbody>
        {sheet.totals ? (
          <tfoot>
            <tr className="font-semibold text-red-600">
              {sheet.totals.cells.map((rowCell, colIndex) => cell(rowCell, colIndex, sheet.totals as SupplierSheetRow))}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}

export default SupplierSheet;
