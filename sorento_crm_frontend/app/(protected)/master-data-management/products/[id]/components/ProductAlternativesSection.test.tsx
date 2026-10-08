import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

import ProductAlternativesSection from './ProductAlternativesSection';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

let canEdit = true;
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => canEdit,
}));

const { addMutate, resetMutate, unlinkRun } = vi.hoisted(() => ({
  addMutate: vi.fn(),
  resetMutate: vi.fn(),
  unlinkRun: vi.fn(),
}));

let queryData: unknown;
vi.mock('../../hooks/useProductAlternatives', () => ({
  useProductAlternatives: () => ({ data: queryData, isLoading: false, isError: false }),
  useAddProductAlternative: () => ({ mutate: addMutate, isPending: false }),
  useResetProductAlternativesAuto: () => ({ mutate: resetMutate, isPending: false }),
  useProductAlternativeUnlink: () => ({ run: unlinkRun, targetId: null, isPending: false }),
}));

vi.mock('./ProductVariantPickerDialog', () => ({
  default: ({
    open,
    excludeIds = [],
    onConfirm,
  }: {
    open: boolean;
    excludeIds?: string[];
    onConfirm: (id: string) => void;
  }) =>
    open ? (
      <div role="dialog" data-exclude={JSON.stringify(excludeIds)}>
        <button type="button" onClick={() => onConfirm('picked-id')}>
          Confirm pick
        </button>
      </div>
    ) : null,
}));

const ROWS = [
  {
    id: 'row-1',
    alternative_product_id: 'p-new',
    product_code: 'CK100-NEW',
    product_name: 'Cistern new',
    source: 'auto_new',
    is_discontinued: false,
  },
  {
    id: 'row-2',
    alternative_product_id: 'p-old',
    product_code: 'CK200',
    product_name: 'Cistern old',
    source: 'manual',
    is_discontinued: true,
  },
];

describe('ProductAlternativesSection', () => {
  beforeEach(() => {
    canEdit = true;
    queryData = { alternatives_manual: true, alternatives: ROWS };
    vi.clearAllMocks();
  });
  afterEach(cleanup);

  it('renders rows with code link, name, source and discontinued badges', () => {
    render(<ProductAlternativesSection productId="me" />);
    expect(screen.getByText('CK100-NEW').closest('a')?.getAttribute('href')).toBe(
      '/master-data-management/products/p-new',
    );
    expect(screen.getByText('Cistern old')).toBeTruthy();
    expect(screen.getByText('Auto')).toBeTruthy();
    expect(screen.getByText('Manual')).toBeTruthy();
    expect(screen.getAllByText('Discontinued')).toHaveLength(1);
  });

  it('renders the empty state', () => {
    queryData = { alternatives_manual: false, alternatives: [] };
    render(<ProductAlternativesSection productId="me" />);
    expect(screen.getByText('No alternatives')).toBeTruthy();
    expect(screen.getByText('None linked yet.')).toBeTruthy();
  });

  it('adds the picked product, excluding self and existing', () => {
    render(<ProductAlternativesSection productId="me" />);
    fireEvent.click(screen.getByRole('button', { name: /Add alternative/ }));
    expect(JSON.parse(screen.getByRole('dialog').getAttribute('data-exclude') as string)).toEqual([
      'me',
      'p-new',
      'p-old',
    ]);
    fireEvent.click(screen.getByText('Confirm pick'));
    expect(addMutate).toHaveBeenCalledWith('picked-id', expect.any(Object));
  });

  it('shows Reset to auto only when manual, and runs it', () => {
    const { unmount } = render(<ProductAlternativesSection productId="me" />);
    fireEvent.click(screen.getByRole('button', { name: /Reset to auto/ }));
    expect(resetMutate).toHaveBeenCalled();
    unmount();
    queryData = { alternatives_manual: false, alternatives: ROWS };
    render(<ProductAlternativesSection productId="me" />);
    expect(screen.queryByRole('button', { name: /Reset to auto/ })).toBeNull();
  });

  it('unlink runs the deferred action with the row id', () => {
    render(<ProductAlternativesSection productId="me" />);
    fireEvent.click(screen.getByRole('button', { name: 'Unlink CK200' }));
    expect(unlinkRun).toHaveBeenCalledWith(expect.objectContaining({ id: 'row-2' }));
  });

  it('hides edit actions without permission', () => {
    canEdit = false;
    render(<ProductAlternativesSection productId="me" />);
    expect(screen.queryByRole('button', { name: /Add alternative/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Unlink/ })).toBeNull();
  });
});
