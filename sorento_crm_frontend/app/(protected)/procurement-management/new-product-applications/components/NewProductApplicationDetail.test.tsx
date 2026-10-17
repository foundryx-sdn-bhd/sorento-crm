/**
 * NEW-PRODUCT-APP staff detail: which button each viewer sees on each status, and the Link
 * product search (it only lists; a person picks). Hooks, the pager and the shell are mocked;
 * the page's own rules run.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  NewProductApplicationDetail as Detail,
  NewProductApplicationLine,
} from '@/types/newProductApplication';

const state = vi.hoisted(() => ({
  perms: new Set<string>(),
  userId: 'u-owner',
  data: undefined as unknown,
  link: vi.fn(),
  search: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
  usePathname: () => '/procurement-management/new-product-applications/a-1',
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => state.perms.has(slug),
}));
vi.mock('@/hooks/useEffectiveUserId', () => ({
  useEffectiveUserId: () => state.userId,
}));
vi.mock('@/hooks/useDeferredAction', () => ({
  useDeferredAction: () => ({
    countdown: null,
    isBlocked: false,
    start: vi.fn(),
  }),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/components/audit/AuditTrail', () => ({
  default: () => <div data-testid="audit-trail" />,
}));
vi.mock('@/components/common/DetailActions', () => ({
  default: ({
    gear,
    primary,
  }: {
    gear?: React.ReactNode;
    primary?: React.ReactNode;
  }) => (
    <div>
      {gear}
      {primary}
    </div>
  ),
}));
vi.mock('@/components/common/DetailActionsMenu', () => ({
  DetailActionsMenu: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenuItem: ({
    children,
    onSelect,
  }: {
    children: React.ReactNode;
    onSelect?: () => void;
  }) => <button onClick={() => onSelect?.()}>{children}</button>,
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: () => Promise.resolve(),
    isLoading: false,
  }),
}));
vi.mock('../hooks/useNewProductApplications', () => ({
  newProductApplicationsPagerQuery: {},
  useNewProductApplicationQuery: () => ({
    data: state.data,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useNewProductApplicationMutations: () => {
    const idle = () => ({
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
      isPending: false,
    });
    return {
      submit: idle(),
      approve: idle(),
      reject: idle(),
      revise: idle(),
      cancel: idle(),
      link: { mutate: state.link, mutateAsync: state.link, isPending: false },
      close: idle(),
      undo: idle(),
    };
  },
}));
vi.mock('../services/newProductApplicationService', () => ({
  searchNewProductApplicationProducts: (q: string) => state.search(q),
}));

import NewProductApplicationDetail from './NewProductApplicationDetail';

function Page() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <NewProductApplicationDetail applicationId="a-1" />
    </QueryClientProvider>
  );
}

function line(
  over: Partial<NewProductApplicationLine> = {},
): NewProductApplicationLine {
  return {
    id: 'l-1',
    line_no: 1,
    proposed_code: 'XY-1234-MB',
    description: 'Example kitchen sink',
    further_description: null,
    category_id: 'g-1',
    category_code: 'KS',
    category_name: 'Kitchen Sink',
    base_uom_id: 'u-1',
    base_uom_code: 'UNIT',
    base_uom_name: 'Unit',
    brand_id: null,
    brand_name: null,
    item_type_id: null,
    item_type_name: null,
    supplier_id: null,
    supplier_code: null,
    supplier_name: null,
    supplier_item_code: null,
    cost_price: null,
    selling_price: null,
    expected_qty: '55',
    remark: null,
    opened_product_id: null,
    opened_product_code: null,
    opened_product_name: null,
    opened_product_active: null,
    closed_reason: null,
    resolved_by: null,
    resolved_by_name: null,
    resolved_at: null,
    ...over,
  };
}

function detail(over: Partial<Detail> = {}): Detail {
  return {
    id: 'a-1',
    application_no: 'NPA-000001',
    status: 'draft',
    status_label: 'Draft',
    source: 'staff',
    applicant_id: 'u-owner',
    applicant_name: 'Owner',
    stock_inquiry_id: 'si-1',
    stock_inquiry_number: 'SI26-0412',
    stock_inquiry_product_code: 'XY-1234-MB',
    customer_id: null,
    customer_code: null,
    customer_name: 'EXAMPLE DEVELOPER SDN BHD',
    project_name: 'EXAMPLE RESIDENCE, TOWER B',
    first_line: 'XY-1234-MB',
    lines_total: 2,
    lines_done: 0,
    opened_codes: [],
    needed_by: null,
    submitted_at: null,
    created_at: '2026-10-17T01:00:00',
    updated_at: null,
    remark: null,
    decided_at: null,
    decided_by: null,
    decided_by_name: null,
    reject_reason: null,
    revise_reason: null,
    cancelled_at: null,
    cancel_reason: null,
    completed_at: null,
    lines: [
      line(),
      line({ id: 'l-2', line_no: 2, proposed_code: 'XY-1234-BG' }),
    ],
    ...over,
  };
}

const button = (name: RegExp) => screen.queryByRole('button', { name });

beforeEach(() => {
  vi.clearAllMocks();
  state.perms = new Set();
  state.userId = 'u-owner';
  state.data = detail();
  state.search.mockResolvedValue([]);
});

describe('who sees which next action', () => {
  it('shows Submit and Edit to the owner on a draft, and neither to someone else', () => {
    state.perms = new Set(['procurement.new_product_applications.create']);
    const { unmount } = render(<Page />);
    expect(button(/^submit$/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Edit' })).toBeInTheDocument();
    expect(button(/^approve$/i)).not.toBeInTheDocument();
    unmount();

    state.userId = 'u-other';
    render(<Page />);
    expect(button(/^submit$/i)).not.toBeInTheDocument();
  });

  it('shows Approve and Reject to an approver on a submitted application, not Submit', () => {
    state.data = detail({ status: 'submitted', status_label: 'Submitted' });
    state.userId = 'u-approver';
    state.perms = new Set(['procurement.new_product_applications.approve']);
    render(<Page />);
    expect(button(/^approve$/i)).toBeInTheDocument();
    expect(button(/^reject$/i)).toBeInTheDocument();
    expect(button(/^submit$/i)).not.toBeInTheDocument();
    expect(
      screen.getByText(
        /Locked: Revise to change it; it goes back to Draft and needs approval again\./,
      ),
    ).toBeInTheDocument();
  });

  it('asks for a reason before Reject goes through', async () => {
    state.data = detail({ status: 'submitted', status_label: 'Submitted' });
    state.userId = 'u-approver';
    state.perms = new Set(['procurement.new_product_applications.approve']);
    render(<Page />);
    fireEvent.click(screen.getByRole('button', { name: /^reject$/i }));
    const confirm = await screen.findByRole('button', {
      name: /^reject$/i,
      hidden: false,
    });
    expect(screen.getByLabelText(/reason/i)).toBeInTheDocument();
    expect(confirm).toBeDisabled();
  });

  it('offers Revise on an approved application with no resolved line, and hides it once one is', () => {
    state.data = detail({ status: 'approved', status_label: 'Approved' });
    state.perms = new Set(['procurement.new_product_applications.create']);
    const { unmount } = render(<Page />);
    expect(button(/^revise$/i)).toBeInTheDocument();
    unmount();

    state.data = detail({
      status: 'approved',
      status_label: 'Approved',
      lines_done: 1,
      lines: [
        line({
          opened_product_id: 'p-1',
          opened_product_code: 'XY-1234-MB',
          opened_product_name: 'Example kitchen sink',
          opened_product_active: true,
        }),
        line({ id: 'l-2', line_no: 2, proposed_code: 'XY-1234-BG' }),
      ],
    });
    render(<Page />);
    expect(button(/^revise$/i)).not.toBeInTheDocument();
  });
});

describe('a portal-sourced application on the staff side', () => {
  it('hides Edit on a rejected draft the owner could otherwise change', () => {
    state.data = detail({
      status: 'rejected',
      status_label: 'Rejected',
      source: 'portal',
    });
    state.perms = new Set(['procurement.new_product_applications.create']);
    render(<Page />);
    expect(
      screen.queryByRole('link', { name: 'Edit' }),
    ).not.toBeInTheDocument();
    expect(button(/^submit$/i)).toBeInTheDocument();
  });
});

describe('Link product', () => {
  beforeEach(() => {
    state.data = detail({ status: 'approved', status_label: 'Approved' });
    state.userId = 'u-buyer';
    state.perms = new Set(['procurement.new_product_applications.open_code']);
  });

  it('says the code is not in the product list yet, and picks nothing', async () => {
    render(<Page />);
    fireEvent.click(
      screen.getAllByRole('button', { name: /^link product$/i })[1],
    );
    const box = await screen.findByRole('combobox', {
      name: /product for line 2/i,
    });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: 'srtks2406' } });

    expect(
      await screen.findByText(
        'srtks2406 is not in the product list yet. It appears after AutoCount sends it.',
      ),
    ).toBeInTheDocument();
    expect(state.link).not.toHaveBeenCalled();
  });

  it('lists a match without choosing it; only a click links the product', async () => {
    state.search.mockResolvedValue([
      {
        id: 'p-9',
        product_code: 'XY-1234-BG',
        product_name: 'Example steel sink',
        brand_name: null,
      },
    ]);
    render(<Page />);
    fireEvent.click(
      screen.getAllByRole('button', { name: /^link product$/i })[1],
    );
    const box = await screen.findByRole('combobox', {
      name: /product for line 2/i,
    });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: 'srtks2406' } });

    const hit = await screen.findByText(/XY-1234-BG - Example steel sink/);
    expect(state.link).not.toHaveBeenCalled();

    fireEvent.mouseDown(hit);
    fireEvent.click(hit);
    await waitFor(() =>
      expect(state.link).toHaveBeenCalledWith({
        id: 'a-1',
        lineId: 'l-2',
        productId: 'p-9',
      }),
    );
  });
});
