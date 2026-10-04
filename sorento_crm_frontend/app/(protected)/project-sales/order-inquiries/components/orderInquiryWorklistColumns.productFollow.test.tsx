/**
 * OI-PRODUCT-FOLLOW (`documentation/plans/scm/PLAN-oi-product-follow-2oct.md`, S3): when
 * Confirm moved a row onto the SO line's new product, the Product cell shows the new code
 * and the old one muted under it ("was X"), the same way a qty/date change keeps its old
 * value visible. One cell serves both the worklist and the OI detail page's Lines tab.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ItemCodeCell } from './orderInquiryWorklistColumns';
import type { OrderInquiryWorklistRow } from '../../_shared/types/orderInquiry.types';

function worklistRow(over: Partial<OrderInquiryWorklistRow> = {}): OrderInquiryWorklistRow {
  return {
    id: 'row-1',
    qty: '10',
    state: 'raised',
    verb: 'ORDER',
    links: [],
    linked_qty: '0',
    item_code: 'MWCY7604-SH',
    product_name: 'MWCY7604-SH',
    ...over,
  } as OrderInquiryWorklistRow;
}

describe('ItemCodeCell shows the product a Confirm moved the row off', () => {
  it('prints the new code and "was <old>" under it', () => {
    render(<ItemCodeCell row={worklistRow({ previous_item_code: 'MWCY7604' })} codeOnly />);
    expect(screen.getByText('MWCY7604-SH')).toBeTruthy();
    expect(screen.getByText('was MWCY7604')).toBeTruthy();
  });

  it('prints no "was" when the product never moved', () => {
    render(<ItemCodeCell row={worklistRow({ previous_item_code: null })} />);
    expect(screen.queryByText(/^was /)).toBeNull();
  });

  it('prints no "was" when the old code equals the current one', () => {
    render(<ItemCodeCell row={worklistRow({ previous_item_code: 'MWCY7604-SH' })} />);
    expect(screen.queryByText(/^was /)).toBeNull();
  });
});
