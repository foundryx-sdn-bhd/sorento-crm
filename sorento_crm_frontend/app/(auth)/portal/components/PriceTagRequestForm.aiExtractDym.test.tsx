/**
 * AI-EXTRACT-DYM (AC-F1..F5, documentation/plans/dealer-kit/
 * ai-extract-dym-acceptance-criteria.md): a row the extract could not match
 * offers the server's "did you mean" suggestions in its Match cell. Nothing is
 * picked for the user; a row only becomes a line once the user picks one.
 *
 * Same harness as `.aiExtractApply.test.tsx`: `AIExtractDialog` is mocked to a
 * prop-capturing stub and the captured `renderRowStatus` is rendered on its
 * own. The SearchableSelect mock here renders its `options`, so a pick is a
 * real `change` to a value the select actually holds.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

const toasts = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), info: vi.fn() }));
vi.mock('@/lib/toast', () => ({ toast: toasts }));

vi.mock('../lib/price-tag-request-service', async () => {
  const { computeLinePricing } = await import(
    '@/app/(auth)/portal/components/__fixtures__/line-pricing'
  );
  return {
    lookupLinePricing: vi.fn(async (mode: string, lines: unknown[]) =>
      computeLinePricing(mode as 'list' | 'selling', lines as never),
    ),
    lookupDebtors: vi.fn(async () => []),
    lookupPromotions: vi.fn(async () => []),
    lookupTagItems: vi.fn(async () => []),
    lookupProductCombos: vi.fn(async () => ({ host_guarded: false, combos: [] })),
    getRequest: vi.fn(),
    createRequest: vi.fn(),
    updateRequest: vi.fn(),
    deleteRequest: vi.fn(),
    submitRequest: vi.fn(),
    approveRequest: vi.fn(),
    requestChanges: vi.fn(),
    listReviewComments: vi.fn(async () => []),
    collectRequest: vi.fn(),
  };
});

vi.mock('../lib/portal-client', () => ({
  uploadAttachment: vi.fn(),
  getPriceTagDesign: vi.fn(),
  fetchMe: vi.fn().mockResolvedValue({ visible_form_types: ['price_tag_request'] }),
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    value: string;
    onChange?: (v: string) => void;
    placeholder?: string;
    options?: { value: string; label: string }[];
    selectedOption?: { value: string; label: string };
  }) => {
    const options = [...(props.options ?? [])];
    if (props.selectedOption && !options.some((o) => o.value === props.selectedOption?.value)) {
      options.push(props.selectedOption);
    }
    return (
      <select
        aria-label={props.id === 'debtor' ? 'Customer' : (props.placeholder ?? '')}
        value={props.value}
        onChange={(e) => props.onChange?.(e.target.value)}
      >
        <option value="" />
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  },
}));

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: () => <select multiple aria-label="Alternatives" onChange={() => {}} />,
}));

vi.mock('./AttachmentDropzone', () => ({ AttachmentDropzone: () => null }));

type CapturedProps = {
  onExtracted?: (products: unknown[]) => void;
  onApply?: (payload: { productLines: unknown[] }) => void;
  renderRowStatus?: (p: Record<string, unknown>, i: number) => React.ReactNode;
};
let captured: CapturedProps = {};
vi.mock('./AIExtractDialog', () => ({
  AIExtractDialog: (props: CapturedProps) => {
    captured = props;
    return null;
  },
}));

import { PriceTagRequestForm } from './PriceTagRequestForm';

const REAL = {
  product_id: 'prod-real-1634-bl',
  product_code: 'SRTKT1634SS-BL',
  product_name: 'ZZT Kitchen Tap Blue',
};
const OTHER = {
  product_id: 'prod-1634-gm',
  product_code: 'SRTKT1634SS-GM',
  product_name: 'ZZT Kitchen Tap Gunmetal',
};

const MISREAD = {
  product_code: 'SRTK1634SS-BL',
  product_name: 'Kitchen tap',
  quantity: 1,
  notes: 'from the SO',
  match: null,
  product_id: null,
  product_set_id: null,
  suggestions: [REAL, OTHER],
};
const BARE_MISS = {
  product_code: 'GHOST-CODE',
  product_name: null,
  quantity: 1,
  notes: null,
  match: null,
  product_id: null,
  product_set_id: null,
  suggestions: [],
};

const PICKER = 'Did you mean?';

beforeEach(() => {
  vi.clearAllMocks();
  captured = {};
});

function openSalesOrderSection() {
  fireEvent.click(screen.getByRole('button', { name: /Sales Order & Lines/ }));
}

async function mountAndExtract(products: Record<string, unknown>[]) {
  render(<PriceTagRequestForm />);
  await screen.findByLabelText('Customer');
  await act(async () => {
    captured.onExtracted?.(products);
  });
}

/** The Match cell for row `i`, rendered from the CURRENT captured props. */
function cell(p: Record<string, unknown>, i = 0) {
  return <div data-testid={`cell-${i}`}>{captured.renderRowStatus?.(p, i)}</div>;
}

describe('PriceTagRequestForm - AI extract did-you-mean (AI-EXTRACT-DYM)', () => {
  it('AC-F1: a not-found row with suggestions shows Not found and an empty picker of CODE - name', async () => {
    await mountAndExtract([MISREAD]);

    const { getByTestId } = render(cell(MISREAD));
    const c = within(getByTestId('cell-0'));

    expect(c.getByText('Not found')).toBeTruthy();
    const picker = c.getByLabelText(PICKER) as HTMLSelectElement;
    expect(picker.value).toBe('');
    const labels = Array.from(picker.options).map((o) => o.textContent);
    expect(labels).toContain(`${REAL.product_code} - ${REAL.product_name}`);
    expect(labels).toContain(`${OTHER.product_code} - ${OTHER.product_name}`);
    // Best first, as the server ranked them.
    expect(labels.indexOf(`${REAL.product_code} - ${REAL.product_name}`)).toBeLessThan(
      labels.indexOf(`${OTHER.product_code} - ${OTHER.product_name}`),
    );
  });

  it('AC-F2: Apply without a pick adds no line and names the code as not found', async () => {
    await mountAndExtract([MISREAD]);
    // Open, so "no line" is a real absence, not a collapsed section.
    openSalesOrderSection();

    await act(async () => {
      captured.onApply?.({ productLines: [MISREAD] });
    });

    expect(screen.queryByLabelText('Quantity for line 1')).toBeNull();
    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith(expect.stringContaining(MISREAD.product_code)),
    );
  });

  it('AC-F3: pick a suggestion, then Apply adds a line for the PICKED product', async () => {
    await mountAndExtract([MISREAD]);
    openSalesOrderSection();

    const { getByTestId } = render(cell(MISREAD));
    await act(async () => {
      fireEvent.change(within(getByTestId('cell-0')).getByLabelText(PICKER), {
        target: { value: REAL.product_id },
      });
    });

    await act(async () => {
      captured.onApply?.({ productLines: [MISREAD] });
    });

    expect(await screen.findByLabelText('Quantity for line 1')).toHaveValue(1);
    expect(screen.getByLabelText('Remarks for line 1')).toHaveValue('from the SO');
    expect(screen.getAllByText(REAL.product_name).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText('Quantity for line 2')).toBeNull();
    expect(toasts.error).not.toHaveBeenCalled();
  });

  it('AC-F4: a pick shows the row as matched; clearing it returns the row to Not found', async () => {
    await mountAndExtract([MISREAD]);
    openSalesOrderSection();

    const { getByTestId, rerender } = render(cell(MISREAD));
    await act(async () => {
      fireEvent.change(within(getByTestId('cell-0')).getByLabelText(PICKER), {
        target: { value: REAL.product_id },
      });
    });
    rerender(cell(MISREAD));
    const picked = within(getByTestId('cell-0'));
    expect(picked.getByText('Matched (picked)')).toBeTruthy();
    expect(picked.queryByText('Not found')).toBeNull();
    expect((picked.getByLabelText(PICKER) as HTMLSelectElement).value).toBe(REAL.product_id);

    await act(async () => {
      fireEvent.change(picked.getByLabelText(PICKER), { target: { value: '' } });
    });
    rerender(cell(MISREAD));
    expect(within(getByTestId('cell-0')).getByText('Not found')).toBeTruthy();

    // Cleared = unpicked: Apply skips it again.
    await act(async () => {
      captured.onApply?.({ productLines: [MISREAD] });
    });
    expect(screen.queryByLabelText('Quantity for line 1')).toBeNull();
    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith(expect.stringContaining(MISREAD.product_code)),
    );
  });

  it('AC-F5: a not-found row with no suggestions shows plain Not found and no picker', async () => {
    await mountAndExtract([BARE_MISS]);

    const { getByTestId } = render(cell(BARE_MISS));
    const c = within(getByTestId('cell-0'));
    expect(c.getByText('Not found')).toBeTruthy();
    expect(c.queryByLabelText(PICKER)).toBeNull();
  });

  it('a fresh extraction forgets an earlier pick (nothing carries over unasked)', async () => {
    await mountAndExtract([MISREAD]);
    const { getByTestId, rerender } = render(cell(MISREAD));
    await act(async () => {
      fireEvent.change(within(getByTestId('cell-0')).getByLabelText(PICKER), {
        target: { value: REAL.product_id },
      });
    });

    await act(async () => {
      captured.onExtracted?.([MISREAD]);
    });
    rerender(cell(MISREAD));

    const c = within(getByTestId('cell-0'));
    expect(c.getByText('Not found')).toBeTruthy();
    expect((c.getByLabelText(PICKER) as HTMLSelectElement).value).toBe('');
  });
});
