/**
 * The proforma-invoice detail page, after the conformance pass.
 *
 * What this pins:
 *
 * - A record header that reads like the purchase order's: the number and its badges on the
 *   left, and on the right the pager, ONE primary CTA, a gear menu holding every secondary
 *   action, and Back. Read-only provenance is a meta line under the title, never a tab body.
 * - Four tabs in a fixed order, the SAME set in view and in edit: editing swaps a value for
 *   an input where the value was.
 * - Editing is a LOCAL DRAFT. A removed line is struck through with an Undo; an added line is
 *   a blank row; nothing at all is written until Save, and Save is ONE call carrying the
 *   number, the container size and the whole line array.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
/* The grace window is the server's; what this file proves is that the control parks one. */
const createPendingAction = vi.fn().mockResolvedValue({
  id: 'pa-1',
  action_key: 'proforma_invoice.delete',
  entity_type: 'proforma_invoice',
  entity_id: 'pi-1',
  commit_at: '2026-08-30T10:00:10',
  window_seconds: 10,
});
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) => createPendingAction(...args),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ProformaInvoiceDetail as ProformaInvoiceDetailData } from '../../../services/proformaInvoiceService';

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

vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
}));

const push = vi.fn();
const replace = vi.fn();
// The tab lives in the URL (S1) - a real `URLSearchParams` so `.get('tab')` and `.toString()`
// both behave, swappable per test to prove a reload lands back on the tab named in it.
let currentSearchParams = new URLSearchParams();
vi.mock('next/navigation', () => ({
  usePathname: () => '/scm/proforma-invoices/pi-1',
  useRouter: () => ({ push, replace }),
  useSearchParams: () => currentSearchParams,
}));

// The WHOLE surface the components under test call. `ConfirmDeleteDialog` reports through
// `toast.custom`, and a mock without it threw inside react-query's own error path - which
// surfaced as an unhandled rejection that failed no test and hid any real one behind it.
vi.mock('@/lib/toast', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    custom: vi.fn(),
    dismiss: vi.fn(),
  },
}));

// The line grid's product picker is server-searched; this suite is not testing the catalogue.
// A `vi.fn()` (not a bare async function) so the AC-B3/AC-B5 tests can hand back a real
// page for the ONE product being picked, without the rest of the suite caring.
const { getProductsMock } = vi.hoisted(() => ({
  getProductsMock: vi.fn().mockResolvedValue({ data: [], pagination: { total: 0 } }),
}));
vi.mock('@/app/(protected)/master-data-management/products/services/productService', () => ({
  getProducts: getProductsMock,
}));

// The master list (AC-B4), not free text - fed to every UoM cell's SearchableSelect.
// `vi.hoisted` because the array has to exist before `vi.mock`'s own hoisting runs it: the
// real `useUOMSelectQuery` is a `useQuery` with `staleTime: Infinity`, so `data` is the SAME
// reference across renders once loaded - a fresh literal returned from the mock on every
// call would fabricate an instability the real hook does not have, and cascade into the
// Lines grid's `columns` memo recomputing (and remounting every row's `SearchableSelect`)
// on every unrelated render.
const { UOM_OPTIONS } = vi.hoisted(() => ({
  UOM_OPTIONS: [
    { id: 'u-pcs', uom_code: 'PCS', uom_name: 'Pieces' },
    { id: 'u-box', uom_code: 'BOX', uom_name: 'Box' },
    { id: 'u-set', uom_code: 'SET', uom_name: 'Set' },
  ],
}));
vi.mock('@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query', () => ({
  useUOMSelectQuery: () => ({
    data: UOM_OPTIONS,
    isLoading: false,
  }),
}));

const state = {
  data: undefined as ProformaInvoiceDetailData | undefined,
  isLoading: false,
  isError: false,
};

/** The writes, so a test can assert what the page ASKED the backend for. */
const writes = {
  matchCode: vi.fn(),
  forgetMatch: vi.fn(),
  save: vi.fn(),
  markAsRevision: vi.fn(),
  convert: vi.fn(),
};

vi.mock('../../../hooks/useProformaInvoices', () => ({
  // The pager reads the list page through the entity's shared key + fetch (S3-03).
  proformaInvoicesPagerQuery: {
    listQueryKey: () => ['scm-proforma-invoices'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  // The real key-builder, not a stub: `useProformaInvoicePacking`'s queryFn reads the
  // detail query's cache through this exact key (AC-E2 fix) - a mock that dropped it
  // left the packing query's `queryFn` throwing on every fetch, silently emptying every
  // dialog/tab that reads packing rows in this suite.
  proformaInvoiceDetailQueryKey: (id: string | null) => ['scm', 'proforma-invoices', 'detail', id],
  useProformaInvoice: () => state,
  // The header's pager pulls the neighbour list through this hook - one row is not enough to
  // show a pager (RecordNavigation's `items.length < 2` guard), so it stays out of the way.
  useProformaInvoices: () => ({ data: undefined, isLoading: false }),
  useConvertProformaInvoicesToDraftShipment: () => ({
    mutateAsync: writes.convert,
    isPending: false,
  }),
  useSaveProformaInvoice: () => ({ mutateAsync: writes.save, isPending: false }),
  useMarkProformaInvoiceAsRevision: () => ({
    mutateAsync: writes.markAsRevision,
    isPending: false,
  }),
}));

vi.mock('../../../hooks/useSupplierCodeAliases', () => ({
  useMatchSupplierCode: () => ({ mutateAsync: writes.matchCode, isPending: false }),
  useForgetSupplierCodeMatch: () => ({ mutateAsync: writes.forgetMatch, isPending: false }),
}));

vi.mock('../../../hooks/useFulfilment', () => ({
  useContainerSizes: () => ({
    data: [
      { id: 'size-40hq', code: '40HQ', label: '40ft high cube', cbm: 65, is_default: true },
      { id: 'size-20gp', code: '20GP', label: '20ft standard', cbm: 28, is_default: false },
    ],
    isLoading: false,
  }),
  // The Packing tab's "Attach packing list" mounts `SupplierDocumentsUploadDialog`
  // (S2), whose self-serve supplier picker reads this hook - irrelevant here since the
  // dialog is opened with `supplierId` already fixed to the invoice's own.
  useFulfilmentSuppliers: () => ({ data: [], isLoading: false }),
}));

import { ProformaInvoiceDetail } from './ProformaInvoiceDetail';

function detail(over: Partial<ProformaInvoiceDetailData> = {}): ProformaInvoiceDetailData {
  return {
    id: 'pi-1',
    supplier_id: 'sup-1',
    supplier_code: 'KAILU',
    supplier_name: 'Kailu Hardware Factory',
    pi_number: 'PI-2026-001',
    // Ours and theirs: `pi_number` is the number we minted, `supplier_ref` the one the
    // factory printed on the document (S1, AC-A2/A5).
    supplier_ref: 'KL20260801',
    invoice_date: '2026-08-01',
    currency: 'CNY',
    container_no: 'TEMU1234567',
    seal_no: 'WHA4528193',
    consignee: 'SORENTO SDN BHD',
    bl_no: 'BL-991',
    total_amount: 1000,
    line_count: 1,
    source_ref: 'proforma.xlsx',
    block_index: 0,
    uploaded_by: 'Ms Tee',
    created_at: '2026-08-01T02:00:00',
    updated_at: '2026-08-01T02:00:00',
    total_cbm: 69.36,
    unmeasured_lines: 0,
    status: 'current',
    revision_no: 1,
    revision_count: 1,
    adjusted_by: null,
    adjusted_at: null,
    is_adjusted: false,
    placement: 'not_converted',
    placed_qty: 0,
    total_qty: 10,
    remaining_qty: 10,
    packing_lists: [],
    lines: [
      {
        id: 'line-1',
        line_no: 1,
        row_number: 2,
        item_code: 'ITEM-1',
        description: 'Widget',
        qty: 10,
        uom: 'PCS',
        unit_price: 100,
        amount: 1000,
        po_ref: 'PO-1',
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
        product_id: 'prod-item-1',
        product_set_id: null,
        product_code: 'ITEM-1',
        set_code: null,
        matched: true,
        shipment_id: null,
        shipment_number: null,
        unmatched_reason: null,
      },
    ],
    converted_shipments: [],
    revisions: [
      {
        id: 'pi-1',
        pi_number: 'PI-2026-001',
        revision_no: 1,
        status: 'current',
        invoice_date: '2026-08-01',
        total_amount: 1000,
        line_count: 1,
      },
    ],
    revision_of_pi_number: null,
    diff: null,
    ...over,
  };
}

/** The last render's `rerender`, so `openTab` can force a re-read of the mocked URL - same
 *  trick `renderDetail`'s own `refresh` uses. */
let currentRerender: (() => void) | null = null;

function renderDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => (
    <QueryClientProvider client={qc}>
      <ProformaInvoiceDetail id="pi-1" />
    </QueryClientProvider>
  );
  const view = render(tree());
  currentRerender = () => view.rerender(tree());
  // The detail hook is mocked, so a write's invalidation cannot be observed the usual way.
  // `refresh` replays what the refetch would do.
  return { ...view, refresh: () => view.rerender(tree()) };
}

/** Radix opens a dropdown on POINTERDOWN, not on click - a plain click is a silent no-op. */
function openActions() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /more actions/i }), {
    button: 0,
    ctrlKey: false,
    pointerType: 'mouse',
  });
}

const TAB_PARAM: Record<'General' | 'Lines' | 'Revisions' | 'Packing lists', string | null> = {
  General: null,
  Lines: 'lines',
  Revisions: 'revisions',
  'Packing lists': 'packing-lists',
};

/**
 * The tab now lives in the URL (S1), not local state, so a click's own re-render depends on
 * the mocked router actually navigating - it doesn't. This sets `?tab=` the way the click's
 * `router.replace` would have written it and re-renders, exactly as `LoadingPlanView.test.tsx`
 * does for the same reason. The writing half of the click itself is pinned separately, in
 * "ProformaInvoiceDetail - the tab lives in the URL (S1)" below.
 */
function openTab(name: 'General' | 'Lines' | 'Revisions' | 'Packing lists') {
  const value = TAB_PARAM[name];
  currentSearchParams = new URLSearchParams(value ? `tab=${value}` : '');
  currentRerender?.();
}

/** Open the gear menu and press Edit, which is where editing starts from now. */
function beginEdit() {
  openActions();
  fireEvent.click(screen.getByRole('menuitem', { name: /^edit$/i }));
}

function lastSavePayload() {
  return writes.save.mock.calls[writes.save.mock.calls.length - 1][0] as {
    pi_number?: string;
    lines?: Array<Record<string, unknown>>;
  };
}

beforeEach(() => {
  state.data = undefined;
  state.isLoading = false;
  state.isError = false;
  push.mockReset();
  replace.mockReset();
  currentSearchParams = new URLSearchParams();
  currentRerender = null;
  writes.save.mockReset().mockResolvedValue(undefined);
  writes.forgetMatch.mockReset().mockResolvedValue(undefined);
  writes.markAsRevision.mockReset().mockResolvedValue(undefined);
  getProductsMock.mockReset().mockResolvedValue({ data: [], pagination: { total: 0 } });
});

/** The Product select is the first combobox in a line's row, the UoM select the second -
 *  column order (`item_code, product, description, qty, uom, ...`) puts Product ahead of
 *  UoM, and only the UoM select carries its own accessible name. */
function lineRow(itemCodeAriaLabel: string): HTMLElement {
  return screen.getByLabelText(itemCodeAriaLabel).closest('tr') as HTMLElement;
}

describe('ProformaInvoiceDetail - loading / error / data states', () => {
  it('shows a loading skeleton while the detail is fetched', () => {
    state.isLoading = true;
    renderDetail();

    expect(screen.queryByText('PI-2026-001')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to proforma invoices/i })).toBeInTheDocument();
  });

  it('says the invoice was not found rather than rendering a blank page', () => {
    state.isError = true;
    renderDetail();

    expect(screen.getByText('Proforma invoice not found')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to proforma invoices/i })).toBeInTheDocument();
  });
});

describe('ProformaInvoiceDetail - the record header', () => {
  it('names the invoice, its currency and where its goods are', () => {
    state.data = detail();
    renderDetail();

    // Twice on purpose: the header title, and the General tab's first field - the number is
    // the first thing the edit view swaps for an input, so it has to be there to swap.
    expect(screen.getAllByText('PI-2026-001')).toHaveLength(2);
    // Same story for the currency: the header badge, and the General tab's Currency field.
    expect(screen.getAllByText('CNY')).toHaveLength(2);
    expect(screen.getByText('Not converted')).toBeInTheDocument();
  });

  it('puts the read-only provenance in a meta line, never inside a tab', () => {
    state.data = detail({ adjusted_by: 'Ms Tee', adjusted_at: '2026-08-02T02:00:00' });
    renderDetail();

    // "proforma.xlsx" now ALSO names an entry in the General tab's own Source files
    // block (S2, AC-B14) - a second, legitimate occurrence, so the meta line itself is
    // found by its own unique text and its source-file mention checked from there.
    const meta = screen.getByText(/Uploaded by Ms Tee/).closest('p')!;
    expect(within(meta).getByText(/proforma\.xlsx/)).toBeInTheDocument();
    expect(within(meta).getByText(/Adjusted by Ms Tee/)).toBeInTheDocument();
  });

  it('offers Convert as the ONE primary action, with everything else in the gear menu', () => {
    state.data = detail();
    renderDetail();

    expect(
      screen.getByRole('button', { name: /convert to packing list/i }),
    ).toBeInTheDocument();
    // Not loose buttons any more: Edit, Export and Delete are behind the gear.
    expect(screen.queryByRole('button', { name: /^edit$/i })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /export adjusted pi/i }),
    ).not.toBeInTheDocument();
  });

  it('the convert dialog asks the container size (S5, ruling 1)', () => {
    state.data = detail();
    renderDetail();

    fireEvent.click(screen.getByRole('button', { name: /convert to packing list/i }));

    expect(screen.getByLabelText('Container size')).toBeInTheDocument();
  });

  it('sends the ticked packing rows, and leaves an unticked one off the draft (AC-D2b, ruling 23)', async () => {
    const row = (id: string, rowNo: number, qty: number) => ({
      id,
      proforma_invoice_line_id: 'line-1',
      row_no: rowNo,
      item_code: 'ITEM-1',
      supplier_code: 'ITEM-1',
      description: 'Widget',
      product_id: 'prod-item-1',
      product_set_id: null,
      qty,
      cartons: 1,
      pcs_per_carton: qty,
      carton_length_cm: null,
      carton_width_cm: null,
      carton_height_cm: null,
      cbm_per_carton: null,
      cbm_total: null,
      net_weight: null,
      gross_weight: null,
      total_net_weight: null,
      total_gross_weight: null,
      material: null,
      container_no: null,
      remark: null,
      match_state: 'matched',
      unmatched_reason: null,
    });
    state.data = detail({
      packing_lines: [row('row-1', 1, 6), row('row-2', 2, 4)],
    } as Partial<ProformaInvoiceDetailData>);
    writes.convert.mockResolvedValue({
      shipment_id: 'sh-1',
      shipment_number: 'PL-2609-001',
      lines_created: 1,
      lines_skipped: 0,
      invoices: [],
      unmatched: [],
      skipped_invoices: [],
    });
    renderDetail();

    fireEvent.click(screen.getByRole('button', { name: /convert to packing list/i }));
    // Both rows start ticked - "everything goes" is the default. Untick the second.
    const second = await screen.findByLabelText(/place row 2 of ITEM-1/i);
    fireEvent.click(second);
    fireEvent.click(screen.getByRole('button', { name: /^Convert$/ }));

    await waitFor(() => expect(writes.convert).toHaveBeenCalled());
    expect(writes.convert.mock.calls[0][0].packingRowIds).toEqual(['row-1']);
  });

  it('lists every secondary action in the gear menu, in one place', () => {
    state.data = detail();
    renderDetail();

    openActions();

    expect(screen.getByRole('menuitem', { name: /^edit$/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /export adjusted pi/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /mark as revision of/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /delete invoice/i })).toBeInTheDocument();
  });

  it('S3-02: leaves Back to the toolbar row, so the record header ends with the primary', () => {
    state.data = detail();
    renderDetail();

    // Back moved to the page's toolbar row (D6); the record header keeps pager,
    // gear and the primary button only.
    expect(
      screen.queryByRole('link', { name: /back to proforma invoices/i }),
    ).not.toBeInTheDocument();
  });

  it('offers "Convert the rest" while something is still to place', () => {
    state.data = detail({ placement: 'split', placed_qty: 4, remaining_qty: 6 });
    renderDetail();

    expect(screen.getByRole('button', { name: /convert the rest/i })).toBeInTheDocument();
  });

  it('offers no convert at all once every line is placed', () => {
    state.data = detail({
      placement: 'converted',
      placed_qty: 10,
      remaining_qty: 0,
      packing_lists: [
        {
          shipment_id: 'sh-1',
          shipment_number: 'FSCU8103365',
          shipment_status: 'draft',
          qty: 10,
          lines: 1,
        },
      ],
    });
    renderDetail();

    expect(screen.queryByRole('button', { name: /convert/i })).not.toBeInTheDocument();
  });
});

describe('ProformaInvoiceDetail - deleting the invoice', () => {
  it('parks the delete and stays put until the server applies it (S6-10)', async () => {
    state.data = detail();
    renderDetail();

    openActions();
    fireEvent.click(screen.getByRole('menuitem', { name: /delete invoice/i }));

    // D7: the menu item IS the action. The page must NOT leave on the click - a
    // record page that returned to the list would be lying for ten seconds, and
    // Cancel would have nowhere to put the reader back.
    await waitFor(() =>
      expect(createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({
          actionKey: 'proforma_invoice.delete',
          entityType: 'proforma_invoice',
          entityId: 'pi-1',
        }),
      ),
    );
    expect(push).not.toHaveBeenCalledWith('/scm/proforma-invoices');
  });

  it('refuses on an invoice already in a packing list, and says why', () => {
    state.data = detail({
      converted_shipments: [{ shipment_id: 'sh-1', shipment_number: 'FSCU8103365' }],
    });
    renderDetail();

    openActions();
    const item = screen.getByRole('menuitem', { name: /delete invoice/i });
    expect(item).toHaveAttribute('data-disabled');
    expect(item).toHaveAttribute('title', expect.stringContaining('FSCU8103365'));
  });
});

describe('ProformaInvoiceDetail - the tab lives in the URL (S1)', () => {
  it('clicking Lines writes ?tab=lines (AC-A1)', () => {
    state.data = detail();
    renderDetail();

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Lines' }), {
      button: 0,
      ctrlKey: false,
    });
    expect(replace).toHaveBeenCalledWith('/scm/proforma-invoices/pi-1?tab=lines', {
      scroll: false,
    });
  });

  it('clicking General from another tab clears ?tab= rather than writing ?tab=general (AC-A1)', () => {
    state.data = detail();
    currentSearchParams = new URLSearchParams('tab=lines');
    renderDetail();

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'General' }), {
      button: 0,
      ctrlKey: false,
    });
    expect(replace).toHaveBeenCalledWith('/scm/proforma-invoices/pi-1', { scroll: false });
  });

  it('a reload on ?tab=revisions lands on Revisions, not General (AC-A3)', () => {
    state.data = detail();
    currentSearchParams = new URLSearchParams('tab=revisions');
    renderDetail();

    expect(screen.getByRole('tab', { name: 'Revisions' })).toHaveAttribute(
      'data-state',
      'active',
    );
    expect(
      screen.getByText('This is the only version the supplier has sent.'),
    ).toBeInTheDocument();
  });

  it('an unrecognised ?tab= falls back to General', () => {
    state.data = detail();
    currentSearchParams = new URLSearchParams('tab=nonsense');
    renderDetail();

    expect(screen.getByRole('tab', { name: 'General' })).toHaveAttribute('data-state', 'active');
  });

  it('pressing Edit while on Lines stays on Lines (AC-A4)', () => {
    state.data = detail();
    currentSearchParams = new URLSearchParams('tab=lines');
    renderDetail();

    beginEdit();

    expect(screen.getByRole('tab', { name: 'Lines' })).toHaveAttribute('data-state', 'active');
    expect(screen.getByLabelText('Item code for line 1')).toBeInTheDocument();
  });
});

describe('ProformaInvoiceDetail - the tabs', () => {
  it('renders five tabs, in a fixed order', () => {
    state.data = detail();
    renderDetail();

    // Packing (S2, AC-B9) sits between Lines and Revisions - the supplier's own packing
    // rows, a different question from the Packing lists tab (which SHIPMENT this
    // invoice's lines went to, once converted).
    const names = screen.getAllByRole('tab').map((t) => t.textContent);
    expect(names).toEqual(['General', 'Lines', 'Packing', 'Revisions', 'Packing lists']);
  });

  it('names each card of the General tab as its own region', () => {
    state.data = detail();
    renderDetail();

    expect(screen.getByRole('region', { name: 'Invoice' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Supplier' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Total volume' })).toBeInTheDocument();
  });

  it('states the total volume as a NUMBER, no capacity, percentage, bar or over-by (S5, ruling 1)', () => {
    state.data = detail();
    renderDetail();

    expect(screen.getByText('69.36 cbm')).toBeInTheDocument();
    expect(screen.queryByText(/40HQ/)).not.toBeInTheDocument();
    expect(screen.queryByText(/% full/)).not.toBeInTheDocument();
    expect(screen.queryByText(/over by/)).not.toBeInTheDocument();
  });

  it('counts the unmeasured lines rather than reading them as an empty container', () => {
    state.data = detail({ total_cbm: null, unmeasured_lines: 2 });
    renderDetail();

    expect(screen.getByText('No volume on this invoice')).toBeInTheDocument();
    expect(screen.getByText('2 unmeasured lines')).toBeInTheDocument();
  });

  it('renders the lines with the weights the supplier stated', () => {
    state.data = detail();
    renderDetail();
    openTab('Lines');

    // ITEM-1 twice: the supplier's own code, and the product it binds to (the same code
    // here, which is what an exact match looks like).
    expect(screen.getAllByText('ITEM-1')).toHaveLength(2);
    expect(screen.getByText('Widget')).toBeInTheDocument();
    expect(screen.getByText('40')).toBeInTheDocument();
    expect(screen.getByText('50')).toBeInTheDocument();
  });

  it('S2: shows Description (EN) beside Description in view mode, dash for null (AC-E1/E3)', () => {
    state.data = detail({
      lines: [
        {
          ...detail().lines[0],
          description: '连体马桶',
          description_en: 'One-piece toilet',
        },
      ],
    });
    renderDetail();
    openTab('Lines');

    expect(screen.getByText('连体马桶')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'One-piece toilet' })).toBeInTheDocument();
  });

  it('S2: renders the SAME Description (EN) cell in edit mode, and editing it does not dirty the line form (AC-E3)', () => {
    state.data = detail({
      lines: [{ ...detail().lines[0], description: '连体马桶', description_en: null }],
    });
    renderDetail();
    openTab('Lines');
    beginEdit();

    // Still the dash-button cell, not folded into the line's own Description <Input>.
    expect(screen.getByRole('button', { name: 'Add English for 连体马桶' })).toBeInTheDocument();
    // The line's own Description field is untouched, still an editable input.
    expect(screen.getByDisplayValue('连体马桶')).toBeInTheDocument();
  });

  it('marks the Packed cell destructive when the packed quantity disagrees with the invoiced one (AC-B11)', async () => {
    // `packing_lines` travels on the SAME detail payload `useProformaInvoicePacking` reads
    // off (`getProformaInvoicePacking`) - no second fetch, so seeding it here is enough.
    state.data = detail({
      packing_lines: [
        {
          id: 'row-1',
          proforma_invoice_line_id: 'line-1',
          row_no: 1,
          item_code: 'ITEM-1',
          supplier_code: 'ITEM-1',
          description: 'Widget',
          product_id: 'prod-item-1',
          product_set_id: null,
          qty: 8,
          cartons: 1,
          pcs_per_carton: 8,
          carton_length_cm: null,
          carton_width_cm: null,
          carton_height_cm: null,
          cbm_per_carton: null,
          cbm_total: null,
          net_weight: null,
          gross_weight: null,
          total_net_weight: null,
          total_gross_weight: null,
          material: null,
          container_no: null,
          remark: null,
          match_state: 'matched',
          unmatched_reason: null,
        },
      ],
    } as Partial<ProformaInvoiceDetailData>);
    renderDetail();
    openTab('Lines');

    // Invoiced 10, packed 8 (from the one row above) - a Badge, not a plain number.
    const packedCell = await screen.findByText('8');
    expect(packedCell).toHaveAttribute('title', 'Invoiced 10, packed 8');
  });

  it('shows "-" in the Packed column when the line has no packing rows at all', () => {
    state.data = detail(); // no packing_lines
    renderDetail();
    openTab('Lines');

    expect(screen.getAllByText('-').length).toBeGreaterThan(0);
  });

  it('states plainly when the invoice has no lines, rather than an empty table', () => {
    state.data = detail({ lines: [], line_count: 0 });
    renderDetail();
    openTab('Lines');

    expect(screen.getByText('This proforma invoice has no lines.')).toBeInTheDocument();
  });

  it('renders the Revisions tab on an original too, with an empty state', () => {
    state.data = detail();
    renderDetail();
    openTab('Revisions');

    expect(
      screen.getByText('This is the only version the supplier has sent.'),
    ).toBeInTheDocument();
  });

  it('renders the Packing lists tab with an empty state and ONE Convert button (header only)', () => {
    // C4/C5 (PLAN-pi-header-fields-convert-fixes-24sep.md): the empty-state's own Convert
    // CTA is removed - only the header's Convert to packing list button exists now. RED
    // until the coder drops `ProformaInvoicePackingListsTab`'s `emptyAction` wiring.
    state.data = detail();
    renderDetail();
    openTab('Packing lists');

    expect(
      screen.getByText('Nothing from this invoice is in a packing list yet.'),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /convert to packing list/i }).length).toBe(1);
  });

  it('names the packing list, and what is left when it is split (Q9)', () => {
    state.data = detail({
      placement: 'split',
      placed_qty: 4,
      remaining_qty: 6,
      packing_lists: [
        {
          shipment_id: 'sh-1',
          shipment_number: 'FSCU8103365',
          shipment_status: 'draft',
          qty: 4,
          lines: 1,
        },
      ],
      lines: [
        {
          ...detail().lines[0],
          placed_qty: 4,
          remaining_qty: 6,
          packing_lists: [{ shipment_id: 'sh-1', shipment_number: 'FSCU8103365', qty: 4 }],
        },
      ],
    });
    renderDetail();
    openTab('Packing lists');

    expect(screen.getByRole('link', { name: /FSCU8103365/ })).toHaveAttribute(
      'href',
      '/procurement-management/packing-lists/sh-1',
    );
    // The tab is the containers this invoice went into and nothing else (ruling 26): the
    // state it is in, and how much of the invoice it carries. What is LEFT is the convert
    // dialog's own table now, so it is no longer restated here.
    // Title case, through the shared status pill the packing lists page uses (ruling 33).
    expect(screen.getByText('Draft')).toBeInTheDocument();
    expect(screen.getByText('4 of 10')).toBeInTheDocument();
  });

  // Ruling 26 moved this off the Packing lists tab, which is now the containers alone. S7
  // then took the Match column with its badges: a line that binds to nothing is an EMPTY
  // Product picker, on the Lines tab, which is also where the reader answers it.
  it('names a line nothing could carry, instead of calling it placed', () => {
    state.data = detail({
      lines: [
        {
          ...detail().lines[0],
          matched: false,
          product_code: null,
          remaining_qty: 0,
          unmatched_reason: "No catalogue product matches this line's item code.",
        },
      ],
    });
    renderDetail();
    openTab('Lines');

    expect(screen.getAllByRole('combobox')[0]).toHaveTextContent('Search a product or set');
  });
});

describe('ProformaInvoiceDetail - editing is a draft until Save', () => {
  it('swaps values for inputs in place, leaving the tabs and their order alone', () => {
    state.data = detail();
    renderDetail();

    expect(screen.queryByLabelText('PI number')).not.toBeInTheDocument();
    beginEdit();

    expect(screen.getByLabelText('PI number')).toHaveValue('PI-2026-001');
    // No Container size field (S5): capacity moved to the convert dialog.
    expect(screen.queryByLabelText('Container size')).not.toBeInTheDocument();
    // Same tabs, same order, mid-edit.
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'General',
      'Lines',
      'Packing',
      'Revisions',
      'Packing lists',
    ]);
  });

  it('states that nothing is written until Save, and offers only Cancel and Save', () => {
    state.data = detail();
    renderDetail();
    beginEdit();

    expect(screen.getByText('Nothing is written until you press Save.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Save proforma invoice$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^cancel$/i })).toBeInTheDocument();
    // Nav and the way out act on the STORED invoice, so they are not offered over a screen
    // full of unsaved changes.
    expect(
      screen.queryByRole('link', { name: /back to proforma invoices/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /more actions/i })).not.toBeInTheDocument();
  });

  it('writes nothing while the quantity is being typed', () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.change(screen.getByLabelText('Quantity for ITEM-1'), { target: { value: '8' } });

    expect(writes.save).not.toHaveBeenCalled();
  });

  it('moves the total volume live as the quantity is typed, before any save', () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');
    fireEvent.change(screen.getByLabelText('Quantity for ITEM-1'), { target: { value: '20' } });
    openTab('General');

    // 20 x 0.17 cbm, computed from the per-unit figure the supplier stated.
    expect(screen.getByText('3.4 cbm')).toBeInTheDocument();
  });

  it('sends the WHOLE line array in one call on Save', async () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');
    fireEvent.change(screen.getByLabelText('Quantity for ITEM-1'), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    await waitFor(() => expect(writes.save).toHaveBeenCalledTimes(1));
    const payload = lastSavePayload();
    expect(payload.pi_number).toBe('PI-2026-001');
    expect(payload.lines).toHaveLength(1);
    expect(payload.lines?.[0]).toMatchObject({
      id: 'line-1',
      item_code: 'ITEM-1',
      qty: 8,
      uom: 'PCS',
      cartons: 10,
      cbm_per_unit: 0.17,
      unit_price: 100,
      net_weight: 40,
      gross_weight: 50,
    });
  });

  it('carries a corrected PI number on the same save', async () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    fireEvent.change(screen.getByLabelText('PI number'), { target: { value: 'PI-REAL-9 ' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    await waitFor(() => expect(writes.save).toHaveBeenCalledTimes(1));
    expect(lastSavePayload().pi_number).toBe('PI-REAL-9');
  });

  it('refuses to save a blank PI number, and writes nothing', async () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    fireEvent.change(screen.getByLabelText('PI number'), { target: { value: '  ' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    await waitFor(() => expect(writes.save).not.toHaveBeenCalled());
    expect(screen.getByLabelText('PI number')).toBeInTheDocument();
  });

  it('marks a removed line struck through, with an Undo, and writes nothing yet', () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('button', { name: /^remove$/i }));

    expect(writes.save).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /^undo$/i })).toBeInTheDocument();
    // Still on screen - it is marked, not gone.
    expect(screen.getByLabelText('Item code for line 1')).toBeInTheDocument();
  });

  it('puts a removed line back on Undo', () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('button', { name: /^remove$/i }));
    fireEvent.click(screen.getByRole('button', { name: /^undo$/i }));

    expect(screen.getByRole('button', { name: /^remove$/i })).toBeInTheDocument();
  });

  it('leaves a removed line OUT of the array on Save', async () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('button', { name: /^remove$/i }));
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    await waitFor(() => expect(writes.save).toHaveBeenCalledTimes(1));
    expect(lastSavePayload().lines).toEqual([]);
  });

  it('adds a blank draft row from Add line, and sends it with no id', async () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('button', { name: /add line/i }));
    fireEvent.change(screen.getByLabelText('Item code for line 2'), {
      target: { value: 'HAND-1' },
    });
    fireEvent.change(screen.getByLabelText('Quantity for HAND-1'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    await waitFor(() => expect(writes.save).toHaveBeenCalledTimes(1));
    const lines = lastSavePayload().lines ?? [];
    expect(lines).toHaveLength(2);
    expect(lines[1]).toMatchObject({ item_code: 'HAND-1', qty: 4 });
    expect(lines[1]).not.toHaveProperty('id');
  });

  it('refuses an added line with no quantity, and writes nothing', async () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('button', { name: /add line/i }));
    fireEvent.change(screen.getByLabelText('Item code for line 2'), {
      target: { value: 'HAND-1' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    await waitFor(() => expect(writes.save).not.toHaveBeenCalled());
  });

  it('offers no Add line while only reading', () => {
    state.data = detail();
    renderDetail();
    openTab('Lines');

    expect(screen.queryByRole('button', { name: /add line/i })).not.toBeInTheDocument();
  });

  it('discards the whole draft on Cancel', () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    fireEvent.change(screen.getByLabelText('PI number'), { target: { value: 'PI-WRONG' } });
    openTab('Lines');
    fireEvent.change(screen.getByLabelText('Quantity for ITEM-1'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

    expect(writes.save).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Quantity for ITEM-1')).not.toBeInTheDocument();
    openTab('General');
    expect(screen.getAllByText('PI-2026-001')).toHaveLength(2);
    expect(screen.queryByText('PI-WRONG')).not.toBeInTheDocument();
  });

  it('offers no Edit at all on an invoice already in a packing list', () => {
    state.data = detail({
      converted_shipments: [{ shipment_id: 'sh-1', shipment_number: 'FSCU8103365' }],
    });
    renderDetail();

    openActions();
    expect(screen.queryByRole('menuitem', { name: /^edit$/i })).not.toBeInTheDocument();
  });
});

describe('F5b - revisions', () => {
  const revised = () =>
    detail({
      pi_number: 'PI-2026-001-R2',
      status: 'current',
      revision_no: 2,
      revision_count: 2,
      revision_of_pi_number: 'PI-2026-001',
      revisions: [
        {
          id: 'pi-0',
          pi_number: 'PI-2026-001',
          revision_no: 1,
          status: 'superseded',
          invoice_date: '2026-07-20',
          total_amount: 900,
          line_count: 1,
        },
        {
          id: 'pi-1',
          pi_number: 'PI-2026-001-R2',
          revision_no: 2,
          status: 'current',
          invoice_date: '2026-08-01',
          total_amount: 1000,
          line_count: 1,
        },
      ],
      diff: {
        compared_to_id: 'pi-0',
        compared_to_pi_number: 'PI-2026-001',
        price_changed_lines: 1,
        qty_changed_lines: 0,
        added_lines: 0,
        removed_lines: 0,
        changes: [
          {
            item_code: 'ITEM-1',
            occurrence: 1,
            description: 'Widget',
            status: 'changed' as const,
            qty_was: 10,
            qty_now: 10,
            qty_changed: false,
            unit_price_was: 90,
            unit_price_now: 100,
            unit_price_changed: true,
            amount_was: 900,
            amount_now: 1000,
          },
        ],
      },
    });

  it('says which revision this is, in the header and in the tab (AC-E7)', () => {
    state.data = revised();
    renderDetail();

    expect(screen.getByText('Revision 2 of 2')).toBeInTheDocument();
    openTab('Revisions');
    expect(screen.getAllByText('Revision 2 of 2')).toHaveLength(2);
    expect(screen.getByText('Revision 1 - superseded')).toBeInTheDocument();
  });

  it('names how many lines the supplier repriced, and by what (AC-E8)', () => {
    state.data = revised();
    renderDetail();
    openTab('Revisions');

    expect(screen.getByText('Price changed on 1 line')).toBeInTheDocument();
    expect(screen.getByText(/against PI-2026-001/)).toBeInTheDocument();
    expect(screen.getByText('CNY 90.00')).toBeInTheDocument();
  });

  it('offers no Edit and no Convert on a superseded revision (AC-E7, AC-E10)', () => {
    state.data = detail({ status: 'superseded' });
    renderDetail();

    expect(screen.getByText('Superseded')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /convert to packing list/i }),
    ).not.toBeInTheDocument();
    openActions();
    expect(screen.queryByRole('menuitem', { name: /^edit$/i })).not.toBeInTheDocument();
  });

  it('offers to link a mis-filed new PI to the one it revises (AC-E11)', () => {
    state.data = detail();
    renderDetail();

    openActions();
    expect(screen.getByRole('menuitem', { name: /mark as revision of/i })).toBeInTheDocument();
  });

  it('does not offer to link one that is already a revision', () => {
    state.data = revised();
    renderDetail();

    openActions();
    expect(
      screen.queryByRole('menuitem', { name: /mark as revision of/i }),
    ).not.toBeInTheDocument();
  });
});

describe('F11 - answering a supplier code by hand', () => {
  // S7: one always-on picker in the Product column, no second door through a Match column.
  it('offers the product-or-set picker on a line that binds to nothing, in read mode', () => {
    state.data = detail({
      lines: [
        {
          ...detail().lines[0],
          matched: false,
          product_code: null,
          unmatched_reason: "No catalogue product matches this line's item code.",
        },
      ],
    });
    renderDetail();
    openTab('Lines');

    expect(screen.queryByRole('button', { name: /match to product/i })).not.toBeInTheDocument();
    expect(screen.getAllByRole('combobox')[0]).toHaveTextContent('Search a product or set');
  });

  // S7: the rung is no longer badged on the line - what the reader acts on is the code the
  // ruling points at, in the picker that can change it.
  it('shows what a bind the ladder worked out points at, with no badge and no Change button', () => {
    state.data = detail({
      lines: [
        { ...detail().lines[0], match_source: 'auto', matched_by: 'token_set', match_id: 'a-1' },
      ],
    });
    renderDetail();
    openTab('Lines');

    expect(screen.getAllByRole('combobox')[0]).toHaveTextContent('ITEM-1');
    expect(screen.queryByText('auto')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^change$/i })).not.toBeInTheDocument();
  });

  it('says nothing about a code that matched exactly', () => {
    state.data = detail();
    renderDetail();
    openTab('Lines');

    expect(screen.queryByText('auto')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^change$/i })).not.toBeInTheDocument();
  });

  it('parks the forget on the RULING, never on the line (S6-10)', async () => {
    state.data = detail({
      lines: [
        {
          ...detail().lines[0],
          item_code: 'SRTWC8357-RL-300',
          product_code: 'SRTWC8357-300-RL',
          match_source: 'auto',
          matched_by: 'token_set',
          match_id: 'alias-1',
        },
      ],
    });
    renderDetail();
    openTab('Lines');

    // S7: clearing the picker IS the forget - there is no Forget button beside it any more.
    fireEvent.pointerDown(
      within(screen.getAllByRole('combobox')[0]).getByRole('button', {
        name: 'Clear selection',
      }),
    );

    // D7: the press IS the action, and the entity is the ALIAS row - forgetting is
    // a ruling being withdrawn, not a line being edited.
    await waitFor(() =>
      expect(createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({
          actionKey: 'supplier_code_alias.forget',
          entityType: 'supplier_code_alias',
          entityId: 'alias-1',
        }),
      ),
    );
    expect(writes.forgetMatch).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

});

describe('S2 - the Product select carries a matched line through edit, and UoM comes off the master list', () => {
  it('AC-B2: a line matched to a PRODUCT shows the product code as the select value', () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    const productCombo = within(lineRow('Item code for line 1')).getAllByRole('combobox')[0];
    expect(productCombo).toHaveTextContent('ITEM-1');
  });

  it('AC-B2: a line matched to a SET shows the set code as the select value', () => {
    state.data = detail({
      lines: [
        {
          ...detail().lines[0],
          product_id: null,
          product_set_id: 'set-1',
          product_code: 'SET-1',
          set_code: 'SET-1',
        },
      ],
    });
    renderDetail();
    beginEdit();
    openTab('Lines');

    const productCombo = within(lineRow('Item code for line 1')).getAllByRole('combobox')[0];
    expect(productCombo).toHaveTextContent('SET-1');
  });

  it('AC-B3: editing only the qty and saving sends a line with NO product_id / product_set_id key', async () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.change(screen.getByLabelText('Quantity for ITEM-1'), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    await waitFor(() => expect(writes.save).toHaveBeenCalledTimes(1));
    const line = lastSavePayload().lines?.[0] ?? {};
    // An untouched Product select leaves the key OUT of the payload entirely - this is
    // the S2 regression (#579): sending it back as `null` unconditionally is what
    // silently unbound every matched product on a plain quantity save.
    expect(line).not.toHaveProperty('product_id');
    expect(line).not.toHaveProperty('product_set_id');
  });

  // S7 replaced the old "clear the select, Save, product_id: null" round trip on a CODED
  // line: what such a line carries is a supplier-code ruling, and the only thing clearing
  // could mean is withdrawing it (AC-7.5). A line nothing was ever ruled on has nothing to
  // withdraw, so it offers no clear at all - and its binding is not unbound from here.
  it('AC-7.5: a coded line with no remembered ruling offers no clear', () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    const productCombo = within(lineRow('Item code for line 1')).getAllByRole('combobox')[0];
    expect(productCombo).toHaveTextContent('ITEM-1');
    expect(
      within(productCombo).queryByRole('button', { name: 'Clear selection' }),
    ).not.toBeInTheDocument();
  });

  // A line the supplier gave no code for: no ruling to make, so the pick is still a draft
  // the Save writes (AC-7.6).
  it('AC-B3: picking a different product sends the new id, and product_set_id: null for a line that was set-bound', async () => {
    getProductsMock.mockResolvedValueOnce({
      data: [{ id: 'prod-99', product_code: 'NEWCODE', product_name: 'New product' }],
      pagination: { total: 1 },
    });
    state.data = detail({
      lines: [
        {
          ...detail().lines[0],
          item_code: '',
          product_id: null,
          product_set_id: 'set-1',
          product_code: 'SET-1',
          set_code: 'SET-1',
        },
      ],
    });
    renderDetail();
    beginEdit();
    openTab('Lines');

    const productCombo = within(lineRow('Item code for line 1')).getAllByRole('combobox')[0];
    fireEvent.click(productCombo);
    fireEvent.click(await screen.findByRole('option', { name: 'NEWCODE - New product' }));
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    await waitFor(() => expect(writes.save).toHaveBeenCalledTimes(1));
    const line = lastSavePayload().lines?.[0] ?? {};
    expect(line.product_id).toBe('prod-99');
    expect(line.product_set_id).toBeNull();
  });

  it('AC-B4: the UoM cell in edit mode renders the master UoM options', async () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('combobox', { name: 'UOM for ITEM-1' }));

    expect(await screen.findByRole('option', { name: 'BOX' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'SET' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'PCS' })).toBeInTheDocument();
  });

  it('AC-B4: picking a UoM writes the chosen code into the payload', async () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('combobox', { name: 'UOM for ITEM-1' }));
    fireEvent.click(await screen.findByRole('option', { name: 'BOX' }));
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    await waitFor(() => expect(writes.save).toHaveBeenCalledTimes(1));
    expect(lastSavePayload().lines?.[0]).toMatchObject({ uom: 'BOX' });
  });

  it('AC-B5: adding a line and picking a product with a base UoM defaults UoM when the cell is blank', async () => {
    getProductsMock.mockResolvedValueOnce({
      data: [
        {
          id: 'prod-hand',
          product_code: 'HAND-1',
          product_name: 'Hand basin',
          base_uom: { id: 'u-box', uom_code: 'BOX', uom_name: 'Box' },
        },
      ],
      pagination: { total: 1 },
    });
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');

    fireEvent.click(screen.getByRole('button', { name: /add line/i }));
    const productCombo = within(lineRow('Item code for line 2')).getAllByRole('combobox')[0];
    fireEvent.click(productCombo);
    fireEvent.click(await screen.findByRole('option', { name: 'HAND-1 - Hand basin' }));

    // Re-queried after the pick (not a reference captured before it), the same caution
    // "SalesOrderDetail.test.tsx" states for its own async-resolved UoM select - a row
    // re-render on patch can swap the node out from under a stale handle.
    const uomCombo = within(lineRow('Item code for line 2')).getAllByRole('combobox')[1];
    expect(uomCombo).toHaveTextContent('BOX');
  });
});

describe('ProformaInvoiceDetail - a refused optional figure marks its field (S9)', () => {
  it('a negative Cartons is refused on the box itself with aria-invalid and the sentence, nothing saved', () => {
    state.data = detail();
    renderDetail();
    beginEdit();
    openTab('Lines');
    const box = screen.getByLabelText('Cartons for ITEM-1');
    fireEvent.change(box, { target: { value: '-5' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save proforma invoice$/i }));

    expect(writes.save).not.toHaveBeenCalled();
    expect(box).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter a number of at least 0, or leave it blank.')).toBeInTheDocument();
  });
});
