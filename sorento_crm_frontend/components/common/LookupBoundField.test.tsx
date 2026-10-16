import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

// Mock the data hook so we can drive loading / empty / error / data states.
const useLookupOptionsByBinding = vi.fn();
vi.mock('@/hooks/useLookupOptionsByBinding', () => ({
  useLookupOptionsByBinding: (table: string, column: string) =>
    useLookupOptionsByBinding(table, column),
}));

import LookupBoundField from './LookupBoundField';

const SALES_TYPE_DATA = {
  set_key: 'procurement_sales_type',
  set_name: 'Sales Type',
  options: [
    { value: 'project', label: 'Project', keywords: [], is_active: true },
    { value: 'cash_sales', label: 'Cash Sales', keywords: [], is_active: true },
  ],
  default_value: 'project',
};

function Fallback() {
  return <div data-testid="fallback">free-text fallback</div>;
}

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('LookupBoundField (sales_type) states', () => {
  it('loading → renders the free-text fallback', () => {
    useLookupOptionsByBinding.mockReturnValue({ data: undefined, isLoading: true });
    render(
      <LookupBoundField
        table="purchase_requests"
        column="sales_type"
        value={null}
        onChange={vi.fn()}
        renderFallback={() => <Fallback />}
      />,
    );
    expect(screen.getByTestId('fallback')).toBeInTheDocument();
  });

  it('empty (no binding) → renders the free-text fallback', () => {
    useLookupOptionsByBinding.mockReturnValue({
      data: { set_key: null, set_name: null, options: [] },
      isLoading: false,
    });
    render(
      <LookupBoundField
        table="purchase_requests"
        column="sales_type"
        value={null}
        onChange={vi.fn()}
        renderFallback={() => <Fallback />}
      />,
    );
    expect(screen.getByTestId('fallback')).toBeInTheDocument();
  });

  it('error / forbidden (no data) → renders the free-text fallback', () => {
    // react-query surfaces an error as data:undefined, isLoading:false.
    useLookupOptionsByBinding.mockReturnValue({ data: undefined, isLoading: false });
    render(
      <LookupBoundField
        table="purchase_requests"
        column="sales_type"
        value={null}
        onChange={vi.fn()}
        renderFallback={() => <Fallback />}
      />,
    );
    expect(screen.getByTestId('fallback')).toBeInTheDocument();
  });

  it('data → renders the bound select (placeholder, not the fallback)', () => {
    useLookupOptionsByBinding.mockReturnValue({ data: SALES_TYPE_DATA, isLoading: false });
    render(
      <LookupBoundField
        table="purchase_requests"
        column="sales_type"
        value="cash_sales"
        onChange={vi.fn()}
        placeholder="Select sales type"
        renderFallback={() => <Fallback />}
      />,
    );
    expect(screen.queryByTestId('fallback')).not.toBeInTheDocument();
  });

  it('pre-selects default_value on a NEW (empty) form', async () => {
    const onChange = vi.fn();
    useLookupOptionsByBinding.mockReturnValue({ data: SALES_TYPE_DATA, isLoading: false });
    render(
      <LookupBoundField
        table="purchase_requests"
        column="sales_type"
        value={null}
        onChange={onChange}
        renderFallback={() => <Fallback />}
      />,
    );
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('project'));
  });

  it('does NOT override an existing value on an edit form', async () => {
    const onChange = vi.fn();
    useLookupOptionsByBinding.mockReturnValue({ data: SALES_TYPE_DATA, isLoading: false });
    render(
      <LookupBoundField
        table="purchase_requests"
        column="sales_type"
        value="cash_sales"
        onChange={onChange}
        renderFallback={() => <Fallback />}
      />,
    );
    // Give the effect a tick; it must not fire for a pre-filled field.
    await new Promise((r) => setTimeout(r, 20));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('applyDefault={false} never applies the binding default, even on an empty value', async () => {
    const onChange = vi.fn();
    useLookupOptionsByBinding.mockReturnValue({ data: SALES_TYPE_DATA, isLoading: false });
    render(
      <LookupBoundField
        table="purchase_requests"
        column="sales_type"
        value={null}
        onChange={onChange}
        applyDefault={false}
        renderFallback={() => <Fallback />}
      />,
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('clearable renders Clear selection and pointerDown on it calls onChange("")', () => {
    const onChange = vi.fn();
    useLookupOptionsByBinding.mockReturnValue({ data: SALES_TYPE_DATA, isLoading: false });
    render(
      <LookupBoundField
        table="purchase_requests"
        column="sales_type"
        value="cash_sales"
        onChange={onChange}
        clearable
        renderFallback={() => <Fallback />}
      />,
    );
    fireEvent.pointerDown(screen.getByLabelText('Clear selection'));
    expect(onChange).toHaveBeenCalledWith('');
  });

  it('without clearable there is no clear control', () => {
    useLookupOptionsByBinding.mockReturnValue({ data: SALES_TYPE_DATA, isLoading: false });
    render(
      <LookupBoundField
        table="purchase_requests"
        column="sales_type"
        value="cash_sales"
        onChange={vi.fn()}
        renderFallback={() => <Fallback />}
      />,
    );
    expect(screen.queryByLabelText('Clear selection')).toBeNull();
  });
});
