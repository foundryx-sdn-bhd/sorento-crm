import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  cleanup,
  fireEvent,
  within,
} from '@testing-library/react';

import SeatCoverFlyerReview from './SeatCoverFlyerReview';
import type { FlyerReview } from '../types/productCompatibility.types';

const perms = new Set<string>();
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => perms.has(slug),
}));

const { applyMutateAsync, retireRun } = vi.hoisted(() => ({
  applyMutateAsync: vi
    .fn()
    .mockResolvedValue({ linked: 1, already_linked: 0, skipped: [] }),
  retireRun: vi.fn(),
}));

let review: FlyerReview | undefined;
vi.mock('../hooks/useProductCompatibility', () => ({
  useFlyerReview: () => ({
    data: review,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useApplyFlyerReview: () => ({
    mutateAsync: applyMutateAsync,
    isPending: false,
  }),
  useRetireSeatCoverSpec: () => ({
    run: retireRun,
    targetId: null,
    isPending: false,
  }),
}));

const sku = (over: Partial<FlyerReview['models'][number]['skus'][number]>) => ({
  product_id: 'id',
  product_code: 'CODE',
  product_name: 'CODE',
  company_name: null,
  is_active: true,
  status: 'ready' as const,
  reason: null,
  printed_code: 'SRTWC8383',
  flyer_row: 3,
  cover_codes: ['SRTSCBD331-UF'],
  extra_segments: ['RL', 'UF'],
  ...over,
});

function makeReview(canRetire = false): FlyerReview {
  return {
    models: [
      {
        family_code: 'SRTWC8383',
        printed: [
          {
            flyer_row: 3,
            wc_code: 'SRTWC8383',
            covers: [{ code: 'SRTSCBD331-UF', found: true }],
          },
          {
            flyer_row: 24,
            wc_code: 'SRTWC8383-MBL',
            covers: [{ code: 'SRTSCBD823-MBL', found: true }],
          },
        ],
        skus: [
          sku({ product_id: 'w-uf', product_code: 'SRTWC8383-RL-UF' }),
          sku({
            product_id: 'w-mbl',
            product_code: 'SRTWC8383-RL-MBL',
            printed_code: 'SRTWC8383-MBL',
            flyer_row: 24,
            cover_codes: ['SRTSCBD823-MBL'],
            extra_segments: ['RL'],
          }),
          sku({
            product_id: 'w-mk',
            product_code: 'SRTWC8383-RL-MK',
            status: 'unresolved',
            reason: 'finish_not_printed',
            printed_code: null,
            flyer_row: null,
            cover_codes: [],
            extra_segments: ['MK', 'RL'],
          }),
          sku({
            product_id: 'w-done',
            product_code: 'SRTWC8383-SH-UF',
            status: 'linked',
          }),
        ],
      },
      {
        family_code: 'SRTWC6015',
        printed: [
          {
            flyer_row: 7,
            wc_code: 'SRTWC6015',
            covers: [{ code: 'SRTSCBD335-UF', found: false }],
          },
        ],
        skus: [],
      },
    ],
    counts: {
      models: 2,
      models_missing: 1,
      covers_missing: 1,
      ready: 2,
      linked: 1,
      unresolved: 1,
      cover_missing: 0,
    },
    seat_cover_spec: {
      registry_id: 'reg-1',
      is_active: true,
      value_count: 11,
      can_retire: canRetire,
    },
  };
}

describe('SeatCoverFlyerReview', () => {
  beforeEach(() => {
    perms.clear();
    perms.add('master_data.products.edit');
    review = makeReview();
    vi.clearAllMocks();
  });
  afterEach(cleanup);

  it('shows each model with its printed codes, SKU mapping and statuses', () => {
    render(<SeatCoverFlyerReview />);
    expect(screen.getAllByText('SRTWC8383').length).toBeGreaterThan(0);
    const mbl = screen.getByTestId('sku-SRTWC8383-RL-MBL');
    expect(within(mbl).getByText('SRTWC8383-MBL')).toBeTruthy();
    expect(within(mbl).getByText('SRTSCBD823-MBL')).toBeTruthy();
    expect(within(mbl).getByText('Ready')).toBeTruthy();
    const mk = screen.getByTestId('sku-SRTWC8383-RL-MK');
    expect(within(mk).getByText('Unresolved')).toBeTruthy();
    expect(within(mk).getByText('Finish not on the flyer')).toBeTruthy();
    expect(
      within(screen.getByTestId('sku-SRTWC8383-SH-UF')).getByText('Linked'),
    ).toBeTruthy();
    expect(screen.getByText('No SKU in the catalogue')).toBeTruthy();
  });

  it('never lets an unresolved or linked SKU be ticked', () => {
    render(<SeatCoverFlyerReview />);
    expect(
      (
        screen.getByRole('checkbox', {
          name: 'Link SRTWC8383-RL-MK',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      (
        screen.getByRole('checkbox', {
          name: 'Link SRTWC8383-SH-UF',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it('the model tick takes every ready SKU and applies only those', async () => {
    render(<SeatCoverFlyerReview />);
    fireEvent.click(
      screen.getByRole('checkbox', {
        name: 'Link every ready SKU of SRTWC8383',
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Link 2 SKUs' }));
    expect(applyMutateAsync).toHaveBeenCalledWith(['w-uf', 'w-mbl']);
  });

  it('a single SKU tick applies that SKU', () => {
    render(<SeatCoverFlyerReview />);
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Link SRTWC8383-RL-MBL' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Link 1 SKU' }));
    expect(applyMutateAsync).toHaveBeenCalledWith(['w-mbl']);
  });

  it('without products edit nothing can be ticked', () => {
    perms.clear();
    render(<SeatCoverFlyerReview />);
    expect(
      (
        screen.getByRole('checkbox', {
          name: 'Link SRTWC8383-RL-UF',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it('retire is disabled until a flyer link exists, then runs the deferred action', () => {
    perms.add('master_data.spec_registry.edit');
    const { unmount } = render(<SeatCoverFlyerReview />);
    expect(screen.getByText('11 products hold a value')).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: /Retire spec/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    unmount();
    review = makeReview(true);
    render(<SeatCoverFlyerReview />);
    fireEvent.click(screen.getByRole('button', { name: /Retire spec/ }));
    expect(retireRun).toHaveBeenCalledWith({
      id: 'reg-1',
      subject: 'Seat Cover spec',
    });
  });

  it('hides retire without the spec registry permission', () => {
    render(<SeatCoverFlyerReview />);
    expect(screen.queryByRole('button', { name: /Retire spec/ })).toBeNull();
  });
});
