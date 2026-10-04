'use client';

/**
 * LinkedComplaintsPanel - the complaints attached to one root cause or one
 * resolution, each row a link into the complaint detail page.
 *
 * ONE component for both master-data domains (and for both surfaces: the detail
 * page and the count-chip dialog), so the columns, empty state and links cannot
 * drift apart the way duplicated per-domain panels always do.
 *
 * Reads the normal complaints list endpoint via `root_cause_ids` /
 * `resolution_ids`, so filtering here and filtering on the Complaints page share
 * one backend code path.
 */

import Link from 'next/link';
import { MessageSquareWarning } from 'lucide-react';

import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import {
  complaintStatusLabel,
  complaintStatusPillClass,
} from '@/lib/complaint-status';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import {
  ColumnDef,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { useMemo } from 'react';

import { useComplaints } from '../complaints/hooks/useComplaints';
import type { Complaint } from '../complaints/types/complaint.types';

const MAX_ROWS = 100;

export interface LinkedComplaintsPanelProps {
  /** Exactly one of these is set; it decides which field the list is filtered on. */
  rootCauseId?: string;
  resolutionId?: string;
  /** Compact height for the dialog surface; the detail page uses the default. */
  maxHeightClassName?: string;
}

export function LinkedComplaintsPanel({
  rootCauseId,
  resolutionId,
  maxHeightClassName,
}: LinkedComplaintsPanelProps) {
  const { data, isLoading, isPlaceholderData, error, refetch } = useComplaints({
    pageIndex: 0,
    pageSize: MAX_ROWS,
    sorting: [{ id: 'complaint_date', desc: true }],
    searchQuery: '',
    root_cause_ids: rootCauseId ? [rootCauseId] : undefined,
    resolution_ids: resolutionId ? [resolutionId] : undefined,
  });

  const rows = useMemo(() => data?.data ?? [], [data]);

  const columns = useMemo<ColumnDef<Complaint>[]>(
    () => [
      {
        accessorKey: 'complaint_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="Complaint No." column={column} />
        ),
        size: 170,
        enableSorting: false,
        cell: ({ row }) => (
          // Human-readable identifier, never the UUID (cursor rule).
          <Link
            href={`/complaint-management/complaints/${row.original.id}`}
            className="text-primary hover:underline font-medium truncate"
            title={row.original.complaint_number ?? undefined}
          >
            {row.original.complaint_number || 'View complaint'}
          </Link>
        ),
        meta: {
          headerTitle: 'Complaint No.',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
      },
      {
        accessorKey: 'customer_name',
        header: ({ column }) => (
          <DataGridColumnHeader title="Customer" column={column} />
        ),
        size: 220,
        enableSorting: false,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.customer_name ?? undefined}>
            {row.original.customer_name || '-'}
          </span>
        ),
        meta: {
          headerTitle: 'Customer',
          skeleton: <Skeleton className="h-4 w-32" />,
        },
      },
      {
        accessorKey: 'delivery_order_number',
        header: ({ column }) => (
          <DataGridColumnHeader title="DO Number" column={column} />
        ),
        size: 150,
        enableSorting: false,
        cell: ({ row }) => (
          <span
            className="truncate"
            title={row.original.delivery_order_number ?? undefined}
          >
            {row.original.delivery_order_number || '-'}
          </span>
        ),
        meta: {
          headerTitle: 'DO Number',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
      },
      {
        accessorKey: 'complaint_date',
        header: ({ column }) => (
          <DataGridColumnHeader title="Complaint Date" column={column} />
        ),
        size: 170,
        enableSorting: false,
        cell: ({ row }) => (
          <span className="truncate">
            {row.original.complaint_date
              ? formatDateTimeInMalaysia(row.original.complaint_date as unknown as string)
              : '-'}
          </span>
        ),
        meta: {
          headerTitle: 'Complaint Date',
          skeleton: <Skeleton className="h-4 w-28" />,
        },
      },
      {
        accessorKey: 'status',
        header: ({ column }) => (
          <DataGridColumnHeader title="Status" column={column} />
        ),
        size: 150,
        enableSorting: false,
        cell: ({ row }) => {
          const status = row.original.status;
          if (!status) return '-';
          return (
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${complaintStatusPillClass(status)}`}
            >
              {complaintStatusLabel(status)}
            </span>
          );
        },
        meta: {
          headerTitle: 'Status',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
      },
      // No trailing chevron column: the complaint number in the first column is
      // already the link, and rows here are not click-navigable.
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.id,
    columnResizeMode: 'onChange',
    getCoreRowModel: getCoreRowModel(),
  });

  if (!isLoading && rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-4 py-10 text-center">
        <MessageSquareWarning className="size-7 text-muted-foreground/50" />
        <p className="text-sm font-medium">No complaints linked yet</p>
        <p className="text-xs text-muted-foreground">
          Complaints appear here once this is selected as their root cause or resolution.
        </p>
        <Link
          href="/complaint-management/complaints"
          className="mt-1 text-xs font-medium text-primary hover:underline"
        >
          Go to complaints
        </Link>
      </div>
    );
  }

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      loadingMode="skeleton"
      tableLayout={{
        width: 'fixed',
        columnsResizable: true,
        // The ScrollArea below is the caller's own vertical viewport
        // (`maxHeightClassName`, when set) - M5-05's default max-height would
        // double-bound it otherwise.
        scrollerMaxHeight: false,
      }}
    >
      <ScrollArea className={maxHeightClassName}>
        <DataGridTable />
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
    </DataGrid>
  );
}

export default LinkedComplaintsPanel;
