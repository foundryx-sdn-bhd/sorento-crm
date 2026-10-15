import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

/**
 * Per-contact chatbot field reveals (chatbot growth r1, Slice C - PLAN-chatbot-growth-r1).
 *
 * ---------------------------------------------------------------------------
 * API CONTRACT (backend `app/api/v1/system/chatbot_field_reveals.py`)
 * ---------------------------------------------------------------------------
 *
 * A "restricted" field is one a presenter marked `restricted=<key>` in its
 * `field_vocabulary` - today that is sellable stock and a PO's supplier. Off by
 * default for every contact; a grant here is what lets the chatbot's answer
 * include it. The keys are DATA, never hardcoded here: a new restricted field
 * on a presenter reaches this list after the next MCP catalog sync with no FE
 * change.
 *
 * GET /api/v1/system/chatbot/field-reveal-keys
 *   Permission: `user_management.contacts.view`.
 *   200 -> { "items": [{ "key": "inventory.sellable", "label": "Sellable stock", "prompt_blocks": [] }, ...] }
 *
 * GET /api/v1/system/chatbot/contacts/{respond_contact_id}/field-reveals
 *   Permission: `user_management.contacts.view`.
 *   200 -> { "granted": ["inventory.sellable"] }
 *   404 for an unknown contact.
 *
 * PUT /api/v1/system/chatbot/contacts/{respond_contact_id}/field-reveals
 *   Permission: `user_management.contacts.edit`.
 *   body: { "granted": ["inventory.sellable"] }   // FULL LIST REPLACE
 *   200 -> { "granted": [...] }   // the keys granted after the write
 */

export interface FieldRevealKey {
  key: string;
  label: string;
  /** Chatbot prompt blocks this key also removes when it is off (`PROMPT_GATES`). */
  prompt_blocks?: string[];
}

export async function getFieldRevealKeys(): Promise<FieldRevealKey[]> {
  const response = await apiFetch('/api/v1/system/chatbot/field-reveal-keys');
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load field reveal keys'));
  }
  const body = (await response.json()) as { items: FieldRevealKey[] };
  return body.items;
}

export async function getContactFieldReveals(contactId: string): Promise<string[]> {
  const response = await apiFetch(`/api/v1/system/chatbot/contacts/${contactId}/field-reveals`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load field reveals'));
  }
  const body = (await response.json()) as { granted: string[] };
  return body.granted;
}

export async function setContactFieldReveals(
  contactId: string,
  granted: string[],
): Promise<string[]> {
  const response = await apiFetch(`/api/v1/system/chatbot/contacts/${contactId}/field-reveals`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ granted }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save field reveals'));
  }
  const body = (await response.json()) as { granted: string[] };
  return body.granted;
}
