/**
 * Stage 1B - the fulfilment planning worklist (journey step 1, AC-A03, AC-G02).
 *
 * The screen is a worklist, not a report: every row carries exactly one review state pill
 * for the whole sales order, never a per-line "3 of 4 confirmed" reading. What is worth
 * pinning here is that the pill text matches what the row is, the review-state filter and
 * the search box both reach the service as query params, every response shape (loading,
 * error, and each empty-state copy) renders explicitly, and a row is the way into its sheet.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FulfilmentPlanningRow } from '../../_shared/types/fulfilmentPlanning.types';

const routerReplace = vi.fn();
/** Back out of a board is a NAVIGATION to another route now, not a rewrite of this one. */
const routerPush = vi.fn();
let currentSearchParams = new URLSearchParams('');

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: (...args: unknown[]) => routerPush(...args),
    replace: (...args: unknown[]) => routerReplace(...args),
  }),
  usePathname: () => '/project-sales/fulfilment-planning',
  useSearchParams: () => currentSearchParams,
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const listFulfilmentPlanning = vi.fn();
const getReconciliation = vi.fn();
const rerunReconciliation = vi.fn();
const adoptSalesOrder = vi.fn();
const getSupply = vi.fn();
const getPlanningBoard = vi.fn();

vi.mock('../../_shared/services/fulfilmentPlanningService', () => ({
  listFulfilmentPlanning: (...args: unknown[]) => listFulfilmentPlanning(...args),
  getReconciliation: (...args: unknown[]) => getReconciliation(...args),
  rerunReconciliation: (...args: unknown[]) => rerunReconciliation(...args),
  adoptSalesOrder: (...args: unknown[]) => adoptSalesOrder(...args),
  getSupply: (...args: unknown[]) => getSupply(...args),
  getPlanningBoard: (...args: unknown[]) => getPlanningBoard(...args),
  confirmSupply: vi.fn(),
  confirmMany: vi.fn(),
  // S4 (`useLineDraftMutation`): `FulfilmentBoardPanel`'s own `decide()` closes over these
  // the moment the board opens, regardless of whether a test presses Save.
  putLineDraft: vi.fn().mockResolvedValue({
    decision: { verdict: 'approved' },
    saved_by: 'Test Planner',
    saved_at: '2026-09-03T00:00:00Z',
  }),
  deleteLineDraft: vi.fn().mockResolvedValue(undefined),
  ConfirmSupplyError: class extends Error {},
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

// S4: `decide()` names the saver off the session (R-F).
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { id: 'user-1', name: 'Test Planner' } },
    status: 'authenticated',
  }),
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
  }: {
    value: string;
    onChange: (next: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select
      aria-label={placeholder ?? 'select'}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">{placeholder ?? ''}</option>
      {(options ?? []).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

import { FulfilmentPlanningClient } from './FulfilmentPlanningClient';

function row(overrides: Partial<FulfilmentPlanningRow> = {}): FulfilmentPlanningRow {
  return {
    id: 'pso-1',
    provisional_ref: 'PSO-000123',
    autocount_doc_no: 'SO376201',
    project_id: 'proj-1',
    project_code: 'PRJ-0041',
    project_name: 'Tuju Residences',
    customer_name: 'Buimaco Sdn Bhd (Project)',
    po_number: 'HQ/26/01/121',
    area_group: 'TOWER',
    status: 'published',
    line_count: 4,
    lines_linked: 4,
    exception_count: 0,
    review_state: 'needs_cs_review',
    updated_at: '2026-08-14T02:41:00',
    ...overrides,
  };
}

function envelope(rows: FulfilmentPlanningRow[]) {
  return { data: rows, total: rows.length, page: 1, limit: 25 };
}

function renderClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FulfilmentPlanningClient />
    </QueryClientProvider>,
  );
}

/** Radix opens its dropdown menus on pointerdown, which fireEvent.click does not send. */
function openFilters() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /filters/i }), {
    button: 0,
    ctrlKey: false,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  currentSearchParams = new URLSearchParams('');
});

describe('FulfilmentPlanningClient', () => {
  it('shows the loading state without flashing an empty or error copy', () => {
    listFulfilmentPlanning.mockReturnValue(new Promise(() => {}));

    renderClient();

    expect(screen.queryByText('Nothing is outstanding')).not.toBeInTheDocument();
    expect(screen.queryByText('The planning list could not be loaded')).not.toBeInTheDocument();
  });

  it('renders one pill per row, carrying the exception count where there is one', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([
        row({ id: 'pso-clean', review_state: 'needs_cs_review', exception_count: 0 }),
        row({
          id: 'pso-open',
          provisional_ref: 'PSO-000124',
          review_state: 'awaiting_reconciliation',
          exception_count: 3,
        }),
      ]),
    );

    renderClient();

    expect(await screen.findByText('Needs CS review')).toBeInTheDocument();
    expect(screen.getByText('Awaiting reconciliation · 3 exceptions')).toBeInTheDocument();
  });

  it('reads the review-state filter into the query params it sends', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([row()]));
    renderClient();

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ review_state: undefined }),
      ),
    );

    openFilters();
    const picker = within(screen.getByRole('menu')).getByRole('combobox');
    fireEvent.change(picker, { target: { value: 'needs_cs_review' } });

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ review_state: 'needs_cs_review' }),
      ),
    );
  });

  it('sends the typed search as the query param', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([row()]));
    renderClient();

    await screen.findByText('PSO-000123');

    fireEvent.change(
      screen.getByPlaceholderText('Search sales order, customer, project or product'),
      { target: { value: 'buimaco' } },
    );

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ query: 'buimaco' }),
      ),
    );
  });

  it('offers the sales order book as the next step when nothing is outstanding', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([]));

    renderClient();

    expect(await screen.findByText('Nothing is outstanding')).toBeInTheDocument();
    expect(
      screen.getByText(
        'An outstanding project sales order appears here on its own, with no upload and nothing to publish.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the sales order book' })).toHaveAttribute(
      'href',
      '/scm/sales-orders',
    );
  });

  it('says so when the review-state filter itself has nothing left to show (needs review)', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([]));
    renderClient();

    openFilters();
    fireEvent.change(within(screen.getByRole('menu')).getByRole('combobox'), {
      target: { value: 'needs_cs_review' },
    });

    expect(
      await screen.findByText('No sales order is waiting on a decision'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Clear the review state filter to see the rest of the sales orders.'),
    ).toBeInTheDocument();
  });

  it('says so when the review-state filter itself has nothing left to show (awaiting reconciliation)', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([]));
    renderClient();

    openFilters();
    fireEvent.change(within(screen.getByRole('menu')).getByRole('combobox'), {
      target: { value: 'awaiting_reconciliation' },
    });

    expect(await screen.findByText('Every sales order here is reconciled')).toBeInTheDocument();
  });

  it('reports a load failure instead of an empty table', async () => {
    listFulfilmentPlanning.mockRejectedValue(new Error('Backend is down'));

    renderClient();

    expect(
      await screen.findByText('The planning list could not be loaded'),
    ).toBeInTheDocument();
    expect(screen.getByText('Backend is down')).toBeInTheDocument();

    listFulfilmentPlanning.mockResolvedValue(envelope([row()]));
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    await screen.findByText('PSO-000123');
    expect(listFulfilmentPlanning).toHaveBeenCalledTimes(2);
  });

  it('opens the sheet for the row that was clicked', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([row()]));
    getReconciliation.mockReturnValue(new Promise(() => {}));

    renderClient();

    fireEvent.click(await screen.findByText('PSO-000123'));

    const dialog = await screen.findByRole('dialog');
    // Shown twice inside the sheet: the title, and the AutoCount doc field in the header strip.
    expect(within(dialog).getAllByText('SO376201').length).toBeGreaterThan(0);
    await waitFor(() => expect(getReconciliation).toHaveBeenCalledWith('pso-1'));
  });
});

/**
 * The AutoCount-driven arm (PLAN-fulfilment-planning-from-autocount-so, journey steps 1
 * and 2). What is worth pinning is that an order nobody planned appears at all, reads Not
 * started, states the facts CS judges it on, and offers exactly one decision.
 */
describe('FulfilmentPlanningClient, the AutoCount sales-order arm', () => {
  /** An outstanding core sales order with no planning record: no id, no Project SO. */
  function notStarted(overrides: Partial<FulfilmentPlanningRow> = {}): FulfilmentPlanningRow {
    return {
      row_kind: 'sales_order',
      id: null,
      sales_order_id: 'so-345418',
      so_number: 'SO345418',
      origin: null,
      provisional_ref: null,
      autocount_doc_no: null,
      project_id: null,
      project_label: 'PEMBINAAN YUEN SENG / TAMAN IMPIAN',
      customer_name: 'PEMBINAAN YUEN SENG SDN BHD (PROJECT)',
      status: null,
      line_count: 1,
      lines_linked: 0,
      exception_count: 0,
      outstanding_qty: '202.0000',
      earliest_required_date: '2025-06-15',
      review_state: 'not_started',
      updated_at: null,
      ...overrides,
    };
  }

  it('shows an unplanned core sales order as Not started, with no Project SO invented for it', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([notStarted()]));

    renderClient();

    expect(await screen.findByText('SO345418')).toBeInTheDocument();
    expect(screen.getByText('Not started')).toBeInTheDocument();
    // AC-FP01: no reference and no document number claiming an upload that never happened.
    expect(screen.getByText('Not authored here')).toBeInTheDocument();
    expect(screen.queryByText('PSO-')).not.toBeInTheDocument();
  });

  it('states the facts the row is judged on: customer, project, earliest required date and outstanding quantity', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([notStarted()]));

    renderClient();

    expect(
      await screen.findByText('PEMBINAAN YUEN SENG SDN BHD (PROJECT)'),
    ).toBeInTheDocument();
    expect(screen.getByText('PEMBINAAN YUEN SENG / TAMAN IMPIAN')).toBeInTheDocument();
    expect(screen.getByText('15/06/2025')).toBeInTheDocument();
    // The decimal string off the wire is read as a quantity, not as a machine's "202.0000".
    expect(screen.getByText('202')).toBeInTheDocument();
  });

  it('says the order named no project rather than showing a bare dash', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([notStarted({ project_label: null, project_name: null })]),
    );

    renderClient();

    expect(await screen.findByText('Not named on the order')).toBeInTheDocument();
  });

  it('counts a not-started order plainly, never as "0 linked" of a mirror nobody wrote', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([notStarted({ line_count: 40, lines_linked: 0 })]),
    );

    renderClient();

    await screen.findByText('SO345418');
    expect(screen.queryByText('0 linked / 40')).not.toBeInTheDocument();
    expect(screen.getByText('40')).toBeInTheDocument();
  });

  it('renders the list in the order the server sent it, earliest required date first', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([
        notStarted({
          sales_order_id: 'so-391698',
          so_number: 'SO391698',
          earliest_required_date: '2022-07-03',
        }),
        notStarted({
          sales_order_id: 'so-346436',
          so_number: 'SO346436',
          earliest_required_date: '2025-04-23',
        }),
        notStarted({
          sales_order_id: 'so-396071',
          so_number: 'SO396071',
          earliest_required_date: '2025-09-01',
        }),
      ]),
    );

    renderClient();

    await screen.findByText('SO391698');
    const rendered = screen
      .getAllByTitle(/^SO\d+$/)
      .map((node) => node.textContent);
    expect(rendered).toEqual(['SO391698', 'SO346436', 'SO396071']);
  });

  it('Start planning is the row’s one decision, and it opens the order it just created', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([notStarted()]));
    adoptSalesOrder.mockResolvedValue({
      project_sales_order_id: 'pso-adopted-345418',
      so_number: 'SO345418',
      review_state: 'needs_cs_review',
      already_adopted: false,
    });
    getReconciliation.mockReturnValue(new Promise(() => {}));

    renderClient();

    fireEvent.click(await screen.findByRole('button', { name: /start planning/i }));

    await waitFor(() => expect(adoptSalesOrder).toHaveBeenCalledWith('so-345418'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getAllByText('SO345418').length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(getReconciliation).toHaveBeenCalledWith('pso-adopted-345418'),
    );
  });

  it('does not open a sheet, or write anything, when a not-started row itself is clicked', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([notStarted()]));

    renderClient();

    // Anywhere but the identity cell, which is the link to the sales order.
    fireEvent.click(await screen.findByText('PEMBINAAN YUEN SENG SDN BHD (PROJECT)'));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(adoptSalesOrder).not.toHaveBeenCalled();
  });

  it('offers the Not started filter, and reaches the service with it', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([notStarted()]));

    renderClient();
    await screen.findByText('SO345418');

    openFilters();
    const select = within(screen.getByRole('menu')).getByRole('combobox');
    expect(within(select).getByRole('option', { name: 'Not started' })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: 'not_started' } });

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ review_state: 'not_started' }),
      ),
    );
  });

  /**
   * The captain: "on click i should be able to view the SO". The row keeps its own meaning
   * (open the plan) and the identity column becomes the way to the sales order, which is the
   * idiom every other listing here already uses.
   */
  describe('the sales-order link', () => {
    it('links an AutoCount-arm row to the core sales order', async () => {
      listFulfilmentPlanning.mockResolvedValue(envelope([notStarted()]));

      renderClient();

      expect(await screen.findByRole('link', { name: 'SO345418' })).toHaveAttribute(
        'href',
        '/scm/sales-orders/so-345418',
      );
    });

    it('links an authored row to its project sales order', async () => {
      listFulfilmentPlanning.mockResolvedValue(
        envelope([
          row({
            row_kind: 'planning_record',
            id: 'pso-authored',
            sales_order_id: null,
            so_number: null,
            project_id: 'proj-1',
            provisional_ref: 'PSO-000123',
            autocount_doc_no: null,
          }),
        ]),
      );

      renderClient();

      expect(await screen.findByRole('link', { name: 'PSO-000123' })).toHaveAttribute(
        'href',
        '/project-sales/proj-1/sales-orders/pso-authored',
      );
    });

    it('renders plain text, never a dead link, when there is nothing to open', async () => {
      listFulfilmentPlanning.mockResolvedValue(
        envelope([
          row({
            row_kind: 'planning_record',
            id: 'pso-adopted',
            sales_order_id: null,
            so_number: null,
            project_id: null,
            provisional_ref: 'PSO-000999',
            autocount_doc_no: null,
          }),
        ]),
      );

      renderClient();

      // Twice: the identity column and the Project SO column, both as plain text.
      expect(await screen.findAllByText('PSO-000999')).toHaveLength(2);
      expect(screen.queryByRole('link', { name: 'PSO-000999' })).not.toBeInTheDocument();
    });

    it('leaves the row’s own click behaviour alone: the link opens the SO, the row opens the plan', async () => {
      listFulfilmentPlanning.mockResolvedValue(envelope([row()]));
      getReconciliation.mockReturnValue(new Promise(() => {}));

      renderClient();

      // The identity cell is a link now, so clicking it must NOT also open the sheet.
      fireEvent.click(await screen.findByRole('link', { name: 'SO376201' }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

      // The rest of the row still opens the plan, exactly as before.
      fireEvent.click(screen.getByText('Buimaco Sdn Bhd (Project)'));
      await screen.findByRole('dialog');
      await waitFor(() => expect(getReconciliation).toHaveBeenCalledWith('pso-1'));
    });
  });

  it('keeps the authored rows and their state beside the adopted ones', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([
        notStarted(),
        row({
          id: 'pso-authored',
          row_kind: 'planning_record',
          origin: 'authored',
          review_state: 'awaiting_reconciliation',
          exception_count: 3,
        }),
      ]),
    );

    renderClient();

    expect(await screen.findByText('Not started')).toBeInTheDocument();
    expect(
      screen.getByText('Awaiting reconciliation · 3 exceptions'),
    ).toBeInTheDocument();
    expect(screen.getByText('PSO-000123')).toBeInTheDocument();
  });
});

/**
 * Sorting the worklist (captain: "all columns should be sortable").
 *
 * The sort is the SERVER's. The grid holds the state and sends it; it never re-sorts the page
 * it was handed, because a page sorted on this side disagrees with paging the moment there is
 * more than one page - row 26 stays on page 2 whatever the browser did to rows 1 to 25.
 */
describe('FulfilmentPlanningClient: sorting', () => {
  function sorted(overrides: Partial<FulfilmentPlanningRow> = {}): FulfilmentPlanningRow {
    return {
      row_kind: 'sales_order',
      id: null,
      sales_order_id: 'so-1',
      so_number: 'SO000001',
      customer_name: 'ALPHA SDN BHD',
      line_count: 1,
      review_state: 'not_started',
      earliest_required_date: '2026-01-01',
      ...overrides,
    };
  }

  it('opens on the order the server promises, earliest required date first', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([sorted()]));

    renderClient();

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ sort: 'earliest_required_date', dir: 'asc' }),
      ),
    );
  });

  it('asks the server for a new sort when a header is pressed, and reverses it on the second press', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([sorted()]));

    renderClient();
    await screen.findByText('ALPHA SDN BHD');

    fireEvent.click(screen.getByRole('button', { name: 'Customer' }));
    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ sort: 'customer_name', dir: 'asc' }),
      ),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Customer' }));
    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ sort: 'customer_name', dir: 'desc' }),
      ),
    );
  });

  it('renders the page in the server’s order, never re-sorting it here', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([
        sorted({ sales_order_id: 'so-3', so_number: 'SO000003', customer_name: 'ZULU SDN BHD' }),
        sorted({ sales_order_id: 'so-1', so_number: 'SO000001', customer_name: 'ALPHA SDN BHD' }),
        sorted({ sales_order_id: 'so-2', so_number: 'SO000002', customer_name: 'MIKE SDN BHD' }),
      ]),
    );

    renderClient();
    await screen.findByText('ZULU SDN BHD');

    fireEvent.click(screen.getByRole('button', { name: 'Customer' }));

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ sort: 'customer_name' }),
      ),
    );
    // The response did not change, so neither did the order on screen. A client re-sort would
    // have put ALPHA first and silently disagreed with page 2.
    expect(screen.getAllByTitle(/SDN BHD$/).map((node) => node.textContent)).toEqual([
      'ZULU SDN BHD',
      'ALPHA SDN BHD',
      'MIKE SDN BHD',
    ]);
  });

  it('renders no sort affordance on a column the server cannot sort', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([sorted()]));

    renderClient();
    await screen.findByText('ALPHA SDN BHD');

    // Sortable: the header is a button that toggles the server's sort.
    expect(screen.getByRole('button', { name: 'Sales order' })).toBeInTheDocument();
    // Not sortable: the selection and action columns carry no server field, so they offer
    // nothing to press.
    expect(screen.queryByRole('button', { name: 'Select' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Action' })).not.toBeInTheDocument();
  });

  it('ignores a sort in the URL that is not one the server can do', async () => {
    currentSearchParams = new URLSearchParams('sort=whatever&dir=desc');
    listFulfilmentPlanning.mockResolvedValue(envelope([sorted()]));

    renderClient();

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ sort: 'earliest_required_date', dir: 'asc' }),
      ),
    );
    expect(listFulfilmentPlanning).not.toHaveBeenCalledWith(
      expect.objectContaining({ sort: 'whatever' }),
    );
  });

  it('opens on the sort the URL carries, so a worklist view is shareable', async () => {
    currentSearchParams = new URLSearchParams('sort=customer_name&dir=desc');
    listFulfilmentPlanning.mockResolvedValue(envelope([sorted()]));

    renderClient();

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ sort: 'customer_name', dir: 'desc' }),
      ),
    );
  });

  it('writes the sort back into the URL', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([sorted()]));

    renderClient();
    await screen.findByText('ALPHA SDN BHD');

    fireEvent.click(screen.getByRole('button', { name: 'Customer' }));

    await waitFor(() =>
      expect(routerReplace).toHaveBeenCalledWith(
        '/project-sales/fulfilment-planning?sort=customer_name&dir=asc',
        expect.objectContaining({ scroll: false }),
      ),
    );
  });

  it('keeps the sort when the review-state filter changes', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([sorted()]));

    renderClient();
    await screen.findByText('ALPHA SDN BHD');

    fireEvent.click(screen.getByRole('button', { name: 'Customer' }));
    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ sort: 'customer_name' }),
      ),
    );

    openFilters();
    fireEvent.change(within(screen.getByRole('menu')).getByRole('combobox'), {
      target: { value: 'not_started' },
    });

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ review_state: 'not_started', sort: 'customer_name' }),
      ),
    );
  });

  it('keeps the sort when the search changes', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([sorted()]));

    renderClient();
    await screen.findByText('ALPHA SDN BHD');

    fireEvent.click(screen.getByRole('button', { name: 'Customer' }));
    fireEvent.change(screen.getByPlaceholderText('Search sales order, customer, project or product'), {
      target: { value: 'alpha' },
    });

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ query: 'alpha', sort: 'customer_name' }),
      ),
    );
  });
});

/**
 * Select all (captain: "need to have select all option also at the top left of table").
 *
 * On the repo's own select column, so the header checkbox, its indeterminate state and the
 * selected-id read are the same ones every other bulk-action list uses. What is worth pinning
 * is that ticking everything means the same thing ticking rows one at a time means, and that
 * the 50-order board cap is STATED when it bites rather than silently truncating the selection.
 */
describe('FulfilmentPlanningClient: select all', () => {
  function selectable(index: number): FulfilmentPlanningRow {
    return {
      row_kind: 'sales_order',
      id: null,
      sales_order_id: `so-${index}`,
      so_number: `SO${100000 + index}`,
      customer_name: 'A CUSTOMER SDN BHD',
      line_count: 1,
      review_state: 'not_started',
      earliest_required_date: '2026-01-01',
    };
  }

  it('offers a select-all control at the top left of the table', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([selectable(1), selectable(2)]));

    renderClient();
    await screen.findByText('SO100001');

    const table = screen.getByRole('table');
    const firstHeader = within(table).getAllByRole('columnheader')[0];
    expect(
      within(firstHeader).getByRole('checkbox', { name: 'Select all rows on this page' }),
    ).toBeInTheDocument();
  });

  it('ticks every row, and Plan together means the same thing it means one row at a time', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([selectable(1), selectable(2), selectable(3)]),
    );

    renderClient();
    await screen.findByText('SO100001');

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows on this page' }));

    expect(screen.getByRole('button', { name: /Plan together \(3\)/ })).toBeEnabled();
    expect(
      screen.getByRole('checkbox', { name: 'Select SO100001 for planning' }),
    ).toBeChecked();
  });

  it('leaves a row with no sales order unselectable, and says why', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([
        selectable(1),
        row({ id: 'pso-authored', so_number: null, sales_order_id: null, autocount_doc_no: null }),
      ]),
    );

    renderClient();
    await screen.findByText('SO100001');

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows on this page' }));

    // Only the row that HAS a core sales order can go on the board: an authored Project SO
    // with no core order has no core lines to aggregate. One selected order names the order,
    // not the count.
    expect(screen.getByRole('button', { name: /Plan SO100001/ })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select row' })).toBeDisabled();
    expect(
      screen.getByTitle('Only a sales order from the AutoCount book can go on the board.'),
    ).toBeInTheDocument();
  });

  it('states the 50-order cap when a select-all goes past it, rather than truncating', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope(Array.from({ length: 60 }, (_unused, index) => selectable(index))),
    );

    renderClient();
    await screen.findByText('SO100000');

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows on this page' }));

    expect(
      screen.getByText('60 ticked. A board takes at most 50 sales orders.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Plan together \(60\)/ })).toBeDisabled();
  });

  it('clears the whole selection', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([selectable(1), selectable(2)]));

    renderClient();
    await screen.findByText('SO100001');

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows on this page' }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear the selection' }));

    expect(screen.getByRole('button', { name: /Plan together \(0\)/ })).toBeDisabled();
  });
});

/**
 * Building one board out of several searches (evidence run, 18 August 2026).
 *
 * A planner does not have the two orders they want to plan together on one screen: they search
 * SO403765, tick it, then search SO396351 and tick that. Both halves of that have to hold - the
 * search box has to still BE there once a row is ticked, and a tick has to survive the page of
 * rows it was made on being replaced. Without either, the only way to a two-order board was to
 * hand-write `?orders=` into the URL, which is not a thing a planner does.
 */
describe('FulfilmentPlanningClient: ticking across searches', () => {
  function found(index: number): FulfilmentPlanningRow {
    return {
      row_kind: 'sales_order',
      id: null,
      sales_order_id: `so-${index}`,
      so_number: `SO${100000 + index}`,
      customer_name: 'A CUSTOMER SDN BHD',
      line_count: 1,
      review_state: 'not_started',
      earliest_required_date: '2026-01-01',
    };
  }

  /** One row per search needle, so each search replaces the page entirely. */
  function respondPerSearch() {
    listFulfilmentPlanning.mockImplementation((params: { query?: string }) =>
      Promise.resolve(envelope([found(params?.query === 'SO100002' ? 2 : 1)])),
    );
  }

  it('keeps the search box once an order is ticked', async () => {
    respondPerSearch();

    renderClient();
    await screen.findByText('SO100001');

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SO100001 for planning' }));

    expect(
      screen.getByPlaceholderText('Search sales order, customer, project or product'),
    ).toBeInTheDocument();
  });

  it('counts an order ticked on an earlier search, and opens the board on both', async () => {
    respondPerSearch();
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    renderClient();
    await screen.findByText('SO100001');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SO100001 for planning' }));

    fireEvent.change(
      screen.getByPlaceholderText('Search sales order, customer, project or product'),
      { target: { value: 'SO100002' } },
    );
    await screen.findByText('SO100002');
    // The first order is off the page now, and still ticked.
    expect(screen.getByRole('button', { name: /Plan SO100001/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SO100002 for planning' }));
    expect(screen.getByRole('button', { name: /Plan together \(2\)/ })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: /Plan together \(2\)/ }));
    await waitFor(() =>
      expect(routerReplace).toHaveBeenCalledWith(
        expect.stringContaining('orders=SO100001%2CSO100002'),
        expect.objectContaining({ scroll: false }),
      ),
    );
  });

  it('clears every ticked order, including the ones no longer on the page', async () => {
    respondPerSearch();

    renderClient();
    await screen.findByText('SO100001');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SO100001 for planning' }));

    fireEvent.change(
      screen.getByPlaceholderText('Search sales order, customer, project or product'),
      { target: { value: 'SO100002' } },
    );
    await screen.findByText('SO100002');
    fireEvent.click(screen.getByRole('button', { name: 'Clear the selection' }));

    expect(screen.getByRole('button', { name: /Plan together \(0\)/ })).toBeDisabled();
  });
});

/**
 * Searching the worklist (captain: "i should be able to search by product, by sales order, by
 * customer, to shrink the dataset i am viewing").
 *
 * The box must SAY what it searches. A planner who types an item code into a box labelled
 * "Search" and gets nothing concludes product search is broken, when the truth would only be
 * that nobody told them what the box covers.
 */
describe('FulfilmentPlanningClient: search', () => {
  it('names what it searches, so a product code is not typed into a box that never promised it', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([row()]));

    renderClient();

    expect(
      await screen.findByPlaceholderText('Search sales order, customer, project or product'),
    ).toBeInTheDocument();
  });

  /**
   * Two things a planner would otherwise discover by being wrong about them. Both are in the
   * box's own tooltip rather than as prose on the page: a hint of the form "what happens if I
   * set this" is allowed, a paragraph teaching the feature is not.
   */
  it('warns that a product match does not narrow the numbers in the row', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([row()]));

    renderClient();

    const box = await screen.findByPlaceholderText(
      'Search sales order, customer, project or product',
    );
    const hint = box.getAttribute('title') ?? '';
    // A product match is an ORDER match: the row still counts the whole order.
    expect(hint).toContain('whole order');
    // And only an outstanding line counts as a match.
    expect(hint).toContain('outstanding');
    // One short hint, not a paragraph: a second sentence is teaching, and belongs in the guide.
    expect(hint).not.toMatch(/\. [A-Z]/);
  });

  it('sends a product code as the same query parameter', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([row()]));

    renderClient();
    await screen.findByText('PSO-000123');

    fireEvent.change(
      screen.getByPlaceholderText('Search sales order, customer, project or product'),
      { target: { value: 'WESERP10B' } },
    );

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({ query: 'WESERP10B' }),
      ),
    );
  });

  it('shrinks the set without clobbering the sort or the filter already in the URL', async () => {
    currentSearchParams = new URLSearchParams('sort=customer_name&dir=desc');
    listFulfilmentPlanning.mockResolvedValue(envelope([row()]));

    renderClient();
    await screen.findByText('PSO-000123');

    openFilters();
    fireEvent.change(within(screen.getByRole('menu')).getByRole('combobox'), {
      target: { value: 'needs_cs_review' },
    });
    fireEvent.change(
      screen.getByPlaceholderText('Search sales order, customer, project or product'),
      { target: { value: 'WESERP10B' } },
    );

    await waitFor(() =>
      expect(listFulfilmentPlanning).toHaveBeenCalledWith(
        expect.objectContaining({
          query: 'WESERP10B',
          review_state: 'needs_cs_review',
          sort: 'customer_name',
          dir: 'desc',
        }),
      ),
    );
  });
});

/**
 * A board link that actually opens the board (PLAN 13.2 / 13.3, finally built).
 *
 * The selection and the granularity live in the URL by sales-order NUMBER, never by id, so a
 * planner can send someone the board they are looking at. Until now `?product=` survived a
 * reload while the board itself did not, which made the shareable link a half-truth.
 */
describe('FulfilmentPlanningClient: the board lives in the URL', () => {
  function planned(index: number): FulfilmentPlanningRow {
    return {
      row_kind: 'sales_order',
      id: null,
      sales_order_id: `so-${index}`,
      so_number: `SO${100000 + index}`,
      customer_name: 'A CUSTOMER SDN BHD',
      line_count: 1,
      review_state: 'not_started',
      earliest_required_date: '2026-01-01',
    };
  }

  it('opens the board on the orders the URL names', async () => {
    currentSearchParams = new URLSearchParams('orders=SO100001,SO100002');
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1), planned(2)]));
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    renderClient();

    expect(await screen.findByText('Planning 2 sales orders together')).toBeInTheDocument();
    // S1 (`PLAN-board-oi-mechanical-22sep.md`, AC-B1-1): an absent granularity param now
    // resolves to `date`, not `week`.
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO100001', 'SO100002'],
        'date',
        false,
        {},
      ),
    );
  });

  it('carries the granularity and the product filter through the same link', async () => {
    currentSearchParams = new URLSearchParams(
      'orders=SO100001,SO100002&granularity=month&product=cks',
    );
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1), planned(2)]));
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    renderClient();

    await screen.findByText('Planning 2 sales orders together');
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO100001', 'SO100002'],
        'month',
        false,
        {},
      ),
    );
    // The board's own box now lives in the list toolbar / grid strip, drawn once the board has
    // loaded (it is still pending here), so the page header carries none.
    expect(
      screen.queryAllByPlaceholderText('Search sales order, customer, project or product'),
    ).toHaveLength(0);
  });

  it('falls back to date on a granularity nobody defined', async () => {
    // S1 (AC-B1-1/AC-B1-5): `date` is what an unrecognised param resolves to now, the same
    // default an ABSENT param gets.
    currentSearchParams = new URLSearchParams('orders=SO100001&granularity=fortnightly');
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1)]));
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    renderClient();

    await screen.findByText('Planning 1 sales orders together');
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(['SO100001'], 'date', false, {}),
    );
  });

  it('refuses a link past the cap rather than quietly planning the first fifty', async () => {
    const many = Array.from({ length: 60 }, (_unused, index) => `SO${200000 + index}`);
    currentSearchParams = new URLSearchParams(`orders=${many.join(',')}`);
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1)]));

    renderClient();

    expect(
      await screen.findByText('60 ticked. A board takes at most 50 sales orders.'),
    ).toBeInTheDocument();
    // The worklist, not a board of the first fifty: a set the sender did not choose is not the
    // set they meant to share.
    expect(screen.queryByText(/sales orders together/)).not.toBeInTheDocument();
    expect(getPlanningBoard).not.toHaveBeenCalled();
  });

  it('opens a board on every order of a planning-change batch, cap or no cap', async () => {
    // The cap exists because a selection the sender did not choose is not the set they
    // meant to share. A batch IS a chosen set - one upload, the orders it moved - and the
    // separate batch page is retired, so refusing it dead-ends the change with nowhere
    // else to decide it.
    const many = Array.from({ length: 60 }, (_unused, index) => `SO${200000 + index}`);
    currentSearchParams = new URLSearchParams(
      `orders=${many.join(',')}&batch=pcb-so381895`,
    );
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1)]));

    renderClient();

    expect(await screen.findByText('Planning 60 sales orders together')).toBeInTheDocument();
    expect(
      screen.queryByText('60 ticked. A board takes at most 50 sales orders.'),
    ).not.toBeInTheDocument();
  });

  /**
   * A "Changed" link (the sales-order list's own badge, or the planning-changes list) deep-
   * links `?orders=<so>&batch=<id>`. Clicking a SECOND such link while this screen is already
   * open is a SAME-ROUTE navigation - only the query string moves - and today `boardOrders`
   * (FulfilmentPlanningClient.tsx ~147-156) is a `React.useState` LAZY INITIALIZER that reads
   * `urlOrders`/`batchId` once, at mount: a lazy initializer never re-runs on a later render,
   * so the worklist stays on screen instead of opening the board for the new order. A full
   * reload (a fresh mount) works, which is why this only reproduces on a rerender of the SAME
   * instance, not a fresh `renderClient()` call.
   */
  it('opens the board for a new order when a same-route link only changes ?orders=/?batch=', async () => {
    currentSearchParams = new URLSearchParams('orders=SO100001&batch=batch-1');
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1), planned(2)]));
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
    });
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <FulfilmentPlanningClient />
      </QueryClientProvider>,
    );

    await screen.findByText('Planning 1 sales orders together');
    // S1 (AC-B1-1): an absent granularity param now resolves to `date`, not `week`.
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(['SO100001'], 'date', false, {}),
    );

    getPlanningBoard.mockClear();
    // The SAME route, a DIFFERENT order and batch - what clicking a second "Changed" link
    // does, without ever unmounting this screen. A FRESH element, not the same object
    // reference rerendered: React bails out of a rerender with an identical element and
    // never re-reads `useSearchParams`, which would hide the very bug this test exists to
    // catch (the coder proved this with a probe).
    currentSearchParams = new URLSearchParams('orders=SO100002&batch=batch-2');
    rerender(
      <QueryClientProvider client={client}>
        <FulfilmentPlanningClient />
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(['SO100002'], 'date', false, {}),
    );
  });

  it('writes the selection into the URL when the board is opened', async () => {
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1), planned(2)]));
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    renderClient();
    await screen.findByText('SO100001');

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows on this page' }));
    fireEvent.click(screen.getByRole('button', { name: /Plan together \(2\)/ }));

    await waitFor(() =>
      expect(routerReplace).toHaveBeenCalledWith(
        expect.stringContaining('orders=SO100001%2CSO100002'),
        expect.objectContaining({ scroll: false }),
      ),
    );
  });

  it('goes back to the SALES ORDER LIST, dropping the whole board from the URL', async () => {
    // The board is opened from the sales-order list's Plan action, so that is where Back
    // belongs. The board's own three parameters leave with it; everything else rides along,
    // so a board opened from a filtered, paged list returns to that page.
    currentSearchParams = new URLSearchParams(
      'orders=SO100001&granularity=month&product=cks&page=3&limit=25&query=rowenda',
    );
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1)]));
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    renderClient();
    await screen.findByText('Planning 1 sales orders together');

    fireEvent.keyDown(screen.getByRole('button', { name: 'Board actions' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Back to sales orders' }));

    await waitFor(() => {
      const back = String(routerPush.mock.calls[routerPush.mock.calls.length - 1][0]);
      expect(back.split('?')[0]).toBe('/scm/sales-orders');
      expect(back).not.toContain('orders=');
      expect(back).not.toContain('granularity=');
      expect(back).not.toContain('product=');
      // The list's own page and search are not collateral damage.
      expect(back).toContain('page=3');
      expect(back).toContain('limit=25');
      expect(back).toContain('query=rowenda');
    });
  });

  it('drops the WORKLIST own query rather than forwarding it as a sales-order one', async () => {
    // A board opened from the worklist carries `sort=earliest_required_date`, which is not a
    // sales-order sort field: the same param name means a different thing on that screen, and
    // carrying it over would ask the list to sort by a column it does not have.
    currentSearchParams = new URLSearchParams(
      'orders=SO100001&sort=earliest_required_date&dir=asc&state=planned',
    );
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1)]));
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    renderClient();
    await screen.findByText('Planning 1 sales orders together');

    fireEvent.keyDown(screen.getByRole('button', { name: 'Board actions' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Back to sales orders' }));

    await waitFor(() => expect(routerPush).toHaveBeenLastCalledWith('/scm/sales-orders'));
  });

  it('goes back to a bare sales-order list when the board URL carried no list query', async () => {
    currentSearchParams = new URLSearchParams('orders=SO100001');
    listFulfilmentPlanning.mockResolvedValue(envelope([planned(1)]));
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    renderClient();
    await screen.findByText('Planning 1 sales orders together');

    fireEvent.keyDown(screen.getByRole('button', { name: 'Board actions' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Back to sales orders' }));

    await waitFor(() =>
      expect(routerPush).toHaveBeenLastCalledWith('/scm/sales-orders'),
    );
  });
});

/**
 * AC-B7 (`PLAN-scm-board-picks-up-pending-change.md`): the worklist shows the same
 * `Changed` pill the SCM Sales Orders list shows, copied from `SalesOrdersGrid.tsx:499-509`,
 * in the `so_number` cell - linking to the board on that order and batch, no `batch=`
 * required by anything else, but present here since it is a known one.
 */
describe('FulfilmentPlanningClient: the Changed pill (AC-B7)', () => {
  it('shows a Changed pill linking to the board+batch for a row with a pending batch', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([
        row({
          id: 'pso-changed',
          so_number: 'SO381895',
          planning_change_batch_id: 'pcb-so381895',
        }),
      ]),
    );

    renderClient();
    await screen.findByText('SO381895');

    const pill = screen.queryByTestId('so-changed-SO381895');
    expect(pill).toBeInTheDocument();
    expect(pill?.getAttribute('href')).toEqual(
      expect.stringContaining('orders=SO381895'),
    );
    expect(pill?.getAttribute('href')).toEqual(
      expect.stringContaining('batch=pcb-so381895'),
    );
  });

  it('shows no Changed pill for a row with no pending batch', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([row({ id: 'pso-unchanged', so_number: 'SO381896' })]),
    );

    renderClient();
    await screen.findByText('SO381896');

    expect(screen.queryByTestId('so-changed-SO381896')).not.toBeInTheDocument();
  });
});
