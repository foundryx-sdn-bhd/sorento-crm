/**
 * Stage 1C - adding a Borrow from a listed candidate (AC-B09, AC-B10), as amended by
 * PLAN-fulfilment-planning-from-autocount-so.md 13.11, and by S4 of
 * `PLAN-local-supplier-oi-routing.md` (AC-1.4 - AC-1.7): the Source section is now the
 * Grid LOCATION TABLE (`CellStockTable`), fed by each candidate's own `location`, with a
 * radio in the Location cell and the `Recommended` badge beside the code - never the
 * dialog's own bespoke seven-column table (`On hand · SO qty · SPO qty · Available · Free ·
 * Committed · After borrow`), which is retired.
 *
 * What is pinned here: the table renders the SAME columns (and none of the retired ones),
 * a source row expands into the same ledger the Grid view expands into (`This line` on the
 * asking line's own row), the impact sentence below the table still updates as the
 * quantity is typed, and the quantity/authorisation validation and the payload handed back
 * on Add are unchanged - none of that lived in the table markup.
 *
 * D3 (S2): the dialog's OWN reason field is gone - `BoardLineDecisionPanel` (and, on the
 * per-order sheet, `SupplyLineCard`) carry the one Reason box now, so `onAdd`'s third
 * argument is the same-agent authorisation, folded, or `''`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  BoardCellLocation,
  BorrowCandidate,
} from '../../_shared/types/fulfilmentPlanning.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const getStockDetail = vi.fn();

vi.mock('../../_shared/services/fulfilmentPlanningService', () => ({
  getStockDetail: (...args: unknown[]) => getStockDetail(...args),
}));

import { BorrowAddDialog } from './BorrowAddDialog';

const WH_HQ = 'a1000000-0000-4000-8000-000000000002';
const WH_JB = 'a1000000-0000-4000-8000-000000000004';
const DONOR_PROJECT = 'b2000000-0000-4000-8000-000000000001';

function loc(overrides: Partial<BoardCellLocation> = {}): BoardCellLocation {
  return {
    location: 'HQ',
    where: 'other_group',
    product_id: 'prod-1',
    warehouse_id: WH_HQ,
    qty: '0',
    qty_demand: '0',
    qty_on_hand: '80',
    so_qty: '0',
    spo_qty: '0',
    available_qty: '80',
    po_open_qty: '0',
    incoming: [],
    ...overrides,
  };
}

/** The donor the ranking put first: nothing owed against its 80, so it keeps 60 of them. */
const OTHER_LOCATION: BorrowCandidate = {
  source: 'other_location',
  warehouse_code: 'HQ',
  warehouse_id: WH_HQ,
  free_qty: '80',
  qty_on_hand: '80',
  so_qty: '0',
  spo_qty: '0',
  available_qty: '80',
  qty_free: '80',
  qty_committed: '140',
  need_qty: '20',
  available_after_need: '60',
  recommended: true,
  donor_impact: { free_before: '80', free_after_full_borrow: '0', committed_qty: '140' },
  location: loc({ location: 'HQ', warehouse_id: WH_HQ, qty_on_hand: '80', available_qty: '80' }),
};

/** Second: 50 free, but the book has sold 60 of its 70, so meeting the 20 leaves it short. */
const OTHER_PROJECT: BorrowCandidate = {
  source: 'other_project',
  warehouse_code: 'JB',
  warehouse_id: WH_JB,
  donor_project_ref: 'PRJ-0052 Seri Emas Phase 2',
  donor_project_id: DONOR_PROJECT,
  free_qty: '50',
  qty_on_hand: '70',
  so_qty: '60',
  spo_qty: '0',
  available_qty: '10',
  qty_free: '50',
  qty_committed: '50',
  need_qty: '20',
  available_after_need: '-10',
  recommended: false,
  donor_impact: { free_before: '50', free_after_full_borrow: '10', committed_qty: '50' },
  location: loc({
    location: 'JB', warehouse_id: WH_JB, qty_on_hand: '70', so_qty: '60', available_qty: '10',
  }),
};

/** A ladder v2 group-borrow donor (section E.4): ranked below this one and offered because
 * it shares the line's own agent - "she can authorise CS to move stock between her own
 * orders" (section 8). */
const GROUP_BORROW: BorrowCandidate = {
  source: 'other_location',
  warehouse_code: 'MWH-BB',
  warehouse_id: 'wh-mwh-bb',
  free_qty: '90',
  qty_on_hand: '90',
  so_qty: '0',
  spo_qty: '0',
  available_qty: '90',
  qty_free: '90',
  qty_committed: '0',
  need_qty: '90',
  available_after_need: '0',
  recommended: false,
  donor_impact: { free_before: '90', free_after_full_borrow: '0', committed_qty: '0' },
  rung: 'group_borrow',
  donor_so_number: 'SO371334',
  donor_line_no: 2,
  donor_agent_code: 'JEREMY',
  donor_core_line_id: 'core-line-1',
  same_agent: true,
  location: loc({ location: 'MWH-BB', warehouse_id: 'wh-mwh-bb', qty_on_hand: '90', available_qty: '90' }),
};

/** A cross-group donor. Uncapped since v7.1 (R5): any ownership group may donate, so this
 * row is offered and selectable like any other. */
const CROSS_GROUP: BorrowCandidate = {
  source: 'other_location',
  warehouse_code: 'WH3',
  warehouse_id: 'wh-wh3',
  free_qty: '500',
  qty_on_hand: '500',
  so_qty: '0',
  spo_qty: '0',
  available_qty: '500',
  qty_free: '500',
  qty_committed: '0',
  need_qty: '20',
  available_after_need: '480',
  recommended: false,
  donor_impact: { free_before: '500', free_after_full_borrow: '480', committed_qty: '0' },
  rung: 'cross_group_borrow',
  location: loc({ location: 'WH3', warehouse_id: 'wh-wh3', qty_on_hand: '500', available_qty: '500' }),
};

const onAdd = vi.fn();
const onDone = vi.fn();

const onAddMany = vi.fn();

function renderDialog(candidates: BorrowCandidate[] = [OTHER_LOCATION, OTHER_PROJECT]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <BorrowAddDialog
        lineNo={2}
        itemCode="SRT501-CP"
        lineId="line-2"
        candidates={candidates}
        onDone={onDone}
        onAdd={onAdd}
        onAddMany={onAddMany}
      />
    </QueryClientProvider>,
  );
}

function donorRow(code: string) {
  return screen.getByTestId(`cell-location-${code}`);
}

beforeEach(() => {
  vi.clearAllMocks();
  getStockDetail.mockReturnValue(new Promise(() => {}));
});

describe('BorrowAddDialog', () => {
  it('names the line and the item it is borrowing for', () => {
    renderDialog();

    expect(screen.getByText('Borrow for line 2')).toBeInTheDocument();
    expect(screen.getByText('SRT501-CP')).toBeInTheDocument();
  });

  it('says "This item" rather than nothing when the line carries no item code', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={client}>
        <BorrowAddDialog
          lineNo={2}
          itemCode={null}
          candidates={[OTHER_LOCATION]}
          onDone={onDone}
          onAdd={onAdd}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText('This item')).toBeInTheDocument();
  });

  // -------------------------------------------------------------- AC-1.4
  it('renders CellStockTable columns with radio and Recommended, never Free/Committed/After borrow', () => {
    renderDialog();

    const table = screen.getByTestId('borrow-donor-table');
    for (const label of [
      'Location', 'Where', 'On hand', 'SO qty', 'SPO qty', 'Available',
      'Available for Project', 'PO qty', 'Taken',
    ]) {
      expect(within(table).getByText(label)).toBeInTheDocument();
    }
    expect(within(table).queryByText('Free')).not.toBeInTheDocument();
    expect(within(table).queryByText('Committed')).not.toBeInTheDocument();
    expect(within(table).queryByText('After borrow')).not.toBeInTheDocument();

    // A radio per donor row.
    expect(within(donorRow('HQ')).getByRole('radio')).toBeInTheDocument();
    expect(within(donorRow('JB')).getByRole('radio')).toBeInTheDocument();

    // Recommended on the first candidate only.
    expect(within(donorRow('HQ')).getByText('Recommended')).toBeInTheDocument();
    expect(within(donorRow('JB')).queryByText('Recommended')).not.toBeInTheDocument();
  });

  it("states a donor's whole position on its own row, off its location figures", () => {
    renderDialog();

    const row = donorRow('JB');
    expect(within(row).getByTestId('stock-on-hand-JB')).toHaveTextContent('70');
    expect(within(row).getByTestId('stock-so-JB')).toHaveTextContent('60');
    expect(within(row).getByTestId('stock-spo-JB')).toHaveTextContent('0');
    expect(within(row).getByTestId('stock-available-JB')).toHaveTextContent('10');
  });

  it('keeps the server-ranked order: donors in the order the candidates array names them', () => {
    renderDialog();

    const table = screen.getByTestId('borrow-donor-table');
    const rows = within(table).getAllByRole('row');
    expect(rows[1]).toHaveAttribute('data-testid', 'cell-location-HQ');
    expect(rows[2]).toHaveAttribute('data-testid', 'cell-location-JB');
  });

  it('opens on the first (recommended) donor, radio checked', () => {
    renderDialog();

    expect(within(donorRow('HQ')).getByRole('radio')).toBeChecked();
    expect(within(donorRow('JB')).getByRole('radio')).not.toBeChecked();
  });

  // -------------------------------------------------------------- fix round: two
  // donors sharing ONE bin (`cell-location-HQ` is not unique between them, so every
  // assertion below addresses a row by POSITION, never by `data-testid`).
  describe('two donors at one bin, told apart by which donor line each rides on', () => {
    const A: BorrowCandidate = {
      ...OTHER_LOCATION,
      warehouse_code: 'HQ',
      warehouse_id: 'wh-1',
      rung: 'group_borrow',
      donor_core_line_id: 'core-1',
      donor_so_number: 'SO371334',
      donor_line_no: 2,
    };
    const B: BorrowCandidate = {
      ...A,
      donor_core_line_id: 'core-2',
      donor_so_number: 'SO371335',
      donor_line_no: 7,
      recommended: false,
    };

    it('lists both rows, each naming its own donor line, and selects positionally', () => {
      renderDialog([A, B]);

      const rows = within(screen.getByTestId('borrow-donor-table')).getAllByRole('row').slice(1);
      expect(rows).toHaveLength(2);
      expect(within(rows[0]).getByText('SO371334 line 2')).toBeInTheDocument();
      expect(within(rows[1]).getByText('SO371335 line 7')).toBeInTheDocument();

      fireEvent.click(within(rows[1]).getByRole('radio'));
      expect(within(rows[1]).getByRole('radio')).toBeChecked();
      expect(within(rows[0]).getByRole('radio')).not.toBeChecked();
    });

    it("expands one row's chevron into exactly one ledger, the other row staying collapsed", () => {
      renderDialog([A, B]);

      const rows = within(screen.getByTestId('borrow-donor-table')).getAllByRole('row').slice(1);
      fireEvent.click(within(rows[1]).getByLabelText(/Show documents behind/));

      expect(screen.getAllByTestId('stock-documents-panel')).toHaveLength(1);
      expect(within(rows[0]).getByLabelText(/Show documents behind/)).toHaveAttribute(
        'aria-expanded',
        'false',
      );
      expect(within(rows[1]).getByLabelText(/Hide documents behind/)).toHaveAttribute(
        'aria-expanded',
        'true',
      );
    });
  });

  // BORROW-LOCATION-ONLY S5: with "Borrow from another order" off the server names no
  // donor, so a group-borrow row reads as its location alone, never "An unnamed sales order".
  it('labels a donor-less group-borrow row by its location alone', () => {
    renderDialog([
      {
        ...OTHER_LOCATION,
        rung: 'group_borrow',
        donor_core_line_id: 'core-9',
        donor_so_number: null,
        donor_line_no: null,
      },
    ]);

    const table = screen.getByTestId('borrow-donor-table');
    expect(within(table).queryByText(/unnamed sales order/i)).not.toBeInTheDocument();
    expect(within(table).queryByText(/line /)).not.toBeInTheDocument();
  });

  // BORROW-LOCATION-ONLY BR2: with the switch off the server names neither the donor order
  // nor the donor project, so two rows at one bin have nothing to tell them apart and are
  // shown as the one location they are.
  it('shows one row for a bin whose donor-less rows nothing tells apart, with no project tag', () => {
    const stepTwo: BorrowCandidate = {
      ...OTHER_PROJECT,
      source: 'other_location',
      rung: 'order_borrow',
      donor_project_ref: null,
      donor_project_id: null,
      donor_core_line_id: 'core-9',
      donor_so_number: null,
      recommended: true,
    };
    const held: BorrowCandidate = { ...OTHER_PROJECT, donor_project_ref: null };
    renderDialog([stepTwo, held]);

    const table = screen.getByTestId('borrow-donor-table');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(1);
    expect(within(table).queryByText(/Another project|PRJ-/)).not.toBeInTheDocument();
  });

  // BORROW-LOCATION-ONLY fix round 2, R1: with the switch ON two NAMED step-2 donors at one
  // bin are two choices, and both stay listed and selectable.
  it('keeps two donor-named order_borrow rows at one bin apart (switch on)', () => {
    const named = (core: string, so: string, lineNo: number): BorrowCandidate => ({
      ...OTHER_PROJECT,
      source: 'other_location',
      rung: 'order_borrow',
      donor_project_ref: null,
      donor_project_id: null,
      donor_core_line_id: core,
      donor_so_number: so,
      donor_line_no: lineNo,
    });
    renderDialog([named('core-1', 'SO371334', 2), named('core-2', 'SO371335', 7)]);

    const rows = within(screen.getByTestId('borrow-donor-table')).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    fireEvent.click(within(rows[1]).getByRole('radio'));
    expect(within(rows[1]).getByRole('radio')).toBeChecked();
  });

  // R2: the merged nameless row is the BIN, so it offers the bin's summed free stock and
  // splits an Add across its lines in the server's order, each within its own free.
  it('offers the summed free of a merged bin and splits an Add of 6 into 4 + 2', () => {
    const line = (core: string): BorrowCandidate => ({
      ...OTHER_PROJECT,
      source: 'other_location',
      rung: 'order_borrow',
      donor_project_ref: null,
      donor_project_id: null,
      donor_core_line_id: core,
      donor_so_number: null,
      free_qty: '4',
      need_qty: null,
    });
    renderDialog([line('core-a'), line('core-b')]);

    expect(screen.getByLabelText('Quantity')).toHaveValue(8);
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '6' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add the borrow' }));

    expect(onAdd).not.toHaveBeenCalled();
    expect(onAddMany).toHaveBeenCalledTimes(1);
    const [parts] = onAddMany.mock.calls[0];
    expect(
      (parts as { candidate: BorrowCandidate; qty: string }[]).map((p) => [
        p.candidate.donor_core_line_id,
        p.qty,
      ]),
    ).toEqual([
      ['core-a', '4'],
      ['core-b', '2'],
    ]);
  });

  // -------------------------------------------------------------- AC-1.7 (fix round)
  it('renders the empty state when every candidate lacks a location, never the table', () => {
    renderDialog([{ ...OTHER_LOCATION, location: undefined }]);

    expect(screen.getByTestId('borrow-donor-empty')).toHaveTextContent(
      'No donor holds this item',
    );
    expect(screen.queryByTestId('borrow-donor-table')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add the borrow' })).toBeDisabled();
  });

  it('offers a cross-group donor like any other, selectable and enabled', () => {
    renderDialog([CROSS_GROUP, OTHER_LOCATION]);

    const row = donorRow('WH3');
    expect(within(row).getByRole('radio')).toBeEnabled();
    expect(within(row).getByRole('radio')).toBeChecked();
  });

  it('shows a Same agent badge for a donor sharing this line’s own sales agent', () => {
    renderDialog([GROUP_BORROW]);

    expect(within(donorRow('MWH-BB')).getByText('Same agent')).toBeInTheDocument();
  });

  it('shows no Same agent badge for a donor that does not share the agent', () => {
    renderDialog([OTHER_LOCATION]);

    expect(within(donorRow('HQ')).queryByText('Same agent')).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------- AC-1.7
  it('renders "No donor holds this item" when there are no candidates', () => {
    renderDialog([]);

    expect(screen.getByTestId('borrow-donor-empty')).toHaveTextContent(
      'No donor holds this item',
    );
    expect(screen.queryByTestId('borrow-donor-table')).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------- AC-1.5
  describe('expanding a source shows its ledger', () => {
    const detail = {
      product_id: 'prod-1',
      item_code: 'SRT501-CP',
      warehouse_id: WH_HQ,
      location: 'HQ',
      qty_on_hand: '80',
      so_qty: '0',
      spo_qty: '0',
      available_qty: '80',
      qty_reserved: '0',
      qty_held_by_decisions: '0',
      qty_free: '80',
      sales_orders: [
        {
          sales_order_id: 'so-mine',
          so_number: 'SO400001',
          customer_name: 'ZZT CONSTRUCTION SDN BHD',
          doc_date: '2026-01-05',
          delivery_date: '2026-09-04',
          so_qty: '20',
          is_this_line: true,
          is_covered: false,
        },
      ],
      incoming: [],
    };

    it('opens the ledger under the row, with This line on the asking line', async () => {
      getStockDetail.mockResolvedValue(detail);
      renderDialog();

      fireEvent.click(screen.getByTestId('stock-expand-HQ'));

      await waitFor(() =>
        expect(getStockDetail).toHaveBeenCalledWith('prod-1', WH_HQ, ['line-2'], undefined),
      );
      const expansion = await screen.findByTestId('stock-expansion-HQ');
      expect(within(expansion).getByTestId('stock-documents-panel')).toBeInTheDocument();
      expect(await within(expansion).findByText('SO400001')).toBeInTheDocument();
      expect(within(expansion).getByTestId('stock-document-this-line')).toHaveTextContent(
        'This line',
      );
    });
  });

  // -------------------------------------------------------------- AC-1.6
  it('states the impact of the typed quantity on the chosen donor, and updates with it', () => {
    renderDialog();

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '20' } });
    expect(screen.getByTestId('borrow-impact')).toHaveTextContent(
      'After borrowing 20: available 60, free 60',
    );

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '30' } });
    expect(screen.getByTestId('borrow-impact')).toHaveTextContent(
      'After borrowing 30: available 50, free 50',
    );
  });

  it('says a donor is left short, and that purchasing will be told (PLAN 13.11)', () => {
    renderDialog();

    const project = donorRow('JB');
    fireEvent.click(within(project).getByRole('radio'));
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '30' } });

    const impact = screen.getByTestId('borrow-impact');
    expect(impact).toHaveTextContent('JB goes short by 20');
    expect(impact).toHaveTextContent('an Order Inquiry will be raised for JB on confirm');
  });

  it('renders the impact section even before a quantity is typed', () => {
    renderDialog();

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '' } });

    expect(screen.getByTestId('borrow-impact')).toHaveTextContent('No quantity yet');
  });

  it('opens on the recommended donor with the quantity this line still needs', () => {
    renderDialog();

    expect(screen.getByLabelText('Quantity')).toHaveValue(20);
  });

  it('falls back to what the donor has free when the server states no need', () => {
    renderDialog([{ ...OTHER_LOCATION, need_qty: null, available_after_need: null }]);

    expect(screen.getByLabelText('Quantity')).toHaveValue(80);
  });

  it('offers only what a donor actually has when the need is larger than that', () => {
    renderDialog([{ ...OTHER_PROJECT, need_qty: '400' }]);

    expect(screen.getByLabelText('Quantity')).toHaveValue(50);
  });

  // D3 (S2): this dialog no longer asks for a reason - `BoardLineDecisionPanel`'s own Reason
  // box (or, on the per-order sheet, `SupplyLineCard`'s own per-row Reason field) is where
  // that text is typed now, so Add is enabled off quantity and donor alone.
  it('adds on a valid quantity with no reason typed - there is no reason field here any more', () => {
    renderDialog();

    expect(screen.queryByLabelText(/^Reason/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add the borrow' })).toBeEnabled();
  });

  it('adds nothing on a zero or negative quantity', () => {
    renderDialog();

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '0' } });
    expect(screen.getByRole('button', { name: 'Add the borrow' })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '-5' } });
    expect(screen.getByRole('button', { name: 'Add the borrow' })).toBeDisabled();
  });

  it('hands back the chosen candidate, the quantity and an empty reason (no same-agent donor)', () => {
    renderDialog();

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '40' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add the borrow' }));

    expect(onAdd).toHaveBeenCalledWith(OTHER_LOCATION, '40', '');
    expect(onDone).toHaveBeenCalled();
  });

  it('switches to another donor and re-fills the quantity with what the line needs', () => {
    renderDialog();

    const project = donorRow('JB');
    fireEvent.click(within(project).getByRole('radio'));

    expect(screen.getByLabelText('Quantity')).toHaveValue(20);

    fireEvent.click(screen.getByRole('button', { name: 'Add the borrow' }));

    expect(onAdd).toHaveBeenCalledWith(OTHER_PROJECT, '20', '');
  });

  it('closes on cancel without adding anything', () => {
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onDone).toHaveBeenCalled();
    expect(onAdd).not.toHaveBeenCalled();
  });

  it('uses a dialog rather than the browser confirm', () => {
    const nativeConfirm = vi.spyOn(window, 'confirm');

    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Add the borrow' }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(nativeConfirm).not.toHaveBeenCalled();
  });

  it('renders no UUID-looking id, though it addresses the donor by one', () => {
    const { container } = renderDialog();

    expect(container.textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-/i);
    expect(screen.getByRole('dialog').textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-/i);
  });
});

/**
 * AC-L6 (section 1c, the captain 25 August 2026): a donor sharing this line's own sales agent
 * is offered at ANY rank, "because the agent can authorise CS to move stock between her own
 * orders" - and taking it therefore requires saying WHO authorised it. Free text, required
 * only on a same-agent borrow, stored beside the quantity it justifies. Unaffected by the
 * table swap - these fields sit below the Source table, not in it.
 */
describe('BorrowAddDialog: authorising a same-agent borrow', () => {
  it('asks who authorised it, naming the agent, and will not add without it', () => {
    renderDialog([GROUP_BORROW]);

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '71' } });

    const add = screen.getByRole('button', { name: 'Add the borrow' });
    expect(add).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^Authorised by agent JEREMY/), {
      target: { value: 'Agreed on the phone, 25 Aug' },
    });
    expect(add).toBeEnabled();
    fireEvent.click(add);

    expect(onAdd).toHaveBeenCalledWith(
      GROUP_BORROW,
      '71',
      'Authorised by agent JEREMY: Agreed on the phone, 25 Aug.',
    );
  });

  it('asks nobody to authorise a donor that is not the same agent’s', () => {
    renderDialog([OTHER_LOCATION]);

    expect(screen.queryByLabelText(/^Authorised by agent/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add the borrow' }));

    expect(onAdd).toHaveBeenCalledWith(OTHER_LOCATION, '20', '');
  });

  it('names the authorisation without an agent code when the donor states none', () => {
    renderDialog([{ ...GROUP_BORROW, donor_agent_code: null }]);

    expect(screen.getByLabelText(/^Authorised by the sales agent/)).toBeInTheDocument();
  });
});

describe('BorrowAddDialog: the authorisation belongs to the donor it was typed for', () => {
  it('clears it when the donor changes, so it cannot be carried onto another agent’s order', () => {
    const otherAgent: BorrowCandidate = {
      ...GROUP_BORROW,
      warehouse_code: 'DC1-BB',
      warehouse_id: 'wh-dc1-bb',
      donor_core_line_id: 'core-line-9',
      donor_so_number: 'SO500999',
      donor_agent_code: 'TERA',
      same_agent: false,
      location: loc({ location: 'DC1-BB', warehouse_id: 'wh-dc1-bb', qty_on_hand: '5', available_qty: '5' }),
    };
    renderDialog([GROUP_BORROW, otherAgent]);

    fireEvent.change(screen.getByLabelText(/^Authorised by agent JEREMY/), {
      target: { value: 'Agreed on the phone, 25 Aug' },
    });

    // Pick the other agent's line: the authorisation is not theirs, so it goes.
    fireEvent.click(within(donorRow('DC1-BB')).getByRole('radio'));
    expect(screen.queryByLabelText(/^Authorised by agent/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add the borrow' }));

    expect(onAdd).toHaveBeenCalledWith(otherAgent, '5', '');
  });

  it('asks again from empty when the planner comes back to the same-agent donor', () => {
    const otherAgent: BorrowCandidate = {
      ...GROUP_BORROW,
      warehouse_code: 'DC1-BB',
      warehouse_id: 'wh-dc1-bb',
      donor_core_line_id: 'core-line-9',
      donor_so_number: 'SO500999',
      same_agent: false,
      location: loc({ location: 'DC1-BB', warehouse_id: 'wh-dc1-bb', qty_on_hand: '5', available_qty: '5' }),
    };
    renderDialog([GROUP_BORROW, otherAgent]);

    fireEvent.change(screen.getByLabelText(/^Authorised by agent JEREMY/), {
      target: { value: 'Agreed on the phone, 25 Aug' },
    });
    fireEvent.click(within(donorRow('DC1-BB')).getByRole('radio'));
    fireEvent.click(within(donorRow('MWH-BB')).getByRole('radio'));

    expect(screen.getByLabelText(/^Authorised by agent JEREMY/)).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Add the borrow' })).toBeDisabled();
  });
});
