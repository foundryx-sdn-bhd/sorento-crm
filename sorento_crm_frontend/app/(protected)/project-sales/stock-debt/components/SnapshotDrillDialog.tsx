'use client';

import * as React from 'react';
import Link from 'next/link';
import { ColumnDef } from '@tanstack/react-table';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { Skeleton } from '@/components/ui/skeleton';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { STATUS_PILL_BASE } from '@/lib/status-pill';
import { cn } from '@/lib/utils';
import { RequestReserveDialog } from '../../_shared/components/RequestReserveDialog';
import { useOrderInquiryHeaderLines } from '../../_shared/hooks/useOrderInquiry';
import { reserveIneligibleReason } from '../../_shared/lib/reserveEligibility';
import { SNAPSHOTS_KEY, useSnapshotLinesQuery } from '../hooks/useStockDebtSnapshots';
import { buildDemandColumns, sumDemand } from '../lib/stockDebtDemandColumns';
import { signed } from '../lib/stockDebtColumns';
import type {
  SnapshotLine,
  SnapshotOiRow,
  SnapshotRow,
} from '../types/stockDebtSnapshot.types';
import { decisionText } from './SnapshotDecisionPopover';

const ACKNOWLEDGE_PERMISSION = 'projects.order_inquiries.acknowledge';

/** A line as one OI group shows it: the frozen line plus only that OI's rows. */
type GroupLine = SnapshotLine & { group_oi_rows: SnapshotOiRow[] };

interface OiGroup {
  oiId: string | null;
  inquiryNo: string | null;
  lines: GroupLine[];
}

const NEUTRAL = 'bg-muted text-foreground';
const AMBER = 'bg-amber-100 text-amber-800';
const GREEN = 'bg-emerald-100 text-emerald-800';
const RED = 'bg-red-100 text-red-800';
const SKY = 'bg-sky-100 text-sky-800';

function stateLabel(state: string): string {
  const text = state.replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const RESERVE_LABEL: Record<string, { text: string; tone: string }> = {
  parked: { text: 'Reserve parked', tone: AMBER },
  requested: { text: 'Reserve requested', tone: AMBER },
  reserved: { text: 'Reserved', tone: GREEN },
  cancelled: { text: 'Reserve cancelled', tone: NEUTRAL },
};

function Pill({ tone, children }: { tone: string; children: React.ReactNode }) {
  return <span className={cn(STATUS_PILL_BASE, 'normal-case', tone)}>{children}</span>;
}

/** What the line and its OI rows read like NOW (AC-12). */
function NowCell({ line }: { line: GroupLine }) {
  const soStatus = line.now.so_status;
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      {soStatus === 'open' ? (
        <Pill tone={NEUTRAL}>{`Open ${line.now.open_qty.toLocaleString()}`}</Pill>
      ) : (
        <Pill tone={RED}>{`SO ${soStatus ?? 'missing'}`}</Pill>
      )}
      {line.group_oi_rows.map((oi) => {
        const reserve = oi.reserve_state ? RESERVE_LABEL[oi.reserve_state] : null;
        return (
          <React.Fragment key={oi.row_id}>
            <Pill tone={NEUTRAL}>
              {oi.now_state === 'missing' ? 'Row gone' : stateLabel(oi.now_state ?? oi.state)}
            </Pill>
            {reserve && <Pill tone={reserve.tone}>{reserve.text}</Pill>}
            {oi.after_snapshot && <Pill tone={SKY}>after the snapshot</Pill>}
          </React.Fragment>
        );
      })}
    </div>
  );
}

function buildColumns(totals: ReturnType<typeof sumDemand>): ColumnDef<GroupLine>[] {
  return [
    ...buildDemandColumns<GroupLine>(totals),
    {
      id: 'short_qty',
      header: ({ column }) => <DataGridColumnHeader title="Short" column={column} />,
      size: 90,
      meta: { headerClassName: 'text-end', cellClassName: 'text-end' },
      footer: () => totals.short.toLocaleString(),
      cell: ({ row }) => (
        <span className="tabular-nums">{row.original.short_qty.toLocaleString()}</span>
      ),
    },
    {
      id: 'oi_row',
      header: ({ column }) => <DataGridColumnHeader title="OI row" column={column} />,
      size: 110,
      cell: ({ row }) =>
        row.original.group_oi_rows.length ? (
          <div className="flex flex-wrap gap-x-2">
            {row.original.group_oi_rows.map((oi) => (
              <Link
                key={oi.row_id}
                href={`/project-sales/order-inquiries/${oi.oi_id}?row=${oi.row_id}`}
                target="_blank"
                rel="noreferrer"
                className="font-medium tabular-nums text-primary hover:underline"
              >
                {oi.qty.toLocaleString()}
              </Link>
            ))}
          </div>
        ) : (
          <span className="text-muted-foreground">-</span>
        ),
    },
    {
      id: 'now',
      header: ({ column }) => <DataGridColumnHeader title="Now" column={column} />,
      size: 260,
      cell: ({ row }) => <NowCell line={row.original} />,
    },
  ];
}

/** The shared "Request CS to reserve", fed by the OI's live rows narrowed to this product's
 *  frozen rows that the OI page itself would still accept. Mounted only while open, so the
 *  OI is read when asked for. */
function OiRequestDialog({
  oiId,
  frozenRowIds,
  defaultWarehouseId,
  onOpenChange,
  onSent,
}: {
  oiId: string;
  frozenRowIds: Set<string>;
  defaultWarehouseId: string | null;
  onOpenChange: (open: boolean) => void;
  onSent: () => void;
}) {
  const live = useOrderInquiryHeaderLines(oiId);
  const rows = React.useMemo(
    () =>
      (live.data ?? []).filter(
        (row) => frozenRowIds.has(row.id) && reserveIneligibleReason(row) === null,
      ),
    [live.data, frozenRowIds],
  );
  return (
    <RequestReserveDialog
      inquiryId={oiId}
      rows={rows}
      defaultWarehouseId={defaultWarehouseId}
      open
      onOpenChange={onOpenChange}
      onSent={onSent}
    />
  );
}

function OiGroupGrid({
  group,
  canRequest,
  defaultWarehouseId,
  onRequested,
}: {
  group: OiGroup;
  canRequest: boolean;
  defaultWarehouseId: string | null;
  onRequested: () => void;
}) {
  const [requestOpen, setRequestOpen] = React.useState(false);
  const totals = React.useMemo(() => sumDemand(group.lines), [group.lines]);
  const columns = React.useMemo(() => buildColumns(totals), [totals]);

  const frozenRowIds = React.useMemo(
    () =>
      new Set(
        group.lines.flatMap((line) =>
          line.group_oi_rows.filter((oi) => !oi.after_snapshot).map((oi) => oi.row_id),
        ),
      ),
    [group.lines],
  );
  const requestable = group.lines.some((line) =>
    line.group_oi_rows.some((oi) => oi.requestable && !oi.after_snapshot),
  );

  return (
    <>
      <PanelDataGrid<GroupLine>
        title={
          group.oiId ? (
            <Link
              href={`/project-sales/order-inquiries/${group.oiId}`}
              target="_blank"
              rel="noreferrer"
              className="text-primary hover:underline"
            >
              {group.inquiryNo}
            </Link>
          ) : (
            'No OI'
          )
        }
        toolbar={
          group.oiId && canRequest && requestable ? (
            <Button type="button" size="sm" onClick={() => setRequestOpen(true)}>
              Request CS to reserve
            </Button>
          ) : undefined
        }
        columns={columns}
        rows={group.lines}
        listingKey="projects.stock_debt.view::snapshot-drill"
        emptyTitle="Nothing is due here"
        pageSize={10}
        // The DialogBody above already owns the scroll viewport (overflow-y-auto).
        scrollerMaxHeight={false}
      />
      {requestOpen && group.oiId && (
        <OiRequestDialog
          oiId={group.oiId}
          frozenRowIds={frozenRowIds}
          defaultWarehouseId={defaultWarehouseId}
          onOpenChange={setRequestOpen}
          onSent={onRequested}
        />
      )}
    </>
  );
}

/** Lines grouped by the OI they sit on (a line on two OIs shows in both), lines with no OI in
 *  a "No OI" group last. */
function groupByOi(lines: SnapshotLine[]): OiGroup[] {
  const byOi = new Map<string, OiGroup>();
  const noOi: GroupLine[] = [];
  for (const line of lines) {
    if (line.oi_rows.length === 0) {
      noOi.push({ ...line, group_oi_rows: [] });
      continue;
    }
    const rowsByOi = new Map<string, SnapshotOiRow[]>();
    for (const oi of line.oi_rows) rowsByOi.set(oi.oi_id, [...(rowsByOi.get(oi.oi_id) ?? []), oi]);
    for (const [oiId, oiRows] of rowsByOi) {
      let group = byOi.get(oiId);
      if (!group) {
        group = { oiId, inquiryNo: oiRows[0].inquiry_no, lines: [] };
        byOi.set(oiId, group);
      }
      group.lines.push({ ...line, group_oi_rows: oiRows });
    }
  }
  const groups = [...byOi.values()].sort((a, b) =>
    (a.inquiryNo ?? '').localeCompare(b.inquiryNo ?? ''),
  );
  return noOi.length ? [...groups, { oiId: null, inquiryNo: null, lines: noOi }] : groups;
}

/** AC-F5: the frozen drill for one cell (`month`) or one product (`month` null). */
export function SnapshotDrillDialog({
  snapshotId,
  row,
  month,
  label,
  takenAt,
  onClose,
}: {
  snapshotId: string;
  row: SnapshotRow;
  /** The bucket pressed, or null for every bucket. */
  month: string | null;
  /** What the pressed column said, or "all months". */
  label: string;
  takenAt: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const canRequest = useHasPermission(ACKNOWLEDGE_PERMISSION);
  const lines = useSnapshotLinesQuery(snapshotId, row.row_id, month);
  const stored = React.useMemo(() => lines.data?.lines ?? [], [lines.data]);
  const groups = React.useMemo(() => groupByOi(stored), [stored]);
  const shortTotal = React.useMemo(
    () => stored.reduce((sum, line) => sum + line.short_qty, 0),
    [stored],
  );

  const summary: Array<[string, string]> = [
    ['Total', signed(row.total)],
    ['BRW on hand', row.pool_on_hand.toLocaleString()],
    ['Lines', stored.length.toLocaleString()],
    ['Short', shortTotal.toLocaleString()],
    ['Decision', row.decision ? decisionText(row) : 'None'],
  ];

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        data-testid="snapshot-drill-dialog"
        className="flex max-h-[85vh] w-full flex-col overflow-hidden p-0 sm:max-w-[95vw]"
      >
        <DialogHeader className="shrink-0 space-y-1 border-b p-4 pe-10 sm:p-6 sm:pe-10">
          <DialogTitle className="min-w-0 break-words">{`${row.product_code} · ${label}`}</DialogTitle>
          <DialogDescription className="truncate text-xs">
            {`As frozen ${formatDateTimeInMalaysia(takenAt)}`}
          </DialogDescription>
          <div className="flex flex-wrap gap-x-5 gap-y-1 pt-1 text-sm">
            {summary.map(([name, value]) => (
              <span key={name}>
                <span className="text-muted-foreground">{name}</span>{' '}
                <span className="font-medium tabular-nums">{value}</span>
              </span>
            ))}
          </div>
        </DialogHeader>
        <DialogBody className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
          {lines.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
            </div>
          ) : lines.isError ? (
            <p className="text-sm text-destructive">
              {lines.error instanceof Error ? lines.error.message : 'The lines could not be loaded.'}
            </p>
          ) : groups.length === 0 ? (
            <div className="px-6 py-10 text-center">
              <p className="text-sm font-semibold">Nothing is due here</p>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                No sales order line falls in this column.
              </p>
            </div>
          ) : (
            groups.map((group) => (
              <OiGroupGrid
                key={group.oiId ?? 'no-oi'}
                group={group}
                canRequest={canRequest}
                defaultWarehouseId={row.decision_warehouse_id}
                onRequested={() =>
                  queryClient.invalidateQueries({ queryKey: [...SNAPSHOTS_KEY, 'lines', snapshotId] })
                }
              />
            ))
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
