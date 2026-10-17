/**
 * Settings -> Chatbot (AC-809, AC-810, issue #679).
 *
 * RED, written before `./page.tsx` exists. Prescribes the contract the coder
 * builds against, mirroring the precedent at `../chatbot-media/page.test.tsx`:
 * the hooks module is mocked directly (never `fetch` / `apiFetch`), so each test
 * can drive query/mutation state precisely.
 *
 * Expected hooks module: `./hooks/useChatbotSettings.ts`, exporting:
 *   - `useChatbotSettings()`   -> query over the existing GET /settings, picking
 *                                 the four chatbot fields into `ChatbotSettings`
 *   - `useSaveChatbotSettings()` -> mutation over POST /settings/general with a
 *                                 `ChatbotSettings` body
 *
 * Expected service module: `./services/chatbotSettingsService.ts`, exporting the
 * type below and the two fetchers the hooks wrap.
 *
 * ```ts
 * export interface ChatbotSettings {
 *   chatbot_stock_denial_enabled: boolean;
 *   chatbot_business_lane_enabled: boolean;
 *   chatbot_ordering_enabled: boolean;
 *   chatbot_unsupported_domains: string[];
 * }
 * ```
 *
 * No feature-explanation copy in the UI (cursor rule).
 *
 * retired: the "Lanes the CRM answers" card and `chatbot_completed_lanes` gating
 * are superseded (PLAN-chatbot-turn-rearch S3 rulings, contract line 73) - the
 * per-branch-kind checkbox grid, its `useChatbotLanes()` hook and `ChatbotLane`
 * type are gone from this contract.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

Element.prototype.scrollIntoView = vi.fn();
(Element.prototype as unknown as { hasPointerCapture: unknown }).hasPointerCapture = vi.fn();
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const mockSettingsQuery = vi.fn();
const mockMutation = vi.fn();
const mockMemoryQuery = vi.fn();
const mockMemoryMutation = vi.fn();
const mockTierOrderQuery = vi.fn();
const mockTierOrderMutation = vi.fn();

vi.mock('./hooks/useChatbotSettings', () => ({
  useChatbotSettings: () => mockSettingsQuery(),
  useSaveChatbotSettings: () => mockMutation(),
}));

// The Memory and Tier order cards' own hook module (browser pass 1, 16 Sep 2026,
// finding: "Switches card has no Save button of its own - the Memory card's Save
// sits right under it and silently no-ops the switches"). Mocked here with real
// data so each card renders its REAL current Save button and the "exactly one Save
// button" assertion below is a genuine red today. The `CrossDomainLadderCard` join
// below is the SAME consolidation, added 16 Sep 2026 browser pass 2 - it was
// EXPLICITLY left out of scope in an earlier pass ("saves a different resource,
// chatbot_domains not system_settings") but that call is superseded: it still
// renders its own separate Save button today, which is the bug this file now pins.
vi.mock('./hooks/useChatbotMemoryAndTierOrder', () => ({
  useChatbotMemorySettings: () => mockMemoryQuery(),
  useSaveChatbotMemorySettings: () => mockMemoryMutation(),
  useChatbotTierOrder: () => mockTierOrderQuery(),
  useSaveChatbotTierOrder: () => mockTierOrderMutation(),
}));

// Urgent finding, 16 Sep 2026 (browser pass 2, supersedes the "not part of this
// consolidation" note above): the ladder card's own separate Save button was IN
// scope too - the ruling is one Save that also fires the ladder's PUT. Coder landed
// this (`CrossDomainLadderCard.tsx` no longer owns a Save of its own); the describe
// block below pins it green: exactly one Save button once the ladder carries real
// data, and clicking it fires `useUpdateChatbotDomain`'s mutate with the reordered
// list for the inventory domain row. Mocked at the hooks module
// `CrossDomainLadderCard` itself imports, so the assertions below drive the REAL
// rendered ladder DOM (OrderableList's own "Move X down" button), not a poked prop.
const mockDomainsQuery = vi.fn();
const mockUpdateDomain = vi.fn();

vi.mock(
  '@/app/(protected)/system-management/chatbot-domains/hooks/useChatbotDomains',
  () => ({
    useChatbotDomainsQuery: () => mockDomainsQuery(),
    useUpdateChatbotDomain: () => mockUpdateDomain(),
  }),
);

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn() },
}));

import ChatbotSettingsPage from './page';
import type { ChatbotSettings } from './services/chatbotSettingsService';

function settings(overrides: Partial<ChatbotSettings> = {}): ChatbotSettings {
  return {
    chatbot_stock_denial_enabled: false,
    chatbot_business_lane_enabled: false,
    chatbot_ordering_enabled: false,
    chatbot_unsupported_domains: ['goods_receive', 'spo_allocation'],
    chatbot_direct_ingress_mode: 'off',
    chatbot_direct_ingress_contacts: null,
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatbotSettingsPage />
    </QueryClientProvider>,
  );
}

const saveButton = () => screen.getByRole('button', { name: /save/i });

const DEFAULT_MEMORY = {
  enabled: false,
  default_level: 'full',
  own_level_count: 1,
};
const DEFAULT_TIER_ORDER = ['dealer', 'office', 'end_user'];

const DEFAULT_LADDER_DOMAINS = [
  {
    id: 'dom-inventory',
    name: 'inventory',
    label: 'Inventory',
    intents: [],
    tools: [],
    primary_tool: null,
    escalation_team_code: null,
    switch_words: [],
    narrowing: {},
    takes_date_filter: false,
    reveal_key: null,
    supported: true,
    ladder: ['incoming', 'purchase_order'],
    updated_at: '2026-09-16T00:00:00Z',
  },
  {
    id: 'dom-incoming',
    name: 'incoming',
    label: 'Incoming',
    intents: [],
    tools: [],
    primary_tool: null,
    escalation_team_code: null,
    switch_words: [],
    narrowing: {},
    takes_date_filter: false,
    reveal_key: null,
    supported: true,
    ladder: [],
    updated_at: '2026-09-16T00:00:00Z',
  },
  {
    id: 'dom-purchase-order',
    name: 'purchase_order',
    label: 'Purchase order',
    intents: [],
    tools: [],
    primary_tool: null,
    escalation_team_code: null,
    switch_words: [],
    narrowing: {},
    takes_date_filter: false,
    reveal_key: null,
    supported: true,
    ladder: [],
    updated_at: '2026-09-16T00:00:00Z',
  },
];

beforeEach(() => {
  mockSettingsQuery.mockReset();
  mockMutation.mockReset();
  mockMemoryQuery.mockReset();
  mockMemoryMutation.mockReset();
  mockTierOrderQuery.mockReset();
  mockTierOrderMutation.mockReset();
  mockDomainsQuery.mockReset();
  mockUpdateDomain.mockReset();
  mockSettingsQuery.mockReturnValue({ data: settings(), isLoading: false, isError: false });
  mockMutation.mockReturnValue({ isPending: false, mutate: vi.fn() });
  // Left LOADING by default (not the real data above) so every pre-existing test in
  // this file, which never asserted on Memory/Tier order/ladder, keeps seeing
  // exactly the ONE "Save" button it always has (the Switches/page-bottom one) -
  // only the consolidated-Save describe block below opts into real data for these.
  mockMemoryQuery.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  mockMemoryMutation.mockReturnValue({ isPending: false, mutate: vi.fn() });
  mockTierOrderQuery.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  mockTierOrderMutation.mockReturnValue({ isPending: false, mutate: vi.fn() });
  mockDomainsQuery.mockReturnValue({ data: undefined, isLoading: true, isError: false });
  mockUpdateDomain.mockReturnValue({ isPending: false, mutate: vi.fn() });
});

afterEach(() => cleanup());

describe('ChatbotSettingsPage - the three switches (AC-810)', () => {
  it('reflects stock denial, business lane and ordering from the current settings', () => {
    mockSettingsQuery.mockReturnValue({
      data: settings({
        chatbot_stock_denial_enabled: true,
        chatbot_business_lane_enabled: false,
        chatbot_ordering_enabled: false,
      }),
      isLoading: false,
      isError: false,
    });
    renderPage();

    expect(screen.getByLabelText(/stock denial/i).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByLabelText(/business lane/i).getAttribute('aria-checked')).toBe('false');
    expect(screen.getByLabelText(/ordering/i).getAttribute('aria-checked')).toBe('false');
  });
});

describe('ChatbotSettingsPage - Save payload (AC-810)', () => {
  it('calls the mutation with the exact current draft, snake_case, on every chatbot field', () => {
    const mutate = vi.fn();
    mockMutation.mockReturnValue({ isPending: false, mutate });
    mockSettingsQuery.mockReturnValue({
      data: settings({
        chatbot_stock_denial_enabled: true,
        chatbot_business_lane_enabled: false,
        chatbot_ordering_enabled: false,
        chatbot_unsupported_domains: ['goods_receive', 'spo_allocation'],
      }),
      isLoading: false,
      isError: false,
    });
    renderPage();

    fireEvent.click(saveButton());

    expect(mutate).toHaveBeenCalledTimes(1);
    const [payload] = mutate.mock.calls[0];
    expect(payload).toEqual({
      chatbot_stock_denial_enabled: true,
      chatbot_business_lane_enabled: false,
      chatbot_ordering_enabled: false,
      chatbot_unsupported_domains: ['goods_receive', 'spo_allocation'],
      chatbot_direct_ingress_mode: 'off',
      chatbot_direct_ingress_contacts: null,
    });
  });
});

describe('ChatbotSettingsPage - one consolidated Save (browser pass 1 finding, 16 Sep 2026)', () => {
  it('renders exactly one Save button once Memory and Tier order carry real data', () => {
    mockMemoryQuery.mockReturnValue({ data: DEFAULT_MEMORY, isLoading: false, isError: false });
    mockTierOrderQuery.mockReturnValue({ data: DEFAULT_TIER_ORDER, isLoading: false, isError: false });
    renderPage();

    // Today: the Switches/page-bottom Save, the Memory card's own Save AND the Tier
    // order card's own Save all match - `getByRole` throws "multiple elements found"
    // before this assertion even runs, which IS the red (not a soft `.length` check
    // a coder could quietly game by leaving two).
    expect(saveButton()).toBeInTheDocument();
  });

  it('clicking the one Save button after toggling a switch AND changing tier order issues both requests', () => {
    const switchesMutate = vi.fn();
    const tierOrderMutate = vi.fn();
    mockMutation.mockReturnValue({ isPending: false, mutate: switchesMutate });
    mockTierOrderMutation.mockReturnValue({ isPending: false, mutate: tierOrderMutate });
    mockSettingsQuery.mockReturnValue({
      data: settings({ chatbot_stock_denial_enabled: false }),
      isLoading: false,
      isError: false,
    });
    mockMemoryQuery.mockReturnValue({ data: DEFAULT_MEMORY, isLoading: false, isError: false });
    mockTierOrderQuery.mockReturnValue({
      data: ['dealer', 'office', 'end_user'],
      isLoading: false,
      isError: false,
    });
    renderPage();

    fireEvent.click(screen.getByLabelText(/stock denial/i));
    fireEvent.click(screen.getByRole('button', { name: /move office down/i }));

    fireEvent.click(saveButton());

    expect(switchesMutate).toHaveBeenCalledTimes(1);
    expect(switchesMutate.mock.calls[0][0]).toMatchObject({ chatbot_stock_denial_enabled: true });

    expect(tierOrderMutate).toHaveBeenCalledTimes(1);
    expect(tierOrderMutate.mock.calls[0][0]).toEqual(['dealer', 'end_user', 'office']);
  });
});

describe('ChatbotSettingsPage - the one Save also fires the ladder PUT (browser pass 2 finding, 16 Sep 2026)', () => {
  it('renders exactly one Save button once the ladder carries real data too', () => {
    mockMemoryQuery.mockReturnValue({ data: DEFAULT_MEMORY, isLoading: false, isError: false });
    mockTierOrderQuery.mockReturnValue({ data: DEFAULT_TIER_ORDER, isLoading: false, isError: false });
    mockDomainsQuery.mockReturnValue({
      data: DEFAULT_LADDER_DOMAINS,
      isLoading: false,
      isError: false,
    });
    renderPage();

    // Today: the ladder card renders its OWN Save button alongside the page's one -
    // `getByRole` throws "multiple elements found" before this assertion even runs,
    // which IS the red, same idiom as the Memory/Tier order consolidation above.
    expect(saveButton()).toBeInTheDocument();
  });

  it('moving a rung down through the real "Move X down" button, then clicking the one Save, fires the ladder PUT with the reordered list', () => {
    const updateMutate = vi.fn();
    mockMutation.mockReturnValue({ isPending: false, mutate: vi.fn() });
    mockMemoryQuery.mockReturnValue({ data: DEFAULT_MEMORY, isLoading: false, isError: false });
    mockTierOrderQuery.mockReturnValue({ data: DEFAULT_TIER_ORDER, isLoading: false, isError: false });
    mockDomainsQuery.mockReturnValue({
      data: DEFAULT_LADDER_DOMAINS,
      isLoading: false,
      isError: false,
    });
    mockUpdateDomain.mockReturnValue({ isPending: false, mutate: updateMutate });
    renderPage();

    // Drives the REAL rendered `OrderableList` DOM handler (`components/common/
    // OrderableList.tsx`'s own "Move {label} down" button), not a poked prop - the
    // ladder starts ['incoming', 'purchase_order'], so moving Incoming down yields
    // ['purchase_order', 'incoming'].
    fireEvent.click(screen.getByRole('button', { name: /move incoming down/i }));

    fireEvent.click(saveButton());

    expect(updateMutate).toHaveBeenCalledTimes(1);
    const [call] = updateMutate.mock.calls[0];
    expect(call.id).toBe('dom-inventory');
    expect(call.input.ladder).toEqual(['purchase_order', 'incoming']);
  });
});

describe('ChatbotSettingsPage - ordering confirm dialog (AC-810)', () => {
  it('switching ordering on opens an AlertDialog whose text mentions 410, never window.confirm', () => {
    const confirmSpy = vi.spyOn(window, 'confirm');
    mockSettingsQuery.mockReturnValue({
      data: settings({ chatbot_ordering_enabled: false }),
      isLoading: false,
      isError: false,
    });
    renderPage();

    fireEvent.click(screen.getByLabelText(/ordering/i));

    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.getByRole('alertdialog').textContent).toMatch(/410/);
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('cancelling the ordering confirm dialog leaves ordering off', () => {
    mockSettingsQuery.mockReturnValue({
      data: settings({ chatbot_ordering_enabled: false }),
      isLoading: false,
      isError: false,
    });
    renderPage();

    fireEvent.click(screen.getByLabelText(/ordering/i));
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));

    expect(screen.getByLabelText(/ordering/i).getAttribute('aria-checked')).toBe('false');
  });

  it('renders the Confirm and Cancel buttons in the document, both focusable, while the dialog is open', () => {
    mockSettingsQuery.mockReturnValue({
      data: settings({ chatbot_ordering_enabled: false }),
      isLoading: false,
      isError: false,
    });
    renderPage();

    fireEvent.click(screen.getByLabelText(/ordering/i));

    const confirmButton = screen.getByRole('button', { name: /turn it on/i });
    const cancelButton = screen.getByRole('button', { name: /cancel/i });

    expect(confirmButton).toBeInTheDocument();
    expect(cancelButton).toBeInTheDocument();
    expect(confirmButton).not.toBeDisabled();
    expect(cancelButton).not.toBeDisabled();

    // Focusable, not just present: a disabled or aria-hidden button would still pass
    // the two assertions above but could never actually receive focus.
    cancelButton.focus();
    expect(document.activeElement).toBe(cancelButton);

    confirmButton.focus();
    expect(document.activeElement).toBe(confirmButton);
  });
});

describe('ChatbotSettingsPage - loading and error states', () => {
  // retired: the "shows a loading/error state" pair asserted absence of the
  // now-gone per-branch-kind checkbox grid (`queryByRole('checkbox')`) as its
  // ONLY signal - the "Lanes the CRM answers" card and `chatbot_completed_lanes`
  // gating are superseded (PLAN-chatbot-turn-rearch S3 rulings, contract line 73).
  // The three switches use `role="switch"`, not `role="checkbox"`, so that
  // assertion would pass vacuously regardless of loading/error state once the
  // grid is gone - a coder-scope test (naming the switch-based loading/error
  // signal) replaces this, not a tester guess at markup that does not exist yet.

  it('shows an error state, not an infinite loader, when the settings query fails', () => {
    mockSettingsQuery.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    renderPage();

    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument();
  });
});

// CHAT-SPEED: the direct-ingress switch (off / listed contacts / all, owner ruling Q4 a).
describe('ChatbotSettingsPage - direct ingress', () => {
  beforeEach(() => {
    mockMemoryQuery.mockReturnValue({ data: DEFAULT_MEMORY, isLoading: false, isError: false });
    mockTierOrderQuery.mockReturnValue({ data: DEFAULT_TIER_ORDER, isLoading: false, isError: false });
  });

  it('shows the mode and hides the contact list while the mode is off', () => {
    mockMutation.mockReturnValue({ isPending: false, mutate: vi.fn() });
    mockSettingsQuery.mockReturnValue({ data: settings(), isLoading: false, isError: false });
    renderPage();

    expect(screen.getByText('Direct ingress')).toBeTruthy();
    expect(screen.getByRole('combobox', { name: /direct ingress/i }).textContent).toMatch(/off/i);
    expect(screen.queryByLabelText(/listed contacts/i)).toBeNull();
  });

  it('shows the listed contacts in listed mode and saves edits to them', () => {
    const mutate = vi.fn();
    mockMutation.mockReturnValue({ isPending: false, mutate });
    mockSettingsQuery.mockReturnValue({
      data: settings({
        chatbot_direct_ingress_mode: 'contacts',
        chatbot_direct_ingress_contacts: '100000077',
      }),
      isLoading: false,
      isError: false,
    });
    renderPage();

    const box = screen.getByLabelText(/listed contacts/i) as HTMLTextAreaElement;
    expect(box.value).toBe('100000077');
    fireEvent.change(box, { target: { value: '100000077\n100000078' } });
    fireEvent.click(saveButton());

    const [payload] = mutate.mock.calls[0];
    expect(payload.chatbot_direct_ingress_mode).toBe('contacts');
    expect(payload.chatbot_direct_ingress_contacts).toBe('100000077\n100000078');
  });
});
