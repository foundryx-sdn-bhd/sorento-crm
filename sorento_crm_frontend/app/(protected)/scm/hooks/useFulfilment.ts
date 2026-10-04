'use client';

import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  buildContainerRequest,
  createLoadingPlanRecord,
  createSpo,
  deleteSpo,
  downloadContainerRequestDocument,
  downloadSpoWorksheet,
  getConsolidatedPackingList,
  getContainerRequestHistory,
  getContainerSizes,
  getFulfilmentSuppliers,
  getLoadingPlanList,
  getShipmentLinePhotos,
  getSpoPlannerState,
  getSpoSuggestion,
  getSupplierChatContacts,
  getSupplierNotices,
  getSupplierStock,
  getSupplierStockListFile,
  previewContainerRequest,
  reviseSpo,
  saveLoadingPlanEdits,
  sendContainerRequest,
  updateLoadingPlanCutOff,
  uploadShipmentLinePhotos,
  type ContainerRequestLine,
  type ContainerRequestSendOptions,
  type LoadingPlanCreate,
  type LoadingPlanLineEdit,
  type LoadingPlanListParams,
  type LoadingPlanWindow,
  type SpoConfirmLine,
} from '../services/fulfilmentService';
import type { ListPagerParams, ListPagerPage } from '@/hooks/useListPager';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

const KEY = ['scm', 'fulfilment'] as const;

const cold = { staleTime: 5 * 60_000, retry: 1 } as const;

export function useFulfilmentSuppliers() {
  // Wrapped (not passed bare): `getFulfilmentSuppliers` now takes an optional server-search
  // `query` (S8-followup) - react-query would otherwise call it with its own
  // QueryFunctionContext in that slot.
  return useQuery({
    queryKey: [...KEY, 'suppliers'],
    queryFn: () => getFulfilmentSuppliers(),
    ...cold,
  });
}

export function useContainerSizes({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: [...KEY, 'container-sizes'],
    queryFn: getContainerSizes,
    ...cold,
    enabled,
  });
}

export function useSupplierStock(supplierId: string | null) {
  return useQuery({
    queryKey: [...KEY, 'stock', supplierId],
    queryFn: () => getSupplierStock(supplierId as string),
    enabled: !!supplierId,
  });
}

/**
 * The plans list (`/scm/loading-plan`, R3). Server-paged, server-sorted and server-searched,
 * so the grid never holds more than the page it shows.
 */
/**
 * The plans list's React Query key. The plan page's pager rebuilds the SAME key
 * from the URL, so it reads the page the list already fetched.
 */
export function loadingPlanListQueryKey(params: LoadingPlanListParams): QueryKey {
  return [...KEY, 'plan-list', params];
}

/** The list query a plan URL describes, in the shape the list passes. */
export function loadingPlanListParamsFromUrl(
  params: ListPagerParams,
): LoadingPlanListParams {
  return {
    pageIndex: params.pageIndex,
    pageSize: params.pageSize,
    sorting: params.sorting,
    searchQuery: params.searchQuery,
    // `active` (planning + sent) is the chip the list opens on.
    status: (params.filters.status as LoadingPlanListParams['status']) ?? 'active',
  };
}

/** The pager's two hooks into the loading plans list. */
export const loadingPlanPagerQuery = {
  listQueryKey: (params: ListPagerParams): QueryKey =>
    loadingPlanListQueryKey(loadingPlanListParamsFromUrl(params)),
  fetchPage: (params: ListPagerParams): Promise<ListPagerPage> =>
    getLoadingPlanList(loadingPlanListParamsFromUrl(params)),
};

export function useLoadingPlanList(params: LoadingPlanListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: loadingPlanListQueryKey(params),
    queryFn: () => getLoadingPlanList(params),
  });
}

/** The stored copy of the supplier's own sheet - for the "View uploaded list" control. */
export function useSupplierStockListFile(supplierId: string | null) {
  return useQuery({
    queryKey: [...KEY, 'stock-list-file', supplierId],
    queryFn: () => getSupplierStockListFile(supplierId as string),
    enabled: !!supplierId,
  });
}

/** Invalidate everything keyed on one supplier: a new snapshot changes every plan under it. */
function useSupplierInvalidator() {
  const qc = useQueryClient();
  return (supplierId: string | null) => {
    void qc.invalidateQueries({ queryKey: [...KEY, 'stock', supplierId] });
    void qc.invalidateQueries({ queryKey: [...KEY, 'stock-list-file', supplierId] });
    void qc.invalidateQueries({ queryKey: [...KEY, 'plan-list'] });
  };
}

export function useStockListApplied() {
  return useSupplierInvalidator();
}

/** Start a plan (R4). No toast: the caller navigates straight onto the record. */
export function useCreateLoadingPlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LoadingPlanCreate) => createLoadingPlanRecord(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [...KEY, 'plan-list'] }),
    onError: (e: Error) => toast.error(e.message),
  });
}

/**
 * Save the typed quantities (R6). The whole map goes in one PUT, and the build is invalidated
 * rather than patched: `suggested_qty` comes back with the edits already applied, so the grid
 * and the document read the same numbers.
 */
export function useUpdateLoadingPlanCutOff(planId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (window: LoadingPlanWindow) =>
      updateLoadingPlanCutOff(planId as string, window),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [...KEY, 'container-request', planId] });
      void qc.invalidateQueries({ queryKey: [...KEY, 'plan-list'] });
      toast.success('Cut-off changed. The suggestion has been worked out again.');
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useSaveLoadingPlanEdits(planId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (edits: Record<string, LoadingPlanLineEdit>) =>
      saveLoadingPlanEdits(planId as string, edits),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [...KEY, 'container-request', planId] });
      void qc.invalidateQueries({ queryKey: [...KEY, 'plan-list'] });
    },
    onError: (e: Error) => toast.error(e.message),
  });
}


/**
 * Stage 1 - the container request (PLAN-scm-loading-plan-demand-first.md). A pure read, so a
 * query the supplier picker drives directly - the same auto-fetch-on-select shape as
 * `useSupplierStock` / `useLoadingPlans` - rather than a mutation Ms Tee has to fire herself;
 * "Refresh suggestion" is this query's own `refetch`.
 *
 * `planHorizonDate` ("Plan until", captain 20 Aug) is keyed into the query so picking a
 * different cutoff is a fresh fetch, not a stale one served out of cache under the same key.
 */
export function useContainerRequestBuild(planId: string | null) {
  return useQuery({
    queryKey: [...KEY, 'container-request', planId],
    queryFn: () => buildContainerRequest(planId as string),
    enabled: !!planId,
    retry: false,
  });
}

/**
 * The exact document a Send would produce (R9, AC-E2) - what the Preview page state shows,
 * in place of Phase 1's client-side `mockSupplierSheet` (deleted with this change; both
 * returned the same `SupplierSheetModel`, so nothing else about `LoadingPlanView` moves).
 *
 * `enabled` is the caller's own `docPreviewOpen`: this is a page state Ms Tee opens
 * deliberately, not a background fetch every record page pays for. `lines` is expected
 * DEBOUNCED by the caller - typing in the preview's own qty/remark inputs changes `lines` on
 * every keystroke, and firing a request per character would be wasteful; `placeholderData`
 * keeps the last document on screen while a new one is asked for, rather than blanking the
 * table between keystrokes.
 */
export function useContainerRequestPreview(
  planId: string | null,
  lines: ContainerRequestLine[],
  enabled: boolean,
) {
  return useQuery({
    // `planId` sits where every other container-request key puts it (right after
    // `'container-request'`), NOT after `'preview'` (review round 1, S5): save and send both
    // invalidate `[...KEY, 'container-request', planId]`, which is a PREFIX match, so a
    // `'preview'` segment ahead of `planId` put this query outside that prefix and a save
    // never invalidated the previously-fetched preview.
    queryKey: [...KEY, 'container-request', planId, 'preview', JSON.stringify(lines)],
    queryFn: () => previewContainerRequest(planId as string, lines),
    enabled: enabled && !!planId,
    placeholderData: (prev) => prev,
    retry: false,
  });
}

/**
 * The sales history behind the rows currently ON SCREEN (AC-B8).
 *
 * Keyed on the product ids, so paging to the next 25 rows is a new query rather than a
 * refetch of everything: the sidecar exists precisely so a 120-product supplier does not pay
 * for 120 products' worth of monthly series to read one page.
 *
 * `cold` because a month bucket cannot change while she is looking at it - this is closed
 * history, not the live sales book.
 */
export function useContainerRequestHistory(supplierId: string | null, productIds: string[]) {
  // Sorted so two pages holding the same products in a different order share one cache entry.
  const key = [...productIds].sort().join(',');
  return useQuery({
    queryKey: [...KEY, 'container-request', 'history', supplierId, key],
    queryFn: () => getContainerRequestHistory(supplierId as string, productIds),
    enabled: !!supplierId && productIds.length > 0,
    ...cold,
  });
}

/**
 * Notices sent to this supplier, either stage - the caller filters by `notice_type`.
 *
 * `loadingPlanId` narrows it to one plan (R3/R11), which is what a plan's record page wants:
 * a supplier commonly has two plans open at once and each holds its own current ask, so the
 * other one's sends and its live link do not belong on this page.
 */
export function useSupplierNotices(supplierId: string | null, loadingPlanId?: string | null) {
  return useQuery({
    queryKey: [...KEY, 'notices', 'supplier', supplierId, loadingPlanId ?? null],
    queryFn: () => getSupplierNotices(supplierId as string, loadingPlanId),
    enabled: !!supplierId,
  });
}

/**
 * Who a chat send could be addressed to, searched on the server (AC-C3).
 *
 * The answer also carries the WORKSPACE's own fact - whether a WeChat channel is connected at
 * all - which is what labels and disables the Chat option, so the send dialog asks on OPEN
 * rather than once Chat is chosen: a choice cannot be disabled with its reason after it has
 * been made. It is a read of our own mirror (`respond_channels` / `respond_contacts`), never
 * a live Respond.io call, and `enabled` still keeps it off any screen that is not asking.
 */
export function useSupplierChatContacts(
  supplierId: string | null,
  query: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: [...KEY, 'chat-contacts', supplierId, query],
    queryFn: () => getSupplierChatContacts(supplierId as string, query),
    enabled: enabled && !!supplierId,
  });
}

export function useSendContainerRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      planId,
      lines,
      options,
    }: {
      planId: string;
      supplierId: string;
      supplierName: string;
      lines: ContainerRequestLine[];
      options?: ContainerRequestSendOptions;
    }) => sendContainerRequest(planId, lines, options),
    onSuccess: (_out, { planId, supplierId, supplierName }) => {
      void qc.invalidateQueries({ queryKey: [...KEY, 'notices', 'supplier', supplierId] });
      void qc.invalidateQueries({ queryKey: [...KEY, 'container-request', planId] });
      void qc.invalidateQueries({ queryKey: [...KEY, 'plan-list'] });
      toast.success(`Request sent to ${supplierName}.`);
    },
    // A refused send still wrote its notice, and the record's "Requests sent" list has to
    // show the attempt and its reason - so the notices are re-read on the failing path too.
    onError: (_e, { supplierId }) => {
      void qc.invalidateQueries({ queryKey: [...KEY, 'notices', 'supplier', supplierId] });
    },
    // No toast: the send dialog stays open on a refusal and prints the reason beside the
    // field that can fix it (AC-C5). A toast would say the same thing where it cannot be
    // acted on, and would vanish while she is still reading it.
  });
}

/**
 * The gear menu's two downloads (R23): the same request, as a file, without sending it.
 *
 * A mutation rather than a query because it is an act she asks for and because the pending
 * state disables the menu item - there is nothing to cache, the answer is a file that has
 * already left for the disk.
 */
export function useDownloadContainerRequestDocument(planId: string | null) {
  return useMutation({
    mutationFn: ({
      lines,
      format,
    }: {
      lines: ContainerRequestLine[];
      format: 'xlsx' | 'pdf';
    }) => downloadContainerRequestDocument(planId as string, lines, format),
    onError: (e: Error) => toast.error(e.message),
  });
}

/** S10 - the consolidated packing list for one container, grouped by factory. */
export function useConsolidatedPackingList(shipmentId: string | null) {
  return useQuery({
    queryKey: [...KEY, 'packing-list', shipmentId],
    queryFn: () => getConsolidatedPackingList(shipmentId as string),
    enabled: !!shipmentId,
  });
}

/**
 * The SPO planner table (`PLAN-scm-proforma-to-spo.md`'s second amendment) - hoisted here so
 * the packing-list detail page's planner tab (`procurement-management/packing-lists/[id]`)
 * and any future caller share one cache key and one mutation shape, rather than each rolling
 * its own inline `useQuery`/`useMutation` the way the first cut's `CreateSpoPanel` did.
 */
export function useSpoSuggestion(shipmentId: string | null) {
  return useQuery({
    queryKey: [...KEY, 'spo-suggestion', shipmentId],
    queryFn: () => getSpoSuggestion(shipmentId as string),
    enabled: !!shipmentId,
  });
}

export function useCreateSpo(shipmentId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (lines: SpoConfirmLine[]) => createSpo(shipmentId as string, lines),
    onSuccess: (out) => {
      void qc.invalidateQueries({ queryKey: [...KEY, 'spo-suggestion', shipmentId] });
      // A new purchase order exists now - the PO list/detail must see it too.
      void qc.invalidateQueries({ queryKey: ['scm', 'purchase-orders'] });
      const names = out.created_spos.map((s) => s.po_number).filter(Boolean).join(', ');
      const allocated = out.allocations.length;
      const allocatedMsg = allocated
        ? ` ${allocated} location${allocated === 1 ? '' : 's'} allocated.`
        : '';
      toast.success(
        out.created_spos.length
          ? `Created ${out.created_spos.length === 1 ? 'SPO' : `${out.created_spos.length} SPOs`}: ${names}.${allocatedMsg}`
          : 'Nothing was created - every line was already covered.',
      );
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

/** Delete action on a row of the Created SPOs grid (R1) - unwinds ONE SPO
 *  (`purchaseOrderId`) or, absent, every SPO the shipment ever made. On success, the
 *  planner's own suggestion query is invalidated so it re-reads the remainder, and the PO
 *  list/detail queries are invalidated since a purchase order just disappeared. */
export function useDeleteSpo(shipmentId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (purchaseOrderId?: string) => deleteSpo(shipmentId as string, purchaseOrderId),
    onSuccess: (out) => {
      void qc.invalidateQueries({ queryKey: [...KEY, 'spo-suggestion', shipmentId] });
      void qc.invalidateQueries({ queryKey: ['scm', 'purchase-orders'] });
      const names = out.deleted_po_numbers.join(', ');
      toast.success(
        out.deleted_spo_count === 1
          ? `Deleted SPO ${names}.`
          : `Deleted ${out.deleted_spo_count} SPOs: ${names}.`,
      );
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

/** The planner in EDIT mode (R24, AC-K1): one SPO's own lines, each carrying both the
 *  suggestion shape and the state it was persisted with. `purchaseOrderId` null means the
 *  planner is in create mode and nothing is fetched. */
export function useSpoPlannerState(shipmentId: string | null, purchaseOrderId: string | null) {
  return useQuery({
    queryKey: [...KEY, 'spo-planner-state', shipmentId, purchaseOrderId],
    queryFn: () => getSpoPlannerState(shipmentId as string, purchaseOrderId as string),
    enabled: !!shipmentId && !!purchaseOrderId,
  });
}

/** Save changes on an SPO already created (R24, AC-K2). Invalidates the same queries
 *  `useCreateSpo` does - the remainder planner and the PO book both moved - plus the
 *  planner state itself, so a second edit of the same SPO re-reads what was just written
 *  rather than the pre-save snapshot. */
export function useReviseSpo(shipmentId: string | null, purchaseOrderId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (lines: SpoConfirmLine[]) =>
      reviseSpo(shipmentId as string, purchaseOrderId as string, lines),
    onSuccess: (out) => {
      void qc.invalidateQueries({ queryKey: [...KEY, 'spo-suggestion', shipmentId] });
      void qc.invalidateQueries({ queryKey: [...KEY, 'spo-planner-state', shipmentId] });
      void qc.invalidateQueries({ queryKey: ['scm', 'purchase-orders'] });
      void qc.invalidateQueries({ queryKey: ['spo-allocations'] });
      const removed = out.removed_line_count
        ? ` ${out.removed_line_count} line${out.removed_line_count === 1 ? '' : 's'} removed.`
        : '';
      toast.success(`Saved ${out.po_number ?? 'the SPO'}.${removed}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export function useDownloadSpoWorksheet(shipmentId: string | null) {
  return useMutation({
    mutationFn: (fallbackName?: string | null) =>
      downloadSpoWorksheet(shipmentId as string, fallbackName),
    onError: (e: Error) => toast.error(e.message),
  });
}

/** Supplier photos on a shipment line (R25, lane C, slice C3) - every line's photos in
 *  one read, keyed by line id, so the Lines tab fetches once rather than once per row. */
export function useShipmentLinePhotos(shipmentId: string | null) {
  return useQuery({
    queryKey: [...KEY, 'line-photos', shipmentId],
    queryFn: () => getShipmentLinePhotos(shipmentId as string),
    enabled: !!shipmentId,
  });
}

/**
 * Upload photos onto a shipment line (R25, lane C, slice C3, review round 1 item 7) -
 * moved out of `ShipmentLinePhotosCell` so the cell reads/writes through the same
 * hook layer every other feature does (`PRINCIPLES.md` layering).
 *
 * Invalidates in `onSettled`, not `onSuccess`: a partial batch (review round 1 item
 * 4 - the backend purges the failed file but keeps whatever landed before it) still
 * changed the line's photo list even though the call itself rejects, so the strip
 * has to refetch on the error path too, not only on success.
 */
export function useUploadShipmentLinePhotos(shipmentId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ lineId, files }: { lineId: string; files: File[] }) =>
      uploadShipmentLinePhotos(shipmentId as string, lineId, files),
    onSuccess: (_photos, variables) => {
      toast.success(
        variables.files.length === 1 ? 'Photo added' : `${variables.files.length} photos added`,
      );
    },
    onError: (e: Error) => toast.error(e.message),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: [...KEY, 'line-photos', shipmentId] });
    },
  });
}
