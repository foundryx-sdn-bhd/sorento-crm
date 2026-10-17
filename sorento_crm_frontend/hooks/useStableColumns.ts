import { useMemo, useRef } from 'react';
import type { ColumnDef } from '@tanstack/react-table';

/**
 * Keeps the column array (and each cell, header and footer function) the same identity across
 * renders while still running the latest closure. TanStack mounts a cell as a component, so a
 * columns memo that depends on edit-draft state remounts every input on each keystroke:
 * the box loses focus and any element held by a caller goes stale.
 */
export function useStableColumns<T>(columns: ColumnDef<T>[]): ColumnDef<T>[] {
  const latest = useRef(columns);
  latest.current = columns;
  const shape = columns.map((c, i) => c.id ?? ('accessorKey' in c ? String(c.accessorKey) : String(i))).join('|');
  return useMemo(
    () =>
      columns.map((col, i) => {
        const wrapped = { ...col } as Record<string, unknown>;
        for (const key of ['cell', 'header', 'footer'] as const) {
          if (typeof col[key] !== 'function') continue;
          wrapped[key] = (ctx: unknown) => {
            const fn = (latest.current[i] as unknown as Record<string, unknown> | undefined)?.[key];
            return typeof fn === 'function' ? (fn as (c: unknown) => unknown)(ctx) : null;
          };
        }
        if (typeof (col as { accessorFn?: unknown }).accessorFn === 'function') {
          wrapped.accessorFn = (row: T, index: number) => {
            const fn = (latest.current[i] as { accessorFn?: (r: T, i: number) => unknown } | undefined)?.accessorFn;
            return fn ? fn(row, index) : undefined;
          };
        }
        return wrapped as unknown as ColumnDef<T>;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shape],
  );
}
