import { describe, expect, it } from 'vitest';
import { PurchaseRequestFormSchema, PurchaseRequestSchema } from './purchase-request-schema';

const BASE = {
  request_number: null,
  request_date: null,
  customer_name: null,
  pic: null,
  project_title: null,
  project_id: null,
  purpose: null,
  delivery_address: null,
  total_project_value: null,
  total_project_value_text: null,
  sales_type: null,
  sponsor_subject: null,
  sponsor_subject_other: null,
  expected_delivery_date: null,
  expected_po_date: null,
  expected_po_date_text: null,
  requested_by: null,
  requested_by_contact_id: null,
  requested_at: null,
  contact_id: null,
  space_id: null,
};

/** Total Project Value FE guard rail - mirrors backend validators.validate_project_value.
 *  Prevents regression of the "string saved as null" / Numeric(15,2) overflow bugs. */
function parseTPV(value: unknown) {
  return PurchaseRequestSchema.shape.total_project_value.safeParse(value);
}

describe('total_project_value validation', () => {
  it.each([1234, '1234', '1234.00', '0.5', null, '', '9999999999999.99'])(
    'accepts numeric / empty value: %s',
    (value) => {
      expect(parseTPV(value).success).toBe(true);
    },
  );

  it.each(['800K', 'abc', '1.6 mil'])('rejects non-numeric: %s', (value) => {
    const res = parseTPV(value);
    expect(res.success).toBe(false);
    if (!res.success) {
      expect(res.error.issues.some((i) => /must be a number/.test(i.message))).toBe(true);
    }
  });

  it.each(['10000000000000', '123446433232323232323232'])(
    'rejects out-of-range (>= 10^13): %s',
    (value) => {
      const res = parseTPV(value);
      expect(res.success).toBe(false);
      if (!res.success) {
        expect(res.error.issues.some((i) => /too large/.test(i.message))).toBe(true);
      }
    },
  );
});

/** #1227: mirrors the `sales_type` superRefine test shape below it - unit price is
 *  mandatory on every real sponsorship form line, purchase requests are unchanged. */
describe('sponsorship unit price validation (#1227)', () => {
  function parse(requestType: 'purchase_request' | 'sponsorship_form', line: Record<string, unknown>) {
    return PurchaseRequestFormSchema.safeParse({
      ...BASE,
      request_type: requestType,
      sales_type: requestType === 'purchase_request' ? 'project' : null,
      products: [line],
    });
  }

  it.each([
    ['missing', { item_code: 'ITEM-A', quantity: 2, unit_price: null, total: null }],
    ['blank', { item_code: 'ITEM-A', quantity: 2, unit_price: '', total: null }],
    ['negative', { item_code: 'ITEM-A', quantity: 2, unit_price: -1, total: null }],
  ])('rejects a sponsorship line with a %s unit price', (_label, line) => {
    const res = parse('sponsorship_form', line);
    expect(res.success).toBe(false);
    if (!res.success) {
      const issue = res.error.issues.find((i) => i.path.join('.') === 'products.0.unit_price');
      expect(issue?.message).toBe('Unit price is required.');
    }
  });

  it('accepts a sponsorship line with a valid unit price', () => {
    const res = parse('sponsorship_form', {
      item_code: 'ITEM-A',
      quantity: 2,
      unit_price: 10,
      total: 20,
    });
    expect(res.success).toBe(true);
  });

  it('accepts a unit price of exactly 0 (AC-P8/S6: mandatory means present, not positive)', () => {
    const res = parse('sponsorship_form', {
      item_code: 'ITEM-A',
      quantity: 2,
      unit_price: 0,
      total: 0,
    });
    expect(res.success).toBe(true);
  });

  it('never refuses a fully blank filler line (nothing to check yet)', () => {
    const res = parse('sponsorship_form', {
      item_code: null,
      quantity: null,
      unit_price: null,
      total: null,
      remark: null,
    });
    expect(res.success).toBe(true);
  });

  it('leaves a purchase request line without a unit price unchanged', () => {
    const res = parse('purchase_request', {
      item_code: 'ITEM-A',
      quantity: 2,
      unit_price: null,
      total: null,
    });
    expect(res.success).toBe(true);
  });
});

describe('PurchaseRequestFormSchema bad input (round 2, S-A)', () => {
  it('a NaN line Quantity reads as the number sentence, never raw zod text, and the other checks still run', () => {
    const result = PurchaseRequestFormSchema.safeParse({
      ...BASE,
      request_type: 'purchase_request',
      sales_type: null,
      products: [{ item_code: 'A', quantity: NaN, remark: null, unit_price: null, total: null }],
    });
    expect(result.success).toBe(false);
    if (result.success) return;
    const byPath = Object.fromEntries(result.error.issues.map((i) => [i.path.join('.'), i.message]));
    expect(byPath['products.0.quantity']).toBe('Enter a number above 0.');
    expect(byPath['sales_type']).toBe('Sales type is required.');
  });
});
