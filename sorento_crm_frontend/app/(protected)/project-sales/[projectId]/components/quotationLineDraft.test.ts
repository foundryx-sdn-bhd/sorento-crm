/**
 * SET-QUOTE-LINE (A2b, A7, F2): the draft carries the SET a line names, and the Complete set text
 * the screen pre-fills is built the same way the server builds it, so a line saved from the grid
 * and a line saved without it read alike.
 */
import { describe, expect, it } from 'vitest';
import type { QuotationLine } from '../../_shared/types/project.types';
import {
  completeSetText,
  emptyDraft,
  lineErrors,
  serverToDraft,
  toBody,
} from './quotationLineDraft';

function line(overrides: Partial<QuotationLine> = {}): QuotationLine {
  return {
    id: 'l1',
    version_id: 'v1',
    product_id: null,
    product_set_id: null,
    set_parts: [],
    unit_price: '900.00',
    quantity: '10.00',
    ...overrides,
  } as QuotationLine;
}

describe('completeSetText', () => {
  it('joins the part codes with " + " in the order given', () => {
    expect(
      completeSetText([
        { product_code: 'CWCX1009-RL', quantity: '1' },
        { product_code: 'CWCY1009', quantity: '1' },
        { product_code: 'CWC1009-SC', quantity: '1' },
      ]),
    ).toBe('CWCX1009-RL + CWCY1009 + CWC1009-SC');
  });

  it('reads a part with a quantity other than one as "2 x CODE"', () => {
    expect(
      completeSetText([
        { product_code: 'CWCX1009-RL', quantity: '1' },
        { product_code: 'CWC1009-SC', quantity: '2' },
      ]),
    ).toBe('CWCX1009-RL + 2 x CWC1009-SC');
    expect(completeSetText([{ product_code: 'AB', quantity: 3 }])).toBe('3 x AB');
  });

  it('treats the server decimal strings "1.0000" and "2.0000" as the numbers 1 and 2', () => {
    expect(
      completeSetText([
        { product_code: 'AB', quantity: '1.0000' },
        { product_code: 'CD', quantity: '2.0000' },
      ]),
    ).toBe('AB + 2 x CD');
  });

  it('is cut at the 100 characters the column holds', () => {
    const parts = Array.from({ length: 6 }, (_, index) => ({
      product_code: `LONG-PART-CODE-NUMBER-${index}-XXXXXXXX`,
      quantity: '1',
    }));
    const full = parts.map((part) => part.product_code).join(' + ');
    expect(full.length).toBeGreaterThan(100);
    const text = completeSetText(parts);
    expect(text).toHaveLength(100);
    expect(text).toBe(full.slice(0, 100));
  });

  it('is empty for a set with no parts', () => {
    expect(completeSetText([])).toBe('');
  });
});

describe('the draft of a set line', () => {
  it('starts a new row with no set', () => {
    expect(emptyDraft().product_set_id).toBe('');
  });

  it('maps the stored product_set_id into the draft, and a product line to empty', () => {
    expect(serverToDraft(line({ product_set_id: 'set-1' })).product_set_id).toBe('set-1');
    expect(serverToDraft(line({ product_id: 'p1' })).product_set_id).toBe('');
  });

  it('sends product_set_id as the string it holds, and null when there is none', () => {
    const setBody = toBody(
      { ...emptyDraft(), product_set_id: 'set-1', unit_price: '900', quantity: '10' },
      null,
    );
    expect(setBody.product_set_id).toBe('set-1');
    expect(setBody.product_id).toBeNull();

    const productBody = toBody({ ...emptyDraft(), product_id: 'p1' }, null);
    expect(productBody.product_set_id).toBeNull();
    expect(productBody.product_id).toBe('p1');
  });

  it('does not ask a set line for a description (the set name is the description)', () => {
    expect(lineErrors({ ...emptyDraft(), product_set_id: 'set-1' })).toEqual({});
    expect(lineErrors(emptyDraft())).toEqual({
      description: 'A non-standard item needs a description',
    });
  });
});
