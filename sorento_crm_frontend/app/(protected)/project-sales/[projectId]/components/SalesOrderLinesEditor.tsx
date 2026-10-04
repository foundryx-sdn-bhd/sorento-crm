'use client';

import * as React from 'react';
import { Badge } from '@/components/ui/badge';
import { useUOMSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query';
import { getProductsForVariantSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import { formatDateInMalaysia } from '@/lib/helpers';
import {
  InlineLineTable,
  type InlineDraft,
  type InlineLineColumn,
  type InlineStaging,
} from '../../_shared/components/InlineLineTable';
import { multiplyMoney, sumMoney } from '../../_shared/lib/money';
import {
  buildFlagItems,
  leadFlagItem,
  FINDING_SEVERITY_BADGE_VARIANT,
  FINDING_SEVERITY_LABEL,
  type FlagItem,
} from '../../_shared/lib/findings';
import type {
  ProjectSalesOrderFinding,
  ProjectSalesOrderLine,
  StagedSalesOrderLine,
} from '../../_shared/types/projectSalesOrder.types';
import { formatMoney, formatQty, formatUnitPrice } from './SalesOrderMoney';
import { salesOrderLineErrors } from '../sales-orders/[psoId]/components/useSalesOrderEditSession';

/**
 * One row of the editor: the key it is drawn under, and the stored line behind it.
 *
 * The same shape the quotation editor uses, for the same reason: a line added in this session
 * has no stored row, and every column that draws a fact only the server can decide (the area
 * label, the PO line it exploded from, its findings) reads it from here and renders nothing
 * while it is null.
 */
export type SalesOrderEditorRow = { id: string; line: ProjectSalesOrderLine | null };

/** What the detail screen hands down so the lines can be edited without writing anything. */
export type SalesOrderLinesEditing = {
  staged: StagedSalesOrderLine[] | null;
  seed: (lines: StagedSalesOrderLine[]) => void;
  stage: (lines: StagedSalesOrderLine[]) => void;
  toggleRemoved: (key: string) => void;
};

export function emptySalesOrderLineDraft(): InlineDraft {
  return {
    product_id: '',
    description: '',
    qty: '1',
    uom: '',
    unit_price: '0',
    delivery_date: '',
    stock_location: '',
  };
}

export function salesOrderLineToDraft(line: ProjectSalesOrderLine): InlineDraft {
  return {
    product_id: line.product_id ?? '',
    description: line.description ?? '',
    qty: line.qty ?? '',
    uom: line.uom ?? '',
    unit_price: line.unit_price ?? '0',
    delivery_date: line.delivery_date ?? '',
    stock_location: line.stock_location ?? '',
  };
}

/**
 * The lines of one sales order draft, as a spreadsheet.
 *
 * WHY THIS IS NOT THE DATAGRID BESIDE IT: `SalesOrderLinesTable` is the READ, and it earns
 * its DataGrid - 99 lines, pagination, resizable columns, the drag handles. An
 * editor wants the opposite three things (an unsaved row, a cell that gives its whole width to
 * an input, a header that survives an empty table), which is exactly the trade
 * `InlineLineTable` was written for and is documented at the top of that file. So this is the
 * shared editor, given the SAME columns in the SAME order as the read table declares, and the
 * two live in one section (`SalesOrderLinesTable` renders this one while a session is open) so
 * nothing moves between the read and the edit.
 *
 * Nothing here writes. Every change is reported up to the screen's edit session, and the one
 * Save in the header turns the whole set into one request.
 */
export function SalesOrderLinesEditor({
  lines,
  findings = [],
  flagItems,
  editing,
  reference,
}: {
  lines: ProjectSalesOrderLine[];
  findings?: ProjectSalesOrderFinding[];
  /** The read table's Flag items; absent, built from `findings` alone. */
  flagItems?: FlagItem[];
  /** Absent, the table is a plain read: no inputs, no add row, nothing to press. */
  editing?: SalesOrderLinesEditing | null;
  /** Names the order in the row labels, so a screen reader hears which document it is. */
  reference?: string;
}) {
  const isEditing = Boolean(editing);
  const staged = editing?.staged ?? null;

  const uoms = useUOMSelectQuery();
  const uomOptions = React.useMemo(
    () =>
      (uoms.data ?? []).map((unit) => ({
        value: unit.uom_code,
        label: unit.uom_code,
        description: unit.uom_name,
      })),
    [uoms.data],
  );

  /**
   * The products the picker has shown, by id. The dropdown already holds the record when
   * somebody chooses one, so the trigger reads its code from here rather than asking the
   * server again for a row it just rendered.
   */
  const productsById = React.useRef(new Map<string, { code: string; name: string }>());

  const fetchProducts = React.useCallback(async (query: string) => {
    const products = await getProductsForVariantSelect(query || undefined);
    products.forEach((product) =>
      productsById.current.set(product.id, {
        code: product.product_code,
        name: product.product_name ?? '',
      }),
    );
    return products.map((product) => ({
      value: product.id,
      label: product.product_code,
      description: product.product_name ?? undefined,
    }));
  }, []);

  /** The most severe open item per line: the read table's Flag pill, without its popover. */
  const flagByLine = React.useMemo(() => {
    const byLine = new Map<string, FlagItem[]>();
    (flagItems ?? buildFlagItems(findings, [])).forEach((item) => {
      if (item.lineId) byLine.set(item.lineId, [...(byLine.get(item.lineId) ?? []), item]);
    });
    const map = new Map<string, FlagItem>();
    byLine.forEach((items, lineId) => {
      const lead = leadFlagItem(items);
      if (lead) map.set(lineId, lead);
    });
    return map;
  }, [findings, flagItems]);

  /**
   * The columns, in the printed order the read table uses: item number, what it is, how much,
   * what it comes to, when it is due. `SalesOrderLinesTable` declares the same headers in the
   * same order, and `SalesOrderLinesEditor.test.tsx` asserts the two lists agree - the read
   * view is what teaches somebody where a field is, so an edit that reshuffled them would make
   * every edit start with re-finding the cell.
   *
   * No Area, From PO line or Stock location (owner hand test, PR #1264 notes 1 and 3): the
   * first two are the schedule's facts, not this table's, and the stock location is derived
   * from the sales agent's location group and stated once in the header. The draft still
   * carries the line's stored `stock_location`, so a save never blanks it.
   */
  const columns = React.useMemo<InlineLineColumn<SalesOrderEditorRow>[]>(
    () => [
      {
        key: 'line_no',
        header: '#',
        width: 60,
        // The row's position, not a box to type in: the backend renumbers by array order on
        // every save, so a typed number would disagree with the document within one press.
        kind: 'derived',
        derive: (_draft, index) => index + 1,
      },
      {
        key: 'product_id',
        header: 'Product',
        width: 200,
        kind: 'searchable-select',
        // Optional on purpose: an unresolved line is a real state on a drafted order, and the
        // `unresolved_product` finding is what says so. Forcing a match here would mean
        // either a wrong product or an unrecordable line.
        placeholder: 'Not resolved',
        fetchOptions: fetchProducts,
        resolveSelected: (row, draft) => {
          const productId = draft.product_id;
          if (!productId) return undefined;
          const picked = productsById.current.get(productId);
          if (picked) {
            return { value: productId, label: picked.code, description: picked.name };
          }
          // Read off the STORED row only while the draft still names the same product: after
          // a pick the trigger would otherwise keep naming the product that was there before.
          if (row?.line?.product_id === productId && row.line.product_code) {
            return { value: productId, label: row.line.product_code };
          }
          return { value: productId, label: 'Selected product' };
        },
      },
      {
        key: 'flag',
        header: 'Flag',
        width: 190,
        // Read-only while editing: a Dismiss writes at once, and an edit session writes
        // nothing until Save. The pill still says which line holds Publish.
        kind: 'derived',
        derive: (_draft, _index, row) => {
          const item = row?.line ? flagByLine.get(row.line.id) : undefined;
          if (!item) return <span className="text-muted-foreground">-</span>;
          return (
            <Badge variant={FINDING_SEVERITY_BADGE_VARIANT[item.severity]} appearance="light" size="sm">
              {FINDING_SEVERITY_LABEL[item.severity]}
            </Badge>
          );
        },
      },
      {
        key: 'description',
        header: 'Description',
        width: 300,
        kind: 'text',
        placeholder: 'As it prints on the sales order',
      },
      {
        key: 'qty',
        header: 'Qty',
        width: 90,
        kind: 'number',
        align: 'end',
        formatReadOnly: (value) => formatQty(value),
      },
      {
        key: 'uom',
        header: 'UOM',
        width: 100,
        // The same dropdown the quotation and PO editors use: "unit" and "UNIT" are one unit
        // to a reader and two strings to every report.
        kind: 'select',
        placeholder: 'UNIT',
        options: uomOptions,
        loadError: uoms.error,
        onRetry: () => void uoms.refetch(),
        resolveSelected: (_row, draft) =>
          uomOptions.find((option) => option.value === draft.uom),
      },
      {
        key: 'unit_price',
        header: 'Unit price',
        width: 120,
        kind: 'number',
        align: 'end',
        prefix: 'RM',
        placeholder: '0.00',
        formatReadOnly: (value) => formatUnitPrice(value),
      },
      {
        key: 'amount',
        header: 'Amount',
        width: 140,
        // Always qty x unit price. The backend computes the same product and refuses a third
        // number, so there is nothing to type here.
        kind: 'derived',
        align: 'end',
        derive: (draft) => formatMoney(multiplyMoney(draft.qty, draft.unit_price) ?? '0'),
        footer: (drafts) =>
          formatMoney(
            sumMoney(drafts.map((draft) => multiplyMoney(draft.qty, draft.unit_price))) ??
              '0',
          ),
      },
      {
        key: 'delivery_date',
        header: 'Delivery',
        width: 160,
        // The shared date picker: dd/mm/yyyy display and typed entry, calendar popover to
        // pick instead. The draft still holds the ISO `YYYY-MM-DD` string the API wants.
        kind: 'date',
        placeholder: 'DD/MM/YYYY',
        formatReadOnly: (value) => formatDateInMalaysia(value),
      },
    ],
    [fetchProducts, flagByLine, uomOptions, uoms.error, uoms.refetch],
  );

  const sortedLines = React.useMemo(
    () => [...lines].sort((a, b) => a.line_no - b.line_no),
    [lines],
  );

  /**
   * The starting point, taken from the server's rows the first time the session opens. Seeded
   * through the screen and never held here, so the staged work survives this component
   * unmounting (a findings filter, a refetch, the table remounting on Cancel).
   */
  const seed = editing?.seed;
  React.useEffect(() => {
    if (!seed || !isEditing || staged !== null) return;
    seed(
      sortedLines.map((line) => ({
        id: line.id,
        key: line.id,
        line,
        draft: salesOrderLineToDraft(line),
        removed: false,
      })),
    );
  }, [isEditing, seed, sortedLines, staged]);

  const rows = React.useMemo<SalesOrderEditorRow[]>(
    () =>
      isEditing && staged
        ? staged.map((line) => ({ id: line.key, line: line.line }))
        : sortedLines.map((line) => ({ id: line.id, line })),
    [isEditing, sortedLines, staged],
  );

  const stagedByKey = React.useMemo(() => {
    const map = new Map<string, StagedSalesOrderLine>();
    (staged ?? []).forEach((line) => map.set(line.key, line));
    return map;
  }, [staged]);

  const toDraft = React.useCallback(
    (row: SalesOrderEditorRow): InlineDraft =>
      // The staged draft when there is one, so what was typed is what comes back rather than
      // the server's untouched row.
      stagedByKey.get(row.id)?.draft ??
      (row.line ? salesOrderLineToDraft(row.line) : emptySalesOrderLineDraft()),
    [stagedByKey],
  );

  const stage = editing?.stage;
  const toggleRemoved = editing?.toggleRemoved;
  const staging = React.useMemo<InlineStaging<SalesOrderEditorRow> | undefined>(() => {
    if (!isEditing || !stage || !toggleRemoved) return undefined;
    return {
      onChange: (reported) =>
        stage(
          reported.map(({ rowKey, draft }) => {
            // A key the session has not seen is a row the table has just added: no id, no
            // stored line, and the whole-document write reads it as new.
            const held = stagedByKey.get(rowKey);
            return {
              id: held?.id ?? null,
              key: rowKey,
              line: held?.line ?? null,
              draft,
              removed: held?.removed ?? false,
            };
          }),
        ),
      isRemoved: (row) => stagedByKey.get(row.id)?.removed ?? false,
      toggleRemove: (rowKey) => toggleRemoved(rowKey),
    };
  }, [isEditing, stage, stagedByKey, toggleRemoved]);

  return (
    <InlineLineTable<SalesOrderEditorRow>
      /**
       * A fresh table when the session opens and again when it closes. The table keeps its own
       * drafts and refuses to overwrite a dirty row from `rows`, which is what stops a
       * neighbour's refetch wiping what somebody is typing - and also means Cancel could not
       * put a row back. Remounting is the honest reset.
       */
      key={isEditing ? 'editing' : 'reading'}
      rows={rows}
      getRowId={(row) => row.id}
      columns={columns}
      toDraft={toDraft}
      emptyDraft={emptySalesOrderLineDraft}
      readOnly={!isEditing}
      addLabel="Add a line"
      staging={staging}
      validateRow={salesOrderLineErrors}
      emptyHint={
        isEditing
          ? 'No lines. Add one, or cancel and rebuild the draft from the purchase order.'
          : 'This draft has no lines. Rebuild it from the purchase order and its delivery schedule.'
      }
      describeRow={(row, index) =>
        row?.line?.product_code ??
        row?.line?.description ??
        `line ${index + 1}${reference ? ` on ${reference}` : ''}`
      }
    />
  );
}
