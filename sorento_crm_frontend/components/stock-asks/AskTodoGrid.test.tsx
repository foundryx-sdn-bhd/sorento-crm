/**
 * AC-ST306: the list view of the to-do, the system DataGrid. Columns Asked at, Customer, Contact,
 * Asked, Answered (Agent when showAgent, Done by), the action column LAST; the three groups are
 * full-width section rows in order; a row click opens, the button does not.
 * The real DataGrid renders; a thin wrapper records the props the grid was given.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render as rtlRender, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const gridProps = vi.hoisted(() => [] as Record<string, unknown>[]);

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/sales/customer-asks',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn().mockResolvedValue(null),
  upsertUserListColumnConfig: vi.fn(),
  resetUserListColumnConfig: vi.fn(),
}));
vi.mock('@/components/ui/data-grid', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/ui/data-grid')>();
  return {
    ...actual,
    DataGrid: (props: Record<string, unknown>) => {
      gridProps.push(props);
      // Record the key the caller asked for, then render without persistence: the preference
      // fetch is not what this file is about, and it holds the rows behind a skeleton in jsdom.
      return <actual.DataGrid {...(props as React.ComponentProps<typeof actual.DataGrid>)} listingKey={null} />;
    },
  };
});

import { AskTodoGrid } from './AskTodoGrid';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import type { StockAsk } from '@/lib/stock-asks';
import type { AskTodoPayload } from '@/lib/stock-asks-todo';

function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function ask(id: string, over: Partial<StockAsk> = {}): StockAsk {
  return {
    id,
    customer_name: `Customer ${id}`,
    contact_name: `Contact ${id}`,
    product_code: `SRT-${id}`,
    product_name: null,
    quantity: 50,
    branch: 'in_stock',
    answer_summary: `SRT-${id} x 50: yes, we have stock, please refer to your salesman.`,
    notified_agent: true,
    notify_skip_reason: null,
    state: 'open',
    note: null,
    created_at: '2026-09-29T01:00:00Z',
    updated_at: null,
    ...over,
  };
}

const OLD = ask('old', { created_at: '2026-09-27T03:00:00Z', agent_code: 'SEAN I' });
const NEW_A = ask('newa', { customer_name: 'Zed Trading', created_at: '2026-09-29T01:00:00Z' });
const NEW_B = ask('newb', { customer_name: 'Abe Trading', created_at: '2026-09-29T02:00:00Z' });
const DONE = ask('done1', { state: 'done', done_by: 'Sean Ibrahim', done_at: '2026-09-29T02:30:00Z' });

const PAYLOAD: AskTodoPayload = {
  today_start: '2026-09-28T16:00:00Z',
  open: [OLD, NEW_A, NEW_B],
  done_today: [DONE],
  truncated: false,
};

function setup(over: Partial<React.ComponentProps<typeof AskTodoGrid>> = {}) {
  const handlers = { onOpen: vi.fn(), onDone: vi.fn(), onReopen: vi.fn() };
  const props = {
    payload: PAYLOAD,
    sort: { key: 'created_at', dir: 'asc' } as const,
    showAgent: false,
    listingKey: null,
    ...handlers,
    ...over,
  };
  const view = render(<AskTodoGrid {...props} />);
  return { ...handlers, ...view };
}

const headers = () => screen.getAllByRole('columnheader').map((h) => (h.textContent ?? '').trim());
const bodyRows = () => Array.from(document.querySelectorAll('tbody tr')) as HTMLElement[];
const rowText = (r: HTMLElement) => (r.textContent ?? '').replace(/\s+/g, ' ').trim();

beforeEach(() => {
  gridProps.length = 0;
});

describe('AskTodoGrid columns (AC-ST306)', () => {
  it('orders Asked at, Customer, Contact, Asked, Answered and ends with the action column', () => {
    setup();
    const h = headers();
    expect(h).toEqual(['Asked at', 'Customer', 'Contact', 'Asked', 'Answered', '']);
  });

  it('puts the Agent column SECOND, after Asked at, and only when showAgent is set', () => {
    const { unmount } = setup({ showAgent: true });
    expect(headers()).toEqual(['Asked at', 'Agent', 'Customer', 'Contact', 'Asked', 'Answered', '']);
    expect(screen.getByText('SEAN I')).toBeInTheDocument();
    unmount();
    setup({ showAgent: false });
    expect(headers()).not.toContain('Agent');
  });

  it('renders Done by only when showDoneBy is set, before the action column', () => {
    const { unmount } = setup({ showDoneBy: true } as never);
    const h = headers();
    expect(h.at(-1)).toBe('');
    expect(h.indexOf('Done by')).toBe(h.length - 2);
    const row = bodyRows().find((r) => rowText(r).includes('Customer done1'))!;
    expect(rowText(row)).toContain('Sean Ibrahim');
    unmount();
    setup();
    expect(headers()).not.toContain('Done by');
  });

  it('renders the cell values: datetime, product x quantity, the answer sentence', () => {
    setup();
    const row = bodyRows().find((r) => rowText(r).includes('Customer old'))!;
    expect(rowText(row)).toContain(formatDateTimeInMalaysia(OLD.created_at));
    expect(rowText(row)).toContain('Contact old');
    expect(rowText(row)).toContain('SRT-old x 50');
    expect(rowText(row)).toContain('Yes, we have stock, please refer to your salesman.');
  });

  it('is a fixed, resizable DataGrid with the listing key it was given', () => {
    const { unmount } = setup({ listingKey: 'sales.customer_asks.view::todo' });
    const last = gridProps.at(-1)!;
    expect(last.tableLayout).toMatchObject({ width: 'fixed', columnsResizable: true });
    expect(last.listingKey).toBe('sales.customer_asks.view::todo');
    expect((last.table as { options: { columnResizeMode: string } }).options.columnResizeMode).toBe('onChange');
    unmount();
    gridProps.length = 0;
    setup({ listingKey: null });
    expect(gridProps.at(-1)!.listingKey).toBeNull();
  });
});

describe('AskTodoGrid groups (CUSTOMER-ASKS-REFER-ONLY)', () => {
  it('lists Open then Done today as full-width section rows, every open ask under Open', () => {
    setup();
    const rows = bodyRows();
    const idx = (label: string) => rows.findIndex((r) => rowText(r) === label);
    const open = idx('Open');
    const doneToday = idx('Done today');
    expect(open).toBe(0);
    expect(doneToday).toBeGreaterThan(open);
    expect(idx('Needs attention')).toBe(-1);
    expect(idx('Today')).toBe(-1);
    for (const at of [open, doneToday]) {
      const cells = within(rows[at]).getAllByRole('cell');
      expect(cells).toHaveLength(1);
      expect((cells[0] as HTMLTableCellElement).colSpan).toBeGreaterThan(1);
      expect(rows[at].className).not.toContain('text-destructive');
      expect(rows[at].innerHTML).not.toContain('text-destructive');
    }
    const at = (name: string) => rows.findIndex((r) => rowText(r).includes(name));
    // Oldest first by default, old and new days in one list.
    expect(at('Customer old')).toBeGreaterThan(open);
    expect(at('Zed Trading')).toBeGreaterThan(at('Customer old'));
    expect(at('Abe Trading')).toBeGreaterThan(at('Zed Trading'));
    expect(at('Abe Trading')).toBeLessThan(doneToday);
    expect(at('Customer done1')).toBeGreaterThan(doneToday);
  });

  it('omits an empty group', () => {
    setup({ payload: { ...PAYLOAD, open: [NEW_A], done_today: [] } });
    const labels = bodyRows().map(rowText);
    expect(labels).toContain('Open');
    expect(labels).not.toContain('Done today');
  });

  it('orders the rows inside a group by the sort it is given', () => {
    const { unmount } = setup({ sort: { key: 'created_at', dir: 'asc' } });
    const order = () => bodyRows().map(rowText).filter((t) => /Zed Trading|Abe Trading/.test(t)).map((t) => (t.includes('Zed') ? 'Zed' : 'Abe'));
    expect(order()).toEqual(['Zed', 'Abe']); // 01:00Z before 02:00Z
    unmount();
    setup({ sort: { key: 'customer_name', dir: 'asc' } });
    expect(order()).toEqual(['Abe', 'Zed']);
  });
});

describe('AskTodoGrid actions (AC-ST306)', () => {
  it('a row click calls onOpen with the ask', () => {
    const { onOpen, onDone } = setup();
    fireEvent.click(screen.getByText('Zed Trading'));
    expect(onOpen).toHaveBeenCalledWith(NEW_A);
    expect(onDone).not.toHaveBeenCalled();
  });

  it('the Done button calls onDone(id) and never onOpen', () => {
    const { onOpen, onDone } = setup();
    const row = bodyRows().find((r) => rowText(r).includes('Customer old'))!;
    fireEvent.click(within(row).getByRole('button', { name: 'Done' }));
    expect(onDone).toHaveBeenCalledWith('old');
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('a done row offers Reopen, calling onReopen(id) and never onOpen', () => {
    const { onOpen, onReopen } = setup();
    const row = bodyRows().find((r) => rowText(r).includes('Customer done1'))!;
    expect(within(row).queryByRole('button', { name: 'Done' })).toBeNull();
    fireEvent.click(within(row).getByRole('button', { name: 'Reopen' }));
    expect(onReopen).toHaveBeenCalledWith('done1');
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('a click on a section row opens nothing', () => {
    const { onOpen } = setup();
    fireEvent.click(bodyRows().find((r) => rowText(r) === 'Open')!);
    expect(onOpen).not.toHaveBeenCalled();
  });
});
