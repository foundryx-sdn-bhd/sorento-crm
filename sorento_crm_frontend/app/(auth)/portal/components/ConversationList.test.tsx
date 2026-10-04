/**
 * Lane SALES-CONVO, the Conversation kind's body (AC-CV4 to AC-CV15): cards latest first with
 * the "Customer wrote last" tint, the DataGrid list with the four columns, the remembered sort,
 * the empty states, and a card opening the read-only thread (`RespondChatList`, no composer)
 * in the bottom Drawer. The service functions are mocked; the thread component is real.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  fireEvent,
  render as rtlRender,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

function render(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return rtlRender(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/portal/c/ah-lim',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock(
  '@/lib/listing-column-preferences/listColumnPreferencesService',
  () => ({
    getUserListColumnConfig: vi.fn().mockResolvedValue(null),
    upsertUserListColumnConfig: vi.fn(),
    resetUserListColumnConfig: vi.fn(),
  }),
);
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const listConversations = vi.fn();
const getConversationPage = vi.fn();
const searchConversation = vi.fn();
vi.mock('../lib/conversations-service', async () => {
  const actual = await vi.importActual<
    typeof import('../lib/conversations-service')
  >('../lib/conversations-service');
  return {
    ...actual,
    listConversations: (...a: unknown[]) => listConversations(...a),
    getConversationPage: (...a: unknown[]) => getConversationPage(...a),
    searchConversation: (...a: unknown[]) => searchConversation(...a),
  };
});

import { NotASalesAgentError } from '../lib/customer-asks-service';
import { ConversationList as Body } from './ConversationList';

const CHIN = {
  contact_id: 'c-chin',
  customer_name: 'Chin Chun Hardware',
  customer_code: 'CCH',
  contact_name: 'Mr. Chin',
  contact_phone: '+60123456789',
  last_message_at: '2026-09-30T01:26:00',
  last_message_snippet: 'Ok hold for me first. Price same as last time?',
  last_message_direction: 'incoming' as const,
};
const TAN = {
  contact_id: 'c-tan',
  customer_name: 'Tan Home Living',
  customer_code: 'THL',
  contact_name: 'Mr. Tan',
  contact_phone: '+60190000520',
  last_message_at: '2026-09-29T09:05:00',
  last_message_snippet: 'Your price tag request PT-202609-0031 is ready.',
  last_message_direction: 'outgoing' as const,
};

const PAGE = {
  items: [
    {
      messageId: 1700000000000,
      traffic: 'incoming',
      message: { type: 'text', text: 'Hi, 60x60 grey matt tile still have?' },
      timestamp: 1759194000000,
    },
    {
      messageId: 1700000000001,
      traffic: 'outgoing',
      message: {
        type: 'text',
        text: 'SR-6060-GM: 32 boxes in stock at Sorento.',
      },
      timestamp: 1759194060000,
    },
  ],
  has_more_older: false,
  has_more_newer: false,
  oldest_message_id: '1700000000000',
  newest_message_id: '1700000000001',
};

/** Radix triggers open on a real pointerdown / pointerup / click sequence. */
async function openMenu(name: string) {
  const trigger = screen.getByRole('button', { name });
  fireEvent.pointerDown(trigger, { button: 0, pointerId: 1 });
  fireEvent.pointerUp(trigger, { button: 0, pointerId: 1 });
  fireEvent.click(trigger);
  await waitFor(() =>
    expect(trigger.getAttribute('aria-expanded')).toBe('true'),
  );
}

const onViewChangeSpy = vi.fn();
function ConversationList(props: {
  search?: string;
  contactId?: string | null;
  initialView?: 'list' | 'board';
}) {
  const [view, setView] = React.useState<'list' | 'board'>(
    props.initialView ?? 'board',
  );
  return (
    <Body
      search={props.search ?? ''}
      contactId={props.contactId ?? 'contact-1'}
      view={view}
      onViewChange={(m) => {
        onViewChangeSpy(m);
        setView(m);
      }}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  listConversations.mockResolvedValue({
    data: [CHIN, TAN],
    pagination: { total: 2, page: 1, limit: 200 },
  });
  getConversationPage.mockResolvedValue(PAGE);
  searchConversation.mockResolvedValue([]);
});

describe('ConversationList - cards (AC-CV5, AC-CV6, AC-CV7)', () => {
  it('renders one card per row, latest first, the incoming one tinted and labelled', async () => {
    render(<ConversationList />);
    expect(await screen.findByText('Chin Chun Hardware')).toBeInTheDocument();
    const cards = screen.getAllByRole('button', {
      name: /Hardware|Home Living/,
    });
    expect(cards.map((c) => c.textContent)).toEqual([
      expect.stringContaining('Chin Chun Hardware'),
      expect.stringContaining('Tan Home Living'),
    ]);
    const chin = screen.getByTestId('conversation-card-c-chin');
    expect(chin).toHaveTextContent('Mr. Chin');
    expect(chin).toHaveTextContent('+60123456789');
    expect(chin).toHaveTextContent(
      'Ok hold for me first. Price same as last time?',
    );
    expect(chin).toHaveTextContent('Customer wrote last');
    expect(
      within(chin).getByLabelText('Last message was incoming'),
    ).toBeInTheDocument();
    expect(chin.className).toContain('bg-primary/5');
    const tan = screen.getByTestId('conversation-card-c-tan');
    expect(tan).not.toHaveTextContent('Customer wrote last');
    expect(
      within(tan).getByLabelText('Last message was outgoing'),
    ).toBeInTheDocument();
    expect(tan.className).not.toContain('bg-primary/5');
    // No id anywhere on screen.
    expect(screen.queryByText('c-chin')).toBeNull();
  });

  it('sorts by Customer when asked and remembers the choice per contact', async () => {
    render(<ConversationList contactId="contact-9" />);
    await screen.findByText('Chin Chun Hardware');
    await openMenu('Sort');
    fireEvent.click(within(screen.getByRole('menu')).getByText('Customer'));
    await waitFor(() =>
      expect(
        JSON.parse(
          window.localStorage.getItem(
            'sorento.portalConversationSort.contact-9',
          ) ?? '{}',
        ),
      ).toEqual({
        key: 'customer_name',
        dir: 'asc',
      }),
    );
    // The menu stays open after a pick (R3-3: a second tap on the active field flips its
    // direction); Tan sorts after Chin ascending, so descending puts Tan first.
    fireEvent.click(within(screen.getByRole('menu')).getByText('Customer'));
    // By test id: the open menu hides the page from the accessibility tree.
    await waitFor(() => {
      const cards = screen.getAllByTestId(/^conversation-card-/);
      expect(cards[0]).toHaveTextContent('Tan Home Living');
    });
  });

  it('filters to the customers who wrote last', async () => {
    render(<ConversationList />);
    await screen.findByText('Tan Home Living');
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
    const combos = await screen.findAllByRole('combobox');
    const lastFrom = combos.find((c) =>
      c.textContent?.includes('Any last message from'),
    );
    expect(lastFrom).toBeTruthy();
    fireEvent.click(lastFrom!);
    fireEvent.click(await screen.findByRole('option', { name: 'Customer' }));
    await waitFor(() =>
      expect(screen.queryByText('Tan Home Living')).toBeNull(),
    );
    expect(screen.getByText('Chin Chun Hardware')).toBeInTheDocument();
  });
});

describe('ConversationList - list view (AC-CV8)', () => {
  it('shows the four columns in the DataGrid and keeps the landing toggle', async () => {
    render(<ConversationList initialView="list" />);
    await screen.findByText('Chin Chun Hardware');
    const headers = screen
      .getAllByRole('columnheader')
      .map((h) => (h.textContent ?? '').trim());
    expect(headers).toEqual(['Customer', 'Contact', 'Last message', 'When']);
    expect(screen.getAllByLabelText('View mode')).toHaveLength(1);
    // Sort is the column header in List view; the toolbar's Sort button is Cards only.
    expect(screen.queryByRole('button', { name: 'Sort' })).toBeNull();
    fireEvent.click(screen.getByRole('radio', { name: 'Board view' }));
    expect(onViewChangeSpy).toHaveBeenCalledWith('board');
  });
});

describe('ConversationList - empty states and errors (AC-CV3, AC-CV10)', () => {
  it('says so when none of my customers has a chat', async () => {
    listConversations.mockResolvedValue({
      data: [],
      pagination: { total: 0, page: 1, limit: 200 },
    });
    render(<ConversationList />);
    expect(await screen.findByText('No conversations yet')).toBeInTheDocument();
    expect(
      screen.getByText('None of your customers has messaged us on WhatsApp.'),
    ).toBeInTheDocument();
  });

  it('names the search when it matched nothing', async () => {
    listConversations.mockResolvedValue({
      data: [],
      pagination: { total: 0, page: 1, limit: 200 },
    });
    render(<ConversationList search="paip" />);
    expect(
      await screen.findByText('No conversation matches "paip"'),
    ).toBeInTheDocument();
  });

  it('tells a contact that is no sales agent', async () => {
    listConversations.mockRejectedValue(new NotASalesAgentError());
    render(<ConversationList />);
    expect(
      await screen.findByText('Conversations are for sales agents only.'),
    ).toBeInTheDocument();
  });
});

describe('ConversationList - the opened thread (AC-CV12, AC-CV15)', () => {
  it('opens the shared chat list in the Drawer, read-only', async () => {
    render(<ConversationList />);
    fireEvent.click(await screen.findByTestId('conversation-card-c-chin'));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() =>
      expect(getConversationPage).toHaveBeenCalledWith('c-chin', { limit: 50 }),
    );
    expect(
      await within(dialog).findByText('Hi, 60x60 grey matt tile still have?'),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText('SR-6060-GM: 32 boxes in stock at Sorento.'),
    ).toBeInTheDocument();
    // The thread header: the customer and the contact, the phone, the search affordance.
    expect(
      within(dialog).getByText('Chin Chun Hardware · Mr. Chin'),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('+60123456789')).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: 'Search messages' }),
    ).toBeInTheDocument();
    // Read-only (Q1): no composer, no mode switch, no note.
    expect(within(dialog).queryByRole('tab', { name: 'Reply' })).toBeNull();
    expect(within(dialog).queryByRole('tab', { name: 'Comment' })).toBeNull();
    expect(within(dialog).queryByRole('textbox')).toBeNull();
    expect(within(dialog).queryByRole('button', { name: /Send/ })).toBeNull();
  });

  it('a row in List view opens the same Drawer', async () => {
    render(<ConversationList initialView="list" />);
    fireEvent.click(await screen.findByText('Tan Home Living'));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() =>
      expect(getConversationPage).toHaveBeenCalledWith('c-tan', { limit: 50 }),
    );
    expect(
      await within(dialog).findByText('Tan Home Living · Mr. Tan'),
    ).toBeInTheDocument();
  });
});
