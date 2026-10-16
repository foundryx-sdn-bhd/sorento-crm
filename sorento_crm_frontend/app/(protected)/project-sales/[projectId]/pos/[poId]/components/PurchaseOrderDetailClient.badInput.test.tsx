/** NUMBER-FIELDS round 3 (R5): bad input in PO amount is refused with the sentence, nothing is sent. */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  Project,
  ProjectPurchaseOrder,
  PurchaseOrderLine,
} from '../../../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const push = vi.fn();
let searchParams = new URLSearchParams();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => '/project-sales/p1/pos/po1',
  useSearchParams: () => searchParams,
}));

const getProject = vi.fn();
const listPurchaseOrders = vi.fn();
const listPurchaseOrderLines = vi.fn();
const updatePurchaseOrder = vi.fn();
const deletePurchaseOrder = vi.fn();

vi.mock('../../../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../../_shared/services/projectService')
  >();
  return {
    ...actual,
    getProject: (...args: unknown[]) => getProject(...args),
    listPurchaseOrders: (...args: unknown[]) => listPurchaseOrders(...args),
    listPurchaseOrderLines: (...args: unknown[]) => listPurchaseOrderLines(...args),
    updatePurchaseOrder: (...args: unknown[]) => updatePurchaseOrder(...args),
    deletePurchaseOrder: (...args: unknown[]) => deletePurchaseOrder(...args),
    listQuotations: vi.fn(async () => []),
    listQuotationVersions: vi.fn(async () => []),
    listParties: vi.fn(async () => ({ data: [], pagination: { total: 0 } })),
  };
});

// The documents strip. Its own tests cover it; here it must simply not fetch.
vi.mock('../../../../_shared/services/poIntakeService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../../_shared/services/poIntakeService')
  >();
  return { ...actual, listPOVersions: vi.fn(async () => []) };
});

vi.mock('@/app/(protected)/master-data-management/products/services/productService', () => ({
  getProductsForVariantSelect: vi.fn(async () => []),
}));

const toastError = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: {
    custom: vi.fn(),
    error: (...args: unknown[]) => toastError(...args),
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

import { PurchaseOrderDetailClient } from './PurchaseOrderDetailClient';

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    project_code: 'PRJ-000001',
    title: 'Menara Test',
    outcome: 'open',
    is_critical: false,
    brands: [],
    brand_ids: [],
    next_action_overdue: false,
    stale_level: 0,
    is_unattended: false,
    open_task_count: 0,
    can_edit: true,
    ...overrides,
  };
}

function po(overrides: Partial<ProjectPurchaseOrder> = {}): ProjectPurchaseOrder {
  return {
    id: 'po1',
    project_id: 'p1',
    po_number: 'PO-9001',
    po_source: 'contractor_direct',
    quotation_version_id: 'v2',
    scope_label: 'House Units',
    version_no: 2,
    issuing_party_id: 'party-1',
    issuing_party_name: 'Bina Utama Sdn Bhd',
    po_date: '2026-07-24',
    po_amount: null,
    notes: 'Phase 1 only',
    line_count: 1,
    line_total: '9000.00',
    model_mismatch_count: 0,
    price_mismatch_count: 0,
    updated_at: '2026-07-25T02:00:00',
    ...overrides,
  };
}

function line(overrides: Partial<PurchaseOrderLine> = {}): PurchaseOrderLine {
  return {
    id: 'pl1',
    po_id: 'po1',
    product_code: 'SRT-WC-01',
    description: 'Wall-hung WC',
    unit_price: '900.00',
    quantity: '10.00',
    uom: 'PCS',
    line_total: '9000.00',
    model_mismatch: false,
    price_mismatch: false,
    sort_order: 0,
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PurchaseOrderDetailClient projectId="p1" poId="po1" />
    </QueryClientProvider>,
  );
}

/** Into the session, from the header's one call to action. */
async function openEditor() {
  // The lines have to be on screen first: the session seeds itself from them, and a Save
  // pressed before that seeding would carry a set the user never saw.
  await screen.findByText('Wall-hung WC');
  fireEvent.click(await screen.findByRole('button', { name: /Edit the PO/i }));
  const save = await screen.findByRole('button', { name: 'Save purchase order' });
  await screen.findByRole('textbox', { name: 'Code on the PO on SRT-WC-01' });
  return save;
}

beforeEach(() => {
  vi.clearAllMocks();
  searchParams = new URLSearchParams();
  getProject.mockResolvedValue(project());
  listPurchaseOrders.mockResolvedValue([po()]);
  listPurchaseOrderLines.mockResolvedValue([line()]);
  updatePurchaseOrder.mockImplementation(async () => po({ po_number: 'PO-9001-A' }));
  deletePurchaseOrder.mockResolvedValue(undefined);
});


import { typeBadInput } from '@/test-utils/badInput';

describe('PurchaseOrderDetailClient bad number input (R5)', () => {
  it('PO amount with bad input: Save is refused, the sentence shows under the box, no request', async () => {
    renderPage();
    const save = await openEditor();
    typeBadInput(await screen.findByLabelText('PO amount (RM)'));
    fireEvent.click(save);
    expect(
      await screen.findByText('Enter a number of at least 0, or leave it blank.'),
    ).toBeInTheDocument();
    expect(updatePurchaseOrder).not.toHaveBeenCalled();
  });
});
