import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

/**
 * Chatbot settings (AC-809, AC-810, issue #679).
 *
 * ---------------------------------------------------------------------------
 * API CONTRACT
 * ---------------------------------------------------------------------------
 *
 * READ   GET /api/v1/user-management/settings
 *   The existing settings blob. Its hand-written response dict carries the four
 *   chatbot switch/domain keys below (plus `chatbot_tier_order` / `chatbot_memory`,
 *   read separately below); a column added to the model but not to that dict never
 *   reaches this page.
 *
 * WRITE  POST /api/v1/user-management/settings/general
 *   `user_management.settings.edit`. Partial: only the keys a caller sends are
 *   applied by the existing setattr path, so the Switches card's save and the
 *   Memory/Tier-order cards' saves are independent writes to the same row.
 *
 * `chatbot_tier_order` (`string[]`, dealer/office/end_user) and `chatbot_memory`
 * (one JSONB: `recall_default`, `episode_retention_days`, `profile_fields`,
 * `focus_reset_events`) are the chatbot turn re-architecture's own two additions
 * (S5, AC-1560/AC-1561) - same route, same partial-setattr path, but NOT part of
 * `ChatbotSettings`: they are their own cards with their own read/save pair,
 * kept out of the Switches card's payload (test contract: `ChatbotSettings` is
 * exactly the four switch/domain keys).
 */

export interface ChatbotSettings {
  chatbot_stock_denial_enabled: boolean;
  /** The business lane may RUN. Independent of it being allowed to answer. */
  chatbot_business_lane_enabled: boolean;
  /** S7 mode: the CRM orders turns per contact and owns the tail. */
  chatbot_ordering_enabled: boolean;
  chatbot_unsupported_domains: string[];
  /** CHAT-SPEED: does the respond.io webhook start the turn itself. */
  chatbot_direct_ingress_mode: ChatbotDirectIngressMode;
  /** respond.io contact ids, one per line, for the `contacts` mode. */
  chatbot_direct_ingress_contacts: string | null;
}

export type ChatbotDirectIngressMode = 'off' | 'contacts' | 'all';

const DIRECT_INGRESS_MODES: readonly ChatbotDirectIngressMode[] = ['off', 'contacts', 'all'];

/**
 * What a settings row that predates these columns reads as.
 *
 * `chatbot_unsupported_domains` is deliberately EMPTY here rather than a copy of the
 * backend's shipped default (AC-931). It used to read `['goods_receive', 'spo_allocation']`
 * and went stale the day A6 unblocked `spo_allocation`, which is the fourth copy of one
 * default drifting - the backend now derives it once, from `DOMAIN_SPEC`, and a mirror
 * here could only ever be a fifth.
 *
 * Nothing is lost by dropping it. The column is NOT NULL with its own server default, and
 * `GET /settings` emits the whole `settings` object as `null` only when there is no
 * settings row at all - the one case this fallback can fire. In that state
 * `POST /settings/general` answers 404 ("Settings not found"), so the screen cannot save
 * an empty list over anything either.
 */
const FALLBACKS: ChatbotSettings = {
  chatbot_stock_denial_enabled: false,
  chatbot_business_lane_enabled: false,
  chatbot_ordering_enabled: false,
  chatbot_unsupported_domains: [],
  chatbot_direct_ingress_mode: 'off',
  chatbot_direct_ingress_contacts: null,
};

function pickChatbotSettings(row: Record<string, unknown> | null | undefined): ChatbotSettings {
  return {
    chatbot_stock_denial_enabled: Boolean(row?.chatbot_stock_denial_enabled),
    chatbot_business_lane_enabled: Boolean(row?.chatbot_business_lane_enabled),
    chatbot_ordering_enabled: Boolean(row?.chatbot_ordering_enabled),
    chatbot_unsupported_domains: Array.isArray(row?.chatbot_unsupported_domains)
      ? (row.chatbot_unsupported_domains as string[])
      : FALLBACKS.chatbot_unsupported_domains,
    chatbot_direct_ingress_mode: DIRECT_INGRESS_MODES.includes(
      row?.chatbot_direct_ingress_mode as ChatbotDirectIngressMode,
    )
      ? (row?.chatbot_direct_ingress_mode as ChatbotDirectIngressMode)
      : FALLBACKS.chatbot_direct_ingress_mode,
    chatbot_direct_ingress_contacts:
      typeof row?.chatbot_direct_ingress_contacts === 'string'
        ? row.chatbot_direct_ingress_contacts
        : null,
  };
}

export async function getChatbotSettings(): Promise<ChatbotSettings> {
  const response = await apiFetch('/api/user-management/settings');
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load settings'));
  }
  const data = await response.json();
  return pickChatbotSettings(data?.settings);
}

export async function saveChatbotSettings(input: ChatbotSettings): Promise<ChatbotSettings> {
  const response = await apiFetch('/api/user-management/settings/general', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save settings'));
  }
  const data = await response.json();
  return pickChatbotSettings(data?.data);
}

/**
 * Settings > Chatbot > Memory card (chatbot memory lane A, round 3 mockup
 * `chatbot-memory-27sep-mockup-settings.html`; contract sections 2 and 5).
 *
 * `system_settings.chatbot_memory` is now exactly `{enabled, default_level,
 * own_level_count}` - the four dead settings this card used to carry (recall_default,
 * episode_retention_days, profile_fields, focus_reset_events) are gone from the type,
 * this service and the card itself; the S0 migration drops them from the column.
 */
export interface ChatbotMemorySettings {
  enabled: boolean;
  /** Never `off`, never null - the select has no clear (contract section 2). */
  default_level: 'conversation' | 'episodes' | 'full';
  /** Read-only: contacts with their own level (contract section 2's per-contact override). */
  own_level_count: number;
}

const MEMORY_FALLBACK: ChatbotMemorySettings = {
  enabled: false,
  default_level: 'full',
  own_level_count: 0,
};

function pickChatbotMemory(row: Record<string, unknown> | null | undefined): ChatbotMemorySettings {
  const memory = row?.chatbot_memory as Partial<ChatbotMemorySettings> | null | undefined;
  if (!memory) return MEMORY_FALLBACK;
  return {
    enabled: Boolean(memory.enabled),
    default_level:
      memory.default_level === 'conversation' ||
      memory.default_level === 'episodes' ||
      memory.default_level === 'full'
        ? memory.default_level
        : MEMORY_FALLBACK.default_level,
    own_level_count: typeof memory.own_level_count === 'number' ? memory.own_level_count : 0,
  };
}

// S0 migration landed (contract section 5): `chatbot_memory` is the real two-key shape
// plus a live `own_level_count`, so this card reads/saves through the same GET/PUT the
// Switches card already uses.
export async function getChatbotMemorySettings(): Promise<ChatbotMemorySettings> {
  const response = await apiFetch('/api/user-management/settings');
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load settings'));
  }
  const data = await response.json();
  return pickChatbotMemory(data?.settings);
}

export async function saveChatbotMemorySettings(
  input: ChatbotMemorySettings,
): Promise<ChatbotMemorySettings> {
  // own_level_count is read-only (set per-contact, on the contact's own card) - never
  // sent; the backend 422s an unrecognised chatbot_memory key regardless.
  const response = await apiFetch('/api/user-management/settings/general', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chatbot_memory: { enabled: input.enabled, default_level: input.default_level },
    }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save memory settings'));
  }
  // The PUT's own response echoes the stored `{enabled, default_level}` only -
  // `own_level_count` is a live count the GET dict builder merges in, so a fresh GET
  // is what actually reflects it (never 0, freshly reset, right after a save).
  return getChatbotMemorySettings();
}

/** Office / Dealer / End user, orderable (AC-1502's tier axis; replaces the three
 * `TIER_ORDER` code copies AC-1594 deletes). */
const TIER_ORDER_FALLBACK: string[] = ['dealer', 'office', 'end_user'];

export async function getChatbotTierOrder(): Promise<string[]> {
  const response = await apiFetch('/api/user-management/settings');
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load settings'));
  }
  const data = await response.json();
  const row = data?.settings as Record<string, unknown> | null | undefined;
  return Array.isArray(row?.chatbot_tier_order)
    ? (row!.chatbot_tier_order as string[])
    : TIER_ORDER_FALLBACK;
}

export async function saveChatbotTierOrder(order: string[]): Promise<string[]> {
  const response = await apiFetch('/api/user-management/settings/general', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chatbot_tier_order: order }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save tier order'));
  }
  const data = await response.json();
  const row = data?.data as Record<string, unknown> | null | undefined;
  return Array.isArray(row?.chatbot_tier_order)
    ? (row!.chatbot_tier_order as string[])
    : TIER_ORDER_FALLBACK;
}
