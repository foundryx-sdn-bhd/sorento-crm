/** Point ratio (lane SALES-TARGET-DEALER, UAC D4-4): the dated history and adding a ratio. */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('@/lib/helpers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/helpers')>()),
  todayMalaysiaYyyyMmDd: () => '2026-10-01',
}));
vi.mock('@/components/ui/date-picker', () => ({
  DatePicker: () => <span data-testid="date-picker" />,
}));

const save = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const ratios = vi.hoisted(() => ({
  rows: [
    { id: 'r2', effective_from: '2026-10-01', rm_per_point: 1.3 },
    { id: 'r1', effective_from: '2026-01-01', rm_per_point: 1.2 },
  ],
}));
vi.mock('../hooks/useSalesTargets', () => ({
  usePointRatios: () => ({ data: ratios, isLoading: false }),
  useSavePointRatio: () => save,
}));

import PointRatioDialog from './PointRatioDialog';

beforeEach(() => {
  save.mutateAsync.mockReset();
  save.mutateAsync.mockResolvedValue({ id: 'r3' });
});

describe('PointRatioDialog', () => {
  it('lists the ratios newest first', () => {
    render(<PointRatioDialog open onOpenChange={() => {}} />);
    const rows = within(screen.getByRole('table', { name: 'Point ratios' })).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual(['1 Oct 20261.3', '1 Jan 20261.2']);
  });

  it('adds a ratio from a date; zero is refused', async () => {
    render(<PointRatioDialog open onOpenChange={() => {}} />);
    const add = screen.getByRole('button', { name: 'Add' }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText('RM per point'), { target: { value: '0' } });
    expect(add.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('RM per point'), { target: { value: '1.35' } });
    fireEvent.click(add);
    await waitFor(() =>
      expect(save.mutateAsync).toHaveBeenCalledWith({ effective_from: '2026-10-01', rm_per_point: 1.35 }),
    );
  });
});
