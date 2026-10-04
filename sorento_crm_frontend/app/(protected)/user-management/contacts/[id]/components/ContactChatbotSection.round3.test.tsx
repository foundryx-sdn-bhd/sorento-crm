/**
 * Fix lane round 3 on PR #1304 (owner hand test, 28 Sep 2026), on the setup of
 * `ContactChatbotSection.reviewFixes.test.tsx`: the Conversations card shows the
 * console's own open and closed conversations, marked Console, and the open
 * conversation's Topic is its current domain (R2), never a dash.
 *
 * Setup notes from the file it was copied from: reviewer findings on PR #1304, chatbot memory lane A.
 *
 * S11 (FE part): a CRM-sourced fact (`source: 'crm'`) still shows Delete even though
 * nothing is stored for it to delete - the countdown commits and changes nothing. A
 * CRM fact whose backend record carries `link` (e.g. `customer`) renders as a Next
 * `Link`, not plain text.
 *
 * N7: no explanation prose in the UI (repo rule) and no raw backend codes ("no
 * conversations recorded yet. See Chat History for the raw transcript." /
 * "(none) - answered on the next pick").
 *
 * N8: a null order date must not render as the epoch ("01/01/1970").
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
  contactChatbotMemoryQueryKey: (contactId: string) => ['contact-chatbot-memory', contactId],
}));

const BASE_PROFILE = {
  chatbot_memory_level: null,
  tier: null,
  default_ledgers: [],
  stock_allowed: true,
  notify_salesman: false,
  packing_list_allowed: false,
};

function memoryFixture(overrides: Partial<ContactChatbotMemory> = {}): ContactChatbotMemory {
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
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useContactChatbotProfile.mockReset();
  useContactChatbotMemory.mockReset();
  mutate.mockReset();
  useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
});

afterEach(() => cleanup());

describe('ContactChatbotSection - Conversations (fix lane round 3)', () => {
  it('shows the open conversation Topic and a console episode marked Console', () => {
    useContactChatbotMemory.mockReturnValue({
      data: memoryFixture({
        episodes: {
          kept: 0,
          console_kept: 1,
          limit: 20,
          current: null,
          console_current: {
            turn_count: 1,
            first_turn_id: 't5',
            started_at: '2026-09-28T03:12:34',
            summary: 'Sun 28 Sep, 1 turns: incoming SRTWC286 (answered).',
            domains: ['incoming'],
          },
          rows: [
            {
              id: 'f1',
              date: '2026-09-28T03:02:17',
              domains: ['inventory'],
              summary: 'Sun 28 Sep, 4 turns: inventory SRTWC286 (answered); inventory SRTWC287 (answered).',
              turn_count: 4,
              close_reason: 'topic_switch',
              first_turn_id: 't1',
              console: true,
            },
          ],
        },
      }),
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    expect(screen.getByText('Incoming')).toBeInTheDocument();
    expect(screen.getByText('Inventory')).toBeInTheDocument();
    expect(screen.getAllByText('Console')).toHaveLength(2);
    expect(screen.getByText('Topic switch')).toBeInTheDocument();
    expect(screen.getByText(/0 kept of 20, 1 console/)).toBeInTheDocument();
  });

  it('marks no row Console when every conversation is live', () => {
    useContactChatbotMemory.mockReturnValue({
      data: memoryFixture({
        episodes: {
          kept: 0,
          limit: 20,
          current: {
            turn_count: 2,
            first_turn_id: 't1',
            started_at: '2026-09-28T03:01:43',
            summary: 'Sun 28 Sep, 2 turns: inventory SRTWC286 (answered).',
            domains: ['inventory'],
          },
          rows: [],
        },
      }),
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    expect(screen.getByText('Inventory')).toBeInTheDocument();
    expect(screen.queryByText('Console')).not.toBeInTheDocument();
  });
});
