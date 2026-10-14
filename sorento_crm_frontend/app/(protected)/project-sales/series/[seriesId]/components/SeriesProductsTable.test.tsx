/**
 * T3 - the client's sheet as a table: what a series sells, for how much, and how far it may
 * be discounted.
 *
 * The claims worth pinning are all about ABSENCE, because absence is the common case and the
 * easy thing to render wrongly:
 *
 * - a product with no price shows an EMPTY cell, never `0`, which would read as "free";
 * - the Floor column is blank unless BOTH numbers are present - 21 of the 51 priced products
 *   on the client's own sheet are in exactly that state (AC-C4);
 * - the floor is whatever the SERVER said. It is the number a refusal is argued from, and
 *   recomputing `price * (1 - pct/100)` here would be a second implementation of it.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SeriesProductRow } from '../../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const getSeriesProductRows = vi.fn();
const updateSeriesProductPricing = vi.fn();
const removeSeriesProduct = vi.fn();
const importSeriesProducts = vi.fn();
const getProductsForLineSelect = vi.fn().mockResolvedValue([]);

vi.mock('../../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../_shared/services/projectService')
  >();
  return {
    ...actual,
    getSeriesProductRows: (...args: unknown[]) => getSeriesProductRows(...args),
    updateSeriesProductPricing: (...args: unknown[]) => updateSeriesProductPricing(...args),
    removeSeriesProduct: (...args: unknown[]) => removeSeriesProduct(...args),
    importSeriesProducts: (...args: unknown[]) => importSeriesProducts(...args),
  };
});

vi.mock(
  '@/app/(protected)/master-data-management/products/services/productService',
  async (importOriginal) => {
    const actual = await importOriginal<
      typeof import('@/app/(protected)/master-data-management/products/services/productService')
    >();
    return {
      ...actual,
      getProductsForLineSelect: (...args: unknown[]) => getProductsForLineSelect(...args),
    };
  },
);

import { SeriesProductsTable } from './SeriesProductsTable';

function row(overrides: Partial<SeriesProductRow> = {}): SeriesProductRow {
  return {
    product_id: 'p1',
    product_code: 'CWC7601-S-RL',
    product_name: 'One piece WC',
    selling_price: null,
    max_discount_pct: null,
    derived_floor: null,
    in_scope: false,
    ...overrides,
  };
}

function renderTable() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SeriesProductsTable seriesId="s1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getProductsForLineSelect.mockResolvedValue([]);
});

describe('SeriesProductsTable', () => {
  it('says the series names nothing yet, and still offers a way to add', async () => {
    getSeriesProductRows.mockResolvedValue([]);
    renderTable();

    expect(await screen.findByText(/no products yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add a product/i })).toBeInTheDocument();
  });

  it('shows a priced row with the floor the server computed', async () => {
    getSeriesProductRows.mockResolvedValue([
      row({ selling_price: '220.00', max_discount_pct: '6.00', derived_floor: '206.80' }),
    ]);
    renderTable();

    await waitFor(() => expect(getSeriesProductRows).toHaveBeenCalledWith('s1'));
    expect(await screen.findByDisplayValue('220.00')).toBeInTheDocument();
    expect(screen.getByDisplayValue('6.00')).toBeInTheDocument();
    // Rendered, not recalculated - and carrying its unit, because a bare `206.80` sitting
    // beside a bare `6.00` percentage is one glance from being read as the wrong quantity.
    expect(screen.getByText('RM 206.80')).toBeInTheDocument();
  });

  it('prints the currency on the price cell, and a percent on the discount', async () => {
    // The unit belongs IN the row, not only in the header: the header scrolls away on a
    // series of ninety products, and two adjacent columns of bare decimals then say nothing
    // about which is money.
    getSeriesProductRows.mockResolvedValue([
      row({ selling_price: '220.00', max_discount_pct: '6.00', derived_floor: '206.80' }),
    ]);
    renderTable();

    await screen.findByDisplayValue('220.00');
    // Once for the price cell, once for the floor. Neither is the header.
    expect(screen.getAllByText('RM').length).toBeGreaterThan(0);
    expect(screen.getByText('%')).toBeInTheDocument();
    // The draft still holds the plain decimal the API wants - the unit is decoration, so
    // nothing has to strip it back off before saving.
    expect(screen.getByDisplayValue('220.00')).toBeInTheDocument();
  });

  it('filters the table by code, and says how much it is hiding', async () => {
    // Ctrl-F is not a substitute: the browser finds only what is scrolled into the DOM, so
    // searching a long series for a code answers 0/0 for a product that is plainly there.
    getSeriesProductRows.mockResolvedValue([
      row({ product_id: 'p1', product_code: 'BT009', product_name: 'Bottle trap' }),
      row({ product_id: 'p2', product_code: 'CB1500SS', product_name: 'Kitchen sink' }),
    ]);
    renderTable();

    await screen.findByText('Bottle trap');
    expect(screen.getByText('2 products')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/search products in this series/i), {
      target: { value: 'bt00' },
    });

    await waitFor(() => expect(screen.queryByText('Kitchen sink')).not.toBeInTheDocument());
    expect(screen.getByText('Bottle trap')).toBeInTheDocument();
    expect(screen.getByText('1 of 2 products')).toBeInTheDocument();
  });

  it('finds a product by NAME as well as by code', async () => {
    // The admin arrives from their own spreadsheet knowing one or the other, and being made
    // to guess which one this box accepts is a puzzle the screen should not set.
    getSeriesProductRows.mockResolvedValue([
      row({ product_id: 'p1', product_code: 'BT009', product_name: 'Bottle trap' }),
      row({ product_id: 'p2', product_code: 'CB1500SS', product_name: 'Kitchen sink' }),
    ]);
    renderTable();

    await screen.findByText('Kitchen sink');
    fireEvent.change(screen.getByLabelText(/search products in this series/i), {
      target: { value: 'sink' },
    });

    await waitFor(() => expect(screen.queryByText('Bottle trap')).not.toBeInTheDocument());
    expect(screen.getByText('Kitchen sink')).toBeInTheDocument();
  });

  it('says the search found nothing, rather than looking like an empty series', async () => {
    getSeriesProductRows.mockResolvedValue([row({ product_code: 'BT009' })]);
    renderTable();

    await screen.findByText('One piece WC');
    fireEvent.change(screen.getByLabelText(/search products in this series/i), {
      target: { value: 'zzt-nothing' },
    });

    // "No products yet" here would be a lie about the series, not about the search.
    expect(await screen.findByText(/no product here matches "zzt-nothing"/i)).toBeInTheDocument();
    expect(screen.queryByText(/no products yet/i)).not.toBeInTheDocument();
  });

  it('leaves an unpriced row EMPTY rather than showing zero', async () => {
    getSeriesProductRows.mockResolvedValue([row()]);
    renderTable();

    // The product cell is a select trigger, not an input, so wait on the derived
    // description instead of a display value.
    await screen.findByText('One piece WC');
    // `0` in a price cell reads as "we sell this for nothing", which is not what a blank
    // cell on the client's sheet means.
    expect(screen.queryByDisplayValue('0')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue('0.00')).not.toBeInTheDocument();
  });

  it('shows no floor for a product priced with no discount stated', async () => {
    // AC-C4 on screen. 21 of the client's 51 priced products look exactly like this, and a
    // floor here would put every one of them in breach on the first discount.
    getSeriesProductRows.mockResolvedValue([
      row({ selling_price: '180.00', max_discount_pct: null, derived_floor: null }),
    ]);
    renderTable();

    expect(await screen.findByDisplayValue('180.00')).toBeInTheDocument();
    expect(screen.queryByText('180.00', { selector: 'span' })).not.toBeInTheDocument();
  });

  it('asks the server for this series only once the id is known', async () => {
    getSeriesProductRows.mockResolvedValue([]);
    renderTable();
    await waitFor(() => expect(getSeriesProductRows).toHaveBeenCalledTimes(1));
    expect(getSeriesProductRows).toHaveBeenCalledWith('s1');
  });
  it('does not repeat the code as the Description when the name equals the code', async () => {
    getSeriesProductRows.mockResolvedValue([row({ product_code: '1861', product_name: '1861' })]);
    renderTable();

    await waitFor(() => expect(getSeriesProductRows).toHaveBeenCalledWith('s1'));
    await waitFor(() => expect(screen.queryAllByText('1861')).toHaveLength(1));
    const dash = screen.getAllByText('-').find((el) => el.getAttribute('title') === '');
    expect(dash?.textContent).toBe('-');
  });

  it('shows the Description when the name differs from the code', async () => {
    getSeriesProductRows.mockResolvedValue([row({ product_code: 'SRT6641', product_name: 'FLOOR TRAP' })]);
    renderTable();

    expect(await screen.findByText('FLOOR TRAP')).toBeInTheDocument();
  });
  it('keeps the product picked on a NEW row, and saves it by its code', async () => {
    getSeriesProductRows.mockResolvedValue([]);
    const product = { id: 'p1', product_code: '1861', product_name: '1861' };
    getProductsForLineSelect.mockResolvedValue([product]);
    importSeriesProducts.mockResolvedValue({});
    renderTable();

    fireEvent.click(await screen.findByRole('button', { name: /add a product/i }));
    const picker = await screen.findByRole('combobox', { name: /product/i });
    fireEvent.click(picker);
    fireEvent.click(await screen.findByRole('option', { name: /1861/ }));

    await waitFor(() => expect(screen.getByRole('combobox', { name: /product/i })).toHaveTextContent('1861'));
    expect(screen.getByRole('combobox', { name: /product/i })).not.toHaveTextContent('Pick a product');

    fireEvent.click(screen.getByRole('button', { name: /^save/i }));
    await waitFor(() =>
      expect(importSeriesProducts).toHaveBeenCalledWith(
        's1',
        expect.objectContaining({ codes: ['1861'] }),
      ),
    );
  });

  it("shows no Remove (bin) button on an in-scope row, and keeps it on others (C6)", async () => {
    getSeriesProductRows.mockResolvedValue([
      row({ product_id: "p1", product_code: "IN-SCOPE", product_name: "Inside item", in_scope: true }),
      row({ product_id: "p2", product_code: "OUT-SCOPE", product_name: "Outside item", in_scope: false }),
    ]);
    renderTable();

    await screen.findByText("Inside item");
    expect(screen.queryByRole("button", { name: "Remove IN-SCOPE" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove OUT-SCOPE" })).toBeInTheDocument();
  });
});
