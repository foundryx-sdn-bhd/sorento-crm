import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

const createMutate = vi.hoisted(() => vi.fn());
vi.mock('../hooks/useContactTags', () => ({
  useContactTagMutations: () => ({
    create: { mutate: createMutate, isPending: false },
    update: { mutate: vi.fn(), isPending: false },
  }),
}));

import ContactTagFormDialog from './ContactTagFormDialog';

afterEach(() => {
  cleanup();
  createMutate.mockReset();
});

describe('ContactTagFormDialog sort order', () => {
  it('keeps Create live with a blank sort order; the click says what is wrong and sends nothing', () => {
    render(<ContactTagFormDialog open onOpenChange={() => {}} tag={null} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Dealer' } });
    const sort = screen.getByLabelText('Sort order');
    fireEvent.change(sort, { target: { value: '' } });

    const create = screen.getByRole('button', { name: 'Create' });
    expect(create).toBeEnabled();
    fireEvent.click(create);
    expect(screen.getByText('Enter a whole number.')).toBeInTheDocument();
    expect(sort).toHaveValue(null);
    expect(createMutate).not.toHaveBeenCalled();
  });
});
