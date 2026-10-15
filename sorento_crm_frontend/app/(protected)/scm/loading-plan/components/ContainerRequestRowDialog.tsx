'use client';

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { StatCard } from '@/components/scm/StatCard';
import { formatDateInMalaysia } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import { EM_DASH, fmtInt } from '../../lib/format';
import type {
  ContainerRequestHistoryProduct,
  ContainerRequestRow,
  ContainerRequestSoLine,
} from '../../services/fulfilmentService';
import { ContainerRequestHistoryBars } from './ContainerRequestHistory';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import { distinctName } from '@/lib/product-display';

function dateLabel(iso: string | null): string {
  return iso ? formatDateInMalaysia(iso) : EM_DASH;
}

/**
 * Everything behind one product's row, in the shape the fulfilment board's cell popover
 * already uses (PLAN section 2b, AC-A2.3).
 *
 * The grid stays the scan surface and this is where a number is taken apart, which is the
 * captain's "too messy for one product" answered: the row says what to ask for, and one click
 * says why - what is needed, what covers it, WHERE the covering stock actually sits, what the
 * product does in a normal month, and what is already coming.
 *
 * A dialog rather than a popover on purpose: at 375px it fills the screen and scrolls, which
 * a popover anchored to a cell inside a horizontally-scrolling grid cannot do (AC-A2.5).
 */
/**
 * What the supplier says they hold, in the words of whichever document said it.
 *
 * A stock list states two quantities and they are never summed; a proforma states one, for
 * one container. `qty_packed` reads 0 on a proforma-sourced row (the backend zeroes it - a
 * proforma has no packed/unfinished split to report), which is how this line came to say
 * "They hold 0" under a grid cell reading 400.
 */
function holdingLabel(row: ContainerRequestRow): string {
  if (row.holding_source === 'proforma') {
    const stamp = row.holding_as_of ? ` ${dateLabel(row.holding_as_of)}` : '';
    return `They hold ${fmtInt(row.holding_qty ?? 0)} on PI${stamp}`;
  }
  if (row.holding_source === 'none') return 'Nothing of theirs on file';
  return `They hold ${fmtInt(row.qty_packed)} packed · ${fmtInt(row.qty_unfinished)} unfinished`;
}

export function ContainerRequestRowDialog({
  row,
  askQty,
  soLines,
  history,
  historyLoading,
  onClose,
}: {
  row: ContainerRequestRow;
  /** The quantity currently in the editable cell - her edit, not `suggested_qty`. */
  askQty: number;
  soLines: ContainerRequestSoLine[];
  history: ContainerRequestHistoryProduct | undefined;
  historyLoading: boolean;
  onClose: () => void;
}) {
  const title = row.item_code ?? row.product_name ?? 'Product';

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        data-testid="container-request-row-dialog"
        className="flex max-h-[92vh] w-full flex-col overflow-hidden p-0 sm:max-w-4xl"
      >
        <DialogHeader className="shrink-0 space-y-2 border-b p-4 sm:p-6">
          <DialogTitle className="min-w-0 break-words">
            {title}
            {row.rank !== null ? (
              <span className="ms-2 text-sm font-normal text-muted-foreground">
                rank {row.rank}
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription className="min-w-0 break-words">
            {row.row_kind === 'set'
              ? // Whose numbers these are, said outright. A set is never stocked and never
                // ordered - its members are - so every figure below belongs to one member,
                // and a reader who is not told which one cannot check any of them (R19).
                `Set of members. Figures from ${row.driver_item_code ?? 'its driver member'}${
                  distinctName(row.driver_item_code, row.driver_product_name)
                    ? ` - ${row.driver_product_name}`
                    : ''
                }`
              : row.product_name
                ? (distinctName(row.item_code, row.product_name) ?? '')
                : 'No product name on file'}
          </DialogDescription>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <StatCard
              testId="row-quantity-needed"
              label="Quantity needed"
              value={fmtInt(row.open_so_need)}
              sub={
                <>
                  Project {fmtInt(row.project_qty)} · Retail {fmtInt(row.retail_qty)}
                  <br />
                  {row.so_count} open sales order{row.so_count === 1 ? '' : 's'} · earliest
                  need-by {dateLabel(row.earliest_required_date)}
                </>
              }
            />
            <StatCard
              testId="row-suggestion"
              label="Ask supplier"
              value={fmtInt(askQty)}
              swatch="bg-rose-500"
              tone="text-rose-700"
              sub={
                <>
                  need {fmtInt(row.open_so_need)} - on hand {fmtInt(row.on_hand)} - SPO{' '}
                  {fmtInt(row.incoming_spo)} = {fmtInt(row.suggested_qty)}
                  <br />
                  {holdingLabel(row)}
                </>
              }
            />
          </div>
        </DialogHeader>

        <DialogBody className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
          <section>
            <h4 className="text-sm font-semibold">Where the stock is</h4>
            <ScrollArea>
              <table className="mt-2 w-auto min-w-full text-xs" data-testid="row-locations">
                <thead>
                  <tr className="text-muted-foreground">
                    <th className="py-1 text-start font-medium">Location</th>
                    <th className="py-1 text-start font-medium">Where</th>
                    <th className="py-1 text-end font-medium">On hand</th>
                    <th className="py-1 text-end font-medium">SPO</th>
                    <th className="py-1 text-end font-medium">Counted</th>
                  </tr>
                </thead>
                <tbody>
                  {row.sites.map((site) => (
                    <tr key={site.warehouse_code} className="border-t border-border">
                      <td className="py-1 font-medium">{site.warehouse_code}</td>
                      <td className="py-1 text-muted-foreground">Site pool</td>
                      <td className="py-1 text-end tabular-nums">{fmtInt(site.on_hand)}</td>
                      <td className="py-1 text-end tabular-nums">{fmtInt(site.incoming_spo)}</td>
                      <td className="py-1 text-end tabular-nums">
                        {fmtInt(site.on_hand + site.incoming_spo)}
                      </td>
                    </tr>
                  ))}
                  {/* Counted like any site row now (R7, captain 8 Sep 2026): the ask already
                      nets this stock, since the demand side counts the project need it
                      covers. No longer muted - a counted row reads as counted. */}
                  <tr className="border-t border-border">
                    <td className="py-1" title={row.group_locations.warehouse_codes.join(', ')}>
                      {row.group_locations.warehouse_codes.slice(0, 2).join(', ') || EM_DASH}
                      {row.group_locations.count > 2
                        ? `, ... (${row.group_locations.count})`
                        : ''}
                    </td>
                    <td className="py-1 text-muted-foreground">Group locations</td>
                    <td className="py-1 text-end tabular-nums">
                      {fmtInt(row.group_locations.on_hand)}
                    </td>
                    <td className="py-1 text-end tabular-nums">
                      {fmtInt(row.group_locations.incoming_spo)}
                    </td>
                    <td className="py-1 text-end tabular-nums">
                      {fmtInt(row.group_locations.on_hand + row.group_locations.incoming_spo)}
                    </td>
                  </tr>
                </tbody>
              </table>
              <ScrollBar orientation="horizontal" />
            </ScrollArea>
          </section>

          <section>
            <h4 className="text-sm font-semibold">Ordered, last 12 months (SO booked)</h4>
            <div className="mt-2">
              <ContainerRequestHistoryBars history={history} loading={historyLoading} />
            </div>
          </section>

          <section>
            <h4 className="text-sm font-semibold">Incoming, for reference</h4>
            <ul className="mt-2 space-y-1 text-xs" data-testid="row-incoming">
              {row.incoming_pl_shipments.map((s) => (
                <li key={s.shipment_id} className="flex items-center justify-between gap-2">
                  <span className="min-w-0 break-words">
                    PL {s.shipment_number ?? 'draft'}
                    {s.estimated_arrival_date
                      ? `, ETA ${dateLabel(s.estimated_arrival_date)}`
                      : ', no ETA'}
                  </span>
                  <span className="shrink-0 tabular-nums">{fmtInt(s.qty)}</span>
                </li>
              ))}
              {row.outstanding_po_lines.map((line, i) => (
                <li
                  key={`${line.po_number ?? 'po'}-${i}`}
                  className="flex items-center justify-between gap-2 text-muted-foreground"
                >
                  <span className="min-w-0 break-words">
                    PO {line.po_number ?? EM_DASH}
                    {line.expected_date ? `, due ${dateLabel(line.expected_date)}` : ''}
                  </span>
                  <span className="shrink-0 tabular-nums">{fmtInt(line.qty)}</span>
                </li>
              ))}
              {row.incoming_pl_shipments.length === 0 &&
              row.outstanding_po_lines.length === 0 ? (
                <li className="text-muted-foreground">
                  Nothing on a packing list or an open PO for this product.
                </li>
              ) : null}
            </ul>
          </section>

          <section>
            <h4 className="text-sm font-semibold">
              Contributing lines{soLines.length > 0 ? ` (${soLines.length})` : ''}
            </h4>
            {soLines.length === 0 ? (
              <p className="mt-2 text-xs text-muted-foreground">
                No open sales-order line behind this row.
              </p>
            ) : (
              <ScrollArea>
                <table className="mt-2 w-auto min-w-full text-xs" data-testid="row-so-lines">
                  <thead>
                    <tr className="text-muted-foreground">
                      <th className="py-1 text-start font-medium">Order</th>
                      <th className="py-1 text-start font-medium">Customer</th>
                      <th className="py-1 text-end font-medium">Balance</th>
                      <th className="py-1 text-end font-medium">Delivery</th>
                    </tr>
                  </thead>
                  <tbody>
                    {soLines.map((line, i) => (
                      <tr
                        key={`${line.so_number ?? 'so'}-${i}`}
                        className="border-t border-border"
                      >
                        <td className="py-1 font-medium">{line.so_number ?? EM_DASH}</td>
                        <td
                          className={cn(
                            'py-1',
                            line.demand_class === 'project'
                              ? 'text-foreground'
                              : 'text-muted-foreground',
                          )}
                        >
                          {line.customer_label ?? EM_DASH}
                        </td>
                        <td className="py-1 text-end tabular-nums">{fmtInt(line.qty)}</td>
                        <td className="py-1 text-end tabular-nums">
                          {dateLabel(line.required_date)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <ScrollBar orientation="horizontal" />
              </ScrollArea>
            )}
          </section>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

export default ContainerRequestRowDialog;
