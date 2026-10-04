/**
 * S6 (coordinator ruling, 16 Sep 2026): stock allowance moves onto the CRM contact,
 * default ON. `ContactChatbotSection` gains a "Stock checks" Switch bound to
 * `profile.stock_allowed`, checked by default; a change sends `stock_allowed` through
 * `save.mutate`. RIGHT NOW every test here is RED - the component renders no such
 * Switch and `ContactChatbotProfile` carries no `stock_allowed` field yet.
 *
 * No existing test file covered this component (checked: no `ContactChatbotSection.
 * test.tsx` in this tree before this one - the coordinator's "extend the existing
 * page/section test file... if one covers the recall switch" does not apply, there was
 * none to extend).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactChatbotSection from './ContactChatbotSection';

const useContactChatbotProfile = vi.fn();
const mutate = vi.fn();
const escalationMutate = vi.fn();
const memoryMutate = vi.fn();

// Chatbot memory lane A: `chatbot_memory_level` replaces `recall_enabled`, `language`
// moved into the facts grid (`useContactChatbotMemory`) and `always_full_report` is
// dead. The empty memory fixture below keeps every test in this file - none of which
// assert on facts/conversations/open orders - rendering the same empty states those
// new cards fall back to, rather than an undefined crash.
vi.mock('../hooks/useContactChatbot', () => ({
  useContactChatbotProfile: (...a: unknown[]) => useContactChatbotProfile(...a),
  useSaveContactChatbotProfile: () => ({ mutate, isPending: false }),
  useSaveContactEscalation: () => ({ mutate: escalationMutate, isPending: false }),
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

// AC-MEM057 (round 3): stubbed as a deterministic native `<select>` so the memory
// context level options/values/clearable-ness are asserted directly, the same
// pattern `StockVisibilitySection.test.tsx` uses for the same real component.
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
};

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useContactChatbotProfile.mockReset();
  mutate.mockReset();
  escalationMutate.mockReset();
});

afterEach(() => cleanup());

describe('ContactChatbotSection - stock checks switch (S6)', () => {
  it('renders a Stock checks switch, checked by default', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    const toggle = screen.getByLabelText(/stock checks/i);
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute('data-state', 'checked');
  });

  it('renders the switch unchecked when the contact is denied stock checks', () => {
    useContactChatbotProfile.mockReturnValue({
      data: { ...BASE_PROFILE, stock_allowed: false },
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.getByLabelText(/stock checks/i)).toHaveAttribute('data-state', 'unchecked');
  });

  it('flipping the switch sends stock_allowed through save.mutate', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    fireEvent.click(screen.getByLabelText(/stock checks/i));
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({ ...BASE_PROFILE, stock_allowed: false });
  });
});

/**
 * Chatbot stock ask v2, Slice S2 (contact toggles) - AC-SA205.
 * `documentation/plans/chatbot/PLAN-chatbot-stock-ask-v2-24sep.md` "S2 - Contact
 * toggles"; `chatbot-stock-ask-v2-24sep-acceptance-criteria.md` AC-SA205.
 *
 * The component ALREADY renders both "Notify salesman" and "Packing list allowed"
 * switches (Phase 1, against `contactChatbotService.ts`'s in-memory mock overlay).
 * These tests mock `useContactChatbot` directly - the hook the component actually
 * calls - bypassing that overlay entirely, so they exercise the LIVE field shape S2
 * lands on the backend (`notify_salesman` / `packing_list_allowed` as plain profile
 * fields), not the Phase 1 mock. They are expected to already be green against the
 * current component, since the switches were built in Phase 1; they pin the contract
 * so the coder cannot regress it while deleting the mock overlay in `contactChatbotService.ts`.
 */
describe('ContactChatbotSection - contact toggles (S2, AC-SA205)', () => {
  it('renders "Notify salesman" and "Packing list allowed" switches reflecting the loaded profile', () => {
    useContactChatbotProfile.mockReturnValue({
      data: { ...BASE_PROFILE, notify_salesman: true, packing_list_allowed: false },
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    const notify = screen.getByLabelText(/notify salesman/i);
    expect(notify).toBeInTheDocument();
    expect(notify).toHaveAttribute('data-state', 'checked');

    const packingList = screen.getByLabelText(/packing list allowed/i);
    expect(packingList).toBeInTheDocument();
    expect(packingList).toHaveAttribute('data-state', 'unchecked');
  });

  it('toggling "Notify salesman" saves the whole profile with every other field unchanged', () => {
    const loaded = {
      ...BASE_PROFILE,
      chatbot_memory_level: 'full',
      tier: 'dealer',
      stock_allowed: true,
      notify_salesman: false,
      packing_list_allowed: true,
    };
    useContactChatbotProfile.mockReturnValue({ data: loaded, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    fireEvent.click(screen.getByLabelText(/notify salesman/i));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({ ...loaded, notify_salesman: true });
  });

  it('toggling "Packing list allowed" saves the whole profile with every other field unchanged, including notify_salesman', () => {
    const loaded = {
      ...BASE_PROFILE,
      chatbot_memory_level: null,
      tier: null,
      stock_allowed: true,
      notify_salesman: true,
      packing_list_allowed: false,
    };
    useContactChatbotProfile.mockReturnValue({ data: loaded, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    fireEvent.click(screen.getByLabelText(/packing list allowed/i));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({ ...loaded, packing_list_allowed: true });
  });
});

/**
 * AC-MEM057 (round 3 UAC, merged 5b110df8): the Contact page "Memory context level"
 * select's option set is renamed to the round 3 level values (`past` -> `episodes`),
 * stays clearable, and its `onChange` payload is asserted against
 * `contactChatbotService.memoryLevel.test.ts` (this file mocks the save HOOK, so it
 * cannot see the outgoing HTTP body key `memory_level` renamed from
 * `chatbot_memory_level` - that is pinned at the service layer instead).
 */
describe('ContactChatbotSection - memory context level select (AC-MEM057, round 3)', () => {
  function levelSelect() {
    return screen.getByLabelText('(follow the system default)') as HTMLSelectElement;
  }

  it('offers exactly Off / This conversation / Past conversations / Full memory, with values off/conversation/episodes/full', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    const options = Array.from(levelSelect().options).map((o) => ({
      value: o.value,
      label: o.textContent,
    }));
    expect(options).toEqual([
      { value: 'off', label: 'Off' },
      { value: 'conversation', label: 'This conversation' },
      { value: 'episodes', label: 'Past conversations' },
      { value: 'full', label: 'Full memory' },
    ]);
  });

  it('is clearable', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(levelSelect()).toHaveAttribute('data-clearable', 'true');
  });

  it('picking "Past conversations" saves chatbot_memory_level as "episodes", not "past"', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);

    fireEvent.change(levelSelect(), { target: { value: 'episodes' } });

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({ ...BASE_PROFILE, chatbot_memory_level: 'episodes' });
  });
});

/**
 * Issue #1328 (AC-EO6): the per-contact ETA offset switch, default on, saved with the
 * rest of the profile unchanged.
 */
describe('ContactChatbotSection - ETA buffer days switch (#1328)', () => {
  it('renders the switch checked by default', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.getByLabelText(/eta buffer days/i)).toHaveAttribute('data-state', 'checked');
  });

  it('renders unchecked for a contact told the exact ETA', () => {
    useContactChatbotProfile.mockReturnValue({
      data: { ...BASE_PROFILE, eta_offset_applied: false },
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.getByLabelText(/eta buffer days/i)).toHaveAttribute('data-state', 'unchecked');
  });

  it('flipping it saves eta_offset_applied with every other field unchanged', () => {
    const loaded = { ...BASE_PROFILE, notify_salesman: true, packing_list_allowed: true };
    useContactChatbotProfile.mockReturnValue({ data: loaded, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    fireEvent.click(screen.getByLabelText(/eta buffer days/i));
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({ ...loaded, eta_offset_applied: false });
  });
});

/**
 * ESCALATION-CONTROL (owner change, 30 Sep 2026): one per-contact switch, "Chatbot hands
 * over to support teams" (owner-approved label, 1 Oct 2026), default on; access types no
 * longer decide it.
 */
describe('ContactChatbotSection - Chatbot hands over to support teams switch', () => {
  it('shows the owner-approved helper text under the switch', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(
      screen.getByText('When off, the chatbot tells this contact to refer to their salesman.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Can escalate to a person')).not.toBeInTheDocument();
  });

  it('renders checked for a contact that may escalate', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.getByLabelText('Chatbot hands over to support teams')).toHaveAttribute('data-state', 'checked');
  });

  it('renders unchecked for a blocked contact', () => {
    useContactChatbotProfile.mockReturnValue({
      data: { ...BASE_PROFILE, escalation_allowed: false },
      isLoading: false,
      isError: false,
    });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.getByLabelText('Chatbot hands over to support teams')).toHaveAttribute('data-state', 'unchecked');
  });

  it('unticking saves through its own mutation, never the card save (security review S3)', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    fireEvent.click(screen.getByLabelText('Chatbot hands over to support teams'));
    expect(escalationMutate).toHaveBeenCalledTimes(1);
    expect(escalationMutate.mock.calls[0][0]).toBe(false);
    expect(mutate).not.toHaveBeenCalled();
  });

  it('shows no inherited-from-access-type text any more', () => {
    useContactChatbotProfile.mockReturnValue({ data: BASE_PROFILE, isLoading: false, isError: false });
    renderWithClient(<ContactChatbotSection contactId="c1" />);
    expect(screen.queryByText(/Inherited/)).not.toBeInTheDocument();
  });
});
