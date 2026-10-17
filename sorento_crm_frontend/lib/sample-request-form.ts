/**
 * Form state for the sample request pages (SR-CORE), shared by the portal form and the staff
 * new/edit page: the draft shape, the payload it writes, and the submit refusals checked before
 * the round trip. The server makes the same refusals and stays the authority.
 */
import {
  type SampleRequestDeliveryMode,
  type SampleRequestDetail,
  type SampleRequestGoodsFrom,
  type SampleRequestLoanUnit,
  type SampleRequestWrite,
} from '@/types/sampleRequest';
import { codeWithName } from '@/lib/product-display';
import { numberFieldError } from '@/lib/number-field';

/** The one rule for a line quantity and the loan period. */
export const WHOLE_AT_LEAST_ONE = { min: 1, integer: true } as const;

export interface DraftLine {
  key: string;
  product_id: string;
  label: string;
  qty: string;
  remark: string;
  /** The product went inactive after it was put on the request (kept, shown as Inactive). */
  inactive?: boolean;
}

export interface FormState {
  customer_id: string;
  customer_label: string;
  /** Typed when the customer has no debtor code; used only while no debtor is picked. */
  customer_name: string;
  project_id: string;
  project_label: string;
  project_title: string;
  scope_note: string;
  lines: DraftLine[];
  board_mounting: boolean;
  board_label: string;
  delivery_date: string;
  delivery_mode: SampleRequestDeliveryMode | '';
  ship_to_name: string;
  ship_to_address: string;
  site_contact_name: string;
  site_contact_phone: string;
  site_contact_note: string;
  loan_value: string;
  loan_unit: SampleRequestLoanUnit;
  /** Goods Taken From ticks: neither or both = either, one = that value. */
  goods_demo: boolean;
  goods_new: boolean;
  purpose: string;
}

export const EMPTY_FORM: FormState = {
  customer_id: '',
  customer_label: '',
  customer_name: '',
  project_id: '',
  project_label: '',
  project_title: '',
  scope_note: '',
  lines: [],
  board_mounting: false,
  board_label: '',
  delivery_date: '',
  delivery_mode: '',
  ship_to_name: '',
  ship_to_address: '',
  site_contact_name: '',
  site_contact_phone: '',
  site_contact_note: '',
  loan_value: '2',
  loan_unit: 'months',
  goods_demo: false,
  goods_new: false,
  purpose: '',
};

let lineSeq = 0;
export const nextKey = () => `line-${(lineSeq += 1)}`;

/** A row the user added but never picked a product for is not a line. */
const pickedLines = (s: FormState) => s.lines.filter((l) => l.product_id);

export const blankLine = (): DraftLine => ({
  key: nextKey(),
  product_id: '',
  label: '',
  qty: '1',
  remark: '',
});

const clean = (v: string): string | null => (v.trim() ? v.trim() : null);

export function stateFromDetail(d: SampleRequestDetail): FormState {
  return {
    customer_id: d.customer_id ?? '',
    customer_label: d.customer_name ?? '',
    customer_name: d.customer_id ? '' : (d.customer_name ?? ''),
    project_id: d.project_id ?? '',
    project_label: d.project_id ? [d.project_code, d.project_name].filter(Boolean).join(' - ') : '',
    project_title: d.project_id ? '' : (d.project_title ?? ''),
    scope_note: d.scope_note ?? '',
    lines: (d.lines ?? []).map((l) => ({
      key: nextKey(),
      product_id: l.product_id,
      label: codeWithName(l.product_code, l.product_name),
      qty: String(l.qty),
      remark: l.remark ?? '',
      inactive: l.product_active === false,
    })),
    board_mounting: Boolean(d.board_mounting),
    board_label: d.board_label ?? '',
    delivery_date: d.delivery_date ?? '',
    delivery_mode: d.delivery_mode ?? '',
    ship_to_name: d.ship_to_name ?? '',
    ship_to_address: d.ship_to_address ?? '',
    site_contact_name: d.site_contact_name ?? '',
    site_contact_phone: d.site_contact_phone ?? '',
    site_contact_note: d.site_contact_note ?? '',
    loan_value: String(d.loan_value ?? 2),
    loan_unit: d.loan_unit ?? 'months',
    goods_demo: d.goods_from === 'demo',
    goods_new: d.goods_from === 'new',
    purpose: d.purpose ?? '',
  };
}

/** One tick is that value; neither or both is either. */
export function goodsFromTicks(demo: boolean, fresh: boolean): SampleRequestGoodsFrom {
  if (demo && !fresh) return 'demo';
  if (fresh && !demo) return 'new';
  return 'either';
}

export function payloadFromState(s: FormState): SampleRequestWrite {
  return {
    customer_id: s.customer_id || null,
    customer_name: s.customer_id ? null : clean(s.customer_name),
    project_id: s.project_id || null,
    project_title: s.project_id ? null : clean(s.project_title),
    scope_note: clean(s.scope_note),
    delivery_date: s.delivery_date || null,
    delivery_mode: s.delivery_mode || null,
    ship_to_name: clean(s.ship_to_name),
    ship_to_address: clean(s.ship_to_address),
    site_contact_name: clean(s.site_contact_name),
    site_contact_phone: clean(s.site_contact_phone),
    site_contact_note: clean(s.site_contact_note),
    // Only built from a form whose numberErrors() is empty: the typed whole number goes through as typed.
    loan_value: Number(s.loan_value),
    loan_unit: s.loan_unit,
    goods_from: goodsFromTicks(s.goods_demo, s.goods_new),
    purpose: clean(s.purpose),
    board_mounting: s.board_mounting,
    board_label: s.board_mounting ? clean(s.board_label) : null,
    lines: pickedLines(s).map((l) => ({
      product_id: l.product_id,
      qty: Number(l.qty),
      remark: clean(l.remark),
    })),
  };
}

/** The same refusals the server makes on submit, so the message lands before the round trip. */
export function validate(
  s: FormState,
  rules: { requireProject?: boolean } = {},
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!s.customer_id && !s.customer_name.trim()) {
    errors.customer_id = 'Pick the customer, or type the customer name';
  }
  if (rules.requireProject) {
    if (!s.project_id) errors.project_id = 'Pick a registered project';
  } else if (!s.project_id && !s.project_title.trim()) {
    errors.project_id = 'Pick the project, or type its title';
  }
  if (!s.delivery_date) errors.delivery_date = 'Enter the Request Delivery Date';
  if (!s.delivery_mode) errors.delivery_mode = 'Choose a delivery mode';
  if (!s.ship_to_name.trim()) errors.ship_to_name = 'Enter who the samples go to';
  if (pickedLines(s).length === 0 && !s.board_mounting) errors.lines = 'Add at least one item';
  if (s.board_mounting && !s.board_label.trim()) errors.board_label = 'Enter the board label';
  return { ...errors, ...numberErrors(s) };
}

/** The qty and loan-period checks: shared by Submit and Save as draft, which never sends a bad figure. */
export function numberErrors(s: FormState): Record<string, string> {
  const errors: Record<string, string> = {};
  const loanError = numberFieldError(s.loan_value, WHOLE_AT_LEAST_ONE);
  if (loanError) errors.loan_value = loanError;
  for (const line of pickedLines(s)) {
    const qtyError = numberFieldError(line.qty, WHOLE_AT_LEAST_ONE);
    if (qtyError) errors[`qty_${line.key}`] = qtyError;
  }
  return errors;
}

