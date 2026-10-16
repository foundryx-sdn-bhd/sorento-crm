import { describe, it, expect, vi, beforeEach } from 'vitest';

import type { PurchaseRequest } from '../types/purchaseRequest.types';

// Capture the array-of-arrays handed to aoa_to_sheet (and the download
// filename) so we can assert on the exact rows the export builds, without
// producing a real .xlsx download.
const captured: { aoa: (string | number)[][] | null; filename: string | null } = {
  aoa: null,
  filename: null,
};

vi.mock('xlsx', () => ({
  utils: {
    aoa_to_sheet: (aoa: (string | number)[][]) => {
      captured.aoa = aoa;
      return {} as unknown;
    },
    book_new: () => ({}) as unknown,
    book_append_sheet: () => undefined,
  },
  writeFile: (_wb: unknown, filename: string) => {
    captured.filename = filename;
  },
}));

import {
  exportSponsorshipFormToExcel,
  exportPurchaseRequestToExcel,
  buildPurchaseRequestOrSponsorshipAoa,
} from './purchase-request-excel-export';

function makeRequest(overrides: Partial<PurchaseRequest> = {}): PurchaseRequest {
  return {
    id: 'r1',
    request_number: 'PSSF26-0326',
    request_type: 'sponsorship_form',
    customer_name: 'BEDI Development Sdn Bhd',
    lines: [
      {
        id: 'l1',
        item_code: 'SRTUB6203',
        quantity: 2,
        unit_price: 60,
        total: 120,
        remark: 'Ceramic waste - urgent',
      },
    ],
    ...overrides,
  } as unknown as PurchaseRequest;
}

describe('exportSponsorshipFormToExcel', () => {
  beforeEach(() => {
    captured.aoa = null;
    captured.filename = null;
  });

  it('includes a Remark column in the line-items header', async () => {
    await exportSponsorshipFormToExcel(makeRequest());
    const header = captured.aoa!.find((r) => r[0] === 'NO.');
    expect(header).toEqual(['NO.', 'Item Code', 'Qty', 'U/P', 'Total', 'Remark']);
  });

  it('writes each line-item remark into the last column', async () => {
    await exportSponsorshipFormToExcel(makeRequest());
    const row = captured.aoa!.find((r) => r[0] === 1);
    expect(row?.[row.length - 1]).toBe('Ceramic waste - urgent');
  });

  it('keeps the Grand Total row aligned under Total (Remark cell blank)', async () => {
    await exportSponsorshipFormToExcel(makeRequest());
    const gt = captured.aoa!.find((r) => r.includes('Grand Total:'));
    expect(gt).toEqual(['', '', '', 'Grand Total:', '120.00', '']);
  });

  it('renders a blank remark when the line has none', async () => {
    await exportSponsorshipFormToExcel(
      makeRequest({
        lines: [
          { id: 'l1', item_code: 'X', quantity: 1, unit_price: 10, total: 10 },
        ] as unknown as PurchaseRequest['lines'],
      }),
    );
    const row = captured.aoa!.find((r) => r[0] === 1);
    expect(row?.[row.length - 1]).toBe('');
  });
});

describe('exportPurchaseRequestToExcel (unchanged - regression guard)', () => {
  beforeEach(() => {
    captured.aoa = null;
    captured.filename = null;
  });

  it('still has its Remark column', async () => {
    await exportPurchaseRequestToExcel(
      makeRequest({ request_type: 'purchase_request' }),
    );
    const header = captured.aoa!.find((r) => r[0] === '#');
    expect(header).toEqual(['#', 'Item Code', 'Qty', 'Remark']);
  });
});

/**
 * The sheet is one of the surfaces that must not disagree with the screens
 * (UAC N4/N5): a revised record shows its derived `-R{n}` suffix in the number
 * cell and in the file it downloads as. Revision 0 stays bare - there is no
 * `-R0` (N3).
 */
describe('revision suffix on the exported document number', () => {
  beforeEach(() => {
    captured.aoa = null;
    captured.filename = null;
  });

  it('suffixes the purchase request number cell and the filename', async () => {
    await exportPurchaseRequestToExcel(
      makeRequest({
        request_type: 'purchase_request',
        request_number: 'PR26-0332',
        revision_no: 2,
      }),
    );
    const numberRow = captured.aoa!.find((r) => r[0] === 'Purchase request number:');
    expect(numberRow?.[1]).toBe('PR26-0332-R2');
    expect(captured.filename).toBe('Purchase_Request_PR26-0332-R2.xlsx');
  });

  it('suffixes the sponsorship form number cell and the filename', async () => {
    await exportSponsorshipFormToExcel(makeRequest({ revision_no: 3 }));
    const numberRow = captured.aoa!.find((r) => r[0] === 'Sponsorship form number:');
    expect(numberRow?.[1]).toBe('PSSF26-0326-R3');
    expect(captured.filename).toBe('Sponsorship_Form_PSSF26-0326-R3.xlsx');
  });

  it('leaves a never-revised record bare', async () => {
    await exportPurchaseRequestToExcel(
      makeRequest({
        request_type: 'purchase_request',
        request_number: 'PR26-0332',
        revision_no: 0,
      }),
    );
    const numberRow = captured.aoa!.find((r) => r[0] === 'Purchase request number:');
    expect(numberRow?.[1]).toBe('PR26-0332');
    expect(captured.filename).toBe('Purchase_Request_PR26-0332.xlsx');
  });
});

describe('sponsorship form Sales Type row', () => {
  it('puts Sales Type immediately after Sponsor Subject', () => {
    const aoa = buildPurchaseRequestOrSponsorshipAoa(
      makeRequest({ sponsor_subject: 'event', sales_type: 'cash_sales' } as Partial<PurchaseRequest>),
      'Cash Sales',
    );
    const i = aoa.findIndex((r) => r[0] === 'Sponsor Subject:');
    expect(i).toBeGreaterThan(-1);
    expect(aoa[i + 1]).toEqual(['Sales Type:', 'Cash Sales']);
  });

  it('is a blank value when the SF has no sales type and no label', () => {
    const aoa = buildPurchaseRequestOrSponsorshipAoa(makeRequest(), null);
    expect(aoa.find((r) => r[0] === 'Sales Type:')).toEqual(['Sales Type:', '']);
  });
});
