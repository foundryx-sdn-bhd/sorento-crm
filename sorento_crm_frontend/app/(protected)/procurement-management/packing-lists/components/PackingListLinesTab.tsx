'use client';

import { useMemo, useRef } from 'react';
import Link from 'next/link';
import { ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Info, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTable, CardTitle, CardToolbar } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { formatDate } from '@/lib/helpers';
import { formatStatusLabel } from '@/lib/status-badge';
import { spoDetailHref } from '@/lib/spo-detail';
import { useConsolidatedPackingList, useShipmentLinePhotos } from '@/app/(protected)/scm/hooks/useFulfilment';
import { getProducts } from '@/app/(protected)/master-data-management/products/services/productService';
import { ProductComboboxSearchable } from './ProductComboboxSearchable';
import { SupplierCombobox } from './SupplierCombobox';
import { ShipmentLinePhotosCell } from './ShipmentLinePhotosCell';
import { deriveLineCells, fmtDp, fmtStated, toNum } from './packingListLineMath';
import {
  usePackingListRecord,
  type DraftLine,
} from '../[id]/components/packing-list-context';
import type { InboundShipmentLine } from '../types/packingList.types';

/** Keyed off the read permission plus a stable id, matching the DataGrid convention for a
 *  detail-page line grid (`scm.dashboard.view::proforma-invoice-lines` is the same shape). */
const LISTING_KEY = 'procurement.packing_lists.view::lines';

const EM_DASH = '-';
const numMeta = { headerClassName: 'text-end', cellClassName: 'text-end tabular-nums' };

type SourceLink = { proforma_invoice_id: string; pi_number: string; qty: number };

/** One shared empty map, so a container with no invoice links keeps a stable reference. */
const NO_INVOICES_BY_LINE: Record<string, SourceLink[]> = {};

/**
 * One grid row, in EITHER mode - the draft while editing, the stored line otherwise.
 *
 * Same shape both ways (the PI detail's own Lines tab sets this precedent): one column set
 * reads either, deciding Input vs text off `editing` rather than off which array the row
 * came from. The read-only, container-derived fields (brand, SPO/GRN links, status) are
 * blank on a draft row - they are what the container DID, not what somebody is typing.
 */
interface GridLine extends DraftLine {
  brand: string | null;
  sourceLinks: SourceLink[];
  spoAllocated: number | null;
  received: number | null;
  status: string | null;
  relatedSpoAllocations: InboundShipmentLine['related_spo_allocations'];
  relatedGrns: InboundShipmentLine['related_grns'];
}

function toStr(value: unknown): string {
  return value === null || value === undefined ? '' : String(value);
}

/** A stored line, as the grid reads it outside editing. Brand comes off the CONSOLIDATED
 *  packing list (`build()`'s own per-line brand) - the packing-list record's product
 *  reference carries no brand, so Logo has nowhere else to read it from. */
function toGridLine(line: InboundShipmentLine, brand: string | null, sourceLinks: SourceLink[]): GridLine {
  return {
    key: line.id,
    id: line.id,
    product_id: line.product_id,
    product_code: line.product?.product_code ?? '',
    product_name: line.product?.product_name ?? null,
    description: toStr(line.description),
    quantity_shipped: toStr(line.quantity_shipped ?? 0),
    supplier_id: line.supplier_id ?? '',
    cartons_count: toStr(line.cartons_count),
    cbm: toStr(line.cbm),
    material: toStr(line.material),
    pcs_per_carton: toStr(line.pcs_per_carton),
    carton_length_cm: toStr(line.carton_length_cm),
    carton_width_cm: toStr(line.carton_width_cm),
    carton_height_cm: toStr(line.carton_height_cm),
    net_weight_per_carton: toStr(line.net_weight_per_carton),
    // The old single weight, read as the gross one where the split column is blank - most
    // containers only ever hold the one weight. Display only; editing the true field is
    // unchanged (`packing-list-context.tsx`'s own `beginEdit` does not apply this fallback).
    gross_weight_per_carton: toStr(line.gross_weight_per_carton ?? line.weight_per_carton),
    uom_id: line.uom_id ?? null,
    currency: line.currency ?? null,
    unit_cost: toStr(line.unit_cost),
    remarks: toStr(line.remarks),
    brand,
    sourceLinks,
    spoAllocated: line.spo_allocated_quantity ?? null,
    received: line.quantity_received ?? null,
    status: line.line_status ?? null,
    relatedSpoAllocations: line.related_spo_allocations,
    relatedGrns: line.related_grns,
  };
}

function draftToGridLine(line: DraftLine): GridLine {
  return {
    ...line,
    brand: null,
    sourceLinks: [],
    spoAllocated: null,
    received: null,
    status: null,
    relatedSpoAllocations: undefined,
    relatedGrns: undefined,
  };
}

/** Line-level status from quantity shipped, allocated, and received. */
function getLineStatus(quantityShipped: number, allocated: number, received: number): string {
  const qty = quantityShipped ?? 0;
  const alloc = allocated ?? 0;
  const recv = received ?? 0;
  if (alloc === 0) return 'in_transit';
  if (recv >= alloc) return 'received';
  if (qty > alloc) return 'partially_allocated';
  if (alloc >= qty && recv === 0) return 'allocated';
  if (alloc >= qty && recv > 0) return 'partially_received';
  return 'in_transit';
}

/** A numeric cell in the editor. One component so every measurement column looks the same. */
function LineNumberInput({
  line,
  name,
  label,
  step,
  onChange,
}: {
  line: GridLine;
  name: keyof DraftLine;
  label: string;
  step?: string;
  onChange: (key: string, name: keyof DraftLine, value: string) => void;
}) {
  return (
    <Input
      type="number"
      min={0}
      step={step}
      className="h-8 w-20 text-end tabular-nums"
      value={(line[name] as string) ?? ''}
      onChange={(e) => onChange(line.key, name, e.target.value)}
      aria-label={`${label} for ${line.product_code || 'the new line'}`}
    />
  );
}

/**
 * What is in the container, laid out column-for-column like the RMB sheet (AC-G1), with the
 * six cells nobody types derived off it the same way the export derives them (AC-G2).
 *
 * The measurement columns (material, pcs per carton, the carton's L / W / H, NW, GW, price,
 * remarks) are editable here because the supplier's own file is where they come from and it
 * is not always right - and because everything the container workbook derives is derived
 * from them. Draft until Save, like every other field on this record.
 */
export function PackingListLinesTab() {
  const {
    packingList,
    editing,
    draftLines,
    setLineField,
    addLine,
    removeLine,
    suppliers,
    supplierNameById,
    sourceInvoices,
    canReadScm,
  } = usePackingListRecord();

  const packingListId = packingList?.id ?? null;
  // Both are SCM reads, which 403 without SCM read: not asked for at all then, so the tab
  // still works and no permission toast lands on it (PL-TABS-ACCESS).
  const scmShipmentId = canReadScm ? packingListId : null;
  const consolidated = useConsolidatedPackingList(scmShipmentId);
  // R25 (lane C, slice C3): every line's photos in one read, not one per row.
  const linePhotos = useShipmentLinePhotos(scmShipmentId);

  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
  } = useDebouncedSearch();

  // Memoised, never a fresh `{}` per render: without SCM read `sourceInvoices` is undefined,
  // and a new object here rebuilt `viewRows` on every render, which re-rendered the grid in
  // an endless loop (the page pegged a core, and no tab click could ever commit: the
  // packing-list "hang", PL-TABS-ACCESS).
  const invoicesByLine = useMemo(
    () => sourceInvoices?.by_shipment_line ?? NO_INVOICES_BY_LINE,
    [sourceInvoices],
  );

  /** Brand per line, off the SAME `build()` JSON the Split card and the download both read -
   *  the only place a shipment line's brand is available on the wire (AC-G6: what she sees
   *  is what Download writes). */
  const brandByLineId = useMemo(() => {
    const map = new Map<string, string | null>();
    for (const factory of consolidated.data?.factories ?? []) {
      for (const line of factory.lines) map.set(line.line_id, line.brand);
    }
    return map;
  }, [consolidated.data]);

  const lineSupplierName = (supplierId?: string | null): string | null =>
    (supplierId ? supplierNameById.get(supplierId) : null) ?? null;

  /** View rows: sorted Factory then No (AC-G3, ruling 4), filtered by the search box. Edit
   *  rows keep insertion order - a mid-edit resort would move the row somebody is typing
   *  into out from under the cursor, so `draftLines` renders exactly as it is held. */
  const viewRows = useMemo<GridLine[]>(() => {
    const lines = packingList?.shipment_lines ?? [];
    const q = search.trim().toLowerCase();
    const filtered = lines.filter((line) => {
      if (!q) return true;
      const code = line.product?.product_code?.toLowerCase() ?? '';
      const name = line.product?.product_name?.toLowerCase() ?? '';
      return code.includes(q) || name.includes(q);
    });
    const sorted = [...filtered].sort((a, b) => {
      // No factory sorts last, same convention `build()` uses for its own factory list.
      if (!!a.supplier_id !== !!b.supplier_id) return a.supplier_id ? -1 : 1;
      const factoryA = (lineSupplierName(a.supplier_id) ?? '').toUpperCase();
      const factoryB = (lineSupplierName(b.supplier_id) ?? '').toUpperCase();
      if (factoryA !== factoryB) return factoryA.localeCompare(factoryB);
      return (a.product?.product_code ?? '').localeCompare(b.product?.product_code ?? '');
    });
    return sorted.map((line) =>
      toGridLine(line, brandByLineId.get(line.id) ?? null, invoicesByLine[line.id] ?? []),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [packingList?.shipment_lines, search, brandByLineId, invoicesByLine, supplierNameById]);

  const editRows = useMemo<GridLine[]>(() => draftLines.map(draftToGridLine), [draftLines]);

  const rows = editing ? editRows : viewRows;

  /** What the rows ON SCREEN add up to - the visible rows, deliberately (AC-G3): a footer
   *  under a searched grid that quietly totals the whole container reads as the search
   *  having found more than it did. While editing, the totals follow the DRAFT. */
  const footerTotals = useMemo(() => {
    let qty = 0;
    let ctnQty = 0;
    let totalCbm = 0;
    let unmeasuredCbm = 0;
    let totalNw = 0;
    let totalGw = 0;
    let amount = 0;
    for (const line of rows) {
      const cells = deriveLineCells(line);
      qty += Number(line.quantity_shipped || 0);
      ctnQty += cells.ctnQty ?? 0;
      if (cells.totalCbm === null) unmeasuredCbm += 1;
      else totalCbm += cells.totalCbm;
      totalNw += cells.totalNw ?? 0;
      totalGw += cells.totalGw ?? 0;
      amount += cells.amount ?? 0;
    }
    return { qty, ctnQty, totalCbm, unmeasuredCbm, totalNw, totalGw, amount };
  }, [rows]);

  /** Read by the footer cells INSTEAD of `footerTotals` directly (AC-J1): `footerTotals` is a
   *  fresh object on every keystroke (it is derived off `rows`, which is derived off
   *  `draftLines`), and the `columns` memo below used to list it as a dependency - rebuilding
   *  every column, with brand-new cell renderer functions, on every character typed. React
   *  treats a changed renderer as a changed component type, so it unmounted and remounted
   *  every `<Input>` on the grid each time, which is what dropped focus after one keystroke.
   *  A ref lets the footer read the CURRENT totals without `columns` needing to change. */
  const footerTotalsRef = useRef(footerTotals);
  footerTotalsRef.current = footerTotals;

  /** Read by the Photos cell INSTEAD of `linePhotos.data` directly, for the same reason
   *  `footerTotalsRef` exists (S7's S3-class defect, reintroduced here by C3): a query's
   *  `data` is a fresh reference on every fetch (and, worse, some test doubles return a
   *  fresh literal on every CALL, so every keystroke's re-render sees a "changed" value
   *  even though nothing was fetched). Listing `linePhotos.data` as a `columns` dependency
   *  rebuilt every column - with brand-new cell renderers - on every keystroke, which
   *  remounted every `<Input>` on the grid and dropped focus after one character. The ref
   *  lets the Photos cell read the CURRENT map without `columns` needing to change. */
  const linePhotosRef = useRef(linePhotos.data);
  linePhotosRef.current = linePhotos.data;

  /** Server-searched, so any of the 10k+ products is reachable rather than a first page. */
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

  const columns = useMemo<ColumnDef<GridLine>[]>(() => {
    const cols: ColumnDef<GridLine>[] = [
      {
        id: 'factory',
        header: ({ column }) => <DataGridColumnHeader title="Factory" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (editing) {
            return (
              <SupplierCombobox
                className="w-40"
                value={line.supplier_id}
                onChange={(v) => setLineField(line.key, 'supplier_id', v)}
                suppliers={suppliers}
                placeholder="No factory named"
              />
            );
          }
          const name = lineSupplierName(line.supplier_id);
          return (
            <span className="block truncate" title={name ?? undefined}>
              {name ?? EM_DASH}
            </span>
          );
        },
        size: 170,
        enableSorting: false,
        meta: { headerTitle: 'Factory' },
        footer: () => <span>Total</span>,
      },
      {
        id: 'no',
        header: ({ column }) => <DataGridColumnHeader title="No" column={column} />,
        cell: ({ row }) => (editing ? EM_DASH : row.index + 1),
        size: 55,
        enableSorting: false,
        meta: { headerTitle: 'No', ...numMeta },
      },
      {
        id: 'model',
        header: ({ column }) => <DataGridColumnHeader title="Model" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          // An EXISTING line gets the same picker as a new one (AC-J3): the factory's
          // packing list is sometimes matched to the wrong catalogue product, and there was
          // no way to correct that short of removing the line and re-adding it.
          if (editing) {
            return (
              <ProductComboboxSearchable
                className="w-52"
                value={line.product_id}
                onChange={(v) => setLineField(line.key, 'product_id', v)}
                // The code/name travel WITH the id (AC-J2, AC-J3): they are what the
                // trigger falls back to displaying while the just-picked id is not on the
                // page's own fetched options any more (a resort, a re-render off other
                // state). Left stale, the trigger read the id's NEW product against the
                // PREVIOUS product's code the moment one was picked.
                onOptionChange={(opt) => {
                  setLineField(line.key, 'product_code', opt?.product_code ?? '');
                  setLineField(line.key, 'product_name', opt?.product_name ?? null);
                }}
                fetchProducts={fetchProducts}
                productFallback={
                  line.product_id
                    ? {
                        id: line.product_id,
                        product_code: line.product_code,
                        product_name: line.product_name ?? undefined,
                      }
                    : null
                }
                placeholder="Search a product"
              />
            );
          }
          if (!line.id) return <span className="text-muted-foreground">{EM_DASH}</span>;
          return (
            <Link
              href={`/master-data-management/products/${line.product_id}`}
              className="block truncate font-medium text-primary hover:underline"
              title={line.product_code || undefined}
            >
              {line.product_code || EM_DASH}
            </Link>
          );
        },
        size: 150,
        enableSorting: false,
        meta: { headerTitle: 'Model' },
      },
      {
        id: 'description',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (editing) {
            return (
              <Input
                className="h-8 w-56"
                value={line.description}
                onChange={(e) => setLineField(line.key, 'description', e.target.value)}
                placeholder={line.product_name ?? undefined}
                aria-label={`Description for ${line.product_code || 'the new line'}`}
              />
            );
          }
          // The supplier's own wording carried off the PI (S9); the product's name when
          // the line has none - a container drafted before this field existed, or one
          // read off the n8n PDF path, which states no description at all.
          const value = line.description || line.product_name;
          return (
            <span className="block truncate" title={value ?? undefined}>
              {value || EM_DASH}
            </span>
          );
        },
        size: 240,
        enableSorting: false,
        meta: { headerTitle: 'Description' },
      },
      {
        id: 'material',
        header: ({ column }) => <DataGridColumnHeader title="Material" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (editing) {
            return (
              <Input
                className="h-8 w-28"
                value={line.material}
                onChange={(e) => setLineField(line.key, 'material', e.target.value)}
                aria-label={`Material for ${line.product_code || 'the new line'}`}
              />
            );
          }
          return (
            <span className="block truncate" title={line.material || undefined}>
              {line.material || EM_DASH}
            </span>
          );
        },
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'Material' },
      },
      {
        id: 'qty',
        header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <LineNumberInput line={line} name="quantity_shipped" label="Quantity" onChange={setLineField} />
          ) : (
            line.quantity_shipped
          );
        },
        size: 85,
        enableSorting: false,
        meta: { headerTitle: 'Qty', ...numMeta },
        footer: () => <span>{footerTotalsRef.current.qty}</span>,
      },
      {
        id: 'pcs_per_carton',
        header: ({ column }) => <DataGridColumnHeader title="Pcs/ctn" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <LineNumberInput
              line={line}
              name="pcs_per_carton"
              label="Pcs per carton"
              step="0.0001"
              onChange={setLineField}
            />
          ) : (
            fmtStated(line.pcs_per_carton)
          );
        },
        size: 85,
        enableSorting: false,
        meta: { headerTitle: 'Pcs/ctn', ...numMeta },
      },
      {
        id: 'ctn_qty',
        header: ({ column }) => <DataGridColumnHeader title="Ctn qty" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          // Derived off Pcs/ctn (`= Qty / Pcs per carton`, the workbook's own `H` formula)
          // whenever a pack size is stated - editing it there would disagree with the number
          // it derives. Where Pcs/ctn is blank there is nothing to derive it FROM, so it is
          // the stated carton count instead (AC-J4), and typing it is the only way a
          // container with no pack size can ever carry one.
          if (editing && !toNum(line.pcs_per_carton)) {
            return (
              <LineNumberInput line={line} name="cartons_count" label="Ctn qty" onChange={setLineField} />
            );
          }
          const cells = deriveLineCells(line);
          return cells.ctnQty === null ? EM_DASH : fmtDp(cells.ctnQty, 2);
        },
        size: 90,
        enableSorting: false,
        meta: { headerTitle: 'Ctn qty', ...numMeta },
        footer: () => <span>{fmtDp(footerTotalsRef.current.ctnQty, 2)}</span>,
      },
      {
        id: 'l',
        header: ({ column }) => <DataGridColumnHeader title="L" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <LineNumberInput line={line} name="carton_length_cm" label="Carton length" step="0.01" onChange={setLineField} />
          ) : (
            fmtStated(line.carton_length_cm)
          );
        },
        size: 70,
        enableSorting: false,
        meta: { headerTitle: 'Length (cm)', ...numMeta },
      },
      {
        id: 'w',
        header: ({ column }) => <DataGridColumnHeader title="W" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <LineNumberInput line={line} name="carton_width_cm" label="Carton width" step="0.01" onChange={setLineField} />
          ) : (
            fmtStated(line.carton_width_cm)
          );
        },
        size: 70,
        enableSorting: false,
        meta: { headerTitle: 'Width (cm)', ...numMeta },
      },
      {
        id: 'h',
        header: ({ column }) => <DataGridColumnHeader title="H" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <LineNumberInput line={line} name="carton_height_cm" label="Carton height" step="0.01" onChange={setLineField} />
          ) : (
            fmtStated(line.carton_height_cm)
          );
        },
        size: 70,
        enableSorting: false,
        meta: { headerTitle: 'Height (cm)', ...numMeta },
      },
      {
        id: 'cbm_per_ctn',
        header: ({ column }) => <DataGridColumnHeader title="CBM/ctn" column={column} />,
        cell: ({ row }) => {
          const cells = deriveLineCells(row.original);
          return cells.cbmPerCtn === null ? EM_DASH : fmtDp(cells.cbmPerCtn, 5);
        },
        size: 100,
        enableSorting: false,
        meta: { headerTitle: 'CBM/ctn', ...numMeta },
      },
      {
        id: 'total_cbm',
        header: ({ column }) => <DataGridColumnHeader title="Total CBM" column={column} />,
        cell: ({ row }) => {
          const cells = deriveLineCells(row.original);
          return cells.totalCbm === null ? EM_DASH : fmtDp(cells.totalCbm, 3);
        },
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Total CBM', ...numMeta },
        footer: () => (
          <span>
            {fmtDp(footerTotalsRef.current.totalCbm, 3)}
            {footerTotalsRef.current.unmeasuredCbm > 0 ? (
              <span className="ms-1 font-normal text-muted-foreground">
                ({footerTotalsRef.current.unmeasuredCbm} unmeasured)
              </span>
            ) : null}
          </span>
        ),
      },
      {
        id: 'nw',
        header: ({ column }) => <DataGridColumnHeader title="NW" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <LineNumberInput line={line} name="net_weight_per_carton" label="Net weight" step="0.001" onChange={setLineField} />
          ) : (
            fmtStated(line.net_weight_per_carton)
          );
        },
        size: 80,
        enableSorting: false,
        meta: { headerTitle: 'NW (kg)', ...numMeta },
      },
      {
        id: 'gw',
        header: ({ column }) => <DataGridColumnHeader title="GW" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <LineNumberInput line={line} name="gross_weight_per_carton" label="Gross weight" step="0.001" onChange={setLineField} />
          ) : (
            fmtStated(line.gross_weight_per_carton)
          );
        },
        size: 80,
        enableSorting: false,
        meta: { headerTitle: 'GW (kg)', ...numMeta },
      },
      {
        id: 'total_nw',
        header: ({ column }) => <DataGridColumnHeader title="Total NW" column={column} />,
        cell: ({ row }) => {
          const cells = deriveLineCells(row.original);
          return cells.totalNw === null ? EM_DASH : fmtDp(cells.totalNw, 2);
        },
        size: 100,
        enableSorting: false,
        meta: { headerTitle: 'Total NW (kg)', ...numMeta },
        footer: () => <span>{fmtDp(footerTotalsRef.current.totalNw, 2)}</span>,
      },
      {
        id: 'total_gw',
        header: ({ column }) => <DataGridColumnHeader title="Total GW" column={column} />,
        cell: ({ row }) => {
          const cells = deriveLineCells(row.original);
          return cells.totalGw === null ? EM_DASH : fmtDp(cells.totalGw, 2);
        },
        size: 100,
        enableSorting: false,
        meta: { headerTitle: 'Total GW (kg)', ...numMeta },
        footer: () => <span>{fmtDp(footerTotalsRef.current.totalGw, 2)}</span>,
      },
      {
        id: 'logo',
        header: ({ column }) => <DataGridColumnHeader title="Logo" column={column} />,
        cell: ({ row }) => {
          const brand = row.original.brand;
          return (
            <span className="block truncate" title={brand ?? undefined}>
              {brand || EM_DASH}
            </span>
          );
        },
        size: 100,
        enableSorting: false,
        meta: { headerTitle: 'Logo' },
      },
      {
        id: 'remarks',
        header: ({ column }) => <DataGridColumnHeader title="Remarks" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (editing) {
            return (
              <Input
                className="h-8 w-44"
                value={line.remarks}
                onChange={(e) => setLineField(line.key, 'remarks', e.target.value)}
                aria-label={`Remarks for ${line.product_code || 'the new line'}`}
              />
            );
          }
          return (
            <span className="block truncate" title={line.remarks || undefined}>
              {line.remarks || EM_DASH}
            </span>
          );
        },
        size: 200,
        enableSorting: false,
        meta: { headerTitle: 'Remarks' },
      },
      {
        id: 'price',
        header: ({ column }) => <DataGridColumnHeader title="Price" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (editing) {
            return <LineNumberInput line={line} name="unit_cost" label="Price" step="0.01" onChange={setLineField} />;
          }
          return line.unit_cost ? `${fmtDp(line.unit_cost, 2)}${line.currency ? ` ${line.currency}` : ''}` : EM_DASH;
        },
        size: 100,
        enableSorting: false,
        meta: { headerTitle: 'Price', ...numMeta },
      },
      {
        id: 'amount',
        header: ({ column }) => <DataGridColumnHeader title="Amount" column={column} />,
        cell: ({ row }) => {
          const cells = deriveLineCells(row.original);
          return cells.amount === null ? EM_DASH : fmtDp(cells.amount, 2);
        },
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Amount', ...numMeta },
        footer: () => <span>{fmtDp(footerTotalsRef.current.amount, 2)}</span>,
      },
      {
        id: 'from_pi',
        header: ({ column }) => <DataGridColumnHeader title="From PI" column={column} />,
        cell: ({ row }) => {
          const links = row.original.sourceLinks;
          if (!links.length) return <span className="text-muted-foreground">{EM_DASH}</span>;
          return (
            <div className="flex flex-col gap-0.5">
              {links.map((src, i) => (
                // ONE invoice can charge the same shipment line twice - two of its own
                // lines for the same item, consolidated into one container line - so the
                // pair (invoice, line) repeats and needs the position too.
                <Link
                  key={`${src.proforma_invoice_id}-${row.original.id}-${i}`}
                  href={`/scm/proforma-invoices/${src.proforma_invoice_id}`}
                  className="truncate text-primary hover:underline"
                >
                  {src.pi_number}
                  <span className="ms-1 text-xs text-muted-foreground">{src.qty}</span>
                </Link>
              ))}
            </div>
          );
        },
        size: 160,
        enableSorting: false,
        meta: { headerTitle: 'From PI' },
      },
      {
        id: 'spo_allocated',
        header: ({ column }) => <DataGridColumnHeader title="SPO allocated" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return (
            <div className="flex items-center gap-2">
              <span>{line.spoAllocated != null ? line.spoAllocated : EM_DASH}</span>
              {line.relatedSpoAllocations?.length ? (
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6 text-muted-foreground hover:text-foreground"
                      aria-label={`View related SPO for ${line.product_code || 'this line'}`}
                    >
                      <Info className="size-4" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-80 space-y-4 p-4">
                    <div className="space-y-1">
                      <p className="text-sm font-medium">{line.product_code || 'Related SPO'}</p>
                    </div>
                    <div className="space-y-2">
                      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Related SPO
                      </p>
                      <div className="space-y-2">
                        {line.relatedSpoAllocations.map((spo) => {
                          const content = (
                            <>
                              <div className="flex items-center justify-between gap-2">
                                <span
                                  className={
                                    spo.spo_number
                                      ? 'text-sm font-medium text-primary'
                                      : 'text-sm font-medium'
                                  }
                                >
                                  {spo.spo_number || 'SPO Allocation'}
                                </span>
                                {spo.receipt_status ? (
                                  <Badge status={spo.receipt_status} className="shrink-0">
                                    {formatStatusLabel(spo.receipt_status)}
                                  </Badge>
                                ) : null}
                              </div>
                              {spo.allocated_quantity != null ? (
                                <p className="mt-1 text-xs text-muted-foreground">
                                  Allocated: {spo.allocated_quantity}
                                </p>
                              ) : null}
                            </>
                          );
                          return spo.spo_number ? (
                            <Link
                              key={spo.id}
                              href={spoDetailHref(spo.spo_number)}
                              className="block rounded-md border px-3 py-2 hover:bg-muted/50"
                            >
                              {content}
                            </Link>
                          ) : (
                            <div key={spo.id} className="block rounded-md border px-3 py-2">
                              {content}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>
              ) : null}
            </div>
          );
        },
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'SPO allocated', ...numMeta },
      },
      {
        id: 'received',
        header: ({ column }) => <DataGridColumnHeader title="Received" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return (
            <div className="flex items-center gap-2">
              <span>{line.received != null ? line.received : EM_DASH}</span>
              {line.relatedGrns?.length ? (
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6 text-muted-foreground hover:text-foreground"
                      aria-label={`View related GRN for ${line.product_code || 'this line'}`}
                    >
                      <Info className="size-4" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-80 space-y-4 p-4">
                    <div className="space-y-1">
                      <p className="text-sm font-medium">{line.product_code || 'Related GRN'}</p>
                    </div>
                    <div className="space-y-2">
                      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Related GRN
                      </p>
                      <div className="space-y-2">
                        {line.relatedGrns.map((grn) => (
                          <Link
                            key={grn.id}
                            href={`/procurement-management/grn/${grn.id}`}
                            className="block rounded-md border px-3 py-2 hover:bg-muted/50"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-sm font-medium text-primary">
                                {grn.picking_number || 'GRN'}
                              </span>
                              {grn.picking_status ? (
                                <Badge status={grn.picking_status} className="shrink-0">
                                  {formatStatusLabel(grn.picking_status)}
                                </Badge>
                              ) : null}
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">
                              {grn.spo_number || 'No SPO'}
                              {grn.picking_date ? ` • ${formatDate(new Date(grn.picking_date))}` : ''}
                            </p>
                          </Link>
                        ))}
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>
              ) : null}
            </div>
          );
        },
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Received', ...numMeta },
      },
      {
        id: 'status',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (editing) return <span className="text-muted-foreground">{EM_DASH}</span>;
          const lineStatus =
            line.status ??
            getLineStatus(Number(line.quantity_shipped || 0), line.spoAllocated ?? 0, line.received ?? 0);
          return <Badge status={lineStatus}>{formatStatusLabel(lineStatus)}</Badge>;
        },
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Status' },
      },
      {
        id: 'photos',
        header: ({ column }) => <DataGridColumnHeader title="Photos" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return (
            <ShipmentLinePhotosCell
              shipmentId={packingListId as string}
              lineId={line.id ?? null}
              productLabel={line.product_code || 'this line'}
              photos={line.id ? (linePhotosRef.current?.[line.id] ?? []) : []}
            />
          );
        },
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Photos' },
      },
    ];

    if (editing) {
      cols.push({
        id: 'remove',
        header: () => '',
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 gap-1 px-2 text-xs text-destructive hover:text-destructive"
            // Nothing is confirmed and nothing is deferred (D7): the line leaves the DRAFT,
            // and Save is what sends it, so until then there is nothing to take back.
            onClick={() => removeLine(row.original.key)}
          >
            <Trash2 className="size-3.5" />
            Remove
          </Button>
        ),
        size: 100,
        enableSorting: false,
        meta: { headerTitle: 'Remove' },
      });
    }

    // From PI and Photos are SCM reads (and Photos an SCM upload): without SCM read they
    // would only ever say "none" and offer an action that 403s (PL-TABS-ACCESS).
    return canReadScm ? cols : cols.filter((c) => c.id !== 'from_pi' && c.id !== 'photos');
    // `footerTotals` and `linePhotos.data` deliberately absent: both are fresh references on
    // every render for reasons that have nothing to do with an edit (a query result, and in
    // at least one test double, a fresh object literal per call) - the footer and Photos
    // cells read them through `footerTotalsRef` / `linePhotosRef` instead, precisely so this
    // memo does NOT rebuild while somebody is typing (AC-J1, see those refs' own comments).
    // `setLineField` and `removeLine` are stable (`useCallback`, empty deps, in
    // `packing-list-context.tsx`) so they cost nothing as dependencies here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, suppliers, supplierNameById, setLineField, removeLine, packingListId, canReadScm]);

  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (r) => r.key,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  if (!packingList) return null;

  // In edit mode the grid is always rendered, even with no lines yet: the empty state is
  // where somebody would go to ADD the first one, and a grid with nothing in it has nowhere
  // to put it.
  const hasLines = (packingList.shipment_lines?.length ?? 0) > 0;
  if (!editing && !hasLines) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <p className="text-sm font-medium">No shipment lines</p>
          <p className="mt-1 text-sm text-muted-foreground">
            This packing list has no product lines yet. They arrive with the packing list
            import, or can be added by editing the packing list.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <DataGrid
        table={table}
        recordCount={rows.length}
        isLoading={false}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        emptyMessage="This packing list has no product lines."
        listingKey={LISTING_KEY}
      >
        <Card>
          {/* "Shipment Lines" plus a 224px search box does not fit the 286px of card content
              at 375px, so the header wraps rather than pushing the page into scroll. */}
          <CardHeader className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle className="min-w-0 break-words">Shipment Lines</CardTitle>
            <CardToolbar>
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                placeholder="Search by product code"
                className="w-full sm:w-56"
              />
            </CardToolbar>
          </CardHeader>
          {/* `DataGridTable` scrolls horizontally inside its own container - the page never
              scrolls sideways at 375px (AC-G7). */}
          <CardTable>
            <DataGridTable />
          </CardTable>
        </Card>
      </DataGrid>
      {editing ? (
        <Button variant="outline" size="sm" onClick={addLine}>
          Add line
        </Button>
      ) : null}
    </div>
  );
}

export default PackingListLinesTab;
