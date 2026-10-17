/** S9: a refused threshold marks the field itself; a blank box never reads as 0. */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const hooks = vi.hoisted(() => ({
  useClassification: vi.fn(),
  useSaveClassification: vi.fn(),
}));
vi.mock('../hooks/usePolicies', () => hooks);

import { ClassificationThresholdsPanel } from './ClassificationThresholdsPanel';

const DATA = { abc_a_pct: 0.8, abc_b_pct: 0.15, xyz_x_max: 0.5, xyz_y_max: 1, exists: true };
const mutateAsync = vi.fn();

beforeEach(() => {
  mutateAsync.mockReset().mockResolvedValue(DATA);
  hooks.useClassification.mockReturnValue({ data: DATA, isLoading: false, isError: false });
  hooks.useSaveClassification.mockReturnValue({ mutateAsync, isPending: false });
});

describe('ClassificationThresholdsPanel field marks (S9)', () => {
  it('a blank Class A box is marked aria-invalid with a number sentence under it, nothing saved', () => {
    render(<ClassificationThresholdsPanel />);
    const box = screen.getByDisplayValue('80') as HTMLInputElement;
    fireEvent.change(box, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Save thresholds/i }));
    expect(box.value).toBe('');
    expect(box).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText(/^Enter a (whole )?number/)).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('a blank X ceiling is refused on the box, not read as 0', () => {
    render(<ClassificationThresholdsPanel />);
    const box = screen.getByDisplayValue('0.5') as HTMLInputElement;
    fireEvent.change(box, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Save thresholds/i }));
    expect(box).toHaveAttribute('aria-invalid', 'true');
    expect(mutateAsync).not.toHaveBeenCalled();
  });
});
