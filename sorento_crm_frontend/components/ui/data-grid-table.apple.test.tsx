/**
 * S1-05, S1-06, S1-07 - the grid on a phone, and the row as a link.
 *
 * `data-grid.tsx` had no horizontal scroll container, so a `table-fixed w-full`
 * table squeezed its columns into whatever width it was given: Stock showed one
 * column at 375, Categories crushed six, and a Complaints row could not be
 * opened at all. And 26 lists had a detail route with no way to reach it from
 * the row.
 *
 * jsdom has no layout, so what is pinned here is the mechanism: the scroller
 * exists, the table may exceed it, the row carries the list state in its href,
 * and the grid's own defaults reach the table instance.
 */
import React from 'react';
import { createPortal } from 'react-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { useReactTable, getCoreRowModel, type ColumnDef, type Table } from '@tanstack/react-table';

import { DataGrid } from './data-grid';
import { DataGridTable } from './data-grid-table';
import { buildSelectColumn } from './data-grid-select-column';

const push = vi.fn();

vi.mock('next/navigation', () => ({
  usePathname: () => '/order-management/orders',
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

type Row = { id: string; name: string; total: string };

const DATA: Row[] = [
  { id: 'a1', name: 'Alpha', total: '10' },
  { id: 'b2', name: 'Beta', total: '20' },
];

const COLUMNS: ColumnDef<Row>[] = [
  buildSelectColumn<Row>(),
  { id: 'name', accessorKey: 'name', header: 'Name', size: 900 },
  { id: 'total', accessorKey: 'total', header: 'Total', size: 900 },
  {
    id: 'actions',
    header: 'Actions',
    size: 200,
    // A real listing row is not inert text: it carries an action button and,
    // on the expanded lists, an inline editor. Both are inside the row, so the
    // row-as-link has to tell them apart from the row itself.
    cell: ({ row }) => (
      <div>
        <button type="button">Edit {row.original.name}</button>
        <input aria-label={`Note for ${row.original.name}`} defaultValue="" />
      </div>
    ),
  },
];

function setMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

function Harness({
  rowHref,
  onRowClick,
  onTable,
  tableLayout,
  columns,
  columnSizing,
}: {
  rowHref?: (row: Row) => string;
  onRowClick?: (row: Row) => void;
  onTable?: (table: Table<Row>) => void;
  tableLayout?: React.ComponentProps<typeof DataGrid<Row>>['tableLayout'];
  columns?: ColumnDef<Row>[];
  columnSizing?: Record<string, number>;
}) {
  const table = useReactTable({
    data: DATA,
    columns: columns ?? COLUMNS,
    getRowId: (r) => r.id,
    enableRowSelection: true,
    initialState: {
      pagination: { pageIndex: 2, pageSize: 25 },
      sorting: [{ id: 'name', desc: true }],
      globalFilter: 'alp',
      ...(columnSizing ? { columnSizing } : {}),
    },
    getCoreRowModel: getCoreRowModel(),
  });
  onTable?.(table);
  return (
    <DataGrid
      table={table}
      recordCount={DATA.length}
      isLoading={false}
      rowHref={rowHref}
      onRowClick={onRowClick}
      tableLayout={tableLayout ?? { width: 'fixed', columnsResizable: true }}
    >
      <DataGridTable />
    </DataGrid>
  );
}

beforeEach(() => {
  push.mockClear();
  setMatchMedia(false);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe('DataGrid scrolls on a phone (S1-05)', () => {
  it('S1-05: the grid owns a horizontal scroll container, so the page never scrolls sideways', () => {
    render(<Harness />);
    const scroller = document.querySelector('[data-slot="data-grid-scroller"]');

    expect(scroller).not.toBeNull();
    expect(scroller).toHaveClass('overflow-x-auto');
    expect(scroller).toHaveClass('overscroll-x-contain');
  });

  /*
    The scroller has to be the table's IMMEDIATE parent.

    S1 shipped the scroller and the min-width and lists still would not scroll:
    161 of them wrapped the grid in a Radix `<ScrollArea>`, which puts a
    `display: table` viewport between `data-grid-scroller` and the table. That
    ancestor shrink-fits, so the scroller measured scrollWidth === clientWidth
    (2178 === 2178 on Orders at 1280) and never overflowed - while still eating
    the wheel gesture via `overscroll-x-contain`, so the one element that COULD
    scroll never got the event.

    jsdom has no layout, so what is pinned here is the containment: nothing sits
    between the scroller and the table. The tree-wide version of the same rule
    is `data-grid-scroller.inventory.test.ts`.
  */
  it('S1-05: no second scrollport sits between the scroller and the table', () => {
    render(<Harness />);
    const table = document.querySelector('[data-slot="data-grid-table"]') as HTMLElement;
    const scroller = document.querySelector('[data-slot="data-grid-scroller"]') as HTMLElement;

    const between: string[] = [];
    for (let node = table.parentElement; node && node !== scroller; node = node.parentElement) {
      between.push(node.tagName.toLowerCase());
      // dnd-kit's context wrapper is allowed: it is a plain div with no
      // scrolling and no layout of its own.
      expect(node.hasAttribute('data-radix-scroll-area-viewport')).toBe(false);
      expect(node.style.overflowX).not.toBe('scroll');
    }
    // The scroller is an ancestor at all: the walk terminated on it, not on the
    // document root.
    expect(scroller.contains(table)).toBe(true);
    expect(between.every((tag) => tag === 'div')).toBe(true);
  });

  /*
    The table gets a DEFINITE min-width, never `min-w-max`.

    `min-width: max-content` is meaningless on a `table-layout: fixed` table -
    fixed layout deliberately ignores content - and Chrome resolves it to its
    "infinite" sentinel of 1,000,000px. The fixed algorithm then distributed that
    width across the columns in proportion to their sizes, so a Products grid
    whose columns sum to 2367px was laid out 1,000,000px wide with every column
    scaled by 422x: measured on :3090 at 1280x800, the last header sat at
    x=962,282 and everything but the checkbox was off-screen.

    `table.getTotalSize()` is the sum of the visible leaf column sizes, which is
    exactly the width the table wants, and as a px value it gives the browser
    nothing to resolve.
  */
  it('S1-05: the table asks for a definite min-width, never max-content', () => {
    render(<Harness />);
    const table = document.querySelector('[data-slot="data-grid-table"]') as HTMLElement;

    expect(table).not.toHaveClass('min-w-max');
    // 44 (select) + 900 (name) + 900 (total) + 200 (actions)
    expect(table.style.minWidth).toBe('2044px');
  });

  it('S1-05: a column with no size of its own counts as the TanStack default', () => {
    render(<Harness columns={[{ id: 'a', accessorKey: 'name', header: 'A' }, { id: 'b', accessorKey: 'total', header: 'B' }]} />);
    const table = document.querySelector('[data-slot="data-grid-table"]') as HTMLElement;

    // No `size` anywhere: two columns at the default 150. A column definition
    // with no size must not read as "unbounded" - its maxSize is
    // Number.MAX_SAFE_INTEGER and summing THAT is what a naive total would do.
    expect(table.style.minWidth).toBe('300px');
  });

  it('S1-05: a persisted column-width preference is what the min-width follows', () => {
    render(
      <Harness
        columns={[{ id: 'a', accessorKey: 'name', header: 'A' }, { id: 'b', accessorKey: 'total', header: 'B' }]}
        columnSizing={{ a: 640, b: 210 }}
      />,
    );
    const table = document.querySelector('[data-slot="data-grid-table"]') as HTMLElement;

    // The user dragged a column wider and the listing-column-preferences hook
    // restored it; the table has to be at least that wide or the restored width
    // is silently squeezed away again.
    expect(table.style.minWidth).toBe('850px');
  });

  it('S1-05: the right edge fades only while there is more to the right', () => {
    render(<Harness />);
    const scroller = document.querySelector('[data-slot="data-grid-scroller"]');

    // jsdom reports every width as 0, i.e. "it fits", so no fade is drawn.
    expect(scroller).toHaveAttribute('data-fade', 'false');
    expect(document.querySelector('[data-slot="data-grid-fade"]')).toBeNull();
  });

  /**
   * S1 pinned the identifier column under `sm` so the row stayed labelled while
   * the grid scrolled sideways. The user tried it and chose the opposite: a
   * column that refuses to move with the rest reads as broken, and losing sight
   * of the name for a moment does not (ruling 2026-08-30). So the phone pins
   * nothing, and this is the test that used to assert the pin.
   */
  it('S1-05: at phone width the whole row scrolls as one, nothing sticks', () => {
    setMatchMedia(true);
    render(<Harness />);

    const nameHeader = screen.getByText('Name').closest('th') as HTMLTableCellElement;
    expect(nameHeader).not.toHaveAttribute('data-pinned');
    expect(nameHeader.style.position).not.toBe('sticky');

    const cell = screen.getByText('Alpha').closest('td') as HTMLTableCellElement;
    expect(cell).not.toHaveAttribute('data-pinned');
    expect(cell.style.position).not.toBe('sticky');
  });

  it('S1-05: at desktop width nothing is pinned either', () => {
    render(<Harness />);
    expect(screen.getByText('Name').closest('th')).not.toHaveAttribute('data-pinned');
  });
});

describe('A row is a link (S1-06)', () => {
  it('S1-06: a click on the row opens the href', () => {
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);

    fireEvent.click(screen.getByText('Alpha'));

    expect(push).toHaveBeenCalledTimes(1);
    expect(push.mock.calls[0][0]).toContain('/order-management/orders/a1');
  });

  it('S1-06: the href carries the page, limit, sort and search the list was showing', () => {
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);

    fireEvent.click(screen.getByText('Alpha'));

    const href = push.mock.calls[0][0] as string;
    const params = new URLSearchParams(href.split('?')[1]);
    expect(params.get('page')).toBe('3');
    expect(params.get('limit')).toBe('25');
    expect(params.get('sort')).toBe('name');
    expect(params.get('dir')).toBe('desc');
    expect(params.get('query')).toBe('alp');
  });

  it('S1-06: a filter the list keeps outside the table survives on the href', () => {
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}?warehouse=HQ`} />);

    fireEvent.click(screen.getByText('Alpha'));

    const params = new URLSearchParams((push.mock.calls[0][0] as string).split('?')[1]);
    expect(params.get('warehouse')).toBe('HQ');
    expect(params.get('page')).toBe('3');
  });

  it('S1-06: the row is reachable by keyboard', () => {
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);
    const row = screen.getByText('Alpha').closest('tr')!;

    // No role override: an explicit one REPLACES the implicit `row`, and the
    // table stops being a table to assistive tech. tabIndex plus Enter and
    // Space is what S1-06 asks for and costs the grid nothing.
    expect(row).not.toHaveAttribute('role');
    expect(row).toHaveAttribute('tabindex', '0');

    fireEvent.keyDown(row, { key: 'Enter' });
    expect(push).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(row, { key: ' ' });
    expect(push).toHaveBeenCalledTimes(2);
  });

  it('S1-06: a middle click opens the record in a new tab', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);

    fireEvent(
      screen.getByText('Alpha').closest('tr')!,
      new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 }),
    );

    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0][0]).toContain('/order-management/orders/a1');
    expect(push).not.toHaveBeenCalled();
  });

  it('S1-06: a middle click opens ONE tab and leaves the current one alone', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);
    const row = screen.getByText('Alpha').closest('tr') as HTMLTableRowElement;

    // A real middle click fires auxclick only. Some paths - a synthetic dispatch,
    // assistive tech, Firefox autoscroll - also deliver a `click` carrying
    // button 1, and the row used to treat that as a plain click and push the
    // current tab to the same record. The user got the record twice: once in a
    // new tab and once over the list they were reading.
    fireEvent(row, new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 }));
    fireEvent(row, new MouseEvent('click', { bubbles: true, cancelable: true, button: 1 }));

    expect(open).toHaveBeenCalledTimes(1);
    expect(push).not.toHaveBeenCalled();
  });

  it('S1-06: a click that is not the primary button does nothing at all', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);
    const row = screen.getByText('Alpha').closest('tr') as HTMLTableRowElement;

    // Middle and right. Opening on the click as WELL as the auxclick would give
    // two tabs, so the click side stays silent and auxclick owns the new tab.
    fireEvent(row, new MouseEvent('click', { bubbles: true, cancelable: true, button: 1 }));
    fireEvent(row, new MouseEvent('click', { bubbles: true, cancelable: true, button: 2 }));

    expect(push).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
  });

  it('S1-06: a control inside the row keeps its own click - the row does not steal it', () => {
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);

    fireEvent.click(screen.getByRole('button', { name: 'Edit Alpha' }));

    // Without this, every action button on all 79 action columns would have to
    // remember stopPropagation, and the one that forgot would navigate away
    // mid-action.
    expect(push).not.toHaveBeenCalled();
  });

  it('S1-06: typing in an inline cell editor does not navigate', () => {
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);
    const input = screen.getByLabelText('Note for Alpha');

    fireEvent.keyDown(input, { key: ' ' });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(push).not.toHaveBeenCalled();
  });

  it('S1-06: Space on the row checkbox ticks it without opening the record', () => {
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);
    const checkbox = screen.getAllByLabelText('Select row')[0];

    fireEvent.keyDown(checkbox, { key: ' ' });
    fireEvent.click(checkbox);

    // Ticking a row is not opening it. (The browser turns Space into the click;
    // jsdom does not, so the click is fired here as well.)
    expect(push).not.toHaveBeenCalled();
    expect(checkbox).toHaveAttribute('data-state', 'checked');
  });

  it('S1-06: a modifier click opens a new tab instead of leaving the list', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);

    for (const modifier of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }]) {
      fireEvent.click(screen.getByText('Alpha'), modifier);
    }

    expect(open).toHaveBeenCalledTimes(3);
    expect(push).not.toHaveBeenCalled();
  });

  it('S1-06: a new tab carries the deploy base path, which the router adds by itself', () => {
    vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '/crm');
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);

    fireEvent.click(screen.getByText('Alpha'), { metaKey: true });
    expect(open.mock.calls[0][0]).toContain('/crm/order-management/orders/a1');

    // ...while an in-app push must NOT carry it, or the base path lands twice.
    fireEvent.click(screen.getByText('Alpha'));
    expect(push.mock.calls[0][0]).not.toContain('/crm/');
  });

  it('S1-06: a grid with neither rowHref nor onRowClick has no pointer cursor and no role', () => {
    render(<Harness />);
    const row = screen.getByText('Alpha').closest('tr')!;

    expect(row).not.toHaveClass('cursor-pointer');
    expect(row).not.toHaveAttribute('role');
    expect(row).not.toHaveAttribute('tabindex');
  });

  it('S1-06: onRowClick still works for lists that edit in a lightbox', () => {
    const onRowClick = vi.fn();
    render(<Harness onRowClick={onRowClick} />);

    fireEvent.click(screen.getByText('Alpha'));

    expect(onRowClick).toHaveBeenCalledWith(DATA[0]);
    expect(push).not.toHaveBeenCalled();
  });

  it('a click inside something a cell portalled out (a dialog, its overlay) is not the row\'s', () => {
    // Owner finding, 12 Oct 2026: closing a lightbox opened from a cell expanded the row,
    // because React bubbles a portal's events through the component tree, not the DOM.
    const onRowClick = vi.fn();
    const columns: ColumnDef<Row>[] = [
      { id: 'name', accessorKey: 'name', header: 'Name', size: 200 },
      {
        id: 'portal',
        header: 'Portal',
        size: 200,
        cell: ({ row }) =>
          row.original.id === 'a1'
            ? createPortal(<div>Portalled body</div>, document.body)
            : null,
      },
    ];
    render(<Harness onRowClick={onRowClick} columns={columns} />);

    fireEvent.click(screen.getByText('Portalled body'));
    expect(onRowClick).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Alpha'));
    expect(onRowClick).toHaveBeenCalledWith(DATA[0]);
  });

  it('S4-03: a lightbox row announces itself and answers the keyboard', () => {
    // It had neither. The href branch carried role, tabIndex and Enter/Space
    // and the onRowClick branch carried a bare onClick, so a Brands row could
    // not be opened without a mouse and read as plain text to a screen reader.
    const onRowClick = vi.fn();
    render(<Harness onRowClick={onRowClick} />);
    const row = screen.getByText('Alpha').closest('tr')!;

    expect(row).toHaveAttribute('tabindex', '0');

    fireEvent.keyDown(row, { key: 'Enter' });
    expect(onRowClick).toHaveBeenCalledWith(DATA[0]);

    fireEvent.keyDown(row, { key: ' ' });
    expect(onRowClick).toHaveBeenCalledTimes(2);
  });

  it('S4-03: an openable row is still a table row', () => {
    // `role="link"` on the <tr> replaced the implicit `row`, so the grid was no
    // longer a table to assistive tech and `getAllByRole('row')` found nothing
    // - which is how the fulfilment board's own tests reported it.
    const { unmount } = render(<Harness onRowClick={vi.fn()} />);
    expect(screen.getAllByRole('row').length).toBeGreaterThan(DATA.length);
    unmount();

    render(<Harness rowHref={(row) => `/order-management/orders/${row.id}`} />);
    expect(screen.getAllByRole('row').length).toBeGreaterThan(DATA.length);
  });

  it('S4-03: a control inside a lightbox row keeps its own click', () => {
    // The bug this closes: a Brands row carries a "View products" link, and
    // clicking it navigated AND set the edit lightbox's state on the way out,
    // so the row looked like it did nothing at all.
    const onRowClick = vi.fn();
    render(<Harness onRowClick={onRowClick} />);

    fireEvent.click(screen.getByRole('button', { name: 'Edit Alpha' }));
    expect(onRowClick).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText('Note for Alpha'));
    expect(onRowClick).not.toHaveBeenCalled();

    // ...and the row itself still opens.
    fireEvent.click(screen.getByText('Alpha'));
    expect(onRowClick).toHaveBeenCalledWith(DATA[0]);
  });

  it('S4-03: a lightbox row has no new tab to open in', () => {
    // `role="button"`, not `"link"`: a middle click or a cmd-click has nowhere
    // to go, so it must not try, and must not swallow the plain open either.
    const onRowClick = vi.fn();
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<Harness onRowClick={onRowClick} />);
    const row = screen.getByText('Alpha').closest('tr')!;

    fireEvent(row, new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 }));
    expect(onRowClick).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Alpha'), { metaKey: true });
    expect(onRowClick).toHaveBeenCalledWith(DATA[0]);
    expect(open).not.toHaveBeenCalled();

    open.mockRestore();
  });
});

describe('DataGrid defaults (S1-07)', () => {
  it('S1-07: columnResizeMode is onChange even when the list did not ask', () => {
    let table: Table<Row> | undefined;
    render(<Harness onTable={(t) => (table = t)} />);

    expect(table!.options.columnResizeMode).toBe('onChange');
  });

  /*
    S1-07's "the header is sticky by default" clause landed as M5-05's
    absolute rule: `DataGridScroller` now gives every grid a bounded default
    max-height (`--grid-max-h`) so `sticky top-0` on the thead has an ancestor
    to stick against, and is the ONE scrollport for both axes - see
    `data-grid.defaults.test.tsx` for the full coverage of both defaults.
  */
  it('M5-05: the header is sticky by default, with no tableLayout prop', () => {
    render(<Harness />);
    expect(document.querySelector('thead')).toHaveClass('sticky');
  });

  it('S1-07: a list that asks for a sticky header still gets one', () => {
    render(<Harness tableLayout={{ width: 'fixed', columnsResizable: true, headerSticky: true }} />);
    const head = document.querySelector('thead');

    expect(head).toHaveClass('sticky');
    expect(head).toHaveClass('top-0');
  });

  it('S1-07: numerals are tabular so figures line up down a column', () => {
    render(<Harness />);
    expect(document.querySelector('[data-slot="data-grid-table"]')).toHaveClass('tabular-nums');
  });

  it('S1-07: the resize handle takes pointer capture', () => {
    const setPointerCapture = vi.fn();
    Element.prototype.setPointerCapture = setPointerCapture;

    render(<Harness />);
    const handle = document.querySelector('.cursor-col-resize')!;

    fireEvent.pointerDown(handle, { pointerId: 7 });

    expect(setPointerCapture).toHaveBeenCalledWith(7);
  });
});
