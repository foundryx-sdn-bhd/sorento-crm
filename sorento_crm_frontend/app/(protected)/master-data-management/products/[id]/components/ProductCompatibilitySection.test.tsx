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
  kind: 'seat_cover',
};
const WC = {
  id: 'link-2',
  product_id: 'wc-1',
  product_code: 'SRTWC8383-RL-MBL',
  product_name: 'WC matt black',
  source: 'manual',
  is_discontinued: true,
  kind: 'seat_cover',
};
const MIRROR = {
  id: 'link-3',
  product_id: 'mirror-1',
  product_code: 'SRTMR11404-BL',
  product_name: 'Mirror black',
  source: 'flyer',
  is_discontinued: false,
  kind: 'mirror',
};
const SHELF = {
  id: 'link-4',
  product_id: 'shelf-1',
  product_code: 'SRTSV11404-BL',
  product_name: 'Shelf black',
  source: 'flyer',
  is_discontinued: false,
  kind: 'shelf',
};
const CABINET = {
  id: 'link-5',
  product_id: 'cab-1',
  product_code: 'SRTBF11404',
  product_name: 'Basin cabinet',
  source: 'flyer',
  is_discontinued: false,
  kind: 'mirror',
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
      { otherProductId: 'picked-id', otherRole: 'accessory', kind: 'seat_cover' },
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
      { otherProductId: 'picked-id', otherRole: 'host', kind: 'seat_cover' },
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

describe('ProductCompatibilitySection mirror and shelf links', () => {
  beforeEach(() => {
    canEdit = true;
    queryData = { covers: [MIRROR, SHELF], fits: [] };
    vi.clearAllMocks();
  });
  afterEach(cleanup);

  it('a cabinet with a mirror and a shelf shows those groups, not seat covers', () => {
    render(
      <ProductCompatibilitySection productId="me" productCode="SRTBF11404" />,
    );
    expect(screen.getByText('Compatible (2)')).toBeTruthy();
    expect(screen.getByText('Mirrors that fit')).toBeTruthy();
    expect(screen.getByText('Shelves that fit')).toBeTruthy();
    expect(screen.getByText('SRTMR11404-BL')).toBeTruthy();
    expect(screen.getByText('SRTSV11404-BL')).toBeTruthy();
    expect(screen.queryByText('No seat covers')).toBeNull();
    expect(screen.queryByText('Seat covers that fit')).toBeNull();
    expect(screen.queryByText('No WCs')).toBeNull();
  });

  it('a mirror product lists the cabinets it fits', () => {
    queryData = { covers: [], fits: [CABINET] };
    render(
      <ProductCompatibilitySection productId="me" productCode="SRTMR11404-BL" />,
    );
    expect(screen.getByText('Fits these cabinets')).toBeTruthy();
    expect(screen.getByText('SRTBF11404')).toBeTruthy();
    expect(screen.queryByText('Fits these WCs')).toBeNull();
    expect(screen.queryByText('No seat covers')).toBeNull();
  });

  it('the add dialog has a Link type picker defaulting to seat cover', () => {
    queryData = { covers: [], fits: [] };
    render(
      <ProductCompatibilitySection productId="me" productCode="SRTBF11404" />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Add compatible/ }));
    expect(screen.getByText('Link type')).toBeTruthy();
    expect(
      screen
        .getByRole('radio', { name: 'Seat cover / WC' })
        .getAttribute('data-state'),
    ).toBe('checked');
    expect(screen.getByRole('radio', { name: 'Mirror / cabinet' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Shelf / cabinet' })).toBeTruthy();
  });

  it('choosing the Mirror link type posts kind mirror', () => {
    queryData = { covers: [], fits: [] };
    render(
      <ProductCompatibilitySection productId="me" productCode="SRTBF11404" />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Add compatible/ }));
    fireEvent.click(screen.getByRole('radio', { name: 'Mirror / cabinet' }));
    expect(screen.getByRole('radio', { name: 'Mirror' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Cabinet' })).toBeTruthy();
    fireEvent.click(screen.getByText('Confirm pick'));
    expect(addMutate).toHaveBeenCalledWith(
      expect.objectContaining({ otherProductId: 'picked-id', kind: 'mirror' }),
      expect.any(Object),
    );
  });

  it('a cabinet with a mirror link defaults the dialog to mirror, accessory', () => {
    render(
      <ProductCompatibilitySection productId="me" productCode="SRTBF11404" />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Add compatible/ }));
    expect(
      screen
        .getByRole('radio', { name: 'Mirror / cabinet' })
        .getAttribute('data-state'),
    ).toBe('checked');
    expect(
      screen.getByRole('radio', { name: 'Mirror' }).getAttribute('data-state'),
    ).toBe('checked');
    fireEvent.click(screen.getByText('Confirm pick'));
    expect(addMutate).toHaveBeenCalledWith(
      { otherProductId: 'picked-id', otherRole: 'accessory', kind: 'mirror' },
      expect.any(Object),
    );
  });

  it('a mirror product defaults the dialog to mirror, host', () => {
    queryData = { covers: [], fits: [CABINET] };
    render(
      <ProductCompatibilitySection productId="me" productCode="SRTMR11404-BL" />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Add compatible/ }));
    fireEvent.click(screen.getByText('Confirm pick'));
    expect(addMutate).toHaveBeenCalledWith(
      { otherProductId: 'picked-id', otherRole: 'host', kind: 'mirror' },
      expect.any(Object),
    );
  });
});
