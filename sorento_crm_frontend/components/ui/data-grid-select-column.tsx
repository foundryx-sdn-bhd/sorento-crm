'use client';

import type { ColumnDef, Row, Table } from '@tanstack/react-table';
import { Checkbox } from '@/components/ui/checkbox';

/**
 * Standard row-selection column for DataGrid lists.
 *
 * Every list that supports bulk actions / export-of-selection MUST use this
 * instead of a hand-rolled `useState<Set<string>>` selection. It binds to
 * react-table's built-in `rowSelection` state so the canonical toolbar
 * (`DataGridListToolbar`) can read selection uniformly for the bulk strip,
 * the "select all N records" banner, and selection-gated export.
 *
 * Requires the table to be created with `enableRowSelection: true` and a
 * stable `getRowId`. Read selected ids via `selectedRowIds(table)`.
 */
export function buildSelectColumn<TData>(options?: {
  /** Disable per-row selection for some rows (e.g. locked records). */
  enableRow?: (row: Row<TData>) => boolean;
  /**
   * WHY a disabled row cannot be selected, as a tooltip on its own checkbox.
   *
   * A box that is simply greyed out teaches nothing, and the reason is usually already
   * written on that row's own destructive action ("Published orders are amended, not
   * deleted"). Passing the SAME string here keeps the two from drifting into two
   * explanations of one rule. Returns undefined for a row that is selectable.
   */
  disabledReason?: (row: Row<TData>) => string | undefined;
  /**
   * Names the record in the checkbox's accessible label, e.g. `Select PSO-000123`.
   *
   * Defaults to the generic "Select row", which is all a grid of anonymous rows can say -
   * but on a list where every row has a reference, a screen reader (and a test) should hear
   * which one is being ticked.
   */
  rowLabel?: (row: Row<TData>) => string;
  /**
   * Header tick box ticks EVERY row across every page, not just the current one. Opt-in:
   * default is page-scope, which is what every other listing relies on.
   */
  selectAllRows?: boolean;
  size?: number;
}): ColumnDef<TData> {
  const size = options?.size ?? 44;
  return {
    id: 'select',
    header: ({ table }: { table: Table<TData> }) =>
      options?.selectAllRows ? (
        <Checkbox
          checked={
            table.getIsAllRowsSelected() || (table.getIsSomeRowsSelected() && 'indeterminate')
          }
          onCheckedChange={(value) => table.toggleAllRowsSelected(!!value)}
          aria-label="Select all rows"
        />
      ) : (
        <Checkbox
          checked={
            table.getIsAllPageRowsSelected() ||
            (table.getIsSomePageRowsSelected() && 'indeterminate')
          }
          onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
          aria-label="Select all rows on this page"
        />
      ),
    cell: ({ row }: { row: Row<TData> }) => {
      const blocked = !row.getCanSelect();
      const reason = blocked ? options?.disabledReason?.(row) : undefined;
      const box = (
        <Checkbox
          checked={row.getIsSelected()}
          disabled={blocked}
          onCheckedChange={(value) => row.toggleSelected(!!value)}
          aria-label={options?.rowLabel?.(row) ?? 'Select row'}
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
        />
      );
      if (!reason) return box;
      // The reason goes on a WRAPPER, not on the box: a disabled control does not reliably
      // receive the hover that would show its own tooltip, and the box would then be greyed
      // out with no way to learn why.
      return (
        <span title={reason} className="inline-flex">
          {box}
        </span>
      );
    },
    enableSorting: false,
    enableHiding: false,
    enableResizing: false,
    size,
    // AC-RS-69: the select column never drags - it is a fixed utility column, the
    // same reason the expand chevron (an expanded-row column) opts out too.
    meta: { draggable: false },
    ...(options?.enableRow ? { enableRowSelection: options.enableRow } : {}),
  };
}

/** Ids of currently-selected rows (page-scope), using the table's `getRowId`. */
export function selectedRowIds<TData>(table: Table<TData>): string[] {
  return table.getSelectedRowModel().rows.map((r) => r.id);
}
