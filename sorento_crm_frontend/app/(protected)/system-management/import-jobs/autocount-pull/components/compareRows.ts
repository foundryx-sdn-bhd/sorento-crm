/**
 * Pure helpers behind `PullCompareTab`'s differences grid + download
 * (PLAN-autocount-compare-tab-detail.md). No React, no DataGrid - kept separate so the
 * formatting/labelling/combining logic is unit-testable without rendering the table.
 */
import {
  isDocumentEntity,
  type AutocountCompareDifference,
  type AutocountComparePullResult,
  type AutocountPullCompareSource,
  type AutocountPullEntity,
} from '../types/autocountPull.types';

/** Booleans -> `Active`/`Inactive` (the only boolean field the compare service sends is
 *  `is_active`); `null`/`undefined`/`""` -> `-` (review S1: the backend sends `""` for a
 *  blank `description`/`item_group`/`item_brand`, which reads the same as "no value" as a
 *  missing field does); everything else -> its string form (`0` stays `"0"`, never a dash).
 *  Shared by the grid cells, their `title`, and the download rows, so what is shown is what
 *  is exported. */
export function formatCompareValue(value: string | number | boolean | null, field?: string): string {
  if (value === null || value === undefined || value === '') return '-';
  // The headers compare's `cancel` flag reads Yes / No; `is_active` keeps Active / Inactive.
  if (typeof value === 'boolean') return field === 'cancel' ? (value ? 'Yes' : 'No') : value ? 'Active' : 'Inactive';
  return String(value);
}

const FIELD_LABELS: Record<string, string> = {
  description: 'Description',
  item_group: 'Item Group',
  item_brand: 'Item Brand',
  price: 'Price',
  // Matches the manual template's own column header (captain ruling N7) - "Active" would
  // collide with the Active/Inactive VALUE cells next to it in the same row.
  is_active: 'Is Active',
  on_hand_qty: 'On Hand Qty',
  // The two macro sheets' own column headers (`compare_delivery_orders`,
  // `compare_delivery_order_headers`).
  qty: 'Qty',
  unit_price: 'Unit Price',
  discount: 'Discount',
  total_ex: 'Total (Ex)',
  doc_date: 'Date',
  debtor_code: 'Debtor Code',
  cancel: 'Cancel',
  // The GRN files (`compare_goods_receive_notes`, `_headers`): the sheets' "Our PO No." and
  // "Transfer From", both the PO or SPO the receipt came from.
  creditor_code: 'Creditor Code',
  source_doc: 'Source PO / SPO',
};

/** Human label for a backend field key (cursor rule: no snake_case in the UI). Unknown keys
 *  fall back to the raw key rather than throwing. */
export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

/** One row the differences grid renders and the download exports - values already formatted,
 *  the field already labelled. */
export interface CompareRow {
  item_code: string;
  /** Delivery orders only. */
  doc_no?: string;
  location?: string;
  field: string;
  excel: string;
  pull: string;
  /** Delivery orders only: which of the two macro files the row came from ("Lines" for
   *  Order Listing, "Headers" for Order Tracking). */
  source?: string;
}

/** Human label for a delivery-orders compare source (cursor rule: no raw key in the UI). */
export const COMPARE_SOURCE_LABEL: Record<AutocountPullCompareSource, string> = {
  lines: 'Lines',
  headers: 'Headers',
};

/** Stock only-in labels are `code|location` (`autocount_pull_compare.py`'s `_stock_pair_label`);
 *  delivery-orders labels are `docno|code|location` (`_do_label`), split on the FIRST and
 *  LAST `|` so an item code carrying one keeps it. Product only-in labels are the raw item
 *  code, never split (review S2: a product code can legitimately contain `|`, so splitting
 *  there would corrupt it). */
function splitOnlyInLabel(
  label: string,
  entity: AutocountPullEntity,
): { item_code: string; doc_no?: string; location?: string } {
  if (isDocumentEntity(entity)) {
    const first = label.indexOf('|');
    const last = label.lastIndexOf('|');
    if (first === -1 || last === first) return { item_code: label };
    return {
      doc_no: label.slice(0, first),
      item_code: label.slice(first + 1, last),
      location: label.slice(last + 1),
    };
  }
  if (entity !== 'stock_balances') return { item_code: label };
  const separatorIndex = label.indexOf('|');
  if (separatorIndex === -1) return { item_code: label };
  return { item_code: label.slice(0, separatorIndex), location: label.slice(separatorIndex + 1) };
}

function differenceRow(difference: AutocountCompareDifference, source?: string): CompareRow {
  return {
    item_code: difference.item_code,
    doc_no: difference.doc_no,
    location: difference.location,
    field: fieldLabel(difference.field),
    excel: formatCompareValue(difference.excel, difference.field),
    pull: formatCompareValue(difference.pull, difference.field),
    source,
  };
}

/** Combines `differences` + `only_in_excel` + `only_in_pull` into the ONE array the grid's
 *  `recordCount` and the download both use, so what is counted is what is downloaded (CT-4).
 *  `source` (delivery orders): labels every row with the file it came from; the headers
 *  file's only-in labels are bare document numbers, never split. */
export function buildCompareRows(
  result: AutocountComparePullResult,
  entity: AutocountPullEntity,
  source?: AutocountPullCompareSource,
): CompareRow[] {
  const sourceLabel = source ? COMPARE_SOURCE_LABEL[source] : undefined;
  const rows: CompareRow[] = result.differences.map((d) => differenceRow(d, sourceLabel));
  const split = (label: string) =>
    source === 'headers' ? { item_code: '', doc_no: label, location: undefined } : splitOnlyInLabel(label, entity);

  for (const label of result.only_in_excel) {
    const { item_code, doc_no, location } = split(label);
    rows.push({ item_code, doc_no, location, field: 'Only in your Excel', excel: 'Present', pull: 'Missing', source: sourceLabel });
  }
  for (const label of result.only_in_pull) {
    const { item_code, doc_no, location } = split(label);
    rows.push({ item_code, doc_no, location, field: 'Only in AutoCount', excel: 'Missing', pull: 'Present', source: sourceLabel });
  }

  return rows;
}
