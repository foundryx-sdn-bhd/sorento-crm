/** NUMBER-FIELDS round 3 (S6): a line Total / Unit price holding browser bad input (NaN) is refused with the shared sentence. */
import { describe, expect, it } from 'vitest';
import { PurchaseRequestFormSchema } from './purchase-request-schema';

const BASE = {
  request_type: 'sponsorship_form' as const,
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
  sales_type: 'project',
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

function issuesFor(requestType: 'sponsorship_form' | 'purchase_request', line: Record<string, unknown>) {
  const result = PurchaseRequestFormSchema.safeParse({
    ...BASE,
    request_type: requestType,
    products: [{ item_code: 'A', quantity: 2, remark: null, unit_price: 10, total: null, ...line }],
  });
  return result.success ? [] : result.error.issues;
}

describe('PR line bad number input (S6)', () => {
  it('a NaN line Total is refused with the sentence at products.0.total', () => {
    const issue = issuesFor('sponsorship_form', { total: NaN }).find(
      (i) => i.path.join('.') === 'products.0.total',
    );
    expect(issue?.message).toMatch(/^Enter a number/);
  });

  it('a NaN Unit price on a purchase request is refused with the sentence', () => {
    const issue = issuesFor('purchase_request', { unit_price: NaN }).find(
      (i) => i.path.join('.') === 'products.0.unit_price',
    );
    expect(issue?.message).toMatch(/^Enter a number/);
  });

  it('a blank Total is still accepted', () => {
    expect(issuesFor('sponsorship_form', { total: null })).toEqual([]);
  });
});
