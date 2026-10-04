'use client';

import { useCallback, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import {
  Ban,
  Bookmark,
  Download,
  FileText,
  Link2,
  ListOrdered,
  Printer,
  ShoppingCart,
  Ship,
  Unlink,
  Undo2,
  Wand2,
} from 'lucide-react';
import BackToList from '@/components/common/BackToList';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DropdownMenuItem, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import DetailActions from '@/components/common/DetailActions';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import { EntityDownloadsButton } from '@/components/my-downloads/EntityDownloadsButton';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { useDeferredBulkAction } from '@/hooks/useDeferredBulkAction';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { LinkDocumentDialog } from '../../../_shared/components/LinkDocumentDialog';
import { BulkRejectOrderInquiryDialog } from '../../../_shared/components/BulkRejectOrderInquiryDialog';
import {
  ORDER_INQUIRY_HEADER_KEY,
  ORDER_INQUIRY_HEADER_LINES_KEY,
  ORDER_INQUIRY_HEADER_RELATED_DOCUMENTS_KEY,
  ORDER_INQUIRY_HEADERS_KEY,
  ORDER_INQUIRY_RESERVE_REQUESTS_KEY,
  orderInquiryHeadersPagerQuery,
  useAutoPlaceOrderInquiryRows,
  useCommitOrderInquiryReserve,
  useCreateOrderInquiryReserveRequest,
  useExportOrderInquiryXlsx,
  useOrderInquiryHandshake,
  useOrderInquiryHeaderDetail,
  useOrderInquiryHeaderLines,
  useOrderInquiryHeaderRelatedDocuments,
  useOrderInquiryReserveRequests,
  useOrderInquiryRowHistory,
} from '../../../_shared/hooks/useOrderInquiry';
import { useReserveRowOptions } from '../../../_shared/hooks/useReserveRowOptions';
import { ackStateOf } from '../../../_shared/lib/orderInquiryAck';
import {
  orderInquiryHeaderStatusLabel,
  orderInquiryHeaderStatusVariant,
} from '../../../_shared/lib/orderInquiryHeaderStatus';
import {
  bareLocationCode,
  canCancelReserveRequest,
  resolveReserveRowRequestAnchor,
} from '../../../_shared/lib/orderInquiryReserve';
import type { OrderInquiryWorklistRow } from '../../../_shared/types/orderInquiry.types';
import {
  foldKeyOf,
  isLiveInquiryRow,
  isWaitingUsedRow,
  reserveHistoryRowOf,
  type OrderInquiryLine,
} from '../../../_shared/lib/orderInquiryLineFold';
import type { CommitReservePayload } from '../../../_shared/services/orderInquiryReserveService';
import { OrderInquiryLinesTab } from './OrderInquiryLinesTab';
import { OrderInquiryGeneralTab } from './OrderInquiryGeneralTab';
import { ReserveRequestDialog } from './ReserveRequestDialog';
import { ReserveLineForm } from './ReserveLineForm';
import { OrderInquiryLineHistoryDialog } from './OrderInquiryLineHistoryDialog';
import type { StagedReserveEntry } from './orderInquiryHeaderLinesColumns';
import {
  OrderInquiryRelatedPurchaseOrdersTab,
  OrderInquiryRelatedSposTab,
} from './OrderInquiryRelatedDocumentsTab';

/** Same grant the worklist gates Choose document / Link / Unlink / Reject / Unconfirm
 * on (`OrderInquiriesClient.tsx`). */
const ORDER_INQUIRY_ACTION_PERMISSION = 'projects.order_inquiry.action';
/** Same grant Confirm is gated on everywhere else in this module. */
const ORDER_INQUIRY_ACKNOWLEDGE_PERMISSION = 'projects.order_inquiries.acknowledge';
/** R1 (`PLAN-oi-request-cs-reserve.md`): only the CS head confirms a reserve. */
const ORDER_INQUIRY_RESERVE_PERMISSION = 'projects.order_inquiries.reserve';

/** BLOCKER B1 (reviewer fix round): `linked_qty` deliberately EXCLUDES a reserve-kind
 * link (`links_for_rows`' own filter, `project_order_inquiry_service.py`: "a reserve
 * link is not a PO or an SPO document ... `reserved_qty` is where it actually
 * surfaces") - the row's TRUE remaining, the same arithmetic the backend's own
 * `create_request` caps `qty_requested` against, subtracts BOTH. */
function rowRemaining(row: OrderInquiryWorklistRow): number {
  return (
    Number(row.qty || '0') -
    Number(row.linked_qty || '0') -
    Number(row.reserved_qty || '0') -
    Number(row.bundled_qty || '0')
  );
}

/** AC-RS-23: why a selected row cannot be named on a "Request CS to reserve" ask - the
 * SAME rules the backend's own `create_request` enforces (3.2), read client-side off
 * the worklist row so the menu item can be gated and explained before the request is
 * even sent. `null` means the row is requestable.
 *
 * N9 (reviewer round): names the OFFENDING row - several rows can be ticked at once,
 * and a tooltip reading "already has an open reserve request" with no indication of
 * WHICH of them left the reader guessing. */
function reserveIneligibleReason(row: OrderInquiryWorklistRow): string | null {
  const item = row.item_code ?? 'This row';
  if (!['ORDER', 'ORDER_BACK'].includes(row.verb)) {
    return `${item}: not an ORDER or ORDER BACK row`;
  }
  if (!['raised', 'partly_linked'].includes(row.state)) {
    return `${item}: not open for a reserve request`;
  }
  if (rowRemaining(row) <= 0) {
    return `${item}: has nothing left to request`;
  }
  if (row.reserve_state === 'requested') {
    return `${item}: already has an open reserve request`;
  }
  return null;
}

export function OrderInquiryDetail({ id }: { id: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const canAct = useHasPermission(ORDER_INQUIRY_ACTION_PERMISSION);
  const canAcknowledge = useHasPermission(ORDER_INQUIRY_ACKNOWLEDGE_PERMISSION);
  const canReserve = useHasPermission(ORDER_INQUIRY_RESERVE_PERMISSION);
  // #1312 (Q6): the Lines tab's own paperclip upload/remove - the same grant that
  // confirms the board and writes OI lines.
  const canEditAttachments = useHasPermission('projects.projects.edit');
  const { data: session } = useSession();
  const currentUserId = session?.user?.id ?? null;

  const headerQuery = useOrderInquiryHeaderDetail(id);
  const linesQuery = useOrderInquiryHeaderLines(id);
  const relatedQuery = useOrderInquiryHeaderRelatedDocuments(id);
  const reserveRequestsQuery = useOrderInquiryReserveRequests(id);
  // S5 (reviewer round): the two reserve writes go through the hooks layer like every
  // other write on this page - `ReserveRequestDialog`/`ReserveRowDialog` receive the
  // mutate functions as props rather than importing the feature service themselves.
  const createReserveRequestMutation = useCreateOrderInquiryReserveRequest(id);
  const commitReserveMutation = useCommitOrderInquiryReserve(id);
  const { acknowledge, unacknowledge } = useOrderInquiryHandshake();
  const autoPlace = useAutoPlaceOrderInquiryRows();
  const exportXlsx = useExportOrderInquiryXlsx(id);

  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const [chooseDocumentOpen, setChooseDocumentOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reserveDialogOpen, setReserveDialogOpen] = useState(false);
  // Round 4 (`PLAN-oi-request-cs-reserve.md` 6e.2): CS's own staged (not yet
  // committed) decisions, keyed by OI row id - cleared on a successful commit or an
  // individual Undo. A page reload loses it by design (plan's own words: "a reload
  // loses it - trigger for server drafts: CS asks to stage across sessions").
  const [stagedByRowId, setStagedByRowId] = useState<Record<string, StagedReserveEntry>>({});
  // Which row `ReserveLineForm` is open for, and in which mode - the tick (AC-RS-84)
  // never opens this at all, it stages directly.
  const [editingRow, setEditingRow] = useState<{
    row: OrderInquiryWorklistRow;
    mode: 'reserve' | 'amend';
  } | null>(null);
  // Which sales order line the one History dialog is open for
  // (`PLAN-oi-no-double-count-25sep.md` AC-ND-13/14, owner ruling 26 Sep, G3).
  const [historyLine, setHistoryLine] = useState<OrderInquiryLine | null>(null);
  // AC-RS-89 / AC-ND-14 (review S4): the line's reserve history (its Reserve tab) is read
  // off the live row that carries a reserve, whichever row is the primary; a line with none
  // reads its primary row and gets no Reserve tab.
  const reserveHistoryRow = historyLine ? reserveHistoryRowOf(historyLine) : null;
  const historyRow = reserveHistoryRow ?? historyLine?.primary ?? null;
  const [downloadsOpen, setDownloadsOpen] = useState(false);
  // Unlink selected (AC-DP-06, fix round UL): a server-deferred pending action
  // (`order_inquiry_row.unlink`), never a confirm dialog - one park per ticked line,
  // ONE countdown rendered inline in the header card (`useDeferredBulkAction`'s own
  // `inline` surface). Cancel withdraws every park and leaves the ticked lines ticked;
  // the selection clears only once every park has actually settled (`onFinished`),
  // never at park time.
  const unlinkSelectedAction = useDeferredBulkAction({
    actionKey: 'order_inquiry_row.unlink',
    entityType: 'order_inquiry_row',
    surface: 'inline',
    verb: 'Unlinking',
    pastVerb: 'unlinked',
    describe: (count) => `${count} line${count === 1 ? '' : 's'}`,
    invalidateKeys: [
      [ORDER_INQUIRY_HEADER_LINES_KEY, id],
      [ORDER_INQUIRY_HEADER_KEY, id],
      [ORDER_INQUIRY_HEADER_RELATED_DOCUMENTS_KEY, id],
      [ORDER_INQUIRY_HEADERS_KEY],
    ],
    onFinished: () => setRowSelection({}),
  });

  // S6 (AC-B6-4): a `?row=<id>` deep link always lands on Lines, whatever `tab` says -
  // the only tab that row could ever be found on.
  const tab = searchParams.get('row') ? 'lines' : searchParams.get('tab') || 'lines';
  function handleTabChange(next: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === 'lines') params.delete('tab');
    else params.set('tab', next);
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  const lines = useMemo(() => linesQuery.data ?? [], [linesQuery.data]);
  // S0 (`PLAN-oi-no-double-count-25sep.md`, G1/G6): a used row is history now, so it is
  // never ticked, linked, unlinked or rejected from this page - only swept by Confirm.
  const activeLines = useMemo(() => lines.filter(isLiveInquiryRow), [lines]);
  const selectedLines = useMemo(
    () => activeLines.filter((l) => rowSelection[l.id]),
    [activeLines, rowSelection],
  );
  const selectedIds = useMemo(() => selectedLines.map((l) => l.id), [selectedLines]);
  // AC-ND-12: the sales order lines ticked. Review S2: a line whose only waiting rows are
  // used ticks those used rows, so "anything ticked" reads the lines, not `selectedIds`
  // (live rows only) - else one such tick would widen Unconfirm and Auto link to the OI.
  const tickedLineKeys = useMemo(
    () => new Set(lines.filter((l) => rowSelection[l.id]).map(foldKeyOf)),
    [lines, rowSelection],
  );
  const anyTicked = tickedLineKeys.size > 0;
  const selectedConfirmable = useMemo(
    () =>
      selectedLines.filter((l) => {
        const state = ackStateOf(l);
        return state === 'awaiting' || state === 'changed';
      }),
    [selectedLines],
  );
  // Review S2 (G6): a ticked line whose only waiting rows are used ones (its live rows
  // were confirmed before the sweep existed, or it has none) sends those used rows, so
  // the header never sits Outstanding on a row no tick can confirm. A line with a live
  // row still waiting sends its live rows only; the server sweeps its used rows.
  const selectedUsedToConfirm = useMemo(() => {
    const confirmableKeys = new Set(selectedConfirmable.map(foldKeyOf));
    return lines.filter(
      (l) =>
        isWaitingUsedRow(l) &&
        tickedLineKeys.has(foldKeyOf(l)) &&
        !confirmableKeys.has(foldKeyOf(l)),
    );
  }, [lines, tickedLineKeys, selectedConfirmable]);
  const selectedUnconfirmable = useMemo(
    () =>
      selectedLines.filter((l) => {
        const state = ackStateOf(l);
        return state === 'acknowledged' || state === 'changed';
      }),
    [selectedLines],
  );
  // W (coordinator's ruling): nothing ticked means every confirmed line of the WHOLE
  // OI, the same ticked/whole-OI symmetry Confirm and Auto link already have - not just
  // "nothing to unconfirm". `activeLines` already holds every non-cancelled line of this
  // header (`useOrderInquiryHeaderLines` reads every page of the worklist's own
  // `inquiry_id` filter), so no extra fetch is needed for the "all" scope.
  const allUnconfirmable = useMemo(
    () =>
      activeLines.filter((l) => {
        const state = ackStateOf(l);
        return state === 'acknowledged' || state === 'changed';
      }),
    [activeLines],
  );
  const unconfirmScope = anyTicked ? selectedUnconfirmable : allUnconfirmable;
  const selectedRejectable = useMemo(
    () =>
      selectedLines.filter(
        (l) => ackStateOf(l) !== 'rejected' && ['raised', 'partly_linked', 'placed'].includes(l.state),
      ),
    [selectedLines],
  );
  const selectedLinked = useMemo(
    () => selectedLines.filter((l) => l.state === 'placed' || l.state === 'partly_linked'),
    [selectedLines],
  );
  const chooseDocumentLine =
    selectedIds.length === 1 ? (activeLines.find((l) => l.id === selectedIds[0]) ?? null) : null;

  // AC-RS-23: enabled only when EVERY selected row is requestable; disabled otherwise,
  // with a tooltip naming the FIRST reason (not a tally of every one).
  const reserveIneligibleReasons = useMemo(
    () => selectedLines.map(reserveIneligibleReason).filter((reason): reason is string => Boolean(reason)),
    [selectedLines],
  );
  const canRequestReserve = selectedLines.length > 0 && reserveIneligibleReasons.length === 0;
  const reserveDisabledReason =
    selectedLines.length === 0 ? 'Select at least one row' : reserveIneligibleReasons[0];

  const reserveDialogEntries = useMemo(
    () =>
      selectedLines.map((line) => ({
        key: line.id,
        productId: line.product_id ?? null,
        location: line.location ?? null,
      })),
    [selectedLines],
  );
  const reserveDialogResolved = useReserveRowOptions(reserveDialogOpen ? reserveDialogEntries : []);
  const reserveDialogRows = useMemo(
    () =>
      selectedLines.map((line) => {
        const own = reserveDialogResolved[line.id];
        const remaining = rowRemaining(line);
        return {
          id: line.id,
          item_code: line.item_code ?? null,
          delivery_date: line.delivery_date ?? null,
          remaining: String(Math.max(0, remaining)),
          defaultLocation: own?.defaultWarehouseId ?? '',
          locationOptions: own?.options ?? [],
        };
      }),
    [selectedLines, reserveDialogResolved],
  );

  function invalidateReserveQueries() {
    linesQuery.refetch();
    reserveRequestsQuery.refetch();
  }

  // AC-RS-83/84/87: the OI's own open reserve request, if any - round 4 keeps a
  // single-open-request-at-a-time model (the same one the Lines tab's own State
  // filter and the header CTA both key off); a row that already carries a link
  // answered by a PAST (no longer open) request is reached through `answeredRequestRowFor`
  // below instead.
  const openReserveRequest = reserveRequestsQuery.data?.find((r) => r.state === 'requested');

  // 6c F2/6e.2: the row's own OPEN (unanswered) request-row entry, extended with the
  // warehouse the request itself was raised against (R3's own default pool) - the
  // tick (AC-RS-84) stages against exactly this, no dialog, no second resolution.
  const openRequestForRow = useCallback(
    (targetRowId: string | null) => {
      if (!targetRowId) return null;
      const request = (reserveRequestsQuery.data ?? []).find(
        (r) =>
          r.state === 'requested' &&
          r.rows.some((row) => row.row_id === targetRowId && row.qty_reserved == null),
      );
      const row = request?.rows.find((r) => r.row_id === targetRowId);
      if (!request || !row) return null;
      return {
        requestId: request.id,
        ordinal: request.ordinal,
        qtyRequested: row.qty_requested,
        warehouseId: row.warehouse_id,
        location: row.location,
        requestedBy: request.requested_by,
        requestedByName: request.requested_by_name,
        requestedAt: request.requested_at,
      };
    },
    [reserveRequestsQuery.data],
  );

  // AC-RS-86/89 + 6e.4 (B3): the ANSWERED request row that anchors Amend/History for a
  // reserved or declined line - the latest answered one, the same row the server's
  // `commit_request` amends (`resolveReserveRowRequestAnchor`).
  const answeredRequestRowFor = useCallback(
    (targetRowId: string) => {
      const candidates = (reserveRequestsQuery.data ?? [])
        .flatMap((r) =>
          r.rows
            .filter((row) => row.row_id === targetRowId && row.qty_reserved != null)
            .map((row) => ({
              id: r.id,
              ordinal: r.ordinal,
              rowQtyReserved: row.qty_reserved,
              qtyRequested: row.qty_requested,
              location: row.location,
              requestedByName: r.requested_by_name,
            })),
        );
      if (candidates.length === 0) return null;
      const anchorId = resolveReserveRowRequestAnchor(candidates);
      return candidates.find((c) => c.id === anchorId) ?? null;
    },
    [reserveRequestsQuery.data],
  );

  // AC-RS-84: the tick - stages the FULL requested qty at the default pool the
  // request itself already named, no dialog, nothing posted. A request row naming no
  // pool still stages: the commit then omits `warehouse_id` and the server defaults it.
  const handleTickReserve = useCallback(
    (row: OrderInquiryWorklistRow) => {
      const open = openRequestForRow(row.id);
      if (!open) return;
      setStagedByRowId((prev) => ({
        ...prev,
        [row.id]: {
          kind: 'reserve',
          qty: Number(open.qtyRequested || '0'),
          warehouseId: open.warehouseId ?? null,
          locationLabel: open.location,
          reason: null,
        },
      }));
    },
    [openRequestForRow],
  );

  const handleEditReserve = useCallback((row: OrderInquiryWorklistRow) => {
    setEditingRow({ row, mode: 'reserve' });
  }, []);

  const handleAmendReserve = useCallback((row: OrderInquiryWorklistRow) => {
    setEditingRow({ row, mode: 'amend' });
  }, []);

  const handleLineHistoryClick = useCallback((line: OrderInquiryLine) => {
    setHistoryLine(line);
  }, []);

  const handleUndoStaged = useCallback((rowId: string) => {
    setStagedByRowId((prev) => {
      if (!(rowId in prev)) return prev;
      const next = { ...prev };
      delete next[rowId];
      return next;
    });
  }, []);

  // AC-RS-85/86: `ReserveLineForm`'s own pool options - resolved only while it is
  // open, for that ONE row (the same lazy-per-row pattern `useReserveRowOptions`
  // already gives the purchasing-side dialog above).
  const editingRowOptionsEntries = useMemo(
    () =>
      editingRow
        ? [
            {
              key: editingRow.row.id,
              productId: editingRow.row.product_id ?? null,
              location: editingRow.row.location ?? null,
            },
          ]
        : [],
    [editingRow],
  );
  const editingRowOptionsResolved = useReserveRowOptions(editingRowOptionsEntries);
  const editingRowOptions = editingRow ? editingRowOptionsResolved[editingRow.row.id] : undefined;
  const editingOpenRequest = editingRow ? openRequestForRow(editingRow.row.id) : null;
  const editingAnsweredRow = editingRow ? answeredRequestRowFor(editingRow.row.id) : null;
  // 6e.4 "Amend edits the LINE's net": the form edits the pill's figure. Its answered
  // request rows, newest request first (the server's own order).
  const editingAnsweredRows = useMemo(
    () =>
      editingRow
        ? [...(reserveRequestsQuery.data ?? [])]
            .sort((a, b) => b.ordinal - a.ordinal)
            .flatMap((r) =>
              r.rows.filter((row) => row.row_id === editingRow.row.id && row.qty_reserved != null),
            )
        : [],
    [editingRow, reserveRequestsQuery.data],
  );
  // What the line has asked for, a later balance request counted once (the server's
  // own rule): net kept on every earlier answered row + the latest row's own ask,
  // capped at the line's qty - 36 asked / 10 got, then 26 asked reads 36, not 62.
  const editingRequestedTotal =
    editingAnsweredRows.length === 0
      ? 0
      : Math.min(
          editingAnsweredRows
            .slice(1)
            .reduce((sum, row) => sum + Number(row.qty_reserved || '0'), 0) +
            Number(editingAnsweredRows[0].qty_requested || '0'),
          Number(editingRow?.row.qty || '0'),
        );

  function handleStage(payload: { warehouse_id?: string; qty_reserved: number; reason: string | null }) {
    if (!editingRow) return;
    const rowId = editingRow.row.id;
    if (editingRow.mode === 'reserve') {
      const locationOption = (editingRowOptions?.options ?? []).find(
        (option) => option.value === payload.warehouse_id,
      );
      setStagedByRowId((prev) => ({
        ...prev,
        [rowId]: {
          kind: 'reserve',
          qty: payload.qty_reserved,
          warehouseId: payload.warehouse_id ?? null,
          locationLabel: locationOption ? bareLocationCode(locationOption.label) : null,
          reason: payload.reason,
        },
      }));
    } else {
      setStagedByRowId((prev) => ({
        ...prev,
        [rowId]: { kind: 'amend', qty: payload.qty_reserved, reason: payload.reason },
      }));
    }
    setEditingRow(null);
  }

  const historyAnchorRequestId =
    (historyRow ? openRequestForRow(historyRow.id)?.requestId : null) ??
    (historyRow ? answeredRequestRowFor(historyRow.id)?.id : null) ??
    null;
  const historyQuery = useOrderInquiryRowHistory(historyAnchorRequestId, historyRow?.id ?? null);

  // AC-RS-87 + 6e.4: the header's own `Reserve (N)` CTA - ONE commit call for every
  // staged line, whichever request it belongs to. The server resolves each row to its
  // open (reserve) or latest answered (amend) request row; the client names rows only.
  const stagedCount = Object.keys(stagedByRowId).length;
  function commitStaged() {
    const entries = Object.entries(stagedByRowId);
    if (entries.length === 0) return;
    const reserves: CommitReservePayload['reserves'] = [];
    const amendments: CommitReservePayload['amendments'] = [];
    let requesterName: string | null = null;
    for (const [rowId, entry] of entries) {
      if (entry.kind === 'reserve') {
        reserves.push({
          row_id: rowId,
          ...(entry.warehouseId ? { warehouse_id: entry.warehouseId } : {}),
          qty_reserved: entry.qty,
          reason: entry.reason,
        });
        requesterName = requesterName ?? openRequestForRow(rowId)?.requestedByName ?? null;
      } else {
        amendments.push({ row_id: rowId, qty_reserved: entry.qty, reason: entry.reason });
        requesterName = requesterName ?? answeredRequestRowFor(rowId)?.requestedByName ?? null;
      }
    }
    commitReserveMutation.mutate(
      { payload: { reserves, amendments }, requesterName },
      { onSuccess: () => setStagedByRowId({}) },
    );
  }

  // AC-RS-89: "Cancel request" moves into the Actions menu (deferred countdown, same
  // shape round 1-3 already used) - no per-dialog header any more.
  const reserveRequestCancelAction = useDeferredAction({
    actionKey: 'order_inquiry_reserve_request.cancel',
    entityType: 'order_inquiry_reserve_request',
    entityId: openReserveRequest?.id ?? null,
    verb: 'Cancelling',
    subject: openReserveRequest ? `Request #${openReserveRequest.ordinal}` : '',
    surface: 'inline',
    watchFromMount: Boolean(openReserveRequest),
    successMessage: 'Reserve request cancelled',
    invalidateKeys: [
      [ORDER_INQUIRY_HEADER_LINES_KEY, id],
      [ORDER_INQUIRY_RESERVE_REQUESTS_KEY, id],
    ],
  });
  const canCancelOpenRequest =
    Boolean(openReserveRequest) &&
    canCancelReserveRequest(currentUserId, openReserveRequest?.requested_by, canReserve);


  const header = headerQuery.data;

  // AC-DP-05: the visible label is simply how many lines are TICKED; disabled reads the
  // scope's own eligibility - the ticked lines' handshake state with something ticked,
  // the header's own count with nothing ticked.
  // AC-ND-12 (`PLAN-oi-no-double-count-25sep.md`): the grid ticks LINES, so N counts the
  // ticked sales order lines, not the rows behind them.
  const confirmLabel = anyTicked ? `Confirm (${tickedLineKeys.size})` : 'Confirm';
  const confirmDisabled = anyTicked
    ? selectedConfirmable.length === 0 && selectedUsedToConfirm.length === 0
    : (header?.lines_to_confirm ?? 0) === 0;

  function runConfirm() {
    // G6 (owner ruling 26 Sep): the ticked lines' live rows; the server takes on each
    // line's waiting used rows in the same call (S1, AC-ND-24). Review S2: plus the used
    // rows of a ticked line with nothing live left to confirm.
    acknowledge.mutate(
      anyTicked
        ? { rowIds: [...selectedIds, ...selectedUsedToConfirm.map((l) => l.id)] }
        : { filter: { inquiry_id: id } },
      { onSuccess: () => setRowSelection({}) },
    );
  }

  function runUnconfirm() {
    unacknowledge.mutate(
      unconfirmScope.map((l) => l.id),
      { onSuccess: () => setRowSelection({}) },
    );
  }

  /**
   * "Link selected" (R18, owner ruling from the hand test on stack C, 25 Sep 2026,
   * supersedes G1): the SAME mutation as `runAutoLink` below, scoped to exactly the
   * ticked lines via `row_ids` - it never turns a suggestion into a link on its own.
   * Pressing it re-runs the AutoCount book step for the ticked lines (in AutoCount's
   * own name) and refreshes their suggestions, catching a mistake in the automation
   * rather than promising a placement. Unlike `runAutoLink`, nothing ticked means
   * nothing to do - there is no whole-OI fallback for this press.
   */
  function runLinkSelected() {
    if (selectedIds.length === 0) return;
    autoPlace.mutate(
      { row_ids: selectedIds },
      { onSuccess: () => setRowSelection({}) },
    );
  }

  /**
   * Auto link (AC-DP-06, owner markup 21 Sep): ALWAYS enabled, no confirm dialog -
   * unlike the worklist's own "Auto link all", which asks for a link-horizon date
   * first. Ticked lines -> exactly those (unfiltered - the cascade itself decides what
   * it can and cannot place, the same as the worklist's own unconditional run over
   * everything); nothing ticked -> the whole OI via `filter: { inquiry_id }` (AC-AL-01).
   * Same hook, same result reporting (`linkOutcomeText`'s toast) as `runLinkSelected`
   * above - since R18 the two presses share the exact same call, just a different
   * scope when nothing is ticked.
   */
  function runAutoLink() {
    // Review S2: a tick on a line with only used rows is still a tick - it links nothing,
    // and it never widens to the whole OI.
    if (anyTicked && selectedIds.length === 0) return;
    autoPlace.mutate(
      anyTicked ? { row_ids: selectedIds } : { filter: { inquiry_id: id } },
      { onSuccess: () => setRowSelection({}) },
    );
  }

  /**
   * Unlink selected (AC-DP-06, fix round UL): one `order_inquiry_row.unlink` pending
   * action per ticked line, behind the one inline countdown `unlinkSelectedAction`
   * renders in the header card. No dialog, no immediate commit.
   */
  function unlinkSelected() {
    if (selectedLinked.length === 0) return;
    unlinkSelectedAction.run(selectedLinked.map((line) => ({ id: line.id })));
  }

  // S1 (reviewer, fix round 22 Sep 2026): `page.tsx`'s own `PageHeader` already renders
  // a `BackToList` - a second one at the top of every state here was redundant. The
  // in-card CTA on not-found stays: it is the page's own next step, not a nav duplicate.
  if (headerQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  const notFound =
    headerQuery.isError &&
    headerQuery.error instanceof Error &&
    headerQuery.error.message.includes('no longer exists');

  if (notFound) {
    return (
      <div className="space-y-4">
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <div className="text-sm font-semibold">This order inquiry no longer exists</div>
          <BackToList listPath="/project-sales/order-inquiries" label="Back to order inquiries" />
        </Card>
      </div>
    );
  }

  if (headerQuery.isError || !header) {
    return (
      <div className="space-y-4">
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <div className="text-sm font-semibold">Could not load this order inquiry</div>
          <p className="max-w-md text-sm text-muted-foreground">
            {headerQuery.error instanceof Error
              ? headerQuery.error.message
              : 'Try again in a moment.'}
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="block py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 flex-col gap-1">
              <div className="flex min-w-0 flex-wrap items-center gap-3">
                <CardTitle className="text-lg tabular-nums">{header.inquiry_no}</CardTitle>
                <Badge
                  variant={orderInquiryHeaderStatusVariant(header.status)}
                  appearance="light"
                  size="md"
                >
                  {orderInquiryHeaderStatusLabel(header.status)}
                </Badge>
              </div>
              <span className="text-sm text-muted-foreground">
                Raised by {header.raised_by_name ?? 'Not recorded'}
                {header.raised_at ? ` on ${formatDateTimeInMalaysia(header.raised_at)}` : ''}
              </span>
            </div>
            <DetailActions
              pager={{
                ...orderInquiryHeadersPagerQuery,
                detailPath: '/project-sales/order-inquiries',
                currentId: id,
                ariaLabel: 'order inquiry',
              }}
              gear={
                <DetailActionsMenu ariaLabel="Order inquiry options">
                  {canAct ? (
                    <>
                      <DropdownMenuItem onSelect={runAutoLink}>
                        <Wand2 className="size-4" aria-hidden />
                        Auto link
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={!chooseDocumentLine}
                        onSelect={
                          chooseDocumentLine
                            ? (e) => {
                                e.preventDefault();
                                setChooseDocumentOpen(true);
                              }
                            : undefined
                        }
                      >
                        <Link2 className="size-4" aria-hidden />
                        Choose document
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={selectedIds.length === 0}
                        onSelect={selectedIds.length ? runLinkSelected : undefined}
                      >
                        <Wand2 className="size-4" aria-hidden />
                        Link selected
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={selectedLinked.length === 0}
                        onSelect={selectedLinked.length ? unlinkSelected : undefined}
                      >
                        <Unlink className="size-4" aria-hidden />
                        Unlink selected
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={selectedRejectable.length === 0}
                        onSelect={
                          selectedRejectable.length
                            ? (e) => {
                                e.preventDefault();
                                setRejectOpen(true);
                              }
                            : undefined
                        }
                      >
                        <Ban className="size-4" aria-hidden />
                        Reject selected
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={unconfirmScope.length === 0}
                        onSelect={unconfirmScope.length ? runUnconfirm : undefined}
                      >
                        <Undo2 className="size-4" aria-hidden />
                        Unconfirm
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                    </>
                  ) : null}
                  {canAcknowledge ? (
                    canRequestReserve ? (
                      <DropdownMenuItem
                        onSelect={(e) => {
                          e.preventDefault();
                          setReserveDialogOpen(true);
                        }}
                      >
                        <Bookmark className="size-4" aria-hidden />
                        Request CS to reserve
                      </DropdownMenuItem>
                    ) : (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="block">
                            <DropdownMenuItem disabled>
                              <Bookmark className="size-4" aria-hidden />
                              Request CS to reserve
                            </DropdownMenuItem>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>{reserveDisabledReason}</TooltipContent>
                      </Tooltip>
                    )
                  ) : null}
                  {canCancelOpenRequest ? (
                    <DropdownMenuItem
                      onSelect={(e) => {
                        e.preventDefault();
                        reserveRequestCancelAction.start();
                      }}
                    >
                      <Undo2 className="size-4" aria-hidden />
                      Cancel request
                    </DropdownMenuItem>
                  ) : null}
                  <DropdownMenuItem
                    disabled={exportXlsx.isPending}
                    onSelect={
                      exportXlsx.isPending ? undefined : () => exportXlsx.mutate()
                    }
                  >
                    <Download className="size-4" aria-hidden />
                    Export Excel
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={(e) => {
                      e.preventDefault();
                      setDownloadsOpen(true);
                    }}
                  >
                    <Printer className="size-4" aria-hidden />
                    Download history
                  </DropdownMenuItem>
                </DetailActionsMenu>
              }
              primary={
                canAcknowledge || (canReserve && (Boolean(openReserveRequest) || stagedCount > 0)) ? (
                  <div className="flex items-center gap-2">
                    {canAcknowledge ? (
                      <Button
                        size="sm"
                        onClick={runConfirm}
                        disabled={confirmDisabled || acknowledge.isPending}
                      >
                        {confirmLabel}
                      </Button>
                    ) : null}
                    {/* AC-RS-87: visible with the reserve permission while a request is
                        open or anything is staged; enabled only once something is
                        staged. */}
                    {canReserve && (openReserveRequest || stagedCount > 0) ? (
                      <Button
                        size="sm"
                        data-testid="reserve-cta"
                        onClick={commitStaged}
                        disabled={stagedCount === 0 || commitReserveMutation.isPending}
                      >
                        {stagedCount > 0 ? `Reserve (${stagedCount})` : 'Reserve'}
                      </Button>
                    ) : null}
                  </div>
                ) : undefined
              }
            />
          </div>
          {unlinkSelectedAction.countdown ? (
            <div className="mt-3">{unlinkSelectedAction.countdown}</div>
          ) : null}
          {reserveRequestCancelAction.countdown ? (
            <div className="mt-3">{reserveRequestCancelAction.countdown}</div>
          ) : null}
        </CardHeader>
      </Card>

      <Tabs value={tab} onValueChange={handleTabChange} className="w-full">
        <TabsList variant="line" className="mb-4 w-full justify-start">
          <TabsTrigger value="lines">
            <ListOrdered />
            <span>Lines</span>
          </TabsTrigger>
          <TabsTrigger value="general">
            <FileText />
            <span>General</span>
          </TabsTrigger>
          <TabsTrigger value="related_po">
            <ShoppingCart />
            <span>Related PO</span>
          </TabsTrigger>
          <TabsTrigger value="related_spo">
            <Ship />
            <span>Related SPO</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="lines" className="mt-0 focus-visible:outline-none">
          <OrderInquiryLinesTab
            lines={lines}
            isLoading={linesQuery.isLoading}
            error={linesQuery.error}
            onRetry={() => void linesQuery.refetch()}
            rowSelection={rowSelection}
            onRowSelectionChange={setRowSelection}
            canReserve={canReserve}
            stagedByRowId={stagedByRowId}
            onTickReserve={handleTickReserve}
            onEditReserve={handleEditReserve}
            onAmendReserve={handleAmendReserve}
            onLineHistoryClick={handleLineHistoryClick}
            onUndoStaged={handleUndoStaged}
            canEditAttachments={canEditAttachments}
          />
        </TabsContent>

        <TabsContent value="general" className="mt-0 focus-visible:outline-none">
          <OrderInquiryGeneralTab header={header} />
        </TabsContent>

        <TabsContent value="related_po" className="mt-0 focus-visible:outline-none">
          <OrderInquiryRelatedPurchaseOrdersTab
            rows={relatedQuery.data?.purchase_orders ?? []}
            isLoading={relatedQuery.isLoading}
            error={relatedQuery.error}
            onRetry={() => void relatedQuery.refetch()}
          />
        </TabsContent>

        <TabsContent value="related_spo" className="mt-0 focus-visible:outline-none">
          <OrderInquiryRelatedSposTab
            rows={relatedQuery.data?.spos ?? []}
            isLoading={relatedQuery.isLoading}
            error={relatedQuery.error}
            onRetry={() => void relatedQuery.refetch()}
          />
        </TabsContent>
      </Tabs>

      {chooseDocumentOpen && chooseDocumentLine ? (
        <LinkDocumentDialog
          rowId={chooseDocumentLine.id}
          itemCode={chooseDocumentLine.item_code}
          qty={chooseDocumentLine.qty}
          linkedQty={String(
            (chooseDocumentLine.links ?? []).reduce((sum, l) => sum + Number(l.qty || '0'), 0),
          )}
          deliveryDate={chooseDocumentLine.delivery_date}
          onDone={() => {
            setChooseDocumentOpen(false);
            setRowSelection({});
          }}
        />
      ) : null}

      <BulkRejectOrderInquiryDialog
        rowIds={selectedRejectable.map((l) => l.id)}
        open={rejectOpen}
        onOpenChange={setRejectOpen}
        onRejected={() => setRowSelection({})}
      />

      {reserveDialogOpen ? (
        <ReserveRequestDialog
          open={reserveDialogOpen}
          onOpenChange={setReserveDialogOpen}
          rows={reserveDialogRows}
          onSend={(payload) => createReserveRequestMutation.mutateAsync(payload)}
          onSent={() => {
            setRowSelection({});
            invalidateReserveQueries();
          }}
        />
      ) : null}

      {/* 6e.4 (S2): mounted only once the row's pool options have loaded, so the
          reserve-mode prefill never reads an empty availability map. */}
      {editingRow && (editingRow.mode === 'amend' || (editingRowOptions && !editingRowOptions.isLoading)) ? (
        <ReserveLineForm
          open
          onOpenChange={(next) => {
            if (!next) setEditingRow(null);
          }}
          itemCode={editingRow.row.item_code ?? null}
          mode={editingRow.mode}
          requestedQty={
            editingRow.mode === 'reserve'
              ? (editingOpenRequest?.qtyRequested ?? '0')
              : editingAnsweredRows.length > 0
                ? String(editingRequestedTotal)
                : (editingRow.row.reserved_qty ?? '0')
          }
          maxQty={
            editingRow.mode === 'amend'
              ? String(
                  Number(editingRow.row.reserved_qty || '0') +
                    Math.max(0, rowRemaining(editingRow.row)),
                )
              : undefined
          }
          answeredRequestCount={editingAnsweredRows.length}
          locationOptions={editingRowOptions?.options ?? []}
          availableQtyByLocation={editingRowOptions?.availableQtyByWarehouseId ?? {}}
          defaultLocationId={
            editingOpenRequest?.warehouseId ?? editingRowOptions?.defaultWarehouseId ?? null
          }
          lockedLocationLabel={editingAnsweredRow?.location ?? editingRow.row.location ?? ''}
          initialQty={editingRow.row.reserved_qty ?? '0'}
          onStage={handleStage}
        />
      ) : null}

      {historyLine ? (
        <OrderInquiryLineHistoryDialog
          line={historyLine}
          onOpenChange={(next) => {
            if (!next) setHistoryLine(null);
          }}
          // AC-ND-14: a Reserve tab only when the line has reserve history - any live row
          // reserved or declined (review S4), not the primary row alone.
          reserveEntries={reserveHistoryRow ? (historyQuery.data ?? []) : undefined}
        />
      ) : null}

      <EntityDownloadsButton
        entityType="order_inquiry"
        entityId={id}
        label={header.inquiry_no}
        open={downloadsOpen}
        onOpenChange={setDownloadsOpen}
      />
    </div>
  );
}

export default OrderInquiryDetail;
