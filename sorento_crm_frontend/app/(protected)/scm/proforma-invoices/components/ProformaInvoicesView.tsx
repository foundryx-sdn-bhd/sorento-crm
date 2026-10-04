'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { toast } from '@/lib/toast';
import {
  Boxes,
  ChevronDown,
  Download,
  LoaderCircle,
  Settings,
  Trash2,
  Upload,
} from 'lucide-react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { useHasPermission } from '@/hooks/usePermissions';
import { useFulfilmentSuppliers } from '../../hooks/useFulfilment';
import {
  useBulkDeleteProformaInvoices,
  useConvertProformaInvoicesToDraftShipment,
  useProformaInvoices,
  useProformaInvoicesApplied,
} from '../../hooks/useProformaInvoices';
import type {
  ProformaInvoiceListRow,
  ProformaPlacement,
} from '../../services/proformaInvoiceService';
import { EM_DASH, fmtDate, fmtInt, fmtQty, fmtSupplierCost } from '../../lib/format';
import { ConvertToPackingListDialog } from './ConvertToPackingListDialog';
import { OverCapacityDialog } from './OverCapacityDialog';
// The shared "Upload supplier documents" dialog (R12, purchasing consolidation batch, lane
// C; its only remaining home since S3, AC-C2/C3 - Packing Lists has no upload of its own).
import { SupplierDocumentsUploadDialog } from './SupplierDocumentsUploadDialog';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

/**
 * What is on file per supplier: the priced document the loading plan and the eventual
 * PI-vs-PO check both read from.
 *
 * On the SAME toolbar every other listing in this product uses (`DataGridListToolbar`):
 * search on the left, the two filters behind one Filters popover, Columns, and the Upload
 * CTA anchored right. The supplier and packing-list selects used to be a hand-rolled row in
 * the card header, which is the one thing that toolbar exists to stop.
 *
 * The WHOLE ROW opens the invoice. The PI-number cell stays a real anchor so middle-click
 * and copy-link keep working, and stops its own click propagating. There is no per-row
 * Delete button: deleting is a bulk action on the selection, the same as the purchase-order
 * book, so the destructive control is not sitting under the cursor of somebody who meant to
 * open a row.
 *
 * The right cluster is [gear] [Start ▾] (R14). Start is what this screen is FOR: upload a
 * proforma invoice, or turn the ticked ones into a packing list. The gear beside it holds
 * the two things that act on a selection somebody has already made - Export and Delete -
 * so the destructive one is never the button nearest the cursor. The selection strip is
 * left with "N selected · Clear" and nothing else.
 *
 * Convert takes the whole selection, any suppliers - a container is routinely several
 * factories' PIs - and runs AT ONCE (R15): every ticked invoice places what it has left
 * into ONE NEW draft packing list, after `ConvertToPackingListDialog` asks which container
 * size it is going into (S5, ruling 1; the SAME dialog the PI detail page's per-invoice
 * convert uses, minus the per-line quantity question that only makes sense for one
 * document). The only interruption is the container being over its volume, which is a
 * question (`OverCapacityDialog`), not a failure. Placing PART of an invoice is a
 * deliberate act made on ONE document, so it lives on the PI detail page.
 */

const UPLOAD_PERMISSION = 'scm.proforma_invoice.upload';
const CONVERT_PERMISSION = 'scm.reorder.run';

/** Keyed off the read permission plus a stable id, never the record's own path - so the
 *  column choice survives the visit and cannot collide with another SCM listing. */
/** `proforma-invoices-20260828.xlsx`. A file called `export.xlsx` is unfindable an hour later,
 *  and the same stem the container-request documents use. */
function exportFilename(): string {
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('');
  return `proforma-invoices-${stamp}.xlsx`;
}

const LISTING_KEY = 'scm.dashboard.view::proforma-invoices';

/** The filter's own vocabulary, in the words the column uses. */
const PLACEMENT_FILTER: { value: string; label: string }[] = [
  { value: 'not_converted', label: 'Not converted' },
  { value: 'split', label: 'Split' },
  { value: 'converted', label: 'In a packing list' },
];

function placementLabel(placement: ProformaPlacement | null): string | null {
  return PLACEMENT_FILTER.find((o) => o.value === placement)?.label ?? null;
}

/** Can this invoice still go into a container? */
function selectableForConvert(row: ProformaInvoiceListRow): boolean {
  return row.status !== 'superseded' && row.placement !== 'converted';
}

/**
 * WHY it cannot, in a sentence naming the container - "In FSCU8103365" (AC-F7).
 *
 * The same string is the checkbox's tooltip and the reason the column shows, so the two
 * cannot drift into two explanations of one rule.
 */
function convertBlockedReason(row: ProformaInvoiceListRow): string | undefined {
  if (row.status === 'superseded') return 'Superseded by a newer revision';
  if (row.placement !== 'converted') return undefined;
  const names = row.packing_lists
    .map((p) => p.shipment_number)
    .filter(Boolean)
    .join(', ');
  return names ? `In ${names}` : 'Already in a packing list';
}

export function ProformaInvoicesView() {
  const router = useRouter();
  const suppliers = useFulfilmentSuppliers();
  const canUpload = useHasPermission(UPLOAD_PERMISSION);
  const canConvert = useHasPermission(CONVERT_PERMISSION);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  const [supplierId, setSupplierId] = useState<string | null>(null);
  // No default filter (AC-D1, S4): a fresh visit shows every invoice, newest first. The
  // Aug plan's default of `not_converted` (AC-F6) looked like missing data - a book where
  // everything was already placed read as empty rather than as "nothing left to place",
  // and the chip was silently applied before anyone chose it. `placement` is still honoured
  // from the URL, one click away in the filter popover.
  const [placement, setPlacement] = useState<ProformaPlacement | null>(null);
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    resetSearchQuery(state.searchQuery);
    setSupplierId(state.filters.supplier_id ?? null);
    setPlacement((state.filters.placement as ProformaPlacement) ?? null);
  });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [uploadOpen, setUploadOpen] = useState(false);
  // The shared dialog does not invalidate any list on its own (it does not know which list
  // is reading what it just wrote) - every caller wires its own, same as
  // `PackingListsList`'s `onImported` does for its own query key.
  const invalidateProformaLists = useProformaInvoicesApplied();
  const [bulkDeleteIds, setBulkDeleteIds] = useState<string[] | null>(null);
  const [convertOpen, setConvertOpen] = useState(false);
  const [overCapacity, setOverCapacity] = useState<string | null>(null);
  const [overrideReason, setOverrideReason] = useState('');
  // Held so an over-capacity refusal can be re-submitted with the reason WITHOUT asking
  // the operator to re-pick the container size they already chose.
  const [convertArgs, setConvertArgs] = useState<{ containerSizeId: string | null } | null>(
    null,
  );

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [searchQuery, supplierId, placement]);
  // A tick on a row the new filter excludes is not a selection any more.
  useEffect(() => {
    setRowSelection({});
  }, [searchQuery, supplierId, placement]);

  const { data, isLoading, isPlaceholderData, isFetching, error, refetch } = useProformaInvoices(supplierId, {
    limit: pagination.pageSize,
    offset: pagination.pageIndex * pagination.pageSize,
    placement,
    query: searchQuery,
  });
  const convertToDraftShipment = useConvertProformaInvoicesToDraftShipment();
  const bulkDeleteInvoices = useBulkDeleteProformaInvoices();

  const rows = useMemo<ProformaInvoiceListRow[]>(() => data?.data ?? [], [data]);

  // Carried into the detail URL so its prev/next pager walks the SAME filtered page the user
  // was reading (same param names as the list GET).
  const detailSearch = useMemo(
    () =>
      buildDetailSearch(
        { pageIndex: pagination.pageIndex, pageSize: pagination.pageSize, searchQuery },
        { supplier_id: supplierId || undefined, placement: placement || undefined },
      ),
    [pagination.pageIndex, pagination.pageSize, searchQuery, supplierId, placement],
  );

  const detailHref = (row: ProformaInvoiceListRow) =>
    `/scm/proforma-invoices/${row.id}${detailSearch ? `?${detailSearch}` : ''}`;

  const columns = useMemo<ColumnDef<ProformaInvoiceListRow>[]>(
    () => [
      buildSelectColumn<ProformaInvoiceListRow>({
        rowLabel: (row) => `Select ${row.original.pi_number}`,
        // A fully placed invoice, and a superseded revision, cannot be converted - so they
        // cannot be picked for it either, and the box says why rather than just greying
        // out (AC-F7).
        enableRow: (row) => selectableForConvert(row.original),
        disabledReason: (row) => convertBlockedReason(row.original),
      }),
      {
        accessorKey: 'pi_number',
        header: ({ column }) => <DataGridColumnHeader title="PI number" column={column} />,
        // A superseded revision says so HERE rather than only on its detail page: it is
        // still listed, still readable, and picking it for a convert is refused - so the
        // list has to explain the refusal before it happens (AC-E7).
        cell: ({ row }) => (
          <div className="flex flex-col gap-0.5">
            {/* The document number IS the way in, and the whole row opens it too. The
                anchor stays real so middle-click and copy-link still work, and stops its
                own click propagating. */}
            <Link
              href={detailHref(row.original)}
              onClick={(e) => e.stopPropagation()}
              className="truncate font-medium text-primary hover:underline"
              title={`Open ${row.original.pi_number}`}
            >
              {row.original.pi_number}
            </Link>
            {row.original.revision_count > 1 ? (
              <span className="text-xs text-muted-foreground">
                Revision {row.original.revision_no} of {row.original.revision_count}
                {row.original.status === 'superseded' ? ' - superseded' : ''}
              </span>
            ) : null}
          </div>
        ),
        size: 170,
        enableSorting: false,
        meta: { headerTitle: 'PI number' },
      },
      {
        accessorKey: 'supplier_ref',
        header: ({ column }) => <DataGridColumnHeader title="Supplier ref" column={column} />,
        // The supplier's own reference (S1, AC-A5) - null when the file states none, or
        // (Phase 1) until the backend sends it at all.
        cell: ({ row }) => {
          const ref = row.original.supplier_ref;
          if (!ref) return <span className="text-muted-foreground">{EM_DASH}</span>;
          return (
            <span className="block truncate" title={ref}>
              {ref}
            </span>
          );
        },
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Supplier ref' },
      },
      {
        id: 'supplier',
        header: ({ column }) => <DataGridColumnHeader title="Supplier" column={column} />,
        // The NAME, once. The normalised code under it said the same fact in a spelling
        // nobody uses out loud, and it is on the invoice's own page for whoever needs it.
        cell: ({ row }) => (
          <span className="truncate" title={row.original.supplier_name ?? undefined}>
            {row.original.supplier_name ?? EM_DASH}
          </span>
        ),
        size: 200,
        enableSorting: false,
        meta: { headerTitle: 'Supplier' },
      },
      {
        id: 'packing_list',
        header: ({ column }) => <DataGridColumnHeader title="Packing list" column={column} />,
        // Where this invoice's goods went. Read from the list rather than only from the
        // detail, because the only way to learn a PI was converted used to be to try
        // converting it again and be refused (AC-F6).
        cell: ({ row }) => {
          const invoice = row.original;
          if (invoice.status === 'superseded') {
            return <span className="text-muted-foreground">Superseded</span>;
          }
          if (invoice.placement === 'not_converted') {
            return <span className="text-muted-foreground">Not converted</span>;
          }
          return (
            <div className="flex flex-col gap-0.5">
              {invoice.packing_lists.map((pl) => (
                <Link
                  key={pl.shipment_id}
                  href={`/procurement-management/packing-lists/${pl.shipment_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="truncate text-primary hover:underline"
                  title={`Open ${pl.shipment_number ?? 'the packing list'}`}
                >
                  {pl.shipment_number ?? 'Draft'}
                  <span className="ms-1 text-xs text-muted-foreground">{fmtQty(pl.qty)}</span>
                </Link>
              ))}
              {invoice.placement === 'split' ? (
                <span className="text-xs text-muted-foreground">
                  Split - {fmtQty(invoice.remaining_qty)} still to place
                </span>
              ) : null}
            </div>
          );
        },
        size: 180,
        enableSorting: false,
        meta: { headerTitle: 'Packing list' },
      },
      {
        accessorKey: 'invoice_date',
        header: ({ column }) => <DataGridColumnHeader title="Invoice date" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{fmtDate(row.original.invoice_date)}</span>
        ),
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'Invoice date' },
      },
      {
        accessorKey: 'container_no',
        header: ({ column }) => <DataGridColumnHeader title="Container" column={column} />,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.container_no ?? undefined}>
            {row.original.container_no ?? EM_DASH}
          </span>
        ),
        size: 130,
        enableSorting: false,
        meta: { headerTitle: 'Container' },
      },
      {
        accessorKey: 'bl_no',
        header: ({ column }) => <DataGridColumnHeader title="BL" column={column} />,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.bl_no ?? undefined}>
            {row.original.bl_no ?? EM_DASH}
          </span>
        ),
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'BL' },
      },
      {
        accessorKey: 'currency',
        header: ({ column }) => <DataGridColumnHeader title="Currency" column={column} />,
        cell: ({ row }) => row.original.currency ?? EM_DASH,
        size: 90,
        enableSorting: false,
        meta: { headerTitle: 'Currency' },
      },
      {
        accessorKey: 'line_count',
        header: ({ column }) => <DataGridColumnHeader title="Lines" column={column} />,
        cell: ({ row }) => fmtInt(row.original.line_count),
        size: 80,
        enableSorting: false,
        meta: {
          headerTitle: 'Lines',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        accessorKey: 'total_amount',
        header: ({ column }) => <DataGridColumnHeader title="Total" column={column} />,
        cell: ({ row }) => fmtSupplierCost(row.original.total_amount, row.original.currency),
        size: 130,
        enableSorting: false,
        meta: {
          headerTitle: 'Total',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      // WHEN and WHO, one line each (ruling 27): stacked in one cell they read as a single
      // wrapped value, and neither could be sized, hidden or moved on its own.
      {
        id: 'uploaded_at',
        header: ({ column }) => <DataGridColumnHeader title="Uploaded at" column={column} />,
        cell: ({ row }) => (
          <span className="text-muted-foreground">{fmtDate(row.original.created_at)}</span>
        ),
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'Uploaded at' },
      },
      {
        id: 'uploaded_by',
        header: ({ column }) => <DataGridColumnHeader title="Uploaded by" column={column} />,
        cell: ({ row }) => (
          <span
            className="block truncate text-muted-foreground"
            title={row.original.uploaded_by ?? undefined}
          >
            {row.original.uploaded_by || EM_DASH}
          </span>
        ),
        size: 160,
        enableSorting: false,
        meta: { headerTitle: 'Uploaded by' },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [detailSearch],
  );

  const total = data?.total ?? 0;

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil(total / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, rowSelection },
    onPaginationChange: setPagination,
    onRowSelectionChange: setRowSelection,
    enableRowSelection: (row) => selectableForConvert(row.original),
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const selectedIds = table.getSelectedRowModel().rows.map((r) => r.original.id);
  const filtersActive = (supplierId ? 1 : 0) + (placement ? 1 : 0);

  const clearFilters = () => {
    setSupplierId(null);
    setPlacement(null);
  };

  // The filter STATED on screen, in the page's own words. A sticky default the user did not
  // set this session is otherwise indistinguishable from missing data.
  const activeSummary = useMemo(() => {
    const parts = [
      placementLabel(placement),
      supplierId
        ? (suppliers.data ?? []).find((s) => s.value === supplierId)?.label ?? 'One supplier'
        : null,
    ].filter(Boolean);
    return parts.length ? { label: parts.join(' - '), onClear: clearFilters } : undefined;
  }, [placement, supplierId, suppliers.data]);

  /**
   * The convert, once the container size is picked (R15, S5 ruling 1). No per-line
   * question stands between the tick and the container: every ticked invoice places what
   * it has LEFT, which is what the backend does anyway.
   */
  const runConvert = async (
    args: { containerSizeId: string | null } | null,
    reason?: string,
  ) => {
    if (!selectedIds.length) return;
    setConvertArgs(args);
    try {
      const result = await convertToDraftShipment.mutateAsync({
        invoiceIds: selectedIds,
        overrideReason: reason,
        containerSizeId: args?.containerSizeId ?? null,
      });
      setOverCapacity(null);
      setOverrideReason('');
      setConvertOpen(false);
      table.resetRowSelection();
      const skippedMsg =
        result.lines_skipped > 0
          ? ` (${result.lines_skipped} line${result.lines_skipped === 1 ? '' : 's'} could not be matched to a product and were skipped)`
          : '';
      // The selected invoices named different containers, so the draft's header was left
      // blank rather than given one of them (AC-D2c). Said here as well as on the detail
      // page: this is the surface where SEVERAL invoices are converted at once, so it is
      // where the disagreement actually happens.
      if (result.header_conflicts?.length) {
        toast.warning(
          `The invoices name different containers, so the container number, seal and bill of lading were left blank.`,
        );
      }
      // An invoice with nothing left to place is NAMED rather than quietly left out of the
      // count, so the operator can see which of their selection did not move (AC-F7).
      if (result.skipped_invoices?.length) {
        toast.warning(
          `Not converted: ${result.skipped_invoices
            .map((i) => `${i.pi_number} - ${i.reason}`)
            .join('; ')}`,
        );
      }
      // "Packing list", in the words the rest of the product uses for this document. The
      // container's own number is what the operator will look for next (R14).
      toast.success(
        `Packing list ${result.shipment_number ?? ''} created with ${result.lines_created} line${
          result.lines_created === 1 ? '' : 's'
        }${skippedMsg}`,
      );
      // The packing-list-to-SPO journey lives in the procurement packing-list book, over
      // this same `inbound_shipments` row - so the convert hand-off lands there, by id.
      router.push(`/procurement-management/packing-lists/${result.shipment_id}`);
    } catch (e) {
      // An over-capacity refusal is a question, not a failure: it names the volume and the
      // capacity and asks whether to load the box anyway (AC-E5).
      const code = (e as { code?: string | null })?.code ?? null;
      const message = e instanceof Error ? e.message : 'Failed to create the packing list';
      if (code === 'over_capacity') {
        setOverCapacity(message);
        return;
      }
      toast.error(message);
    }
  };

  const runBulkDelete = async () => {
    if (!bulkDeleteIds) return;
    try {
      const res = await bulkDeleteInvoices.mutateAsync(bulkDeleteIds);
      table.resetRowSelection();
      const deletedMsg = `Deleted ${res.deleted} proforma invoice${res.deleted === 1 ? '' : 's'}`;
      if (res.blocked.length > 0) {
        const names = res.blocked
          .map((b) => `${b.pi_number} (already converted to ${b.shipment_number ?? 'a shipment'})`)
          .join(', ');
        toast.error(`${deletedMsg} - could not delete: ${names}`);
      } else {
        toast.success(deletedMsg);
      }
      setBulkDeleteIds(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to delete proforma invoices');
    }
  };

  const hasSelection = selectedIds.length > 0;
  /** Why a menu item refuses, in the words the plan gave it (AC-E1 / AC-E2). */
  const NO_SELECTION = 'Select invoices first';

  // An empty book and an over-filtered one look identical in the grid, so they say different
  // things. The default filter IS a filter, so it counts - otherwise a book where everything
  // is already in a container would tell the operator nothing was ever uploaded.
  const emptyMessage = (
    <div className="flex flex-col items-start gap-2">
      <span>
        {searchQuery
          ? 'No proforma invoice matches this search and filter.'
          : placement === 'not_converted'
            ? 'No proforma invoice is waiting for a container.'
            : supplierId
              ? 'No proforma invoice on file for this supplier.'
              : 'No proforma invoice read yet. Upload the supplier’s proforma workbook to hold its priced lines.'}
      </span>
      {placement || supplierId ? (
        <Button variant="ghost" size="sm" onClick={clearFilters}>
          Show every invoice
        </Button>
      ) : null}
    </div>
  );

  return (
    <div className="space-y-3">
      <DataGrid
        table={table}
        recordCount={total}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        emptyMessage={emptyMessage}
        listingKey={LISTING_KEY}
        rowHref={(row) => detailHref(row)}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                  aria-label="Search proforma invoices"
                  placeholder="Search PI number, supplier ref, supplier, container or BL..."
                  className="w-72"
                />
              }
              filters={{
                kind: 'custom',
                active: filtersActive > 0,
                activeCount: filtersActive,
                activeSummary,
                content: (
                  <div className="space-y-4">
                    <div>
                      <Label htmlFor="proforma-supplier-filter" className="mb-1 block">
                        Supplier
                      </Label>
                      <SearchableSelect
                        id="proforma-supplier-filter"
                        value={supplierId ?? ''}
                        onChange={(v: string) => setSupplierId(v || null)}
                        options={suppliers.data ?? []}
                        placeholder="All suppliers"
                        clearable
                      />
                    </div>
                    <div>
                      <Label htmlFor="proforma-placement-filter" className="mb-1 block">
                        Packing list
                      </Label>
                      <SearchableSelect
                        id="proforma-placement-filter"
                        value={placement ?? ''}
                        onChange={(v: string) => setPlacement((v as ProformaPlacement) || null)}
                        options={PLACEMENT_FILTER}
                        placeholder="Any"
                        clearable
                      />
                    </div>
                    {filtersActive > 0 ? (
                      <div className="flex justify-end">
                        <Button variant="ghost" size="sm" onClick={clearFilters}>
                          Clear filters
                        </Button>
                      </div>
                    ) : null}
                  </div>
                ),
              }}
              // "N selected · Clear" and nothing else (AC-E2): both bulk actions moved
              // into the right cluster, where they sit in one place whether or not a row
              // is ticked.
              bulkActions={[]}
              // The toolbar renders no Export button of its own here - the gear owns it,
              // and `openExport` below opens the SAME selected-rows export dialog. The
              // config still travels, so the file is named for what is in it: with
              // `exportConfig={false}` every export downloaded as `export.xlsx`.
              showExport={false}
              exportConfig={{ filename: exportFilename() }}
              primaryAction={({ openExport }) => (
                <>
                  {/* Gear LEFT of the CTA, the same split-button shape the PI detail page
                      uses: the things done TO a selection, out of the way of the thing
                      this screen is for. */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" size="icon" aria-label="More actions">
                        <Settings className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        disabled={!hasSelection}
                        title={hasSelection ? undefined : NO_SELECTION}
                        onClick={hasSelection ? openExport : undefined}
                      >
                        <Download className="size-4" />
                        Export
                      </DropdownMenuItem>
                      {canUpload ? (
                        <DropdownMenuItem
                          className="text-destructive"
                          disabled={!hasSelection}
                          // A disabled item's reason travels as a native `title` - Radix
                          // leaves no room for a Tooltip wrapper inside a menu.
                          title={hasSelection ? undefined : NO_SELECTION}
                          onClick={hasSelection ? () => setBulkDeleteIds(selectedIds) : undefined}
                        >
                          <Trash2 className="size-4" />
                          {hasSelection ? `Delete ${fmtInt(selectedIds.length)}` : 'Delete'}
                        </DropdownMenuItem>
                      ) : null}
                    </DropdownMenuContent>
                  </DropdownMenu>

                  {canUpload || canConvert ? (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button>
                          Start
                          <ChevronDown className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {canUpload ? (
                          <DropdownMenuItem onClick={() => setUploadOpen(true)}>
                            <Upload className="size-4" />
                            Upload proforma invoice
                          </DropdownMenuItem>
                        ) : null}
                        {canConvert ? (
                          <DropdownMenuItem
                            disabled={!hasSelection}
                            title={hasSelection ? undefined : NO_SELECTION}
                            onClick={hasSelection ? () => setConvertOpen(true) : undefined}
                          >
                            <Boxes className="size-4" />
                            {hasSelection
                              ? `Convert ${fmtInt(selectedIds.length)} to packing list`
                              : 'Convert to packing list'}
                          </DropdownMenuItem>
                        ) : null}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : null}
                </>
              )}
            />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
          <CardFooter>
            {/* The list GET caps `limit` at 100 (`Query(25, ge=1, le=100)`), so the
                bigger sizes the grid offers by default would 422 the fetch AND put a
                page size in the detail URL that the pager cannot honour. Capping the
                choice is the one place both sides can agree on. */}
            <DataGridPagination sizes={[25, 50, 100]} />
          </CardFooter>
        </Card>
      </DataGrid>

      {/* No auto-close on import here: the dialog's own result summary ("Created N,
          updated M") would never paint if the parent closed it the instant the apply
          finished. The user dismisses it themselves once they have read the result. */}
      <SupplierDocumentsUploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onImported={invalidateProformaLists}
      />

      {/* How many cbm the WHOLE selection is, and which container it is going into (S5,
          ruling 1). No per-line quantity question here - that is the PI detail's own. */}
      <ConvertToPackingListDialog
        open={convertOpen}
        onOpenChange={setConvertOpen}
        invoiceIds={selectedIds}
        pending={convertToDraftShipment.isPending}
        onConvert={(args) => void runConvert({ containerSizeId: args.containerSizeId })}
      />

      <OverCapacityDialog
        message={overCapacity}
        reason={overrideReason}
        onReasonChange={setOverrideReason}
        onCancel={() => setOverCapacity(null)}
        onConfirm={() => void runConvert(convertArgs, overrideReason.trim())}
        pending={convertToDraftShipment.isPending}
      />

      {/* Bulk delete - AlertDialog + destructive button per ADR-PRODUCT-STANDARDS, same
          shape as the PO book's bulk delete. Reports which invoices were BLOCKED (already
          already in a packing list) rather than silently deleting only some. */}
      <AlertDialog open={!!bulkDeleteIds} onOpenChange={(o) => !o && setBulkDeleteIds(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm delete</AlertDialogTitle>
            <AlertDialogDescription>
              {bulkDeleteIds
                ? `Delete ${fmtInt(bulkDeleteIds.length)} proforma invoice${
                    bulkDeleteIds.length === 1 ? '' : 's'
                  }? This action cannot be undone.`
                : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button
              variant="outline"
              onClick={() => setBulkDeleteIds(null)}
              disabled={bulkDeleteInvoices.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={runBulkDelete}
              disabled={bulkDeleteInvoices.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {bulkDeleteInvoices.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : null}
              Delete
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default ProformaInvoicesView;
