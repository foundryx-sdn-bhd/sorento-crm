/**
 * AddEditPolicyModal - scope-driven target picker, edit-global lock, and
 * validation surfacing.
 *   AC-EDIT-1 (scope switch changes the target picker: sku→product,
 *     product_class→class, abc_xyz_cell→two ABC/XYZ pickers)
 *   AC-EDIT-4 (edit-global locks the scope + hides the target picker)
 *   AC-VAL-7 (statistical safety stock without a service level is blocked FE-side)
 *   AC-EDIT-5 (a rejecting submit keeps the modal open)
 *
 * SearchableSelect is mocked to a native <select> so scope switching is
 * deterministic in jsdom (the real one is a Radix popover + cmdk list).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
  });
}

// Native-select stand-in; aria-label = placeholder (each interactive select in
// the modal has a distinct placeholder) so tests can target them unambiguously.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
    disabled,
  }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    placeholder?: string;
    disabled?: boolean;
  }) => (
    <select
      aria-label={placeholder ?? 'select'}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{placeholder ?? ''}</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

const hooks = vi.hoisted(() => ({
  useProductScopeOptions: vi.fn(),
  useClassScopeOptions: vi.fn(),
}));
vi.mock('../hooks/usePolicies', () => hooks);

import { AddEditPolicyModal } from './AddEditPolicyModal';
import type { ReorderPolicyRow } from '../types/policy.types';

const PRODUCTS = [{ value: 'prd-uuid-1', label: 'BRK-450 · Brake Disc' }];
const CLASSES = [{ value: 'BRK', label: 'Braking' }];

function renderModal(over: Partial<React.ComponentProps<typeof AddEditPolicyModal>> = {}) {
  const props: React.ComponentProps<typeof AddEditPolicyModal> = {
    open: true,
    onOpenChange: vi.fn(),
    mode: 'create',
    initial: null,
    onSubmit: vi.fn().mockResolvedValue(undefined),
    isSubmitting: false,
    ...over,
  };
  render(<AddEditPolicyModal {...props} />);
  return props;
}

const GLOBAL_ROW: ReorderPolicyRow = {
  id: 'pol-global',
  scope_type: 'global',
  scope_ref: null,
  scope_label: '-',
  policy_type: 'reorder_point',
  service_level: null,
  safety_stock_method: 'fixed_days',
  safety_days: 7,
  review_period_days: null,
  forecast_window_days: 90,
  baseline_source: null,
  spike_handling: null,
  buy_scope: null,
  dead_stock_days: 180,
  overstock_days: 120,
  min_override: null,
  max_override: null,
  priority: 0,
  is_active: true,
  supplier_selection: 'best_score',
  lead_time_default_days: 14,
};

beforeEach(() => {
  hooks.useProductScopeOptions.mockReturnValue({ data: PRODUCTS, isLoading: false });
  hooks.useClassScopeOptions.mockReturnValue({ data: CLASSES, isLoading: false });
});

describe('AddEditPolicyModal - scope-driven target picker (AC-EDIT-1)', () => {
  it('defaults to the SKU product picker', () => {
    renderModal();
    expect(screen.getByLabelText('Search a product')).toBeInTheDocument();
    expect(screen.queryByLabelText('Search a product class')).not.toBeInTheDocument();
  });

  it('switching scope to Product class shows the class picker (value = category_code)', () => {
    renderModal();
    fireEvent.change(screen.getByLabelText('Choose a scope'), { target: { value: 'product_class' } });
    const classSelect = screen.getByLabelText('Search a product class');
    expect(classSelect).toBeInTheDocument();
    expect(screen.queryByLabelText('Search a product')).not.toBeInTheDocument();
    // The Braking option carries the category_code as its value.
    expect(within(classSelect).getByRole('option', { name: 'Braking' })).toHaveValue('BRK');
  });

  it('switching scope to ABC-XYZ cell shows two pickers', () => {
    renderModal();
    fireEvent.change(screen.getByLabelText('Choose a scope'), { target: { value: 'abc_xyz_cell' } });
    expect(screen.getByLabelText('ABC')).toBeInTheDocument();
    expect(screen.getByLabelText('XYZ')).toBeInTheDocument();
    expect(screen.queryByLabelText('Search a product')).not.toBeInTheDocument();
  });
});

describe('AddEditPolicyModal - edit global default (AC-EDIT-4)', () => {
  it('locks the scope to Global default and hides the target picker', () => {
    renderModal({ mode: 'edit', initial: GLOBAL_ROW });
    expect(screen.getByText('Edit global default policy')).toBeInTheDocument();
    // Scope is a locked label, not a select.
    expect(screen.queryByLabelText('Choose a scope')).not.toBeInTheDocument();
    expect(screen.getByText('Global default')).toBeInTheDocument();
    // No scope-target picker for a global row.
    expect(screen.queryByLabelText('Search a product')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Search a product class')).not.toBeInTheDocument();
  });
});

describe('AddEditPolicyModal - validation surfacing (AC-VAL-7 / AC-EDIT-5)', () => {
  it('blocks a statistical policy with no service level and does not submit', () => {
    const props = renderModal();
    // Pick a product so scope_ref passes, then choose statistical SS.
    fireEvent.change(screen.getByLabelText('Search a product'), { target: { value: 'prd-uuid-1' } });
    // safety-stock method select carries the statistical option.
    const ssMethod = screen
      .getAllByRole('combobox')
      .find((el) => within(el).queryByRole('option', { name: 'Statistical' }))!;
    fireEvent.change(ssMethod, { target: { value: 'statistical' } });
    fireEvent.click(screen.getByRole('button', { name: /Add policy/i }));
    // Inline error surfaces and onSubmit is NOT called.
    expect(screen.getByText(/requires a service level/i)).toBeInTheDocument();
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it('never self-closes on submit - closing is delegated to the parent (AC-EDIT-5)', async () => {
    // The modal only closes when the parent flips `open` after a RESOLVED save;
    // it never calls onOpenChange(false) itself, so on a rejected save it stays
    // open. (The error toast is proven by the usePolicies hook tests.)
    const onOpenChange = vi.fn();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderModal({ onOpenChange, onSubmit });
    fireEvent.change(screen.getByLabelText('Choose a scope'), { target: { value: 'product_class' } });
    fireEvent.change(screen.getByLabelText('Search a product class'), { target: { value: 'BRK' } });
    fireEvent.click(screen.getByRole('button', { name: /Add policy/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('a valid product_class override submits the write body with scope_ref = category_code', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderModal({ onSubmit });
    fireEvent.change(screen.getByLabelText('Choose a scope'), { target: { value: 'product_class' } });
    fireEvent.change(screen.getByLabelText('Search a product class'), { target: { value: 'BRK' } });
    fireEvent.click(screen.getByRole('button', { name: /Add policy/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const body = onSubmit.mock.calls[0][0];
    expect(body.scope_type).toBe('product_class');
    expect(body.scope_ref).toBe('BRK');
  });
});

describe('AddEditPolicyModal - engine fields (AC-EDIT-2)', () => {
  it('renders the four engine fields defaulted to the engine defaults', () => {
    renderModal();
    expect(screen.getByPlaceholderText('e.g. 90')).toHaveValue(90);
    expect(screen.getByLabelText('Choose a baseline')).toHaveValue('continuous_only');
    expect(screen.getByLabelText('Choose spike handling')).toHaveValue('committed_only');
    expect(screen.getByLabelText('Choose a buy scope')).toHaveValue('network');
  });

  it('round-trips edited engine values into the write body', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderModal({ onSubmit });
    fireEvent.change(screen.getByLabelText('Choose a scope'), { target: { value: 'product_class' } });
    fireEvent.change(screen.getByLabelText('Search a product class'), { target: { value: 'BRK' } });
    fireEvent.change(screen.getByPlaceholderText('e.g. 90'), { target: { value: '120' } });
    fireEvent.change(screen.getByLabelText('Choose a baseline'), { target: { value: 'all' } });
    fireEvent.change(screen.getByLabelText('Choose spike handling'), { target: { value: 'statistical' } });
    fireEvent.change(screen.getByLabelText('Choose a buy scope'), { target: { value: 'warehouse' } });
    fireEvent.click(screen.getByRole('button', { name: /Add policy/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const body = onSubmit.mock.calls[0][0];
    expect(body.forecast_window_days).toBe(120);
    expect(body.baseline_source).toBe('all');
    expect(body.spike_handling).toBe('statistical');
    expect(body.buy_scope).toBe('warehouse');
  });

  it('preserves existing engine values when editing (AC-EDIT-2)', () => {
    renderModal({
      mode: 'edit',
      initial: {
        ...GLOBAL_ROW,
        id: 'pol-class',
        scope_type: 'product_class',
        scope_ref: 'BRK',
        scope_label: 'Braking',
        forecast_window_days: 45,
        baseline_source: 'all',
        spike_handling: 'ignore',
        buy_scope: 'warehouse',
      },
    });
    expect(screen.getByPlaceholderText('e.g. 90')).toHaveValue(45);
    expect(screen.getByLabelText('Choose a baseline')).toHaveValue('all');
    expect(screen.getByLabelText('Choose spike handling')).toHaveValue('ignore');
    expect(screen.getByLabelText('Choose a buy scope')).toHaveValue('warehouse');
  });
});

describe('AddEditPolicyModal - bad input on an optional override (round 2, S-B)', () => {
  it('"5-" in Min override (badInput) + Save shows the sentence under the box and saves nothing', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderModal({ onSubmit });
    fireEvent.change(screen.getByLabelText('Choose a scope'), { target: { value: 'product_class' } });
    fireEvent.change(screen.getByLabelText('Search a product class'), { target: { value: 'BRK' } });
    const typeSelect = screen
      .getAllByRole('combobox')
      .find((el) => (el as HTMLSelectElement).querySelector('option[value="min_max"]'))!;
    fireEvent.change(typeSelect, { target: { value: 'min_max' } });
    const min = screen.getByPlaceholderText('e.g. 40') as HTMLInputElement;
    fireEvent.change(min, { target: { value: '5' } });
    Object.defineProperty(min, 'validity', { value: { badInput: true }, configurable: true });
    fireEvent.change(min, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Add policy/i }));
    expect(screen.getByText('Enter a number of at least 0, or leave it blank.')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
