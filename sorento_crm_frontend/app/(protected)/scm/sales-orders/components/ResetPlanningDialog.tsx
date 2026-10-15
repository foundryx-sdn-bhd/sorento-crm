'use client';

import { useState } from 'react';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { useResetSalesOrderPlanning } from '../../hooks/useSalesOrders';

/**
 * Reset planning (the captain, 27 Aug): a UAT walk has to be repeatable from the screen.
 * Shared by the Sales Orders and Consignments lists (CONSIGN-PLAN), so there is one dialog
 * and one importer of the retired confirm dialog.
 *
 * KEPT as a dialog where S6b turned single-record deletes into grace windows (D7). Two
 * reasons, either of them enough: it acts on a SELECTION of orders, and one countdown cannot
 * speak for a set; and it collects an answer ("also rewind the book"), which a countdown has
 * nowhere to put.
 */
export function ResetPlanningDialog({
  open,
  onOpenChange,
  orders,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orders: Array<{ id: string; so_number: string }>;
  onDone: () => void;
}) {
  const [rewindBook, setRewindBook] = useState(false);
  const resetMut = useResetSalesOrderPlanning();

  return (
    <ConfirmDeleteDialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) setRewindBook(false);
      }}
      title="Reset planning"
      confirmLabel="Reset planning"
      description={
        <span className="flex flex-col gap-3">
          <span>
            Put{' '}
            <span className="font-medium">
              {orders.map((o) => o.so_number).join(', ')}
            </span>{' '}
            back to never planned? Every order inquiry, link, allocation, stock
            transfer and confirmed revision on{' '}
            {orders.length === 1 ? 'it' : 'them'} is removed. The sales order
            and its lines stay. This cannot be undone.
          </span>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={rewindBook}
              onChange={(e) => setRewindBook(e.target.checked)}
            />
            <span>
              Also put the lines a sales-order upload changed back to before the
              first upload
            </span>
          </label>
        </span>
      }
      onDelete={async () => {
        for (const o of orders) {
          await resetMut.mutateAsync({ id: o.id, rewindBook });
        }
      }}
      successMessage="Planning reset"
      onSuccess={onDone}
    />
  );
}
