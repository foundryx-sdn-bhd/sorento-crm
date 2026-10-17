/** NUMBER-FIELDS round 3 (R9): bad input in a number filter value refuses Apply; the filter is not applied. */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { typeBadInput } from '@/test-utils/badInput';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const toastError = vi.hoisted(() => vi.fn());
vi.mock('@/lib/toast', () => ({ toast: { error: toastError, success: vi.fn() } }));

vi.mock('@/lib/list-query/listQueryService', () => ({
  fetchListQueryFields: vi.fn(async () => [
    {
      field_key: 'total',
      label: 'Total',
      data_type: 'number',
      compile_key: 'total',
      allowed_operators: ['gt'],
      filterable: true,
      exportable: true,
      is_line_field: false,
      sort_order: 1,
    },
  ]),
}));

import { ListQueryFilterDialog } from './ListQueryFilterDialog';

describe('ListQueryFilterDialog bad number input (R9)', () => {
  it('refuses Apply with the sentence and applies no filter', async () => {
    const onApply = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ListQueryFilterDialog
          resourceKey={'orders' as never}
          open
          onOpenChange={vi.fn()}
          initialFilter={null}
          onApply={onApply}
        />
      </QueryClientProvider>,
    );
    typeBadInput(await screen.findByPlaceholderText('Value'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(onApply).not.toHaveBeenCalled();
    const shown =
      toastError.mock.calls.some((c) => /Enter a number/.test(String(c[0]))) ||
      screen.queryByText(/Enter a number/) !== null;
    expect(shown).toBe(true);
  });
});
