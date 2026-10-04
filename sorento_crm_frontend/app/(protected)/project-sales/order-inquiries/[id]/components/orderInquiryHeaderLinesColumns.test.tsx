/**
 * S3 (`PLAN-board-oi-mechanical-22sep.md`, AC-B3-1): the Lines tab's own columns - Product,
 * Qty, Taken, Remaining, Delivery date, Supplier, PO, SPO, Suggested, Location, Instruction,
 * State - with Taken/Remaining hideable through Columns like the rest of the grid. `SO line`
 * is S6 (AC-B6-1), also asserted here since it sits in this same list; its own href/label
 * behaviour is `orderInquiryWorklist.test.ts`'s and `orderInquiryWorklistColumns.test.tsx`'s.
 * `Suggested`, after SPO, is `PLAN-oi-links-autocount-truth-24sep.md` AC-LT-07 - reuses the
 * worklist's own `orderInquirySuggestedColumn()`.
 *
 * Column-order list updated at the #1119 x oi-request-cs-reserve merge (22 Sep, cross-lane):
 * main's own version of this assertion predates a column this lane already shipped in
 * Phase 1 - `Expand` (the board stock-grid chevron, `PLAN-oi-request-cs-reserve.md` 3.9)
 * sits FIRST, before Product. Real, shipped, not a merge artefact.
 *
 * Round 3 (`PLAN-oi-request-cs-reserve.md` section 6d G2, AC-RS-68): the round-2 `Reserve`
 * column this list used to end with is retired - the State cell itself is the reserve
 * click target now (`AC-RS-68` suite below), so the census here drops back to one column
 * per fact.
 *
 * Owner rulings 26 Sep 2026 (`PLAN-oi-no-double-count-25sep.md` S0, issue #1248): the grid
 * is one row per sales order line. No. replaces SO line; SO Qty / Requested / Taken /
 * Remaining replace Qty / Taken / Remaining (G4); one History icon per line replaces the
 * reserve History icon and the decision trail icon (G3); no Was / now on the line (G1).
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, renderHook, screen } from '@testing-library/react';
import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getOrderInquiryPoDetail = vi.fn();
const getOrderInquirySpoDetail = vi.fn();

vi.mock('../../../_shared/services/orderInquiryService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../_shared/services/orderInquiryService')>();
  return {
    ...actual,
    getOrderInquiryPoDetail: (...args: unknown[]) => getOrderInquiryPoDetail(...args),
    getOrderInquirySpoDetail: (...args: unknown[]) => getOrderInquirySpoDetail(...args),
  };
});

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import { useOrderInquiryHeaderLinesColumns } from './orderInquiryHeaderLinesColumns';
import { foldInquiryLines, toLineRows } from '../../../_shared/lib/orderInquiryLineFold';
import { formatDateInMalaysia } from '@/lib/helpers';
import type { OrderInquiryWorklistRow } from '../../../_shared/types/orderInquiry.types';

function renderWithClient(node: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
});

function headerTitleOf(column: unknown): string | undefined {
  return (column as { meta?: { headerTitle?: string } }).meta?.headerTitle;
}

describe('AC-B3-1: the Lines tab reads Product, Qty, Taken, Remaining, Delivery date, Supplier, PO, SPO, Location, Instruction, State', () => {
  it('carries every named column, in order, after the select column', () => {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const titles = result.current.map((column) => headerTitleOf(column)).filter(Boolean);

    expect(titles).toEqual([
      'Expand',
      // AC-ND-3, owner ruling 26 Sep (G4): No. replaces SO line; SO Qty and Requested
      // replace Qty.
      'No.',
      'Product',
      'Confirmed',
      'SO Qty',
      'Requested',
      'Taken',
      'Remaining',
      'Delivery date',
      'Supplier',
      'PO',
      'SPO',
      'Suggested',
      'Location',
      'Instruction',
      // AC-DT-6 (`PLAN-oi-decision-trail-ui.md`): the trail behind the instruction,
      // right after it. "Raised via", not "Raised" - the worklist already has a
      // "Raised by" column beside it (captain ruling, review round 1).
      'Raised via',
      'State',
    ]);
  });

  it('Taken and Remaining are hideable through Columns, the same as the rest of the grid', () => {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const taken = result.current.find((column) => headerTitleOf(column) === 'Taken') as
      | { enableHiding?: boolean }
      | undefined;
    const remaining = result.current.find((column) => headerTitleOf(column) === 'Remaining') as
      | { enableHiding?: boolean }
      | undefined;

    expect(taken).toBeDefined();
    expect(remaining).toBeDefined();
    // Only the select column opts out of hiding (`buildSelectColumn`'s own `enableHiding:
    // false`); Taken/Remaining carry no such flag, so they are hideable by the grid's
    // default the same way Supplier or Location already are.
    expect(taken?.enableHiding).not.toBe(false);
    expect(remaining?.enableHiding).not.toBe(false);
  });
});

function linesRow(overrides: Partial<OrderInquiryWorklistRow>): OrderInquiryWorklistRow {
  return {
    id: 'row-1',
    verb: 'ORDER',
    state: 'raised',
    qty: '10',
    linked_qty: '0',
    bundled_qty: '0',
    ...overrides,
  } as OrderInquiryWorklistRow;
}

/** Only the FOOTER row: the three sums are what this is about, and rendering the cells
 * would drag every per-cell dependency (documents dialog, state pill, links) into a test
 * that asserts none of them. */
function LinesFooter({ rows }: { rows: OrderInquiryWorklistRow[] }) {
  const columns = useOrderInquiryHeaderLinesColumns();
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <table>
      <tfoot>
        <tr>
          {table.getFooterGroups().flatMap((group) =>
            group.headers.map((header) => (
              <td key={header.id} data-testid={`footer-${header.column.id}`}>
                {header.column.columnDef.footer
                  ? flexRender(header.column.columnDef.footer, header.getContext())
                  : null}
              </td>
            )),
          )}
        </tr>
      </tfoot>
    </table>
  );
}

/**
 * AC-B3-4/AC-B3-5, review round 22 Sep (kill test): the Lines tab's OWN Qty footer had no
 * test of its own - only Taken/Remaining's shared footer did
 * (`orderInquiryWorklistColumns.test.tsx`) - so a Qty footer that summed EVERY loaded row
 * rather than the buy rows alone would have shipped green, printing a total that included
 * a notice row's quantity and a cancelled row's.
 */
describe('AC-B3-5: the Lines tab footers total the buy rows only', () => {
  it('excludes notice rows and cancelled rows from Qty, Taken and Remaining alike', () => {
    const buyRows = Array.from({ length: 10 }, (_unused, index) =>
      linesRow({ id: `buy-${index}`, qty: '10', linked_qty: '4', so_line_qty: '10' }),
    );
    render(
      <LinesFooter
        rows={[
          ...buyRows,
          // A notice row carries a quantity of its own and never a link: counted, it
          // would add 999 to Qty and 999 to Remaining.
          linesRow({ id: 'notice', verb: 'ADVANCE', qty: '999', linked_qty: '0' }),
          // A cancelled row is quantity the book called off: counted, it would add 500
          // to Qty and 250 to Taken.
          linesRow({
            id: 'cancelled',
            state: 'cancelled',
            qty: '500',
            linked_qty: '250',
          }),
        ]}
      />,
    );

    // AC-ND-17 (owner ruling 26 Sep, G4): SO Qty (the server's `so_line_qty`, S2) and
    // Requested; the notice and cancelled rows name no sales order line Qty here.
    expect(screen.getByTestId('footer-so_qty')).toHaveTextContent(/^100$/);
    expect(screen.getByTestId('footer-requested')).toHaveTextContent(/^100$/);
    expect(screen.getByTestId('footer-taken')).toHaveTextContent(/^40$/);
    expect(screen.getByTestId('footer-remaining')).toHaveTextContent(/^60$/);
  });
});

/**
 * Round 4 (`PLAN-oi-request-cs-reserve.md` 6e) + AC-RS-83c (owner, 24 Sep: "this pen can
 * put right next to state?"): the State pill is plain text (never a button), and the
 * reserve icons render INSIDE the State cell to the right of it - no separate
 * `reserve_actions` column (saved column preferences appended an unknown id at the end,
 * after Location). Gated exactly as before: `canReserve` plus the line's reserve state.
 * `requested_qty` is cast onto the fixture: the local `linesRow` helper types only the
 * fields it sets.
 */
describe('AC-RS-83 / 83b / 83c: the reserve icons live inside the State cell', () => {
  function stateCell(
    row: OrderInquiryWorklistRow,
    options: Record<string, unknown> = {},
  ) {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns(options as never));
    const stateColumn = result.current.find(
      (column) => ((column as { id?: string }).id ?? (column as { accessorKey?: string }).accessorKey) === 'state',
    ) as { cell: (context: unknown) => React.ReactNode } | undefined;
    expect(stateColumn).toBeDefined();
    return stateColumn!.cell({ row: { original: row } });
  }

  const requestedRow = {
    ...linesRow({ id: 'row-req', state: 'raised', reserve_state: 'requested' }),
    requested_qty: '107',
  } as unknown as OrderInquiryWorklistRow;

  it('AC-RS-83c: no column carries id reserve_actions, with or without the permission', () => {
    for (const canReserve of [true, false]) {
      const { result } = renderHook(() =>
        useOrderInquiryHeaderLinesColumns({ canReserve } as never),
      );
      expect(
        result.current.some((column) => (column as { id?: string }).id === 'reserve_actions'),
      ).toBe(false);
    }
  });

  it('a requested row with the permission: pill text (not a button) + Reserve and Edit reserve in the same cell', () => {
    render(<>{stateCell(requestedRow, { canReserve: true })}</>);
    const pill = screen.getByText('Request to reserve 107');
    expect(pill.closest('button')).toBeNull();
    expect(screen.getByLabelText('Reserve')).toBeInTheDocument();
    expect(screen.getByLabelText('Edit reserve')).toBeInTheDocument();
  });

  it('AC-RS-83c: with the permission the State column starts at 410 wide with no minSize, so it can be narrowed back down', () => {
    const { result } = renderHook(() =>
      useOrderInquiryHeaderLinesColumns({ canReserve: true } as never),
    );
    const stateColumn = result.current.find(
      (column) => ((column as { id?: string }).id ?? (column as { accessorKey?: string }).accessorKey) === 'state',
    ) as { size?: number; minSize?: number };
    // Round 2 (`PLAN-oi-decision-trail-ui.md`): 380 -> 410, room for the new Decision
    // trail icon beside the pill and the reserve icons.
    expect(stateColumn.size).toBe(410);
    expect(stateColumn.minSize).toBeUndefined();
  });

  it('without the permission: the pill only, no icons', () => {
    render(<>{stateCell(requestedRow, { canReserve: false })}</>);
    expect(screen.getByText('Request to reserve 107')).toBeInTheDocument();
    expect(screen.queryByLabelText('Reserve')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Edit reserve')).not.toBeInTheDocument();
  });

  it('AC-RS-83b: a declined row reads Not reserved with Amend reserve + History beside it', () => {
    const declinedRow = linesRow({ id: 'row-decl', state: 'raised', reserve_state: 'declined' });
    render(<>{stateCell(declinedRow, { canReserve: true })}</>);
    expect(screen.getByText('Not reserved')).toBeInTheDocument();
    expect(screen.getByLabelText('Amend reserve')).toBeInTheDocument();
    // AC-ND-13 (owner ruling 26 Sep, G3): the line's ONE History icon; the reserve
    // History icon is gone.
    expect(screen.getAllByLabelText('History')).toHaveLength(1);
  });

  it('a line with no reserve state: the plain state pill, no icons', () => {
    render(<>{stateCell(linesRow({ id: 'row-plain', state: 'raised' }), { canReserve: true })}</>);
    expect(screen.queryByLabelText('Edit reserve')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Amend reserve')).not.toBeInTheDocument();
  });

  it('a staged line shows the chip and Undo inside the State cell', () => {
    render(
      <>
        {stateCell(requestedRow, {
          canReserve: true,
          stagedByRowId: { 'row-req': { kind: 'reserve', qty: 107, locationLabel: 'BRW' } },
        })}
      </>,
    );
    expect(screen.getByText('Reserve 107 @ BRW')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /undo/i })).toBeInTheDocument();
  });
});

/**
 * Issue #1215 point 5. Before this, the PO cell's own `firstLinkOf` read only
 * `link.kind === 'po'`, so a row whose ONLY link is an SPO carrying `source_po_number`
 * (`derived_po: true`) showed a bare dash here even though the worklist's own
 * `DocumentsCell`/`documentsOf` already printed the PO number "via SPO" for the exact
 * same row. Reusing `documentsOf` closes that gap without changing what counts as a
 * link - the underlying link is still SPO-kind, only the derived DISPLAY changes.
 */
describe('issue #1215 point 5: the PO cell reads "via SPO" for an SPO-only link naming its source PO', () => {
  function poCell(row: OrderInquiryWorklistRow) {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const poColumn = result.current.find(
      (column) => (column as { id?: string }).id === 'po_number',
    ) as { cell: (context: unknown) => React.ReactNode } | undefined;
    expect(poColumn).toBeDefined();
    return poColumn!.cell({ row: { original: row } });
  }

  it('prints the source PO number with a "via SPO" mark, not a dash', () => {
    const row = linesRow({
      id: 'row-spo-only',
      links: [
        {
          id: 'link-1',
          kind: 'spo',
          document: 'SPO-2026/09-0080',
          source_po_number: '202607-S0105',
          derived_po: true,
          qty: '6',
        },
      ],
    } as never);

    renderWithClient(<>{poCell(row)}</>);

    expect(screen.getByText('202607-S0105')).toBeInTheDocument();
    expect(screen.getByText(/via SPO/)).toBeInTheDocument();
    expect(screen.queryByText('-')).not.toBeInTheDocument();
  });

  it('still reads a plain dash when the row carries no link naming a PO at all', () => {
    const row = linesRow({ id: 'row-none', links: [] } as never);

    renderWithClient(<>{poCell(row)}</>);

    expect(screen.getByText('-')).toBeInTheDocument();
  });

  it('a real PO-kind link carries no "via" mark', () => {
    const row = linesRow({
      id: 'row-real-po',
      links: [{ id: 'link-2', kind: 'po', document: '202607-S0031', qty: '4' }],
    } as never);

    renderWithClient(<>{poCell(row)}</>);

    expect(screen.getByText('202607-S0031')).toBeInTheDocument();
    expect(screen.queryByText(/via /)).not.toBeInTheDocument();
  });
});

/**
 * R17 (owner rulings, 25 Sep 2026, hand test on stack C): "I also need here to be
 * clickable" - reversing review round 1's should-fix 4 plain-text fix. The via-SPO PO
 * cell opens the PO lightbox for the source PO, resolved by `purchase_order_id` when
 * the payload carries it, else by number.
 */
describe('R17: the via-SPO PO cell is clickable again', () => {
  function poCell(row: OrderInquiryWorklistRow) {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const poColumn = result.current.find(
      (column) => (column as { id?: string }).id === 'po_number',
    ) as { cell: (context: unknown) => React.ReactNode } | undefined;
    expect(poColumn).toBeDefined();
    return poColumn!.cell({ row: { original: row } });
  }

  it('opens the PO lightbox by purchase_order_id when the payload carries it, never a dead-lightbox message', async () => {
    getOrderInquiryPoDetail.mockResolvedValue({
      id: 'po-source-1',
      po_number: '202607-S0105',
      supplier_name: 'DAFUYUAN',
      status: 'confirmed',
      expected_date: '2026-09-01',
      lines: [],
      allocations: [],
    });
    const row = linesRow({
      id: 'row-spo-only',
      links: [
        {
          id: 'link-1',
          kind: 'spo',
          document: 'SPO-2026/09-0080',
          source_po_number: '202607-S0105',
          derived_po: true,
          purchase_order_id: 'po-source-1',
          qty: '6',
        },
      ],
    } as never);

    renderWithClient(<>{poCell(row)}</>);
    fireEvent.click(screen.getByTestId('document-detail-trigger-202607-S0105'));

    expect(await screen.findByText('DAFUYUAN')).toBeInTheDocument();
    expect(getOrderInquiryPoDetail).toHaveBeenCalledWith('po-source-1');
    expect(
      screen.queryByText('This link does not reach a purchase order in the system.'),
    ).not.toBeInTheDocument();
  });

  it('Should fix 3 (review round 2): purchase_order_id is resolved on the SERVER now, never a client scan - a payload carrying none reads the same dead-lightbox message as any other unresolved PO', async () => {
    const row = linesRow({
      id: 'row-spo-only-no-id',
      links: [
        {
          id: 'link-2',
          kind: 'spo',
          document: 'SPO-2026/09-0081',
          source_po_number: '202607-S0105',
          derived_po: true,
          purchase_order_id: null,
          qty: '6',
        },
      ],
    } as never);

    renderWithClient(<>{poCell(row)}</>);
    fireEvent.click(screen.getByTestId('document-detail-trigger-202607-S0105'));

    expect(
      await screen.findByText('This link does not reach a purchase order in the system.'),
    ).toBeInTheDocument();
    expect(getOrderInquiryPoDetail).not.toHaveBeenCalled();
  });
});

/**
 * R15 (owner rulings, 25 Sep 2026, hand test on stack C): the SPO cell threads the
 * REAL link's `spo_allocation_id` through, so the SPO lightbox opened from the Lines
 * tab can highlight the exact line - the same identity the worklist's own
 * backing-documents dialog now carries.
 */
describe('R15: the SPO cell threads spo_allocation_id through', () => {
  function spoCell(row: OrderInquiryWorklistRow) {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const spoColumn = result.current.find(
      (column) => (column as { id?: string }).id === 'spo_number',
    ) as { cell: (context: unknown) => React.ReactNode } | undefined;
    expect(spoColumn).toBeDefined();
    return spoColumn!.cell({ row: { original: row } });
  }

  it('highlights the exact SPO line the real link names', async () => {
    getOrderInquirySpoDetail.mockResolvedValue({
      spo_number: 'SPO-2026/09-0051',
      supplier_name: 'CHAOSHENG',
      lines: [
        { id: 'spo-line-taken', sku: 'TPE-9204', allocated: '10', received: '0', remaining: '10' },
        { id: 'spo-line-other', sku: 'TPE-9204', allocated: '5', received: '0', remaining: '5' },
      ],
      allocations: [],
    });
    const row = linesRow({
      id: 'row-spo-real',
      links: [
        {
          id: 'link-3',
          kind: 'spo',
          document: 'SPO-2026/09-0051',
          spo_allocation_id: 'spo-line-taken',
          qty: '10',
        },
      ],
    } as never);

    renderWithClient(<>{spoCell(row)}</>);
    fireEvent.click(screen.getByTestId('document-detail-trigger-SPO-2026/09-0051'));

    const rows = (await screen.findAllByText('TPE-9204')).map(
      (cell) => cell.closest('tr') as HTMLElement,
    );
    expect(rows).toHaveLength(2);
    const takenRow = rows.find((r) => r.getAttribute('data-linked-line') === 'true');
    expect(takenRow).toBeTruthy();
    const otherRow = rows.find((r) => r !== takenRow);
    expect(otherRow).not.toHaveAttribute('data-linked-line');
  });
});

describe('AC-DT-6: the Raised via column (PLAN-oi-decision-trail-ui.md)', () => {
  function raisedCell(row: OrderInquiryWorklistRow) {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const raisedColumn = result.current.find(
      (column) => (column as { id?: string }).id === 'raise_event',
    ) as { cell: (context: unknown) => React.ReactNode } | undefined;
    expect(raisedColumn).toBeDefined();
    return raisedColumn!.cell({ row: { original: row } });
  }

  it('reads "Reconfirmed by <name> · <date time>" for a matched reconfirm event', () => {
    render(
      <>
        {raisedCell(
          linesRow({
            raise_event_kind: 'reconfirmed',
            raise_event_by_name: 'Nurain',
            raise_event_at: '2026-09-25T01:20:34',
          }),
        )}
      </>,
    );
    expect(screen.getByText(/Reconfirmed by Nurain/)).toBeInTheDocument();
  });

  it('reads "Raised by <name> · <date time>" for a matched raise event', () => {
    render(
      <>
        {raisedCell(
          linesRow({
            raise_event_kind: 'raised',
            raise_event_by_name: 'CONTACT AA',
            raise_event_at: '2026-09-20T03:22:00',
          }),
        )}
      </>,
    );
    expect(screen.getByText(/Raised by CONTACT AA/)).toBeInTheDocument();
  });

  it('reads "Sheet" for a row whose note starts with the sheet migration stamp, no event matched', () => {
    render(
      <>
        {raisedCell(
          linesRow({
            raise_event_kind: null,
            note: 'Migrated from order inquiry sheet, row 42',
          }),
        )}
      </>,
    );
    expect(screen.getByText('Sheet')).toBeInTheDocument();
  });

  it('reads "Planning change" for a row whose note carries the date-move stamp, no event matched', () => {
    render(
      <>
        {raisedCell(
          linesRow({
            raise_event_kind: null,
            note: 'Was 2026-09-01',
          }),
        )}
      </>,
    );
    expect(screen.getByText('Planning change')).toBeInTheDocument();
  });

  it('reads a dash when nothing at all is known about how the row was raised', () => {
    render(<>{raisedCell(linesRow({ raise_event_kind: null, note: null }))}</>);
    expect(screen.getByText('-')).toBeInTheDocument();
  });

  // Reviewer B1, round 1: on the 24 Sep prod copy 10,246 sheet-migrated rows also match
  // migration 523's anonymous backfill `raised` event and 2,070 more latch onto a reconfirm
  // hours later - the note's own sheet stamp is the fact, and it outranks any event.
  it('reads a bare "Sheet" - no by, no date - for a sheet-migrated row even when an event matched it', () => {
    render(
      <>
        {raisedCell(
          linesRow({
            raise_event_kind: 'reconfirmed',
            raise_event_by_name: 'Jayson Foundryx',
            raise_event_at: '2026-09-25T04:00:00',
            note: 'Migrated from order inquiry sheet, row 42',
          }),
        )}
      </>,
    );
    expect(screen.getByText('Sheet')).toBeInTheDocument();
    expect(screen.queryByText(/Reconfirmed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Jayson/)).not.toBeInTheDocument();
    expect(screen.queryByText(/2026/)).not.toBeInTheDocument();
  });

  // Reviewer S2, round 1: an ORDINARY raise/reconfirm note also starts with "Was" -
  // `Was {qty} on {date}` / `Was {qty}, no previous delivery date`
  // (project_order_inquiry_service.py, the import service). Only what
  // planning_change_service.py itself writes reads Planning change.
  it('does NOT read Planning change for an ordinary "Was 5 on 2026-09-01" raise note', () => {
    render(<>{raisedCell(linesRow({ raise_event_kind: null, note: 'Was 5 on 2026-09-01' }))}</>);
    expect(screen.queryByText('Planning change')).not.toBeInTheDocument();
    expect(screen.getByText('-')).toBeInTheDocument();
  });

  it('does NOT read Planning change for "Was 5, no previous delivery date"', () => {
    render(
      <>
        {raisedCell(
          linesRow({ raise_event_kind: null, note: 'Was 5, no previous delivery date' }),
        )}
      </>,
    );
    expect(screen.queryByText('Planning change')).not.toBeInTheDocument();
  });

  it('reads Planning change for the date-move stamp "No previous delivery date"', () => {
    render(
      <>{raisedCell(linesRow({ raise_event_kind: null, note: 'No previous delivery date' }))}</>,
    );
    expect(screen.getByText('Planning change')).toBeInTheDocument();
  });

  it('reads Planning change for the qty-drop stamp "Was 5, now 3"', () => {
    render(<>{raisedCell(linesRow({ raise_event_kind: null, note: 'Was 5, now 3' }))}</>);
    expect(screen.getByText('Planning change')).toBeInTheDocument();
  });

  it('carries an `accessorFn` so the column picker can list and toggle it (`data-grid-column-visibility.tsx` only offers a column that has one)', () => {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const raiseEventColumn = result.current.find(
      (column) => (column as { id?: string }).id === 'raise_event',
    );
    expect(raiseEventColumn).toBeDefined();
    expect(typeof (raiseEventColumn as { accessorFn?: unknown })?.accessorFn).toBe('function');
  });

  it('is hidden by default here too now (round 2 ruling), same as the worklist', async () => {
    const { DEFAULT_HIDDEN_COLUMNS } = await import('../../components/orderInquiryWorklistColumns');
    expect(DEFAULT_HIDDEN_COLUMNS).toContain('raise_event');
  });
});

describe('AC-ND-13 (owner ruling 26 Sep, G3): ONE History icon per line, in the State cell', () => {
  function stateCellFor(row: OrderInquiryWorklistRow, options: Record<string, unknown> = {}) {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns(options as never));
    const stateColumn = result.current.find(
      (column) => ((column as { id?: string }).id ?? (column as { accessorKey?: string }).accessorKey) === 'state',
    ) as { cell: (context: unknown) => React.ReactNode } | undefined;
    expect(stateColumn).toBeDefined();
    return stateColumn!.cell({ row: { original: row } });
  }

  it('renders one History icon on a plain line and hands the line to the caller', () => {
    const onLineHistoryClick = vi.fn();
    render(
      <>
        {stateCellFor(linesRow({ id: 'row-plain', core_line_id: 'core-line-1' }), {
          onLineHistoryClick,
        })}
      </>,
    );
    const buttons = screen.getAllByRole('button', { name: 'History' });
    expect(buttons).toHaveLength(1);
    expect(screen.queryByRole('button', { name: /decision trail/i })).not.toBeInTheDocument();
    fireEvent.click(buttons[0]);
    expect(onLineHistoryClick).toHaveBeenCalledTimes(1);
    expect(onLineHistoryClick.mock.calls[0][0].primary.id).toBe('row-plain');
  });

  it('still one History icon on a reserved line with the permission, beside Amend reserve', () => {
    render(
      <>
        {stateCellFor(
          linesRow({ id: 'row-reserved', reserve_state: 'reserved', core_line_id: 'core-line-1' }),
          { canReserve: true },
        )}
      </>,
    );
    expect(screen.getAllByRole('button', { name: 'History' })).toHaveLength(1);
    expect(screen.getByLabelText('Amend reserve')).toBeInTheDocument();
  });

  it('renders the icon even for a row that names no core sales-order line', () => {
    render(<>{stateCellFor(linesRow({ id: 'row-no-core-line' }))}</>);
    expect(screen.getAllByRole('button', { name: 'History' })).toHaveLength(1);
  });

  it('AC-ND-10 (G7): a cancelled line reads Line cancelled', () => {
    render(<>{stateCellFor(linesRow({ id: 'row-c', line_cancelled: true }))}</>);
    expect(screen.getByText('Line cancelled')).toBeInTheDocument();
  });

  it('AC-ND-11 (O2): a line with only used rows reads Nothing to buy', () => {
    render(<>{stateCellFor(linesRow({ id: 'row-u', redirected_to_pool: true }))}</>);
    expect(screen.getByText('Nothing to buy')).toBeInTheDocument();
  });
});

describe('AC-ND-7 (owner ruling 26 Sep, G1): no Was / now on the line row', () => {
  it('the #1248 line renders no Qty (i) Was trigger and no used pill', () => {
    const [lineRow] = toLineRows(
      foldInquiryLines([
        linesRow({ id: 'used', core_line_id: 'cl-1', qty: '2', so_line_qty: '5', redirected_to_pool: true, ack_state: 'changed' }),
        linesRow({ id: 'fresh', core_line_id: 'cl-1', qty: '5', so_line_qty: '5', previous_qty: '2', ack_state: 'changed' }),
      ]),
    );
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const cells = result.current
      .filter((column) => ['so_qty', 'requested', 'taken', 'remaining'].includes((column as { id?: string }).id ?? ''))
      .map((column) => (column as { cell: (context: unknown) => React.ReactNode }).cell({ row: { original: lineRow } }));
    const { container } = render(<>{cells.map((cell, index) => <span key={index}>{cell}</span>)}</>);
    expect(container.textContent).toBe('5505');
    expect(container.querySelector('[data-testid^="qty-annotation-trigger"]')).toBeNull();
    expect(screen.queryByText('used')).not.toBeInTheDocument();
    expect(screen.queryByText(/was 2/i)).not.toBeInTheDocument();
  });

  const case1248 = () =>
    toLineRows(
      foldInquiryLines([
        linesRow({ id: 'used', core_line_id: 'cl-1', qty: '2', state: 'placed', redirected_to_pool: true, ack_state: 'changed' }),
        linesRow({
          id: 'fresh',
          core_line_id: 'cl-1',
          qty: '5',
          previous_qty: '2',
          ack_state: 'changed',
          note: 'Replaces 2 used; PO-2026/09-0023 received 24 Sep 2026 into BRW-IB',
        }),
      ]),
    )[0];

  function cellOf(key: string, lineRow: OrderInquiryWorklistRow) {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const column = result.current.find(
      (c) => ((c as { id?: string }).id ?? (c as { accessorKey?: string }).accessorKey) === key,
    ) as { cell: (context: unknown) => React.ReactNode };
    return column.cell({ row: { original: lineRow } });
  }

  it('B1: the #1248 line State reads To confirm, per the approved mockup', () => {
    render(<>{cellOf('state', case1248())}</>);
    expect(screen.getByText('To confirm')).toBeInTheDocument();
    expect(screen.queryByText('To buy')).not.toBeInTheDocument();
  });

  it('B2: the Instruction cell carries no note (i), so "Replaces 2 used" never reads on the main grid', () => {
    const { container } = render(<>{cellOf('verb', case1248())}</>);
    expect(screen.getByText('ORDER')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Why this instruction' })).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/replaces|used/i);
  });

  it('Nit N1: a cancelled line reads "-" in Instruction, as the mockup draws it', () => {
    const [lineRow] = toLineRows(foldInquiryLines([linesRow({ id: 'c', core_line_id: 'cl-5', line_cancelled: true })]));
    const { container } = render(<>{cellOf('verb', lineRow)}</>);
    expect(container.textContent).toBe('-');
  });
});

/**
 * PR #1266 fix round W1 (owner, 26 Sep 2026: "i just realized after we click confirm, at
 * the line level can't really see it is confirmed, can we have an icon here to show it is
 * confirmed?"). A new `confirmation` column sits between Product and SO Qty.
 */
describe('W1 line confirmation column', () => {
  function confirmationCell(lineRow: OrderInquiryWorklistRow) {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const column = result.current.find(
      (c) => (c as { id?: string }).id === 'confirmation',
    ) as { cell: (context: unknown) => React.ReactNode } | undefined;
    expect(column).toBeDefined();
    return column!.cell({ row: { original: lineRow } });
  }

  it('W1: a fully confirmed line shows the check with Confirmed by <name> on <date>', () => {
    const [lineRow] = toLineRows(
      foldInquiryLines([
        linesRow({
          id: 'a',
          core_line_id: 'cl-w1',
          ack_state: 'acknowledged',
          acknowledged_by_name: 'Aisyah',
          acknowledged_at: '2026-09-26T11:20:00Z',
        }),
      ]),
    );
    render(<>{confirmationCell(lineRow)}</>);
    const expectedLabel = `Confirmed by Aisyah on ${formatDateInMalaysia('2026-09-26T11:20:00Z')}`;
    const mark = screen.getByTestId('line-confirmed-mark');
    expect(mark).toHaveAttribute('aria-label', expectedLabel);
    expect(mark).toHaveAttribute('title', expectedLabel);
    expect(screen.queryByTestId('line-partly-confirmed-mark')).not.toBeInTheDocument();
    // Review N5: the worklist's confirmed mark, not just any icon.
    const icon = mark.querySelector('svg');
    expect(icon).toHaveClass('lucide-circle-check');
    expect(icon).toHaveClass('text-emerald-600');
  });

  it('W1: a partly confirmed line shows the half-state mark reading n of m', () => {
    const [lineRow] = toLineRows(
      foldInquiryLines([
        linesRow({ id: 'a', core_line_id: 'cl-w2', ack_state: 'acknowledged' }),
        linesRow({ id: 'b', core_line_id: 'cl-w2', ack_state: 'awaiting' }),
      ]),
    );
    render(<>{confirmationCell(lineRow)}</>);
    const mark = screen.getByTestId('line-partly-confirmed-mark');
    expect(mark).toHaveAttribute('aria-label', '1 of 2 rows confirmed');
    expect(mark).toHaveAttribute('title', '1 of 2 rows confirmed');
    const icon = mark.querySelector('svg');
    expect(icon).toHaveClass('lucide-circle-dashed');
    expect(icon).toHaveClass('text-muted-foreground');
    expect(screen.queryByTestId('line-confirmed-mark')).not.toBeInTheDocument();
  });

  it('W1: a line with nothing to confirm shows no mark', () => {
    const [lineRow] = toLineRows(
      foldInquiryLines([linesRow({ id: 'a', core_line_id: 'cl-w3', ack_state: 'awaiting' })]),
    );
    render(<>{confirmationCell(lineRow)}</>);
    expect(screen.queryByTestId('line-confirmed-mark')).not.toBeInTheDocument();
    expect(screen.queryByTestId('line-partly-confirmed-mark')).not.toBeInTheDocument();
  });

  it('W1: the column sits between Product and SO Qty and does not sort', () => {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const ids = result.current.map((column) => (column as { id?: string; accessorKey?: string }).id ?? (column as { accessorKey?: string }).accessorKey);
    const productIndex = ids.indexOf('item_code');
    const confirmationIndex = ids.indexOf('confirmation');
    const soQtyIndex = ids.indexOf('so_qty');
    expect(productIndex).toBeGreaterThanOrEqual(0);
    expect(confirmationIndex).toBe(productIndex + 1);
    expect(soQtyIndex).toBe(confirmationIndex + 1);
    const confirmationColumn = result.current.find(
      (c) => (c as { id?: string }).id === 'confirmation',
    ) as { enableSorting?: boolean; size?: number; meta?: { draggable?: boolean } } | undefined;
    expect(confirmationColumn?.enableSorting).toBe(false);
    // Review N4: a blank header carries no lone drag grip.
    expect(confirmationColumn?.meta?.draggable).toBe(false);
    expect(confirmationColumn?.size ?? 0).toBeLessThanOrEqual(48);
  });
});

describe('AC-ND-9 (G5): the SPO cell lists every document of the line as first + "+N"', () => {
  it('a 364 + 364 split on two SPOs reads the first SPO and +1, and +1 lists both', async () => {
    const [lineRow] = toLineRows(
      foldInquiryLines([
        linesRow({
          id: 'a',
          core_line_id: 'cl-3',
          qty: '364',
          state: 'placed',
          links: [{ kind: 'spo', document: 'SPO-2026/09-0007', qty: '364' }] as never,
        }),
        linesRow({
          id: 'b',
          core_line_id: 'cl-3',
          qty: '364',
          state: 'placed',
          links: [{ kind: 'spo', document: 'SPO-2026/09-0009', qty: '364' }] as never,
        }),
      ]),
    );
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const spoColumn = result.current.find(
      (column) => (column as { id?: string }).id === 'spo_number',
    ) as { cell: (context: unknown) => React.ReactNode };
    renderWithClient(<>{spoColumn.cell({ row: { original: lineRow } })}</>);
    expect(screen.getByText('SPO-2026/09-0007')).toBeInTheDocument();
    // Nit N3: the accessible name says what "+1" means, never "plus one".
    const more = screen.getByRole('button', { name: '1 more SPO' });
    expect(more).toHaveTextContent('+1');
    fireEvent.click(more);
    expect(await screen.findByText('SPO-2026/09-0009')).toBeInTheDocument();
  });
});

/**
 * PR #1266 review at d0d328d7f, State-sort nit: the State column sorted on the primary
 * row's raw `state`, so a line reading To confirm, Line cancelled or Nothing to buy sorted
 * as raised / placed. It sorts on what the cell shows, the line's own State.
 */
describe('State sorts on the line State, not the primary row state', () => {
  it('reads to_confirm, line_cancelled and nothing_to_buy through its accessor', () => {
    const { result } = renderHook(() => useOrderInquiryHeaderLinesColumns());
    const stateColumn = result.current.find(
      (column) => (column as { id?: string }).id === 'state',
    ) as { accessorFn?: (row: unknown, index: number) => unknown } | undefined;
    expect(typeof stateColumn?.accessorFn).toBe('function');

    const base = {
      verb: 'ORDER',
      qty: '5',
      linked_qty: '0',
      reserved_qty: '0',
      bundled_qty: '0',
      state: 'placed',
    } as Partial<OrderInquiryWorklistRow>;
    const lineRows = toLineRows(
      foldInquiryLines([
        { ...base, id: 'a', core_line_id: 'cl-1', line_no: 1, ack_state: 'changed' },
        { ...base, id: 'b', core_line_id: 'cl-2', line_no: 2, line_cancelled: true },
        { ...base, id: 'c', core_line_id: 'cl-3', line_no: 3, redirected_to_pool: true, ack_state: 'acknowledged' },
      ] as OrderInquiryWorklistRow[]),
    );
    expect(lineRows.map((row, index) => stateColumn!.accessorFn!(row, index))).toEqual([
      'to_confirm',
      'line_cancelled',
      'nothing_to_buy',
    ]);
  });
});
