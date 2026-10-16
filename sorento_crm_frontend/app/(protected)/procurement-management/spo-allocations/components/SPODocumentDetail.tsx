'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  ColumnDef,
  SortingState,
  VisibilityState,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { toast } from '@/lib/toast';
import {
  Columns3,
  FileText,
  Info,
  Link as LinkIcon,
  ListOrdered,
  LoaderCircleIcon,
  Pencil,
  RotateCcw,
  SquarePen,
  Trash2,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardFooter,
  CardHeader,
  CardHeading,
  CardTable,
  CardTitle,
  CardToolbar,
} from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridColumnVisibility } from '@/components/ui/data-grid-column-visibility';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Skeleton } from '@/components/ui/skeleton';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { formatStatusLabel } from '@/lib/status-badge';
import { parseDetailSearch } from '@/lib/listNavQuery';
import DetailActions from '@/components/common/DetailActions';
import { useBackToListHref } from '@/components/common/BackToList';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import {
  PlanRowDialog,
  SoCoveragePicker,
  type SoCoverageRow,
} from '@/app/(protected)/scm/components/PlanRowDialog';
import { WarehouseCombobox } from './WarehouseCombobox';
import { useSPODocument, spoDocumentsPagerQuery } from '../hooks/useSPODocuments';
import { useUpdateSPOAllocation, useDeleteSPOAllocation } from '../hooks/useSPOAllocations';
import {
  fmtEta,
  fmtQty,
  overdueClassName,
  planningSpanBadge,
  spoDocumentStatusPill,
} from '../lib/spoDocumentStatus';
import type { LinkedGRNRef, SPODocument, SPODocumentLine } from '../types/spoDocument.types';
import { getWarehouses } from '@/app/(protected)/inventory-management/warehouses/services/warehouseService';
import type { Warehouse } from '@/app/(protected)/inventory-management/warehouses/types/warehouse.types';
import { getProducts } from '@/app/(protected)/master-data-management/products/services/productService';
// Reused, not duplicated (UAT AC-24 parts 1/3): the same server-searched product and
// supplier pickers the packing-list Lines tab already uses per line, and the GRN form
// already reuses the product one across features.
import { ProductComboboxSearchable } from '../../packing-lists/components/ProductComboboxSearchable';
import { SupplierCombobox } from '../../packing-lists/components/SupplierCombobox';
import { distinctName } from '@/lib/product-display';

const DETAIL_PATH = '/procurement-management/spo-allocations';

function detailHref(spoNumber: string, search: string): string {
  return `${DETAIL_PATH}/${encodeURIComponent(spoNumber)}${search ? `?${search}` : ''}`;
}

type LineDraft = {
  product_id: string;
  warehouse_id: string;
  allocated_quantity: string;
  quantity_received: string;
  quantity_rejected: string;
  // ETA and supplier editors (UAT AC-24 parts 2/3) - the line's OWN fields, never the
  // shipment's own ETA/supplier, which stay read-only.
  expected_date: string;
  supplier_id: string;
};

function seedDraft(line: SPODocumentLine): LineDraft {
  return {
    product_id: line.product_id ?? '',
    warehouse_id: line.warehouse_id ?? '',
    allocated_quantity: String(line.allocated_quantity),
    quantity_received: String(line.quantity_received),
    quantity_rejected: String(line.quantity_rejected),
    expected_date: line.expected_date ?? '',
    supplier_id: line.supplier_id ?? '',
  };
}

// Picking number + status, in one tooltip - the icon carries no visible label of its
// own (captain's correction), so the detail lives in `title` instead.
function grnLinkTitle(grn: LinkedGRNRef): string | undefined {
  return [grn.picking_number, grn.picking_status].filter(Boolean).join(' - ') || undefined;
}

/** Does this line answer the search above the Lines grid? Product code/name or
 *  warehouse code/name (AC-S1.3) - the same shape the PO/SO detail line search uses. */
function lineMatches(line: SPODocumentLine, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    (line.product?.product_code ?? '').toLowerCase().includes(q) ||
    (line.product?.product_name ?? '').toLowerCase().includes(q) ||
    (line.warehouse?.warehouse_code ?? '').toLowerCase().includes(q) ||
    (line.warehouse?.warehouse_name ?? '').toLowerCase().includes(q)
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      {htmlFor ? (
        <label htmlFor={htmlFor} className="text-xs text-muted-foreground">
          {label}
        </label>
      ) : (
        <span className="text-xs text-muted-foreground">{label}</span>
      )}
      <span className="text-sm font-medium">{children}</span>
    </div>
  );
}

export function SPODocumentDetail({ spoNumber }: { spoNumber: string }) {
  const { data, isLoading, isError } = useSPODocument(spoNumber);
  const router = useRouter();
  const searchParams = useSearchParams();
  const filters = useMemo(() => parseDetailSearch(searchParams), [searchParams]);
  const filterProductId = filters.filters.product_id || null;
  const filterWarehouseId = filters.filters.warehouse_id || null;

  const doc = data ?? null;

  // The active Header/Lines tab (UAT AC-22): a local echo of `?tab=`, the same
  // "seed from the URL, resync on a genuinely new record" shape the edit-session
  // reset below already uses for `isEditing`/`lineDrafts` - a PURELY url-derived
  // value only updates once the mocked/real router actually re-renders this page,
  // which a step via the pager does (new url, same component instance) but a raw
  // click inside this render would not. `selectTab` still writes `?tab=lines` into
  // the url (via `router.replace`), which is what the pager's own href-builder
  // (`useListPager`'s `stepHref`) reads back and carries into the next/previous
  // record's link - `header` is the default and never appears in the query string.
  const [activeTab, setActiveTabState] = useState<'header' | 'lines'>(() =>
    searchParams.get('tab') === 'lines' ? 'lines' : 'header',
  );
  const selectTab = (tab: 'header' | 'lines') => {
    setActiveTabState(tab);
    const next = new URLSearchParams(searchParams.toString());
    if (tab === 'header') next.delete('tab');
    else next.set('tab', tab);
    router.replace(detailHref(spoNumber, next.toString()), { scroll: false });
  };

  const [isEditing, setIsEditing] = useState(false);
  const [lineDrafts, setLineDrafts] = useState<Record<string, LineDraft>>({});
  // Quantities are checked when Save is pressed; afterwards the messages follow the fields.
  const [qtyChecked, setQtyChecked] = useState(false);
  const [removedLineIds, setRemovedLineIds] = useState<Set<string>>(new Set());
  const [supplierExpanded, setSupplierExpanded] = useState(false);
  const [saving, setSaving] = useState(false);
  // The Received cell's multi-GRN lightbox (captain's ruling: a Dialog, not a
  // Popover, matching the apple-alignment "popups = lightbox" standard) - which
  // line's GRNs are showing, or null when closed.
  const [grnDialogReceipts, setGrnDialogReceipts] = useState<LinkedGRNRef[] | null>(null);
  // The SO covered lightbox (R23, AC-J2) - which line's `so_covered` list is open, or null.
  // Read-only: it lists what this allocation already covers, never ticks anything - the
  // SAME `so_coverage` kind the SPO planner opens (`PlanRowDialog`), so a reader never has
  // to learn two lightboxes for the one question.
  const [soCoverageLine, setSoCoverageLine] = useState<SPODocumentLine | null>(null);
  // The header linkage strip's own "SO covered" chip (R23, AC-J3) opens the SAME dialog,
  // over every line's `so_covered` combined rather than one line's.
  const [soCoverageAggregateOpen, setSoCoverageAggregateOpen] = useState(false);
  // Rejected and Overdue start hidden (UAT AC-23) - available through the Columns
  // toggle, same DataGridColumnVisibility control the Purchase Order form view uses
  // for its own line table.
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    rejected: false,
    overdue: false,
  });
  // Sorting and search over the lines already loaded (AC-S1.2/S1.3) - the same shape the
  // PO and SO detail line grids already use: no second request, no page boundary to work
  // across.
  const [sorting, setSorting] = useState<SortingState>([]);
  const {
    value: lineSearchInput,
    setValue: setLineSearchInput,
    debouncedValue: lineSearch,
  } = useDebouncedSearch();

  const updateLineMutation = useUpdateSPOAllocation();
  const deleteLineMutation = useDeleteSPOAllocation();

  const backHref = useBackToListHref(DETAIL_PATH);
  // Delete document, from the gear (UAT AC-26): the SAME `spo_document.delete`
  // pending action the list's bulk delete parks, one countdown, no confirm dialog
  // (D7) - `entityId` is the SPO number itself, spo_document's own registry key.
  const deletion = useDeferredAction({
    actionKey: 'spo_document.delete',
    entityType: 'spo_document',
    entityId: spoNumber,
    verb: 'Deleting',
    subject: spoNumber,
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'SPO document deleted',
    invalidateKeys: [['spo-allocations']],
    onCommitted: () => router.push(backHref),
  });

  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  useEffect(() => {
    getWarehouses({ pageIndex: 0, pageSize: 100, sorting: [], searchQuery: '', is_active: true }).then((res) =>
      setWarehouses(res.data ?? []),
    );
  }, []);

  // Server-searched, so any product in the catalogue is reachable from a line editor
  // (UAT AC-24 part 1), the same `getProducts` call the list's own product filter
  // makes - never a capped local list (standing rule).
  const fetchProducts = async (query: string, pageIndex: number) => {
    const res = await getProducts({
      pageIndex,
      pageSize: 50,
      sorting: [],
      searchQuery: query,
      status: 'active',
    });
    return { data: res.data ?? [] };
  };

  // Stepping to a neighbouring document (the pager, or Back then another row) must not
  // carry the previous document's edit session along with it. The active tab
  // RESYNCS from the new url instead of resetting outright (AC-22): the pager's own
  // link already carries `?tab=lines` forward when that is where the user was, so
  // reading it back here is what "the pager preserves the active tab" means - a row
  // clicked fresh off the list carries no `tab` param and lands back on Header.
  useEffect(() => {
    setIsEditing(false);
    setLineDrafts({});
    setRemovedLineIds(new Set());
    setSupplierExpanded(false);
    setActiveTabState(searchParams.get('tab') === 'lines' ? 'lines' : 'header');
    // Deliberately `[spoNumber]` only: `searchParams` changes together with
    // `spoNumber` on a real navigation (both come from the same new url), and
    // re-running this on every other `searchParams` change (typing in a list
    // filter elsewhere on the page) would wipe an in-progress edit for no reason.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spoNumber]);

  const beginEdit = (d: SPODocument) => {
    const drafts: Record<string, LineDraft> = {};
    for (const line of d.lines) drafts[line.id] = seedDraft(line);
    setLineDrafts(drafts);
    setRemovedLineIds(new Set());
    setQtyChecked(false);
    setIsEditing(true);
  };

  const cancelEdit = () => {
    setIsEditing(false);
    setLineDrafts({});
    setRemovedLineIds(new Set());
  };

  const toggleRemoveLine = (lineId: string) => {
    setRemovedLineIds((prev) => {
      const next = new Set(prev);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });
  };

  const qtyError = (raw: string) => numberFieldError(raw, { min: 0 });

  const handleSave = async () => {
    if (!doc) return;
    setQtyChecked(true);
    const quantityProblem = doc.lines.some((l) => {
      const draft = lineDrafts[l.id];
      return (
        !removedLineIds.has(l.id) &&
        draft &&
        (qtyError(draft.allocated_quantity) ||
          qtyError(draft.quantity_received) ||
          qtyError(draft.quantity_rejected))
      );
    });
    if (quantityProblem) return;
    setSaving(true);
    try {
      const toDelete = [...removedLineIds];
      // Only a line whose draft actually differs from what it was seeded with -
      // toggling one line's removal must not re-PUT every other line unchanged.
      const toUpdate = doc.lines
        .filter((l) => !removedLineIds.has(l.id) && lineDrafts[l.id])
        .filter((l) => {
          const draft = lineDrafts[l.id];
          const seed = seedDraft(l);
          return (
            draft.product_id !== seed.product_id ||
            draft.warehouse_id !== seed.warehouse_id ||
            draft.allocated_quantity !== seed.allocated_quantity ||
            draft.quantity_received !== seed.quantity_received ||
            draft.quantity_rejected !== seed.quantity_rejected ||
            draft.expected_date !== seed.expected_date ||
            draft.supplier_id !== seed.supplier_id
          );
        });

      const results = await Promise.allSettled([
        ...toUpdate.map((l) =>
          updateLineMutation.mutateAsync({
            id: l.id,
            data: {
              product_id: lineDrafts[l.id].product_id || undefined,
              warehouse_id: lineDrafts[l.id].warehouse_id || null,
              allocated_quantity: parseNumberDraft(lineDrafts[l.id].allocated_quantity) as number,
              quantity_received: parseNumberDraft(lineDrafts[l.id].quantity_received) as number,
              quantity_rejected: parseNumberDraft(lineDrafts[l.id].quantity_rejected) as number,
              expected_date: lineDrafts[l.id].expected_date || null,
              supplier_id: lineDrafts[l.id].supplier_id || null,
            },
          }),
        ),
        ...toDelete.map((id) => deleteLineMutation.mutateAsync(id)),
      ]);

      const changed = toUpdate.length + toDelete.length;
      const failed = results.filter((r) => r.status === 'rejected').length;
      if (changed === 0) {
        toast.success('No changes to save.');
      } else if (failed === 0) {
        const parts = [
          toUpdate.length ? `${toUpdate.length} line${toUpdate.length === 1 ? '' : 's'} updated` : null,
          toDelete.length ? `${toDelete.length} line${toDelete.length === 1 ? '' : 's'} removed` : null,
        ].filter(Boolean);
        toast.success(`Saved. ${parts.join(', ')}.`);
      } else if (failed === changed) {
        toast.error(`Nothing saved - ${failed} line change${failed === 1 ? '' : 's'} failed.`);
      } else {
        toast.error(`Saved ${changed - failed} of ${changed} line changes; ${failed} failed.`);
      }
      if (failed === 0) {
        setIsEditing(false);
        setLineDrafts({});
        setRemovedLineIds(new Set());
      }
    } finally {
      setSaving(false);
    }
  };

  const isMatchingLine = (line: SPODocumentLine): boolean => {
    if (!filterProductId && !filterWarehouseId) return false;
    return (
      (!filterProductId || line.product_id === filterProductId) &&
      (!filterWarehouseId || line.warehouse_id === filterWarehouseId)
    );
  };

  const visibleLines = useMemo(() => doc?.lines ?? [], [doc]);
  const matchingCount = useMemo(
    () => (filterProductId || filterWarehouseId ? visibleLines.filter(isMatchingLine).length : 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [visibleLines, filterProductId, filterWarehouseId],
  );

  const distinctSuppliers = useMemo(() => {
    const names = new Set<string>();
    for (const l of visibleLines) if (l.supplier_name) names.add(l.supplier_name);
    return [...names];
  }, [visibleLines]);

  /**
   * "Edit in planner" (R24, AC-K5): the same screen the Created SPOs grid opens, reached
   * from the document itself so both entry points meet at one editor.
   *
   * Rendered ONLY when this document's lines agree on one packing list AND one purchase
   * order - which is what a CRM SPO always is (one `create` run, one header per supplier,
   * one container). Anything else is an imported document, or a document split across
   * containers, and the planner has no single SPO to load for it; the action is hidden
   * rather than shown pointing at whichever half the query met first.
   */
  const editInPlannerHref = useMemo(() => {
    const shipmentIds = new Set(
      visibleLines.map((l) => l.inbound_shipment?.id).filter(Boolean) as string[],
    );
    const poIds = new Set(
      visibleLines.map((l) => l.po?.purchase_order_id).filter(Boolean) as string[],
    );
    if (shipmentIds.size !== 1 || poIds.size !== 1) return null;
    const [shipmentId] = [...shipmentIds];
    const [poId] = [...poIds];
    return `/procurement-management/packing-lists/${shipmentId}/spo?edit=${encodeURIComponent(poId)}`;
  }, [visibleLines]);

  const columns = useMemo<ColumnDef<SPODocumentLine>[]>(
    () => [
      {
        id: 'product',
        accessorFn: (l) => l.product?.product_code ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          const matches = isMatchingLine(line);
          const removed = removedLineIds.has(line.id);
          if (isEditing) {
            const draft = lineDrafts[line.id];
            return (
              <ProductComboboxSearchable
                value={draft?.product_id ?? ''}
                onChange={(v) =>
                  setLineDrafts((prev) => ({ ...prev, [line.id]: { ...seedDraft(line), ...prev[line.id], product_id: v } }))
                }
                fetchProducts={fetchProducts}
                productFallback={line.product}
                truncateTriggerLabel
              />
            );
          }
          // AC-S1.6: the sub-line renders only when the product name is non-empty AND
          // differs from the code (case-insensitive, trimmed) - same rule the PO form
          // view's own Product cell uses (`PurchaseOrderDetail.tsx`). Without this every
          // row printed its own code twice (e.g. "C-FHSS14" over "C-FHSS14").
          const showProductName =
            distinctName(line.product?.product_code, line.product?.product_name) !== null;
          return (
            <div className={cn('flex items-start gap-2', removed && 'opacity-50')}>
              {matches ? (
                <span
                  className="mt-1.5 size-1.5 shrink-0 rounded-full bg-primary"
                  aria-hidden
                  title="Matches your filter"
                />
              ) : null}
              <div className="flex min-w-0 flex-col">
                <span className={cn('truncate font-medium', matches && 'text-primary')}>
                  {line.product?.product_code ?? '-'}
                  {removed ? <span className="ms-1 text-xs font-normal text-muted-foreground">(removed)</span> : null}
                </span>
                {showProductName ? (
                  <span
                    className="truncate text-xs text-muted-foreground"
                    title={line.product?.product_name}
                  >
                    {line.product?.product_name}
                  </span>
                ) : null}
              </div>
            </div>
          );
        },
        size: 220,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'warehouse',
        accessorFn: (l) => l.warehouse?.warehouse_code ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Warehouse" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (isEditing) {
            const draft = lineDrafts[line.id];
            return (
              <WarehouseCombobox
                value={draft?.warehouse_id ?? ''}
                onChange={(v) =>
                  setLineDrafts((prev) => ({ ...prev, [line.id]: { ...seedDraft(line), ...prev[line.id], warehouse_id: v } }))
                }
                warehouses={warehouses}
                warehouseFallback={line.warehouse}
                placeholder="No warehouse"
                // NOT a fixed height (UAT AC-24 part 1 bug fix): `SearchableSelect`'s
                // trigger deliberately wraps a long label rather than truncating it
                // (`selectTriggerVariants`'s own doc comment) - forcing `h-8` here
                // clipped the second line instead of letting the control grow, which
                // is what read as the label rendering twice ("BRW-SYNT - BRW-S...").
                clearable
                truncateTriggerLabel
              />
            );
          }
          return line.warehouse ? line.warehouse.warehouse_code : <span className="text-muted-foreground">No location</span>;
        },
        size: 160,
        meta: { headerTitle: 'Warehouse' },
      },
      {
        id: 'allocated',
        accessorFn: (l) => l.allocated_quantity,
        header: ({ column }) => <DataGridColumnHeader title="Allocated" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (isEditing) {
            const draft = lineDrafts[line.id];
            return (
              <NumberInput
                min={0}
                aria-label={`Allocated qty on ${line.product?.product_code ?? line.id}`}
                value={draft?.allocated_quantity ?? String(line.allocated_quantity)}
                error={qtyChecked && draft ? qtyError(draft.allocated_quantity) : undefined}
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [line.id]: { ...seedDraft(line), ...prev[line.id], allocated_quantity: e.target.value },
                  }))
                }
                className="h-8 text-right tabular-nums"
              />
            );
          }
          return fmtQty(line.allocated_quantity);
        },
        size: 110,
        meta: { headerTitle: 'Allocated', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        id: 'received',
        accessorFn: (l) => l.quantity_received,
        header: ({ column }) => <DataGridColumnHeader title="Received" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (isEditing) {
            const draft = lineDrafts[line.id];
            return (
              <NumberInput
                min={0}
                aria-label={`Received qty on ${line.product?.product_code ?? line.id}`}
                value={draft?.quantity_received ?? String(line.quantity_received)}
                error={qtyChecked && draft ? qtyError(draft.quantity_received) : undefined}
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [line.id]: { ...seedDraft(line), ...prev[line.id], quantity_received: e.target.value },
                  }))
                }
                className="h-8 text-right tabular-nums"
              />
            );
          }
          // The GRN(s) behind the received figure (the retired page's linkage): the
          // picking lines naming THIS allocation where the matcher linked them, else
          // the document's key-matched receipts for a line that has received stock.
          const receipts =
            line.grns.length > 0
              ? line.grns
              : line.quantity_received > 0
                ? (doc?.linked_grns ?? [])
                : [];
          return (
            <div className="flex items-center justify-end gap-1.5">
              <span>{fmtQty(line.quantity_received)}</span>
              {receipts.length === 1 ? (
                // The Packing List column's exact grammar (icon beside the value,
                // title carries the detail) - one GRN links straight through.
                <Link
                  href={`/procurement-management/grn/${receipts[0].id}`}
                  className="text-primary hover:underline"
                  title={grnLinkTitle(receipts[0])}
                  onClick={(e) => e.stopPropagation()}
                >
                  <LinkIcon className="size-3 shrink-0" />
                </Link>
              ) : receipts.length > 1 ? (
                // Two or more GRNs matched this line: the same icon, but it opens
                // the lightbox listing every GRN as its own row (captain's ruling -
                // a Dialog, not a Popover, per the apple-alignment "popups = lightbox"
                // standard other SCM screens already follow).
                <button
                  type="button"
                  className="text-primary hover:underline"
                  title={`${receipts.length} goods receipts`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setGrnDialogReceipts(receipts);
                  }}
                >
                  <LinkIcon className="size-3 shrink-0" />
                </button>
              ) : null}
            </div>
          );
        },
        size: 130,
        meta: { headerTitle: 'Received', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        id: 'rejected',
        accessorFn: (l) => l.quantity_rejected,
        header: ({ column }) => <DataGridColumnHeader title="Rejected" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (isEditing) {
            const draft = lineDrafts[line.id];
            return (
              <NumberInput
                min={0}
                aria-label={`Rejected qty on ${line.product?.product_code ?? line.id}`}
                value={draft?.quantity_rejected ?? String(line.quantity_rejected)}
                error={qtyChecked && draft ? qtyError(draft.quantity_rejected) : undefined}
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [line.id]: { ...seedDraft(line), ...prev[line.id], quantity_rejected: e.target.value },
                  }))
                }
                className="h-8 text-right tabular-nums"
              />
            );
          }
          return fmtQty(line.quantity_rejected);
        },
        size: 100,
        meta: { headerTitle: 'Rejected', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        id: 'balance',
        accessorFn: (l) => l.balance,
        header: ({ column }) => <DataGridColumnHeader title="Balance" column={column} />,
        cell: ({ row }) => fmtQty(row.original.balance),
        size: 100,
        meta: { headerTitle: 'Balance', headerClassName: 'text-right', cellClassName: 'text-right tabular-nums' },
      },
      {
        id: 'eta',
        accessorFn: (l) => l.arrival_date ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="ETA"
            column={column}
            // The source, on the column itself (markup ruling, Q3/Q16): shipment
            // `eta_delay_date` -> shipment `estimated_arrival_date` -> SPO line
            // `expected_date`. Rendered AS IS - no TBA masking of a placeholder date.
            icon={
              <Tooltip>
                <TooltipTrigger asChild>
                  <Info className="size-3.5 text-muted-foreground" aria-label="Where this date comes from" />
                </TooltipTrigger>
                <TooltipContent>
                  Shipment revised ETA, else shipment estimate, else the SPO line&apos;s expected date.
                </TooltipContent>
              </Tooltip>
            }
          />
        ),
        cell: ({ row }) => {
          const line = row.original;
          if (isEditing) {
            const draft = lineDrafts[line.id];
            return (
              <Input
                type="date"
                aria-label={`ETA on ${line.product?.product_code ?? line.id}`}
                value={draft?.expected_date ?? (line.expected_date ?? '')}
                onChange={(e) =>
                  setLineDrafts((prev) => ({
                    ...prev,
                    [line.id]: { ...seedDraft(line), ...prev[line.id], expected_date: e.target.value },
                  }))
                }
                className="h-8"
              />
            );
          }
          return fmtEta(line.arrival_date);
        },
        size: 150,
        meta: { headerTitle: 'ETA' },
      },
      {
        id: 'supplier',
        accessorFn: (l) => l.supplier_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (isEditing) {
            const draft = lineDrafts[line.id];
            return (
              <SupplierCombobox
                value={draft?.supplier_id ?? ''}
                onChange={(v) =>
                  setLineDrafts((prev) => ({ ...prev, [line.id]: { ...seedDraft(line), ...prev[line.id], supplier_id: v } }))
                }
                // Best-effort label for a value not yet in the search results: the
                // line's own `supplier_id` may differ from the DISPLAYED
                // `supplier_name` when a shipment is booked (that field prefers the
                // shipment's supplier) - opening the picker and searching resolves
                // the exact name.
                supplierFallback={
                  line.supplier_id
                    ? { id: line.supplier_id, supplier_code: '', supplier_name: line.supplier_name ?? '' }
                    : null
                }
                placeholder="No supplier"
                clearable
                truncateTriggerLabel
              />
            );
          }
          return line.supplier_name || <span className="text-muted-foreground">-</span>;
        },
        size: 170,
        meta: { headerTitle: 'Supplier' },
      },
      {
        id: 'overdue',
        accessorFn: (l) => l.overdue_days,
        header: ({ column }) => <DataGridColumnHeader title="Overdue" column={column} />,
        cell: ({ row }) => {
          const days = row.original.overdue_days;
          return <span className={overdueClassName(days)}>{days > 0 ? `${days}d` : '-'}</span>;
        },
        size: 100,
        meta: { headerTitle: 'Overdue', headerClassName: 'text-right', cellClassName: 'text-right' },
      },
      {
        id: 'plan',
        accessorFn: (l) => l.planning_span ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Plan" column={column} />,
        cell: ({ row }) => {
          const pill = planningSpanBadge(row.original.planning_span);
          return (
            <Badge variant={pill.variant} appearance="light" size="sm">
              {pill.label}
            </Badge>
          );
        },
        size: 110,
        meta: { headerTitle: 'Plan' },
      },
      {
        id: 'packing_list',
        accessorFn: (l) => l.inbound_shipment?.shipment_number ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Packing List" column={column} />,
        cell: ({ row }) => {
          const ship = row.original.inbound_shipment;
          if (!ship) return <span className="text-muted-foreground">-</span>;
          return (
            <Link
              href={`/procurement-management/packing-lists/${ship.id}`}
              className="flex items-center gap-1 text-primary hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              <LinkIcon className="size-3 shrink-0" />
              <span className="truncate">
                {ship.shipment_number}
                {ship.shipping_container_number ? ` (${ship.shipping_container_number})` : ''}
              </span>
            </Link>
          );
        },
        size: 170,
        meta: { headerTitle: 'Packing List' },
      },
      {
        // R23, AC-J2: which PURCHASE order line this SPO line pulled from - the SPO's own
        // "supply" side, not to be confused with the Packing List column above (the SHIPMENT
        // this allocation landed off).
        id: 'po',
        header: ({ column }) => <DataGridColumnHeader title="PO" column={column} />,
        accessorFn: (l) => l.po?.po_number ?? '',
        cell: ({ row }) => {
          const po = row.original.po;
          if (!po) return <span className="text-muted-foreground">-</span>;
          return (
            <Link
              href={`/scm/purchase-orders/${po.purchase_order_id}`}
              className="flex items-center gap-1 text-primary hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              <LinkIcon className="size-3 shrink-0" />
              <span className="truncate">{po.po_number}</span>
            </Link>
          );
        },
        size: 140,
        meta: { headerTitle: 'PO' },
      },
      {
        // The AutoCount book's OWN source purchase-order number, straight off the ingest -
        // never resolved into a CRM row, so it is text, not a link. Distinct from `po`
        // above (a real `purchase_order_lines` id this SPO pulled from, which stays a
        // link): the two can disagree without either being wrong, because they answer
        // different questions (`PLAN-scm-book-linkage-on-document-lines.md` Slice B).
        id: 'from_po_number',
        header: ({ column }) => <DataGridColumnHeader title="PO (book)" column={column} />,
        accessorFn: (l) => l.from_po_number ?? '',
        cell: ({ row }) => {
          const value = row.original.from_po_number;
          if (!value) return <span className="text-muted-foreground">-</span>;
          return (
            <span className="block truncate" title={value}>
              {value}
            </span>
          );
        },
        size: 140,
        meta: { headerTitle: 'PO (book)' },
      },
      {
        // R23, AC-J2: which sales orders this line's allocation already covers - opens the
        // SAME read-only lightbox the planner's own "SO covered" cell does.
        id: 'so_covered',
        header: ({ column }) => <DataGridColumnHeader title="SO covered" column={column} />,
        accessorFn: (l) => l.so_covered?.length ?? 0,
        cell: ({ row }) => {
          const covered = row.original.so_covered ?? [];
          if (!covered.length) return <span className="text-muted-foreground">-</span>;
          const qty = covered.reduce((sum, c) => sum + c.qty, 0);
          return (
            <button
              type="button"
              className="text-primary hover:underline"
              onClick={(e) => {
                e.stopPropagation();
                setSoCoverageLine(row.original);
              }}
            >
              {`${covered.length} SO${covered.length === 1 ? '' : 's'} · ${fmtQty(qty)}`}
            </button>
          );
        },
        size: 140,
        meta: { headerTitle: 'SO covered' },
      },
      {
        id: 'status',
        accessorFn: (l) => l.receipt_status ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => (
          <Badge status={row.original.receipt_status} size="sm">
            {formatStatusLabel(row.original.receipt_status)}
          </Badge>
        ),
        size: 120,
        meta: { headerTitle: 'Status' },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => {
          if (!isEditing) return null;
          const line = row.original;
          const removed = removedLineIds.has(line.id);
          return (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className={cn('gap-1.5', removed ? '' : 'text-destructive hover:text-destructive')}
              onClick={() => toggleRemoveLine(line.id)}
            >
              {removed ? <RotateCcw className="size-4" /> : <Trash2 className="size-4" />}
              {removed ? 'Undo' : 'Remove'}
            </Button>
          );
        },
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Actions' },
      },
    ],
    // `isEditing` / `lineDrafts` / `removedLineIds` / `warehouses` drive the editable
    // cells; `filterProductId` / `filterWarehouseId` drive the highlight dot; `doc`
    // feeds the Received cell's document-level GRN fallback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isEditing, qtyChecked, lineDrafts, removedLineIds, warehouses, filterProductId, filterWarehouseId, doc],
  );

  const table = useReactTable({
    columns,
    data: visibleLines,
    getRowId: (row) => row.id,
    state: { columnVisibility, sorting, globalFilter: lineSearch },
    onColumnVisibilityChange: setColumnVisibility,
    onSortingChange: setSorting,
    // Nothing calls table.setGlobalFilter directly - the box drives lineSearch
    // (debounced) instead - but a controlled globalFilter still requires this.
    onGlobalFilterChange: () => {},
    getColumnCanGlobalFilter: () => true,
    globalFilterFn: (row, _columnId, value) => lineMatches(row.original, String(value ?? '')),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });
  const visibleLineCount = table.getFilteredRowModel().rows.length;

  // Back lives on the PAGE-level header row now (UAT AC-21, page.tsx), the same spot
  // the Purchase Order form view puts it - not here any more.

  if (isLoading && !doc) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  if (isError || !doc) {
    return (
      <div className="space-y-4">
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <div className="text-sm font-semibold">SPO document not found</div>
          <p className="max-w-md text-sm text-muted-foreground">
            This SPO number doesn&apos;t exist, or every allocation on it was removed. Head
            back to the list to pick another.
          </p>
        </Card>
      </div>
    );
  }

  const statusPill = spoDocumentStatusPill(doc.status);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="block py-4">
          {/* The number, the status badge, Edit and the pager read as ONE title row
              (review nit / AC-5); Back moved to the page-level header (UAT AC-21). */}
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              <CardTitle className="text-lg">{doc.spo_number}</CardTitle>
              <Badge variant={statusPill.variant} appearance="light" size="md">
                {statusPill.label}
              </Badge>
            </div>
            {isEditing ? (
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" onClick={cancelEdit} disabled={saving}>
                  Cancel
                </Button>
                <Button size="sm" onClick={handleSave} disabled={saving}>
                  {saving ? <LoaderCircleIcon className="me-2 size-4 animate-spin" /> : null}
                  Save
                </Button>
              </div>
            ) : (
              <DetailActions
                pager={{
                  ...spoDocumentsPagerQuery,
                  detailPath: DETAIL_PATH,
                  currentId: doc.spo_number,
                  hrefFor: (id, search) => detailHref(id, search),
                  ariaLabel: 'SPO document',
                }}
                gearLabel="SPO document options"
                // Delete document, from the gear (UAT AC-26) - the same deferred,
                // no-confirm-dialog pattern (D7) every other destructive action uses;
                // the countdown takes the Edit button's spot while it runs.
                actions={[
                  {
                    key: 'spo_document.delete',
                    label: 'Delete document',
                    icon: Trash2,
                    kind: 'destructive',
                    disabled: deletion.isPending,
                    run: () => deletion.start(),
                  },
                ]}
                pendingAction={deletion.countdown}
                primary={
                  <div className="flex items-center gap-2">
                    {/* R24, AC-K5 - beside Edit, because both are "change this document",
                        one field by field and one through the planner that made it. */}
                    {editInPlannerHref ? (
                      <Button variant="outline" size="sm" className="gap-1.5" asChild>
                        <Link href={editInPlannerHref}>
                          <Pencil className="size-4" />
                          Edit in planner
                        </Link>
                      </Button>
                    ) : null}
                    <Button variant="primary" size="sm" className="gap-1.5" onClick={() => beginEdit(doc)}>
                      <SquarePen className="size-4" />
                      Edit
                    </Button>
                  </div>
                }
              />
            )}
          </div>
        </CardHeader>
      </Card>

      <Tabs value={activeTab} onValueChange={(v) => selectTab(v as 'header' | 'lines')} className="w-full">
        <TabsList variant="line" className="mb-4 w-full justify-start">
          <TabsTrigger value="header">
            <FileText />
            <span>Header</span>
          </TabsTrigger>
          <TabsTrigger value="lines">
            <ListOrdered />
            <span>Lines</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="header" className="mt-0 space-y-4 focus-visible:outline-none">
          {/* R23, AC-J3: PO / SPO lines / SO covered / Packing list / GRN, in one row - each
              a link when the document has one, muted text when it does not. Absent in
              Phase 1 until the backend computes `linkage` (Phase 2). */}
          {doc.linkage ? (
            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
              <span>{`PO ${fmtQty(doc.linkage.po_count)}`}</span>
              <span aria-hidden>·</span>
              <span>{`SPO lines ${fmtQty(doc.linkage.line_count)}`}</span>
              <span aria-hidden>·</span>
              {doc.linkage.so_count > 0 ? (
                <button
                  type="button"
                  className="text-primary hover:underline"
                  onClick={() => setSoCoverageAggregateOpen(true)}
                >
                  {`SO covered ${fmtQty(doc.linkage.so_count)} · ${fmtQty(doc.linkage.so_qty)}`}
                </button>
              ) : (
                <span>{`SO covered ${fmtQty(doc.linkage.so_count)}`}</span>
              )}
              <span aria-hidden>·</span>
              {doc.linkage.packing_list ? (
                <Link
                  href={`/procurement-management/packing-lists/${doc.linkage.packing_list.id}`}
                  className="flex items-center gap-1 text-primary hover:underline"
                >
                  <LinkIcon className="size-3 shrink-0" />
                  {`Packing list ${doc.linkage.packing_list.container ?? ''}`}
                </Link>
              ) : (
                <span>Packing list -</span>
              )}
              <span aria-hidden>·</span>
              {doc.linked_grns.length === 1 ? (
                <Link
                  href={`/procurement-management/grn/${doc.linked_grns[0].id}`}
                  className="flex items-center gap-1 text-primary hover:underline"
                >
                  <LinkIcon className="size-3 shrink-0" />
                  GRN 1
                </Link>
              ) : (
                <span>{`GRN ${fmtQty(doc.linkage.grn_count)}`}</span>
              )}
            </div>
          ) : null}
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Document</CardTitle>
              </CardHeading>
            </CardHeader>
            <section aria-label="Document" className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              <Field label="Supplier">
                <span className="flex flex-wrap items-center gap-1.5">
                  {doc.supplier_name ?? '-'}
                  {doc.supplier_extra_count > 0 ? (
                    <button
                      type="button"
                      onClick={() => setSupplierExpanded((v) => !v)}
                      className="text-xs font-normal text-primary hover:underline"
                    >
                      +{doc.supplier_extra_count} more
                    </button>
                  ) : null}
                </span>
                {supplierExpanded && distinctSuppliers.length > 1 ? (
                  <ul className="mt-1 list-disc space-y-0.5 ps-4 text-xs font-normal text-muted-foreground">
                    {distinctSuppliers.map((name) => (
                      <li key={name}>{name}</li>
                    ))}
                  </ul>
                ) : null}
              </Field>
              <Field label="Doc date">{fmtEta(doc.doc_date)}</Field>
              <Field label="Allocated">{fmtQty(doc.total_allocated)}</Field>
              <Field label="Received">{fmtQty(doc.total_received)}</Field>
              <Field label="Balance">{fmtQty(doc.balance)}</Field>
              <Field label="Line count">{fmtQty(doc.line_count)}</Field>
              <Field label="Goods receipts">
                {doc.linked_grns.length === 0 ? (
                  <span className="text-muted-foreground">None received yet</span>
                ) : (
                  <ul className="space-y-0.5">
                    {doc.linked_grns.map((grn) => (
                      <li key={grn.id} className="flex items-center gap-2">
                        <Link
                          href={`/procurement-management/grn/${grn.id}`}
                          className="text-primary hover:underline"
                        >
                          {grn.picking_number || 'GRN'}
                        </Link>
                        {grn.picking_date ? (
                          <span className="text-xs font-normal text-muted-foreground">
                            {fmtEta(grn.picking_date)}
                          </span>
                        ) : null}
                        {grn.picking_status ? (
                          <Badge variant="outline" size="sm" className="font-normal">
                            {grn.picking_status}
                          </Badge>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </Field>
            </section>
          </Card>
        </TabsContent>

        <TabsContent value="lines" className="mt-0 space-y-3 focus-visible:outline-none">
          {matchingCount > 0 ? (
            <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm">
              {matchingCount} line{matchingCount === 1 ? '' : 's'} below match your product/warehouse
              filter from the list - marked with a dot.
            </div>
          ) : null}
          <DataGrid
            table={table}
            recordCount={visibleLineCount}
            isLoading={false}
            tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
            emptyMessage={lineSearch.trim() ? 'No lines match' : 'This SPO document has no lines.'}
            // A real key, not the pathname fallback (review S2): every document's URL is
            // different, so a per-pathname key would fragment one reader's column prefs
            // across every SPO number they ever open instead of sharing ONE preference.
            listingKey="procurement.spo_allocations.view::spo-document-lines"
          >
            <Card>
              <CardHeader className="flex-wrap gap-3">
                <CardHeading>
                  <CardTitle>Lines</CardTitle>
                </CardHeading>
                {/* Rejected and Overdue start hidden (UAT AC-23) - reachable here,
                    the same Columns control the Purchase Order form view's line
                    table already uses. */}
                <CardToolbar className="flex-wrap">
                  <ListSearchInput
                    value={lineSearchInput}
                    onChange={setLineSearchInput}
                    aria-label="Search lines"
                    placeholder="Search product or warehouse"
                    className="w-64"
                  />
                  <DataGridColumnVisibility
                    table={table}
                    trigger={
                      <Button variant="outline" size="sm" className="gap-1.5">
                        <Columns3 className="size-4" />
                        Columns
                      </Button>
                    }
                  />
                </CardToolbar>
              </CardHeader>
              <CardTable>
                <DataGridTable />
              </CardTable>
              <CardFooter>
                <DataGridPagination />
              </CardFooter>
            </Card>
          </DataGrid>
        </TabsContent>
      </Tabs>

      {/* The Received cell's multi-GRN lightbox (captain's ruling) - the same
          picking number / date / status shape the Header tab's own "Goods
          receipts" field already renders, just for one line's GRNs. */}
      <Dialog
        open={grnDialogReceipts !== null}
        onOpenChange={(next) => !next && setGrnDialogReceipts(null)}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Goods receipts for {doc.spo_number}</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <ul className="space-y-1.5">
              {(grnDialogReceipts ?? []).map((grn) => (
                <li key={grn.id} className="flex flex-wrap items-center gap-2">
                  <Link
                    href={`/procurement-management/grn/${grn.id}`}
                    className="flex items-center gap-1 text-primary hover:underline"
                    onClick={() => setGrnDialogReceipts(null)}
                  >
                    <LinkIcon className="size-3 shrink-0" />
                    {grn.picking_number || 'GRN'}
                  </Link>
                  {grn.picking_date ? (
                    <span className="text-xs font-normal text-muted-foreground">
                      {fmtEta(grn.picking_date)}
                    </span>
                  ) : null}
                  {grn.picking_status ? (
                    <Badge variant="outline" size="sm" className="font-normal">
                      {grn.picking_status}
                    </Badge>
                  ) : null}
                </li>
              ))}
            </ul>
          </DialogBody>
        </DialogContent>
      </Dialog>

      {/* SO covered (R23, AC-J2/AC-J3) - read-only: what an allocation already covers, never
          a tick list. Same `so_coverage` kind and shell the SPO planner opens; the shape
          here is the document's own flatter list (document/customer/class/qty), lifted
          into `SoCoverageRow` with the fields this reader has no equivalent for left null.
          One line's own cell opens it scoped to that line; the header strip's chip opens
          the SAME dialog over every line's list combined. */}
      <PlanRowDialog
        kind="so_coverage"
        productCode={soCoverageLine?.product?.product_code ?? (soCoverageAggregateOpen ? doc.spo_number : '')}
        productName={soCoverageLine?.product?.product_name}
        open={soCoverageLine !== null || soCoverageAggregateOpen}
        onOpenChange={(next) => {
          if (!next) {
            setSoCoverageLine(null);
            setSoCoverageAggregateOpen(false);
          }
        }}
      >
        {soCoverageLine || soCoverageAggregateOpen ? (
          <SoCoveragePicker
            readOnly
            coverage={(
              soCoverageLine ? (soCoverageLine.so_covered ?? []) : doc.lines.flatMap((l) => l.so_covered ?? [])
            ).map(
              (c, i): SoCoverageRow => ({
                key: `${soCoverageLine?.id ?? 'doc'}-${i}`,
                kind: c.demand_class,
                demand_class: c.demand_class,
                document: c.document,
                customer_name: c.customer,
                required_date: null,
                qty: c.qty,
                warehouse_code: null,
                taken_qty: 0,
                taken_by: [],
              }),
            )}
            tickedKeys={[]}
            onChange={() => {}}
            unassigned={0}
          />
        ) : null}
      </PlanRowDialog>
    </div>
  );
}

export default SPODocumentDetail;
