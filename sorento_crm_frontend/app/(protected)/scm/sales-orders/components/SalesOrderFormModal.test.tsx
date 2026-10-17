/**
 * SalesOrderFormModal - the Add sales order dialog.
 *
 * The per-line Product field is SEARCHED ON THE SERVER (`products/select` answers with its
 * own default of 100 rows against ~22,000 active products, so the static list this replaced
 * held 0.5% of the catalogue and said "no product found" for the rest - the same defect the
 * Start Plan products field carried). The picked code is what submit sends, and a code picked
 * off a page the search has since moved past still reads as `CODE · Name`.
 *
 * `SearchableSelect` is stubbed in BOTH of its modes so the pick is deterministic: a native
 * <select> for the static fields, and a search box + a button per returned row for the async
 * product field, exactly the `(query, pageIndex) => Promise<Option[]>` contract the component
 * calls `fetchOptions` with.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

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

type StubOption = { value: string; label: string };

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    fetchOptions,
    selectedOption,
    placeholder,
  }: {
    value: string;
    onChange: (v: string) => void;
    options?: StubOption[];
    fetchOptions?: (query: string, pageIndex: number) => Promise<StubOption[]>;
    selectedOption?: StubOption;
    placeholder?: string;
  }) => {
    const [fetched, setFetched] = React.useState<StubOption[]>([]);
    const [query, setQuery] = React.useState('');
    React.useEffect(() => {
      if (!fetchOptions) return;
      let live = true;
      void fetchOptions(query, 0).then((rows) => {
        if (live) setFetched(rows);
      });
      return () => {
        live = false;
      };
    }, [fetchOptions, query]);

    if (!fetchOptions) {
      return (
        <select
          aria-label={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{placeholder}</option>
          {(options ?? []).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      );
    }
    return (
      <div data-testid="product-field">
        <input
          aria-label={`Search ${placeholder}`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {/* What the trigger reads: the caller's own label for the picked code, which is the
            only thing standing between the user and a bare SKU once the search moves on. */}
        <span data-testid="product-trigger">{selectedOption?.label ?? value}</span>
        {fetched.map((o) => (
          <button type="button" key={o.value} onClick={() => onChange(o.value)}>
            {o.label}
          </button>
        ))}
      </div>
    );
  },
}));

vi.mock('../../hooks/useScmOptions', () => ({
  useOrderTypeOptions: () => ({
    data: [
      { value: 'dealer', label: 'Dealer' },
      { value: 'project', label: 'Project' },
    ],
    isLoading: false,
  }),
  useCustomerOptions: () => ({
    data: [{ value: '300-R009', label: 'Rowenda Kitchen Sdn Bhd', description: 'DEALER' }],
    isLoading: false,
  }),
}));

/** The page the server answers with when nothing is typed. The codes a person is most likely
 *  to want are deliberately NOT in it - they only come back for a query, which is what the
 *  old capped static list could never do. */
const FIRST_PAGE: StubOption[] = [
  { value: 'SRTWT7408', label: 'SRTWT7408 · Wall-hung WC 7408' },
  { value: 'CW-BASIN-450', label: 'CW-BASIN-450 · Ceramic Wash Basin 450mm' },
];
const OFF_FIRST_PAGE: StubOption[] = [
  { value: 'CB2907', label: 'CB2907 · Concealed cistern 2907' },
  { value: 'SRTWT7445-LV', label: 'SRTWT7445-LV · Wall-hung WC 7445 LV' },
];

// The page index is still recorded on every call (the assertions read it off the spy); the
// stub answers the same one page for each, since paging is `SearchableSelect`'s own concern.
const searchProductOptions = vi.fn<(query: string, pageIndex?: number) => Promise<StubOption[]>>(
  async (query) => {
    const q = query.trim().toLowerCase();
    if (!q) return FIRST_PAGE;
    return [...FIRST_PAGE, ...OFF_FIRST_PAGE].filter((o) => o.label.toLowerCase().includes(q));
  },
);

vi.mock('../../services/scmOptionsService', () => ({
  SELECT_PAGE_SIZE: 50,
  searchProductOptions: (query: string, pageIndex?: number) =>
    searchProductOptions(query, pageIndex),
}));

import { SalesOrderFormModal } from './SalesOrderFormModal';

async function renderModal(
  over: Partial<React.ComponentProps<typeof SalesOrderFormModal>> = {},
) {
  const onSubmit = vi.fn(async () => {});
  const onOpenChange = vi.fn();
  render(
    <SalesOrderFormModal
      open
      onOpenChange={onOpenChange}
      onSubmit={onSubmit}
      isPending={false}
      {...over}
    />,
  );
  // Wait out the product field's first server search, so nothing resolves after the test.
  await screen.findByText(FIRST_PAGE[0].label);
  return { onSubmit, onOpenChange };
}

/** The date field's Label carries no `htmlFor`, and the dialog renders through a portal. */
const dateInput = () =>
  document.body.querySelector('input[type="date"]') as HTMLInputElement;

/** Fill the two required header fields, so a test about the lines is about the lines. */
function fillHeader() {
  fireEvent.change(screen.getByLabelText('Select type'), { target: { value: 'project' } });
  fireEvent.change(screen.getByLabelText('Select customer'), { target: { value: '300-R009' } });
}

beforeEach(() => vi.clearAllMocks());

describe('SalesOrderFormModal - the product field searches the server', () => {
  it('asks the server for the first page with no query when the line opens', async () => {
    await renderModal();
    expect(searchProductOptions).toHaveBeenCalledWith('', 0);
  });

  it('typing sends the query to the server rather than filtering the loaded page', async () => {
    await renderModal();
    fireEvent.change(screen.getByLabelText('Search Select product'), {
      target: { value: 'CB2907' },
    });
    await waitFor(() => expect(searchProductOptions).toHaveBeenCalledWith('CB2907', 0));
  });

  it('a product outside the first page is selectable and its code is what submit sends', async () => {
    const { onSubmit } = await renderModal();
    // Not on the first page - the capped list this replaced could never offer it.
    expect(screen.queryByText('CB2907 · Concealed cistern 2907')).not.toBeInTheDocument();

    fillHeader();
    fireEvent.change(screen.getByLabelText('Search Select product'), {
      target: { value: 'CB2907' },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'CB2907 · Concealed cistern 2907' }));
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create sales order' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit).toHaveBeenCalledWith({
      order_type: 'project',
      customer_code: '300-R009',
      priority: 'normal',
      requested_delivery_date: null,
      lines: [{ sku: 'CB2907', qty_ordered: 4, uom: '' }],
    });
  });

  it('keeps a picked product readable once the search moves on to another page', async () => {
    await renderModal();
    fireEvent.change(screen.getByLabelText('Search Select product'), {
      target: { value: 'SRTWT7445-LV' },
    });
    fireEvent.click(
      await screen.findByRole('button', { name: 'SRTWT7445-LV · Wall-hung WC 7445 LV' }),
    );

    // Search on to a page that does not contain it: the field still reads as the product,
    // not as a bare code.
    fireEvent.change(screen.getByLabelText('Search Select product'), {
      target: { value: 'Basin' },
    });
    await waitFor(() => expect(searchProductOptions).toHaveBeenCalledWith('Basin', 0));
    expect(screen.getByTestId('product-trigger').textContent).toBe(
      'SRTWT7445-LV · Wall-hung WC 7445 LV',
    );
  });

  it('gives every added line its own server-searched product field', async () => {
    await renderModal();
    fireEvent.click(screen.getByRole('button', { name: /Add line/i }));
    await waitFor(() => expect(screen.getAllByTestId('product-field')).toHaveLength(2));

    const second = screen.getAllByTestId('product-field')[1];
    fireEvent.change(within(second).getByLabelText('Search Select product'), {
      target: { value: 'CB2907' },
    });
    expect(
      await within(second).findByRole('button', { name: 'CB2907 · Concealed cistern 2907' }),
    ).toBeInTheDocument();
  });
});

describe('SalesOrderFormModal - payload and validation are unchanged', () => {
  it('sends the picked code, quantity and header, with uom left to the backend', async () => {
    const { onSubmit } = await renderModal();
    fillHeader();
    fireEvent.change(screen.getByLabelText('Select priority'), { target: { value: 'urgent' } });
    fireEvent.change(dateInput(), { target: { value: '2026-09-30' } });
    fireEvent.click(screen.getByRole('button', { name: FIRST_PAGE[0].label }));
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create sales order' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit).toHaveBeenCalledWith({
      order_type: 'project',
      customer_code: '300-R009',
      priority: 'urgent',
      requested_delivery_date: '2026-09-30',
      lines: [{ sku: 'SRTWT7408', qty_ordered: 12, uom: '' }],
    });
  });

  it('still refuses a submit with no product picked', async () => {
    const { onSubmit } = await renderModal();
    fillHeader();
    fireEvent.click(screen.getByRole('button', { name: 'Create sales order' }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(
      screen.getByText('Add at least one line with a product and quantity.'),
    ).toBeInTheDocument();
  });

  it('NUMBER-FIELDS: a picked product with a blank quantity is told, not dropped, and nothing is sent', async () => {
    const { onSubmit } = await renderModal();
    fillHeader();
    fireEvent.click(screen.getByRole('button', { name: FIRST_PAGE[0].label }));
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create sales order' }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/^Enter a number/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '3' } });
    expect(screen.queryByText(/^Enter a number/)).not.toBeInTheDocument();
  });

  it('still refuses a submit with no customer', async () => {
    const { onSubmit } = await renderModal();
    fireEvent.change(screen.getByLabelText('Select type'), { target: { value: 'dealer' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create sales order' }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText('Select a customer.')).toBeInTheDocument();
  });
});
