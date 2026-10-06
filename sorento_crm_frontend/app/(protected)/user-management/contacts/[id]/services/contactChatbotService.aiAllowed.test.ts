/**
 * AI-ALLOWED-GATE (AC-AI-22): `chatbot_allowed` rides the contact GET and the
 * PUT .../chatbot body. It is read onto the card's profile (absent reads as blocked, the production default is off), written ONLY by
 * `saveContactChatbotAllowed`, and the card save never sends it.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn();
const getContact = vi.fn();

vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));
vi.mock('./contactService', () => ({
  getContact: (...a: unknown[]) => getContact(...a),
}));

import {
  getContactChatbotProfile,
  saveContactChatbotAllowed,
  saveContactChatbotProfile,
} from './contactChatbotService';

beforeEach(() => {
  apiFetch.mockReset();
  getContact.mockReset();
});

describe('contactChatbotService - AI replies allowed (AI-ALLOWED-GATE)', () => {
  it('reads false off the contact', async () => {
    getContact.mockResolvedValueOnce({ chatbot_allowed: false });
    expect((await getContactChatbotProfile('c1')).chatbot_allowed).toBe(false);
  });

  it('reads true off the contact', async () => {
    getContact.mockResolvedValueOnce({ chatbot_allowed: true });
    expect((await getContactChatbotProfile('c1')).chatbot_allowed).toBe(true);
  });

  it('a contact without the field defaults to blocked (the read is chatbot_allowed === true)', async () => {
    getContact.mockResolvedValueOnce({});
    expect((await getContactChatbotProfile('c1')).chatbot_allowed).toBe(false);
  });

  it('the AI save sends only chatbot_allowed and returns the profile with the new value', async () => {
    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ chatbot_allowed: false }), { status: 200 }),
    );

    const saved = await saveContactChatbotAllowed('c1', false);

    expect(apiFetch.mock.calls[0][0]).toBe('/api/v1/user-management/contacts/c1/chatbot');
    expect(apiFetch.mock.calls[0][1].method).toBe('PUT');
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ chatbot_allowed: false });
    expect(saved.chatbot_allowed).toBe(false);
  });

  it('the card save never sends chatbot_allowed (a stale page cannot switch it back on)', async () => {
    getContact.mockResolvedValueOnce({ chatbot_allowed: false });
    const profile = await getContactChatbotProfile('c1');
    apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 }));

    await saveContactChatbotProfile('c1', { ...profile, notify_salesman: true });

    const body = JSON.parse(apiFetch.mock.calls[0][1].body);
    expect(body).not.toHaveProperty('chatbot_allowed');
  });
});
