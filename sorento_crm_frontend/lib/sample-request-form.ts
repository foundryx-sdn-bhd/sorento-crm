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

export type SectionKey = 'customer' | 'lines' | 'delivery' | 'additional';

export interface DraftLine {
  key: string;
  product_id: string;
  label: string;
  alt_product_id: string | null;
  alt_label: string | null;
  qty: string;
  remark: string;
}

export interface FormState {
  customer_id: string;
  customer_label: string;
  project_id: string;
  project_label: string;
  project_title: string;
  quotation_version_id: string;
  quotation_label: string;
  scope_note: string;
  lines: DraftLine[];
  board_mounting: boolean;
  board_label: string;
  immediate: boolean;
  delivery_date: string;
  delivery_mode: SampleRequestDeliveryMode | '';
  ship_to_name: string;
  ship_to_address: string;
  site_contact_name: string;
  site_contact_phone: string;
  site_contact_note: string;
  loan_value: string;
  loan_unit: SampleRequestLoanUnit;
  goods_from: SampleRequestGoodsFrom;
  purpose: string;
}

export const EMPTY_FORM: FormState = {
  customer_id: '',
  customer_label: '',
  project_id: '',
  project_label: '',
  project_title: '',
  quotation_version_id: '',
  quotation_label: '',
  scope_note: '',
  lines: [],
  board_mounting: false,
  board_label: '',
  immediate: false,
  delivery_date: '',
  delivery_mode: '',
  ship_to_name: '',
  ship_to_address: '',
  site_contact_name: '',
  site_contact_phone: '',
  site_contact_note: '',
  loan_value: '2',
  loan_unit: 'months',
  goods_from: 'either',
  purpose: '',
};

/** Which section holds the field a refusal is about. */
export const FIELD_SECTION: Record<string, SectionKey> = {
  customer_id: 'customer',
  project_id: 'customer',
  quotation_version_id: 'customer',
  lines: 'lines',
  board_label: 'lines',
  delivery_date: 'delivery',
  delivery_mode: 'delivery',
  ship_to_name: 'delivery',
};

let lineSeq = 0;
export const nextKey = () => `line-${(lineSeq += 1)}`;

const clean = (v: string): string | null => (v.trim() ? v.trim() : null);

export function stateFromDetail(d: SampleRequestDetail): FormState {
  return {
    customer_id: d.customer_id ?? '',
    customer_label: d.customer_name ?? '',
    project_id: d.project_id ?? '',
    project_label: d.project_id ? [d.project_code, d.project_name].filter(Boolean).join(' - ') : '',
    project_title: d.project_id ? '' : (d.project_title ?? ''),
    quotation_version_id: d.quotation_version_id ?? '',
    quotation_label: d.quotation_label ?? '',
    scope_note: d.scope_note ?? '',
    lines: (d.lines ?? []).map((l) => ({
      key: nextKey(),
      product_id: l.product_id,
      label: codeWithName(l.product_code, l.product_name),
      alt_product_id: l.alt_product_id,
      alt_label: l.alt_product_code,
      qty: String(l.qty),
      remark: l.remark ?? '',
    })),
    board_mounting: Boolean(d.board_mounting),
    board_label: d.board_label ?? '',
    immediate: Boolean(d.deliver_immediately),
    delivery_date: d.delivery_date ?? '',
    delivery_mode: d.delivery_mode ?? '',
    ship_to_name: d.ship_to_name ?? '',
    ship_to_address: d.ship_to_address ?? '',
    site_contact_name: d.site_contact_name ?? '',
    site_contact_phone: d.site_contact_phone ?? '',
    site_contact_note: d.site_contact_note ?? '',
    loan_value: String(d.loan_value ?? 2),
    loan_unit: d.loan_unit ?? 'months',
    goods_from: d.goods_from ?? 'either',
    purpose: d.purpose ?? '',
  };
}

export function payloadFromState(s: FormState): SampleRequestWrite {
  return {
    customer_id: s.customer_id || null,
    project_id: s.project_id || null,
    project_title: s.project_id ? null : clean(s.project_title),
    quotation_version_id: s.project_id ? s.quotation_version_id || null : null,
    scope_note: clean(s.scope_note),
    delivery_date: s.immediate ? null : s.delivery_date || null,
    deliver_immediately: s.immediate,
    delivery_mode: s.delivery_mode || null,
    ship_to_name: clean(s.ship_to_name),
    ship_to_address: clean(s.ship_to_address),
    site_contact_name: clean(s.site_contact_name),
    site_contact_phone: clean(s.site_contact_phone),
    site_contact_note: clean(s.site_contact_note),
    loan_value: Math.max(1, Number.parseInt(s.loan_value, 10) || 2),
    loan_unit: s.loan_unit,
    goods_from: s.goods_from,
    purpose: clean(s.purpose),
    board_mounting: s.board_mounting,
    board_label: s.board_mounting ? clean(s.board_label) : null,
    lines: s.lines.map((l) => ({
      product_id: l.product_id,
      alt_product_id: l.alt_product_id,
      qty: Math.max(1, Number.parseInt(l.qty, 10) || 1),
      remark: clean(l.remark),
    })),
  };
}

/** The same refusals the server makes on submit, so the message lands before the round trip. */
export function validate(
  s: FormState,
  rules: { requireProject?: boolean; requireQuotation?: boolean } = {},
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!s.customer_id) errors.customer_id = 'Pick the customer';
  if (rules.requireProject) {
    if (!s.project_id) errors.project_id = 'Pick a registered project';
  } else if (!s.project_id && !s.project_title.trim()) {
    errors.project_id = 'Pick the project, or type its title';
  }
  if (rules.requireQuotation && !s.quotation_version_id) {
    errors.quotation_version_id = 'Pick the quotation';
  }
  if (!s.immediate && !s.delivery_date) errors.delivery_date = 'Choose a date, or Immediate';
  if (!s.delivery_mode) errors.delivery_mode = 'Choose a delivery mode';
  if (!s.ship_to_name.trim()) errors.ship_to_name = 'Enter who the samples go to';
  if (s.lines.length === 0 && !s.board_mounting) errors.lines = 'Add at least one item';
  if (s.board_mounting && !s.board_label.trim()) errors.board_label = 'Enter the board label';
  return errors;
}

