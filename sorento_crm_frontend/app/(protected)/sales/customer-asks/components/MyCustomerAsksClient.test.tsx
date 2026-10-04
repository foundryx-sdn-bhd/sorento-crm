/**
 * AC-ST209 / AC-ST210 as reshaped by S3 (AC-ST304 to AC-ST308): Sales > Customer asks. The
 * SERVICE functions are mocked (never the Phase 1 store); the hooks, `LandingToolbar`,
 * `AskTodoList` and the conversation Sheet are real.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render as rtlRender, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/sales/customer-asks',
  useSearchParams: () => new URLSearchParams(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('@/lib/toast', () => ({ toast }));

const setSorting = vi.fn();
let storedSorting: { id: string; desc: boolean }[] = [{ id: 'asked_at', desc: false }];
const prefsCalls: { listingKey?: string | null }[] = [];
vi.mock('@/lib/listing-column-preferences/useListingViewPreferences', () => ({
  useListingViewPreferences: (args: { listingKey?: string | null }) => {
    prefsCalls.push(args);
    return { sorting: storedSorting, setSorting, filters: null, setFilters: vi.fn(), isLoading: false };
  },
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    value: string;
    onChange: (v: string) => void;
    clearable?: boolean;
    options?: { value: string; label: string }[];
  }) => (
    <select
      id={props.id}
      data-clearable={props.clearable ? 'true' : 'false'}
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
    >
      <option value="">-</option>
      {(props.options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

const gridProps = vi.hoisted(() => [] as Record<string, unknown>[]);
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
      // The key asked for is recorded; rows render without the preference fetch.
      return <actual.DataGrid {...(props as React.ComponentProps<typeof actual.DataGrid>)} listingKey={null} />;
    },
  };
});

const getCustomerAsksTodo = vi.fn();
const listAskAgents = vi.fn();
const updateSalesAsk = vi.fn();
const getAskConversation = vi.fn();
const getAskConversationPage = vi.fn();
const searchAskConversation = vi.fn();
vi.mock('@/services/stockAskService', () => ({
  getCustomerAsksTodo: (...a: unknown[]) => getCustomerAsksTodo(...a),
  listAskAgents: (...a: unknown[]) => listAskAgents(...a),
  updateSalesAsk: (...a: unknown[]) => updateSalesAsk(...a),
  getAskConversation: (...a: unknown[]) => getAskConversation(...a),
  getAskConversationPage: (...a: unknown[]) => getAskConversationPage(...a),
  searchAskConversation: (...a: unknown[]) => searchAskConversation(...a),
}));
vi.mock('@/components/common/AttachmentPreviewModal', () => ({ __esModule: true, default: () => null }));

import { MyCustomerAsksClient } from './MyCustomerAsksClient';

const TODAY_START = '2026-09-28T16:00:00Z';

function ask(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    customer_name: `Customer ${id}`,
    contact_name: 'Ah Seng',
    product_code: `SRT-${id}`,
    product_name: null,
    quantity: 10,
    branch: 'in_stock',
    answer_summary: 'answer',
    notified_agent: true,
    notify_skip_reason: null,
    state: 'open',
    note: null,
    created_at: '2026-09-29T01:00:00Z',
    updated_at: null,
    ...over,
  };
}

function payload(over: Record<string, unknown> = {}) {
  return {
    today_start: TODAY_START,
    open: [ask('mine')],
    done_today: [],
    truncated: false,
    agent: { code: 'SEAN I', name: 'Sean Ibrahim' },
    ...over,
  };
}

const AGENTS = [
  { agent_id: 'agent-a', code: 'SEAN I', name: 'Sean Ibrahim', open: 4 },
  { agent_id: 'agent-b', code: 'WT I', name: 'William Tan', open: 1 },
];

/** ASKS-UX item 3: the anchor read plus the shared thread's tail page. */
const BASE_US = 1_790_000_000_000_000;
const CONVERSATION = { messages: [], ask_message_id: 2, ask_message_ref: String(BASE_US + 2_000_000), contact_id: 'contact-7' };
const TAIL_PAGE = {
  items: [
    { messageId: BASE_US + 1_000_000, traffic: 'incoming', message: { type: 'text', text: 'Boss, ada stock?' }, status: [] },
    { messageId: BASE_US + 2_000_000, traffic: 'outgoing', message: { type: 'text', text: 'answer' }, status: [] },
  ],
  has_more_older: false,
  has_more_newer: false,
  oldest_message_id: String(BASE_US + 1_000_000),
  newest_message_id: String(BASE_US + 2_000_000),
};

function showCards() {
  fireEvent.click(screen.getByRole('radio', { name: 'Board view' }));
}
function showList() {
  fireEvent.click(screen.getByRole('radio', { name: 'List view' }));
}
async function openMenu(name: string) {
  const trigger = screen.getByRole('button', { name });
  fireEvent.pointerDown(trigger, { button: 0, pointerId: 1 });
  fireEvent.pointerUp(trigger, { button: 0, pointerId: 1 });
  fireEvent.click(trigger);
  await waitFor(() => expect(trigger.getAttribute('aria-expanded')).toBe('true'));
}
const cardOf = (text: string) => screen.getByText(text).closest('li, article, [tabindex], [role="button"]') as HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  gridProps.length = 0;
  getAskConversation.mockResolvedValue(CONVERSATION);
  getAskConversationPage.mockResolvedValue(TAIL_PAGE);
  searchAskConversation.mockResolvedValue([]);
  storedSorting = [{ id: 'created_at', desc: false }];
  prefsCalls.length = 0;
  getCustomerAsksTodo.mockResolvedValue(payload());
  listAskAgents.mockResolvedValue(AGENTS);
  updateSalesAsk.mockResolvedValue(ask('mine', { state: 'done' }));
});

describe('MyCustomerAsksClient (AC-ST209)', () => {
  it('renders the Customer asks header and the to-do from the query hook, no counts line', async () => {
    render(<MyCustomerAsksClient />);
    expect(screen.getByRole('heading', { level: 1, name: 'Customer asks' })).toBeInTheDocument();
    expect(await screen.findByText('Customer mine')).toBeInTheDocument();
    expect(getCustomerAsksTodo).toHaveBeenCalledWith(undefined);
    expect(screen.queryByTestId('ask-todo-counts')).toBeNull();
  });

  // AC-ST304
  it('renders the landing toolbar (Filter, Sort, view toggle) and no New button', async () => {
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    expect(screen.getByRole('button', { name: 'Filter' })).toBeInTheDocument();
    showCards();
    // The UAC says the default reads "Created"; the asks field list (AC-ST302) names the date
    // field "Asked". Either satisfies the toolbar until the captain rules which word wins.
    expect(screen.getByRole('button', { name: 'Sort' })).toHaveTextContent(/Created|Asked/);
    expect(screen.getByLabelText('View mode')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^New/ })).toBeNull();
    expect(screen.queryByRole('link', { name: /^New/ })).toBeNull();
  });

  // AC-ST306 (CRM half)
  it('list view is the DataGrid keyed sales.customer_asks.view::todo with Done last; a row opens, Done does not', async () => {
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    showList();
    const headers = screen.getAllByRole('columnheader').map((h) => (h.textContent ?? '').trim());
    expect(headers.slice(0, 5)).toEqual(['Asked at', 'Customer', 'Contact', 'Asked', 'Answered']);
    expect(headers.at(-1)).toBe('');
    expect(headers).not.toContain('Agent'); // one agent shown
    expect(gridProps.length).toBeGreaterThan(0);
    expect(gridProps.every((g) => g.listingKey === 'sales.customer_asks.view::todo')).toBe(true);
    expect(gridProps.at(-1)!.tableLayout).toMatchObject({ width: 'fixed', columnsResizable: true });
    const row = screen.getByText('Customer mine').closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(updateSalesAsk).toHaveBeenCalledWith('mine', { state: 'done' }));
    expect(getAskConversation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Customer mine'));
    await waitFor(() => expect(getAskConversation).toHaveBeenCalledWith('mine'));
  });

  // AC-ST307 (CRM half)
  it('a card opens the right-hand Sheet: conversation, agent code and Open in Conversations', async () => {
    getCustomerAsksTodo.mockResolvedValue(payload({ open: [ask('mine', { agent_code: 'SEAN I' })] }));
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    showCards();
    fireEvent.click(screen.getByText('Customer mine'));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(getAskConversation).toHaveBeenCalledWith('mine'));
    await waitFor(() => expect(getAskConversationPage).toHaveBeenCalledWith('mine', { limit: 50 }));
    expect(within(dialog).getByRole('button', { name: 'Done' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Reopen' })).toBeNull(); // an open ask
    expect(await within(dialog).findByText('Boss, ada stock?')).toBeInTheDocument();
    expect(within(dialog).getByTestId('chat-scroll-container')).toBeInTheDocument(); // the shared thread
    expect(within(dialog).getByText('This enquiry')).toBeInTheDocument();
    expect(within(dialog).getByText(/SEAN I/)).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: 'Open in Conversations' })).toHaveAttribute(
      'href',
      '/sla-management/conversations?contact=contact-7',
    );
    expect(within(dialog).queryByRole('button', { name: 'Show the whole day' })).toBeNull();
  });

  // ASKS-UX item 1 (AC-AU02): the asker is a filter on the CRM too.
  it('the Filter popover offers Contact', async () => {
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    await openMenu('Filter');
    expect(screen.getByText('Contact')).toBeInTheDocument();
  });

  it('Save note (not blur) goes through updateSalesAsk, Done at the foot marks the ask done', async () => {
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    showCards();
    fireEvent.click(screen.getByText('Customer mine'));
    const dialog = await screen.findByRole('dialog');
    const note = await within(dialog).findByLabelText('Note');
    fireEvent.change(note, { target: { value: 'Visited, ordering Friday' } });
    fireEvent.blur(note);
    expect(updateSalesAsk).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save note' }));
    await waitFor(() => expect(updateSalesAsk).toHaveBeenCalledWith('mine', { note: 'Visited, ordering Friday' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(updateSalesAsk).toHaveBeenCalledWith('mine', { state: 'done' }));
  });

  it('Done on a card goes through updateSalesAsk, refetches and toasts, without opening the card', async () => {
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    showCards();
    fireEvent.click(within(cardOf('Customer mine')).getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(updateSalesAsk).toHaveBeenCalledWith('mine', { state: 'done' }));
    await waitFor(() => expect(getCustomerAsksTodo).toHaveBeenCalledTimes(2));
    expect(toast.success).toHaveBeenCalled();
    expect(getAskConversation).not.toHaveBeenCalled();
  });

  it('a note-only save does not refetch the agents list', async () => {
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    await waitFor(() => expect(listAskAgents).toHaveBeenCalledTimes(1));
    showCards();
    fireEvent.click(screen.getByText('Customer mine'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(await within(dialog).findByLabelText('Note'), { target: { value: 'Called' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save note' }));
    await waitFor(() => expect(updateSalesAsk).toHaveBeenCalledWith('mine', { note: 'Called' }));
    await waitFor(() => expect(getCustomerAsksTodo).toHaveBeenCalledTimes(2)); // the to-do refetches
    expect(listAskAgents).toHaveBeenCalledTimes(1); // the counts cannot have moved
  });

  it('Reopen goes through updateSalesAsk', async () => {
    getCustomerAsksTodo.mockResolvedValue(payload({ open: [], done_today: [ask('d1', { state: 'done', done_by: 'Sean' })] }));
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer d1');
    showCards();
    fireEvent.click(within(cardOf('Customer d1')).getByRole('button', { name: 'Reopen' }));
    await waitFor(() => expect(updateSalesAsk).toHaveBeenCalledWith('d1', { state: 'open' }));
  });

  it('toasts the extracted message when the update fails', async () => {
    updateSalesAsk.mockRejectedValue(new Error('Stock ask not found'));
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    showCards();
    fireEvent.click(within(cardOf('Customer mine')).getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Stock ask not found'));
  });

  it('shows the unlinked message in place of the list when the caller has no agent', async () => {
    getCustomerAsksTodo.mockResolvedValue(payload({ open: [], agent: null }));
    render(<MyCustomerAsksClient />);
    expect(await screen.findByRole('heading', { name: 'Not linked to a sales agent' })).toBeInTheDocument();
    // A hint line under the heading, and no button (one CTA per page, none here).
    const box = screen.getByRole('heading', { name: 'Not linked to a sales agent' }).parentElement as HTMLElement;
    expect((box.textContent ?? '').replace('Not linked to a sales agent', '').trim().length).toBeGreaterThan(0);
    expect(within(box).queryByRole('button')).toBeNull();
    expect(screen.queryByText('You are not linked to a sales agent')).toBeNull();
    expect(screen.queryByTestId('ask-todo-counts')).toBeNull();
  });

  it('shows the error state when the to-do cannot be loaded', async () => {
    getCustomerAsksTodo.mockRejectedValue(new Error('Server down'));
    render(<MyCustomerAsksClient />);
    expect(await screen.findByText('Server down')).toBeInTheDocument();
  });

  // AC-ST308
  it('shows Nothing waiting with the toolbar still rendered when the payload is empty', async () => {
    getCustomerAsksTodo.mockResolvedValue(payload({ open: [], done_today: [] }));
    render(<MyCustomerAsksClient />);
    expect(await screen.findByText('Nothing waiting')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Filter' })).toBeInTheDocument();
    expect(screen.getByLabelText('View mode')).toBeInTheDocument();
  });
});

describe('MyCustomerAsksClient Agent select (AC-ST210)', () => {
  it('renders no Agent select when the agents list is empty', async () => {
    listAskAgents.mockResolvedValue([]);
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    await waitFor(() => expect(listAskAgents).toHaveBeenCalled());
    expect(screen.queryByLabelText('Agent')).toBeNull();
  });

  it('renders a clearable Agent select with All agents first and the counts, when the list is non-empty', async () => {
    render(<MyCustomerAsksClient />);
    const select = await screen.findByLabelText('Agent');
    expect(select).toHaveAttribute('data-clearable', 'true');
    // CUSTOMER-ASKS-REFER-ONLY: the open count alone; the to-do has no needs-attention split.
    await screen.findByRole('option', { name: 'SEAN I · 4 open' });
    const labels = within(select).getAllByRole('option').map((o) => o.textContent);
    expect(labels[0]).toBe('-'); // the mock's clear option
    expect(labels[1]).toBe('All agents');
  });

  it('picking an agent refetches with agent_id and clearing returns to mine', async () => {
    render(<MyCustomerAsksClient />);
    const select = await screen.findByLabelText('Agent');
    await screen.findByRole('option', { name: /WT I/ });
    fireEvent.change(select, { target: { value: 'agent-b' } });
    await waitFor(() => expect(getCustomerAsksTodo).toHaveBeenLastCalledWith('agent-b'));
    fireEvent.change(select, { target: { value: '' } });
    await waitFor(() => expect(getCustomerAsksTodo).toHaveBeenLastCalledWith(undefined));
  });

  it('a single picked agent renders no Agent column: only All agents does', async () => {
    render(<MyCustomerAsksClient />);
    const select = await screen.findByLabelText('Agent');
    await screen.findByRole('option', { name: /WT I/ });
    fireEvent.change(select, { target: { value: 'agent-b' } });
    await waitFor(() => expect(getCustomerAsksTodo).toHaveBeenLastCalledWith('agent-b'));
    await screen.findByText('Customer mine');
    showList();
    expect(screen.getAllByRole('columnheader').map((h) => (h.textContent ?? '').trim())).not.toContain('Agent');
  });

  it('All agents fetches agent_id=all and names the agent on each row', async () => {
    getCustomerAsksTodo.mockImplementation((agentId?: string) =>
      Promise.resolve(
        agentId === 'all'
          ? payload({ open: [ask('z1', { agent_code: 'WT I' })], agent: null })
          : payload(),
      ),
    );
    render(<MyCustomerAsksClient />);
    const select = await screen.findByLabelText('Agent');
    await screen.findByRole('option', { name: 'All agents' });
    fireEvent.change(select, { target: { value: 'all' } });
    await waitFor(() => expect(getCustomerAsksTodo).toHaveBeenLastCalledWith('all'));
    await screen.findByText('Customer z1');
    showList();
    expect(screen.getAllByRole('columnheader').map((h) => (h.textContent ?? '').trim())).toContain('Agent');
    expect(await screen.findByText('WT I')).toBeInTheDocument();
    expect(screen.queryByText('You are not linked to a sales agent')).toBeNull();
  });
});

describe('MyCustomerAsksClient remembered sort (AC-ST120, AC-ST304)', () => {
  it('reads the sort from the listing view preference under sales.customer_asks.view::todo', async () => {
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    expect(prefsCalls.length).toBeGreaterThan(0);
    expect(prefsCalls.every((c) => c.listingKey === 'sales.customer_asks.view::todo')).toBe(true);
  });

  it('applies a stored customer sort on open: rows inside a section are A to Z and Sort reads Customer', async () => {
    storedSorting = [{ id: 'customer_name', desc: false }];
    getCustomerAsksTodo.mockResolvedValue(
      payload({ open: [ask('zed', { customer_name: 'Zed Trading' }), ask('abe', { customer_name: 'Abe Trading' })] }),
    );
    render(<MyCustomerAsksClient />);
    await screen.findByText('Zed Trading');
    showCards();
    expect(screen.getAllByText(/Trading$/).map((n) => n.textContent)).toEqual(['Abe Trading', 'Zed Trading']);
    expect(screen.getByRole('button', { name: 'Sort' })).toHaveTextContent('Customer');
  });

  it('changing Sort in the toolbar writes the new entry through the hook setter', async () => {
    render(<MyCustomerAsksClient />);
    await screen.findByText('Customer mine');
    showCards();
    await openMenu('Sort');
    fireEvent.click(within(screen.getByRole('menu')).getByText('Customer'));
    await waitFor(() => expect(setSorting).toHaveBeenCalled());
    const arg = setSorting.mock.calls.at(-1)![0];
    const value = typeof arg === 'function' ? arg([]) : arg;
    expect(value).toEqual([{ id: 'customer_name', desc: false }]);
  });
});
