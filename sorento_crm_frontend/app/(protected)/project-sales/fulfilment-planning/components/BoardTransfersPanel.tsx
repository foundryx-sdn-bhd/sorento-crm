'use client';

import * as React from 'react';
import Link from 'next/link';
import { ColumnDef } from '@tanstack/react-table';
import { Check } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useHasPermission } from '@/hooks/usePermissions';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import {
  useBoardTransferMutations,
  useBoardTransfers,
} from '../../_shared/hooks/useBoardTransfers';
import {
  TRANSFER_KIND_LABEL,
  TRANSFER_STATE_LABEL,
  type StockTransfer,
} from '@/app/(protected)/inventory-management/stock-transfers/types/stockTransfer.types';

/** What the transfers page gates its own state changes on. */
const EDIT_PERMISSION = 'inventory.stock_transfers.edit';
/** And what it gates the READ on. Without it there is no list to show and none to ask for. */
const VIEW_PERMISSION = 'inventory.stock_transfers.view';

/** The movement one row states, in the transfers page's own words. */
function movementOf(transfer: StockTransfer): string {
  return `${transfer.qty} ${transfer.item_code ?? ''} ${transfer.from_location ?? '?'} to ${
    transfer.to_location ?? '?'
  }`.trim();
}

/**
 * The movements this board's confirmations raised, ON this board (PLAN section 3.D4).
 *
 * A confirmation that draws on the pool or on another location does not move any stock by
 * itself: it writes a `proposed` transfer that somebody has to approve, and until this panel
 * existed those lived on `/inventory-management/stock-transfers`, which is a screen the
 * planner had no reason to open. So the promise was made here and the movement it implied was
 * approved somewhere else, by somebody who had not seen the order it was for.
 *
 * It LISTS, it does not remember (D8): the rows are the open transfers for the orders on the
 * board, so a reload shows the same panel. Nothing about it depends on the confirm that was
 * pressed a minute ago.
 *
 * Approve and Approve all proposed are the EXISTING stock-transfer endpoints. Mark moved and
 * Cancel are deliberately not offered: the first needs the AutoCount reference and the second
 * needs a reason, and both belong to the transfers screen, which the row links to.
 */
export function BoardTransfersPanel({
  soNumbers,
  justConfirmed = false,
  inquiryRows = 0,
}: {
  soNumbers: string[];

  /**
   * Whether a confirmation was pressed on this board since it opened.
   *
   * An empty panel is HIDDEN on a board nobody has confirmed anything on - there is nothing
   * to say, and an empty card above the matrix is one more thing to read past. After a
   * confirm it stays, empty, and says so: "nothing had to move" is an outcome, and a panel
   * that vanished at that moment would read as a panel that failed to load.
   */
  justConfirmed?: boolean;
  /** Buy rows the same confirmation handed to purchasing (D10). */
  inquiryRows?: number;
}) {
  const canEdit = useHasPermission(EDIT_PERMISSION);
  // Not merely a hidden panel: the QUERY is off too (D9). A user with no read grant would
  // otherwise fire a request that comes back 403 on every board they open.
  const canView = useHasPermission(VIEW_PERMISSION);
  const { data, isLoading, error } = useBoardTransfers(soNumbers, canView);
  const { approve, approveAll } = useBoardTransferMutations();
  /**
   * The approval waiting on its confirmation - one row, or every proposed row.
   *
   * Approving a transfer commits somebody to physically moving stock between warehouses, so
   * it is confirmed first, with the SAME words the transfers page uses (the movement itself,
   * under the document number): one act, one sentence, wherever it is pressed from.
   */
  const [pending, setPending] = React.useState<StockTransfer | 'all' | null>(
    null,
  );

  const rows = React.useMemo<StockTransfer[]>(() => data?.data ?? [], [data]);
  const proposedRows = React.useMemo(
    () => rows.filter((row) => row.state === 'proposed'),
    [rows],
  );
  const proposedIds = React.useMemo(
    () => proposedRows.map((row) => row.id),
    [proposedRows],
  );

  const columns = React.useMemo<ColumnDef<StockTransfer>[]>(
    () => [
      {
        id: 'transfer_no',
        accessorFn: (row) => row.transfer_no,
        header: ({ column }) => (
          <DataGridColumnHeader title="Transfer no" column={column} />
        ),
        // The document number IS the way to the movement's own record, where it is marked
        // moved or cancelled - the two verbs this panel deliberately does not carry.
        cell: ({ row }) => (
          <Link
            href={`/inventory-management/stock-transfers/${row.original.id}`}
            onClick={(event) => event.stopPropagation()}
            className="block truncate text-sm font-medium text-primary hover:underline"
            title={row.original.transfer_no}
          >
            {row.original.transfer_no}
          </Link>
        ),
        size: 150,
        minSize: 120,
        meta: { headerTitle: 'Transfer no' },
      },
      {
        id: 'item_code',
        accessorFn: (row) => row.item_code ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Product" column={column} />
        ),
        cell: ({ row }) => (
          <div className="min-w-0">
            <div
              className="truncate text-sm"
              title={row.original.item_code ?? ''}
            >
              {row.original.item_code || 'Not stated'}
            </div>
            <div
              className="truncate text-xs text-muted-foreground"
              title={row.original.product_name ?? ''}
            >
              {row.original.product_name || ''}
            </div>
          </div>
        ),
        size: 180,
        minSize: 140,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'route',
        accessorFn: (row) =>
          `${row.from_location ?? ''} ${row.to_location ?? ''}`,
        header: ({ column }) => (
          <DataGridColumnHeader title="From / To" column={column} />
        ),
        // One cell, because it is one fact: a movement is the PAIR, and split over two
        // columns a reader has to hold the left one in their head to read the right one.
        cell: ({ row }) => {
          const route = `${row.original.from_location ?? 'Not stated'} to ${
            row.original.to_location ?? 'Not stated'
          }`;
          return (
            <span className="block truncate text-sm" title={route}>
              {route}
            </span>
          );
        },
        size: 170,
        minSize: 130,
        meta: { headerTitle: 'From / To' },
      },
      {
        id: 'qty',
        accessorFn: (row) => row.qty,
        header: ({ column }) => (
          <DataGridColumnHeader title="Qty" column={column} />
        ),
        cell: ({ row }) => (
          <span className="block truncate text-sm font-medium tabular-nums">
            {row.original.qty}
          </span>
        ),
        size: 90,
        minSize: 70,
        meta: { headerTitle: 'Qty' },
      },
      {
        id: 'kind',
        accessorFn: (row) => row.kind,
        header: ({ column }) => (
          <DataGridColumnHeader title="Kind" column={column} />
        ),
        // Section 2's own words, so the transfer says where the stock came from the way the
        // board said it a moment ago.
        cell: ({ row }) => (
          <span
            className="block truncate text-sm"
            title={TRANSFER_KIND_LABEL[row.original.kind]}
          >
            {TRANSFER_KIND_LABEL[row.original.kind]}
          </span>
        ),
        size: 150,
        minSize: 120,
        meta: { headerTitle: 'Kind' },
      },
      {
        id: 'for',
        accessorFn: (row) => `${row.so_number ?? ''} ${row.so_line_no ?? ''}`,
        header: ({ column }) => (
          <DataGridColumnHeader title="For" column={column} />
        ),
        cell: ({ row }) => {
          const label = row.original.so_number
            ? `${row.original.so_number} · line ${row.original.so_line_no ?? '?'}`
            : 'Not stated';
          return (
            <div className="min-w-0">
              <div className="truncate text-sm tabular-nums" title={label}>
                {label}
              </div>
              <div
                className="truncate text-xs text-muted-foreground"
                title={row.original.customer_name ?? ''}
              >
                {row.original.customer_name || ''}
              </div>
            </div>
          );
        },
        size: 180,
        minSize: 140,
        meta: { headerTitle: 'For' },
      },
      {
        id: 'state',
        accessorFn: (row) => row.state,
        header: ({ column }) => (
          <DataGridColumnHeader title="State" column={column} />
        ),
        cell: ({ row }) => (
          <span
            className={`${STATUS_PILL_BASE} normal-case ${statusPillClass(
              row.original.state === 'proposed'
                ? 'pending'
                : row.original.state,
            )}`}
          >
            {TRANSFER_STATE_LABEL[row.original.state]}
          </span>
        ),
        size: 150,
        minSize: 120,
        meta: { headerTitle: 'State' },
      },
      {
        id: 'proposed_at',
        accessorFn: (row) => row.proposed_at ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Proposed at" column={column} />
        ),
        cell: ({ row }) => (
          <span className="block truncate text-sm tabular-nums">
            {row.original.proposed_at
              ? formatDateTimeInMalaysia(row.original.proposed_at)
              : 'Not stated'}
          </span>
        ),
        size: 160,
        minSize: 130,
        meta: { headerTitle: 'Proposed at' },
      },
      {
        id: 'action',
        header: '',
        // Nothing at all without the permission (D9): a button that answers 403 is worse
        // than no button. An approved row keeps its place in the list and simply has no
        // verb left here - marking it moved belongs to the transfer's own record.
        cell: ({ row }) =>
          canEdit && row.original.state === 'proposed' ? (
            <div className="flex justify-end">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={approve.isPending || approveAll.isPending}
                onClick={(event) => {
                  event.stopPropagation();
                  setPending(row.original);
                }}
              >
                <Check className="size-4" aria-hidden />
                Approve
              </Button>
            </div>
          ) : null,
        size: 120,
        enableSorting: false,
        enableHiding: false,
        meta: { headerTitle: 'Action' },
      },
    ],
    [canEdit, approve, approveAll.isPending],
  );

  // No read grant, no panel (D9). Nothing about the movements is stated - not an empty
  // card, not an error - because none of it is this user's to see.
  if (!canView) return null;
  // Nothing raised and nothing pressed: no card. See the `justConfirmed` prop.
  if (!isLoading && !error && rows.length === 0 && !justConfirmed) return null;

  return (
    <div className="space-y-1">
      <PanelDataGrid<StockTransfer>
        title="Stock transfers"
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        listingKey="projects.projects.view::board-stock-transfers"
        isLoading={isLoading}
        error={error}
        emptyTitle="Nothing has to move"
        emptyBody="Every confirmed line is served from its own location."
        toolbar={
          canEdit && proposedIds.length > 0 ? (
            <Button
              type="button"
              size="sm"
              disabled={approveAll.isPending || approve.isPending}
              onClick={() => setPending('all')}
            >
              <Check className="size-4" aria-hidden />
              {`Approve all proposed (${proposedIds.length})`}
            </Button>
          ) : undefined
        }
        pageSize={10}
      />
      {/* The other half of what a confirmation produced: what nobody holds and somebody has
          to buy. A count and a way there, not a paragraph about what an inquiry is. */}
      {inquiryRows > 0 ? (
        <p className="text-sm text-muted-foreground">
          {`${inquiryRows} order inquiry row${inquiryRows === 1 ? '' : 's'} raised - `}
          <Link
            href="/project-sales/order-inquiries"
            className="text-primary hover:underline"
          >
            Order Inquiries
          </Link>
        </p>
      ) : null}

      {/* Confirm before the movement is committed to, in the transfers page's own words
          (`StockTransferActions`). `AlertDialog`, never `confirm()`. */}
      <AlertDialog
        open={pending !== null}
        onOpenChange={(next) => !next && setPending(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pending === 'all'
                ? `Approve ${proposedIds.length} proposed transfer${
                    proposedIds.length === 1 ? '' : 's'
                  }?`
                : `Approve ${pending?.transfer_no ?? ''}?`}
            </AlertDialogTitle>
            {/* Approve all names the count only: the twenty movements are the rows on the
                page already, and a paragraph repeating them was a wall (the captain, 28 Aug
                2026: "just remove the details here"). One transfer still states its move. */}
            <AlertDialogDescription>
              {pending === 'all'
                ? 'Every proposed transfer listed on this page is approved.'
                : pending
                  ? movementOf(pending)
                  : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              disabled={approve.isPending || approveAll.isPending}
            >
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={approve.isPending || approveAll.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (pending === 'all') approveAll.mutate(proposedIds);
                else if (pending) approve.mutate(pending.id);
                setPending(null);
              }}
            >
              Approve
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
