/**
 * AC-CONSIGN-CRM UAC-06: the Fulfilment Planning list shows "Consignment" in the Sales order
 * cell when the row's `so_doc_type` is consignment. Mocking copied from
 * `FulfilmentPlanningClient.test.tsx`.
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

describe('UAC-06: Sales order cell carries the Consignment label', () => {
  it('a consignment row shows Consignment', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([row({ so_number: 'CG000123', so_doc_type: 'consignment' } as Partial<FulfilmentPlanningRow>)]),
    );
    renderClient();
    const cell = (await screen.findByText('CG000123')).closest('tr') as HTMLElement;
    expect(within(cell).getByText('Consignment')).toBeInTheDocument();
  });

  it('a sales_order row shows no Consignment label', async () => {
    listFulfilmentPlanning.mockResolvedValue(
      envelope([row({ so_number: 'SO000123', so_doc_type: 'sales_order' } as Partial<FulfilmentPlanningRow>)]),
    );
    renderClient();
    const cell = (await screen.findByText('SO000123')).closest('tr') as HTMLElement;
    expect(within(cell).queryByText('Consignment')).toBeNull();
  });
});
