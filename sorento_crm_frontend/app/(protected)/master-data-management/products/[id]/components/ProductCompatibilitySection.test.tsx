import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

import ProductCompatibilitySection from './ProductCompatibilitySection';

vi.mock('next/link', () => ({
  default: ({
    href,
    children,
    ...rest
  }: { href: string; children: ReactNode } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

let canEdit = true;
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => canEdit,
}));

const { addMutate, unlinkRun } = vi.hoisted(() => ({
  addMutate: vi.fn(),
  unlinkRun: vi.fn(),
}));

let queryData: unknown;
vi.mock('../../hooks/useProductCompatibility', () => ({
  useProductCompatibility: () => ({
    data: queryData,
    isLoading: false,
    isError: false,
  }),
  useAddProductCompatibility: () => ({ mutate: addMutate, isPending: false }),
  useProductCompatibilityUnlink: () => ({
    run: unlinkRun,
    targetId: null,
    isPending: false,
  }),
}));

vi.mock('./ProductVariantPickerDialog', () => ({
  default: ({
    open,
    excludeIds = [],
    onConfirm,
    children,
  }: {
    open: boolean;
    excludeIds?: string[];
    onConfirm: (id: string) => void;
    children?: ReactNode;
  }) =>
    open ? (
      <div role="dialog" data-exclude={JSON.stringify(excludeIds)}>
        {children}
        <button type="button" onClick={() => onConfirm('picked-id')}>
          Confirm pick
        </button>
      </div>
    ) : null,
}));

const COVER = {
  id: 'link-1',
  product_id: 'cover-1',
  product_code: 'SRTSCBD823-MBL',
  product_name: 'Seat cover matt black',
  source: 'flyer',
  is_discontinued: false,
};
const WC = {
  id: 'link-2',
  product_id: 'wc-1',
  product_code: 'SRTWC8383-RL-MBL',
  product_name: 'WC matt black',
  source: 'manual',
  is_discontinued: true,
};

describe('ProductCompatibilitySection', () => {
  beforeEach(() => {
    canEdit = true;
    queryData = { covers: [COVER], fits: [] };
    vi.clearAllMocks();
  });
  afterEach(cleanup);

  it('lists the covers that fit, with a link, the source and the empty other list', () => {
    render(
      <ProductCompatibilitySection
        productId="me"
        productCode="SRTWC8383-RL-MBL"
      />,
    );
    expect(screen.getByText('Compatible (1)')).toBeTruthy();
    expect(
      screen.getByText('SRTSCBD823-MBL').closest('a')?.getAttribute('href'),
    ).toBe('/master-data-management/products/cover-1');
    expect(screen.getByText('Flyer')).toBeTruthy();
    expect(screen.getByText('No WCs')).toBeTruthy();
  });

  it('lists the WCs a cover fits, with manual and discontinued badges', () => {
    queryData = { covers: [], fits: [WC] };
    render(
      <ProductCompatibilitySection
        productId="me"
        productCode="SRTSCBD823-MBL"
      />,
    );
    expect(screen.getByText('SRTWC8383-RL-MBL')).toBeTruthy();
    expect(screen.getByText('Manual')).toBeTruthy();
    expect(screen.getByText('Discontinued')).toBeTruthy();
    expect(screen.getByText('No seat covers')).toBeTruthy();
  });

  it('renders both empty states with no button inside them', () => {
    queryData = { covers: [], fits: [] };
    render(
      <ProductCompatibilitySection
        productId="me"
        productCode="SRTWC8383-RL-UF"
      />,
    );
    expect(screen.getByText('Compatible')).toBeTruthy();
    expect(screen.getByText('No seat covers')).toBeTruthy();
    expect(screen.getByText('No WCs')).toBeTruthy();
  });

  it('adds a seat cover from a WC page, excluding self and linked products', () => {
    render(
      <ProductCompatibilitySection
        productId="me"
        productCode="SRTWC8383-RL-MBL"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Add compatible/ }));
    const dialog = screen.getByRole('dialog');
    expect(JSON.parse(dialog.getAttribute('data-exclude') as string)).toEqual([
      'me',
      'cover-1',
    ]);
    expect(
      screen
        .getByRole('radio', { name: 'Seat cover' })
        .getAttribute('data-state'),
    ).toBe('checked');
    fireEvent.click(screen.getByText('Confirm pick'));
    expect(addMutate).toHaveBeenCalledWith(
      { otherProductId: 'picked-id', otherRole: 'seat_cover' },
      expect.any(Object),
    );
  });

  it('defaults to adding a WC on a seat cover page, including a -SC SKU', () => {
    queryData = { covers: [], fits: [] };
    render(
      <ProductCompatibilitySection productId="me" productCode="SRTWC8383-SC" />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Add compatible/ }));
    expect(
      screen.getByRole('radio', { name: 'WC' }).getAttribute('data-state'),
    ).toBe('checked');
    fireEvent.click(screen.getByText('Confirm pick'));
    expect(addMutate).toHaveBeenCalledWith(
      { otherProductId: 'picked-id', otherRole: 'wc' },
      expect.any(Object),
    );
  });

  it('unlink runs the deferred action with the link id', () => {
    render(
      <ProductCompatibilitySection
        productId="me"
        productCode="SRTWC8383-RL-MBL"
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Unlink SRTSCBD823-MBL' }),
    );
    expect(unlinkRun).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'link-1' }),
    );
  });

  it('hides edit actions without permission', () => {
    canEdit = false;
    render(
      <ProductCompatibilitySection
        productId="me"
        productCode="SRTWC8383-RL-MBL"
      />,
    );
    expect(screen.queryByRole('button', { name: /Add compatible/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Unlink/ })).toBeNull();
  });
});
