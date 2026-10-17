/**
 * Settings -> Search ranking (AC-C.1, AC-G.8).
 *
 * The component talks to the two hooks only, so both are mocked here per the
 * agreed testing seam: components with mocked hooks, hooks with a mocked service.
 */
import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SpecSearchPolicyRow } from '@/app/(protected)/master-data-management/product-specifications/types/productSpec.types';

const mockMutate = vi.fn();
let mockSave: { isPending: boolean; variables: { policyKey: string; value: number } | undefined };
let mockPolicyState: {
  data: SpecSearchPolicyRow[] | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
};

vi.mock('../../hooks/useSearchPolicyQuery', () => ({
  useSearchPolicyQuery: () => mockPolicyState,
}));

vi.mock('../../hooks/useSearchPolicyMutations', () => ({
  useSearchPolicyMutations: () => ({ save: { mutate: mockMutate, ...mockSave } }),
}));

import { SearchRankingSettings } from './SearchRankingSettings';

const ROWS: SpecSearchPolicyRow[] = [
  {
    policy_key: 'class_boost',
    label: 'Class match boost',
    help_text: 'How much a matching product class outranks a non-match.',
    value: 5,
    default_value: 5,
  },
  {
    policy_key: 'brand_boost',
    label: 'Brand preference boost',
    help_text: 'How much a preferred brand outranks the rest.',
    value: 8,
    default_value: 5,
  },
];

beforeEach(() => {
  cleanup();
  mockMutate.mockReset();
  mockSave = { isPending: false, variables: undefined };
  mockPolicyState = { data: ROWS, isLoading: false, isError: false, error: null };
});

describe('SearchRankingSettings', () => {
  it('renders one row per policy key with its current value', () => {
    render(<SearchRankingSettings />);
    expect(screen.getByText('Class match boost')).toBeInTheDocument();
    expect(screen.getByText('Brand preference boost')).toBeInTheDocument();
    expect(screen.getByDisplayValue('5')).toBeInTheDocument();
    expect(screen.getByDisplayValue('8')).toBeInTheDocument();
  });

  it('shows the Changed from pill only for a row off its default', () => {
    render(<SearchRankingSettings />);
    expect(screen.getByText('Changed from 5')).toBeInTheDocument();
    expect(screen.getAllByText(/Changed from/)).toHaveLength(1);
  });

  it('Save starts disabled and enables once the row is edited (dirty)', () => {
    render(<SearchRankingSettings />);
    const saveButtons = screen.getAllByRole('button', { name: 'Save' });
    expect(saveButtons[0]).toBeDisabled();

    const inputs = screen.getAllByRole('spinbutton');
    fireEvent.change(inputs[0], { target: { value: '7' } });
    expect(saveButtons[0]).toBeEnabled();
  });

  it('Save calls the mutation with the policy key and the numeric draft', () => {
    render(<SearchRankingSettings />);
    const inputs = screen.getAllByRole('spinbutton');
    fireEvent.change(inputs[0], { target: { value: '7' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[0]);
    expect(mockMutate).toHaveBeenCalledWith({ policyKey: 'class_boost', value: 7 });
  });

  it('item 11 - an emptied draft does not save 0: Save says what is wrong and sends nothing', () => {
    render(<SearchRankingSettings />);
    const saveButtons = screen.getAllByRole('button', { name: 'Save' });
    const inputs = screen.getAllByRole('spinbutton');

    fireEvent.change(inputs[0], { target: { value: '' } });
    expect(saveButtons[0]).toBeEnabled();

    fireEvent.click(saveButtons[0]);
    expect(screen.getByText('Enter a number of at least 0.')).toBeInTheDocument();
    expect(inputs[0]).toHaveValue(null);
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it('item 11 - a half-typed draft (e.g. a bare "-") keeps Save live and blocks with a sentence', () => {
    render(<SearchRankingSettings />);
    const saveButtons = screen.getAllByRole('button', { name: 'Save' });
    const inputs = screen.getAllByRole('spinbutton');

    fireEvent.change(inputs[0], { target: { value: '-' } });
    expect(saveButtons[0]).toBeEnabled();
    fireEvent.click(saveButtons[0]);
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it('renders the field hint but not the removed explainer paragraph', () => {
    render(<SearchRankingSettings />);
    expect(
      screen.getByText('How much a matching product class outranks a non-match.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/never a filter/)).not.toBeInTheDocument();
  });

  it('renders the empty state when no policy rows exist', () => {
    mockPolicyState = { data: [], isLoading: false, isError: false, error: null };
    render(<SearchRankingSettings />);
    expect(screen.getByText('No ranking settings are configured yet.')).toBeInTheDocument();
  });

  it('renders skeletons while loading', () => {
    mockPolicyState = { data: undefined, isLoading: true, isError: false, error: null };
    const { container } = render(<SearchRankingSettings />);
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  });

  it('renders the standard error state on a load failure', () => {
    mockPolicyState = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('Not permitted'),
    };
    render(<SearchRankingSettings />);
    expect(screen.getByText('Not permitted')).toBeInTheDocument();
  });
});
