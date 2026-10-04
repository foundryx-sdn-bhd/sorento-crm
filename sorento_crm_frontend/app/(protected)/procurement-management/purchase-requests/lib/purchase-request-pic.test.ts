/**
 * PIC reaches the Excel export, for both form types.
 *
 * Background: there was nowhere to record the person receiving the delivery, so
 * staff typed them onto the end of the delivery address
 * ("... Pulau Pinang Contact: CONTACT Q (012-000 0502)"). PIC is now its own field,
 * and it has to survive into every export or people will go straight back to
 * putting it in the address.
 *
 * The export builds an array-of-rows and hands it to the sheet writer, so the
 * assertion is on the rows: PIC present, and positioned directly under Customer
 * Name so the printed order matches the form.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const addedSheets: { name: string; rows: unknown[][] }[] = [];

vi.mock('xlsx', () => {
  const utils = {
    book_new: () => ({}),
    aoa_to_sheet: (rows: unknown[][]) => ({ __rows: rows }),
    book_append_sheet: (_wb: unknown, ws: { __rows: unknown[][] }, name: string) => {
      addedSheets.push({ name, rows: ws.__rows });
    },
    encode_cell: () => 'A1',
  };
  return { utils, writeFile: vi.fn(), write: vi.fn(() => new Uint8Array()), default: { utils } };
});

import { exportPurchaseRequestOrSponsorshipToExcel } from './purchase-request-excel-export';

function request(over: Record<string, unknown> = {}) {
  return {
    id: 'pr-1',
    request_type: 'purchase_request',
    request_number: 'PR26-0332',
    customer_name: 'KEE LIN TRADING SDN BHD',
    pic: 'CONTACT Q (012-000 0502)',
    delivery_address: '2, Lebuh Cecil, Ghaut, 10300 George Town',
    project_title: 'ECO SUMMIT',
    lines: [],
    ...over,
  } as never;
}

function flatRows() {
  return addedSheets.flatMap((s) => s.rows);
}

describe('PIC in the PR / SF Excel export', () => {
  beforeEach(() => {
    addedSheets.length = 0;
  });

  it.each([['purchase_request'], ['sponsorship_form']])(
    'writes the PIC row for %s',
    async (request_type) => {
      await exportPurchaseRequestOrSponsorshipToExcel(request({ request_type }));

      const pic = flatRows().find((r) => String(r?.[0]).startsWith('PIC'));
      expect(pic, 'no PIC row in the exported sheet').toBeTruthy();
      expect(String(pic?.[1])).toContain('CONTACT Q');
    },
  );

  it('puts PIC directly under Customer Name, matching the form', async () => {
    await exportPurchaseRequestOrSponsorshipToExcel(request());

    const rows = flatRows();
    const customer = rows.findIndex((r) => String(r?.[0]).startsWith('Customer Name'));
    const pic = rows.findIndex((r) => String(r?.[0]).startsWith('PIC'));
    expect(customer).toBeGreaterThanOrEqual(0);
    expect(pic).toBe(customer + 1);
  });

  it('renders an empty PIC without printing "null"', async () => {
    await exportPurchaseRequestOrSponsorshipToExcel(request({ pic: null }));

    const pic = flatRows().find((r) => String(r?.[0]).startsWith('PIC'));
    expect(pic).toBeTruthy();
    expect(String(pic?.[1]).toLowerCase()).not.toContain('null');
  });
});

describe('Sales Type in the PR Excel export', () => {
  beforeEach(() => {
    addedSheets.length = 0;
  });

  it('writes the resolved label, not the stored code', async () => {
    // `sales_type` holds `cash_sales`; the screen shows "Cash Sales" via
    // LookupBoundLabel. A sheet printing the code would disagree with the screen.
    await exportPurchaseRequestOrSponsorshipToExcel(
      request({ sales_type: 'cash_sales' }),
      'Cash Sales',
    );

    const row = flatRows().find((r) => String(r?.[0]).startsWith('Sales Type'));
    expect(row, 'no Sales Type row in the exported sheet').toBeTruthy();
    expect(String(row?.[1])).toBe('Cash Sales');
  });

  it('falls back to the stored value when no label resolved', async () => {
    // A missing binding must still put something on the page - a code beats a blank.
    await exportPurchaseRequestOrSponsorshipToExcel(request({ sales_type: 'cash_sales' }));

    const row = flatRows().find((r) => String(r?.[0]).startsWith('Sales Type'));
    expect(String(row?.[1])).toBe('cash_sales');
  });

  it('omits Sales Type from a sponsorship form', async () => {
    // The field is Purchase Request only; an empty row on the SF would imply the
    // form has a sales type that nobody filled in.
    await exportPurchaseRequestOrSponsorshipToExcel(
      request({ request_type: 'sponsorship_form', sales_type: 'cash_sales' }),
      'Cash Sales',
    );

    expect(flatRows().find((r) => String(r?.[0]).startsWith('Sales Type'))).toBeFalsy();
  });
});
