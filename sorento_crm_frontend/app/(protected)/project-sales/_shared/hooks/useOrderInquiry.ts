'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { useUploadActivity } from '@/components/upload-activity/useUploadActivity';
import { ENTITY_DOWNLOADS_QUERY_KEY, MY_DOWNLOADS_QUERY_KEY } from '@/services/myDownloadsService';
import {
  acknowledgeOrderInquiryRows,
  acknowledgeOrderInquiryRowsByFilter,
  autoPlaceOrderInquiryRows,
  exportOrderInquiryXlsx,
  getOrderInquiryHeader,
  getOrderInquiryHeaderLines,
  getOrderInquiryHeaderRelatedDocuments,
  getOrderInquiryPoCandidates,
  getOrderInquiryUploadJob,
  getOrderInquiryPoDetail,
  getOrderInquirySpoDetail,
  getOrderInquirySummary,
  getOrderInquiryWorklistSummary,
  getSalesOrderInquiry,
  getUnplaceAllPreview,
  listOrderInquiryHeaders,
  listOrderInquiryRows,
  linkNowOrderInquiryRows,
  listOrderInquiryWorklist,
  markOrderInquiryRows,
  rejectOrderInquiryRow,
  rejectOrderInquiryRows,
  placeOrderInquiryRowOnPo,
  placeOrderInquiryRowOnPoAllocations,
  unacknowledgeOrderInquiryRows,
  unplaceAllOrderInquiryRows,
  unplaceOrderInquiryRow,
} from '../services/orderInquiryService';
import {
  commitOrderInquiryReserve,
  createOrderInquiryReserveRequest,
  getDecisionTrail,
  getOrderInquiryReserveRequests,
  getOrderInquiryRowHistory,
  getReserveNoticeRecipients,
  getReserveStatusSummary,
  previewParkedReserveNotices,
  previewParkedReserveRequests,
  sendParkedReserveNotices,
  sendParkedReserveRequests,
  type CommitReservePayload,
  type CreateReserveRequestPayload,
} from '../services/orderInquiryReserveService';
import { getOrderInquiryMatrix } from '../services/orderInquiryMatrixService';
import {
  previewHandoverResend,
  sendHandoverResend,
} from '../services/orderInquiryHandoverResendService';
import { PLANNING_BOARD_KEY } from './useFulfilmentPlanning';
import type { LinkHorizonRequest } from '../lib/linkHorizon';
import { acknowledgeOutcomeText, linkOutcomeText } from '../lib/linkHorizon';
import type {
  AutoPlaceRequest,
  OrderInquiryHeaderListParams,
  OrderInquiryListParams,
  OrderInquiryMatrixParams,
  OrderInquiryPoAllocation,
  OrderInquiryWorklistParams,
  UnplaceAllRequest,
} from '../types/orderInquiry.types';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import type { ListPagerParams, ListPagerPage } from '@/hooks/useListPager';

export const ORDER_INQUIRY_ROWS_KEY = 'project-order-inquiry-rows';
export const ORDER_INQUIRY_SUMMARY_KEY = 'project-order-inquiry-summary';
export const ORDER_INQUIRY_KEY = 'project-order-inquiry';
export const ORDER_INQUIRY_WORKLIST_KEY = 'order-inquiry-worklist';
export const ORDER_INQUIRY_WORKLIST_SUMMARY_KEY = 'order-inquiry-worklist-summary';
export const ORDER_INQUIRY_PO_CANDIDATES_KEY = 'order-inquiry-po-candidates';
export const ORDER_INQUIRY_PO_DETAIL_KEY = 'order-inquiry-po-detail';
export const ORDER_INQUIRY_SPO_DETAIL_KEY = 'order-inquiry-spo-detail';
export const ORDER_INQUIRY_UNPLACE_ALL_PREVIEW_KEY = 'order-inquiry-unplace-all-preview';
export const ORDER_INQUIRY_UPLOAD_JOB_KEY = 'order-inquiry-upload-job';
export const ORDER_INQUIRY_MATRIX_KEY = 'order-inquiry-matrix';
export const ORDER_INQUIRY_HEADERS_KEY = 'order-inquiry-headers';
export const ORDER_INQUIRY_HEADER_KEY = 'order-inquiry-header';
export const ORDER_INQUIRY_HEADER_LINES_KEY = 'order-inquiry-header-lines';
export const ORDER_INQUIRY_HEADER_RELATED_DOCUMENTS_KEY =
  'order-inquiry-header-related-documents';
export const ORDER_INQUIRY_RESERVE_REQUESTS_KEY = 'order-inquiry-reserve-requests';
export const ORDER_INQUIRY_RESERVE_SUMMARY_KEY = 'order-inquiry-reserve-summary';
export const ORDER_INQUIRY_PARKED_PREVIEW_KEY = 'order-inquiry-parked-preview';
export const ORDER_INQUIRY_HANDOVER_RESEND_PREVIEW_KEY = 'order-inquiry-handover-resend-preview';
export const ORDER_INQUIRY_NOTICE_RECIPIENTS_KEY = 'order-inquiry-notice-recipients';
export const ORDER_INQUIRY_ROW_HISTORY_KEY = 'order-inquiry-row-history';
export const DECISION_TRAIL_KEY = 'order-inquiry-decision-trail';

/**
 * The header LIST's own React Query key (`PLAN-oi-header-list-detail.md`). Built through
 * one function so `useOrderInquiryHeaders` and `orderInquiryHeadersPagerQuery`'s pager
 * (`useListPager`) can never construct it two different ways.
 */
export function orderInquiryHeadersListQueryKey(params: OrderInquiryHeaderListParams) {
  return [ORDER_INQUIRY_HEADERS_KEY, params] as const;
}

/** The list query a detail URL describes, in the shape the header list passes.
 *
 * S3 (reviewer, fix round 22 Sep 2026): defaults `state`/`sort` the same way
 * `OrderInquiryHeadersList`'s own `params` memo does (`stateFilter` defaults to
 * `'outstanding'`, `sort` to `'raised_at'`) rather than leaving them `undefined`
 * when the URL omits them (the list's own default view never writes `?state=` or
 * `?sort=` - see its own `useEffect` above). Query-key hashing drops `undefined`
 * properties, so an un-defaulted `state`/`sort` here builds a DIFFERENT key from
 * the list's own `{state:'outstanding', sort:'raised_at', ...}` on the very page
 * a reader opens a detail from by default - a cache miss on `useListPager`'s
 * `useQuery`, which then fires a second, redundant list request. */
function orderInquiryHeaderListParamsFromUrl(
  params: ListPagerParams,
): OrderInquiryHeaderListParams {
  return {
    page: params.pageIndex + 1,
    limit: params.pageSize,
    sort: params.sorting?.[0]?.id ?? 'raised_at',
    dir: params.sorting?.[0]?.desc ? 'desc' : 'asc',
    query: params.searchQuery || undefined,
    state:
      (params.filters.state as OrderInquiryHeaderListParams['state']) || 'outstanding',
    raised_by: params.filters.raised_by || undefined,
    agent: params.filters.agent || undefined,
    project: params.filters.project || undefined,
    reserve: (params.filters.reserve as OrderInquiryHeaderListParams['reserve']) || undefined,
  };
}

/** The detail page's prev/next pager (`DetailActions`'s `pager` prop): the same key and
 * fetch the list itself uses, so a step within the loaded page issues no request. */
export const orderInquiryHeadersPagerQuery = {
  listQueryKey: (params: ListPagerParams) =>
    orderInquiryHeadersListQueryKey(orderInquiryHeaderListParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    listOrderInquiryHeaders(orderInquiryHeaderListParamsFromUrl(params)),
};

/** The Documents view (AC-HL-01..07): one row per order inquiry header. */
export function useOrderInquiryHeaders(params: OrderInquiryHeaderListParams = {}) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: orderInquiryHeadersListQueryKey(params),
    queryFn: () => listOrderInquiryHeaders(params),
  });
}

/** The detail page's own header (AC-DP-01/07): the Order/Customer blocks, counts, status
 * and raise history. */
export function useOrderInquiryHeaderDetail(id: string | undefined) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_HEADER_KEY, id],
    queryFn: () => getOrderInquiryHeader(id as string),
    enabled: Boolean(id),
    retry: false,
  });
}

/** The Lines tab (AC-DP-03): every row this header raised, cancelled rows included since
 * `PLAN-oi-no-double-count-25sep.md` S2 (`include_history`) - the tab folds them into
 * their line's History. Reuses the cross-project worklist's own `inquiry_id` filter (see
 * `orderInquiryService.ts`'s header section). */
export function useOrderInquiryHeaderLines(id: string | undefined) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_HEADER_LINES_KEY, id],
    queryFn: () => getOrderInquiryHeaderLines(id as string),
    enabled: Boolean(id),
  });
}

/** Related PO / Related SPO tabs (AC-DP-08). */
export function useOrderInquiryHeaderRelatedDocuments(id: string | undefined) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_HEADER_RELATED_DOCUMENTS_KEY, id],
    queryFn: () => getOrderInquiryHeaderRelatedDocuments(id as string),
    enabled: Boolean(id),
  });
}

/**
 * `PLAN-oi-request-cs-reserve.md` section 6c: every reserve request this header has
 * ever raised, newest first - `ReserveRowDialog`'s own read, used to find a row's open
 * request (or its last-answered one).
 */
export function useOrderInquiryReserveRequests(inquiryId: string | undefined) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_RESERVE_REQUESTS_KEY, inquiryId],
    queryFn: () => getOrderInquiryReserveRequests(inquiryId as string),
    enabled: Boolean(inquiryId),
  });
}

/**
 * Send a "Request CS to reserve" (or park it). `RequestReserveDialog`
 * (`_shared/components`) calls this hook, which supplies the invalidate + toast the
 * layering rule asks for; the OI page and the Stock Debt snapshot drill share it.
 */
export function useCreateOrderInquiryReserveRequest(inquiryId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateReserveRequestPayload) =>
      createOrderInquiryReserveRequest(inquiryId as string, payload),
    onSuccess: (response, variables) => {
      toast.success(
        variables.park
          ? `Request #${response.ordinal} parked. Send it from Order inquiries > Actions.`
          : `Request #${response.ordinal} sent to ${response.first_to_name ?? 'CS'}`,
      );
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_RESERVE_REQUESTS_KEY, inquiryId] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_RESERVE_SUMMARY_KEY] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

/**
 * `PLAN-oi-request-cs-reserve.md` section 6e.2, AC-RS-87: the Lines grid's own header
 * `Reserve (N)` CTA - ONE commit call for every staged decision at once (supersedes
 * the per-row `useReserveOrderInquiryRow`, whose own route is retired). Invalidates
 * both the lines (the pills/chips move) and the reserve requests (the staged map's
 * own source) on success.
 */
export function useCommitOrderInquiryReserve(inquiryId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
    }: {
      payload: CommitReservePayload;
      /** The requester's own name, for the toast alone - never read by `mutationFn`. */
      requesterName?: string | null;
      /** The notice recipient named in the parked toast. */
      noticeName?: string | null;
    }) => commitOrderInquiryReserve(inquiryId as string, payload),
    onSuccess: (data, variables) => {
      // Every staged line was a no-op on the server: nothing was written or mailed.
      toast.success(
        data.length === 0
          ? 'Nothing to change'
          : variables.payload.park_notice
            ? `Reserved. Notice to ${variables.noticeName ?? 'purchasing'} parked; send it from Order inquiries > Actions.`
            : `Reserved, ${variables.noticeName ?? variables.requesterName ?? 'the requester'} notified`,
      );
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_LINES_KEY, inquiryId] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_RESERVE_REQUESTS_KEY, inquiryId] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_RESERVE_SUMMARY_KEY] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

/** F3: one row's own History tab, fetched only while `ReserveRowDialog` is open for it. */
export function useOrderInquiryRowHistory(
  requestId: string | null | undefined,
  rowId: string | null | undefined,
) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_ROW_HISTORY_KEY, requestId, rowId],
    queryFn: () => getOrderInquiryRowHistory(requestId as string, rowId as string),
    enabled: Boolean(requestId) && Boolean(rowId),
  });
}

/**
 * `PLAN-oi-decision-trail-ui.md` (round 2, AC-DT-10): the History icon's own read,
 * keyed by the CORE sales-order line rather than by a row or a request - the id an OI row
 * and a fulfilment-board line both point at, so the same trail opens from either surface.
 * Fetched only while `DecisionTrailButton`'s own dialog is open for it.
 */
export function useDecisionTrail(coreLineId: string | null | undefined) {
  return useQuery({
    queryKey: [DECISION_TRAIL_KEY, coreLineId],
    queryFn: () => getDecisionTrail(coreLineId as string),
    enabled: Boolean(coreLineId),
  });
}

/**
 * The detail page's own Export Excel (Lane B, AC-B1/AC-B5): starts the export and
 * refreshes both surfaces that show it - My Downloads' own drawer and this OI's
 * "Download history" chip. The workbook itself is fetched later from My Downloads,
 * once the worker marks the row ready - this mutation never returns or saves a file.
 */
export function useExportOrderInquiryXlsx(id: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => exportOrderInquiryXlsx(id as string),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MY_DOWNLOADS_QUERY_KEY });
      queryClient.invalidateQueries({
        queryKey: [...ENTITY_DOWNLOADS_QUERY_KEY, 'order_inquiry', id],
      });
      toast.success('Preparing the order inquiry export - it will appear in My Downloads.');
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to start the order inquiry export'),
  });
}

export const orderInquiryRowsKey = (
  projectId: string,
  params: OrderInquiryListParams,
) => [ORDER_INQUIRY_ROWS_KEY, projectId, params];

export function useOrderInquiryRows(
  projectId: string | undefined,
  params: OrderInquiryListParams = {},
) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: orderInquiryRowsKey(projectId ?? '', params),
    queryFn: () => listOrderInquiryRows(projectId as string, params),
    enabled: Boolean(projectId),
  });
}

export function useOrderInquirySummary(projectId: string | undefined) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_SUMMARY_KEY, projectId],
    queryFn: () => getOrderInquirySummary(projectId as string),
    enabled: Boolean(projectId),
  });
}

/**
 * Purchasing's own worklist: every raised row, across every project AND every adopted
 * AutoCount order, which belongs to no project and is therefore reachable nowhere else.
 *
 * `enabled` lets a caller hold the request off until it is actually needed - the
 * calendar view's day drilldown, for one, has nothing to ask for until a day is picked.
 */
export function useOrderInquiryWorklist(
  params: OrderInquiryWorklistParams = {},
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [ORDER_INQUIRY_WORKLIST_KEY, params],
    queryFn: () => listOrderInquiryWorklist(params),
    enabled: options.enabled,
    // Every filter and every page is a new key, so without this the grid empties itself
    // between the press and the answer and the page jumps under the cursor.
  });
}

/**
 * The month strip and the state counts.
 *
 * Sent WITH the month filter, because the strip's totals are the visible month's. The
 * month AXIS (`by_month`) is the one thing the server computes ignoring that filter, so
 * the control that changes month never empties itself.
 */
export function useOrderInquiryWorklistSummary(
  params: OrderInquiryWorklistParams = {},
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [ORDER_INQUIRY_WORKLIST_SUMMARY_KEY, params],
    queryFn: () => getOrderInquiryWorklistSummary(params),
    enabled: options.enabled,
    // Above all here: the month strip is inside this answer, so without it pressing a
    // month makes the control you just used vanish until the next answer lands.
  });
}

/**
 * The Schedule matrix's own read (S3): the same filters as the list, plus which axis and
 * which date cut. Server-side GROUP BY in Phase 2; a small in-process fixture in Phase 1
 * (`orderInquiryMatrixService`) - the caller never has to know which.
 */
export function useOrderInquiryMatrix(
  params: OrderInquiryMatrixParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [ORDER_INQUIRY_MATRIX_KEY, params],
    queryFn: () => getOrderInquiryMatrix(params),
    enabled: options.enabled,
  });
}

/**
 * "Unplace all"'s own count for the toolbar button and its confirm dialog - the CURRENT
 * worklist scope (the SAME filters the list itself reads), resolved server-side so it is
 * right regardless of how many pages the matching set actually spans. Kept live the same
 * way the month strip is: it has to go to zero the instant the last placed row in scope
 * is dealt with, or the button stays clickable on an empty scope.
 */
export function useUnplaceAllPreview(
  filters: UnplaceAllRequest = {},
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [ORDER_INQUIRY_UNPLACE_ALL_PREVIEW_KEY, filters],
    queryFn: () => getUnplaceAllPreview(filters),
    enabled: options.enabled,
  });
}

export function useSalesOrderInquiry(psoId: string | undefined) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_KEY, psoId],
    queryFn: () => getSalesOrderInquiry(psoId as string),
    enabled: Boolean(psoId),
    // A sales order that has not published yet has no inquiry, and 404 is the honest
    // answer rather than a failure worth retrying.
    retry: false,
  });
}

/**
 * Marking rows refetches the rows AND the summary: the header count is the thing that
 * tells purchasing how much of this project is still open, and a stale one is worse than
 * no count at all.
 */
export function useOrderInquiryMutations(projectId: string) {
  const queryClient = useQueryClient();

  const mark = useMutation({
    mutationFn: ({ rowIds, state }: { rowIds: string[]; state: string }) =>
      markOrderInquiryRows(rowIds, state),
    onSuccess: (rows, variables) => {
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_ROWS_KEY, projectId] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_SUMMARY_KEY, projectId] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_KEY] });
      const count = rows.length;
      const said =
        variables.state === 'actioned'
          ? 'marked as actioned'
          : variables.state === 'cancelled'
            ? 'cancelled'
            : 'reopened';
      toast.success(`${count} row${count === 1 ? '' : 's'} ${said}`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { mark };
}

/** Candidates for one row's "Place on PO" dialog (section G), fetched only while it is
 * open - `enabled` lets the caller hold the request off until the dialog mounts. */
export function useOrderInquiryPoCandidates(
  rowId: string | undefined,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_PO_CANDIDATES_KEY, rowId],
    queryFn: () => getOrderInquiryPoCandidates(rowId as string),
    enabled: Boolean(rowId) && options.enabled !== false,
  });
}

/** The document lightbox's PO half: that purchase order's header, every line and who
 * holds its quantity. Fetched only while the dialog is open - `enabled` holds the
 * request off until then. */
export function useOrderInquiryPoDetail(
  poId: string | undefined,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_PO_DETAIL_KEY, poId],
    queryFn: () => getOrderInquiryPoDetail(poId as string),
    enabled: Boolean(poId) && options.enabled !== false,
  });
}

/** The same lightbox's SPO half, addressed by the shipping order's own number. */
export function useOrderInquirySpoDetail(
  spoNumber: string | undefined,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_SPO_DETAIL_KEY, spoNumber],
    queryFn: () => getOrderInquirySpoDetail(spoNumber as string),
    enabled: Boolean(spoNumber) && options.enabled !== false,
    // A shipping order the endpoint cannot answer for is an empty state, not something
    // to ask for three more times.
    retry: false,
  });
}

/**
 * Place on PO / Unplace (section G). Not scoped to a project id: the per-project screen
 * and purchasing's cross-project worklist carry the same row action, so both invalidate
 * every query family a placement touches rather than just their own screen's.
 */
export function useOrderInquiryPlacementMutations() {
  const queryClient = useQueryClient();

  function invalidateAfterPlacement() {
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_ROWS_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_SUMMARY_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_SUMMARY_KEY] });
    // Another raised row on the same product may now cover less (or more) of the
    // document line this one just linked or freed.
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_PO_CANDIDATES_KEY] });
    // A single link/unlink moves the linked count "Unlink all" reads too.
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_UNPLACE_ALL_PREVIEW_KEY] });
    // W (`PLAN-oi-header-list-detail.md`): the OI detail page's own Choose document /
    // Link selected / Unlink selected all move the Related PO/SPO tabs and the Lines
    // tab's own document chips - and Related PO/SPO's own footer totals.
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADERS_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_LINES_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_RELATED_DOCUMENTS_KEY] });
  }

  const place = useMutation({
    mutationFn: ({ rowId, poLineId }: { rowId: string; poLineId: string }) =>
      placeOrderInquiryRowOnPo(rowId, poLineId),
    onSuccess: () => {
      invalidateAfterPlacement();
      toast.success('Linked');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  /**
   * The cascade shape: one or more `{po_line_id | spo_allocation_id, qty}` lines in one
   * call. SET semantics (S8, AC-CF-25): the submission becomes the row's whole link
   * set - a line the row held that is missing from it is retired (unless
   * `offeredLineIds` says the caller never saw it), a resubmitted line is adjusted, and
   * a new line is linked.
   */
  const placeAllocations = useMutation({
    mutationFn: ({
      rowId,
      allocations,
      offeredLineIds,
    }: {
      rowId: string;
      allocations: OrderInquiryPoAllocation[];
      offeredLineIds?: string[];
    }) => placeOrderInquiryRowOnPoAllocations(rowId, allocations, offeredLineIds),
    onSuccess: () => {
      invalidateAfterPlacement();
      toast.success('Linked');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  /** Unlink: one link when `linkId` names it, every link the row holds when it does not. */
  const unplace = useMutation({
    mutationFn: ({ rowId, linkId }: { rowId: string; linkId?: string }) =>
      unplaceOrderInquiryRow(rowId, linkId),
    onSuccess: () => {
      invalidateAfterPlacement();
      toast.success('Unlinked');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { place, placeAllocations, unplace };
}

/**
 * Run the cascade now - the worklist's "Auto-link". Invalidates the same query families a
 * single link does, since a bulk pass can touch any of them.
 */
export function useAutoPlaceOrderInquiryRows() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (params: AutoPlaceRequest = {}) => autoPlaceOrderInquiryRows(params),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_ROWS_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_SUMMARY_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_SUMMARY_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_PO_CANDIDATES_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_UNPLACE_ALL_PREVIEW_KEY] });
      // W: the OI detail page's own "Auto link" / "Link selected" run through this same
      // mutation - the Related PO/SPO tabs and the Lines tab's own document chips move.
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_LINES_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_RELATED_DOCUMENTS_KEY] });
      toast.success(linkOutcomeText(result));
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

/**
 * "Unlink all" for the CURRENT worklist scope (the captain, 20-21 Aug): every linked or
 * partly linked row matching the filters passed in loses its links, ready for a clean
 * Auto-link re-deal. Named after its route, which the plan deliberately left unrenamed.
 */
export function useUnplaceAllOrderInquiryRows() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (params: UnplaceAllRequest = {}) => unplaceAllOrderInquiryRows(params),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_ROWS_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_SUMMARY_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_SUMMARY_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_PO_CANDIDATES_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_UNPLACE_ALL_PREVIEW_KEY] });
      toast.success(
        `${result.unplaced} row${result.unplaced === 1 ? '' : 's'} unlinked`,
      );
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

/**
 * The upload this page queued, watched to its end (AC-H13).
 *
 * The drawer's own feed is the watcher - one poll for every upload in the system, already
 * running - so this reads the session whose id is the job's rather than starting a second
 * one. `landed` is the moment the worker is done with it, whichever way it ended; only
 * then is the job asked what it wrote, because before then the answer is half a book.
 *
 * A job the feed has never heard of reads as still running: it was queued a moment ago and
 * the feed has not caught up, and offering to link against a book nobody has read yet is
 * the thing this gate exists to stop.
 */
export function useUploadedBook(jobId: string | null) {
  const { sessions } = useUploadActivity();
  const session = jobId
    ? sessions.find((s) => s.session_id === jobId || s.import_job_id === jobId)
    : undefined;
  const landed = Boolean(
    session && session.status !== 'uploading' && session.status !== 'processing',
  );

  const scope = useQuery({
    queryKey: [ORDER_INQUIRY_UPLOAD_JOB_KEY, jobId],
    queryFn: () => getOrderInquiryUploadJob(jobId as string),
    enabled: Boolean(jobId) && landed,
    // The job is terminal by now, so its answer cannot change; a refetch on every focus
    // would ask the same question again for the life of the alert.
    staleTime: Infinity,
    retry: false,
  });

  return { landed, failed: session?.status === 'failed', scope: scope.data ?? null };
}

/**
 * The handshake (`PLAN-scm-oi-handshake.md`): Acknowledge, Reject, Link now.
 *
 * All three invalidate the same families a link does, because all three MOVE links:
 * acknowledging runs the cascade for the rows it takes on, rejecting takes a row out of
 * netting (and its line back to the board), and Link now is the cascade itself.
 */
export function useOrderInquiryHandshake() {
  const queryClient = useQueryClient();

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_ROWS_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_SUMMARY_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_WORKLIST_SUMMARY_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_PO_CANDIDATES_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_UNPLACE_ALL_PREVIEW_KEY] });
    // A confirm/reject/unconfirm moves the HEADER between Outstanding and Completed too
    // (AC-CF-01/02): the detail page's own counts and the Documents list both read it.
    // Link now (the cascade) also moves the Related PO/SPO tabs, the same as Auto link.
    //
    // S6 (reviewer, fix round 22 Sep 2026): `refetchType: 'all'`, not the default
    // `'active'`, on the LIST's own key specifically - a Confirm pressed on the detail
    // page runs while the Documents list is UNMOUNTED (the reader navigated away from
    // it to get here), so the default only marks it stale; a plain browser Back then
    // showed the OLD Outstanding row until a manual refresh, because nothing forced the
    // now-inactive list query to refetch before that remount raced ahead of it. `'all'`
    // refetches it immediately, so the list's cache already holds the new Completed
    // status by the time the reader steps back onto it.
    queryClient.invalidateQueries({
      queryKey: [ORDER_INQUIRY_HEADERS_KEY],
      refetchType: 'all',
    });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_LINES_KEY] });
    queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADER_RELATED_DOCUMENTS_KEY] });
  }

  const acknowledge = useMutation({
    // `horizon` is the LINK HORIZON the cascade half of the press runs under (AC-LH1):
    // every ticked row is taken on, and one due after that date is left Not linked and
    // reported back as "N after <date>". `linkHorizonRequest` builds it, so this press and
    // the other three say the same thing about the same date (S1).
    //
    // `filter` is the worklist's own "Select all N matching" (PLAN-oi-confirm-per-so,
    // AC-CF-7): the CURRENT worklist scope rather than a client-built id list, for a
    // selection that spans more pages than were ever loaded. Mutually exclusive with
    // `rowIds` on the wire - the caller sends exactly one.
    mutationFn: ({
      rowIds,
      filter,
      horizon,
    }: {
      rowIds?: string[];
      filter?: OrderInquiryWorklistParams;
      horizon?: LinkHorizonRequest;
    }) =>
      filter
        ? acknowledgeOrderInquiryRowsByFilter(filter, horizon)
        : acknowledgeOrderInquiryRows(rowIds ?? [], horizon),
    onSuccess: (result) => {
      invalidate();
      toast.success(acknowledgeOutcomeText(result));
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const reject = useMutation({
    mutationFn: ({ rowId, reason }: { rowId: string; reason: string }) =>
      rejectOrderInquiryRow(rowId, reason),
    onSuccess: () => {
      invalidate();
      // The board is where the line went back to, so its own reads are stale now.
      queryClient.invalidateQueries({ queryKey: [PLANNING_BOARD_KEY] });
      toast.success('Rejected. The line is back with CS.');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  /**
   * The same refusal, for a batch, with ONE reason (item 15). Reject moved into the
   * Actions menu when the row actions column went, so the reason is asked for once and
   * carried onto every ticked row.
   */
  const rejectRows = useMutation({
    mutationFn: ({ rowIds, reason }: { rowIds: string[]; reason: string }) =>
      rejectOrderInquiryRows(rowIds, reason),
    onSuccess: (result) => {
      invalidate();
      queryClient.invalidateQueries({ queryKey: [PLANNING_BOARD_KEY] });
      const failed = (result.results ?? []).filter((entry) => !entry.ok).length;
      const rows = `${result.rejected} row${result.rejected === 1 ? '' : 's'} rejected`;
      if (failed > 0) toast.warning(`${rows}, ${failed} could not be`);
      else toast.success(`${rows}. The lines are back with CS.`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const linkNow = useMutation({
    mutationFn: (params: AutoPlaceRequest = {}) => linkNowOrderInquiryRows(params),
    onSuccess: (result) => {
      invalidate();
      toast.success(linkOutcomeText(result));
    },
    onError: (error: Error) => toast.error(error.message),
  });

  // PLAN-oi-worklist-split-customer-project.md, owner 18 Sep 2026: reversible (a plain Confirm undoes
  // it), so it invalidates the same families Confirm does and asks for no confirmation
  // dialog of its own.
  const unacknowledge = useMutation({
    mutationFn: (rowIds: string[]) => unacknowledgeOrderInquiryRows(rowIds),
    onSuccess: (result) => {
      invalidate();
      const rows = `${result.updated} row${result.updated === 1 ? '' : 's'}`;
      // N4 (review round 1): the same warn/success split `rejectRows` above uses for a
      // partial outcome - a batch that skipped something is not silently the same as
      // one that did not.
      if (result.skipped > 0) {
        toast.warning(`${rows} back to To confirm, ${result.skipped} skipped`);
      } else {
        toast.success(`${rows} back to To confirm`);
      }
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return { acknowledge, reject, rejectRows, linkNow, unacknowledge };
}

/** OI counts per reserve status and the caller's own parked items (AC-SN-31). */
export function useReserveStatusSummary(enabled = true) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_RESERVE_SUMMARY_KEY],
    queryFn: getReserveStatusSummary,
    enabled,
  });
}

/** Who the reserve confirmation's notice goes to (AC-SN-27); read while it is open. */
export function useReserveNoticeRecipients(
  inquiryId: string | undefined,
  enabled: boolean,
  rowIds?: string[],
) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_NOTICE_RECIPIENTS_KEY, inquiryId, rowIds ?? []],
    queryFn: () => getReserveNoticeRecipients(inquiryId as string, rowIds),
    enabled: enabled && Boolean(inquiryId),
    staleTime: 0,
  });
}

export type ParkedKind = 'requests' | 'notices';

/** The send-all dialog's preview, fetched fresh each time it opens (AC-SN-44). */
export function useParkedPreview(kind: ParkedKind | null) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_PARKED_PREVIEW_KEY, kind],
    queryFn: () =>
      kind === 'notices' ? previewParkedReserveNotices() : previewParkedReserveRequests(),
    enabled: kind !== null,
    gcTime: 0,
    staleTime: 0,
  });
}

/** Send all of the caller's parked requests / notices; toast + invalidate (AC-SN-44). */
export function useSendParked(kind: ParkedKind) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => (kind === 'notices' ? sendParkedReserveNotices() : sendParkedReserveRequests()),
    onSuccess: (result) => {
      if (result.emails_sent === 0) {
        toast.success('Nothing left to send: someone already sent these.');
      } else {
        toast.success(
          `${result.emails_sent} ${result.emails_sent === 1 ? 'email' : 'emails'} sent (${result.items_sent} order ${result.items_sent === 1 ? 'inquiry' : 'inquiries'})`,
        );
      }
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HEADERS_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_RESERVE_SUMMARY_KEY] });
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_RESERVE_REQUESTS_KEY] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

/** Send to purchasing: the preview for these order inquiries, loaded while `ids` is set. */
export function useHandoverResendPreview(ids: string[] | null, rowIds?: string[] | null) {
  return useQuery({
    queryKey: [ORDER_INQUIRY_HANDOVER_RESEND_PREVIEW_KEY, ids, rowIds ?? null],
    queryFn: () => previewHandoverResend(ids ?? [], rowIds ?? undefined),
    enabled: ids !== null && ids.length > 0,
    // Re-ticking a line keeps the last email on screen while the new one loads.
    placeholderData: (previous) => previous,
    gcTime: 0,
    staleTime: 0,
  });
}

/** Send to purchasing: one email per order inquiry; toast what went and what did not. */
export function useSendHandoverResend() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ids, rowIds }: { ids: string[]; rowIds?: string[] | null }) =>
      sendHandoverResend(ids, rowIds ?? undefined),
    onSuccess: (result) => {
      const failed = result.skipped.filter((skip) => skip.reason === 'failed');
      if (result.emails_sent > 0) {
        toast.success(
          `Sent to purchasing: ${result.emails_sent} ${result.emails_sent === 1 ? 'email' : 'emails'}`,
        );
      }
      if (failed.length > 0) {
        toast.error(
          `Not sent: ${failed.map((skip) => skip.inquiry_no ?? 'order inquiry').join(', ')}. Try again.`,
        );
      } else if (result.emails_sent === 0) {
        toast.error('Nothing was sent: no open lines or no purchasing recipient.');
      }
      queryClient.invalidateQueries({ queryKey: [ORDER_INQUIRY_HANDOVER_RESEND_PREVIEW_KEY] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}
