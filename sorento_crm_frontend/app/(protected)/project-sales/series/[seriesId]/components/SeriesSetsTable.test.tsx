/**
 * SET-QUOTE-LINE C1: a series prices a SET on its own row, as it prices a product.
 *
 * Mirrors SeriesProductsTable.test.tsx. The floor is whatever the SERVER said (`derived_floor`),
 * a blank price stays blank rather than reading as zero, and the parts read as one " + " line.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SeriesSetRow } from '../../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const getSeriesSetRows = vi.fn();
const updateSeriesSetPricing = vi.fn();
const removeSeriesSet = vi.fn();

vi.mock('../../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../_shared/services/projectService')
  >();
  return {
    ...actual,
    getSeriesSetRows: (...args: unknown[]) => getSeriesSetRows(...args),
    updateSeriesSetPricing: (...args: unknown[]) => updateSeriesSetPricing(...args),
    removeSeriesSet: (...args: unknown[]) => removeSeriesSet(...args),
  };
});

import { SeriesSetsTable } from './SeriesSetsTable';

function row(overrides: Partial<SeriesSetRow> = {}): SeriesSetRow {
  return {
    product_set_id: 'set1',
    set_code: 'CWC1009-RL',
    set_name: 'Water closet set',
    part_codes: ['CWCX1009-RL', 'CWCY1009', 'CWC1009-SC'],
    selling_price: null,
    max_discount_pct: null,
    derived_floor: null,
    ...overrides,
  };
}

function renderTable() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SeriesSetsTable seriesId="s1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  updateSeriesSetPricing.mockResolvedValue(row());
});

describe('SeriesSetsTable', () => {
  it('asks the server for this series only', async () => {
    getSeriesSetRows.mockResolvedValue([]);
    renderTable();
    await waitFor(() => expect(getSeriesSetRows).toHaveBeenCalledWith('s1'));
  });

  it('shows the empty state and still offers a way to add a set', async () => {
    getSeriesSetRows.mockResolvedValue([]);
    renderTable();

    expect(await screen.findByText(/no sets yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add a set/i })).toBeInTheDocument();
  });

  it('renders the set code, name, parts joined with " + " and the server floor', async () => {
    getSeriesSetRows.mockResolvedValue([
      row({ selling_price: '1000.00', max_discount_pct: '10.00', derived_floor: '900.00' }),
    ]);
    renderTable();

    expect(await screen.findByText('Water closet set')).toBeInTheDocument();
    expect(screen.getByText('CWC1009-RL')).toBeInTheDocument();
    expect(screen.getByText('CWCX1009-RL + CWCY1009 + CWC1009-SC')).toBeInTheDocument();
    expect(screen.getByDisplayValue('1000.00')).toBeInTheDocument();
    expect(screen.getByDisplayValue('10.00')).toBeInTheDocument();
    expect(screen.getByText('RM 900.00')).toBeInTheDocument();
  });

  it('leaves an unpriced set EMPTY rather than showing zero', async () => {
    getSeriesSetRows.mockResolvedValue([row()]);
    renderTable();

    await screen.findByText('Water closet set');
    expect(screen.queryByDisplayValue('0')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue('0.00')).not.toBeInTheDocument();
  });

  it('saves an edited price through updateSeriesSetPricing', async () => {
    getSeriesSetRows.mockResolvedValue([
      row({ selling_price: '1000.00', max_discount_pct: '10.00', derived_floor: '900.00' }),
    ]);
    renderTable();

    const price = await screen.findByDisplayValue('1000.00');
    fireEvent.change(price, { target: { value: '1200.00' } });
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(updateSeriesSetPricing).toHaveBeenCalledWith('s1', 'set1', {
        selling_price: '1200.00',
        max_discount_pct: '10.00',
      }),
    );
  });
});
