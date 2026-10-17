/**
 * NUMBER-FIELDS (S4 SNAP screen): the numbering rule dialog snapped a cleared "Next running
 * number" to 0 and "Number digits" to 1. After: the box stays empty; Save with it empty sends
 * nothing, names the problem under the field and keeps the dialog open.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  });
}

const mutate = vi.fn();
vi.mock('../hooks/useNumberingRules', () => ({
  useUpdateNumberingRule: () => ({ mutate, isPending: false }),
}));
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: () => <div data-testid="select-stub" />,
}));

import NumberingRuleEditDialog from './NumberingRuleEditDialog';

const RULE = {
  doc_type: 'purchase_request',
  enabled: true,
  prefix_template: 'PR-{year}-',
  number_digits: 4,
  next_value: 12,
  start_value: 1,
  reset_policy: 'none',
} as never;

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('NumberingRuleEditDialog - blank number fields', () => {
  it('Next running number stays empty after being cleared (not 0)', () => {
    render(<NumberingRuleEditDialog open onOpenChange={vi.fn()} rule={RULE} />);
    const input = screen.getByLabelText('Next running number') as HTMLInputElement;
    expect(input.value).toBe('12');
    fireEvent.change(input, { target: { value: '' } });
    expect(input.value).toBe('');
  });

  it('Number digits stays empty after being cleared (not 1)', () => {
    render(<NumberingRuleEditDialog open onOpenChange={vi.fn()} rule={RULE} />);
    const input = screen.getByLabelText('Number digits (padding)') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '' } });
    expect(input.value).toBe('');
  });

  it('Save with Next running number empty sends nothing, shows the message, dialog stays open', async () => {
    const onOpenChange = vi.fn();
    render(<NumberingRuleEditDialog open onOpenChange={onOpenChange} rule={RULE} />);
    const input = screen.getByLabelText('Next running number') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save numbering rule' }));

    expect(await screen.findByText(/^Enter a (whole )?number/)).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
