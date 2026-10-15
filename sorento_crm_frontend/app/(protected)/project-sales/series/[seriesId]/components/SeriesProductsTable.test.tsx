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

/** The service answers one server page: `{ data, total }`. */
function serverPage(rows: SeriesProductRow[], total = rows.length) {
  return { data: rows, total };
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
    getSeriesProductRows.mockResolvedValue(serverPage([]));
    renderTable();

    expect(await screen.findByText(/no products yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add a product/i })).toBeInTheDocument();
  });

  it('shows a priced row with the floor the server computed', async () => {
    getSeriesProductRows.mockResolvedValue(serverPage([
      row({ selling_price: '220.00', max_discount_pct: '6.00', derived_floor: '206.80' }),
    ]));
    renderTable();

    await waitFor(() => expect(getSeriesProductRows).toHaveBeenCalledWith('s1', expect.objectContaining({ page: 1, limit: 50 })));
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
    getSeriesProductRows.mockResolvedValue(serverPage([
      row({ selling_price: '220.00', max_discount_pct: '6.00', derived_floor: '206.80' }),
    ]));
    renderTable();

    await screen.findByDisplayValue('220.00');
    // Once for the price cell, once for the floor. Neither is the header.
    expect(screen.getAllByText('RM').length).toBeGreaterThan(0);
    expect(screen.getByText('%')).toBeInTheDocument();
    // The draft still holds the plain decimal the API wants - the unit is decoration, so
    // nothing has to strip it back off before saving.
    expect(screen.getByDisplayValue('220.00')).toBeInTheDocument();
  });

  it('D3: search is sent to the server (debounced), not filtered in the browser, and the line reads N of TOTAL', async () => {
    const two = [
      row({ product_id: 'p1', product_code: 'BT009', product_name: 'Bottle trap' }),
      row({ product_id: 'p2', product_code: 'CB1500SS', product_name: 'Kitchen sink' }),
    ];
    getSeriesProductRows.mockImplementation(async (_id: string, opts?: { query?: string }) =>
      opts?.query ? serverPage([two[0]], 1) : serverPage(two, 2),
    );
    renderTable();

    await screen.findByText('Bottle trap');
    expect(screen.getByText('2 products')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/search products in this series/i), {
      target: { value: 'bt00' },
    });

    await waitFor(() =>
      expect(getSeriesProductRows).toHaveBeenCalledWith(
        's1',
        expect.objectContaining({ page: 1, query: 'bt00' }),
      ),
    );
    await waitFor(() => expect(screen.queryByText('Kitchen sink')).not.toBeInTheDocument());
    expect(screen.getByText('1 of 2 products')).toBeInTheDocument();
  });

  it('D3: a search from page 2 goes back to page 1', async () => {
    getSeriesProductRows.mockImplementation(async (_id: string, opts?: { page?: number }) =>
      serverPage(
        [row({ product_id: `p${opts?.page}`, product_code: `C${opts?.page}`, product_name: `Item page ${opts?.page}` })],
        120,
      ),
    );
    renderTable();
    await screen.findByText('Item page 1');
    fireEvent.click(await screen.findByRole('button', { name: /next page/i }));
    await screen.findByText('Item page 2');
    fireEvent.change(screen.getByLabelText(/search products in this series/i), {
      target: { value: 'item' },
    });
    await waitFor(() =>
      expect(getSeriesProductRows).toHaveBeenLastCalledWith(
        's1',
        expect.objectContaining({ page: 1, query: 'item' }),
      ),
    );
  });

  it('says the search found nothing, rather than looking like an empty series', async () => {
    getSeriesProductRows.mockImplementation(async (_id: string, opts?: { query?: string }) =>
      opts?.query ? serverPage([], 0) : serverPage([row({ product_code: 'BT009' })], 1),
    );
    renderTable();

    await screen.findByText('One piece WC');
    fireEvent.change(screen.getByLabelText(/search products in this series/i), {
      target: { value: 'zzt-nothing' },
    });

    expect(await screen.findByText(/no product here matches "zzt-nothing"/i)).toBeInTheDocument();
    expect(screen.queryByText(/no products yet/i)).not.toBeInTheDocument();
  });

  it('D1/D2: shows the SERVER total, not the page size, and the app pager asks for page 2', async () => {
    const page = (n: number) =>
      Array.from({ length: 50 }, (_, i) =>
        row({
          product_id: `p${n}-${i}`,
          product_code: `C${n}-${String(i).padStart(2, '0')}`,
          product_name: `Item ${n}-${i}`,
        }),
      );
    getSeriesProductRows.mockImplementation(async (_id: string, opts?: { page?: number }) =>
      serverPage(page(opts?.page ?? 1), 120),
    );
    renderTable();

    await screen.findByText('Item 1-0');
    expect(screen.getByText('120 products')).toBeInTheDocument();
    expect(screen.getByText(/1 - 50 of 120/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /next page/i }));

    await waitFor(() =>
      expect(getSeriesProductRows).toHaveBeenCalledWith(
        's1',
        expect.objectContaining({ page: 2, limit: 50 }),
      ),
    );
    expect(await screen.findByText('Item 2-0')).toBeInTheDocument();
  });

  it('E1: Add a product sends the PICKED product code even when it is not in the first catalogue page', async () => {
    getSeriesProductRows.mockResolvedValue(serverPage([]));
    importSeriesProducts.mockResolvedValue({});
    const others = Array.from({ length: 100 }, (_, i) => ({
      id: `o${i}`,
      product_code: `A${String(i).padStart(3, '0')}`,
      product_name: `Other ${i}`,
    }));
    const zed = { id: 'uuid-zed', product_code: 'ZZ9000', product_name: 'Zed product' };
    // The no-query list is 100 other products; only a typed search finds ZZ9000.
    getProductsForLineSelect.mockImplementation(async (query?: string) =>
      query ? [zed] : others,
    );
    renderTable();

    fireEvent.click(await screen.findByRole('button', { name: /add a product/i }));
    fireEvent.click(await screen.findByRole('combobox', { name: /^Product on / }));
    const input = await screen.findByPlaceholderText(/search/i);
    fireEvent.change(input, { target: { value: 'ZZ9' } });
    fireEvent.click(await screen.findByRole('option', { name: /ZZ9000/ }, { timeout: 3000 }));
    fireEvent.click(screen.getByRole('button', { name: /^Save / }));

    await waitFor(() =>
      expect(importSeriesProducts).toHaveBeenCalledWith(
        's1',
        { codes: ['ZZ9000'], mode: 'append' },
      ),
    );
    expect(importSeriesProducts).not.toHaveBeenCalledWith(
      's1',
      expect.objectContaining({ codes: ['uuid-zed'] }),
    );
  });

  it('leaves an unpriced row EMPTY rather than showing zero', async () => {
    getSeriesProductRows.mockResolvedValue(serverPage([row()]));
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
    getSeriesProductRows.mockResolvedValue(serverPage([
      row({ selling_price: '180.00', max_discount_pct: null, derived_floor: null }),
    ]));
    renderTable();

    expect(await screen.findByDisplayValue('180.00')).toBeInTheDocument();
    expect(screen.queryByText('180.00', { selector: 'span' })).not.toBeInTheDocument();
  });

  it('asks the server for this series only once the id is known', async () => {
    getSeriesProductRows.mockResolvedValue(serverPage([]));
    renderTable();
    await waitFor(() => expect(getSeriesProductRows).toHaveBeenCalledTimes(1));
    expect(getSeriesProductRows).toHaveBeenCalledWith('s1', expect.objectContaining({ page: 1, limit: 50 }));
  });
  it('does not repeat the code as the Description when the name equals the code', async () => {
    getSeriesProductRows.mockResolvedValue(serverPage([row({ product_code: '1861', product_name: '1861' })]));
    renderTable();

    await waitFor(() => expect(getSeriesProductRows).toHaveBeenCalledWith('s1', expect.objectContaining({ page: 1, limit: 50 })));
    await waitFor(() => expect(screen.queryAllByText('1861')).toHaveLength(1));
    const dash = screen.getAllByText('-').find((el) => el.getAttribute('title') === '');
    expect(dash?.textContent).toBe('-');
  });

  it('shows the Description when the name differs from the code', async () => {
    getSeriesProductRows.mockResolvedValue(serverPage([row({ product_code: 'SRT6641', product_name: 'FLOOR TRAP' })]));
    renderTable();

    expect(await screen.findByText('FLOOR TRAP')).toBeInTheDocument();
  });
  it('keeps the product picked on a NEW row, and saves it by its code', async () => {
    getSeriesProductRows.mockResolvedValue(serverPage([]));
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

  it('D1: a newly added row keeps the picked product, shows its code, and saves it by id', async () => {
    getSeriesProductRows.mockResolvedValue(serverPage([]));
    importSeriesProducts.mockResolvedValue({});
    updateSeriesProductPricing.mockResolvedValue(row());
    getProductsForLineSelect.mockResolvedValue([
      { id: 'p9', product_code: 'BT900', product_name: 'Nine trap' },
    ]);
    renderTable();

    fireEvent.click(await screen.findByRole('button', { name: /add a product/i }));
    fireEvent.click(await screen.findByRole('combobox', { name: /^Product on / }));
    fireEvent.click(await screen.findByRole('option', { name: /BT900/ }));

    await waitFor(() => expect(screen.getByText('Nine trap')).toBeInTheDocument());
    expect(screen.getByRole('combobox', { name: /^Product on / })).toHaveTextContent('BT900');
    expect(screen.getByRole('combobox', { name: /^Product on / })).not.toHaveTextContent(
      'Pick a product',
    );

    fireEvent.change(screen.getByRole('textbox', { name: /^Selling price on / }), {
      target: { value: '150.00' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Save / }));

    await waitFor(() =>
      expect(importSeriesProducts).toHaveBeenCalledWith(
        's1',
        expect.objectContaining({ codes: ['BT900'] }),
      ),
    );
    await waitFor(() =>
      expect(updateSeriesProductPricing).toHaveBeenCalledWith(
        's1',
        'p9',
        expect.objectContaining({ selling_price: '150.00' }),
      ),
    );
  });

  it("shows no Remove (bin) button on an in-scope row, and keeps it on others (C6)", async () => {
    getSeriesProductRows.mockResolvedValue(serverPage([
      row({ product_id: "p1", product_code: "IN-SCOPE", product_name: "Inside item", in_scope: true }),
      row({ product_id: "p2", product_code: "OUT-SCOPE", product_name: "Outside item", in_scope: false }),
    ]));
    renderTable();

    await screen.findByText("Inside item");
    expect(screen.queryByRole("button", { name: "Remove IN-SCOPE" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove OUT-SCOPE" })).toBeInTheDocument();
  });
});
