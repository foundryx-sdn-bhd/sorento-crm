/**
 * SR-CORE portal form, rebuilt as the portal Purchase Request / Sponsorship Form layout (mock v4
 * frames 2-3; AC-SR-02, 03, 11, 12, 80, 81). Red-first: written before the rebuild.
 * Component: ./SampleRequestForm.tsx (default export, props { slug, requestId? }).
 *
 * UI hooks this file fixes for the coder (the mock names no markup):
 *  - the document card carries the visible title text "Consignment Requisition Form";
 *  - header labels (prefix match, may carry " *"): Customer Name, Project Title, Registered
 *    project, For, Request Delivery Date, Ship To, then Shipping Address and Contact Person
 *    (either order: the mock puts Contact Person beside Ship To), then Contact Phone / Note.
 *    Each control is reachable with getByLabelText(<label>) (htmlFor/id or aria-label);
 *  - Customer Name: a free-text <input> over the contact's debtors (AsyncCombobox + lookupDebtors);
 *    typing a name that matches no debtor and leaving the box keeps it as customer_name;
 *  - an "Items" card: header cells "#", "Item code", "Quantity", "Remark"; outline button
 *    "Add item" appends a row; each PRODUCT row is one element with data-testid="sample-request-line";
 *    inside it the product search control has role="combobox" and the accessible name "Item code"
 *    (an <input> or a SearchableSelect trigger);
 *  - row 1 is NOT a product row: a checkbox named "Mount & label sample board" outside any
 *    data-testid="sample-request-line" element; once ticked, a textbox named "Board label" shows;
 *  - footer: radios "Deliver-Van", "Self-Deliver", "Self-Collection"; labels "Loan Period",
 *    "Goods Taken From" (options "Demo Sets", "New Goods"), "Purpose"; buttons "Cancel",
 *    "Save as draft", "Submit sample request";
 *  - a date refusal is a sentence (not the <label>) containing "Request Delivery Date"; the
 *    delivery mode one reads "Choose a delivery mode".
 * Whether a blank product row exists before "Add item" is left open: the tests add one if none.
 *
 * Service module: app/(auth)/portal/lib/sample-request-service.ts (beside the price tag one).
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock('../lib/sample-request-service', () => ({
  searchSampleRequestProducts: vi.fn(async () => []),
  lookupSampleRequestCustomers: vi.fn(async () => []),
  createSampleRequest: vi.fn(),
  updateSampleRequest: vi.fn(),
  submitSampleRequest: vi.fn(),
  getSampleRequest: vi.fn(),
}));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/portal-client')>();
  return {
    ...actual,
    lookupDebtors: vi.fn(async () => []),
    lookupProjects: vi.fn(async () => []),
    fetchSubmissionNeighbours: vi.fn(async () => ({ prev: null, next: null })),
  };
});

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

vi.mock('@/components/common/DetailActionsMenu', () => ({
  __esModule: true,
  DetailActionsMenu: () => null,
}));

import {
  createSampleRequest,
  updateSampleRequest,
  getSampleRequest,
  lookupSampleRequestCustomers,
  searchSampleRequestProducts,
  submitSampleRequest,
} from '../lib/sample-request-service';
import { lookupDebtors } from '../lib/portal-client';
import { typeBadInput } from '@/test-utils/badInput';
import SampleRequestForm from './SampleRequestForm';

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;
const TPE = { id: 'p-1', product_code: 'TPE-9101', product_name: 'ZZT Tape', brand_name: 'ZZT' };
const TITLE = 'Consignment Requisition Form';

function renderForm(props: { requestId?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SampleRequestForm slug="zzt-space" {...props} />
    </QueryClientProvider>,
  );
}

const rows = () =>
  Array.from(document.querySelectorAll<HTMLElement>('[data-testid="sample-request-line"]'));
const clickAddItem = () => fireEvent.click(screen.getByRole('button', { name: /^\+?\s*add item$/i }));
const ensureRow = (): HTMLElement => {
  if (rows().length === 0) clickAddItem();
  return rows()[0];
};
const labelNode = (re: RegExp): HTMLElement => screen.getAllByText(re)[0];
const follows = (a: HTMLElement, b: HTMLElement) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

/** Types into a row's product search, whether it is a text input or a select trigger. */
function searchProduct(row: HTMLElement, text: string) {
  const box = within(row).getByRole('combobox', { name: /item code/i });
  if (box.tagName === 'INPUT') {
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: text } });
  } else {
    fireEvent.click(box);
    fireEvent.change(screen.getByPlaceholderText('Search...'), { target: { value: text } });
  }
  return box;
}

const shows = (row: HTMLElement, text: string) =>
  row.textContent?.includes(text) ||
  Array.from(row.querySelectorAll('input')).some((i) => (i as HTMLInputElement).value.includes(text));

const fold = (q: string) => q.toLowerCase().replace(/[^a-z0-9]/g, '');

beforeEach(() => {
  vi.clearAllMocks();
  asMock(searchSampleRequestProducts).mockImplementation(async (q: string) =>
    fold(q) === 'tpe9101' ? [TPE] : [],
  );
  asMock(lookupSampleRequestCustomers).mockResolvedValue([]);
  asMock(createSampleRequest).mockResolvedValue({ id: 'sr-new', lines: [] });
  asMock(submitSampleRequest).mockResolvedValue({ id: 'sr-new' });
});

describe('AC-SR-80 the document card', () => {
  it('shows the paper title in a card, not collapsible sections', async () => {
    renderForm();
    expect(await screen.findByText(TITLE)).toBeTruthy();
    expect(screen.queryByText('Customer & project')).toBeNull();
    expect(screen.queryByText('Additional Information')).toBeNull();
  });

  it('prints the header labels in the paper order', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const chain = [
      /^Customer Name/,
      /^Project Title/,
      /^Registered project/,
      /^For\b/,
      /^Request Delivery Date/,
      /^Ship To/,
    ].map(labelNode);
    for (let i = 0; i < chain.length - 1; i += 1) {
      expect(follows(chain[i], chain[i + 1]), `header label ${i} before ${i + 1}`).toBe(true);
    }
    const shipTo = chain[chain.length - 1];
    const address = labelNode(/^Shipping Address/);
    const person = labelNode(/^Contact Person/);
    const phone = labelNode(/^Contact Phone/);
    expect(follows(shipTo, address)).toBe(true);
    expect(follows(shipTo, person)).toBe(true);
    expect(follows(address, phone)).toBe(true);
    expect(follows(person, phone)).toBe(true);
  });

  it('has no Quotation, Or (alternative) or Immediate arrangement anywhere', async () => {
    renderForm();
    await screen.findByText(TITLE);
    clickAddItem();
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/quotation/i);
    expect(text).not.toMatch(/or \(alternative\)/i);
    expect(text).not.toMatch(/immediate/i);
    expect(screen.queryByLabelText(/alternative/i)).toBeNull();
  });

  it('has the footer fields and the three buttons', async () => {
    renderForm();
    await screen.findByText(TITLE);
    for (const name of ['Deliver-Van', 'Self-Deliver', 'Self-Collection']) {
      expect(screen.getByRole('radio', { name })).toBeTruthy();
    }
    expect(labelNode(/^Loan Period/)).toBeTruthy();
    expect(labelNode(/^Goods Taken From/)).toBeTruthy();
    expect(labelNode(/^Demo Sets$/)).toBeTruthy();
    expect(labelNode(/^New Goods$/)).toBeTruthy();
    expect(labelNode(/^Purpose/)).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /^cancel$/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /^save as draft$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^submit sample request$/i })).toBeTruthy();
  });
});

describe('AC-SR-80 the Items table', () => {
  it('has the Item code, Quantity and Remark headers and Add item appends a product row', async () => {
    renderForm();
    await screen.findByText(TITLE);
    expect(labelNode(/^Items$/)).toBeTruthy();
    clickAddItem();
    expect(screen.getAllByText(/^Item code/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^Quantity$/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^Remark$/).length).toBeGreaterThan(0);
    const before = rows().length;
    expect(before).toBeGreaterThan(0);
    clickAddItem();
    expect(rows().length).toBe(before + 1);
  });

  it('keeps the board in row 1 as a checkbox with a label, never as a product row', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const board = screen.getByRole('checkbox', { name: /mount & label sample board/i });
    expect(board.closest('[data-testid="sample-request-line"]')).toBeNull();
    const before = rows().length;
    fireEvent.click(board);
    expect(await screen.findByRole('textbox', { name: /board label/i })).toBeTruthy();
    expect(rows().length).toBe(before);
  });
});

describe('AC-SR-11 product search lists and never picks', () => {
  it('"tpe9101" lists TPE-9101 and leaves the row empty until it is clicked', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const row = ensureRow();
    searchProduct(row, 'tpe9101');

    const option = await screen.findByRole('option', { name: /TPE-9101/ });
    expect(searchSampleRequestProducts).toHaveBeenCalledWith('tpe9101');
    expect(shows(row, 'TPE-9101')).toBeFalsy();

    fireEvent.click(option);
    await waitFor(() => expect(shows(row, 'TPE-9101')).toBeTruthy());
  });
});

describe('AC-SR-12 product not in the list', () => {
  it('shows the card sentence and adds nothing', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const row = ensureRow();
    const before = rows().length;
    searchProduct(row, 'B2155-BLUE-NL');

    expect(
      await screen.findByText(
        'B2155-BLUE-NL is not in the product list. Pick the product you mean, or ask purchasing to add it as a new product before you request it.',
      ),
    ).toBeTruthy();
    expect(searchSampleRequestProducts).toHaveBeenCalledWith('B2155-BLUE-NL');
    expect(rows().length).toBe(before);
    expect(shows(row, 'TPE-9101')).toBeFalsy();
  });
});

describe('AC-SR-02 / AC-SR-03 submit names the missing field', () => {
  it('without a Request Delivery Date shows an error naming it and does not save', async () => {
    renderForm();
    await screen.findByText(TITLE);
    fireEvent.click(screen.getByRole('button', { name: /^submit sample request$/i }));

    const messages = await screen.findAllByText(
      (content, el) =>
        el?.tagName !== 'LABEL' &&
        /request delivery date/i.test(content) &&
        content.trim().length > 'Request Delivery Date'.length + 2,
    );
    expect(messages.length).toBeGreaterThan(0);
    expect(createSampleRequest).not.toHaveBeenCalled();
    expect(submitSampleRequest).not.toHaveBeenCalled();
  });

  it('without a delivery mode shows "Choose a delivery mode"', async () => {
    renderForm();
    await screen.findByText(TITLE);
    fireEvent.click(screen.getByRole('button', { name: /^submit sample request$/i }));
    expect(await screen.findByText(/choose a delivery mode/i)).toBeTruthy();
    expect(createSampleRequest).not.toHaveBeenCalled();
    expect(submitSampleRequest).not.toHaveBeenCalled();
  });
});

describe('AC-SR-81 Customer Name is a searchable box that accepts free text', () => {
  it('keeps a typed name that matches no debtor, and saves it as customer_name', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const customer = screen.getByLabelText(/^Customer Name/) as HTMLInputElement;
    fireEvent.focus(customer);
    fireEvent.change(customer, { target: { value: 'Walk-in Trading' } });
    await waitFor(() => expect(lookupSampleRequestCustomers).toHaveBeenCalled());
    fireEvent.blur(customer);
    await new Promise((r) => setTimeout(r, 250));
    expect(customer.value).toBe('Walk-in Trading');

    fireEvent.click(screen.getByRole('button', { name: /^save as draft$/i }));
    await waitFor(() => expect(createSampleRequest).toHaveBeenCalled());
    expect(asMock(createSampleRequest).mock.calls[0][0]).toMatchObject({
      customer_name: 'Walk-in Trading',
      customer_id: null,
    });
  });

  it("lists the contact's debtors with their code and links the pick", async () => {
    asMock(lookupSampleRequestCustomers).mockResolvedValue([
      {
        customer_id: 'c-1',
        debtor_name: 'ZZT PERKASA SDN BHD',
        customer_code: 'ZZT-C001',
      },
    ]);
    renderForm();
    await screen.findByText(TITLE);
    const customer = screen.getByLabelText(/^Customer Name/);
    fireEvent.focus(customer);
    fireEvent.change(customer, { target: { value: 'perkasa' } });
    expect(await screen.findByText('ZZT-C001')).toBeTruthy();
    fireEvent.click(await screen.findByRole('option', { name: /ZZT PERKASA SDN BHD/ }));

    fireEvent.click(screen.getByRole('button', { name: /^save as draft$/i }));
    await waitFor(() => expect(createSampleRequest).toHaveBeenCalled());
    expect(asMock(createSampleRequest).mock.calls[0][0]).toMatchObject({ customer_id: 'c-1' });
  });

  it('asks the sample request lookup (the set submit accepts), never the unscoped debtor list', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const customer = screen.getByLabelText(/^Customer Name/);
    fireEvent.focus(customer);
    fireEvent.change(customer, { target: { value: 'zzt' } });
    await waitFor(() => expect(lookupSampleRequestCustomers).toHaveBeenCalledWith('zzt'));
    expect(lookupDebtors).not.toHaveBeenCalled();
  });
});

describe('AC-SR-03 / AC-SR-80 the write payload', () => {
  it('sends the plain delivery date and none of the removed keys', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const customer = screen.getByLabelText(/^Customer Name/);
    fireEvent.focus(customer);
    fireEvent.change(customer, { target: { value: 'Walk-in Trading' } });
    fireEvent.blur(customer);
    await new Promise((r) => setTimeout(r, 250));
    fireEvent.change(screen.getByLabelText(/^Project Title/), { target: { value: 'Tower A' } });
    fireEvent.change(screen.getByLabelText(/^Request Delivery Date/), {
      target: { value: '2026-12-01' },
    });
    fireEvent.change(screen.getByLabelText(/^Ship To/), { target: { value: 'Site store' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Deliver-Van' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /mount & label sample board/i }));
    fireEvent.change(await screen.findByRole('textbox', { name: /board label/i }), {
      target: { value: 'TOWER A' },
    });

    fireEvent.click(screen.getByRole('button', { name: /^submit sample request$/i }));
    await waitFor(() => expect(submitSampleRequest).toHaveBeenCalledWith('sr-new'));

    const body = asMock(createSampleRequest).mock.calls[0][0];
    expect(body).toMatchObject({
      delivery_date: '2026-12-01',
      delivery_mode: 'driver',
      board_mounting: true,
      board_label: 'TOWER A',
    });
    expect(body).not.toHaveProperty('quotation_version_id');
    expect(body).not.toHaveProperty('deliver_immediately');
    for (const line of body.lines ?? []) expect(line).not.toHaveProperty('alt_product_id');
  });
});

describe('read-only view of an existing request', () => {
  it('shows the request number and status for a submitted request', async () => {
    asMock(getSampleRequest).mockResolvedValue({
      id: 'sr-1',
      request_no: 'SR-000007',
      status: 'submitted',
      status_label: 'Submitted',
      lines: [],
      board_mounting: false,
    });
    renderForm({ requestId: 'sr-1' });
    expect(await screen.findByText('SR-000007')).toBeTruthy();
    expect(screen.getByText('Submitted')).toBeTruthy();
    expect(getSampleRequest).toHaveBeenCalledWith('sr-1');
  });

  it('prints the created date the way staff screens and the PDF do (dd/mm/yyyy, Malaysia day)', async () => {
    // A naive UTC stamp from the API: 15 Oct 23:30 UTC is already 16 Oct in Malaysia.
    asMock(getSampleRequest).mockResolvedValue({
      id: 'sr-2',
      request_no: 'SR-000008',
      status: 'submitted',
      status_label: 'Submitted',
      created_at: '2026-10-15T23:30:00',
      lines: [],
      board_mounting: false,
    });
    renderForm({ requestId: 'sr-2' });
    expect(await screen.findByText('Created 16/10/2026')).toBeTruthy();
  });
});

describe('number fields: a blank line Quantity is refused, never defaulted to 1', () => {
  it('shows the sentence under Quantity, sends nothing, and clears it once 3 is typed', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const customer = screen.getByLabelText(/^Customer Name/);
    fireEvent.focus(customer);
    fireEvent.change(customer, { target: { value: 'Walk-in Trading' } });
    fireEvent.blur(customer);
    await new Promise((r) => setTimeout(r, 250));
    fireEvent.change(screen.getByLabelText(/^Project Title/), { target: { value: 'Tower A' } });
    fireEvent.change(screen.getByLabelText(/^Request Delivery Date/), { target: { value: '2026-12-01' } });
    fireEvent.change(screen.getByLabelText(/^Ship To/), { target: { value: 'Site store' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Deliver-Van' }));
    const row = ensureRow();
    searchProduct(row, 'tpe9101');
    fireEvent.click(await screen.findByRole('option', { name: /TPE-9101/ }));

    const qty = within(row).getByLabelText('Quantity') as HTMLInputElement;
    fireEvent.change(qty, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^submit sample request$/i }));

    expect(await screen.findByText('Enter a whole number of at least 1.')).toBeTruthy();
    expect(createSampleRequest).not.toHaveBeenCalled();
    expect(submitSampleRequest).not.toHaveBeenCalled();

    fireEvent.change(qty, { target: { value: '3' } });
    await waitFor(() => expect(screen.queryByText('Enter a whole number of at least 1.')).toBeNull());
  });
});

describe('number fields round 9: Save as draft refuses a bad quantity or loan period like Submit', () => {
  const SENTENCE = 'Enter a whole number of at least 1.';
  async function pickProduct() {
    const row = ensureRow();
    searchProduct(row, 'tpe9101');
    fireEvent.click(await screen.findByRole('option', { name: /TPE-9101/ }));
    return row;
  }
  const draft = () => fireEvent.click(screen.getByRole('button', { name: /^save as draft$/i }));
  const nothingSent = () => {
    expect(createSampleRequest).not.toHaveBeenCalled();
    expect(updateSampleRequest).not.toHaveBeenCalled();
  };

  it('a blank Quantity sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const row = await pickProduct();
    fireEvent.change(within(row).getByLabelText('Quantity'), { target: { value: '' } });
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('a Quantity of 0 sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const row = await pickProduct();
    fireEvent.change(within(row).getByLabelText('Quantity'), { target: { value: '0' } });
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('a bad-input Quantity sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText(TITLE);
    const row = await pickProduct();
    typeBadInput(within(row).getByLabelText('Quantity'));
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('a blank Loan Period sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText(TITLE);
    await pickProduct();
    fireEvent.change(screen.getByLabelText('Loan Period'), { target: { value: '' } });
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('a bad-input Loan Period sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText(TITLE);
    await pickProduct();
    typeBadInput(screen.getByLabelText('Loan Period'));
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('S1 guard: a refused Submit with a blank Loan Period shows the sentence under the box', async () => {
    renderForm();
    await screen.findByText(TITLE);
    fireEvent.change(screen.getByLabelText('Loan Period'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^submit sample request$/i }));
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });
});
