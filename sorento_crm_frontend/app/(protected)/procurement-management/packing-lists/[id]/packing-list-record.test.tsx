/**
 * The packing list reads like a user record: one toolbar over routed tabs.
 *
 * What is under test here, in the order the brief asks for it:
 *  - the six tabs, in order, and the toolbar that sits above them (prev/next, the ONE
 *    primary action, the occasional ones behind a gear, Back);
 *  - the Proforma invoices tab that replaces the old one-line "Origin" card;
 *  - the Shipment lines editor and its new measurement columns, which is where every
 *    figure the container workbook derives actually comes from;
 *  - and the edit draft, which now lives above all six tabs because Edit is on the
 *    toolbar - so a save started on Details still writes the lines in the same PUT.
 *
 * A mixed container throughout: two factories on the lines and nobody on the header, which
 * is the shape that used to make this page say "No supplier" about a full container.
 */
import React, { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from '@/lib/toast';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const routerState = {
  pathname: '/procurement-management/packing-lists/pl-1',
  push: vi.fn(),
  // Watched as well as `push` (R19): an edit that moved the operator off the tab they
  // pressed Edit on would do it with either.
  replace: vi.fn(),
  // The list position (page/sort/search) the record pager reads back off the URL - a
  // tab click must not drop it (see "the toolbar over the tabs" below).
  search: '',
};

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerState.push, replace: routerState.replace, back: vi.fn() }),
  usePathname: () => routerState.pathname,
  useSearchParams: () => new URLSearchParams(routerState.search),
}));

// `ConfirmDeleteDialog` reports through `toast.custom`; a mock without it throws inside
// react-query's error path and surfaces as an unhandled rejection that fails no test.
vi.mock('@/lib/toast', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
    custom: vi.fn(),
    dismiss: vi.fn(),
  },
}));

/** The signed-in user's permission slugs. Every SCM read on this page (the proforma invoices
 *  behind the container, the SPO planner, the consolidated build, line photos, container
 *  sizes) sits behind `scm.dashboard.view`; a procurement-only user has none of them. */
const perms = { set: new Set<string>(['scm.dashboard.view']), isLoading: false };
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({
    permissions: [...perms.set],
    permissionSet: perms.set,
    isLoading: perms.isLoading,
  }),
  useHasPermission: (slug: string) => !perms.isLoading && perms.set.has(slug),
}));

/** What each SCM read was asked for, so a test can tell "never fetched" from "fetched". */
const calls = {
  sourceInvoices: [] as unknown[][],
  consolidated: [] as unknown[],
  linePhotos: [] as unknown[],
  containerSizes: [] as unknown[][],
};

const state = {
  packingList: null as unknown,
  /** The proforma invoices behind the container - four readings of one payload. */
  sourceInvoices: undefined as unknown,
  /** That read failed (a 5xx, say): Documents must not read it as "none". */
  sourceInvoicesFailed: false,
  /** The container's audit trail, which is what the Timeline's History card lists. */
  history: undefined as unknown,
  /** The consolidated `build()` JSON (S7) - brand-per-line for the Lines grid's Logo
   *  column, and the whole payload for the Split card beneath it. */
  consolidated: undefined as unknown,
};

const updatePackingList = vi.fn();

// The pager has its own tests (hooks/useListPager.test.ts); here it is noise.
vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

vi.mock('../hooks/usePackingLists', () => ({
  usePackingList: () => ({ data: state.packingList, isLoading: false }),
  packingListsPagerQuery: {
    listQueryKey: () => ['packing-lists'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  useDeletePackingList: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdatePackingList: () => ({ mutateAsync: updatePackingList, isPending: false }),
  usePackingListSourceInvoices: (...args: unknown[]) => {
    calls.sourceInvoices.push(args);
    return {
      data: state.sourceInvoices,
      isLoading: false,
      isError: state.sourceInvoicesFailed,
    };
  },
  // The Timeline's History card reads the container's audit trail (R17).
  usePackingListHistory: () => ({ data: state.history, isLoading: false, isError: false }),
  // Checkpoint labels and order come from config, and the edit-in-place clearance fields
  // read the same list the timeline does.
  useClearanceCheckpoints: () => ({
    data: [
      { field: 'etd_date', label: 'ETD', caption: null },
      { field: 'estimated_arrival_date', label: 'ETA', caption: null },
      { field: 'gatepass_date', label: 'Gatepass', caption: null },
    ],
    isLoading: false,
    isError: false,
  }),
}));

vi.mock('@/app/(protected)/master-data-management/products/services/productService', () => ({
  getProducts: vi.fn(async () => ({ data: [] })),
}));

// `vi.hoisted` because the array has to exist before `vi.mock`'s own hoisting runs it: the
// real `useSupplierSelectQuery` is a `useQuery`, so `data` is the SAME reference across
// renders once loaded - a fresh literal returned from the mock on every call would fabricate
// an instability the real hook does not have, and cascade into the Lines grid's `columns`
// memo recomputing (and remounting every row's inputs) on every unrelated render (same class
// of defect as 9118d123b's `useUOMSelectQuery` mock).
const { SUPPLIER_OPTIONS } = vi.hoisted(() => ({
  SUPPLIER_OPTIONS: [
    { id: 'sup-a', supplier_code: '400-K029', supplier_name: 'KAILU HARDWARE FACTORY' },
    { id: 'sup-b', supplier_code: '400-C011', supplier_name: 'CAIZHOU SANITARY' },
  ],
}));
vi.mock('../../suppliers/hooks/useSupplierSelectQuery', () => ({
  useSupplierSelectQuery: () => ({ data: SUPPLIER_OPTIONS }),
}));

vi.mock('@/app/(protected)/resource-management/attachments/hooks/useAttachments', () => ({
  useDownloadAttachment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/app/(protected)/resource-management/attachments/services/attachmentService', () => ({
  getAttachmentPreviewUrl: vi.fn(),
}));

const downloadWorkbook = vi.fn<(id: string, fallback?: string | null) => Promise<void>>();
// E1/E2 (PLAN-pi-header-fields-convert-fixes-24sep.md): the gear's "Download packing list"
// enqueues an async export now (`enqueuePackingListExport`), never the old synchronous
// blob download - `downloadPackingListExport` stays mocked (kept for the GET route, E1)
// but nothing in this suite calls it any more.
const enqueueExport = vi.fn<(id: string) => Promise<void>>(async () => undefined);
vi.mock('@/app/(protected)/scm/services/fulfilmentService', () => ({
  downloadPackingListExport: (id: string, fallback?: string | null) =>
    downloadWorkbook(id, fallback),
  enqueuePackingListExport: (id: string) => enqueueExport(id),
}));

vi.mock('@/app/(protected)/scm/hooks/useFulfilment', () => ({
  useConsolidatedPackingList: (id: unknown) => ({
    _: calls.consolidated.push(id),
    data: state.consolidated,
    isLoading: false,
    isError: false,
    error: null,
  }),
  // The Details tab's Container size select (S5) - not this suite's concern, so one
  // active default is enough to satisfy the render.
  useContainerSizes: (...args: unknown[]) => ({
    _: calls.containerSizes.push(args),
    data: [{ id: 'size-40hq', code: '40HQ', label: '40ft high cube', cbm: 65, is_default: true }],
    isLoading: false,
  }),
  // R25 (lane C, slice C3): the Lines tab reads every line's photos in one call, and
  // `ShipmentLinePhotosCell` uploads through its own mutation - neither is this
  // suite's concern, so both settle on an empty/no-op default.
  useShipmentLinePhotos: (id: unknown) => ({
    _: calls.linePhotos.push(id),
    data: {},
    isLoading: false,
    isError: false,
    error: null,
  }),
  useUploadShipmentLinePhotos: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}));

// The Lines grid carries a real `listingKey` (S7); a live column-preferences query has
// nothing to talk to in jsdom.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('../components/PackingListNavigation', () => ({ default: () => null }));
vi.mock('../components/packing-list-delete-dialog', () => ({ default: () => null }));
vi.mock('../components/ClearanceDeliveryCard', () => ({ default: () => null }));
vi.mock('../components/ContainerStatusImportDialog', () => ({ default: () => null }));
vi.mock('../components/SpoPlannerTable', () => ({ default: () => <div>SPO planner body</div> }));
vi.mock('@/components/common/LinkAttachmentBrowserDialog', () => ({ default: () => null }));
// Container reads the settings provider, which this test has no business standing up.
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import { getProducts } from '@/app/(protected)/master-data-management/products/services/productService';
import PackingListLayout from './layout';
import DetailsPage from './page';
import ProformaInvoicesPage from './proforma-invoices/page';
import LinesPage from './lines/page';
import DocumentsPage from './documents/page';
import SpoPage from './spo/page';
import TimelinePage from './timeline/page';

/** Two factories on the lines, nobody on the header. */
function mixedContainer(over: Record<string, unknown> = {}) {
  return {
    id: 'pl-1',
    shipment_number: 'SPO-0042',
    supplier_id: null,
    supplier: undefined,
    shipment_date: '2026-07-30',
    shipping_container_number: 'FSCU8103365',
    shipment_status: 'in_transit',
    created_at: '2026-07-30',
    updated_at: '2026-07-30',
    synced_to_excel: false,
    shipment_lines: [
      {
        id: 'l-1',
        shipment_id: 'pl-1',
        product_id: 'p-1',
        quantity_shipped: 490,
        cartons_count: 86,
        cbm: '12.5',
        supplier_id: 'sup-a',
        material: '不锈钢',
        pcs_per_carton: '10',
        carton_length_cm: '34.00',
        carton_width_cm: '24.00',
        carton_height_cm: '30.00',
        net_weight_per_carton: '7.000',
        gross_weight_per_carton: '8.300',
        currency: 'CNY',
        unit_cost: '65.50',
        // The supplier's own wording (S9) - deliberately different from the product name,
        // so a test reading it can tell the two apart.
        description: '连体马桶',
        product: { id: 'p-1', product_code: 'SRTWT7443', product_name: 'Basin Mixer Tall' },
      },
      {
        id: 'l-2',
        shipment_id: 'pl-1',
        product_id: 'p-2',
        quantity_shipped: 900,
        cartons_count: 55,
        cbm: 7.25,
        supplier_id: 'sup-b',
        product: { id: 'p-2', product_code: 'MCHWT1200', product_name: 'Shower Set' },
      },
      {
        id: 'l-3',
        shipment_id: 'pl-1',
        product_id: 'p-3',
        quantity_shipped: 120,
        cartons_count: 30,
        supplier_id: null,
        weight_per_carton: 4.5,
        product: { id: 'p-3', product_code: 'SRTBT2200', product_name: 'Bath Tub 1700' },
      },
    ],
    ...over,
  };
}

/** One PI behind this container, charging half of the KAILU line. */
function sourceInvoices() {
  return {
    invoices: [
      {
        id: 'pi-1',
        pi_number: 'PI-2026-001',
        supplier_id: 'sup-a',
        supplier_name: 'KAILU HARDWARE FACTORY',
        invoice_date: '2026-07-17',
        revision_no: 2,
        revision_count: 2,
        status: 'current' as const,
        source_ref: 'KAILU proforma.xlsx',
        currency: 'CNY',
        lines: 1,
        total_lines: 3,
        qty: 490,
        total_qty: 900,
        amount: 32095,
      },
    ],
    by_shipment_line: {
      'l-1': [{ proforma_invoice_id: 'pi-1', pi_number: 'PI-2026-001', qty: 490 }],
    },
    created_by: 'Ms Tee',
  };
}

// One stable promise: `use(params)` suspends, and a promise recreated on every render
// suspends again forever.
const PARAMS = Promise.resolve({ id: 'pl-1' });

async function renderTab(tab: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback={null}>
          <PackingListLayout params={PARAMS}>{tab}</PackingListLayout>
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

/** Radix opens on pointerdown, which jsdom does not synthesize from a click, so drive the
 *  gear by keyboard instead (ArrowDown opens it and focuses the first item). */
async function openGear() {
  const trigger = await screen.findByRole('button', { name: 'Packing list options' });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'ArrowDown', code: 'ArrowDown' });
  return screen.findByRole('menu');
}

beforeEach(async () => {
  await PARAMS;
  vi.clearAllMocks();
  routerState.pathname = '/procurement-management/packing-lists/pl-1';
  routerState.push = vi.fn();
  routerState.replace = vi.fn();
  routerState.search = '';
  state.packingList = mixedContainer();
  state.sourceInvoices = undefined;
  state.sourceInvoicesFailed = false;
  state.history = undefined;
  state.consolidated = undefined;
  updatePackingList.mockReset();
  updatePackingList.mockResolvedValue({});
  perms.set = new Set(['scm.dashboard.view']);
  perms.isLoading = false;
  calls.sourceInvoices = [];
  calls.consolidated = [];
  calls.linePhotos = [];
  calls.containerSizes = [];
});

afterEach(() => cleanup());

describe('the toolbar over the tabs', () => {
  it('names the container and its factories, and offers the six tabs in order', async () => {
    await renderTab(<DetailsPage />);

    expect(screen.getByRole('heading', { name: 'FSCU8103365' })).toBeInTheDocument();
    // The factories come off the LINES: the header of a mixed container names nobody.
    // Twice on purpose - once as the toolbar's subtitle, once as the Details tab's
    // Supplier value, which has to stay there because Edit swaps it for an input in place.
    expect(
      screen.getByText(/KAILU HARDWARE FACTORY, CAIZHOU SANITARY . Shipment date/),
    ).toBeInTheDocument();

    const tabs = screen.getAllByRole('tab').map((t) => t.textContent);
    expect(tabs.map((t) => t?.replace(/\d+$/, ''))).toEqual([
      'Details',
      'Proforma invoices',
      'Shipment lines',
      'Documents',
      'SPO planner',
      'Timeline',
    ]);
  });

  it('falls back to the shipment number when the container has no number', async () => {
    state.packingList = mixedContainer({ shipping_container_number: null });
    await renderTab(<DetailsPage />);

    expect(screen.getByRole('heading', { name: 'SPO-0042' })).toBeInTheDocument();
  });

  it('carries ONE primary action: editing the packing list', async () => {
    await renderTab(<DetailsPage />);

    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    expect(screen.getByRole('button', { name: 'Save packing list' })).toBeInTheDocument();
  });

  it('enqueues the container workbook export from the gear and toasts My Downloads', async () => {
    // E1/E2: "Download packing list" no longer downloads a blob straight from the click -
    // it enqueues an async export (same shape as a complaint's PDF export) and the file
    // shows up in My Downloads.
    await renderTab(<DetailsPage />);
    const menu = await openGear();

    fireEvent.click(within(menu).getByText('Download packing list'));

    await waitFor(() => expect(enqueueExport).toHaveBeenCalledTimes(1));
    expect(enqueueExport).toHaveBeenCalledWith('pl-1');
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Added to My Downloads'));
    expect(downloadWorkbook).not.toHaveBeenCalled();
  });

  it('offers a Download history gear item alongside Download packing list', async () => {
    await renderTab(<DetailsPage />);
    const menu = await openGear();

    expect(within(menu).getByText('Download packing list')).toBeInTheDocument();
    expect(within(menu).getByText('Download history')).toBeInTheDocument();
  });

  it('keeps the occasional actions behind the gear', async () => {
    await renderTab(<DetailsPage />);
    const menu = await openGear();

    expect(within(menu).getByText('Download packing list')).toBeInTheDocument();
    expect(within(menu).getByText('Import Container Status workbook')).toBeInTheDocument();
    expect(within(menu).getByText('Delete')).toBeInTheDocument();
  });

  it('offers a way back to the list', async () => {
    await renderTab(<DetailsPage />);

    expect(screen.getByRole('link', { name: /back to packing lists/i })).toHaveAttribute(
      'href',
      '/procurement-management/packing-lists',
    );
  });

  it('routes rather than swapping a local tab, so a tab is linkable', async () => {
    await renderTab(<DetailsPage />);

    fireEvent.click(screen.getByRole('tab', { name: /shipment lines/i }));

    expect(routerState.push).toHaveBeenCalledWith(
      '/procurement-management/packing-lists/pl-1/lines',
    );
  });

  it('keeps the list position in the URL when a tab is clicked, so the record pager does not reset', async () => {
    // The list the operator came from - page 1 (0-indexed), sorted, with a search term.
    routerState.search = 'page=1&limit=20&sort=shipment_date&dir=desc&query=FSCU';
    await renderTab(<DetailsPage />);

    fireEvent.click(screen.getByRole('tab', { name: /shipment lines/i }));

    expect(routerState.push).toHaveBeenCalledWith(
      '/procurement-management/packing-lists/pl-1/lines?page=1&limit=20&sort=shipment_date&dir=desc&query=FSCU',
    );
  });
});

describe('the Proforma invoices tab', () => {
  it('names them with what came from each, and who converted the container', async () => {
    state.sourceInvoices = sourceInvoices();
    routerState.pathname = '/procurement-management/packing-lists/pl-1/proforma-invoices';
    await renderTab(<ProformaInvoicesPage />);

    const row = screen.getByText('PI-2026-001').closest('tr') as HTMLElement;
    expect(within(row).getByText('490 of 900')).toBeInTheDocument();
    expect(within(row).getByText('1 of 3')).toBeInTheDocument();
    expect(within(row).getByText(/Revision 2 of 2/)).toBeInTheDocument();
    expect(within(row).getByRole('link', { name: 'Open' })).toHaveAttribute(
      'href',
      '/scm/proforma-invoices/pi-1',
    );
    // The name the server resolved, never the user id the container carries.
    expect(screen.getByText(/Uploaded by Ms Tee, converted on/)).toBeInTheDocument();
  });

  it('says plainly when a container came off a real packing list instead', async () => {
    state.sourceInvoices = { invoices: [], by_shipment_line: {}, created_by: 'System' };
    routerState.pathname = '/procurement-management/packing-lists/pl-1/proforma-invoices';
    await renderTab(<ProformaInvoicesPage />);

    expect(screen.getByText('Source proforma invoices')).toBeInTheDocument();
    expect(
      screen.getByText('Read from a packing list, not drafted from a proforma invoice.'),
    ).toBeInTheDocument();
  });

  it('leaves the Timeline to the checkpoints and the container history', async () => {
    state.sourceInvoices = sourceInvoices();
    routerState.pathname = '/procurement-management/packing-lists/pl-1/timeline';
    await renderTab(<TimelinePage />);

    expect(screen.getByText('Clearance & Delivery')).toBeInTheDocument();
    expect(screen.getByText('History')).toBeInTheDocument();
    // The Origin card is gone: it is a whole tab now.
    expect(screen.queryByText(/Created from PI-2026-001/)).not.toBeInTheDocument();
  });

  it('prints the over-capacity reason on the Timeline, where the conversion wrote it', async () => {
    // R17: the reason is an audit entry on the container, not a sentence appended to the
    // operator's own Notes field.
    state.history = {
      data: [
        {
          id: 'a-1',
          entity_type: 'inbound_shipments',
          entity_id: 'pl-1',
          action: 'CREATE',
          changed_at: '2026-08-28T02:15:00',
          user_display_name: 'Ms Tee',
          description:
            'Converted over capacity: PI-2026-001 places 71.2 cbm into a 65 cbm 40HQ. ' +
            'Reason: the forwarder agreed to the extra pallet.',
        },
      ],
      empty: false,
      pagination: { total: 1, page: 1 },
    };
    routerState.pathname = '/procurement-management/packing-lists/pl-1/timeline';
    await renderTab(<TimelinePage />);

    expect(screen.getByText(/Converted over capacity/)).toBeInTheDocument();
    expect(screen.getByText(/the forwarder agreed to the extra pallet/)).toBeInTheDocument();
  });
});

describe('the Shipment lines tab', () => {
  beforeEach(() => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
  });

  it('states what the workbook measures each line by, in its own column order (AC-G1)', async () => {
    await renderTab(<LinesPage />);

    for (const name of [
      'Factory',
      'No',
      'Model',
      'Description',
      'Material',
      'Qty',
      'Pcs/ctn',
      'Ctn qty',
      'L',
      'W',
      'H',
      'CBM/ctn',
      'Total CBM',
      'NW',
      'GW',
      'Total NW',
      'Total GW',
      'Logo',
      'Remarks',
      'Price',
      'Amount',
      'From PI',
      'SPO allocated',
      'Received',
      'Status',
    ]) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument();
    }
    const kailu = screen.getByText('SRTWT7443').closest('tr') as HTMLElement;
    expect(within(kailu).getByText('不锈钢')).toBeInTheDocument();
    expect(within(kailu).getByText('10')).toBeInTheDocument();
    expect(within(kailu).getByText('34')).toBeInTheDocument();
    expect(within(kailu).getByText('7')).toBeInTheDocument();
    expect(within(kailu).getByText('8.3')).toBeInTheDocument();
    // Derived off pcs/ctn and the carton size, the same rule the export's formulas use
    // (AC-G2): ctn qty = 490/10 = 49, cbm/ctn = 34*24*30/10^6, total cbm = ctn*cbm/ctn.
    expect(within(kailu).getByText('49')).toBeInTheDocument();
    expect(within(kailu).getByText('0.02448')).toBeInTheDocument();
    expect(within(kailu).getByText('1.2')).toBeInTheDocument();
  });

  it('reads the one old weight as the gross one, and "-" where nothing was measured', async () => {
    await renderTab(<LinesPage />);

    const unmeasured = screen.getByText('SRTBT2200').closest('tr') as HTMLElement;
    // `weight_per_carton` is the only weight most containers hold, and it is the gross -
    // the per-carton cell reads it, and so does the total (135 = 4.5 * the stored 30 ctn).
    expect(within(unmeasured).getByText('4.5')).toBeInTheDocument();
    expect(within(unmeasured).getByText('135')).toBeInTheDocument();
    // Material, pcs/ctn and total cbm all state nothing rather than 0 - no carton size was
    // ever given for this line, so there is no formula to derive a volume from.
    const cells = Array.from(unmeasured.querySelectorAll('td')).map((c) => c.textContent);
    expect(cells[4]).toBe('-'); // material
    expect(cells[6]).toBe('-'); // pcs per carton
    expect(cells[12]).toBe('-'); // total cbm
  });

  it('names the factory that loaded each line, and guesses no owner for the rest', async () => {
    await renderTab(<LinesPage />);

    const kailu = screen.getByText('SRTWT7443').closest('tr') as HTMLElement;
    expect(within(kailu).getByText('KAILU HARDWARE FACTORY')).toBeInTheDocument();
    const unclaimed = screen.getByText('SRTBT2200').closest('tr') as HTMLElement;
    expect(unclaimed.querySelectorAll('td')[0]).toHaveTextContent('-');
  });

  it('sorts rows factory then no, with the unclaimed line last (AC-G3)', async () => {
    await renderTab(<LinesPage />);

    // CAIZHOU (l-2) sorts ahead of KAILU (l-1) alphabetically; the unclaimed line (l-3,
    // no factory) sorts after both regardless of its product code.
    const rows = screen.getAllByRole('row').slice(1); // drop the header row
    const codes = rows.map((r) => within(r).queryByText(/^(MCHWT1200|SRTWT7443|SRTBT2200)$/)?.textContent);
    expect(codes.filter(Boolean)).toEqual(['MCHWT1200', 'SRTWT7443', 'SRTBT2200']);
  });

  it('totals qty, ctn qty, total cbm (with its unmeasured caveat), nw, gw and amount (AC-G3)', async () => {
    await renderTab(<LinesPage />);

    const total = screen.getByText('Total').closest('tr') as HTMLElement;
    expect(within(total).getByText('1510')).toBeInTheDocument(); // qty
    expect(within(total).getByText('134')).toBeInTheDocument(); // ctn qty
    expect(within(total).getByText('8.45')).toBeInTheDocument(); // total cbm
    expect(within(total).getByText('(1 unmeasured)')).toBeInTheDocument();
    expect(within(total).getByText('343')).toBeInTheDocument(); // total nw
    expect(within(total).getByText('541.7')).toBeInTheDocument(); // total gw
    expect(within(total).getByText('32095')).toBeInTheDocument(); // amount
  });

  it('reads the brand off the consolidated packing list as the Logo column (AC-G1)', async () => {
    state.consolidated = {
      factories: [
        {
          supplier_id: 'sup-a',
          lines: [{ line_id: 'l-1', brand: 'SORENTO' }],
        },
      ],
      total: { lines: 1, qty: 490, cartons: 49, cbm: 1.2 },
      split: [],
      costs: { clearance_cost: null, china_freight_cost: null, insurance_rate: null },
    };
    await renderTab(<LinesPage />);

    const kailu = screen.getByText('SRTWT7443').closest('tr') as HTMLElement;
    expect(within(kailu).getByText('SORENTO')).toBeInTheDocument();
  });

  it("shows the supplier's own description, the product name when the line has none (S9)", async () => {
    await renderTab(<LinesPage />);

    // l-1 carries its own description, and it prints INSTEAD of the product name.
    const kailu = screen.getByText('SRTWT7443').closest('tr') as HTMLElement;
    expect(within(kailu).getByText('连体马桶')).toBeInTheDocument();
    expect(within(kailu).queryByText('Basin Mixer Tall')).not.toBeInTheDocument();

    // l-2 has no description of its own, so the column falls back to the product's name.
    const caizhou = screen.getByText('MCHWT1200').closest('tr') as HTMLElement;
    expect(within(caizhou).getByText('Shower Set')).toBeInTheDocument();
  });

  it('names the invoice per line', async () => {
    state.sourceInvoices = sourceInvoices();
    await renderTab(<LinesPage />);

    const kailu = screen.getByText('SRTWT7443').closest('tr') as HTMLElement;
    expect(within(kailu).getByRole('link', { name: /PI-2026-001/ })).toHaveAttribute(
      'href',
      '/scm/proforma-invoices/pi-1',
    );
  });

  /**
   * ONE invoice can charge the same container line twice - two of its own lines for the
   * same item, consolidated into one shipment line. The key was (invoice, line), so the
   * pair repeated, React kept one child and the second charge vanished from the cell.
   */
  it('lists both charges when one invoice bills the same line twice', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    state.sourceInvoices = {
      ...sourceInvoices(),
      by_shipment_line: {
        'l-1': [
          { proforma_invoice_id: 'pi-1', pi_number: 'PI-2026-001', qty: 300 },
          { proforma_invoice_id: 'pi-1', pi_number: 'PI-2026-001', qty: 190 },
        ],
      },
    };
    await renderTab(<LinesPage />);

    const kailu = screen.getByText('SRTWT7443').closest('tr') as HTMLElement;
    expect(within(kailu).getAllByRole('link', { name: /PI-2026-001/ })).toHaveLength(2);
    expect(within(kailu).getByText('300')).toBeInTheDocument();
    expect(within(kailu).getByText('190')).toBeInTheDocument();
    expect(errors.mock.calls.map(String).join(' ')).not.toMatch(/same key/);
    errors.mockRestore();
  });

  /**
   * R19 / AC-F5 - Edit is pressed on the tab the operator is reading, and it has to leave
   * them there. Editing that navigated back to Details would lose the place in a long line
   * list, and reads as the page throwing the edit away.
   */
  it('edits in place: Edit, Cancel and Save never leave /lines', async () => {
    await renderTab(<LinesPage />);

    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect(screen.getByRole('button', { name: 'Save packing list' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Shipment lines/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(routerState.push).not.toHaveBeenCalled();
    expect(routerState.replace).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(routerState.push).not.toHaveBeenCalled();
    expect(routerState.replace).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Save packing list' }));
    await waitFor(() => expect(updatePackingList).toHaveBeenCalled());
    expect(routerState.push).not.toHaveBeenCalled();
    expect(routerState.replace).not.toHaveBeenCalled();
  });
});

describe('the edit draft, which now lives above every tab', () => {
  it('swaps values for inputs where the values were, on the Details tab', async () => {
    await renderTab(<DetailsPage />);

    expect(screen.queryByLabelText('Bill of lading')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    expect(screen.getByLabelText('Container no')).toHaveValue('FSCU8103365');
    // The three the container workbook prints.
    expect(screen.getByLabelText('Seal no')).toBeInTheDocument();
    expect(screen.getByLabelText('Shipper')).toBeInTheDocument();
    expect(screen.getByLabelText('SO')).toBeInTheDocument();
    // The fill gauge moved here from the proforma invoice (S5, ruling 1) - a select in
    // edit mode, the size code in view. Not `getByLabelText`: like `Supplier` above it, the
    // field is a bespoke block with the label as plain text, not a `<label htmlFor>`.
    expect(screen.getByText('Container size')).toBeInTheDocument();
    // The ones with no input counterpart stay as values - nothing moves between the two.
    expect(screen.getByText('Total items')).toBeInTheDocument();
    expect(screen.getByText('Source sheet')).toBeInTheDocument();
  });

  // R17 / AC-H1 (purchasing consolidation batch, 6 Sep 2026): the Costs card is gone in
  // both view and edit mode. The three columns stay in the DB untouched - only the UI (and
  // the export, covered on the backend) drops them.
  it('has no Costs card, in view or edit mode', async () => {
    await renderTab(<DetailsPage />);

    expect(screen.queryByText('Costs')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Clearance cost')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('China freight cost')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Insurance rate')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    expect(screen.queryByText('Costs')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Clearance cost')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('China freight cost')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Insurance rate')).not.toBeInTheDocument();
  });

  it('saves the header and the lines in one PUT, with no cost field on the payload', async () => {
    await renderTab(<DetailsPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    fireEvent.change(screen.getByLabelText('Seal no'), { target: { value: 'J0713349' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save packing list$/i }));

    await waitFor(() => expect(updatePackingList).toHaveBeenCalledTimes(1));
    const { id, data } = updatePackingList.mock.calls[0][0];
    expect(id).toBe('pl-1');
    expect(data.seal_number).toBe('J0713349');
    expect(data.shipment_lines).toHaveLength(3);
    // Not shown, not sent - whatever the DB already holds for these three is left alone.
    expect(Object.keys(data)).not.toContain('clearance_cost');
    expect(Object.keys(data)).not.toContain('china_freight_cost');
    expect(Object.keys(data)).not.toContain('insurance_rate');
  });

  it('sends the packing list regions on save (review round 1)', async () => {
    state.packingList = mixedContainer({ regions: ['east', 'west'] });
    await renderTab(<DetailsPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    fireEvent.click(screen.getByRole('button', { name: /^Save packing list$/i }));

    await waitFor(() => expect(updatePackingList).toHaveBeenCalledTimes(1));
    const { data } = updatePackingList.mock.calls[0][0];
    expect([...data.regions].sort()).toEqual(['east', 'west']);
  });

  it('edits the measurements on the lines tab and sends them', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    expect(screen.getByLabelText('Pcs per carton for SRTWT7443')).toHaveValue(10);
    fireEvent.change(screen.getByLabelText('Carton length for SRTWT7443'), {
      target: { value: '36' },
    });
    fireEvent.change(screen.getByLabelText('Gross weight for SRTWT7443'), {
      target: { value: '9.1' },
    });
    fireEvent.change(screen.getByLabelText('Material for SRTWT7443'), {
      target: { value: '铜' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Save packing list$/i }));

    await waitFor(() => expect(updatePackingList).toHaveBeenCalledTimes(1));
    const line = updatePackingList.mock.calls[0][0].data.shipment_lines[0];
    expect(line.carton_length_cm).toBe(36);
    expect(line.gross_weight_per_carton).toBe(9.1);
    expect(line.material).toBe('铜');
    // The unit the price is in, round-tripped: a payload carrying the cost and dropping
    // this hands the backend a number with no meaning.
    expect(line.currency).toBe('CNY');
  });

  it('edits the description and sends it (S9, AC-I3)', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    // l-1 already carries its own description - the input opens on it, not on the
    // product's name.
    expect(screen.getByLabelText('Description for SRTWT7443')).toHaveValue('连体马桶');
    fireEvent.change(screen.getByLabelText('Description for SRTWT7443'), {
      target: { value: '304 STAINLESS STEEL BASIN TAP' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Save packing list$/i }));

    await waitFor(() => expect(updatePackingList).toHaveBeenCalledTimes(1));
    const line = updatePackingList.mock.calls[0][0].data.shipment_lines[0];
    expect(line.description).toBe('304 STAINLESS STEEL BASIN TAP');
  });

  it('sends null for a cleared description, never dropping the key', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    // l-1 already carries its own description; clearing it back to blank must be a Save
    // that says "null", not one that says nothing at all - an omitted key on this PUT means
    // "unchanged", so the old wording would otherwise survive the save.
    fireEvent.change(screen.getByLabelText('Description for SRTWT7443'), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Save packing list$/i }));

    await waitFor(() => expect(updatePackingList).toHaveBeenCalledTimes(1));
    const line = updatePackingList.mock.calls[0][0].data.shipment_lines[0];
    expect(line.description).toBeNull();
    expect(Object.keys(line)).toContain('description');
  });

  it('totals follow the draft while somebody is typing', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    // Ctn qty and total cbm are DERIVED (AC-G2), so typing into the inputs that feed the
    // formula - quantity and pack size - is what the footer has to follow, not a raw total
    // input (there is none any more; the export never had one either).
    fireEvent.change(screen.getByLabelText('Quantity for SRTWT7443'), {
      target: { value: '400' },
    });
    fireEvent.change(screen.getByLabelText('Pcs per carton for SRTWT7443'), {
      target: { value: '20' },
    });

    const total = screen.getByText('Total').closest('tr') as HTMLElement;
    expect(within(total).getByText('1420')).toBeInTheDocument(); // qty
    expect(within(total).getByText('105')).toBeInTheDocument(); // ctn qty
    expect(within(total).getByText('7.74')).toBeInTheDocument(); // total cbm
  });

  it('drops a line from the draft on the first press, asking nothing (S6-10)', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    const row = screen.getByLabelText('Quantity for SRTWT7443').closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: /remove/i }));

    // D7: neither confirmed nor deferred, because the line leaves the DRAFT and
    // Save is what sends it. The last assertion is the whole reason: nothing has
    // reached the server, so there is nothing there to take back.
    await waitFor(() =>
      expect(screen.queryByLabelText('Quantity for SRTWT7443')).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByText(/This removes SRTWT7443 from this packing list/),
    ).not.toBeInTheDocument();
    expect(updatePackingList).not.toHaveBeenCalled();
  });

  it('restores the saved values on Cancel, writing nothing', async () => {
    await renderTab(<DetailsPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    fireEvent.change(screen.getByLabelText('Invoice number'), { target: { value: 'INV-77' } });
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

    expect(updatePackingList).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Invoice number')).not.toBeInTheDocument();
  });
});

describe('the lines grid stays usable while editing (Round 2, S10)', () => {
  it('keeps the SAME input, still focused, after three keystrokes into Qty (AC-J1)', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    const qty = screen.getByLabelText('Quantity for SRTWT7443');
    await userEvent.clear(qty);
    await userEvent.type(qty, '123');

    // The SAME DOM node, still focused: a remount partway through typing (the columns
    // memo rebuilding on every keystroke, S7's own S3-class defect) would have swapped
    // this element out from under the keystrokes and dropped the rest of them.
    expect(screen.getByLabelText('Quantity for SRTWT7443')).toBe(qty);
    expect(document.activeElement).toBe(qty);
    expect(qty).toHaveValue(123);
  });

  it('keeps focus while typing a measurement cell too (AC-J1)', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    const length = screen.getByLabelText('Carton length for SRTWT7443');
    await userEvent.clear(length);
    await userEvent.type(length, '99');

    expect(screen.getByLabelText('Carton length for SRTWT7443')).toBe(length);
    expect(document.activeElement).toBe(length);
    expect(length).toHaveValue(99);
  });

  it('shows a freshly added lines product as soon as it is picked (AC-J2)', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    vi.mocked(getProducts).mockResolvedValue({
      data: [{ id: 'p-9', product_code: 'SRTNEW099', product_name: 'New Basin' }],
      pagination: { total: 1, page: 1, limit: 50 },
    } as never);
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    fireEvent.click(screen.getByRole('button', { name: 'Add line' }));
    const row = screen.getByLabelText('Quantity for the new line').closest('tr') as HTMLElement;
    // Neither combobox in this row carries an aria-label of its own (no `<Label htmlFor>`
    // pairing, same as the old `PackingListForm`'s own line pickers) - found by POSITION,
    // Factory then Model, the column order.
    const productCombo = within(row).getAllByRole('combobox')[1];
    fireEvent.click(productCombo);
    fireEvent.click(await screen.findByRole('option', { name: /SRTNEW099/ }));

    await waitFor(() => expect(productCombo).toHaveTextContent('SRTNEW099'));
  });

  it("lets an existing line's product be changed (AC-J3, FE)", async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    vi.mocked(getProducts).mockResolvedValue({
      data: [{ id: 'p-9', product_code: 'SRTNEW099', product_name: 'New Basin' }],
      pagination: { total: 1, page: 1, limit: 50 },
    } as never);
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    const row = screen.getByLabelText('Quantity for SRTWT7443').closest('tr') as HTMLElement;
    const productCombo = within(row).getAllByRole('combobox')[1];
    expect(productCombo).toHaveTextContent('SRTWT7443');
    fireEvent.click(productCombo);
    fireEvent.click(await screen.findByRole('option', { name: /SRTNEW099/ }));

    await waitFor(() => expect(within(row).getAllByRole('combobox')[1]).toHaveTextContent('SRTNEW099'));
    expect(within(row).getAllByRole('combobox')[1]).not.toHaveTextContent('SRTWT7443');
  });

  it('Ctn qty is an input when Pcs/ctn is blank, derived and read-only when it is stated (AC-J4)', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    // l-2 (MCHWT1200) states no Pcs/ctn - the workbook's own `H` formula has nothing to
    // derive Ctn qty FROM, so the stated carton count (55) is editable directly.
    const ctnQty = screen.getByLabelText('Ctn qty for MCHWT1200');
    await userEvent.clear(ctnQty);
    await userEvent.type(ctnQty, '77');
    expect(ctnQty).toHaveValue(77);

    // l-1 (SRTWT7443) states Pcs/ctn = 10, so Ctn qty is DERIVED (490 / 10 = 49) - typing
    // into it would disagree with the number it derives, so there is no input at all.
    expect(screen.queryByLabelText('Ctn qty for SRTWT7443')).not.toBeInTheDocument();
    const row = screen.getByLabelText('Quantity for SRTWT7443').closest('tr') as HTMLElement;
    expect(within(row).getByText('49')).toBeInTheDocument();
  });

  it('sends the stated Ctn qty on save when Pcs/ctn is blank (AC-J4)', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    const ctnQty = screen.getByLabelText('Ctn qty for MCHWT1200');
    await userEvent.clear(ctnQty);
    await userEvent.type(ctnQty, '77');
    fireEvent.click(screen.getByRole('button', { name: /^Save packing list$/i }));

    await waitFor(() => expect(updatePackingList).toHaveBeenCalledTimes(1));
    const lines = updatePackingList.mock.calls[0][0].data.shipment_lines;
    const mocha = lines.find((l: { product_id: string }) => l.product_id === 'p-2');
    expect(mocha.cartons_count).toBe(77);
  });

  it('every measurement is an input on every row, not only the first (AC-J5)', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    for (const code of ['MCHWT1200', 'SRTBT2200']) {
      for (const field of [
        'Material',
        'Pcs per carton',
        'Carton length',
        'Carton width',
        'Carton height',
        'Net weight',
        'Gross weight',
        'Remarks',
        'Price',
        'Description',
      ]) {
        expect(screen.getByLabelText(`${field} for ${code}`)).toBeInTheDocument();
      }
    }
  });
});

describe('the remaining tabs still render their own bodies', () => {
  it('lists the proforma files on Documents', async () => {
    state.sourceInvoices = sourceInvoices();
    routerState.pathname = '/procurement-management/packing-lists/pl-1/documents';
    await renderTab(<DocumentsPage />);

    expect(screen.getByText('Related Documents')).toBeInTheDocument();
    expect(screen.getByText(/KAILU proforma\.xlsx/)).toBeInTheDocument();
  });

  it('asks before unlinking the attachment, never on one click', async () => {
    state.packingList = mixedContainer({
      attachment_id: 'att-1',
      attachment: {
        id: 'att-1',
        original_filename: 'FSCU8103365.pdf',
        stored_filename: 'x.pdf',
        file_path: '/x.pdf',
        file_size_bytes: 2048,
        mime_type: 'application/pdf',
        attachment_type: null,
      },
    });
    routerState.pathname = '/procurement-management/packing-lists/pl-1/documents';
    await renderTab(<DocumentsPage />);

    fireEvent.click(screen.getByTitle('Unlink attachment'));

    expect(screen.getByText('Unlink this attachment?')).toBeInTheDocument();
    expect(updatePackingList).not.toHaveBeenCalled();
  });

  it('renders the SPO planner', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/spo';
    await renderTab(<SpoPage />);

    expect(screen.getByText('SPO planner body')).toBeInTheDocument();
  });
});

/**
 * PL-TABS-ACCESS: a procurement user without SCM read (Kah Xin, 1 Oct). Every SCM read on
 * this page 403s for her, so the tabs that are nothing but an SCM read are not offered, a
 * deep link to one says so plainly, and the tabs she CAN use never fire the reads at all -
 * a 403 there used to surface as a permission toast on a tab that works, and as "none"
 * where the honest answer is "you cannot see this".
 */
describe('a user without SCM read (PL-TABS-ACCESS)', () => {
  beforeEach(() => {
    perms.set = new Set(['procurement.packing_lists.view', 'procurement.packing_lists.edit']);
  });

  it('is offered only the tabs she can open', async () => {
    await renderTab(<DetailsPage />);

    const tabs = screen.getAllByRole('tab').map((t) => t.textContent?.replace(/\d+$/, ''));
    expect(tabs).toEqual(['Details', 'Shipment lines', 'Documents', 'Timeline']);
  });

  it('offers no SCM tab while her permissions are still loading', async () => {
    perms.set = new Set(['scm.dashboard.view']);
    perms.isLoading = true;
    await renderTab(<DetailsPage />);

    const tabs = screen.getAllByRole('tab').map((t) => t.textContent?.replace(/\d+$/, ''));
    expect(tabs).not.toContain('Proforma invoices');
    expect(tabs).not.toContain('SPO planner');
  });

  it('never asks for the proforma invoices behind the container', async () => {
    await renderTab(<DetailsPage />);

    // The hook is still called (rules of hooks) but told not to fetch.
    expect(calls.sourceInvoices.length).toBeGreaterThan(0);
    for (const [, opts] of calls.sourceInvoices) {
      expect(opts).toEqual(expect.objectContaining({ enabled: false }));
    }
  });

  it('says plainly she has no access on a deep link to Proforma invoices', async () => {
    state.sourceInvoices = sourceInvoices();
    routerState.pathname = '/procurement-management/packing-lists/pl-1/proforma-invoices';
    await renderTab(<ProformaInvoicesPage />);

    expect(screen.getByText("You don't have access to this page")).toBeInTheDocument();
    expect(screen.queryByText('PI-2026-001')).not.toBeInTheDocument();
    expect(screen.queryByText(/Read from a packing list/)).not.toBeInTheDocument();
  });

  it('says plainly she has no access on a deep link to the SPO planner', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/spo';
    await renderTab(<SpoPage />);

    expect(screen.getByText("You don't have access to this page")).toBeInTheDocument();
    expect(screen.queryByText('SPO planner body')).not.toBeInTheDocument();
  });

  it('keeps Documents usable, without claiming there is no proforma invoice', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/documents';
    await renderTab(<DocumentsPage />);

    expect(screen.getByText('Related Documents')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Link attachment/ })).toBeInTheDocument();
    expect(screen.queryByText('No proforma invoice behind this container.')).not.toBeInTheDocument();
  });

  it('keeps Shipment lines working without the SCM reads behind it', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);

    expect(screen.getByText('SRTWT7443')).toBeInTheDocument();
    expect(calls.consolidated.length).toBeGreaterThan(0);
    expect(calls.consolidated.every((id) => id === null)).toBe(true);
    expect(calls.linePhotos.every((id) => id === null)).toBe(true);
  });

  it('drops the Lines columns that are SCM reads or SCM actions (From PI, Photos)', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);

    expect(screen.getByRole('columnheader', { name: 'Qty' })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'From PI' })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Photos' })).not.toBeInTheDocument();
  });

  it('offers no container-workbook download, which is an SCM export', async () => {
    await renderTab(<DetailsPage />);
    const menu = await openGear();

    expect(within(menu).queryByText('Download packing list')).not.toBeInTheDocument();
    expect(within(menu).getByText('Download history')).toBeInTheDocument();
  });

  it('keeps the container size as a value in edit mode, where its options cannot be read', async () => {
    state.packingList = mixedContainer({
      container_size_id: 'size-40hq',
      container_size_code: '40HQ',
    });
    await renderTab(<DetailsPage />);
    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));

    const field = screen.getByText('Container size').parentElement as HTMLElement;
    expect(within(field).getByText('40HQ')).toBeInTheDocument();
    expect(within(field).queryByRole('combobox')).not.toBeInTheDocument();
  });

  // The packing-list "hang" (owner, 1 Oct): without SCM read the proforma invoices are
  // undefined, and the Lines tab rebuilt its grid rows from a fresh `{}` on every render.
  // TanStack resets the page index on every new row model, that reset is a state update,
  // and the update re-rendered with new rows again: an endless loop that pegged the main
  // thread, so no tab click out of Shipment lines could ever commit. It takes one render
  // after mount to start (a query settling does it in the browser); here, a rerender.
  it(
    'settles on Shipment lines instead of re-rendering forever',
    async () => {
      routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const tree = () => (
        <QueryClientProvider client={client}>
          <Suspense fallback={null}>
            <PackingListLayout params={PARAMS}>
              <LinesPage />
            </PackingListLayout>
          </Suspense>
        </QueryClientProvider>
      );
      let view!: ReturnType<typeof render>;
      await act(async () => {
        view = render(tree());
      });
      await act(async () => {
        await new Promise((r) => setTimeout(r, 50));
      });
      await act(async () => {
        view.rerender(tree());
      });
      const before = calls.consolidated.length;
      await act(async () => {
        await new Promise((r) => setTimeout(r, 300));
      });

      // A settled page renders a handful of times at most; the loop never stops (and on
      // the old code this test does not reach this line at all: it times out).
      expect(calls.consolidated.length - before).toBeLessThan(5);
      expect(screen.getByText('SRTWT7443')).toBeInTheDocument();
    },
    5000,
  );

  it('keeps Details working without reading the SCM container sizes', async () => {
    await renderTab(<DetailsPage />);

    expect(screen.getByRole('heading', { name: 'FSCU8103365' })).toBeInTheDocument();
    expect(calls.containerSizes.length).toBeGreaterThan(0);
    for (const [opts] of calls.containerSizes) {
      expect(opts).toEqual(expect.objectContaining({ enabled: false }));
    }
  });
});

describe('a user with SCM read', () => {
  it('says the proforma invoices could not be read on Documents, never "none"', async () => {
    state.sourceInvoicesFailed = true;
    routerState.pathname = '/procurement-management/packing-lists/pl-1/documents';
    await renderTab(<DocumentsPage />);

    expect(screen.getByText('Could not load the proforma invoices.')).toBeInTheDocument();
    expect(screen.queryByText('No proforma invoice behind this container.')).not.toBeInTheDocument();
  });

  it('keeps the From PI and Photos columns and the workbook download', async () => {
    routerState.pathname = '/procurement-management/packing-lists/pl-1/lines';
    await renderTab(<LinesPage />);

    expect(screen.getByRole('columnheader', { name: 'From PI' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Photos' })).toBeInTheDocument();
    const menu = await openGear();
    expect(within(menu).getByText('Download packing list')).toBeInTheDocument();
  });

  it('reads the proforma invoices and the planner as before', async () => {
    await renderTab(<DetailsPage />);

    for (const [, opts] of calls.sourceInvoices) {
      expect(opts).toEqual(expect.objectContaining({ enabled: true }));
    }
    const tabs = screen.getAllByRole('tab').map((t) => t.textContent?.replace(/\d+$/, ''));
    expect(tabs).toContain('Proforma invoices');
    expect(tabs).toContain('SPO planner');
  });
});
