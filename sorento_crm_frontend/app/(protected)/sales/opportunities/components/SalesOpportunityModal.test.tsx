/**
 * The Log opportunity modal (UAC S2-12, S2-15, S2-16; plan 3.4, 3.5, section 16, N10, N11).
 *
 * ONE "Customer or prospect" search - no "Not a customer yet" switch (N10, S2-15) - and an
 * optional Products table (N11, S2-16), the complaint form's product-line pattern: a product
 * search and a quantity per row, Add product, remove, "No products yet" when empty.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { selectOption } from '@/test-utils';

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    'aria-label'?: string;
    value: string;
    onChange: (v: string) => void;
    onOptionChange?: (o: { value: string; label: string; disabled?: boolean } | null) => void;
    options?: { value: string; label: string; disabled?: boolean }[];
    fetchOptions?: (q: string, page: number) => Promise<
      { value: string; label: string; disabled?: boolean }[]
    >;
    onSearchChange?: (q: string) => void;
    selectedOption?: { value: string; label: string; disabled?: boolean };
  }) => {
    const [query, setQuery] = React.useState('');
    const [options, setOptions] = React.useState(props.options ?? []);
    React.useEffect(() => {
      if (props.fetchOptions) {
        props.fetchOptions(query, 0).then(setOptions);
      }
    }, [query]);
    // Static mode (no fetchOptions): the real SearchableSelect reads `options` straight
    // off props on every render, so a caller whose own async fetch fills it in AFTER
    // mount (ProductLineRow) sees it update live. `useState(props.options)` above only
    // seeds the FIRST render - mirror the real component's reactivity here too, or a
    // test that correctly waits for the row's own fetch to resolve waits on a value
    // this mock can never produce (LESSONS 27 - the wait would be real, the mock isn't).
    React.useEffect(() => {
      if (!props.fetchOptions) setOptions(props.options ?? []);
    }, [props.options, props.fetchOptions]);
    const label = props['aria-label'] ?? props.id ?? 'select';
    // Mirrors the real component's own fallback: a `selectedOption` whose value isn't in
    // the fetched page still shows as the current selection (reviewer should-fix 7).
    const displayOptions =
      props.selectedOption && !options.some((o) => o.value === props.selectedOption!.value)
        ? [props.selectedOption, ...options]
        : options;
    return (
      <div>
        {props.fetchOptions || props.onSearchChange ? (
          <input
            aria-label={`${label} search`}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              props.onSearchChange?.(e.target.value);
            }}
          />
        ) : null}
        <select
          aria-label={label}
          value={props.value}
          onChange={(e) => {
            props.onChange(e.target.value);
            const opt = displayOptions.find((o) => o.value === e.target.value) ?? null;
            props.onOptionChange?.(opt);
          }}
        >
          <option value="" />
          {displayOptions.map((o) => (
            <option key={o.value} value={o.value} disabled={o.disabled}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
    );
  },
}));

const service = vi.hoisted(() => ({
  getSalesOpportunityCustomerOptions: vi.fn(),
  getSalesOpportunityProductOptions: vi.fn(),
}));
vi.mock('../services/salesOpportunityService', () => service);

const save = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
vi.mock('../hooks/useSalesOpportunities', () => ({
  useSaveSalesOpportunity: () => save,
}));

import SalesOpportunityModal from './SalesOpportunityModal';

beforeEach(() => {
  save.mutateAsync.mockReset();
  save.mutateAsync.mockResolvedValue({ id: 'new' });
  service.getSalesOpportunityCustomerOptions.mockReset();
  service.getSalesOpportunityCustomerOptions.mockResolvedValue({
    items: [],
    prospect: null,
    blocked: null,
  });
  service.getSalesOpportunityProductOptions.mockReset();
  service.getSalesOpportunityProductOptions.mockResolvedValue([
    { value: 'p1', label: 'ZZT-001 - ZZT Basin' },
  ]);
});

describe('SalesOpportunityModal', () => {
  it('has one Customer or prospect field and no toggle/switch element', () => {
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    expect(screen.getByLabelText('Customer or prospect')).toBeTruthy();
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.queryByText(/not a customer yet/i)).toBeNull();
  });

  it('offers "Add ... as a new prospect" when no customer matches, and sends prospect_name', async () => {
    service.getSalesOpportunityCustomerOptions.mockResolvedValue({
      items: [],
      prospect: { name: 'Seri Indah Renovation' },
      blocked: null,
    });
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);

    fireEvent.change(screen.getByLabelText('Customer or prospect search'), {
      target: { value: 'Seri Indah Renovation' },
    });
    await waitFor(() =>
      expect(screen.getByText('Add "Seri Indah Renovation" as a new prospect')).toBeTruthy(),
    );

    fireEvent.change(screen.getByLabelText('Customer or prospect'), {
      target: { value: 'prospect:Seri Indah Renovation' },
    });
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'ZZT Opp' } });
    fireEvent.change(screen.getByLabelText('Expected amount'), { target: { value: '1000' } });
    fireEvent.change(screen.getByLabelText('Expected close date'), {
      target: { value: '2026-11-01' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    const payload = save.mutateAsync.mock.calls[0][0];
    expect(payload.prospect_name).toBe('Seri Indah Renovation');
    expect(payload.customer_id).toBeFalsy();
  });

  it('renders a blocked match as a disabled option', async () => {
    service.getSalesOpportunityCustomerOptions.mockResolvedValue({
      items: [],
      prospect: null,
      blocked: { name: 'Seri Indah', message: "Seri Indah is another agent's customer" },
    });
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByLabelText('Customer or prospect search'), {
      target: { value: 'Seri Indah' },
    });
    await waitFor(() => {
      const option = screen.getByText("Seri Indah is another agent's customer");
      expect((option as HTMLOptionElement).disabled).toBe(true);
    });
  });

  it('shows "No products yet" until a row is added, and Add product adds one', () => {
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    expect(screen.getByText(/no products yet/i)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    expect(screen.queryByText(/no products yet/i)).toBeNull();
  });

  it('removes a product row', () => {
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    const removeButtons = screen.getAllByRole('button', { name: /remove/i });
    expect(removeButtons.length).toBe(2);
    fireEvent.click(removeButtons[0]);
    expect(screen.getAllByRole('button', { name: /remove/i }).length).toBe(1);
  });

  it('sends the product lines in the order they were entered', async () => {
    // Both selects below are populated by an async fetch that has not resolved on the
    // first render (LESSONS 27): a `fireEvent.change` fired before that resolution is a
    // silent no-op (jsdom rejects a <select> value with no matching <option>), which used
    // to leave `lines` empty rather than fail on the field that dropped it.
    service.getSalesOpportunityCustomerOptions.mockResolvedValue({
      items: [{ customer_id: 'cust-1', customer_code: 'C1', customer_name: 'ZZT Customer' }],
      prospect: null,
      blocked: null,
    });
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    await selectOption('Customer or prospect', 'cust-1');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'ZZT Opp' } });
    fireEvent.change(screen.getByLabelText('Expected amount'), { target: { value: '1000' } });
    fireEvent.change(screen.getByLabelText('Expected close date'), {
      target: { value: '2026-11-01' },
    });

    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    const rows = screen.getAllByTestId('opportunity-line-row');
    // A row now also has its own search box (reviewer-7's fix exercises onSearchChange),
    // so `role: 'combobox'` is what picks the <select> out of the two /product/i matches.
    await waitFor(() =>
      expect(
        Array.from(
          (within(rows[0]).getByRole('combobox', { name: /product/i }) as HTMLSelectElement)
            .options,
        ).map((o) => o.value),
      ).toContain('p1'),
    );
    const firstProductSelect = within(rows[0]).getByRole('combobox', { name: /product/i });
    fireEvent.change(firstProductSelect, { target: { value: 'p1' } });
    const firstQty = within(rows[0]).getByLabelText(/qty/i);
    fireEvent.change(firstQty, { target: { value: '3' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync.mock.calls[0][0].lines).toEqual([
      { product_id: 'p1', qty: 3, unit_price: null },
    ]);
  });

  it('fix round2 F7: picking a product prefills Unit price, and Amount is qty x price', async () => {
    service.getSalesOpportunityProductOptions.mockResolvedValue([
      { value: 'p1', label: 'ZZT-001 - ZZT Basin', listPrice: '10.00' },
    ]);
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    const rows = screen.getAllByTestId('opportunity-line-row');
    const productSelect = within(rows[0]).getByRole('combobox', { name: /product/i }) as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(productSelect.options).map((o) => o.value)).toContain('p1'),
    );
    fireEvent.change(productSelect, { target: { value: 'p1' } });

    const unitPrice = within(rows[0]).getByLabelText(/unit price/i) as HTMLInputElement;
    await waitFor(() => expect(unitPrice.value).toBe('10.00'));
    const amount = within(rows[0]).getByLabelText(/^amount$/i) as HTMLInputElement;
    expect(amount.value).toBe('RM 10.00');
  });

  it('fix round2 F7: Expected amount defaults to the live line total until typed into', async () => {
    service.getSalesOpportunityProductOptions.mockResolvedValue([
      { value: 'p1', label: 'ZZT-001 - ZZT Basin', listPrice: '10.00' },
    ]);
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    const rows = screen.getAllByTestId('opportunity-line-row');
    const productSelect = within(rows[0]).getByRole('combobox', { name: /product/i }) as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(productSelect.options).map((o) => o.value)).toContain('p1'),
    );
    fireEvent.change(productSelect, { target: { value: 'p1' } });

    const amountInput = screen.getByLabelText('Expected amount') as HTMLInputElement;
    await waitFor(() => expect(amountInput.value).toBe('10.00'));

    fireEvent.change(within(rows[0]).getByLabelText(/qty/i), { target: { value: '3' } });
    await waitFor(() => expect(amountInput.value).toBe('30.00'));

    // Typing into it directly makes the typed value win, even as the lines keep changing.
    fireEvent.change(amountInput, { target: { value: '999' } });
    fireEvent.change(within(rows[0]).getByLabelText(/qty/i), { target: { value: '5' } });
    expect(amountInput.value).toBe('999');
  });

  it('fix round2 S1: removing every priced line brings Expected amount back to 0.00, not a stale total', async () => {
    service.getSalesOpportunityProductOptions.mockResolvedValue([
      { value: 'p1', label: 'ZZT-001 - ZZT Basin', listPrice: '10.00' },
    ]);
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    const rows = screen.getAllByTestId('opportunity-line-row');
    const productSelect = within(rows[0]).getByRole('combobox', { name: /product/i }) as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(productSelect.options).map((o) => o.value)).toContain('p1'),
    );
    fireEvent.change(productSelect, { target: { value: 'p1' } });

    const amountInput = screen.getByLabelText('Expected amount') as HTMLInputElement;
    await waitFor(() => expect(amountInput.value).toBe('10.00'));

    fireEvent.click(within(rows[0]).getByRole('button', { name: /remove/i }));
    expect(amountInput.value).toBe('0.00');
  });

  it('fix round2 F7: sends the summed amount when Expected amount is left untouched', async () => {
    service.getSalesOpportunityCustomerOptions.mockResolvedValue({
      items: [{ customer_id: 'cust-1', customer_code: 'C1', customer_name: 'ZZT Customer' }],
      prospect: null,
      blocked: null,
    });
    service.getSalesOpportunityProductOptions.mockResolvedValue([
      { value: 'p1', label: 'ZZT-001 - ZZT Basin', listPrice: '10.00' },
    ]);
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    await selectOption('Customer or prospect', 'cust-1');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'ZZT Opp' } });
    fireEvent.change(screen.getByLabelText('Expected close date'), {
      target: { value: '2026-11-01' },
    });

    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    const rows = screen.getAllByTestId('opportunity-line-row');
    const productSelect = within(rows[0]).getByRole('combobox', { name: /product/i }) as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(productSelect.options).map((o) => o.value)).toContain('p1'),
    );
    fireEvent.change(productSelect, { target: { value: 'p1' } });
    fireEvent.change(within(rows[0]).getByLabelText(/qty/i), { target: { value: '2' } });

    await waitFor(() =>
      expect((screen.getByLabelText('Expected amount') as HTMLInputElement).value).toBe('20.00'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
    expect(save.mutateAsync.mock.calls[0][0].expected_amount).toBe('20.00');
  });

  it('NUMBER-FIELDS: a cleared or negative Expected amount keeps Save enabled, says why and sends nothing', async () => {
    service.getSalesOpportunityCustomerOptions.mockResolvedValue({
      items: [{ customer_id: 'cust-1', customer_code: 'C1', customer_name: 'ZZT Customer' }],
      prospect: null,
      blocked: null,
    });
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    await selectOption('Customer or prospect', 'cust-1');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'ZZT Opp' } });
    fireEvent.change(screen.getByLabelText('Expected close date'), {
      target: { value: '2026-11-01' },
    });

    fireEvent.change(screen.getByLabelText('Expected amount'), { target: { value: '' } });
    const saveButton = screen.getByRole('button', { name: 'Save' });
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);
    expect(await screen.findByText(/^Enter a number/)).toBeInTheDocument();
    expect(save.mutateAsync).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Expected amount'), { target: { value: '-5' } });
    expect(screen.getByText(/^Enter a number/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Expected amount'), { target: { value: '5' } });
    expect(screen.queryByText(/^Enter a number/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(save.mutateAsync).toHaveBeenCalledTimes(1));
  });

  it('NUMBER-FIELDS: a product row with a blank quantity blocks Save with a message instead of sending 0', async () => {
    service.getSalesOpportunityCustomerOptions.mockResolvedValue({
      items: [{ customer_id: 'cust-1', customer_code: 'C1', customer_name: 'ZZT Customer' }],
      prospect: null,
      blocked: null,
    });
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    await selectOption('Customer or prospect', 'cust-1');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'ZZT Opp' } });
    fireEvent.change(screen.getByLabelText('Expected close date'), {
      target: { value: '2026-11-01' },
    });
    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    const rows = screen.getAllByTestId('opportunity-line-row');
    const productSelect = within(rows[0]).getByRole('combobox', { name: /product/i }) as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(productSelect.options).map((o) => o.value)).toContain('p1'),
    );
    fireEvent.change(productSelect, { target: { value: 'p1' } });
    fireEvent.change(within(rows[0]).getByLabelText(/qty/i), { target: { value: '' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/^Enter a number/)).toBeInTheDocument();
    expect(save.mutateAsync).not.toHaveBeenCalled();
  });

  it('fix reviewer-7: a preset customer shows its own name, not a blank field', () => {
    render(
      <SalesOpportunityModal
        open
        onOpenChange={() => {}}
        presetCustomerId="cust-1"
        presetCustomerLabel="C1 - ZZT Customer"
      />,
    );
    const select = screen.getByLabelText('Customer or prospect') as HTMLSelectElement;
    expect(select.value).toBe('cust-1');
    expect(screen.getByRole('option', { name: 'C1 - ZZT Customer' })).toBeTruthy();
  });

  it('fix reviewer-7: a picked product keeps its label after a later search', async () => {
    service.getSalesOpportunityProductOptions.mockImplementation(async (q: string) => {
      if (!q) return [{ value: 'p1', label: 'ZZT-001 - ZZT Basin' }];
      return [{ value: 'p2', label: 'ZZT-002 - ZZT Faucet' }];
    });
    render(<SalesOpportunityModal open onOpenChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /add product/i }));
    const rows = screen.getAllByTestId('opportunity-line-row');
    const productSelect = within(rows[0]).getByRole('combobox', { name: /product/i }) as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(productSelect.options).map((o) => o.value)).toContain('p1'),
    );
    fireEvent.change(productSelect, { target: { value: 'p1' } });
    expect(productSelect.value).toBe('p1');

    // A later search for something else replaces the fetched page entirely.
    const searchBox = within(rows[0]).getByLabelText(/product.*search/i);
    fireEvent.change(searchBox, { target: { value: 'faucet' } });
    await waitFor(() =>
      expect(Array.from(productSelect.options).map((o) => o.value)).toContain('p2'),
    );

    // The earlier pick is still selected AND still has its own label available.
    expect(productSelect.value).toBe('p1');
    expect(screen.getByRole('option', { name: 'ZZT-001 - ZZT Basin' })).toBeTruthy();
  });
});
