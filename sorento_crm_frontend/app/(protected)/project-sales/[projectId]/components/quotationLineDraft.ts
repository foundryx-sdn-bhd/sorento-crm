/**
 * A quotation line as the editor holds it: every field a string, the way the grid edits it and the
 * bulk write sends it. Pure helpers, shared by the editor (staging, Save body, totals) and the
 * lines grid (cells, validation, footer), so the two cannot disagree about a line.
 */
import {
  INLINE_CHECKED,
  isInlineChecked,
  type InlineDraft,
} from '../../_shared/components/InlineLineTable';
import { multiplyMoney, sumMoney } from '../../_shared/lib/money';
import type {
  QuotationLine,
  QuotationLineBulkItem,
  UnitType,
} from '../../_shared/types/project.types';

export type LineDraft = InlineDraft;

export { INLINE_CHECKED, isInlineChecked };

/**
 * "Priced per" (the line's `unit_type`): reference only, it changes no total and prints nowhere
 * (owner, Q4). Empty means not set.
 */
export const PRICED_PER_OPTIONS: { value: UnitType; label: string }[] = [
  { value: 'house_unit', label: 'Each house unit' },
  { value: 'bathroom', label: 'Each bathroom' },
  { value: 'facility', label: 'Each facility' },
  { value: 'common_area', label: 'Whole site (common area)' },
];

/** The two picked files a line can carry. Only ever sent when set, or when clearing one. */
const PICKS = ['image_attachment_id', 'drawing_attachment_id'] as const;

/** A stored line as an editable draft. Every field the bulk write sends has to be in here. */
export function serverToDraft(line: QuotationLine): LineDraft {
  return {
    product_id: line.product_id ?? '',
    product_set_id: line.product_set_id ?? '',
    description: line.description ?? '',
    // No cell any more: carried through the save untouched, and printed under the description.
    technical_spec: line.technical_spec ?? '',
    // The response names this one `brand`, the request `brand_snapshot`. The draft holds the
    // request's name so the payload stays a copy rather than a translation.
    brand_snapshot: line.brand ?? '',
    quantity: line.quantity ?? '1',
    uom: line.uom ?? '',
    unit_price: line.unit_price ?? '',
    complete_set: line.complete_set ?? '',
    unit_type: line.unit_type ?? '',
    is_rate_only: line.is_rate_only ? INLINE_CHECKED : '',
    band_label: line.band_label ?? '',
    notes: line.notes ?? '',
    image_attachment_id: line.image_attachment_id ?? '',
    drawing_attachment_id: line.drawing_attachment_id ?? '',
    // Read under the rate, never sent back (the server snapshots it from the product).
    list_price: line.list_price ?? '',
  };
}

/** A brand-new row's starting draft. Also the "untouched" comparison. */
export function emptyDraft(): LineDraft {
  return {
    product_id: '',
    product_set_id: '',
    description: '',
    technical_spec: '',
    brand_snapshot: '',
    quantity: '1',
    uom: '',
    unit_price: '',
    complete_set: '',
    unit_type: '',
    is_rate_only: '',
    band_label: '',
    notes: '',
    image_attachment_id: '',
    drawing_attachment_id: '',
    list_price: '',
  };
}

/** Nothing typed into it yet: not wrong, just empty. */
export function isUntouched(draft: LineDraft): boolean {
  const empty = emptyDraft();
  const keys = new Set([...Object.keys(empty), ...Object.keys(draft)]);
  for (const key of keys) {
    if ((draft[key] ?? '') !== (empty[key] ?? '')) return false;
  }
  return true;
}

/**
 * The one rule a quotation line has to satisfy before it can be stored, keyed by the field at
 * fault: a non-standard item (no product, no set) carries the description the customer reads. Used to mark the
 * cell and to stop a Save that would only come back as the server's 422.
 */
export function lineErrors(draft: LineDraft): Record<string, string> {
  return !draft.product_id && !draft.product_set_id && !(draft.description ?? '').trim()
    ? { description: 'A non-standard item needs a description' }
    : {};
}

/**
 * The scope's money off the LIVE drafts. Rate-only lines contribute nothing, the rule the PDF and
 * the backend total apply; a cell that is not a number yet counts as zero rather than blanking
 * the whole total.
 */
export function totalFromDrafts(drafts: LineDraft[]): string | null {
  return sumMoney(
    drafts
      .filter((draft) => !isInlineChecked(draft.is_rate_only))
      .map((draft) => multiplyMoney(draft.quantity, draft.unit_price) ?? '0'),
  );
}

/**
 * Draft to the body the bulk write takes.
 *
 * `brand_snapshot` is the request's name for what the response calls `brand`. `item_label` is not
 * sent: the item number is the row's position. A picked photo or drawing is sent when set, and as
 * null when it clears one the line had stored (a product change clears both), so a line that never
 * had one keeps the body it always had.
 */
export function toBody(
  draft: LineDraft,
  stored: QuotationLine | null,
): QuotationLineBulkItem {
  const body: QuotationLineBulkItem = {
    product_id: draft.product_id || null,
    description_snapshot: (draft.description ?? '').trim() || null,
    unit_price: (draft.unit_price ?? '').trim() || '0',
    quantity: (draft.quantity ?? '').trim() || '1',
    uom: (draft.uom ?? '').trim() || null,
    unit_type: (draft.unit_type || null) as UnitType | null,
    notes: (draft.notes ?? '').trim() || null,
    brand_snapshot: (draft.brand_snapshot ?? '').trim() || null,
    technical_spec: (draft.technical_spec ?? '').trim() || null,
    complete_set: (draft.complete_set ?? '').trim() || null,
    band_label: (draft.band_label ?? '').trim() || null,
    is_rate_only: isInlineChecked(draft.is_rate_only),
  };
  // Sent only when it says something: a set, a product pick (which moves the line off a set),
  // or a line that had a set and no longer does. A plain custom line keeps the body it had.
  if (draft.product_set_id || draft.product_id || stored?.product_set_id) {
    body.product_set_id = draft.product_set_id || null;
  }
  PICKS.forEach((key) => {
    const value = (draft[key] ?? '').trim() || null;
    if (value || stored?.[key]) body[key] = value;
  });
  return body;
}

/**
 * The Complete set text for a set's parts: codes joined by " + ", "2 x CODE" when the quantity is
 * not 1, cut to the 100 characters the column holds. The server builds the same text when a set
 * line is saved with this field blank, so the two read alike.
 */
export function completeSetText(
  parts: { product_code: string; quantity: string | number }[],
): string {
  return parts
    .map((part) => {
      const quantity = Number(part.quantity);
      return Number.isNaN(quantity) || quantity === 1
        ? part.product_code
        : `${quantity} x ${part.product_code}`;
    })
    .join(' + ')
    .slice(0, 100);
}

/** Quantities come back as "2.00"; nobody writes two bathrooms as 2.00. */
export function trimAmount(value: string): string {
  const amount = Number(value);
  if (Number.isNaN(amount)) return value;
  return String(amount);
}
