/**
 * Reviewer findings on PR #1304, chatbot memory lane A.
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

describe('ContactChatbotSection - CRM fact rows (S11)', () => {
  it('renders no Delete button on a CRM-sourced fact row', () => {
    useContactChatbotMemory.mockReturnValue({
      data: memoryFixture({
        facts: [
          {
            key: 'segment',
            label: 'Segment',
            value: 'dealer',
            display: 'Dealer',
            source: 'crm',
            last_seen: null,
            editable: true,
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    expect(screen.queryByLabelText(/delete segment/i)).not.toBeInTheDocument();
  });

  it('still renders Delete on a staff-sourced editable fact row', () => {
    useContactChatbotMemory.mockReturnValue({
      data: memoryFixture({
        facts: [
          {
            key: 'note',
            label: 'Note',
            value: 'Prefers morning calls',
            display: 'Prefers morning calls',
            source: 'staff',
            last_seen: '2026-09-01',
            editable: true,
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    expect(screen.getByLabelText(/delete note/i)).toBeInTheDocument();
  });

  it('renders a CRM fact carrying a link as a Next Link with that href', () => {
    useContactChatbotMemory.mockReturnValue({
      data: memoryFixture({
        facts: [
          {
            key: 'customer',
            label: 'Customer',
            value: 'Chin Chun Trading (CC001)',
            display: 'Chin Chun Trading (CC001)',
            source: 'crm',
            last_seen: null,
            editable: false,
            // Real shape (captain correction): `/order-management/customers/<uuid>` -
            // the actual FE route is `app/(protected)/order-management/customers/[id]`.
            // Rendered as-is, whatever the backend sends.
            link: '/order-management/customers/11111111-1111-1111-1111-111111111111',
          },
        ],
      }),
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    const link = screen.getByRole('link', { name: 'Chin Chun Trading (CC001)' });
    expect(link).toHaveAttribute(
      'href',
      '/order-management/customers/11111111-1111-1111-1111-111111111111',
    );
  });
});

describe('ContactChatbotSection - no explanation prose or raw codes in the UI (N7)', () => {
  it('never renders "answered on the next pick" or "See Chat History for the raw transcript"', () => {
    useContactChatbotMemory.mockReturnValue({ data: memoryFixture(), isLoading: false, isError: false });
    const { container } = renderWithClient(<ContactChatbotSection contactId="c1" />);

    expect(container.textContent).not.toMatch(/answered on the next pick/i);
    expect(container.textContent).not.toMatch(/see chat history for the raw transcript/i);
  });
});

describe('ContactChatbotSection - a null order date (N8)', () => {
  it('renders a dash, never the epoch (01/01/1970)', () => {
    useContactChatbotMemory.mockReturnValue({
      data: memoryFixture({
        open_orders: {
          customer_name: 'Chin Chun Trading',
          rows: [
            {
              document: 'SO-1001',
              kind: 'sales_order',
              status: 'Open',
              summary: '',
              date: null,
              href: '/scm/sales-orders/so-1',
            },
          ],
        },
      }),
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    expect(screen.queryByText('01/01/1970')).not.toBeInTheDocument();
  });
});
