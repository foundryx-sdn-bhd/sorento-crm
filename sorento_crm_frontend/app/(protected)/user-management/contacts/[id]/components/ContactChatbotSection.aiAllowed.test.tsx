/**
 * AI-ALLOWED-GATE (AC-AI-9): the Contact > Chatbot card gains an "AI replies allowed" switch,
 * id `contact-chatbot-ai-allowed`, bound to `profile.chatbot_allowed` and saved through its OWN
 * mutation (`useSaveContactChatbotAllowed`), never the card save, so a stale page cannot switch
 * it back on. RED until the section renders the switch and the hook exists.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactChatbotSection from './ContactChatbotSection';

const useContactChatbotProfile = vi.fn();
const mutate = vi.fn();
const escalationMutate = vi.fn();
const aiAllowedMutate = vi.fn();
const memoryMutate = vi.fn();

vi.mock('../hooks/useContactChatbot', () => ({
  useContactChatbotProfile: (...a: unknown[]) => useContactChatbotProfile(...a),
  useSaveContactChatbotProfile: () => ({ mutate, isPending: false }),
  useSaveContactEscalation: () => ({ mutate: escalationMutate, isPending: false }),
  useSaveContactChatbotAllowed: () => ({ mutate: aiAllowedMutate, isPending: false }),
  useContactChatbotMemory: () => ({
    data: {
      level: { own: null, effective: 'off', system_default: 'off' },
      facts: [],
      vocabulary: [],
      episodes: { kept: 0, limit: 20, current: null, rows: [] },
      open_orders: { customer_name: null, rows: [] },
    },
    isLoading: false,
    isError: false,
  }),
  useSaveContactFact: () => ({ mutate: memoryMutate, isPending: false }),
  contactChatbotMemoryQueryKey: (contactId: string) => ['contact-chatbot-memory', contactId],
}));

vi.mock('@/components/common/SearchableSelect', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('@/components/common/SearchableSelect')>();
  type Props = Parameters<typeof actual.SearchableSelect>[0];
  const Stub = ({ value, onChange, options, placeholder, disabled, clearable }: Props) => (
    <select
      aria-label={placeholder ?? 'select'}
      data-clearable={clearable === undefined ? 'unset' : String(clearable)}
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      {(options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
  return { ...actual, SearchableSelect: (props: Props) => <Stub {...props} /> };
});

const BASE_PROFILE = {
  chatbot_memory_level: null,
  tier: null,
  default_ledgers: [],
  stock_allowed: true,
  notify_salesman: false,
  packing_list_allowed: false,
  eta_offset_applied: true,
  escalation_allowed: true,
  chatbot_allowed: true,
};

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useContactChatbotProfile.mockReset();
  mutate.mockReset();
  escalationMutate.mockReset();
  aiAllowedMutate.mockReset();
});

afterEach(() => cleanup());

describe('ContactChatbotSection - AI replies allowed switch (AI-ALLOWED-GATE)', () => {
  it('renders the switch with its label and the owner hint', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    expect(screen.getByLabelText('AI replies allowed')).toBeInTheDocument();
    expect(document.getElementById('contact-chatbot-ai-allowed')).not.toBeNull();
    expect(
      screen.getByText('When off, the chatbot sends this contact nothing, not even an error.'),
    ).toBeInTheDocument();
  });

  it('renders checked for a contact the AI may answer', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.getByLabelText('AI replies allowed')).toHaveAttribute('data-state', 'checked');
  });

  it('renders unchecked for a contact the AI may not answer', () => {
    useContactChatbotProfile.mockReturnValue({
      data: { ...BASE_PROFILE, chatbot_allowed: false },
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.getByLabelText('AI replies allowed')).toHaveAttribute('data-state', 'unchecked');
  });

  it('unticking saves through its own mutation with false, never the card save or the escalation save', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    fireEvent.click(screen.getByLabelText('AI replies allowed'));

    expect(aiAllowedMutate).toHaveBeenCalledTimes(1);
    expect(aiAllowedMutate.mock.calls[0][0]).toBe(false);
    expect(mutate).not.toHaveBeenCalled();
    expect(escalationMutate).not.toHaveBeenCalled();
  });

  it('ticking a blocked contact saves true through the same mutation', () => {
    useContactChatbotProfile.mockReturnValue({
      data: { ...BASE_PROFILE, chatbot_allowed: false },
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    fireEvent.click(screen.getByLabelText('AI replies allowed'));

    expect(aiAllowedMutate).toHaveBeenCalledTimes(1);
    expect(aiAllowedMutate.mock.calls[0][0]).toBe(true);
    expect(mutate).not.toHaveBeenCalled();
  });
});
