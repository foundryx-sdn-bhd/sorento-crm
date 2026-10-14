/**
 * RED tests - FORM-ROUTING-CONDITIONS PC-5 (portal "Customer name" picker).
 *
 * Picking a customer sets `customer_name` AND `customer_id` in the saved payload; typing free
 * text leaves `customer_id` empty; on a PR a non-null `sales_type` on the picked item fills
 * Sales type, a null one leaves it as it was; a sponsorship form has no Sales type at all.
 *
 * Mocking pattern: SubmissionForm.sponsorshipUnitPrice.test.tsx (portal-client mocked, the
 * form drives `saveDraft`).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import type { PortalContact } from '../lib/portal-client';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const toasts = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), info: vi.fn() }));
vi.mock('@/lib/toast', () => ({ toast: toasts }));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../lib/portal-client')>();
  return {
    ...original,
    fetchMe: vi.fn(),
    fetchSubmission: vi.fn(),
    fetchSubmissionNeighbours: vi.fn(),
    fetchRevisions: vi.fn(),
    reviseSubmission: vi.fn(),
    saveDraft: vi.fn(),
    submitDraft: vi.fn(),
    deleteDraftSubmission: vi.fn(),
    uploadAttachment: vi.fn(),
    deleteAttachment: vi.fn(),
    lookupDebtors: vi.fn(),
    lookupSet: vi.fn().mockResolvedValue({ options: [], defaultValue: null }),
  };
});

import {
  fetchMe,
  fetchSubmissionNeighbours,
  lookupDebtors,
  saveDraft,
} from '../lib/portal-client';
import { SubmissionForm } from './SubmissionForm';

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const CONTACT: PortalContact = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'SALESMAN A',
  phone_number: '60123456789',
  expires_at: '2026-08-01T00:00:00Z',
};

const PROJECT_CASH = {
  customer_id: 'cust-302',
  debtor_name: 'CUSTOMER A SDN BHD',
  customer_code: '302-ZZT001',
  debtor_type: 'project_cash',
  sales_type: 'cash_sales',
};
const PROJECT = {
  customer_id: 'cust-300',
  debtor_name: 'CUSTOMER B SDN BHD',
  customer_code: '300-ZZT001',
  debtor_type: 'project',
  sales_type: 'project',
};
const WALKIN = {
  customer_id: 'cust-301',
  debtor_name: 'CASH (PERSON A)',
  customer_code: '301-ZZTC01',
  debtor_type: null,
  sales_type: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  asMock(fetchMe).mockResolvedValue(CONTACT);
  asMock(fetchSubmissionNeighbours).mockResolvedValue({
    prev_id: null,
    next_id: null,
    position: 1,
    total: 1,
  });
  asMock(saveDraft).mockResolvedValue({ id: 'new-1' });
  asMock(lookupDebtors).mockResolvedValue([PROJECT_CASH, PROJECT, WALKIN]);
});

async function renderForm(kind: 'purchase_request' | 'sponsorship_form') {
  const view = render(<SubmissionForm kind={kind} />);
  await screen.findByText(CONTACT.name as string);
  return view;
}

const customerInput = (container: HTMLElement) =>
  container.querySelector('#customer_name') as HTMLInputElement | HTMLTextAreaElement;

async function pick(container: HTMLElement, debtorName: string) {
  fireEvent.focus(customerInput(container));
  const option = await screen.findByRole('option', { name: new RegExp(debtorName.replace(/[()]/g, '\\$&')) });
  fireEvent.click(option);
}

/** Type a name that is not a pick and commit it: Escape closes the list, Enter then takes the
 *  typed text as free text (AsyncCombobox), with no deferred blur timer to race the form. */
function typeFreeText(input: HTMLElement, text: string) {
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: 'Escape' });
  fireEvent.keyDown(input, { key: 'Enter' });
}

async function savedFields(): Promise<Record<string, unknown>> {
  fireEvent.click(screen.getAllByRole('button', { name: 'Save as draft' })[0]);
  await waitFor(() => expect(saveDraft).toHaveBeenCalled());
  const calls = asMock(saveDraft).mock.calls;
  return calls[calls.length - 1][1] as Record<string, unknown>;
}

describe('SubmissionForm customer picker (PC-5)', () => {
  it.each(['purchase_request', 'sponsorship_form'] as const)(
    'picking a customer sends customer_name and customer_id (%s)',
    async (kind) => {
      const { container } = await renderForm(kind);

      await pick(container, PROJECT.debtor_name);
      const fields = await savedFields();

      expect(fields.customer_name).toBe(PROJECT.debtor_name);
      expect(fields.customer_id).toBe(PROJECT.customer_id);
    },
  );

  it('typing free text after a pick clears customer_id and keeps the typed name', async () => {
    const { container } = await renderForm('purchase_request');
    await pick(container, PROJECT.debtor_name);
    expect((await savedFields()).customer_id).toBe(PROJECT.customer_id); // linked first

    const input = customerInput(container);
    typeFreeText(input, 'WALK-IN BUYER');
    const fields = await savedFields();

    expect(fields.customer_name).toBe('WALK-IN BUYER');
    expect([null, undefined, '']).toContain(fields.customer_id);
  });

  it('free text with no pick at all sends no customer_id', async () => {
    const { container } = await renderForm('purchase_request');
    const input = customerInput(container);
    typeFreeText(input, 'WALK-IN BUYER');
    const fields = await savedFields();

    expect(fields.customer_name).toBe('WALK-IN BUYER');
    expect([null, undefined, '']).toContain(fields.customer_id);
  });

  it('on a PR a customer with sales_type cash_sales sets Sales type to cash_sales', async () => {
    const { container } = await renderForm('purchase_request');

    await pick(container, PROJECT_CASH.debtor_name);
    const fields = await savedFields();

    expect(fields.sales_type).toBe('cash_sales');
  });

  it('on a PR a customer with a null sales_type leaves Sales type as it was', async () => {
    const { container } = await renderForm('purchase_request');
    await pick(container, PROJECT.debtor_name); // sets project
    await pick(container, WALKIN.debtor_name); // null sales_type: must not blank or change it
    const fields = await savedFields();

    expect(fields.customer_id).toBe(WALKIN.customer_id);
    expect(fields.sales_type).toBe('project');
  });

  it('a sponsorship form has no Sales type, so a pick never writes one', async () => {
    const { container } = await renderForm('sponsorship_form');

    await pick(container, PROJECT_CASH.debtor_name);
    const fields = await savedFields();

    expect(fields.customer_id).toBe(PROJECT_CASH.customer_id);
    expect(fields.sales_type ?? null).toBeNull();
  });

  it('shows the customer code as meta next to the name in the dropdown', async () => {
    const { container } = await renderForm('purchase_request');
    fireEvent.focus(customerInput(container));

    const option = await screen.findByRole('option', { name: /CUSTOMER A SDN BHD/ });
    expect(option).toHaveTextContent('302-ZZT001');
  });
});
