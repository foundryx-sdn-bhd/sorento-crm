/**
 * Fix round 6 on PR #1304 (owner hand test, 28 Sep 2026: "the structure of our summary
 * is quite messy"). The Conversations card shows the stored summary as the readable
 * sentence the chatbot's recall reply prints, under the same Topic word; When and
 * Turns stay their own columns, so the sentence carries no date and no turn count.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactChatbotSection from './ContactChatbotSection';
import type { ContactChatbotMemory } from '../services/contactChatbotService';

// `OpenOrdersCard`'s DataGrid carries a `rowHref`, which routes through
// `DataGridTable`'s `useRouter()` - not mounted under a plain `render()`.
vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/contacts/c1',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const useContactChatbotProfile = vi.fn();
const useContactChatbotMemory = vi.fn();
const mutate = vi.fn();
const memoryMutate = vi.fn();

vi.mock('../hooks/useContactChatbot', () => ({
  useContactChatbotProfile: (...a: unknown[]) => useContactChatbotProfile(...a),
  useSaveContactChatbotProfile: () => ({ mutate, isPending: false }),
  useSaveContactEscalation: () => ({ mutate: vi.fn(), isPending: false }),
  useContactChatbotMemory: (...a: unknown[]) => useContactChatbotMemory(...a),
  useSaveContactFact: () => ({ mutate: memoryMutate, isPending: false }),
  contactChatbotMemoryQueryKey: (contactId: string) => [
    'contact-chatbot-memory',
    contactId,
  ],
}));

const BASE_PROFILE = {
  chatbot_memory_level: null,
  tier: null,
  default_ledgers: [],
  stock_allowed: true,
  notify_salesman: false,
  packing_list_allowed: false,
};

function memoryFixture(
  overrides: Partial<ContactChatbotMemory> = {},
): ContactChatbotMemory {
  return {
    level: { own: null, effective: 'full', system_default: 'full' },
    facts: [],
    vocabulary: [],
    episodes: { kept: 0, limit: 20, current: null, rows: [] },
    open_orders: { customer_name: null, rows: [] },
    ...overrides,
  };
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

beforeEach(() => {
  useContactChatbotProfile.mockReset();
  useContactChatbotMemory.mockReset();
  mutate.mockReset();
  useContactChatbotProfile.mockReturnValue({
    data: BASE_PROFILE,
    isLoading: false,
    isError: false,
  });
});

afterEach(() => cleanup());

const SENTENCE_1 =
  'Asked about stock for SRTWC286 and got an answer. Still open: the offer to pass this to the warehouse team got no reply.';
const SENTENCE_2 = 'Asked about incoming stock and got an answer.';

describe('ContactChatbotSection - Conversations (fix round 6, readable summaries)', () => {
  beforeEach(() => {
    useContactChatbotMemory.mockReturnValue({
      data: memoryFixture({
        episodes: {
          kept: 2,
          limit: 20,
          current: {
            turn_count: 3,
            first_turn_id: 't9',
            started_at: '2026-09-28T03:12:34',
            summary: SENTENCE_2,
            domains: ['incoming'],
            topic: 'Incoming stock',
          },
          rows: [
            {
              id: 'f1',
              date: '2026-09-28T03:02:17',
              domains: ['inventory'],
              topic: 'Stock',
              summary: SENTENCE_1,
              turn_count: 7,
              close_reason: 'topic_switch',
              first_turn_id: 't1',
            },
          ],
        },
      }),
      isLoading: false,
      isError: false,
    });
  });

  it('keeps When, Topic, Summary, Turns and Ended by as columns', () => {
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    const headers = screen
      .getAllByRole('columnheader')
      .map((h) => h.textContent?.trim());
    for (const header of ['When', 'Topic', 'Summary', 'Turns', 'Ended by']) {
      expect(headers).toContain(header);
    }
  });

  it('shows the summary sentence word for word, with the full text as its title', () => {
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    const cell = screen.getByText(SENTENCE_1);
    expect(cell).toHaveAttribute('title', SENTENCE_1);
    expect(screen.getByText(SENTENCE_2)).toBeInTheDocument();
  });

  it('shows the recall reply Topic word, not the raw domain', () => {
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.getByText('Stock')).toBeInTheDocument();
    expect(screen.getByText('Incoming stock')).toBeInTheDocument();
    expect(screen.queryByText('Inventory')).not.toBeInTheDocument();
  });

  it('puts the turn count in the Turns column, never in the summary', () => {
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.queryByText(/\d+ turns?\b/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/\((answered|not found|escalated|declined)\)/),
    ).not.toBeInTheDocument();
  });
});
