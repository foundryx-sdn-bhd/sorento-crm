/**
 * The convert-to-packing-list dialog (PLAN-pi-header-fields-convert-fixes-24sep.md, design
 * B/C): the ONE table of what goes on the container, a client-side search over it, and the
 * "Carried onto the draft" line naming what the convert will write onto the shipment.
 *
 * The carry line is asserted against a SERVER-PROVIDED `convert_carry` object on the invoice
 * payload (AC-C5/AC-C6, B3) - Phase 1 built this dialog against a LOCAL computation off
 * `container_no`/`seal_no`/`bl_no` + a hardcoded consignee (see the component's own
 * `carryPreview` docstring, "MOCKED (Phase 1)"); that test is expected RED until Phase 2
 * wires the dialog to read `invoice.convert_carry` instead.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  ProformaInvoiceDetail,
  ProformaInvoiceLine,
} from '../../services/proformaInvoiceService';
import type { ProformaInvoicePackingLine } from '../types/packingLine.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}
if (!window.ResizeObserver) {
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});

vi.mock('../../hooks/useFulfilment', () => ({
  useContainerSizes: () => ({
    data: [{ id: 'size-40hq', code: '40HQ', label: '40ft high cube', cbm: 65, is_default: true }],
    isLoading: false,
  }),
}));

const invoiceState = {
  data: undefined as ProformaInvoiceDetail | undefined,
  isLoading: false,
};
vi.mock('../../hooks/useProformaInvoices', () => ({
  useProformaInvoice: () => invoiceState,
}));

const packingState = {
  data: { rows: [] as ProformaInvoicePackingLine[], file: null },
  isLoading: false,
};
vi.mock('../../hooks/useProformaInvoicePacking', () => ({
  useProformaInvoicePacking: () => packingState,
}));

import { ConvertToPackingListDialog } from './ConvertToPackingListDialog';

function line(over: Partial<ProformaInvoiceLine> = {}): ProformaInvoiceLine {
  return {
    id: 'line-1',
    line_no: 1,
    row_number: 2,
    item_code: 'ITEM-1',
    description: 'Widget',
    qty: 10,
    uom: 'PCS',
    unit_price: 100,
    amount: 1000,
    po_ref: null,
    remark: null,
    cartons: 10,
    cbm_per_unit: 0.17,
    cbm_total: 1.7,
    net_weight: 40,
    gross_weight: 50,
    supplier_qty: 10,
    supplier_unit_price: 100,
    placed_qty: 0,
    remaining_qty: 10,
    packing_lists: [],
    matched_by: null,
    match_source: null,
    match_id: null,
    product_id: 'prod-1',
    product_set_id: null,
    product_code: 'ITEM-1',
    set_code: null,
    matched: true,
    shipment_id: null,
    shipment_number: null,
    unmatched_reason: null,
    ...over,
  } as ProformaInvoiceLine;
}

function invoice(over: Partial<ProformaInvoiceDetail> = {}): ProformaInvoiceDetail {
  return {
    id: 'pi-1',
    supplier_id: 'sup-1',
    supplier_code: 'DAFUYUAN',
    supplier_name: 'Dafuyuan Ceramic Industrial Limited',
    pi_number: 'PI-2026-001',
    supplier_ref: 'DFY20260922',
    invoice_date: '2026-09-22',
    currency: 'CNY',
    container_no: 'FSCU9304169',
    seal_no: 'OOLLGZ7182',
    consignee: null,
    bl_no: 'OOLU2339207730',
    total_amount: 110434,
    line_count: 2,
    source_ref: 'dafuyuan.xlsx',
    block_index: 0,
    uploaded_by: 'Ms Tee',
    created_at: '2026-09-22T02:00:00',
    updated_at: '2026-09-22T02:00:00',
    total_cbm: 67.38,
    unmeasured_lines: 0,
    status: 'current',
    revision_no: 1,
    revision_count: 1,
    adjusted_by: null,
    adjusted_at: null,
    is_adjusted: false,
    placement: 'not_converted',
    placed_qty: 0,
    total_qty: 137,
    remaining_qty: 137,
    packing_lists: [],
    lines: [
      line({
        id: 'line-a',
        item_code: 'CWCY604',
        product_code: 'SRT-WATER-TANK',
        description: 'Water tank',
        qty: 40,
        remaining_qty: 40,
      }),
      line({
        id: 'line-b',
        line_no: 2,
        item_code: 'CWCX604-S-RL',
        product_code: 'SRT-TOILET-SEAT',
        description: 'Toilet seat',
        qty: 97,
        remaining_qty: 97,
      }),
    ],
    converted_shipments: [],
    revisions: [],
    revision_of_pi_number: null,
    diff: null,
    ...over,
  } as ProformaInvoiceDetail;
}

function renderDialog(over: Partial<ProformaInvoiceDetail> = {}) {
  invoiceState.data = invoice(over);
  const onConvert = vi.fn();
  const onOpenChange = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <ConvertToPackingListDialog
        open
        onOpenChange={onOpenChange}
        invoiceIds={['pi-1']}
        onConvert={onConvert}
      />
    </QueryClientProvider>,
  );
  return { ...view, onConvert, onOpenChange };
}

describe('ConvertToPackingListDialog', () => {
  it('lists every placeable line once', () => {
    renderDialog();

    expect(screen.getByText('CWCY604')).toBeInTheDocument();
    expect(screen.getByText('CWCX604-S-RL')).toBeInTheDocument();
  });

  it('search narrows the table but leaves the footer totals on the full set (C4)', () => {
    renderDialog();

    // Both lines' qty sum before any search - 40 + 97.
    expect(screen.getByText('137')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Search code or product...'), {
      target: { value: 'CWCY604' },
    });

    // The row that does not match is gone from the table...
    expect(screen.queryByText('CWCX604-S-RL')).toBeNull();
    expect(screen.getByText('CWCY604')).toBeInTheDocument();
    // ...but the footer keeps totalling the FULL set, not the filtered one (search finds,
    // it does not select).
    expect(screen.getByText('137')).toBeInTheDocument();
  });

  it('footer Convert/Cancel buttons are present', () => {
    renderDialog();

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /convert/i })).toBeInTheDocument();
  });

  it('"Carried onto the draft" reads the server-provided convert_carry object, not the PI\'s own fields', () => {
    // AC-C5/AC-C6, B3: the dialog must print exactly what the server's `convert_carry`
    // states - here deliberately DIFFERENT from the PI's own container/seal/bl_no/consignee
    // fields, so a component still reading those (today's Phase 1 mock) is caught reading
    // the wrong source. RED until the coder wires `invoice.convert_carry` in.
    renderDialog({
      container_no: 'WRONG-CONTAINER',
      seal_no: 'WRONG-SEAL',
      bl_no: 'WRONG-BL',
      consignee: 'Wrong Co',
      ...({
        convert_carry: {
          container: 'FSCU9304169',
          seal: 'OOLLGZ7182',
          so: 'OOLU2339207730',
          consignee: 'Sorento',
        },
      } as Partial<ProformaInvoiceDetail>),
    });

    const carryLine = screen.getByText(/Carried onto the draft:/).closest('p');
    expect(carryLine?.textContent).toContain('FSCU9304169');
    expect(carryLine?.textContent).toContain('OOLLGZ7182');
    expect(carryLine?.textContent).toContain('OOLU2339207730');
    expect(carryLine?.textContent).toContain('Sorento');
    expect(carryLine?.textContent).not.toContain('WRONG-CONTAINER');
    expect(carryLine?.textContent).not.toContain('WRONG-SEAL');
    expect(carryLine?.textContent).not.toContain('WRONG-BL');
    expect(carryLine?.textContent).not.toContain('Wrong Co');
  });

  it('V3 (fix round 1): "Carried onto the draft" omits a part that carries nothing, rather than printing "Container -"', () => {
    // Only consignee carries (R-B: always the company, regardless of the container
    // agreement) - container/seal/so are all null. The line must not claim "Container -"
    // for a fact that is not being carried at all; today the `Container {...}` segment
    // renders unconditionally with an em-dash fallback.
    renderDialog({
      ...({
        convert_carry: {
          container: null,
          seal: null,
          so: null,
          consignee: 'Sorento',
        },
      } as Partial<ProformaInvoiceDetail>),
    });

    const carryLine = screen.getByText(/Carried onto the draft:/).closest('p');
    expect(carryLine?.textContent).toContain('Sorento');
    expect(carryLine?.textContent).not.toContain('Container');
  });

  it('V3 (fix round 1): container size, the carried line and search stay OUTSIDE the table\'s own scroll region', () => {
    // C4/AC-C3: the footer stays visible at a short window because only the TABLE scrolls
    // inside the dialog body - the container-size picker, the carried-onto line and the
    // search box are all ABOVE that scroll region, not inside it. Today the whole
    // `DialogBody` (everything from the container-size select down to the table) carries
    // the ONE `overflow-y-auto` region, so the search box is a descendant of it.
    renderDialog({
      ...({
        convert_carry: {
          container: 'FSCU9304169', seal: null, so: null, consignee: 'Sorento',
        },
      } as Partial<ProformaInvoiceDetail>),
    });

    const searchInput = screen.getByPlaceholderText('Search code or product...');
    // Radix's `Dialog` portals its content onto `document.body`, not the RTL `container`
    // the render call returns - the scroll region has to be found there instead.
    const scrollRegion = document.body.querySelector('.overflow-y-auto');
    expect(scrollRegion).not.toBeNull();
    expect(scrollRegion?.contains(searchInput)).toBe(false);
  });

  it('V-SO (owner ruling, 25 Sep): "Carried onto the draft" names SO and BL as two separate facts', () => {
    // SO is its own header field now, distinct from BL (the pre-SO-ruling carry-BL-as-SO
    // rule this dialog used to echo) - convert_carry gains a `bl` key alongside `so`, and
    // the line must name BOTH when the server states both. Today the dialog has no "BL"
    // clause at all: `carryParts` only ever pushes Container/Seal/SO/Consignee, so a
    // `convert_carry.bl` value is silently dropped regardless of what the server sends.
    renderDialog({
      ...({
        convert_carry: {
          container: 'FSCU9304169',
          seal: 'OOLLGZ7182',
          so: 'SO456',
          bl: 'BL123',
          consignee: 'Sorento',
        },
      } as Partial<ProformaInvoiceDetail>),
    });

    const carryLine = screen.getByText(/Carried onto the draft:/).closest('p');
    expect(carryLine?.textContent).toContain('SO SO456');
    expect(carryLine?.textContent).toContain('BL BL123');
  });
});

describe('ConvertToPackingListDialog Quantity to place (B4)', () => {
  it('a blank Quantity to place sends nothing and shows the sentence under the box', () => {
    const { onConvert } = renderDialog();
    const box = screen.getByLabelText('Quantity to place for CWCY604') as HTMLInputElement;
    fireEvent.change(box, { target: { value: '' } });
    expect(box.value).toBe('');
    fireEvent.click(screen.getByRole('button', { name: /convert/i }));
    expect(onConvert).not.toHaveBeenCalled();
    expect(box).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter a number between 0 and 40.')).toBeInTheDocument();
  });

  it('X1: 0 is valid, leaves the line for the next container and sends {lineId: 0}', () => {
    const { onConvert } = renderDialog();
    const box = screen.getByLabelText('Quantity to place for CWCY604') as HTMLInputElement;
    fireEvent.change(box, { target: { value: '0' } });
    expect(box).not.toHaveAttribute('aria-invalid', 'true');
    fireEvent.click(screen.getByRole('button', { name: /convert/i }));
    expect(onConvert).toHaveBeenCalledTimes(1);
    expect(onConvert.mock.calls[0][0].lineQuantities['line-a']).toBe(0);
  });

  it('X1: over the remaining quantity is refused with the between sentence', () => {
    const { onConvert } = renderDialog();
    const box = screen.getByLabelText('Quantity to place for CWCY604') as HTMLInputElement;
    fireEvent.change(box, { target: { value: '41' } });
    fireEvent.click(screen.getByRole('button', { name: /convert/i }));
    expect(onConvert).not.toHaveBeenCalled();
    expect(screen.getByText('Enter a number between 0 and 40.')).toBeInTheDocument();
  });
});


describe('ConvertToPackingListDialog Quantity to place shows its sentence on press (round 3b)', () => {
  it('clearing the box stays quiet until Convert is pressed, then the sentence stays live until valid', () => {
    const { onConvert } = renderDialog();
    const sentence = 'Enter a number between 0 and 40.';
    const box = screen.getByLabelText('Quantity to place for CWCY604') as HTMLInputElement;
    const convert = screen.getByRole('button', { name: /convert/i });

    fireEvent.change(box, { target: { value: '' } });
    expect(screen.queryByText(sentence)).toBeNull();
    expect(box).not.toHaveAttribute('aria-invalid', 'true');
    expect(convert).toBeEnabled();

    fireEvent.click(convert);
    expect(onConvert).not.toHaveBeenCalled();
    expect(screen.getByText(sentence)).toBeInTheDocument();
    expect(box).toHaveAttribute('aria-invalid', 'true');

    fireEvent.change(box, { target: { value: '5' } });
    expect(screen.queryByText(sentence)).toBeNull();
    expect(box).not.toHaveAttribute('aria-invalid', 'true');
  });
});
