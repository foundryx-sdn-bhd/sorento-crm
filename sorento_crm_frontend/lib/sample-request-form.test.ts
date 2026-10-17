import { describe, expect, it } from 'vitest';
import { EMPTY_FORM, payloadFromState, stateFromDetail, validate, type FormState } from './sample-request-form';
import type { SampleRequestDetail } from '@/types/sampleRequest';

const complete = (over: Partial<FormState> = {}): FormState => ({
  ...EMPTY_FORM,
  project_title: 'Tower',
  delivery_date: '2026-12-01',
  delivery_mode: 'driver',
  ship_to_name: 'Site',
  lines: [
    { key: 'l1', product_id: 'p1', label: 'P1', qty: '1', remark: '' },
  ],
  ...over,
});

describe('sample request form: customer', () => {
  it('passes validation with a typed customer name and no debtor', () => {
    expect(validate(complete({ customer_name: 'Walk-in Sdn Bhd' }))).toEqual({});
  });

  it('refuses when neither a debtor nor a typed name is given', () => {
    expect(validate(complete()).customer_id).toMatch(/customer/i);
  });

  it('sends the typed name only while no debtor is picked', () => {
    expect(payloadFromState(complete({ customer_name: ' Walk-in ' })).customer_name).toBe('Walk-in');
    const picked = payloadFromState(complete({ customer_id: 'c1', customer_name: 'stale' }));
    expect(picked.customer_id).toBe('c1');
    expect(picked.customer_name).toBeNull();
  });

  it('loads a typed customer back into the typed field, a debtor into the picker', () => {
    const base = { lines: [] } as unknown as SampleRequestDetail;
    expect(stateFromDetail({ ...base, customer_id: null, customer_name: 'Walk-in' }).customer_name).toBe('Walk-in');
    expect(stateFromDetail({ ...base, customer_id: 'c1', customer_name: 'DEBTOR' }).customer_name).toBe('');
  });

  it('keeps the inactive flag of a line whose product went inactive', () => {
    const detail = {
      lines: [{ id: 'x', line_no: 1, product_id: 'p', product_code: 'A', product_name: 'a', brand_name: null, product_active: false, qty: 1, remark: null }],
    } as unknown as SampleRequestDetail;
    expect(stateFromDetail(detail).lines[0].inactive).toBe(true);
  });
});

describe('sample request form: number fields refuse, never default', () => {
  const SENTENCE = 'Enter a whole number of at least 1.';

  it.each(['', '0', '2.5', 'NaN'])('reports a line qty of %j as an error and never sends 1', (qty) => {
    const s = complete({ lines: [{ key: 'l1', product_id: 'p1', label: 'P1', qty, remark: '' }] });
    expect(Object.values(validate(s))).toContain(SENTENCE);
    expect(payloadFromState(s).lines[0].qty).not.toBe(1);
  });

  it('passes a valid qty of 3 through as 3 with no error', () => {
    const s = complete({ lines: [{ key: 'l1', product_id: 'p1', label: 'P1', qty: '3', remark: '' }] });
    expect(Object.values(validate(s))).not.toContain(SENTENCE);
    expect(payloadFromState(s).lines[0].qty).toBe(3);
  });

  it.each(['', '0', '2.5', 'NaN'])('reports a loan period of %j as an error and never sends 2', (loan) => {
    const s = complete({ loan_value: loan });
    expect(validate(s).loan_value).toBe(SENTENCE);
    expect(payloadFromState(s).loan_value).not.toBe(2);
  });

  it('passes a valid loan period of 5 through as 5 with no error', () => {
    const s = complete({ loan_value: '5' });
    expect(validate(s).loan_value).toBeUndefined();
    expect(payloadFromState(s).loan_value).toBe(5);
  });
});
