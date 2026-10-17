import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import userEvent from '@testing-library/user-event';

// STOCK-STATUS: the panel has its own suite; here only that the dialog opens it for the row.
const stockStatusPanelSpy = vi.fn();
vi.mock('@/components/stock-status/StockStatusPanel', () => ({
  StockStatusPanel: (props: Record<string, unknown>) => {
    stockStatusPanelSpy(props);
    return <div data-testid="stock-status-panel" />;
  },
}));

import { ContainerRequestRowDialog } from './ContainerRequestRowDialog';
import type { ContainerRequestRow } from '../../services/fulfilmentService';

function row(over: Partial<ContainerRequestRow> = {}): ContainerRequestRow {
  const merged: ContainerRequestRow = {
    // F12: an ordinary product row. A set row sets `row_kind`, `product_set_id` and the
    // driver fields; `row_key` follows `product_id` unless a test names its own.
    row_key: 'p1',
    row_kind: 'product' as const,
    product_set_id: null,
    set_code: null,
    set_name: null,
    driver_product_id: null,
    driver_item_code: null,
    driver_product_name: null,
    product_id: 'p1',
    item_code: 'SRTWB241',
    product_name: 'Wall hung basin 600mm',
    open_so_need: 3999,
    suggested_qty: 2426,
    engine_qty: 2426,
    on_hand: 1573,
    on_hand_group: 640,
    incoming_spo: 0,
    incoming_spo_group: 0,
    incoming_pl: 600,
    incoming_pl_shipments: [
      { shipment_id: 's1', shipment_number: null, estimated_arrival_date: null, qty: 400 },
      {
        shipment_id: 's2',
        shipment_number: 'FSCU8103365',
        estimated_arrival_date: '2026-07-27',
        qty: 200,
      },
    ],
    outstanding_po: 297,
    outstanding_po_lines: [
      { po_number: '202607-S0105', expected_date: '2026-09-30', qty: 297 },
    ],
    sites: [
      { warehouse_code: 'BRW', on_hand: 1573, incoming_spo: 0 },
      { warehouse_code: 'MWH', on_hand: 0, incoming_spo: 0 },
      { warehouse_code: 'WH3', on_hand: 0, incoming_spo: 0 },
    ],
    group_locations: {
      count: 12,
      on_hand: 640,
      incoming_spo: 0,
      warehouse_codes: ['BRW-BB', 'DC1-BB'],
    },
    project_qty: 2229,
    retail_qty: 1770,
    unclassified_qty: 0,
    earliest_required_date: '2024-11-20',
    so_count: 37,
    holding_source: 'stock_list' as const,
    holding_qty: 522,
    holding_as_of: null,
    holding_blocks: 0,
    blocks: [],
    qty_packed: 522,
    qty_unfinished: 1411,
    cbm_per_unit: 0.11,
    row_as_of: '2026-08-21',
    rank: 1,
    rank_score: 0.94,
    rank_factors: [],
    has_demand: true,
    ...over,
  };
  // Keyed off whatever `product_id` ended up being, so two rows in one test never collide
  // on the grid's row id just because only one of them named a key.
  return { ...merged, row_key: over.row_key ?? merged.product_id };
}

function renderDialog(over: Partial<ContainerRequestRow> = {}, askQty = 2426) {
  return render(
    <ContainerRequestRowDialog
      row={row(over)}
      askQty={askQty}
      soLines={[
        {
          product_id: 'p1',
          item_code: 'SRTWB241',
          so_number: 'SO381895',
          customer_label: 'L4 TUJU RESIDENCE',
          project_title: 'Tuju Residence',
          agent_label: 'Wong Mei Ling',
          unit_price: 12.5,
          demand_class: 'project',
          order_date: '2026-05-01',
          required_date: '2026-08-19',
          open_qty: 120,
          qty: 120,
        },
      ]}
      history={undefined}
      historyLoading={false}
      onClose={vi.fn()}
    />,
  );
}

describe('ContainerRequestRowDialog', () => {
  it('STOCK-STATUS: a Stock status tab opens the panel for the row item, beside the request', async () => {
    renderDialog();
    expect(screen.queryByTestId('stock-status-panel')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: 'Stock status' }));
    expect(screen.getByTestId('stock-status-panel')).toBeInTheDocument();
    expect(stockStatusPanelSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ productId: 'p1', showOpenFullPage: true }),
    );
  });

  it('opens on what is needed and what is being asked for (AC-A2.3)', () => {
    renderDialog();

    const needed = screen.getByTestId('row-quantity-needed');
    expect(needed).toHaveTextContent('3,999');
    expect(needed).toHaveTextContent('Project 2,229');
    expect(needed).toHaveTextContent('Retail 1,770');
    expect(needed).toHaveTextContent('37 open sales orders');

    const suggestion = screen.getByTestId('row-suggestion');
    expect(suggestion).toHaveTextContent('2,426');
    // The arithmetic, spelled out (AC-B5).
    expect(suggestion).toHaveTextContent('need 3,999 - on hand 1,573 - SPO 0 = 2,426');
    // Gone: "Incoming PL 600 and outstanding PO 297 are not deducted" was a sentence
    // teaching the reader the rule, which the UI does not do (PRINCIPLES design mandates).
    // The two figures keep their own section further down, labelled, not explained.
    expect(suggestion).not.toHaveTextContent('not deducted');
    expect(suggestion).toHaveTextContent('They hold 522 packed');
  });

  it('reads the stand-in proforma quantity, not the zeroed packed field', () => {
    // The backend zeroes `qty_packed` on a proforma-sourced row, so the dialog said "They
    // hold 0" under a grid cell reading 400.
    renderDialog({
      holding_source: 'proforma',
      holding_qty: 400,
      holding_as_of: '2026-07-31',
      qty_packed: 0,
      qty_unfinished: 0,
    });

    expect(screen.getByTestId('row-suggestion')).toHaveTextContent('They hold 400 on PI');
  });

  it('says nothing is on file when neither document names the product', () => {
    renderDialog({ holding_source: 'none', holding_qty: null, qty_packed: 0, qty_unfinished: 0 });

    expect(screen.getByTestId('row-suggestion')).toHaveTextContent('Nothing of theirs on file');
  });

  it('renders the ask the server computed rather than recomputing the netting', () => {
    // One netting rule, and it is the server's (`suggested_qty`). A second copy in the
    // browser is a copy that can disagree with the number the grid was built from.
    renderDialog({ suggested_qty: 111, open_so_need: 9_999, on_hand: 1, incoming_spo: 1 });

    expect(screen.getByTestId('row-suggestion')).toHaveTextContent('= 111');
  });

  it('shows the ask she edited, not the suggestion she overrode', () => {
    renderDialog({}, 900);

    expect(screen.getByTestId('row-suggestion')).toHaveTextContent('900');
  });

  it('lists every site pool, zero rows included, and the group locations counted too (R7)', () => {
    const table = renderDialog() && screen.getByTestId('row-locations');
    const rows = within(table).getAllByRole('row');

    // header + 3 site pools + 1 group line
    expect(rows).toHaveLength(5);
    expect(within(table).getByText('BRW')).toBeInTheDocument();
    expect(within(table).getByText('MWH')).toBeInTheDocument();
    expect(within(table).getByText('WH3')).toBeInTheDocument();
    // The group line names a couple of codes and how many there are, and its Counted cell is
    // a real figure now (R7, captain 8 Sep 2026): this stock nets into the ask same as a
    // site pool's, so it reads as counted rather than as a dash.
    expect(within(table).getByText('BRW-BB, DC1-BB, ... (12)')).toBeInTheDocument();
    expect(within(table).getByText('Group locations')).toBeInTheDocument();
    const groupRow = within(table).getByText('Group locations').closest('tr');
    expect(groupRow).not.toBeNull();
    // group_locations.on_hand (640) + group_locations.incoming_spo (0) = 640, in both the
    // On hand cell and the Counted cell.
    expect(within(groupRow as HTMLElement).getAllByText('640')).toHaveLength(2);
  });

  it('names the packing lists and open POs behind the reference figures (AC-B4)', () => {
    renderDialog();

    const incoming = screen.getByTestId('row-incoming');
    expect(incoming).toHaveTextContent('PL draft, no ETA');
    expect(incoming).toHaveTextContent('PL FSCU8103365, ETA 27/07/2026');
    expect(incoming).toHaveTextContent('PO 202607-S0105, due 30/09/2026');
  });

  it('says so when nothing is on its way, rather than showing an empty list', () => {
    renderDialog({ incoming_pl_shipments: [], outstanding_po_lines: [] });

    expect(
      screen.getByText('Nothing on a packing list or an open PO for this product.'),
    ).toBeInTheDocument();
  });

  it('a set row says which member its figures come from (AC-F12.3)', () => {
    // A set is never stocked and never ordered - its members are - so every figure in this
    // dialog belongs to ONE member, and a reader who is not told which one cannot check any
    // of them.
    renderDialog({
      row_key: 'set:s-1',
      row_kind: 'set',
      item_code: 'CWC605-RL',
      product_name: 'Close-coupled WC',
      product_set_id: 's-1',
      set_code: 'CWC605-RL',
      set_name: 'Close-coupled WC',
      driver_product_id: 'p-driver',
      driver_item_code: 'CWCX605-RL',
      driver_product_name: 'Pedestal',
    });

    expect(
      screen.getByText(/Figures from CWCX605-RL - Pedestal/),
    ).toBeInTheDocument();
  });

  it('lists the sales-order lines behind the row', () => {
    renderDialog();

    const lines = screen.getByTestId('row-so-lines');
    expect(within(lines).getByText('SO381895')).toBeInTheDocument();
    expect(within(lines).getByText('L4 TUJU RESIDENCE')).toBeInTheDocument();
    expect(within(lines).getByText('120')).toBeInTheDocument();
  });
});
