/**
 * ============================================================================
 * Contact > Chatbot (chatbot memory lane A, round 3 mockup)
 * ============================================================================
 * Layering: UI -> hooks (`useContactChatbot.ts`) -> THIS service -> lib/api -> backend.
 *
 * Mockup: `documentation/plans/chatbot/chatbot-memory-27sep-mockup-contact.html`.
 * Contract: `documentation/plans/chatbot/chatbot-memory-lane-a-contract.md` sections
 * 4 (profile facts) and 5 (HTTP contract).
 *
 * ---------------------------------------------------------------------------
 * THE CONTRACT (every route below is real - S0 landed the first two, S2 the rest)
 * ---------------------------------------------------------------------------
 *
 * GET  /api/v1/user-management/contacts/{id}                       -> RespondContact
 * PUT  /api/v1/user-management/contacts/{id}/chatbot
 *   { chatbot_profile?: { tier, default_ledgers }, memory_level?: ChatbotMemoryLevel
 *     | null, chatbot_stock_allowed?, notify_salesman?, packing_list_allowed?,
 *     chatbot_eta_offset_applied? }
 *   Round 3 (AC-MEM055) renames the body key `chatbot_memory_level` -> `memory_level`
 *   (the GET/response side keeps `chatbot_memory_level`); `chatbot_recall_enabled` is
 *   dropped outright, body and column both, and a body still naming it 422s.
 *   `chatbot_profile` in the body never touches `facts`. Absent means "leave it
 *   alone", never "clear it".
 *   `chatbot_eta_offset_applied` (issue #1328): respond_contacts boolean NOT NULL
 *   DEFAULT true; whether the ETA this contact is told carries the product-or-category
 *   +x days, on the stock ask and the incoming routes alike (`app/services/eta_policy.py`).
 *
 * GET    /api/v1/user-management/contacts/{id}/chatbot/memory  (`...contacts.view`)
 *   -> ContactChatbotMemory (below), the exact shape contract section 5 documents.
 * PUT    /api/v1/user-management/contacts/{id}/chatbot/facts/{key}  (`...contacts.edit`)
 *   body `{ value: string | string[] }` -> sets a staff fact, returns the memory GET body.
 * DELETE /api/v1/user-management/contacts/{id}/chatbot/facts/{key}  (`...contacts.edit`)
 *   Hard delete through the deferred-action path (the FE fires it when the countdown
 *   lapses, same mechanism every other delete uses) -> 204.
 */

import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import { getProductsForLineSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import { getBrands } from '@/app/(protected)/master-data-management/brands/services/brandService';
import { getWarehouses } from '@/app/(protected)/inventory-management/warehouses/services/warehouseService';
import type { SearchableMultiSelectOption } from '@/components/common/SearchableMultiSelect';
import { getContact } from './contactService';

export type ChatbotMemoryLevel = 'off' | 'conversation' | 'episodes' | 'full';

export interface ContactChatbotProfile {
  /** null = follow the system default (contract section 2). */
  chatbot_memory_level: ChatbotMemoryLevel | null;
  tier: string | null;
  /** Not edited on this card - carried through unchanged so a save never clears it. */
  default_ledgers: string[];
  /** S6: stock checks allowed for this contact (`chatbot_stock_allowed`), default on. */
  stock_allowed: boolean;
  /** R7: the customer's sales agent gets one WhatsApp line per answered ask. Default off. */
  notify_salesman: boolean;
  /** R7: the shipment's packing list is attached on a B3 answer. Default off. */
  packing_list_allowed: boolean;
  /** #1328: the ETA this contact is told carries the +x days offset. Default on. */
  eta_offset_applied: boolean;
  /** ESCALATION-CONTROL: may the chatbot hand this contact to a person. Default on. */
  escalation_allowed: boolean;
}

export type ChatbotFactSource = 'crm' | 'tallied' | 'stated' | 'staff';

export interface ContactChatbotFact {
  key: string;
  label: string;
  /** The stored raw value (`"ms"`, `["SRTWB1455"]`) - fed straight back into an
   * in-place edit, no label reverse-mapping (contract section 5). */
  value: string | string[] | null;
  /** The grid's own printable label (`"Malay"`, `"SRTWB1455, M486-75-BL"`). */
  display: string | null;
  source: ChatbotFactSource;
  /** ISO date, or null for a fact read live from the CRM (contract section 5). */
  last_seen: string | null;
  editable: boolean;
  /** Set only on a CRM fact whose source record has its own page (e.g. `customer`). */
  link?: string | null;
}

export type ChatbotVocabularyKind = 'choice' | 'multi' | 'text';

export interface ContactChatbotVocabularyEntry {
  key: string;
  label: string;
  kind: ChatbotVocabularyKind;
  options: { value: string; label: string }[] | null;
  max_length: number | null;
}

export interface ContactChatbotEpisodeCurrent {
  turn_count: number;
  first_turn_id: string;
  started_at: string;
  summary: string;
  domains: string[];
  /** The Topic the chatbot's recall reply prints (`Stock`, `Incoming stock`). */
  topic?: string;
}

export interface ContactChatbotEpisodeRow {
  id: string;
  date: string;
  domains: string[];
  /** The Topic the chatbot's recall reply prints (`Stock`, `Incoming stock`). */
  topic?: string;
  summary: string;
  turn_count: number;
  close_reason: string;
  first_turn_id: string;
  /** A console (test) conversation, written by the Chatbot Console, never read by live turns. */
  console?: boolean;
}

export interface ContactChatbotOpenOrderRow {
  document: string;
  kind: string;
  status: string;
  summary: string;
  /** Null when the order carries no order date. */
  date: string | null;
  href: string;
}

export interface ContactChatbotMemory {
  level: {
    own: ChatbotMemoryLevel | null;
    effective: ChatbotMemoryLevel;
    system_default: ChatbotMemoryLevel;
  };
  facts: ContactChatbotFact[];
  vocabulary: ContactChatbotVocabularyEntry[];
  // `null` when the caller lacks `system.chat_history.view` on top of this page's
  // own view permission (security review 26 Sep 2026, S3) - conversation summaries
  // are gated separately from the rest of the memory card, never hidden as an
  // empty list (which would read as "no conversations" rather than "no access").
  episodes: {
    kept: number;
    /** Closed console conversations kept (their own newest 20). */
    console_kept?: number;
    limit: number;
    current: ContactChatbotEpisodeCurrent | null;
    /** The console's own open conversation, when the console has turns in no episode yet. */
    console_current?: ContactChatbotEpisodeCurrent | null;
    rows: ContactChatbotEpisodeRow[];
  } | null;
  open_orders: {
    customer_name: string | null;
    rows: ContactChatbotOpenOrderRow[];
  };
}

export type ContactChatbotSaveInput = ContactChatbotProfile;

function profileFromContact(contact: {
  chatbot_profile?: { tier?: string | null; default_ledgers?: string[] | null } | null;
  chatbot_memory_level?: ChatbotMemoryLevel | null;
  chatbot_stock_allowed?: boolean;
  notify_salesman?: boolean;
  packing_list_allowed?: boolean;
  chatbot_eta_offset_applied?: boolean;
  escalation_allowed?: boolean;
}): ContactChatbotProfile {
  const profile = contact.chatbot_profile ?? null;
  return {
    chatbot_memory_level: contact.chatbot_memory_level ?? null,
    tier: profile?.tier ?? null,
    default_ledgers: profile?.default_ledgers ?? [],
    stock_allowed: contact.chatbot_stock_allowed !== false,
    notify_salesman: Boolean(contact.notify_salesman),
    packing_list_allowed: Boolean(contact.packing_list_allowed),
    eta_offset_applied: contact.chatbot_eta_offset_applied !== false,
    escalation_allowed: contact.escalation_allowed !== false,
  };
}

export async function getContactChatbotProfile(contactId: string): Promise<ContactChatbotProfile> {
  const contact = await getContact(contactId);
  return profileFromContact(contact);
}

export async function saveContactChatbotProfile(
  contactId: string,
  input: ContactChatbotSaveInput,
): Promise<ContactChatbotProfile> {
  const response = await apiFetch(`/api/v1/user-management/contacts/${contactId}/chatbot`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      // The route replaces the whole `chatbot_profile` dict (never a merge), so every
      // field the contact already had is sent back, not just the one edited here.
      chatbot_profile: { tier: input.tier, default_ledgers: input.default_ledgers },
      // Round 3 (AC-MEM055) renames the BODY key off `chatbot_memory_level` -
      // `ContactChatbotProfile`/`ContactChatbotSaveInput` keep that name (it is what
      // the GET/response side still calls it), only the outgoing PUT key changes.
      memory_level: input.chatbot_memory_level,
      chatbot_stock_allowed: input.stock_allowed,
      notify_salesman: input.notify_salesman,
      packing_list_allowed: input.packing_list_allowed,
      chatbot_eta_offset_applied: input.eta_offset_applied,
      // `escalation_allowed` is NOT sent here: it has its own save
      // (`saveContactEscalation`), so another switch saved from a stale page can never
      // switch a contact's escalation back on (security review S3).
    }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save chatbot settings'));
  }
  return profileFromContact(await response.json());
}

/**
 * ESCALATION-CONTROL: "Chatbot hands over to support teams", saved on its own - the body carries this
 * one key, and the route leaves every field it does not name alone.
 */
export async function saveContactEscalation(contactId: string, allowed: boolean): Promise<ContactChatbotProfile> {
  const response = await apiFetch(`/api/v1/user-management/contacts/${contactId}/chatbot`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ escalation_allowed: allowed }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save chatbot settings'));
  }
  return profileFromContact(await response.json());
}

export async function getContactChatbotMemory(contactId: string): Promise<ContactChatbotMemory> {
  const response = await apiFetch(`/api/v1/user-management/contacts/${contactId}/chatbot/memory`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load chatbot memory'));
  }
  return response.json();
}

/** `PUT .../chatbot/facts/{key}` - sets a staff fact; every save makes the fact Staff,
 * even one that replaces a Learned or Said value (contract section 4). Returns the
 * memory GET body so the grid, vocabulary and everything else stay in sync with the
 * one write. */
export async function saveContactFact(
  contactId: string,
  key: string,
  value: string | string[],
): Promise<ContactChatbotMemory> {
  const response = await apiFetch(`/api/v1/user-management/contacts/${contactId}/chatbot/facts/${key}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save fact'));
  }
  return response.json();
}

/**
 * One option per stored value. A value that exists in several companies lists every company,
 * sorted and joined with ", ", before ` · ` and the label: `Mocha, Sorento · CABANA`. The
 * contact belongs to no one company, so every option says which.
 */
function optionsByValue(
  rows: { value: string; label: string; company?: string | null }[],
): SearchableMultiSelectOption[] {
  const merged = new Map<string, { label: string; companies: Set<string> }>();
  for (const row of rows) {
    const entry = merged.get(row.value) ?? { label: row.label, companies: new Set<string>() };
    if (row.company) entry.companies.add(row.company);
    merged.set(row.value, entry);
  }
  return Array.from(merged, ([value, { label, companies }]) => ({
    value,
    label: companies.size ? `${Array.from(companies).sort().join(', ')} · ${label}` : label,
  }));
}

/**
 * Server-searched product options for the "Usual products" fact (Add fact modal).
 *
 * Real endpoint, not a mock (`/api/v1/master-data/products/select`, LESSONS-LEARNT: a
 * static option list does not scale to the product catalog) - product codes double as
 * the human-readable value the fact stores, so no id/label split is needed.
 */
export async function searchUsualProductOptions(query: string): Promise<SearchableMultiSelectOption[]> {
  const products = await getProductsForLineSelect(query, { companyScope: 'grants' });
  return optionsByValue(
    products.map((product) => ({
      value: product.product_code,
      label: product.product_name
        ? `${product.product_code} - ${product.product_name}`
        : product.product_code,
      company: product.company_name,
    })),
  );
}

/** Server-searched brand names for "Usual brands" - stored as the brand NAME, not an id
 * (contract section 4: "list of brand names"). */
export async function searchUsualBrandOptions(query: string): Promise<SearchableMultiSelectOption[]> {
  const result = await getBrands({
    pageIndex: 0,
    pageSize: 20,
    sorting: [],
    searchQuery: query,
    companyScope: 'grants',
  });
  return optionsByValue(
    result.data.map((brand) => ({
      value: brand.brand_name,
      label: brand.brand_name,
      company: brand.company_name,
    })),
  );
}

/** Server-searched warehouse names for "Usual sites" - stored as the warehouse NAME
 * (contract section 4: "list of warehouse names"). */
export async function searchUsualSiteOptions(query: string): Promise<SearchableMultiSelectOption[]> {
  const result = await getWarehouses({
    pageIndex: 0,
    pageSize: 20,
    sorting: [],
    searchQuery: query,
    companyScope: 'grants',
  });
  return optionsByValue(
    result.data
      .filter((warehouse) => warehouse.warehouse_name)
      .map((warehouse) => ({
        value: warehouse.warehouse_name as string,
        label: warehouse.warehouse_name as string,
        company: warehouse.company_name,
      })),
  );
}
