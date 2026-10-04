/**
 * The loading plans list (part 4, R3; S1 AC-A1, AC-A3, AC-A4, AC-A5).
 *
 * What this suite pins is the LIST's own contract, not the grid component's: the columns the
 * captain named, the default Active filter, the single Upload primary, the whole-row click,
 * and the row's "..." menu (Send to supplier, Cancel plan, separator, Delete plan) - Cancel
 * and Delete both park a deferred action rather than opening a confirm dialog, and Delete is
 * refused with a reason once a notice has gone out. The record page behind the row is
 * `LoadingPlanView.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
/* The grace window is the server's; what this file proves is that the row parks one. */
const createPendingAction = vi.fn().mockResolvedValue({
  id: 'pa-1',
  action_key: 'loading_plan.delete',
  entity_type: 'loading_plan',
  entity_id: 'plan-1',
  commit_at: '2026-08-30T10:00:10',
  window_seconds: 10,
});
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) => createPendingAction(...args),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

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

const push = vi.fn();
vi.mock('next/navigation', () => ({
  usePathname: () => '/scm/loading-plan',
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

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

// DataGrid persists column prefs through this hook (which fires network); without the stub
// the grid renders skeletons forever and no row can be asserted.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

// The dialog owns the whole upload journey and has its own suite; here it only has to prove
// the one primary action reaches it.
vi.mock('./PlanContainerDialog', () => ({
  PlanContainerDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="plan-container-dialog">Plan a container</div> : null,
}));

const getLoadingPlanList = vi.fn();
const deleteLoadingPlan = vi.fn();
vi.mock('../../services/fulfilmentService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/fulfilmentService')>();
  return {
    ...actual,
    getLoadingPlanList: (...a: unknown[]) => getLoadingPlanList(...a),
    deleteLoadingPlan: (...a: unknown[]) => deleteLoadingPlan(...a),
  };
});

import { LoadingPlansGrid } from './LoadingPlansGrid';

const PLANNING = {
  id: 'plan-1',
  supplier_id: 'sup-1',
  supplier_name: 'CHAOZHOU JINBAICHUAN SANITARY WARE CO., LTD',
  supplier_email: 'person36@example.com',
  started_at: '2026-08-27T14:02:00',
  plan_horizon_date: '2026-09-30',
  document_kind: 'stock_list' as const,
  document_label: 'Stock list 27/07/2026',
  source_attachment_id: null,
  source_attachment_filename: null,
  status: 'planning' as const,
  sent_channel: null,
  sent_at: null,
  opened_at: null,
  last_opened_at: null,
  open_count: 0,
  cancelled_at: null,
  cancelled_by: null,
  line_edits: {},
  to_request_qty: 83229,
  to_request_cbm: 2588,
};

const SENT = {
  ...PLANNING,
  id: 'plan-2',
  supplier_name: 'KAILU SANITARY',
  document_kind: 'none' as const,
  document_label: 'No file',
  status: 'sent' as const,
  sent_channel: 'email' as const,
  sent_at: '2026-08-27T14:40:00',
  opened_at: '2026-08-27T06:55:00',
  last_opened_at: '2026-08-27T07:10:00',
  open_count: 3,
  to_request_qty: 4120,
  to_request_cbm: 61,
};

function renderGrid() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LoadingPlansGrid />
    </QueryClientProvider>,
  );
}

/**
 * The row's "..." menu (`RowActionsMenu`/D15). Radix opens on pointerdown, which jsdom
 * does not synthesize from a click, so drive it by keyboard instead (ArrowDown opens and
 * focuses the first item). Both rows share the accessible name, so a caller picks by index.
 */
async function openRowMenu(index: number) {
  const triggers = await screen.findAllByRole('button', { name: 'loading plan actions' });
  triggers[index].focus();
  fireEvent.keyDown(triggers[index], { key: 'ArrowDown', code: 'ArrowDown' });
  return within(await screen.findByRole('menu'));
}

describe('LoadingPlansGrid', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLoadingPlanList.mockResolvedValue({ data: [PLANNING, SENT], total: 2 });
    deleteLoadingPlan.mockResolvedValue(undefined);
  });

  it('shows the columns the plan names, newest first, and asks for the Active chip', async () => {
    renderGrid();

    for (const header of [
      'Started',
      'Supplier',
      'Sales orders needed',
      'Document',
      'To request',
      'Sent',
      'Opened',
      'Status',
    ]) {
      expect(await screen.findByRole('button', { name: header })).toBeTruthy();
    }
    await waitFor(() => expect(getLoadingPlanList).toHaveBeenCalled());
    const params = getLoadingPlanList.mock.calls[0][0] as Record<string, unknown>;
    expect(params.status).toBe('active');
    expect(params.sorting).toEqual([{ id: 'started_at', desc: true }]);
  });

  it('prints the ask with its estimated volume underneath', async () => {
    renderGrid();

    expect(await screen.findByText('83,229')).toBeTruthy();
    expect(screen.getByText('est. 2,588 cbm')).toBeTruthy();
  });

  it('the Opened column reads the real opens, times and all (AC-C8)', async () => {
    renderGrid();

    // The sent plan: when they last looked, and how many times they have.
    expect(await screen.findByText('3 times')).toBeTruthy();
    // The plan nobody has been sent yet says so in words - a dash would read as "we do not
    // know", and since the notice counts opens we do.
    expect(screen.getByText('Not opened yet')).toBeTruthy();
  });

  it('carries ONE primary action, Upload, and it opens Plan a container', async () => {
    renderGrid();

    const upload = await screen.findByTestId('open-plan-container');
    expect(screen.queryAllByRole('button', { name: /^Upload/ })).toHaveLength(1);

    fireEvent.click(upload);

    expect(await screen.findByTestId('plan-container-dialog')).toBeTruthy();
  });

  it('opens the record on a whole-row click', async () => {
    renderGrid();

    fireEvent.click(await screen.findByText('Stock list 27/07/2026'));

    expect(push).toHaveBeenCalledWith('/scm/loading-plan/plan-1');
  });

  it('the row menu offers Send to supplier, Cancel plan and Delete plan, in that order (AC-A1)', async () => {
    renderGrid();

    const menu = await openRowMenu(0);
    const items = menu.getAllByRole('menuitem').map((el) => el.textContent);
    expect(items).toEqual(['Send to supplier', 'Cancel plan', 'Delete plan']);
  });

  it('parks a cancel from the row menu, no dialog in the way (AC-A3)', async () => {
    renderGrid();

    const menu = await openRowMenu(0);
    fireEvent.click(menu.getByRole('menuitem', { name: 'Cancel plan' }));

    await waitFor(() =>
      expect(createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({
          actionKey: 'loading_plan.cancel',
          entityType: 'loading_plan',
          entityId: 'plan-1',
        }),
      ),
    );
    expect(screen.queryByText('Cancel this plan?')).not.toBeInTheDocument();
    // The row action must not ALSO open the record.
    expect(push).not.toHaveBeenCalled();
  });

  it('refuses to delete a sent plan and says why (Q5, AC-A5)', async () => {
    renderGrid();

    // Row order is the response order: the sent plan is the second row.
    const menu = await openRowMenu(1);
    const del = menu.getByRole('menuitem', { name: 'Delete plan' });
    expect(del.getAttribute('aria-disabled')).toBe('true');
    expect(del.getAttribute('title')).toBe('Sent plans are cancelled, not deleted');

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    const firstMenu = await openRowMenu(0);
    expect(
      firstMenu.getByRole('menuitem', { name: 'Delete plan' }).getAttribute('aria-disabled'),
    ).toBeNull();
  });

  it('parks the delete of an unsent plan, with no dialog in the way (S6-10, AC-A4)', async () => {
    renderGrid();

    const menu = await openRowMenu(0);
    fireEvent.click(menu.getByRole('menuitem', { name: 'Delete plan' }));

    // D7: the press IS the action. The sent-plan rule is still the server's, and it
    // is stated up front by the disabled item in the test above.
    await waitFor(() =>
      expect(createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({
          actionKey: 'loading_plan.delete',
          entityType: 'loading_plan',
          entityId: 'plan-1',
        }),
      ),
    );
    expect(deleteLoadingPlan).not.toHaveBeenCalled();
    expect(screen.queryByText('Delete this plan?')).not.toBeInTheDocument();
  });

  it('sends from the row menu by opening the record with the send dialog queued', async () => {
    renderGrid();

    const menu = await openRowMenu(0);
    fireEvent.click(menu.getByRole('menuitem', { name: 'Send to supplier' }));

    expect(push).toHaveBeenCalledWith('/scm/loading-plan/plan-1?send=1');
  });

  it('says what an empty list means rather than showing an empty table', async () => {
    getLoadingPlanList.mockResolvedValue({ data: [], total: 0 });

    renderGrid();

    expect(
      await screen.findByText(
        /No container plans yet\. Upload a supplier stock list or proforma invoice to start one\./,
      ),
    ).toBeTruthy();
  });
});
