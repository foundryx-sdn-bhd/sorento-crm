/**
 * AC-ST305 (list half), AC-ST308, AC-ST217 (pending), AC-ST216 (Done by), AC-ST116 (truncated):
 * the shared to-do body (portal Customer asks, CRM Sales > Customer asks) after the S3 reshape.
 * Presentational: payload in, callbacks out. The mounts own the toolbar, the sort persistence,
 * the drawer / sheet and the conversation query.
 *
 * S3 removed from this component: the counts line, the age label, the inline Sort select, the
 * per-day sub-headings, the branch / Notified / Console badges and the inline note input.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render as rtlRender, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AskTodoList } from './AskTodoList';
import type { StockAsk } from '@/lib/stock-asks';
import type { AskTodoPayload } from '@/lib/stock-asks-todo';
import { formatDateTimeInMalaysia } from '@/lib/helpers';

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

function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const TODAY_START = '2026-09-28T16:00:00Z';

function ask(over: Partial<StockAsk> & { id: string }): StockAsk {
  return {
    customer_name: 'Hock Lee Trading',
    contact_name: 'Ah Seng',
    product_code: 'SRT5674',
    product_name: 'Wiper Blade 24in',
    quantity: 50,
    branch: 'in_stock',
    answer_summary: 'SRT5674 x 50: yes, we have stock. Please refer to your salesman.',
    notified_agent: true,
    notify_skip_reason: null,
    state: 'open',
    note: 'A saved note',
    created_at: '2026-09-29T01:00:00Z',
    updated_at: null,
    ...over,
  };
}

const OLD = ask({
  id: 'old',
  customer_name: 'Old Customer',
  product_code: 'SRT-OLD',
  quantity: 7,
  branch: 'no_incoming',
  created_at: '2026-09-27T05:00:00Z', // two Malaysia days before 29 Sep
});
const OLDER = ask({ id: 'older', customer_name: 'Older Customer', created_at: '2026-09-22T05:00:00Z' });
const NEW = ask({ id: 'new', customer_name: 'New Customer', product_code: 'SRT-NEW' });
const DONE = ask({
  id: 'done1',
  customer_name: 'Finished Customer',
  product_code: 'SRT-DONE',
  state: 'done',
  done_by: 'Sean Ibrahim',
  done_at: '2026-09-29T02:00:00Z',
});

function payload(over: Partial<AskTodoPayload> = {}): AskTodoPayload {
  return { today_start: TODAY_START, open: [OLD, NEW], done_today: [DONE], truncated: false, ...over };
}

function setup(p: AskTodoPayload | null, extra: Partial<React.ComponentProps<typeof AskTodoList>> = {}) {
  const handlers = { onOpen: vi.fn(), onDone: vi.fn(), onReopen: vi.fn() };
  const view = render(<AskTodoList payload={p} loading={false} view="board" {...handlers} {...extra} />);
  return { ...handlers, ...view };
}

describe('AskTodoList cards: sections (CUSTOMER-ASKS-REFER-ONLY)', () => {
  it('renders one Open heading, then Done today, and nothing else: no Needs attention / Today split', () => {
    setup(payload({ open: [OLDER, OLD, NEW] }));
    expect(screen.getAllByRole('heading').map((h) => `${h.tagName}:${h.textContent}`)).toEqual([
      'H2:Open',
      'H2:Done today',
    ]);
    expect(screen.getByRole('heading', { name: 'Open' }).className).not.toContain('text-destructive');
    expect(screen.queryByText('Needs attention')).toBeNull();
    expect(screen.queryByText('Today')).toBeNull();
  });

  it('puts every open ask under Open, oldest first by default, old and new days together', () => {
    setup(payload({ open: [NEW, OLD, OLDER] }));
    const open = screen.getByRole('heading', { name: 'Open' }).closest('section') as HTMLElement;
    expect(within(open).getAllByText(/^(Old|Older|New) Customer$/).map((n) => n.textContent)).toEqual([
      'Older Customer',
      'Old Customer',
      'New Customer',
    ]);
  });

  it('each card shows the date it was asked', () => {
    setup(payload({ open: [OLDER], done_today: [] }));
    const card = screen.getByText('Older Customer').closest('li') as HTMLElement;
    expect(card.textContent).toContain(formatDateTimeInMalaysia(OLDER.created_at));
  });

  it('applies the sort it is given inside the section', () => {
    const a = ask({ id: 'a1', customer_name: 'Zed', created_at: '2026-09-29T01:00:00Z' });
    const b = ask({ id: 'b1', customer_name: 'Abe', created_at: '2026-09-29T02:00:00Z' });
    const { unmount } = setup(payload({ open: [a, b], done_today: [] }), { sort: { key: 'created_at', dir: 'asc' } });
    expect(screen.getAllByText(/^(Zed|Abe)$/).map((n) => n.textContent)).toEqual(['Zed', 'Abe']);
    unmount();
    setup(payload({ open: [a, b], done_today: [] }), { sort: { key: 'customer_name', dir: 'asc' } });
    expect(screen.getAllByText(/^(Zed|Abe)$/).map((n) => n.textContent)).toEqual(['Abe', 'Zed']);
  });
});

describe('AskTodoList cards: what the S3 reshape removed (AC-ST305)', () => {
  it('has no counts line and no inline Sort select', () => {
    const { container } = setup(payload());
    expect(screen.queryByTestId('ask-todo-counts')).toBeNull();
    expect(container.textContent).not.toMatch(/Open\s*\d+\s*·/);
    expect(container.textContent).not.toContain('Needs attention 1');
    expect(screen.queryByLabelText('Sort')).toBeNull();
    expect(screen.queryByRole('combobox')).toBeNull();
  });

  it('has no age label, no badge, no Open link and no note input on the rows', () => {
    const { container } = setup(payload({ open: [OLD, NEW, ask({ id: 'inc', customer_name: 'Inc Customer', branch: 'incoming', source: 'console' })] }));
    const text = container.textContent ?? '';
    for (const gone of ['2 days ago', 'Yesterday', 'In stock', 'No stock, no incoming', 'Incoming', 'Console', 'Sent', 'Not sent', 'A saved note']) {
      expect(text).not.toContain(gone);
    }
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.queryByLabelText(/^Note for /)).toBeNull();
  });

  it('a card shows Asked: CODE x Q and Answered: <sentence>', () => {
    setup(payload({ open: [NEW], done_today: [] }));
    const text = (screen.getByText('New Customer').closest('li, article, [tabindex], [role="button"]') as HTMLElement).textContent!.replace(/\s+/g, ' ');
    expect(text).toContain('Ah Seng');
    expect(text).toContain('Asked: SRT-NEW x 50');
    expect(text).toContain('Answered: Yes, we have stock. Please refer to your salesman.');
  });

  it('puts the agent code on the card date line only when showAgent is set', () => {
    const withCode = payload({ open: [{ ...NEW, agent_code: 'SEAN I' }], done_today: [] });
    const { unmount } = setup(withCode, { showAgent: true });
    expect(screen.getByText('SEAN I')).toBeInTheDocument();
    unmount();
    setup(withCode);
    expect(screen.queryByText('SEAN I')).toBeNull();
  });
});

describe('AskTodoList cards: actions (AC-ST305)', () => {
  it('clicking a card calls onOpen with the ask, not onDone', () => {
    const { onOpen, onDone } = setup(payload());
    fireEvent.click(screen.getByText('New Customer'));
    expect(onOpen).toHaveBeenCalledWith(NEW);
    expect(onDone).not.toHaveBeenCalled();
  });

  it('Done calls onDone(id) and does not open the card', () => {
    const { onOpen, onDone } = setup(payload());
    const card = screen.getByText('New Customer').closest('li, article, [tabindex], [role="button"]') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: 'Done' }));
    expect(onDone).toHaveBeenCalledWith('new');
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('a done card sits under Done today with Done by and a Reopen that calls onReopen', () => {
    const { onReopen, onOpen } = setup(payload());
    const section = screen.getByRole('heading', { name: 'Done today' }).closest('section') as HTMLElement;
    expect(section.textContent).toContain('Done by Sean Ibrahim');
    expect(within(section).queryByRole('button', { name: 'Done' })).toBeNull();
    fireEvent.click(within(section).getByRole('button', { name: 'Reopen' }));
    expect(onReopen).toHaveBeenCalledWith('done1');
    expect(onOpen).not.toHaveBeenCalled();
  });
});

describe('AskTodoList pending save (AC-ST217)', () => {
  it('disables the button of the pending ask only', () => {
    setup(payload({ open: [OLD, NEW] }), { pendingAskId: 'new' });
    const pending = screen.getByText('New Customer').closest('li, article, [tabindex], [role="button"]') as HTMLElement;
    const other = screen.getByText('Old Customer').closest('li, article, [tabindex], [role="button"]') as HTMLElement;
    expect(within(pending).getByRole('button', { name: 'Done' })).toBeDisabled();
    expect(within(other).getByRole('button', { name: 'Done' })).toBeEnabled();
  });

  it('disables Reopen for a pending done card', () => {
    setup(payload(), { pendingAskId: 'done1' });
    expect(screen.getByRole('button', { name: 'Reopen' })).toBeDisabled();
  });
});

describe('AskTodoList list view (AC-ST306 via the shared body)', () => {
  it('names the agent in a column only when showAgent is set', () => {
    const withCode = payload({ open: [{ ...NEW, agent_code: 'SEAN I' }], done_today: [] });
    const { unmount } = setup(withCode, { view: 'list', showAgent: true, listingKey: null } as never);
    expect(screen.getByText('SEAN I')).toBeInTheDocument();
    unmount();
    setup(withCode, { view: 'list', listingKey: null } as never);
    expect(screen.queryByText('SEAN I')).toBeNull();
  });

  it('renders the DataGrid with the action column last, and no cards', () => {
    setup(payload(), { view: 'list', listingKey: null } as never);
    const headers = screen.getAllByRole('columnheader').map((h) => (h.textContent ?? '').trim());
    expect(headers.slice(0, 5)).toEqual(['Asked at', 'Customer', 'Contact', 'Asked', 'Answered']);
    expect(headers.at(-1)).toBe('');
    expect(screen.queryByRole('heading', { name: 'Open' })).toBeNull(); // section ROWS, not headings
  });

  it('a row click opens, and Done calls onDone only', () => {
    const { onOpen, onDone } = setup(payload(), { view: 'list', listingKey: null } as never);
    fireEvent.click(screen.getByText('New Customer'));
    expect(onOpen).toHaveBeenCalledWith(NEW);
    onOpen.mockClear();
    const row = screen.getByText('Old Customer').closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Done' }));
    expect(onDone).toHaveBeenCalledWith('old');
    expect(onOpen).not.toHaveBeenCalled();
  });
});

describe('AskTodoList states (AC-ST308, AC-ST116)', () => {
  it('renders Nothing waiting with the hint and no button when there is nothing at all', () => {
    setup(payload({ open: [], done_today: [] }));
    expect(screen.getByText('Nothing waiting')).toBeInTheDocument();
    expect(screen.getByText(/land here/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('still shows Nothing waiting and the cleared cards when only done rows remain', () => {
    setup(payload({ open: [] }));
    expect(screen.getByText('Nothing waiting')).toBeInTheDocument();
    expect(screen.getByText('Finished Customer')).toBeInTheDocument();
  });

  it('with a filter active and no rows reads "No asks match", not "Nothing waiting"', () => {
    setup(payload({ open: [], done_today: [] }), { filtered: true } as never);
    expect(screen.getByText('No asks match')).toBeInTheDocument();
    expect(screen.queryByText('Nothing waiting')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('shows the truncated notice only when the payload is truncated', () => {
    const { unmount } = setup(payload({ truncated: true }));
    expect(screen.getByText('Showing the oldest 500 open asks')).toBeInTheDocument();
    unmount();
    setup(payload());
    expect(screen.queryByText('Showing the oldest 500 open asks')).toBeNull();
  });

  it('renders a skeleton while loading and the error block on error', () => {
    const { unmount } = render(
      <AskTodoList payload={null} loading view="board" onOpen={vi.fn()} onDone={vi.fn()} onReopen={vi.fn()} />,
    );
    expect(screen.getByRole('status')).toBeInTheDocument();
    unmount();
    render(
      <AskTodoList payload={null} loading={false} error="Boom" view="board" onOpen={vi.fn()} onDone={vi.fn()} onReopen={vi.fn()} />,
    );
    expect(screen.getByText('Boom')).toBeInTheDocument();
    expect(screen.getByText('The asks could not be loaded')).toBeInTheDocument();
  });
});
