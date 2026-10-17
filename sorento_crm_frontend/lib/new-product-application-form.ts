/**
 * Form state for the new product application pages (NEW-PRODUCT-APP), shared by the staff
 * form and the portal form: the draft shape, the payload it writes, and the number checks run
 * before the round trip. The server makes the required-field refusals on Submit and names the
 * line; it stays the authority.
 */
import type {
  NewProductApplicationDetail,
  NewProductApplicationLine,
  NewProductApplicationPrefill,
  NewProductApplicationWrite,
} from '@/types/newProductApplication';
import { numberFieldError } from '@/lib/number-field';

export interface DraftLine {
  key: string;
  proposed_code: string;
  description: string;
  further_description: string;
  category_id: string;
  category_label: string;
  base_uom_id: string;
  base_uom_label: string;
  brand_id: string;
  brand_label: string;
  item_type_id: string;
  item_type_label: string;
  supplier_id: string;
  supplier_label: string;
  supplier_item_code: string;
  /** Typed text; an emptied box is sent as null, never 0. */
  cost_price: string;
  selling_price: string;
  expected_qty: string;
  remark: string;
}

export interface FormState {
  stock_inquiry_id: string;
  stock_inquiry_label: string;
  customer_id: string;
  /** The debtor's name when picked, otherwise the typed text. */
  customer_name: string;
  project_name: string;
  needed_by: string;
  remark: string;
  lines: DraftLine[];
}

export const EMPTY_FORM: FormState = {
  stock_inquiry_id: '',
  stock_inquiry_label: '',
  customer_id: '',
  customer_name: '',
  project_name: '',
  needed_by: '',
  remark: '',
  lines: [],
};

let lineSeq = 0;
export const nextKey = () => `npa-line-${(lineSeq += 1)}`;

export const blankLine = (): DraftLine => ({
  key: nextKey(),
  proposed_code: '',
  description: '',
  further_description: '',
  category_id: '',
  category_label: '',
  base_uom_id: '',
  base_uom_label: '',
  brand_id: '',
  brand_label: '',
  item_type_id: '',
  item_type_label: '',
  supplier_id: '',
  supplier_label: '',
  supplier_item_code: '',
  cost_price: '',
  selling_price: '',
  expected_qty: '',
  remark: '',
});

/** "SI26-0412 - XY-1234-MB": the inquiry as the header picker and the detail page name it. */
export const stockInquiryLabel = (
  number?: string | null,
  productCode?: string | null,
) => [number, productCode].filter(Boolean).join(' - ');

const text = (v: string | null | undefined) => v ?? '';
const clean = (v: string): string | null => (v.trim() ? v.trim() : null);
const figure = (v: string | number | null | undefined) =>
  v === null || v === undefined ? '' : String(v);

function lineFromDetail(l: NewProductApplicationLine): DraftLine {
  return {
    key: nextKey(),
    proposed_code: text(l.proposed_code),
    description: text(l.description),
    further_description: text(l.further_description),
    category_id: text(l.category_id),
    category_label: text(l.category_name),
    base_uom_id: text(l.base_uom_id),
    base_uom_label: text(l.base_uom_code),
    brand_id: text(l.brand_id),
    brand_label: text(l.brand_name),
    item_type_id: text(l.item_type_id),
    item_type_label: text(l.item_type_name),
    supplier_id: text(l.supplier_id),
    supplier_label: text(l.supplier_name),
    supplier_item_code: text(l.supplier_item_code),
    cost_price: figure(l.cost_price),
    selling_price: figure(l.selling_price),
    expected_qty: figure(l.expected_qty),
    remark: text(l.remark),
  };
}

export function stateFromDetail(d: NewProductApplicationDetail): FormState {
  return {
    stock_inquiry_id: text(d.stock_inquiry_id),
    stock_inquiry_label: d.stock_inquiry_id
      ? stockInquiryLabel(d.stock_inquiry_number, d.stock_inquiry_product_code)
      : '',
    customer_id: text(d.customer_id),
    customer_name: text(d.customer_name),
    project_name: text(d.project_name),
    needed_by: text(d.needed_by),
    remark: text(d.remark),
    lines: (d.lines ?? []).map(lineFromDetail),
  };
}

/**
 * The pre-fill copies TEXT only: the customer stays typed text with no debtor, and line 1
 * carries the inquiry's code field as written. Nothing is matched or split.
 */
export function stateFromPrefill(p: NewProductApplicationPrefill): FormState {
  const first = p.lines?.[0];
  return {
    ...EMPTY_FORM,
    stock_inquiry_id: text(p.stock_inquiry_id),
    stock_inquiry_label: stockInquiryLabel(
      p.stock_inquiry_number,
      first?.proposed_code,
    ),
    customer_name: text(p.customer_name),
    project_name: text(p.project_name),
    needed_by: text(p.needed_by),
    remark: text(p.remark),
    lines: [
      {
        ...blankLine(),
        proposed_code: text(first?.proposed_code),
        description: text(first?.description),
        expected_qty: figure(first?.expected_qty),
      },
    ],
  };
}

export function payloadFromState(s: FormState): NewProductApplicationWrite {
  return {
    stock_inquiry_id: s.stock_inquiry_id || null,
    customer_id: s.customer_id || null,
    customer_name: clean(s.customer_name),
    project_name: clean(s.project_name),
    needed_by: s.needed_by || null,
    remark: clean(s.remark),
    lines: s.lines.map((l) => ({
      proposed_code: clean(l.proposed_code),
      description: clean(l.description),
      further_description: clean(l.further_description),
      category_id: l.category_id || null,
      base_uom_id: l.base_uom_id || null,
      brand_id: l.brand_id || null,
      item_type_id: l.item_type_id || null,
      supplier_id: l.supplier_id || null,
      supplier_item_code: clean(l.supplier_item_code),
      cost_price: clean(l.cost_price),
      selling_price: clean(l.selling_price),
      expected_qty: clean(l.expected_qty),
      remark: clean(l.remark),
    })),
  };
}

/** Money may be emptied or 0 or more; the quantity may be emptied or above 0. */
const MONEY = { min: 0, optional: true } as const;
const QTY = { min: 0, minExclusive: true, optional: true } as const;

/** Keys are `<field>_<line key>`; empty means every number is fine or blank. */
export function numberErrors(
  s: Pick<FormState, 'lines'>,
): Record<string, string> {
  const found: Record<string, string> = {};
  for (const l of s.lines) {
    const checks: [string, string, typeof MONEY | typeof QTY][] = [
      ['cost_price', l.cost_price, MONEY],
      ['selling_price', l.selling_price, MONEY],
      ['expected_qty', l.expected_qty, QTY],
    ];
    for (const [field, raw, rules] of checks) {
      const problem = numberFieldError(raw, rules);
      if (problem) found[`${field}_${l.key}`] = problem;
    }
  }
  return found;
}
