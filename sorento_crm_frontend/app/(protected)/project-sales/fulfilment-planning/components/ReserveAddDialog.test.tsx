/**
 * S3 - Reserve add-location (AC-3.1, AC-3.2): "any location with free stock, the site pool
 * included, can be added to Reserve by hand; the server's on-hand check stays the guard."
 *
 * `locations` arrives ALREADY FILTERED to what is left to add (see the prop's own doc on
 * `ReserveAddDialog.tsx`) - the caller (`BoardLineDecisionPanel`) is what drops a zero-free
 * or already-added row. What this dialog owns, and what is pinned here, is the SORT (site
 * pool first, per R-A - the same order the walk itself asks in), what a pick seeds the
 * quantity box to, and the empty state when the caller hands it nothing.
 */
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { BoardCellLocation } from '../../_shared/types/fulfilmentPlanning.types';

import { ReserveAddDialog } from './ReserveAddDialog';

/** The site pool: the ladder asks it FIRST for every product (R-A), and the lightbox's own
 * "Available for Project" (R-K) rides on this exact row (BRW 47 free reads 23). */
const SITE_POOL: BoardCellLocation = {
  location: 'BRW',
  warehouse_id: 'wh-brw',
  where: 'site_pool',
  qty: '0',
  qty_free: '47',
  available_for_project: '23',
};

/** The line's own bin. No `available_for_project` - that figure is a pool-only reading. */
const OWN: BoardCellLocation = {
  location: 'BRW-AM',
  warehouse_id: 'wh-brw-am',
  where: 'own',
  qty: '0',
  qty_free: '9',
};

/** A group member, ranked after the pool and the own location. */
const GROUP: BoardCellLocation = {
  location: 'DC1-AM',
  warehouse_id: 'wh-dc1-am',
  where: 'group',
  qty: '0',
  qty_free: '15',
};

const onAdd = vi.fn();
const onDone = vi.fn();

function renderDialog(
  locations: BoardCellLocation[],
  openRemainder = '24',
) {
  return render(
    <ReserveAddDialog
      lineNo={22}
      itemCode="SRTWB7518"
      locations={locations}
      openRemainder={openRemainder}
      onDone={onDone}
      onAdd={onAdd}
    />,
  );
}

function rows(): HTMLElement[] {
  return within(screen.getByTestId('reserve-location-table')).getAllByRole('row');
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ReserveAddDialog: the candidate order (R-A, AC-3.1)', () => {
  it('sorts the site pool first, then own, then group - whatever order the caller handed them in', () => {
    renderDialog([OWN, GROUP, SITE_POOL]);

    const table = rows();
    expect(table[1]).toHaveAttribute('data-testid', 'reserve-location-BRW');
    expect(table[2]).toHaveAttribute('data-testid', 'reserve-location-BRW-AM');
    expect(table[3]).toHaveAttribute('data-testid', 'reserve-location-DC1-AM');
  });

  it('states the site pool’s Available for Project, and "Not stated" on a location the server sent none for', () => {
    renderDialog([OWN, SITE_POOL]);

    expect(
      screen.getByTestId('reserve-cell-available-for-project-BRW'),
    ).toHaveTextContent('23');
    expect(
      screen.getByTestId('reserve-cell-available-for-project-BRW-AM'),
    ).toHaveTextContent('Not stated');
  });

  it('states a group row s own Available as its Available for Project (D2, S2 fix round 5)', () => {
    // Outside a site pool nothing is kept back, so Available for Project reads the same
    // signed figure `CellStockTable`'s own column reads for the row - never "Not stated"
    // over a number the cell it was added from already states.
    const groupWithAvailable: BoardCellLocation = {
      ...GROUP,
      available_qty: '15',
    };
    renderDialog([OWN, groupWithAvailable]);

    expect(
      screen.getByTestId('reserve-cell-available-for-project-DC1-AM'),
    ).toHaveTextContent('15');
  });

  it('states each location’s free quantity beside it', () => {
    renderDialog([OWN, SITE_POOL]);

    expect(screen.getByTestId('reserve-cell-free-BRW')).toHaveTextContent('47');
    expect(screen.getByTestId('reserve-cell-free-BRW-AM')).toHaveTextContent('9');
  });

  it('names where each row stands', () => {
    renderDialog([OWN, SITE_POOL, GROUP]);

    expect(screen.getByText('Site pool')).toBeInTheDocument();
    expect(screen.getByText('Own')).toBeInTheDocument();
    expect(screen.getByText('Group')).toBeInTheDocument();
  });

  it('reads "Unknown" for a row the server sent no location name for, never the warehouse id (N3, no UUID in the UI)', () => {
    const NAMELESS: BoardCellLocation = {
      location: null,
      warehouse_id: 'a3f1c9e2-6b40-4d1a-9d0e-8f2c4b1a7d55',
      where: 'own',
      qty: '0',
      qty_free: '5',
    };
    renderDialog([NAMELESS]);

    expect(screen.getByText('Unknown')).toBeInTheDocument();
    expect(
      screen.queryByText('a3f1c9e2-6b40-4d1a-9d0e-8f2c4b1a7d55'),
    ).not.toBeInTheDocument();
  });
});

describe('ReserveAddDialog: picking a row seeds the quantity (AC-3.2)', () => {
  it('opens on the first (ranked) candidate, seeded to min(open remainder, its free stock)', () => {
    renderDialog([OWN, SITE_POOL], '24');

    // BRW is ranked first (site pool); its free is 47, so the remainder (24) wins.
    expect(within(screen.getByTestId('reserve-location-BRW')).getByRole('radio')).toBeChecked();
    expect(screen.getByLabelText('Quantity')).toHaveValue(24);
  });

  it('re-seeds the quantity to the newly picked location’s own cap when it is smaller', () => {
    renderDialog([OWN, SITE_POOL], '24');

    fireEvent.click(within(screen.getByTestId('reserve-location-BRW-AM')).getByRole('radio'));

    // BRW-AM's free (9) is smaller than the 24 remainder, so it caps the box.
    expect(screen.getByLabelText('Quantity')).toHaveValue(9);
  });

  it('falls back to the location’s own free stock when nothing is left to cover', () => {
    renderDialog([SITE_POOL], '0');

    expect(screen.getByLabelText('Quantity')).toHaveValue(47);
  });

  it('hands back the picked location and the typed quantity, then closes', () => {
    renderDialog([OWN, SITE_POOL], '24');

    fireEvent.click(within(screen.getByTestId('reserve-location-BRW-AM')).getByRole('radio'));
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add the location' }));

    expect(onAdd).toHaveBeenCalledWith(OWN, '5');
    expect(onDone).toHaveBeenCalled();
  });

  it('will not add on a zero, negative or blank quantity - Add stays enabled and says why', () => {
    renderDialog([SITE_POOL], '24');

    // NUMBER-FIELDS: the button is never disabled without a reason; pressing it names the gap.
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '0' } });
    expect(screen.getByRole('button', { name: 'Add the location' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Add the location' }));
    expect(screen.getByText(/^Enter a number/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '-3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add the location' }));
    expect(screen.getByText(/^Enter a number/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add the location' }));
    expect(screen.getByText(/^Enter a number/)).toBeInTheDocument();
    expect(onAdd).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });
});

describe('ReserveAddDialog: nothing left to add', () => {
  it('states there is nowhere left to reserve from, rather than an empty table (AC-3.1)', () => {
    renderDialog([]);

    expect(screen.getByTestId('reserve-location-empty')).toHaveTextContent(
      'No location holds free stock of this item',
    );
    expect(screen.queryByTestId('reserve-location-table')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add the location' })).toBeDisabled();
  });
});

describe('ReserveAddDialog: closing without adding', () => {
  it('closes on Cancel, and adds nothing', () => {
    renderDialog([SITE_POOL]);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onDone).toHaveBeenCalled();
    expect(onAdd).not.toHaveBeenCalled();
  });
});
