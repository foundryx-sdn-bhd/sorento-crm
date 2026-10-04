'use client';

import * as React from 'react';
import { AlertTriangle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
// The shared products `/select` mapper. Its name says "variant" because that screen
// needed it first; the endpoint and the shape are the generic ones.
import { useUOMSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query';
import { getProductsForVariantSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import { usePurchaseOrderLines } from '../../_shared/hooks/useProjects';
import type {
  Project,
  ProjectPurchaseOrder,
  PurchaseOrderLine,
  PurchaseOrderLineBulkItem,
  StagedPurchaseOrderLine,
} from '../../_shared/types/project.types';
import {
  InlineLineTable,
  type InlineDraft,
  type InlineLineColumn,
  type InlineStaging,
} from '../../_shared/components/InlineLineTable';
import { formatMyr } from './QuotationsPanel';
import { formatMyrExact, isDecimalString, multiplyMoney, sumMoney } from '../../_shared/lib/money';

/**
 * A row of the line table: the stored line, or a stand-in for one added in this edit session.
 *
 * `id` is the row's identity - the line's own id when it has one, the staged key when it does
 * not - so one set of columns serves both the read and the edit.
 */
type LineRow = { id: string; line: PurchaseOrderLine | null };

/** A stored line as an editable draft. Every field the save sends has to be in here. */
function serverToDraft(line: PurchaseOrderLine): InlineDraft {
  return {
    product_id: line.product_id ?? '',
    product_code: line.product_code ?? '',
    description: line.description ?? '',
    quantity: line.quantity ?? '1',
    uom: line.uom ?? '',
    unit_price: line.unit_price ?? '',
    notes: line.notes ?? '',
  };
}

/** A brand-new row's starting draft. Also the table's "untouched" comparison. */
function emptyDraft(): InlineDraft {
  return {
    product_id: '',
    product_code: '',
    description: '',
    quantity: '1',
    uom: '',
    unit_price: '',
    notes: '',
  };
}

/**
 * The one rule a PO line has to satisfy before it can be stored.
 *
 * A line with no matched product carries the contractor's own code, so that is the one thing
 * that cannot be left out. Written once and used twice - to mark the cell at fault, and to stop
 * a Save that would only come back as a 422 - so the desk and the button cannot disagree about
 * what is wrong.
 */
function lineErrors(draft: InlineDraft): Record<string, string> {
  return !draft.product_id && !draft.product_code.trim()
    ? { product_code: 'Needed when no product is matched' }
    : {};
}

/** Staged lines that are not ready to be written. Counted so a refusal can say how many. */
export function unfinishedStagedPoLines(lines: StagedPurchaseOrderLine[]): number {
  return lines.filter(
    (line) => !line.removed && Object.keys(lineErrors(line.draft)).length > 0,
  ).length;
}

/**
 * A staged set as the body of the whole-PO save.
 *
 * Removed lines are simply left out, which is exactly how the endpoint deletes them, and a line
 * added in this session carries no `id` so the server reads it as new. Order is array position;
 * `sort_order` is not sent at all. Exported because the SAVE lives on the PO's page: one button
 * covers the header and the lines, so it cannot live inside the line table.
 */
export function stagedPoLinesToBody(
  lines: StagedPurchaseOrderLine[],
): PurchaseOrderLineBulkItem[] {
  return lines
    .filter((line) => !line.removed)
    .map((line) => (line.id ? { id: line.id, ...toBody(line.draft) } : toBody(line.draft)));
}

/**
 * What the PO comes to, off the LIVE drafts, by the same rule the table's own footer applies.
 * Exported so the header card can state the figure the screen currently shows rather than the
 * one the server last stored.
 */
export function stagedPoLinesTotal(lines: StagedPurchaseOrderLine[]): string | null {
  return totalFromDrafts(lines.filter((line) => !line.removed).map((line) => line.draft));
}

/**
 * What the PO's page hands down so the lines can be edited without writing anything.
 *
 * Its presence IS edit mode. Absent, the table is a clean read: no inputs, no per-row saves,
 * nothing to press by accident. The client's complaint on the quotation was the same table's
 * fault - "every addition of line doesn't trigger a save ... very annoying" - and the answer is
 * the same: a view that is a view, plus an Edit that puts the whole record into one staged
 * session.
 */
export type PurchaseOrderLinesEditing = {
  /** The staged lines, or null until they have been seeded from the server's rows. */
  staged: StagedPurchaseOrderLine[] | null;
  seed: (lines: StagedPurchaseOrderLine[]) => void;
  stage: (lines: StagedPurchaseOrderLine[]) => void;
  toggleRemoved: (key: string) => void;
};

/**
 * The lines of one PO, entered like a spreadsheet, with what each one was quoted at
 * beside what was ordered.
 *
 * Showing both numbers on the same row is the point: "price differs" on its own sends
 * the user hunting through the quotation, and the difference is usually the thing they
 * want to talk to the contractor about within the next ten minutes.
 *
 * With no `edit` it is a READ. Every field is text, there is nothing to add and nothing to save,
 * because a screen that saves on blur cannot also be the screen somebody reads a PO on.
 */
export function PurchaseOrderLinesEditor({
  project,
  po,
  edit,
}: {
  project: Project;
  po: ProjectPurchaseOrder;
  /** Set by the PO's page while its edit session is open. See `PurchaseOrderLinesEditing`. */
  edit?: PurchaseOrderLinesEditing | null;
}) {
  const lines = usePurchaseOrderLines(po.id);

  const rows = React.useMemo(
    () => [...(lines.data ?? [])].sort((a, b) => a.sort_order - b.sort_order),
    [lines.data],
  );
  const editable = project.can_edit;
  const isEditing = Boolean(edit) && editable;
  const staged = edit?.staged ?? null;

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

  const fetchProducts = React.useCallback(async (query: string) => {
    const products = await getProductsForVariantSelect(query || undefined);
    return products.map((product) => ({
      value: product.id,
      label: product.product_code,
      description: product.product_name,
    }));
  }, []);

  const columns = React.useMemo<InlineLineColumn<LineRow>[]>(
    () => [
      {
        key: 'product_id',
        header: 'Our product',
        width: 190,
        kind: 'searchable-select',
        // Optional on purpose: contractors order using their own codes, and forcing a
        // match at entry time would mean either a wrong match or an unrecordable PO. An
        // unmatched line is recorded and flagged instead (AC-F9).
        placeholder: 'Not matched',
        fetchOptions: fetchProducts,
        // Read off the DRAFT, not the stored line: nothing refetches during an edit session,
        // so resolving from the stored row would leave the trigger naming the product that was
        // there before the pick, right up until Save.
        resolveSelected: (row, draft) =>
          draft.product_id
            ? {
                value: draft.product_id,
                label:
                  row?.line?.product_id === draft.product_id
                    ? (row.line.product_code ?? 'Selected product')
                    : 'Selected product',
              }
            : undefined,
        annotate: (row) => (row?.line ? <LineFlags line={row.line} /> : null),
      },
      {
        key: 'product_code',
        header: 'Code on the PO',
        width: 150,
        kind: 'text',
        placeholder: 'What their document calls it',
      },
      {
        key: 'description',
        header: 'Description',
        width: 230,
        kind: 'text',
        placeholder: 'As written on the PO',
      },
      {
        key: 'quantity',
        header: 'Qty',
        width: 96,
        kind: 'number',
        align: 'end',
        validate: (value) =>
          value.trim() === '' || isDecimalString(value) ? null : 'Must be a number',
        formatReadOnly: (value) => trimAmount(value),
      },
      {
        key: 'uom',
        header: 'UOM',
        width: 110,
        // Same dropdown the quotation editor uses: "pcs" and "PCS" are one unit to a reader
        // and two strings to every report.
        kind: 'select',
        placeholder: 'PCS',
        options: uomOptions,
        loadError: uoms.error,
        onRetry: () => void uoms.refetch(),
        resolveSelected: (_row, draft) =>
          uomOptions.find((option) => option.value === draft.uom),
      },
      {
        key: 'unit_price',
        header: 'Ordered at',
        width: 128,
        kind: 'number',
        align: 'end',
        placeholder: '0.00',
        validate: (value) =>
          value.trim() === '' || isDecimalString(value) ? null : 'Must be a number',
        formatReadOnly: (value) => formatMyr(value),
        annotate: (row) =>
          row?.line?.quoted_unit_price ? (
            <span
              className={
                row.line.price_mismatch
                  ? 'mt-0.5 block text-end text-xs text-destructive'
                  : 'mt-0.5 block text-end text-xs text-muted-foreground'
              }
            >
              {`Quoted ${formatMyr(row.line.quoted_unit_price)}`}
            </span>
          ) : null,
      },
      {
        key: 'line_total',
        header: 'Total',
        width: 128,
        kind: 'derived',
        align: 'end',
        derive: (draft) => formatMyr(multiplyMoney(draft.quantity, draft.unit_price) ?? '0'),
        /**
         * The PO's own total, under the column it sums, and off the LIVE drafts so it moves
         * while a quantity is being typed. Reading the saved rows instead left the footer
         * stating the last save's answer while the cells said something else, and the first
         * thing anybody does after changing a quantity is look down here.
         */
        footer: (drafts) => {
          const total = totalFromDrafts(drafts);
          return total === null ? '-' : formatMyrExact(total);
        },
      },
    ],
    [fetchProducts, uomOptions, uoms.error, uoms.refetch],
  );

  /**
   * The starting point, taken from the server's rows the first time the session is opened.
   * Seeded through the page, never held here: the staged work has to outlive this component.
   */
  const seedLines = edit?.seed;
  React.useEffect(() => {
    if (!seedLines || !isEditing || staged !== null || lines.isLoading) return;
    seedLines(
      rows.map((line) => ({
        id: line.id,
        key: line.id,
        line,
        draft: serverToDraft(line),
        removed: false,
      })),
    );
  }, [isEditing, lines.isLoading, rows, seedLines, staged]);

  /**
   * What the table draws, in display order: the staged set while editing, the server's rows
   * otherwise. One row type either way, so there is one set of columns rather than two that can
   * drift apart.
   */
  const lineRows = React.useMemo<LineRow[]>(
    () =>
      isEditing && staged
        ? staged.map((line) => ({ id: line.key, line: line.line }))
        : rows.map((line) => ({ id: line.id, line })),
    [isEditing, rows, staged],
  );

  const stagedByKey = React.useMemo(() => {
    const map = new Map<string, StagedPurchaseOrderLine>();
    (staged ?? []).forEach((line) => map.set(line.key, line));
    return map;
  }, [staged]);

  const toDraft = React.useCallback(
    (row: LineRow): InlineDraft =>
      // The staged draft when there is one, so an edit already made is what comes back rather
      // than the server's untouched row.
      stagedByKey.get(row.id)?.draft ?? (row.line ? serverToDraft(row.line) : emptyDraft()),
    [stagedByKey],
  );

  const stageLines = edit?.stage;
  const toggleRemoved = edit?.toggleRemoved;
  const staging = React.useMemo<InlineStaging<LineRow> | undefined>(() => {
    if (!isEditing || !stageLines || !toggleRemoved) return undefined;
    return {
      onChange: (reported) =>
        stageLines(
          reported.map(({ rowKey, draft }) => {
            // A key the page has not seen is a row the table has just added: no id, no stored
            // line, and the save will read it as new.
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
  }, [isEditing, stageLines, stagedByKey, toggleRemoved]);

  return (
    <div className="min-w-0 space-y-3">
      {/* A real risk signal, not a lesson: without a bound version nothing on these lines
          is checked, and a clean-looking table would say the opposite. One line, no
          paragraph. */}
      {!po.quotation_version_id && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <AlertTriangle className="size-3.5 shrink-0 text-amber-600" aria-hidden />
          Not tied to a quotation version, so no price is checked.
        </p>
      )}

      <InlineLineTable<LineRow>
        /**
         * A fresh table when the session opens and again when it closes.
         *
         * The table keeps its own drafts and deliberately refuses to overwrite a dirty row from
         * `rows` - that is what stops a refetch wiping what somebody is typing. It also means
         * Cancel could not put a row back: the staged set disappears and the table carries on
         * showing the edit. Remounting is the honest reset.
         */
        key={isEditing ? 'editing' : 'reading'}
        rows={lineRows}
        getRowId={(row) => row.id}
        columns={columns}
        toDraft={toDraft}
        emptyDraft={emptyDraft}
        // A view is a view: outside an edit session there is nothing to type into, nothing to
        // add and nothing that can be saved by moving the caret.
        readOnly={!isEditing}
        isLoading={lines.isLoading}
        addLabel="Add a line"
        staging={staging}
        emptyHint={
          isEditing
            ? 'Nothing entered yet. Add a line and match it to a product to have its price checked against the quotation.'
            : editable
              ? 'No lines entered. Press Edit to enter what the PO ordered.'
              : 'This PO was recorded as a single amount with no line detail.'
        }
        describeRow={(row, index) =>
          row?.line?.product_code ?? row?.line?.description ?? `line ${index + 1}`
        }
        rowDetail={{
          key: 'notes',
          label: 'Notes',
          placeholder: 'Why the price or the model differs, if it does',
        }}
        validateRow={lineErrors}
      />
    </div>
  );
}

/** What the check against the quoted version found, on the line it found it on. */
function LineFlags({ line }: { line: PurchaseOrderLine }) {
  if (!line.model_mismatch && !line.price_mismatch) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      {line.model_mismatch && (
        <Badge
          variant="destructive"
          className="gap-1 text-[11px]"
          title="This item does not appear on the quoted version"
        >
          <AlertTriangle className="size-3" aria-hidden />
          Not quoted
        </Badge>
      )}
      {line.price_mismatch && (
        <Badge variant="destructive" className="text-[11px]">
          Price differs
        </Badge>
      )}
    </div>
  );
}

/**
 * The PO's money, off the LIVE drafts.
 *
 * A cell that is not a number yet counts as zero rather than blanking the whole total, so the
 * figure survives a half-typed price.
 */
function totalFromDrafts(drafts: InlineDraft[]): string | null {
  return sumMoney(drafts.map((draft) => multiplyMoney(draft.quantity, draft.unit_price) ?? '0'));
}

/** Draft to the body the save sends per line. Unchanged from the per-row write it replaces. */
function toBody(draft: InlineDraft) {
  return {
    product_id: draft.product_id || null,
    product_code: draft.product_code.trim() || null,
    description: draft.description.trim() || null,
    unit_price: draft.unit_price.trim() || '0',
    quantity: draft.quantity.trim() || '1',
    uom: draft.uom.trim() || null,
    notes: draft.notes.trim() || null,
  };
}

function trimAmount(value: string): string {
  const amount = Number(value);
  if (Number.isNaN(amount)) return value;
  return String(amount);
}
