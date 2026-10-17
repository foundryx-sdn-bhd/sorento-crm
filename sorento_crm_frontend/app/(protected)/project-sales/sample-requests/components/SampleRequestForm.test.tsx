/**
 * SR-CORE staff New / Edit form, rebuilt as the Purchase Request / Sponsorship Form document
 * card (mock v4 frame 1; AC-SR-02, 03, 11, 12, 80, 81). Red-first: written before the rebuild.
 * Component: ./SampleRequestForm.tsx (default export, props { requestId?, projectId? }).
 *
 * UI hooks this file fixes for the coder (the mock names no markup):
 *  - the document card carries the visible title text "Consignment Requisition Form";
 *  - every header field has a visible label with the text asserted below, and the control is
 *    reachable with getByLabelText(<label>) (use htmlFor/id or aria-label). Labels may carry a
 *    trailing " *" or "(optional)"; the matchers are prefix matches;
 *  - Customer Name: a searchable box that is a free-text <input> (AsyncCombobox +
 *    lookupCustomersForRequest, as RequestCustomerField); typing text that matches no debtor and
 *    leaving the box keeps it as customer_name with customer_id null;
 *  - the line table: header cells "No.", "Item Code", "Qty", "Remark"; the outline button
 *    "Add row" appends one row; each PRODUCT row is exactly one element with
 *    data-testid="sample-request-line"; inside it the product search control has role="combobox"
 *    and the accessible name "Item code" (an <input> or a SearchableSelect trigger; both work
 *    with the helper below); Qty = aria-label "Quantity", Remark = aria-label "Remark";
 *  - row 1 is NOT a product row: a checkbox named "Mount & label sample board" outside any
 *    data-testid="sample-request-line" element; once ticked, a textbox named "Board label" shows;
 *  - footer: radios named "Deliver-Van", "Self-Deliver", "Self-Collection"; labels "Loan Period",
 *    "Goods Taken From" (options "Demo Sets", "New Goods"), "Purpose"; buttons "Cancel",
 *    "Save as draft", "Submit";
 *  - a submit refusal for the date is a sentence (not the <label>) that contains
 *    "Request Delivery Date"; the delivery mode one reads "Choose a delivery mode".
 * Whether a blank product row exists before "Add row" is left open: the tests add one if none.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

const mut = vi.hoisted(() => ({
  createAsync: vi.fn(),
  updateAsync: vi.fn(),
  submitAsync: vi.fn(),
}));

vi.mock('../hooks/useSampleRequests', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../hooks/useSampleRequests')>();
  return {
    ...actual,
    useSampleRequestMutations: () => ({
      create: { mutateAsync: mut.createAsync, isPending: false },
      update: { mutateAsync: mut.updateAsync, isPending: false },
      submit: { mutateAsync: mut.submitAsync, isPending: false },
    }),
    useSampleRequestQuery: () => ({ data: undefined, isLoading: false }),
    useSampleRequestSwitches: () => ({ data: { requireProject: false } }),
  };
});

vi.mock('../services/sampleRequestService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/sampleRequestService')>();
  return { ...actual, searchSampleRequestProducts: vi.fn() };
});

vi.mock('../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../_shared/services/projectService')>();
  return {
    ...actual,
    getProject: vi.fn(async () => ({})),
    listProjects: vi.fn(async () => ({ data: [] })),
    listQuotations: vi.fn(async () => []),
  };
});

vi.mock(
  '@/app/(protected)/procurement-management/purchase-requests/services/customerLookupService',
  () => ({ lookupCustomersForRequest: vi.fn(async () => []) }),
);

import { searchSampleRequestProducts } from '../services/sampleRequestService';
import { lookupCustomersForRequest } from '@/app/(protected)/procurement-management/purchase-requests/services/customerLookupService';
import { typeBadInput } from '@/test-utils/badInput';
import SampleRequestForm from './SampleRequestForm';

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;
const TPE = { id: 'p-1', product_code: 'TPE-9101', product_name: 'ZZT Tape', brand_name: 'ZZT' };

function renderForm(props: { requestId?: string; projectId?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SampleRequestForm {...props} />
    </QueryClientProvider>,
  );
}

const rows = () =>
  Array.from(document.querySelectorAll<HTMLElement>('[data-testid="sample-request-line"]'));
const clickAddRow = () => fireEvent.click(screen.getByRole('button', { name: /^\+?\s*add row$/i }));
const ensureRow = (): HTMLElement => {
  if (rows().length === 0) clickAddRow();
  return rows()[0];
};
const labelNode = (re: RegExp): HTMLElement => screen.getAllByText(re)[0];

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
  mut.createAsync.mockResolvedValue({ id: 'sr-new' });
  mut.updateAsync.mockResolvedValue({ id: 'sr-new' });
  mut.submitAsync.mockResolvedValue({ id: 'sr-new' });
  asMock(searchSampleRequestProducts).mockImplementation(async (q: string) =>
    fold(q) === 'tpe9101' ? [TPE] : [],
  );
  asMock(lookupCustomersForRequest).mockResolvedValue([]);
});

describe('AC-SR-80 the document card', () => {
  it('shows the paper title in a card, not collapsible sections', async () => {
    renderForm();
    expect(await screen.findByText('Consignment Requisition Form')).toBeTruthy();
    expect(screen.queryByText('Customer & project')).toBeNull();
    expect(screen.queryByText('Additional Information')).toBeNull();
  });

  it('prints the header labels in the paper order', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    const order = [
      /^Sales Person/,
      /^Date\b/,
      /^Customer Name/,
      /^Project Title/,
      /^Registered project/,
      /^For\b/,
      /^Request Delivery Date/,
      /^Ship To/,
      /^Shipping Address/,
      /^Contact Person/,
      /^Contact Phone/,
      /^Consignment No/,
      /^PO No/,
    ];
    const nodes = order.map(labelNode);
    for (let i = 0; i < nodes.length - 1; i += 1) {
      expect(
        nodes[i].compareDocumentPosition(nodes[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING,
        `${order[i]} should come before ${order[i + 1]}`,
      ).toBeTruthy();
    }
  });

  it('has no Quotation, Or (alternative) or Immediate arrangement anywhere', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    clickAddRow();
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/quotation/i);
    expect(text).not.toMatch(/or \(alternative\)/i);
    expect(text).not.toMatch(/immediate/i);
    expect(screen.queryByLabelText(/alternative/i)).toBeNull();
  });

  it('has the footer fields and the three buttons', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
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
    expect(screen.getByRole('button', { name: /^submit$/i })).toBeTruthy();
  });
});

describe('AC-SR-80 the line table', () => {
  it('has the Item Code, Qty and Remark headers and Add row appends a product row', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    clickAddRow();
    expect(screen.getAllByText(/^Item Code/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^Qty$/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^Remark$/).length).toBeGreaterThan(0);
    const before = rows().length;
    expect(before).toBeGreaterThan(0);
    clickAddRow();
    expect(rows().length).toBe(before + 1);
  });

  it('keeps the board in row 1 as a checkbox with a label, never as a product row', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
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
    await screen.findByText('Consignment Requisition Form');
    const row = ensureRow();
    searchProduct(row, 'tpe9101');

    const option = await screen.findByRole('option', { name: /TPE-9101/ });
    expect(asMock(searchSampleRequestProducts)).toHaveBeenCalledWith('tpe9101');
    expect(shows(row, 'TPE-9101')).toBeFalsy();

    fireEvent.click(option);
    await waitFor(() => expect(shows(row, 'TPE-9101')).toBeTruthy());
  });
});

describe('AC-SR-12 product not in the list', () => {
  it('shows the card sentence and adds nothing', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    const row = ensureRow();
    const before = rows().length;
    searchProduct(row, 'B2155-BLUE-NL');

    expect(
      await screen.findByText(
        'B2155-BLUE-NL is not in the product list. Pick the product you mean, or ask purchasing to add it as a new product before you request it.',
      ),
    ).toBeTruthy();
    expect(rows().length).toBe(before);
    expect(shows(row, 'TPE-9101')).toBeFalsy();
  });
});

describe('AC-SR-02 / AC-SR-03 submit names the missing field', () => {
  it('without a Request Delivery Date shows an error naming it and does not save', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    fireEvent.click(screen.getByRole('button', { name: /^submit$/i }));

    const messages = await screen.findAllByText(
      (content, el) =>
        el?.tagName !== 'LABEL' &&
        /request delivery date/i.test(content) &&
        content.trim().length > 'Request Delivery Date'.length + 2,
    );
    expect(messages.length).toBeGreaterThan(0);
    expect(mut.createAsync).not.toHaveBeenCalled();
    expect(mut.submitAsync).not.toHaveBeenCalled();
  });

  it('without a delivery mode shows "Choose a delivery mode"', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    fireEvent.click(screen.getByRole('button', { name: /^submit$/i }));
    expect(await screen.findByText(/choose a delivery mode/i)).toBeTruthy();
    expect(mut.createAsync).not.toHaveBeenCalled();
  });
});

describe('AC-SR-81 Customer Name is a searchable box that accepts free text', () => {
  it('keeps a typed name that matches no debtor, and saves it as customer_name', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    const customer = screen.getByLabelText(/^Customer Name/) as HTMLInputElement;
    fireEvent.focus(customer);
    fireEvent.change(customer, { target: { value: 'Walk-in Trading' } });
    await waitFor(() => expect(lookupCustomersForRequest).toHaveBeenCalled());
    fireEvent.blur(customer);
    await new Promise((r) => setTimeout(r, 250));
    expect(customer.value).toBe('Walk-in Trading');

    fireEvent.click(screen.getByRole('button', { name: /^save as draft$/i }));
    await waitFor(() => expect(mut.createAsync).toHaveBeenCalled());
    expect(mut.createAsync.mock.calls[0][0]).toMatchObject({
      customer_name: 'Walk-in Trading',
      customer_id: null,
    });
  });

  it('lists a debtor with its code and links the pick', async () => {
    asMock(lookupCustomersForRequest).mockResolvedValue([
      {
        customer_id: 'c-1',
        debtor_name: 'JV PERKASA SDN BHD',
        customer_code: '300-J099',
        debtor_type: null,
        sales_type: null,
      },
    ]);
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    const customer = screen.getByLabelText(/^Customer Name/);
    fireEvent.focus(customer);
    fireEvent.change(customer, { target: { value: 'perkasa' } });
    expect(await screen.findByText('300-J099')).toBeTruthy();
    fireEvent.click(await screen.findByRole('option', { name: /JV PERKASA SDN BHD/ }));

    fireEvent.click(screen.getByRole('button', { name: /^save as draft$/i }));
    await waitFor(() => expect(mut.createAsync).toHaveBeenCalled());
    expect(mut.createAsync.mock.calls[0][0]).toMatchObject({ customer_id: 'c-1' });
  });
});

describe('AC-SR-03 / AC-SR-80 the write payload', () => {
  it('sends the plain delivery date and none of the removed keys', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
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

    fireEvent.click(screen.getByRole('button', { name: /^submit$/i }));
    await waitFor(() => expect(mut.submitAsync).toHaveBeenCalledWith('sr-new'));

    const body = mut.createAsync.mock.calls[0][0];
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
    expect(mut.createAsync).not.toHaveBeenCalled();
    expect(mut.updateAsync).not.toHaveBeenCalled();
  };

  it('a blank Quantity sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    const row = await pickProduct();
    fireEvent.change(within(row).getByLabelText('Quantity'), { target: { value: '' } });
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('a Quantity of 0 sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    const row = await pickProduct();
    fireEvent.change(within(row).getByLabelText('Quantity'), { target: { value: '0' } });
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('a bad-input Quantity sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    const row = await pickProduct();
    typeBadInput(within(row).getByLabelText('Quantity'));
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('a blank Loan Period sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    await pickProduct();
    fireEvent.change(screen.getByLabelText('Loan Period'), { target: { value: '' } });
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('a bad-input Loan Period sends nothing and shows the sentence', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    await pickProduct();
    typeBadInput(screen.getByLabelText('Loan Period'));
    draft();
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });

  it('S1 guard: a refused Submit with a blank Loan Period shows the sentence under the box', async () => {
    renderForm();
    await screen.findByText('Consignment Requisition Form');
    fireEvent.change(screen.getByLabelText('Loan Period'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^submit$/i }));
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    nothingSent();
  });
});
