/**
 * Portal new product application form (NEW-PRODUCT-APP): start from the contact's own responded
 * inquiry, the line table without supplier or cost, Revise with a required reason, and the
 * read-only view once the application is with purchasing. The service module is mocked.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type {
  NewProductApplicationDetail,
  NewProductApplicationLine,
} from '@/types/newProductApplication';

const push = vi.fn();
const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace, back: vi.fn() }),
}));
vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));
vi.mock('@/components/common/DetailActionsMenu', () => ({
  __esModule: true,
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

vi.mock('../lib/new-product-application-service', () => ({
  listMyRespondedInquiries: vi.fn(),
  getNewProductApplicationPrefill: vi.fn(),
  lookupNewProductApplication: vi.fn(async () => []),
  lookupNewProductApplicationCustomers: vi.fn(async () => []),
  createNewProductApplication: vi.fn(),
  updateNewProductApplication: vi.fn(),
  submitNewProductApplication: vi.fn(),
  reviseNewProductApplication: vi.fn(),
  cancelNewProductApplication: vi.fn(),
  getNewProductApplication: vi.fn(),
}));

import {
  createNewProductApplication,
  getNewProductApplication,
  getNewProductApplicationPrefill,
  listMyRespondedInquiries,
  reviseNewProductApplication,
  submitNewProductApplication,
} from '../lib/new-product-application-service';
import NewProductApplicationForm from './NewProductApplicationForm';

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const INQUIRY = {
  id: 'si-1',
  inquiry_number: 'SI26-0412',
  product_code: 'XY-1234-MB & XY-1234-BG',
  item_description: 'Example sinks',
  project_name: 'EXAMPLE RESIDENCE, TOWER B',
  purchasing_response: 'Supplier can make it, please apply for a new code.',
  last_responded_at: null,
};

function line(
  over: Partial<NewProductApplicationLine> = {},
): NewProductApplicationLine {
  return {
    id: 'l-1',
    line_no: 1,
    proposed_code: 'XY-1234-MB',
    description: 'Example kitchen sink',
    category_id: 'g-1',
    category_code: 'KS',
    category_name: 'Kitchen Sink',
    base_uom_id: 'u-1',
    base_uom_code: 'UNIT',
    base_uom_name: 'Unit',
    brand_name: null,
    item_type_name: null,
    supplier_code: null,
    supplier_name: null,
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

function detail(
  over: Partial<NewProductApplicationDetail> = {},
): NewProductApplicationDetail {
  return {
    id: 'a-1',
    application_no: 'NPA-000001',
    status: 'draft',
    status_label: 'Draft',
    source: 'portal',
    applicant_id: null,
    applicant_name: null,
    stock_inquiry_id: null,
    stock_inquiry_number: null,
    stock_inquiry_product_code: null,
    customer_id: null,
    customer_code: null,
    customer_name: 'EXAMPLE DEVELOPER SDN BHD',
    project_name: 'EXAMPLE RESIDENCE, TOWER B',
    first_line: 'XY-1234-MB',
    lines_total: 1,
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
    lines: [line()],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  asMock(listMyRespondedInquiries).mockResolvedValue([INQUIRY]);
  asMock(createNewProductApplication).mockResolvedValue(detail());
  asMock(submitNewProductApplication).mockResolvedValue(
    detail({ status: 'submitted' }),
  );
});

describe('new application', () => {
  it('starts from my stock inquiry: line 1 gets the code text, the customer stays typed text', async () => {
    asMock(getNewProductApplicationPrefill).mockResolvedValue({
      stock_inquiry_id: 'si-1',
      stock_inquiry_number: 'SI26-0412',
      customer_id: null,
      customer_name: 'EXAMPLE DEVELOPER SDN BHD',
      project_name: 'EXAMPLE RESIDENCE, TOWER B',
      needed_by: null,
      remark: null,
      lines: [
        {
          proposed_code: 'XY-1234-MB & XY-1234-BG',
          description: 'Example sinks',
        },
      ],
    });
    render(<NewProductApplicationForm slug="acme" />);

    const picker = await screen.findByRole('combobox', {
      name: /start from my stock inquiry/i,
    });
    fireEvent.focus(picker);
    const hit = await screen.findByText('SI26-0412 - XY-1234-MB & XY-1234-BG');
    fireEvent.mouseDown(hit);
    fireEvent.click(hit);

    await waitFor(() =>
      expect(
        (screen.getByLabelText('Proposed code') as HTMLInputElement).value,
      ).toBe('XY-1234-MB & XY-1234-BG'),
    );
    expect(getNewProductApplicationPrefill).toHaveBeenCalledWith('si-1');
    await waitFor(() =>
      expect(
        (screen.getByLabelText('Customer') as HTMLInputElement).value,
      ).toBe('EXAMPLE DEVELOPER SDN BHD'),
    );

    fireEvent.click(screen.getByRole('button', { name: /save as draft/i }));
    await waitFor(() =>
      expect(createNewProductApplication).toHaveBeenCalledTimes(1),
    );
    const body = asMock(createNewProductApplication).mock.calls[0][0];
    expect(body.customer_id).toBeNull();
    expect(body.stock_inquiry_id).toBe('si-1');
    expect(body.lines[0].proposed_code).toBe('XY-1234-MB & XY-1234-BG');
  });

  it('has no Supplier, Supplier model no or Cost price column', async () => {
    render(<NewProductApplicationForm slug="acme" />);
    await screen.findByLabelText('Proposed code');
    expect(screen.queryByText(/^supplier/i)).toBeNull();
    expect(screen.queryByText(/cost/i)).toBeNull();
    expect(screen.queryByLabelText(/supplier/i)).toBeNull();
    expect(screen.getByText(/selling/i)).toBeInTheDocument();
  });

  it('stays on the form with the server text when Submit is refused', async () => {
    asMock(submitNewProductApplication).mockRejectedValue(
      new Error('Line 1: choose an item group'),
    );
    render(<NewProductApplicationForm slug="acme" />);
    await screen.findByLabelText('Proposed code');
    fireEvent.click(screen.getByRole('button', { name: /^submit$/i }));
    expect(
      await screen.findByText('Line 1: choose an item group'),
    ).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});

describe('an existing application', () => {
  it('is read-only once submitted, with the lock line and Revise', async () => {
    asMock(getNewProductApplication).mockResolvedValue(
      detail({ status: 'submitted', status_label: 'Submitted' }),
    );
    render(<NewProductApplicationForm slug="acme" applicationId="a-1" />);

    const description = (await screen.findByLabelText(
      'Description',
    )) as HTMLInputElement;
    expect(description).toBeDisabled();
    expect(screen.queryByRole('button', { name: /save as draft/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^submit$/i })).toBeNull();
    expect(
      screen.getByText(
        /Locked: Revise to change it; it goes back to Draft and needs approval again\./,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /^revise$/i }),
    ).toBeInTheDocument();
  });

  it('needs a reason before Revise goes through', async () => {
    asMock(getNewProductApplication).mockResolvedValue(
      detail({ status: 'approved', status_label: 'Approved' }),
    );
    asMock(reviseNewProductApplication).mockResolvedValue(detail());
    render(<NewProductApplicationForm slug="acme" applicationId="a-1" />);

    fireEvent.click(await screen.findByRole('button', { name: /^revise$/i }));
    const dialogConfirm = await screen.findByRole('button', {
      name: /^revise$/i,
    });
    expect(dialogConfirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/what changed, and why/i), {
      target: { value: 'UOM is SET not UNIT' },
    });
    expect(dialogConfirm).not.toBeDisabled();
    fireEvent.click(dialogConfirm);
    await waitFor(() =>
      expect(reviseNewProductApplication).toHaveBeenCalledWith(
        'a-1',
        'UOM is SET not UNIT',
      ),
    );
  });

  it('hides Revise once a line is resolved and shows the result of each line', async () => {
    asMock(getNewProductApplication).mockResolvedValue(
      detail({
        status: 'approved',
        status_label: 'Approved',
        lines: [
          line({ opened_product_id: 'p-1', opened_product_code: 'XY-1234-MB' }),
          line({ id: 'l-2', line_no: 2, closed_reason: 'supplier dropped it' }),
        ],
      }),
    );
    render(<NewProductApplicationForm slug="acme" applicationId="a-1" />);
    expect(
      await screen.findByText('XY-1234-MB', { selector: 'span,p,a' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/not opening/i)).toBeInTheDocument();
    expect(screen.getByText('supplier dropped it')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^revise$/i })).toBeNull();
  });

  it('shows the reject reason on a rejected application and lets it be edited', async () => {
    asMock(getNewProductApplication).mockResolvedValue(
      detail({
        status: 'rejected',
        status_label: 'Rejected',
        reject_reason: 'Use the existing codes',
      }),
    );
    render(<NewProductApplicationForm slug="acme" applicationId="a-1" />);
    expect(
      await screen.findByText(/Use the existing codes/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Description')).not.toBeDisabled();
  });
});
