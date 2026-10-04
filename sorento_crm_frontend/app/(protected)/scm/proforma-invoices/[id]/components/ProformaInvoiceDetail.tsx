'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { toast } from '@/lib/toast';
import {
  Boxes,
  Download,
  FileText,
  GitBranch,
  ListOrdered,
  LoaderCircle,
  PackageCheck,
  PackageSearch,
  Plus,
  Settings,
  SquarePen,
  Trash2,
  Undo2,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardHeader,
  CardHeading,
  CardTable,
  CardTitle,
  CardToolbar,
} from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import { Field } from '@/components/common/Field';
import AttachmentFileCard from '@/components/common/AttachmentFileCard';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  SET_OPTION_PREFIX,
  aliasTargetFor,
  fetchProductOrSetOptions,
  isSetOption,
  renderProductOrSetOption,
} from '../../../components/productOrSetPicker';
import { useUOMSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query';
import { useHasPermission } from '@/hooks/usePermissions';
import { useUrlTab } from '@/hooks/useUrlTab';
import {
  useConvertProformaInvoicesToDraftShipment,
  useProformaInvoice,
  useSaveProformaInvoice,
} from '../../../hooks/useProformaInvoices';
import {
  useProformaInvoicePacking,
  useProformaInvoicePackingMutations,
} from '../../../hooks/useProformaInvoicePacking';
import { useMatchSupplierCode } from '../../../hooks/useSupplierCodeAliases';
import { packedQtyForLine } from '../../services/proformaInvoicePackingService';
import { EM_DASH, fmtDate, fmtQty, fmtSupplierCost, fmtTrimmedDecimal } from '../../../lib/format';
import {
  downloadProformaInvoiceExport,
  type ProformaInvoiceDetail as ProformaInvoiceDetailPayload,
  type ProformaInvoiceLine,
  type ProformaInvoiceLineWrite,
} from '../../../services/proformaInvoiceService';
import ConvertToPackingListDialog from '../../components/ConvertToPackingListDialog';
import { DescriptionEnCell } from '../../components/DescriptionEnCell';
import OverCapacityDialog from '../../components/OverCapacityDialog';
import { ProformaInvoicePackingListsTab } from './ProformaInvoicePackingListsTab';
import { ProformaInvoicePackingTab } from './ProformaInvoicePackingTab';
import { SupplierDocumentsUploadDialog } from '../../components/SupplierDocumentsUploadDialog';
import DetailActions from '@/components/common/DetailActions';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { proformaInvoicesPagerQuery } from '../../../hooks/useProformaInvoices';
import { MarkAsRevisionDialog } from './MarkAsRevisionDialog';
import { ProformaRevisionsCard } from './ProformaRevisionsCard';
import BackToList, { useBackToListHref } from '@/components/common/BackToList';

const CONVERT_PERMISSION = 'scm.reorder.run';
const ADJUST_PERMISSION = 'scm.proforma_invoice.upload';
/** Writing a supplier-code ruling is `scm.reorder.run`, the permission the alias POST and
 *  DELETE are behind (`fulfilment.py`). The Product picker acts on THAT, so it is offered
 *  only to somebody who holds it as well as the invoice's own adjust permission - a select
 *  that 403s on the pick is worse than a plain read-only code. */
const RULING_PERMISSION = CONVERT_PERMISSION;

/** Keyed off the read permission plus a stable id, never the record's own path - a 30-line
 *  invoice is read with the same few columns every time and the choice has to survive. */
const LINES_LISTING_KEY = 'scm.dashboard.view::proforma-invoice-lines';

/** How many products a page of the picker asks for. */
const PRODUCT_PAGE_SIZE = 50;

/** The record's five tabs (S1, S2): General (default), Lines, Packing (the supplier's own
 *  packing rows, AC-B9), Revisions, Packing lists (which SHIPMENT this invoice's lines
 *  went to, once converted - a different question from Packing). */
const PI_TABS = ['general', 'lines', 'packing', 'revisions', 'packing-lists'] as const;

/**
 * One line as the EDIT DRAFT holds it - and, in view mode, as the grid reads it.
 *
 * Both modes render from this one shape on purpose: the columns, their order and their
 * formatting are then written once, and "view and edit are the same layout" cannot rot into
 * two grids that drifted apart. Numbers are STRINGS because that is what an `<input>` holds;
 * an empty string is "not stated", which is not the same as zero.
 */
interface DraftLine {
  /** Stable React key. The server id for a stored line, a local one for an added row. */
  key: string;
  id?: string;
  productId: string | null;
  /** The line's other possible binding (R19) - carried through so an untouched line does not
   *  lose it on Save, though this slice's select only picks PRODUCTS (S3 brings set memory). */
  productSetId: string | null;
  productCode: string | null;
  itemCode: string;
  description: string;
  qty: string;
  uom: string;
  cartons: string;
  cbmPerUnit: string;
  unitPrice: string;
  netWeight: string;
  grossWeight: string;
  /** Struck through in the draft, and only actually deleted on Save. */
  removed: boolean;
  /** The stored line this row came from - the read-only facts (match provenance, the
   *  supplier's own figures) live there. Null on a row the operator added. */
  source: ProformaInvoiceLine | null;
}

function str(value: number | null | undefined): string {
  return value == null ? '' : String(value);
}

function num(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function toDraft(line: ProformaInvoiceLine): DraftLine {
  return {
    key: line.id,
    id: line.id,
    // AC-B2: the matched product travels into the draft, so the edit-mode select shows it
    // rather than reading empty on every open.
    productId: line.product_id,
    productSetId: line.product_set_id,
    productCode: line.product_code,
    itemCode: line.item_code,
    description: line.description ?? '',
    qty: str(line.qty),
    uom: line.uom ?? '',
    cartons: str(line.cartons),
    cbmPerUnit: str(line.cbm_per_unit),
    unitPrice: str(line.unit_price),
    netWeight: str(line.net_weight),
    grossWeight: str(line.gross_weight),
    removed: false,
    source: line,
  };
}

/**
 * What one line takes up per unit, however the supplier stated it.
 *
 * The document states a per-unit volume and a total, and only sometimes both; a line whose
 * quantity we then trim has to keep answering "how much room does it take", so the per-unit
 * figure is the one that survives an adjustment and the total is derived from it.
 */
function perUnitCbm(row: DraftLine): number | null {
  const per = num(row.cbmPerUnit);
  if (per != null) return per;
  const total = row.source?.cbm_total;
  const qty = num(row.qty);
  if (total != null && qty) return total / qty;
  return null;
}

export function ProformaInvoiceDetail({ id }: { id: string }) {
  const router = useRouter();
  const backHref = useBackToListHref('/scm/proforma-invoices');
  const canConvert = useHasPermission(CONVERT_PERMISSION);
  const canAdjust = useHasPermission(ADJUST_PERMISSION);
  const canRule = useHasPermission(RULING_PERMISSION);
  /** Both, for the Product cell: it edits the line AND writes the supplier's ruling. */
  const canPickProduct = canAdjust && canRule;
  const { data, isLoading, isError } = useProformaInvoice(id);
  // AC-B4: the master list, not free text - "unit" and "UNIT" read as one unit here and two
  // strings on the export.
  const uoms = useUOMSelectQuery();
  const uomSelectOptions = useMemo(
    () => (uoms.data ?? []).map((u) => ({ value: u.uom_code, label: u.uom_code })),
    [uoms.data],
  );
  const convertToDraftShipment = useConvertProformaInvoicesToDraftShipment();
  const saveInvoice = useSaveProformaInvoice(id);
  // Delete asks nothing (D7): the countdown takes the primary button's place and
  // Cancel is the way back.
  const deletion = useDeferredAction({
    actionKey: 'proforma_invoice.delete',
    entityType: 'proforma_invoice',
    entityId: id,
    verb: 'Deleting',
    subject: data?.pi_number ?? 'this invoice',
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Proforma invoice deleted.',
    invalidateKeys: [['scm', 'proforma-invoices', 'list']],
    onCommitted: () => router.push(backHref),
  });
  // Forgetting a ruling un-binds the rows it held, and the same ruling can be made
  // again from this screen, so it takes the reversible window rather than the long one.
  const matchForgetting = useDeferredRowAction({
    actionKey: 'supplier_code_alias.forget',
    entityType: 'supplier_code_alias',
    verb: 'Forgetting',
    successMessage: 'Match forgotten.',
    invalidateKeys: [['scm', 'proforma-invoices', 'detail', id]],
    // In the cell the picker was in (S7, AC-7.5): clearing the Product select IS the
    // forget, so the countdown belongs where the control the reader just used stands,
    // not in a toast over a screen they are still reading.
    surface: 'inline',
    // The cell is 240px, narrower than the countdown's own comfortable minimum: without
    // this it overflowed the column and the Cancel button came out as "Canc".
    countdownClassName: 'w-full min-w-0',
  });
  // Destructured for the Lines grid's `columns` memo below: `run` is the only thing that
  // memo calls, and it never changes reference (`useDeferredRowAction`'s own `useCallback`
  // has an empty dep array). Depending on `matchForgetting` WHOLE ties every row's cells to
  // `isPending`/`targetId`, which flip the moment ANY row's Forget starts, counts down or
  // commits - a background pending-action watch settling while a product picker on a
  // DIFFERENT row is open recomputed every column, remounted every row's `SearchableSelect`,
  // and silently closed the open popover (reproduced in isolation, S2 tester).
  const forgetMatch = matchForgetting.run;
  // Which ruling is counting down, as a plain string: the cells below have to know, and
  // depending on the countdown NODE would rebuild `columns` on every render of the window
  // for the same reason the note above gives. The node itself is read through a ref at
  // cell-render time, which costs the memo nothing.
  const forgetTargetId = matchForgetting.targetId;
  const forgetCountdownRef = useRef<React.ReactNode>(null);
  forgetCountdownRef.current = matchForgetting.countdown;
  // "This code is that product", written the moment it is picked (S7): the same hook the
  // Match dialog used, so the rebind count is still toasted and every screen reading those
  // rows is invalidated together.
  const matchCode = useMatchSupplierCode();
  // `mutateAsync` off the hook, not the hook's result object: the object is a new one on
  // every render, and `columns` below depends on the writer.
  const writeMatch = matchCode.mutateAsync;
  /** A ruling is being written, so the pickers go quiet until the server has answered
   *  (AC-7.3) - one ruling at a time, and the answer re-points rows on other lines too. */
  const matchPending = matchCode.isPending;
  const supplierId = data?.supplier_id ?? '';
  // Read through a ref by the writer below: `patchLine` is declared further down (it needs
  // the draft state) and a fresh function every render would rebuild `columns` every
  // render, which is the one thing this grid cannot survive.
  const patchLineRef = useRef<(key: string, patch: Partial<DraftLine>) => void>(() => {});
  /** Record what the picked option means for this supplier's code (AC-7.3, AC-7.4).
   *
   *  The DRAFT is patched with the same answer once the server has taken it: in read mode
   *  the invalidated detail query brings the new binding back on its own, but an edit
   *  session renders from `draftLines`, which no refetch touches - the cell would have
   *  gone on showing the old code until Cancel. Patching it also makes the eventual Save
   *  agree with what the server already holds, so the line is written back unchanged
   *  rather than reverted. */
  const writeCodeMatch = useCallback(
    async (supplierCode: string, option: SearchableSelectOption, rowKey: string) => {
      const value = option.value;
      if (!supplierId || !value) return;
      try {
        await writeMatch({
          supplier_id: supplierId,
          supplier_code: supplierCode,
          ...aliasTargetFor(value),
        });
      } catch {
        // The hook toasts the refusal; the cell keeps whatever it already held.
        return;
      }
      const set = isSetOption(value);
      patchLineRef.current(rowKey, {
        productId: set ? null : value,
        productSetId: set ? value.slice(SET_OPTION_PREFIX.length) : null,
        productCode: option.code ?? null,
      });
    },
    [supplierId, writeMatch],
  );

  // The tab lives in the URL, not component state (S1, AC-A1-A4): reload, the record's own
  // prev/next pager, and pressing Edit all have to land back on the tab she was reading.
  // Shared with the loading plan as `useUrlTab`, extracted from its own inline version.
  const [tab, setTab] = useUrlTab({
    tabs: PI_TABS,
    defaultTab: 'general',
    basePath: `/scm/proforma-invoices/${id}`,
  });
  const [editing, setEditing] = useState(false);
  const [draftNumber, setDraftNumber] = useState('');
  const [draftLines, setDraftLines] = useState<DraftLine[]>([]);
  const [overCapacity, setOverCapacity] = useState<string | null>(null);
  const [overrideReason, setOverrideReason] = useState('');
  const [convertOpen, setConvertOpen] = useState(false);
  const [revisionOpen, setRevisionOpen] = useState(false);
  // Held so an over-capacity refusal can be re-submitted with the reason WITHOUT asking
  // the operator to re-type the split - or the container size - they already chose.
  const [convertArgs, setConvertArgs] = useState<{
    lineQuantities: Record<string, number>;
    containerSizeId: string | null;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  /** Attach / Replace packing list (S2, AC-B10) - the shared upload dialog, opened with
   *  the supplier and this invoice preselected and locked (`attachTo`). */
  const [packingUploadOpen, setPackingUploadOpen] = useState(false);

  const lines = useMemo<ProformaInvoiceLine[]>(() => data?.lines ?? [], [data]);
  const packing = useProformaInvoicePacking(data);
  const packingRows = packing.data?.rows ?? [];
  // Read by the Packed cell INSTEAD of `packingRows` directly, for the same reason
  // `footerTotalsRef`/`linePhotosRef` exist on the packing-list Lines grid next door: the
  // packing query resolving is a fresh reference unrelated to any edit, and listing
  // `packingRows` as a `columns` dependency rebuilt the whole memo - with brand-new cell
  // renderers - the moment it settled, which remounted every `<Input>`/async Product
  // select on the grid and could close a dropdown mid-fetch before an option was clicked.
  const packingRowsRef = useRef(packingRows);
  packingRowsRef.current = packingRows;
  const packingMutations = useProformaInvoicePackingMutations(id);
  const superseded = data?.status === 'superseded';
  // An invoice with ANY of its goods on a shipment is frozen: trimming the document
  // afterwards would leave the two disagreeing with nothing on screen saying which one the
  // container was loaded from. `fullyPlaced` is the narrower question - whether there is
  // anything left to convert (Q9).
  const converted = (data?.converted_shipments?.length ?? 0) > 0;
  const fullyPlaced = data?.placement === 'converted';
  const canEdit = canAdjust && !superseded && !converted;

  /** Product code per id, filled as the picker's pages come back, so an added line can
   *  default its item code from the product without parsing the option's label. */
  const productCodes = useRef<Map<string, { code: string; uom: string }>>(new Map());

  /**
   * Products AND our product sets in one list (S7), the same picker the loading plan's
   * Supplier codes cell reads from: the supplier prices a whole WC, and a line whose code
   * means one of our SETS cannot be answered by a products-only list.
   *
   * The product half is recorded on the way past, because an added line fills its item code
   * and UOM from the product chosen and an option value alone says neither.
   */
  const fetchProductOrSet = useCallback(
    (query: string, pageIndex: number) =>
      fetchProductOrSetOptions(query, pageIndex, (products) => {
        for (const p of products) productCodes.current.set(p.id, { code: p.code, uom: p.uom });
      }),
    [],
  );

  // Editing starts from whatever the server currently holds, every time - a draft left over
  // from a cancelled edit would silently re-apply what the user backed out of. It does NOT
  // reset the tab (AC-A4): pressing Edit from Lines has to stay on Lines.
  const beginEdit = () => {
    if (!data) return;
    setDraftNumber(data.pi_number);
    setDraftLines(lines.map(toDraft));
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setDraftLines([]);
  };

  // Nothing to re-sync while editing: the draft IS the document until Save, and a payload
  // that changed underneath it is a conflict the save will report, not something to silently
  // merge into what the operator is typing.
  useEffect(() => {
    if (!editing) setDraftLines([]);
  }, [editing]);

  /** What the grid renders: the draft while editing, the stored lines otherwise. */
  const rows = useMemo<DraftLine[]>(
    () => (editing ? draftLines : lines.map(toDraft)),
    [editing, draftLines, lines],
  );

  const patchLine = (key: string, patch: Partial<DraftLine>) => {
    setDraftLines((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  };
  patchLineRef.current = patchLine;

  const addLine = () => {
    setDraftLines((prev) => [
      ...prev,
      {
        key: `new-${Date.now()}-${prev.length}`,
        productId: null,
        productSetId: null,
        productCode: null,
        itemCode: '',
        description: '',
        qty: '',
        uom: '',
        cartons: '',
        cbmPerUnit: '',
        unitPrice: '',
        netWeight: '',
        grossWeight: '',
        removed: false,
        source: null,
      },
    ]);
  };

  // Live while editing (the journey's "fill bar live"), and the server's own figures once
  // saved (AC-E3) - the two agree because both multiply the per-unit volume by the quantity.
  const volume = useMemo(() => {
    if (!editing) {
      return { total: data?.total_cbm ?? null, unmeasured: data?.unmeasured_lines ?? 0 };
    }
    let total: number | null = null;
    let unmeasured = 0;
    for (const row of draftLines) {
      if (row.removed) continue;
      const per = perUnitCbm(row);
      if (per == null) {
        unmeasured += 1;
        continue;
      }
      total = (total ?? 0) + per * (num(row.qty) ?? 0);
    }
    return { total, unmeasured };
  }, [editing, draftLines, data?.total_cbm, data?.unmeasured_lines]);

  const saveEdit = async () => {
    if (!data) return;
    const number = draftNumber.trim();
    if (!number) {
      toast.error('The invoice needs a number.');
      return;
    }
    const kept = draftLines.filter((r) => !r.removed);
    for (const row of kept) {
      if (!row.itemCode.trim()) {
        toast.error('Every line needs an item code.');
        return;
      }
      const qty = num(row.qty);
      if (qty == null || qty < 0) {
        toast.error(`${row.itemCode || 'A new line'}: enter a quantity of zero or more.`);
        return;
      }
    }
    // ONE call. Rows with an id update, rows without create, and a line the array no longer
    // names is deleted - so the whole draft either lands or none of it does.
    //
    // `product_id` / `product_set_id`: the key is sent ONLY when the operator actually
    // touched the Product select on this row - untouched, it is left out of the payload
    // entirely, and the server keeps whatever the line already had (AC-B3). Sending the
    // CURRENT value unconditionally (what this used to do, always omitting it because
    // `toDraft` hard-coded `productId: null`) is what silently unbound every matched product
    // on every save.
    const payload: ProformaInvoiceLineWrite[] = kept.map((row) => {
      const productIdChanged = row.productId !== (row.source?.product_id ?? null);
      const productSetIdChanged = row.productSetId !== (row.source?.product_set_id ?? null);
      return {
        ...(row.id ? { id: row.id } : {}),
        ...(productIdChanged ? { product_id: row.productId } : {}),
        ...(productSetIdChanged ? { product_set_id: row.productSetId } : {}),
        item_code: row.itemCode.trim(),
        description: row.description.trim() || null,
        qty: num(row.qty) ?? 0,
        uom: row.uom.trim() || null,
        cartons: num(row.cartons),
        cbm_per_unit: num(row.cbmPerUnit),
        unit_price: num(row.unitPrice),
        net_weight: num(row.netWeight),
        gross_weight: num(row.grossWeight),
      };
    });
    setSaving(true);
    try {
      await saveInvoice.mutateAsync({
        pi_number: number,
        lines: payload,
      });
      setEditing(false);
      setDraftLines([]);
      toast.success('Proforma invoice saved.');
    } catch {
      // The mutation hook already toasts the message; the edit stays open so the operator
      // can see which figure was refused rather than losing every other change with it.
    } finally {
      setSaving(false);
    }
  };

  const runConvert = async (
    args: {
      lineQuantities: Record<string, number>;
      containerSizeId: string | null;
      packingRowIds?: string[];
    } | null,
    reason?: string,
  ) => {
    setConvertArgs(args);
    try {
      const result = await convertToDraftShipment.mutateAsync({
        invoiceIds: [id],
        overrideReason: reason,
        lineQuantities: args?.lineQuantities,
        containerSizeId: args?.containerSizeId ?? null,
        packingRowIds: args?.packingRowIds,
      });
      setOverCapacity(null);
      setOverrideReason('');
      setConvertOpen(false);
      const skippedMsg =
        result.lines_skipped > 0
          ? ` (${result.lines_skipped} line${result.lines_skipped === 1 ? '' : 's'} could not be matched to a product and were skipped)`
          : '';
      // An invoice with nothing left to place is NAMED rather than quietly left out of the
      // count, so the operator can see which of their selection did not move (AC-F7).
      // The selected invoices named different containers, so the draft's header was left
      // blank rather than given one of them (AC-D2c). The server decides this, and it is
      // said out loud here rather than guessed at in the dialog beforehand.
      if (result.header_conflicts?.length) {
        toast.warning(
          `The invoices name different containers, so the container number, seal and bill of lading were left blank.`,
        );
      }
      if (result.skipped_invoices?.length) {
        toast.warning(
          `Not converted: ${result.skipped_invoices
            .map((i) => `${i.pi_number} - ${i.reason}`)
            .join('; ')}`,
        );
      }
      toast.success(
        `Packing list ${result.shipment_number ?? ''} created with ${result.lines_created} line${
          result.lines_created === 1 ? '' : 's'
        }${skippedMsg}`,
      );
      router.push(`/procurement-management/packing-lists/${result.shipment_id}`);
    } catch (e) {
      const code = (e as { code?: string | null })?.code ?? null;
      const message = e instanceof Error ? e.message : 'Failed to create the packing list';
      if (code === 'over_capacity') {
        setOverCapacity(message);
        return;
      }
      toast.error(message);
    }
  };

  const runExport = async () => {
    try {
      await downloadProformaInvoiceExport(id, data?.pi_number);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to export the proforma invoice');
    }
  };

  const columns = useMemo<ColumnDef<DraftLine>[]>(
    () => [
      {
        id: 'item_code',
        header: ({ column }) => <DataGridColumnHeader title="Item code" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (editing) {
            return (
              <Input
                value={line.itemCode}
                onChange={(e) => patchLine(line.key, { itemCode: e.target.value })}
                className="h-8"
                aria-label={`Item code for line ${row.index + 1}`}
                disabled={line.removed}
              />
            );
          }
          return (
            <span
              className={`truncate whitespace-pre-line ${line.removed ? 'line-through opacity-60' : ''}`}
              title={line.itemCode}
            >
              {line.itemCode}
            </span>
          );
        },
        size: 160,
        meta: { headerTitle: 'Item code' },
      },
      {
        id: 'product',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        /**
         * What this line IS, and the one place it is answered (S7).
         *
         * A line the supplier gave a code for is answered by a supplier-code RULING, not by
         * an edit: picking here writes "this code means that product or set" the moment it
         * is picked, re-binds every row already on file under the code, and holds for every
         * later upload. So the control is the same in read mode and in edit mode, and there
         * is no Match column beside it offering the same decision through a second door.
         *
         * A row the operator added by hand has no code to rule on, so it keeps the draft
         * behaviour it always had: pick in edit mode, written on Save.
         */
        cell: ({ row }) => {
          const line = row.original;
          const saved = line.source;
          // The code ON FILE, never the one being typed: an alias is written against a code
          // the invoice actually carries, and a half-typed one names nothing.
          const supplierCode = saved?.item_code?.trim() ?? '';
          const matchId = saved?.match_id ?? null;
          const value = line.productId
            ? line.productId
            : line.productSetId
              ? `${SET_OPTION_PREFIX}${line.productSetId}`
              : '';
          const label = line.productCode ?? saved?.set_code ?? null;

          if (!canPickProduct) {
            // AC-7.7: picking here edits the line and writes the supplier's ruling, so it
            // takes both permissions; short of either, the answer is read-only text.
            return label ? (
              <span className="truncate" title={label}>
                {label}
              </span>
            ) : (
              <span className="text-muted-foreground">{EM_DASH}</span>
            );
          }

          if (supplierCode) {
            // The countdown, only while there IS one: `forgetTargetId` alone still named
            // this row for a beat after Cancel, and the cell went blank with it.
            const forgettingThis =
              matchId && forgetTargetId === matchId ? forgetCountdownRef.current : null;
            if (forgettingThis) {
              return <>{forgettingThis}</>;
            }
            return (
              <SearchableSelect
                value={value}
                onChange={() => {}}
                onOptionChange={(opt) => {
                  if (opt) {
                    void writeCodeMatch(supplierCode, opt, line.key);
                    return;
                  }
                  // Cleared: the ruling is withdrawn, not corrected - the code goes back to
                  // whatever the ladder can work out on its own, after the window lapses.
                  if (matchId) {
                    forgetMatch({
                      id: matchId,
                      subject: `${supplierCode} means ${label ?? 'this product'}`,
                    });
                  }
                }}
                fetchOptions={fetchProductOrSet}
                renderOption={renderProductOrSetOption}
                paginated
                pageSize={PRODUCT_PAGE_SIZE}
                selectedOption={value && label ? { value, label } : undefined}
                placeholder="Search a product or set"
                emptyMessage="No product or set found."
                size="sm"
                truncateTriggerLabel
                // Only a ruling can be withdrawn: a line the ladder has not answered has
                // nothing to clear.
                clearable={!!matchId}
                disabled={matchPending || line.removed}
              />
            );
          }

          if (editing) {
            // A line matched to a SET (R19) has no `productId` - the select still shows
            // the match, keyed by the set's id, so a bound line never reads empty in edit
            // mode (AC-B2).
            return (
              // SERVER-SEARCHED and paginated: the catalogue is tens of thousands of rows,
              // and a picker holding one cached page silently hides the item being looked for.
              <SearchableSelect
                value={value}
                onChange={() => {}}
                onOptionChange={(opt) => {
                  const v = opt?.value ?? '';
                  const set = !!v && isSetOption(v);
                  // The option's own code, so a SET reads as its set code rather than
                  // keeping whatever product code was there before it. `productCodes` is
                  // still consulted for the UOM, which only a product has.
                  const code = opt?.code ?? null;
                  const known = v && !set ? productCodes.current.get(v) : undefined;
                  patchLine(line.key, {
                    productId: set || !v ? null : v,
                    productSetId: set ? v.slice(SET_OPTION_PREFIX.length) : null,
                    productCode: code ?? (v ? line.productCode : null),
                    // Only where the operator has not written one themselves: the supplier's
                    // own spelling is the document of record, and overwriting it would make
                    // our copy disagree with their paper. A hand-added row has no supplier
                    // spelling at all, and a line with no code cannot be saved.
                    ...(code && !line.itemCode.trim() ? { itemCode: code } : {}),
                    ...(known?.uom && !line.uom.trim() ? { uom: known.uom } : {}),
                  });
                }}
                fetchOptions={fetchProductOrSet}
                renderOption={renderProductOrSetOption}
                paginated
                pageSize={PRODUCT_PAGE_SIZE}
                selectedOption={value && label ? { value, label } : undefined}
                placeholder="Search a product or set"
                emptyMessage="No product or set found."
                size="sm"
                truncateTriggerLabel
                clearable
                disabled={line.removed}
              />
            );
          }
          return label ? (
            <span className="truncate" title={label}>
              {label}
            </span>
          ) : (
            <span className="text-muted-foreground">{EM_DASH}</span>
          );
        },
        size: 240,
        enableSorting: false,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'description',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <Input
              value={line.description}
              onChange={(e) => patchLine(line.key, { description: e.target.value })}
              className="h-8"
              aria-label={`Description for line ${row.index + 1}`}
              disabled={line.removed}
            />
          ) : (
            <span className="truncate" title={line.description || undefined}>
              {line.description || EM_DASH}
            </span>
          );
        },
        size: 200,
        enableSorting: false,
        meta: { headerTitle: 'Description' },
      },
      {
        id: 'description_en',
        header: ({ column }) => <DataGridColumnHeader title="Description (EN)" column={column} />,
        // Same cell in BOTH view and edit mode, same position: it is not part of the line
        // form, it writes the glossary directly (S2), so editing it never dirties the draft.
        // Keys on the SAVED description (`source`), never the unsaved draft one (review
        // round, 10 Sep) - a translation written against text that has not been Saved yet
        // could teach the memory a phrase that never actually lands on this line, and the
        // route's own "source_text must be on this invoice" check would 422 it anyway. A
        // line whose draft description has diverged from what is on file is read-only
        // here until Save reconciles the two.
        cell: ({ row }) => {
          const savedDescription = row.original.source?.description ?? null;
          const isDirty = row.original.description !== (savedDescription ?? '');
          return (
            <DescriptionEnCell
              invoiceId={id}
              description={savedDescription}
              descriptionEn={row.original.source?.description_en ?? null}
              canAdjust={canAdjust && !isDirty}
            />
          );
        },
        size: 200,
        // Matches its twin (`description` above, also `enableSorting: false`): an
        // editable inline cell is not something this grid sorts by (review round, 10 Sep).
        enableSorting: false,
        meta: { headerTitle: 'Description (EN)' },
      },
      {
        id: 'qty',
        header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
        // View and edit in the SAME place: the input replaces the number where the number
        // was, and the supplier's own figure sits under both so the comparison never moves.
        cell: ({ row }) => {
          const line = row.original;
          const supplierQty = line.source?.supplier_qty ?? null;
          const differs = supplierQty != null && String(supplierQty) !== line.qty;
          return (
            <div className="flex flex-col items-end gap-0.5">
              {editing ? (
                <Input
                  type="number"
                  min={0}
                  value={line.qty}
                  onChange={(e) => patchLine(line.key, { qty: e.target.value })}
                  className="h-8 w-24 text-right tabular-nums"
                  aria-label={`Quantity for ${line.itemCode || `line ${row.index + 1}`}`}
                  disabled={line.removed}
                />
              ) : (
                <span>{fmtQty(num(line.qty))}</span>
              )}
              {differs ? (
                <span className="text-2xs text-muted-foreground">
                  Supplier: {fmtQty(supplierQty)}
                </span>
              ) : null}
            </div>
          );
        },
        size: 130,
        enableSorting: false,
        meta: {
          headerTitle: 'Qty',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'packed',
        header: ({ column }) => <DataGridColumnHeader title="Packed" column={column} />,
        // The packed quantity, checked against the invoiced one (AC-B11). No packing
        // rows at all reads "-", not 0 - a line nobody has packed yet is not the same
        // fact as a line packed short.
        cell: ({ row }) => {
          const lineId = row.original.id;
          if (!lineId) return <span className="text-muted-foreground">{EM_DASH}</span>;
          const packed = packedQtyForLine(lineId, packingRowsRef.current);
          if (packed == null) return <span className="text-muted-foreground">{EM_DASH}</span>;
          const invoiced = num(row.original.qty);
          const mismatched = invoiced != null && packed !== invoiced;
          if (!mismatched) return fmtQty(packed);
          return (
            <Badge
              variant="destructive"
              appearance="light"
              title={`Invoiced ${fmtQty(invoiced)}, packed ${fmtQty(packed)}`}
            >
              {fmtQty(packed)}
            </Badge>
          );
        },
        size: 100,
        enableSorting: false,
        meta: {
          headerTitle: 'Packed',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'uom',
        header: ({ column }) => <DataGridColumnHeader title="UOM" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          if (editing) {
            const selectId = `pi-edit-line-${line.key}-uom`;
            return (
              <>
                {/* SearchableSelect forwards `id`, not arbitrary aria props - same pattern
                    as the Product select above and the UoM cell on the sales order. */}
                <label className="sr-only" htmlFor={selectId}>
                  UOM for {line.itemCode || `line ${row.index + 1}`}
                </label>
                <SearchableSelect
                  id={selectId}
                  value={line.uom}
                  onChange={(v: string) => patchLine(line.key, { uom: v })}
                  options={uomSelectOptions}
                  loadError={uoms.error}
                  onRetry={() => void uoms.refetch()}
                  placeholder="Search a UoM"
                  emptyMessage="No UoM found."
                  size="sm"
                  clearable
                  disabled={line.removed}
                />
              </>
            );
          }
          return (
            <span className="truncate" title={line.uom || undefined}>
              {line.uom || EM_DASH}
            </span>
          );
        },
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'UOM' },
      },
      {
        id: 'cartons',
        header: ({ column }) => <DataGridColumnHeader title="Cartons" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <Input
              type="number"
              min={0}
              value={line.cartons}
              onChange={(e) => patchLine(line.key, { cartons: e.target.value })}
              className="h-8 w-20 text-right tabular-nums"
              aria-label={`Cartons for ${line.itemCode || `line ${row.index + 1}`}`}
              disabled={line.removed}
            />
          ) : num(line.cartons) == null ? (
            EM_DASH
          ) : (
            fmtQty(num(line.cartons))
          );
        },
        size: 100,
        enableSorting: false,
        meta: {
          headerTitle: 'Cartons',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'cbm_per_unit',
        header: ({ column }) => <DataGridColumnHeader title="CBM / unit" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <Input
              type="number"
              min={0}
              step="0.001"
              value={line.cbmPerUnit}
              onChange={(e) => patchLine(line.key, { cbmPerUnit: e.target.value })}
              className="h-8 w-24 text-right tabular-nums"
              aria-label={`CBM per unit for ${line.itemCode || `line ${row.index + 1}`}`}
              disabled={line.removed}
            />
          ) : num(line.cbmPerUnit) == null ? (
            EM_DASH
          ) : (
            fmtTrimmedDecimal(num(line.cbmPerUnit), 3)
          );
        },
        size: 110,
        enableSorting: false,
        meta: {
          headerTitle: 'CBM / unit',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'cbm_total',
        header: ({ column }) => <DataGridColumnHeader title="Total CBM" column={column} />,
        // Derived here as well as on the server, from the same two figures, so the fill bar
        // and this cell cannot disagree while the quantity is being typed.
        cell: ({ row }) => {
          const line = row.original;
          const per = perUnitCbm(line);
          const shown = per == null ? line.source?.cbm_total ?? null : per * (num(line.qty) ?? 0);
          return shown == null ? EM_DASH : fmtTrimmedDecimal(shown, 2);
        },
        size: 100,
        enableSorting: false,
        meta: {
          headerTitle: 'Total CBM',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'unit_price',
        header: ({ column }) => <DataGridColumnHeader title="Unit price" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <Input
              type="number"
              min={0}
              step="0.01"
              value={line.unitPrice}
              onChange={(e) => patchLine(line.key, { unitPrice: e.target.value })}
              className="h-8 w-24 text-right tabular-nums"
              aria-label={`Unit price for ${line.itemCode || `line ${row.index + 1}`}`}
              disabled={line.removed}
            />
          ) : (
            fmtSupplierCost(num(line.unitPrice), data?.currency)
          );
        },
        size: 120,
        enableSorting: false,
        meta: {
          headerTitle: 'Unit price',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'amount',
        header: ({ column }) => <DataGridColumnHeader title="Amount" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          const price = num(line.unitPrice);
          const shown = price == null ? line.source?.amount ?? null : price * (num(line.qty) ?? 0);
          return fmtSupplierCost(shown, data?.currency);
        },
        size: 120,
        enableSorting: false,
        meta: {
          headerTitle: 'Amount',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'net_weight',
        header: ({ column }) => <DataGridColumnHeader title="Net wt" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <Input
              type="number"
              min={0}
              step="0.01"
              value={line.netWeight}
              onChange={(e) => patchLine(line.key, { netWeight: e.target.value })}
              className="h-8 w-20 text-right tabular-nums"
              aria-label={`Net weight for ${line.itemCode || `line ${row.index + 1}`}`}
              disabled={line.removed}
            />
          ) : num(line.netWeight) == null ? (
            EM_DASH
          ) : (
            fmtTrimmedDecimal(num(line.netWeight), 2)
          );
        },
        size: 100,
        enableSorting: false,
        meta: {
          headerTitle: 'Net wt',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'gross_weight',
        header: ({ column }) => <DataGridColumnHeader title="Gross wt" column={column} />,
        cell: ({ row }) => {
          const line = row.original;
          return editing ? (
            <Input
              type="number"
              min={0}
              step="0.01"
              value={line.grossWeight}
              onChange={(e) => patchLine(line.key, { grossWeight: e.target.value })}
              className="h-8 w-20 text-right tabular-nums"
              aria-label={`Gross weight for ${line.itemCode || `line ${row.index + 1}`}`}
              disabled={line.removed}
            />
          ) : num(line.grossWeight) == null ? (
            EM_DASH
          ) : (
            fmtTrimmedDecimal(num(line.grossWeight), 2)
          );
        },
        size: 100,
        enableSorting: false,
        meta: {
          headerTitle: 'Gross wt',
          headerClassName: 'text-right',
          cellClassName: 'text-right tabular-nums',
        },
      },
      {
        id: 'line_actions',
        header: '',
        // Marking, not deleting. The row stays on screen struck through until Save, and Undo
        // puts it back - so a mis-click costs nothing and one write carries the whole draft.
        cell: ({ row }) =>
          editing ? (
            <div className="flex items-center justify-end">
              {row.original.removed ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 gap-1 px-2 text-xs"
                  onClick={() => patchLine(row.original.key, { removed: false })}
                >
                  <Undo2 className="size-3.5" />
                  Undo
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 gap-1 px-2 text-xs text-destructive hover:text-destructive"
                  onClick={() => patchLine(row.original.key, { removed: true })}
                >
                  <Trash2 className="size-3.5" />
                  Remove
                </Button>
              )}
            </div>
          ) : null,
        size: 110,
        enableHiding: false,
        enableSorting: false,
      },
    ],
    // `forgetMatch`, not `matchForgetting` - see the note where it is destructured above.
    [
      id,
      data?.currency,
      editing,
      canAdjust,
      canPickProduct,
      fetchProductOrSet,
      forgetMatch,
      forgetTargetId,
      matchPending,
      writeCodeMatch,
      uomSelectOptions,
    ],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.key,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  // Back carries the list query the row click wrote (S3-01). It lives on the
  // toolbar row now; the empty states below keep one of their own.
  const backLink = (
    <BackToList listPath="/scm/proforma-invoices" label="Back to proforma invoices" />
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="flex justify-end">{backLink}</div>
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="space-y-4">
        <div className="flex justify-end">{backLink}</div>
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <FileText className="size-6 text-muted-foreground" />
          <div className="text-sm font-semibold">Proforma invoice not found</div>
          <p className="max-w-md text-sm text-muted-foreground">
            This proforma invoice doesn&apos;t exist, or it was deleted. Head back to the list to
            pick another.
          </p>
        </Card>
      </div>
    );
  }

  const invoice: ProformaInvoiceDetailPayload = data;
  const convertLabel =
    invoice.placement === 'split' ? 'Convert the rest' : 'Convert to packing list';
  const showConvert = !fullyPlaced && !superseded && canConvert;
  const deleteBlockedReason = converted
    ? `Already converted to ${invoice.converted_shipments.map((s) => s.shipment_number ?? 'a packing list').join(', ')}`
    : undefined;

  // The files the record actually LINKS (S8), and the one it can only name. A workbook
  // whose filing failed leaves `source_ref` - the name the apply stamped - with no
  // attachment behind it, so that row is still printed, without buttons it cannot honour.
  // The packing workbook needs no such fallback: `packing_file` is DERIVED from these same
  // links server-side, so a packing file with no link cannot reach this screen at all.
  //
  // D1 (PLAN-pi-header-fields-convert-fixes-24sep.md): shown only when there is NO linked
  // source file at all - matched on whether a link EXISTS, never on whether its name
  // happens to equal `source_ref`'s. `sanitize_storage_filename` strips characters like
  // `（1）` on the way into the link's own name, so the same upload's link and its raw
  // `source_ref` can legitimately spell the name two different ways (AC-S1) - a name
  // comparison read that as "two different files" and printed the one upload twice.
  const sourceFiles = invoice.source_files ?? [];
  const unlinkedSourceRef =
    invoice.source_ref && sourceFiles.length === 0 ? invoice.source_ref : null;

  /** The placement, as one chip in the header - "Not converted", "Split", or the container. */
  const placementBadge =
    invoice.placement === 'not_converted' ? (
      <Badge variant="secondary" appearance="light">
        Not converted
      </Badge>
    ) : (
      <Badge variant={invoice.placement === 'split' ? 'warning' : 'success'} appearance="light">
        {invoice.placement === 'split'
          ? `Split - ${fmtQty(invoice.remaining_qty)} still to place`
          : `In ${invoice.packing_lists.map((p) => p.shipment_number ?? 'a packing list').join(', ')}`}
      </Badge>
    );

  return (
    <div className="space-y-4">
      {/* The record header - what the invoice IS, and what can be done to it. Above the
          tabs, because it belongs to the whole record rather than to any one of its
          concerns. Read-only provenance lives in the meta line, never inside a tab body. */}
      <Card>
        <CardHeader className="block py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex min-w-0 flex-wrap items-center gap-3">
                <CardTitle className="text-lg">{invoice.pi_number}</CardTitle>
                {invoice.currency ? <Badge variant="secondary">{invoice.currency}</Badge> : null}
                {invoice.revision_count > 1 ? (
                  <Badge variant="secondary" appearance="light">
                    Revision {invoice.revision_no} of {invoice.revision_count}
                  </Badge>
                ) : null}
                {superseded ? (
                  <Badge variant="secondary" appearance="light">
                    Superseded
                  </Badge>
                ) : null}
                {placementBadge}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {/* Ours is the title; the supplier's own reference (S1, AC-A5) is the
                    first fact under it - what she matches against the paper on her desk. */}
                {invoice.supplier_ref ?? 'no supplier reference'}
                {' · '}
                {invoice.source_ref ?? 'No source file'}
                {' · '}
                Uploaded by {invoice.uploaded_by ?? 'unknown'} on {fmtDate(invoice.created_at)}
                {invoice.adjusted_by
                  ? ` · Adjusted by ${invoice.adjusted_by} on ${fmtDate(invoice.adjusted_at)}`
                  : ''}
              </p>
            </div>

            {/* In an edit session the header states ONE intent: Save or Cancel. Nav and the
                way out act on the invoice as it is STORED, and offering them over a screen
                full of unsaved changes is offering to act on a document nobody is reading. */}
            {editing ? (
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  Nothing is written until you press Save.
                </span>
                <Button variant="outline" size="sm" onClick={cancelEdit} disabled={saving}>
                  Cancel
                </Button>
                <Button size="sm" onClick={() => void saveEdit()} disabled={saving}>
                  {saving ? <LoaderCircle className="me-2 size-4 animate-spin" /> : null}
                  Save proforma invoice
                </Button>
              </div>
            ) : (
              <DetailActions
                pager={{
                  ...proformaInvoicesPagerQuery,
                  detailPath: '/scm/proforma-invoices',
                  currentId: id,
                  ariaLabel: 'proforma invoice',
                }}
                gearLabel="Proforma invoice options"
                pendingAction={deletion.countdown}
                gear={
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="icon" aria-label="More actions">
                      <Settings className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {canEdit ? (
                      <DropdownMenuItem onClick={beginEdit}>
                        <SquarePen className="size-4" />
                        Edit
                      </DropdownMenuItem>
                    ) : null}
                    <DropdownMenuItem onClick={() => void runExport()}>
                      <Download className="size-4" />
                      Export adjusted PI
                    </DropdownMenuItem>
                    {canAdjust && !superseded && !invoice.revision_of_pi_number ? (
                      <DropdownMenuItem onClick={() => setRevisionOpen(true)}>
                        <GitBranch className="size-4" />
                        Mark as revision of
                      </DropdownMenuItem>
                    ) : null}
                    {canAdjust ? (
                      <>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className="text-destructive"
                          disabled={converted}
                          // A disabled item's reason travels as a native `title` - Radix has
                          // no room for a Tooltip wrapper here, and a control that refuses
                          // without saying why reads as a defect.
                          title={deleteBlockedReason}
                          onClick={converted ? undefined : () => deletion.start()}
                        >
                          <Trash2 className="size-4" />
                          Delete invoice
                        </DropdownMenuItem>
                      </>
                    ) : null}
                  </DropdownMenuContent>
                </DropdownMenu>
                }
                primary={
                  <>
                {/* The main action on this page, so it wears the main colour. Everything
                    else is a secondary action and lives in the menu beside it. */}
                {showConvert ? (
                  <Button
                    variant="primary"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => setConvertOpen(true)}
                    disabled={convertToDraftShipment.isPending}
                  >
                    <Boxes className="size-4" />
                    {convertLabel}
                  </Button>
                ) : null}
                  </>
                }
              />
            )}
          </div>
        </CardHeader>
      </Card>

      {/* One tab per concern of the invoice, the same shape as the purchase-order screen.
          The tab set is the SAME in view and in edit - editing swaps a value for an input
          inside the tab it already lived in. */}
      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <TabsList variant="line" className="mb-4 w-full justify-start">
          <TabsTrigger value="general">
            <FileText />
            <span>General</span>
          </TabsTrigger>
          <TabsTrigger value="lines">
            <ListOrdered />
            <span>Lines</span>
          </TabsTrigger>
          <TabsTrigger value="packing">
            <PackageSearch />
            <span>Packing</span>
          </TabsTrigger>
          <TabsTrigger value="revisions">
            <GitBranch />
            <span>Revisions</span>
          </TabsTrigger>
          <TabsTrigger value="packing-lists">
            <PackageCheck />
            <span>Packing lists</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="mt-0 space-y-4 focus-visible:outline-none">
          {/* Three cards, each at most two columns of label/value - the three things a
              person asks about this document separately: what it is, whose it is, and
              whether it fits. Each is named as a region so a reader (and a test) can
              address one. */}
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Invoice</CardTitle>
              </CardHeading>
            </CardHeader>
            <section aria-label="Invoice" className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              {/* First field, and correctable: a derived number (`PI-<file stem>-<block>`)
                  is a guess the file forced on us, and the person holding the paper knows
                  what the supplier actually called it. */}
              <Field label="PI number" htmlFor={editing ? 'pi-number' : undefined}>
                {editing ? (
                  <Input
                    id="pi-number"
                    value={draftNumber}
                    onChange={(e) => setDraftNumber(e.target.value)}
                    maxLength={100}
                    className="h-8"
                  />
                ) : (
                  invoice.pi_number
                )}
              </Field>
              <Field label="Invoice date">{fmtDate(invoice.invoice_date)}</Field>
              {/* A3 (PLAN-pi-header-fields-convert-fixes-24sep.md), SO added by R-E (owner
                  ruling 25 Sep): Container, Seal, BL, SO, Consignee in that order - the
                  same five facts, same order, the converted packing list shows (H1/H2). */}
              <Field label="Container">{invoice.container_no ?? EM_DASH}</Field>
              <Field label="Seal">{invoice.seal_no ?? EM_DASH}</Field>
              <Field label="BL">{invoice.bl_no ?? EM_DASH}</Field>
              <Field label="SO">{invoice.so_no ?? EM_DASH}</Field>
              <Field label="Consignee">{invoice.consignee ?? EM_DASH}</Field>
              <Field label="Currency">{invoice.currency ?? EM_DASH}</Field>
              <Field label="Total">
                {fmtSupplierCost(invoice.total_amount, invoice.currency)}
              </Field>
              <Field label="Lines">{invoice.line_count}</Field>
            </section>
          </Card>

          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Supplier</CardTitle>
              </CardHeading>
            </CardHeader>
            <section aria-label="Supplier" className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              <Field label="Supplier">{invoice.supplier_name ?? EM_DASH}</Field>
              <Field label="Supplier code">{invoice.supplier_code ?? EM_DASH}</Field>
            </section>
          </Card>

          {/* No container size, gauge or "over by" here (S5, ruling 1): capacity is a
              property of the CONTAINER this invoice's goods may end up sharing with
              several others, chosen on the convert dialog. This is the number Ms Tee adds
              up across invoices to decide which of them share a box. */}
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Total volume</CardTitle>
              </CardHeading>
            </CardHeader>
            <section aria-label="Total volume" className="p-4">
              <p className="text-sm font-medium">
                {volume.total === null
                  ? 'No volume on this invoice'
                  : `${fmtTrimmedDecimal(volume.total, 2)} cbm`}
              </p>
              {volume.unmeasured > 0 ? (
                <p className="mt-1 text-2xs text-muted-foreground">
                  {volume.unmeasured} unmeasured {volume.unmeasured === 1 ? 'line' : 'lines'}
                </p>
              ) : null}
            </section>
          </Card>

          {/* The PI's own uploaded files (S2, AC-B14) - never the shipment's, which has
              its own Documents tab for what a converted draft carries. */}
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Source files</CardTitle>
              </CardHeading>
            </CardHeader>
            <section aria-label="Source files" className="space-y-2 p-4">
              {sourceFiles.length === 0 && !unlinkedSourceRef ? (
                <p className="text-sm text-muted-foreground">No source file on record.</p>
              ) : (
                <>
                  {/* A linked file is the same row the packing list's own Documents card
                      draws (S8): openable and downloadable, never merely named. Nothing is
                      detached from here - the link IS what the invoice was read from. */}
                  {sourceFiles.map((file) => (
                    <AttachmentFileCard
                      key={file.id}
                      attachmentId={file.attachment_id}
                      name={file.name ?? 'Source file'}
                      typeLabel={file.type}
                      sizeBytes={file.file_size_bytes ?? null}
                    />
                  ))}
                  {/* A file uploaded before the link existed is still named here, with the
                      date it arrived - there is no attachment to offer buttons for. */}
                  {unlinkedSourceRef ? (
                    <div className="rounded-lg border p-3">
                      <p className="truncate text-sm font-medium" title={unlinkedSourceRef}>
                        {unlinkedSourceRef}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Proforma invoice • {fmtDate(invoice.created_at)}
                      </p>
                    </div>
                  ) : null}
                </>
              )}
            </section>
          </Card>
        </TabsContent>

        <TabsContent value="lines" className="mt-0 space-y-4 focus-visible:outline-none">
          {/* Lines - always rendered, explicit empty state. */}
          <DataGrid
            table={table}
            recordCount={rows.length}
            isLoading={false}
            tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
            emptyMessage="This proforma invoice has no lines."
            listingKey={LINES_LISTING_KEY}
          >
            <Card>
              <CardHeader>
                <CardHeading>
                  <CardTitle>Invoice lines</CardTitle>
                </CardHeading>
                <CardToolbar>
                  {superseded ? (
                    <span className="text-xs text-muted-foreground">
                      A superseded revision is read-only.
                    </span>
                  ) : converted ? (
                    <span className="text-xs text-muted-foreground">
                      Already in a packing list, so the quantities are fixed.
                    </span>
                  ) : null}
                </CardToolbar>
              </CardHeader>
              <CardTable>
                <DataGridTable />
              </CardTable>
            </Card>
          </DataGrid>
          {editing ? (
            <Button variant="outline" size="sm" className="gap-1.5" onClick={addLine}>
              <Plus className="size-4" />
              Add line
            </Button>
          ) : null}
        </TabsContent>

        <TabsContent value="packing" className="mt-0 focus-visible:outline-none">
          {/* The supplier's packing list as filed (S2, AC-B9) - always rendered, with an
              explicit empty state: the packing list arriving days after the invoice is
              the normal case (Kailu), not an error. */}
          <ProformaInvoicePackingTab
            invoice={invoice}
            canAdjust={canAdjust}
            onAttach={() => setPackingUploadOpen(true)}
            onReplace={() => setPackingUploadOpen(true)}
          />
        </TabsContent>

        <TabsContent value="revisions" className="mt-0 focus-visible:outline-none">
          {/* Always rendered with its own empty state, per the CRUD standard. */}
          <ProformaRevisionsCard invoice={invoice} />
        </TabsContent>

        <TabsContent value="packing-lists" className="mt-0 focus-visible:outline-none">
          {/* Which containers this invoice's goods went into (ruling 26). One row per
              packing list and nothing else: what is still to place is the convert dialog's
              own table, and why a line cannot go is the Lines tab's Matched column. */}
          <ProformaInvoicePackingListsTab invoice={invoice} />
        </TabsContent>
      </Tabs>

      <MarkAsRevisionDialog
        open={revisionOpen}
        onOpenChange={setRevisionOpen}
        invoice={invoice}
      />

      <OverCapacityDialog
        message={overCapacity}
        reason={overrideReason}
        onReasonChange={setOverrideReason}
        onCancel={() => setOverCapacity(null)}
        onConfirm={() => void runConvert(convertArgs, overrideReason.trim())}
        pending={convertToDraftShipment.isPending}
      />

      {/* How much of this invoice goes onto a container (AC-F10, Q9). Always a NEW draft
          packing list: "add to an existing draft" is gone everywhere (Q6). */}
      <ConvertToPackingListDialog
        open={convertOpen}
        onOpenChange={setConvertOpen}
        invoiceIds={[id]}
        pending={convertToDraftShipment.isPending}
        onConvert={(args) => void runConvert(args)}
      />

      {/* Attach / Replace packing list (S2, AC-B10) - the shared dialog, opened with the
          supplier and THIS invoice preselected and locked. */}
      <SupplierDocumentsUploadDialog
        open={packingUploadOpen}
        onOpenChange={setPackingUploadOpen}
        supplierId={invoice.supplier_id}
        supplierName={invoice.supplier_name}
        attachTo={{ id: invoice.id, pi_number: invoice.pi_number }}
        onImported={() => {
          // The apply wrote the rows onto this invoice; read it again so the tab, the
          // Packed column and the Source files block all show what landed.
          packingMutations.refresh();
        }}
      />
    </div>
  );
}

export default ProformaInvoiceDetail;
