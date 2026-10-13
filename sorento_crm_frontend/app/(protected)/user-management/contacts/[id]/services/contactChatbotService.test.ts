/**
 * Issue #1328: `chatbot_eta_offset_applied` rides the contact GET and the
 * PUT .../chatbot body as `eta_offset_applied` on the card's profile, default on.
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
  saveContactChatbotProfile,
  saveContactEscalation,
} from './contactChatbotService';

beforeEach(() => {
  apiFetch.mockReset();
  getContact.mockReset();
});

describe('contactChatbotService - ETA offset switch (#1328)', () => {
  it('reads the switch off the contact, and a contact without the field defaults on', async () => {
    getContact.mockResolvedValueOnce({ chatbot_eta_offset_applied: false });
    expect((await getContactChatbotProfile('c1')).eta_offset_applied).toBe(
      false,
    );

    getContact.mockResolvedValueOnce({});
    expect((await getContactChatbotProfile('c1')).eta_offset_applied).toBe(
      true,
    );
  });

  it('sends the switch as chatbot_eta_offset_applied on save', async () => {
    getContact.mockResolvedValueOnce({ chatbot_eta_offset_applied: true });
    const profile = await getContactChatbotProfile('c1');
    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ chatbot_eta_offset_applied: false }), {
        status: 200,
      }),
    );

    const saved = await saveContactChatbotProfile('c1', {
      ...profile,
      eta_offset_applied: false,
    });

    const body = JSON.parse(apiFetch.mock.calls[0][1].body);
    expect(body.chatbot_eta_offset_applied).toBe(false);
    expect(saved.eta_offset_applied).toBe(false);
  });
});

describe('contactChatbotService - escalation switch (ESCALATION-CONTROL)', () => {
  it('reads the switch off the contact, and a contact without the field defaults on', async () => {
    getContact.mockResolvedValueOnce({ escalation_allowed: false });
    expect((await getContactChatbotProfile('c1')).escalation_allowed).toBe(false);

    getContact.mockResolvedValueOnce({});
    expect((await getContactChatbotProfile('c1')).escalation_allowed).toBe(true);
  });

  it('the card save never sends escalation_allowed (a stale page cannot switch it back on)', async () => {
    getContact.mockResolvedValueOnce({ escalation_allowed: true });
    const profile = await getContactChatbotProfile('c1');
    apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 }));

    await saveContactChatbotProfile('c1', { ...profile, notify_salesman: true });

    const body = JSON.parse(apiFetch.mock.calls[0][1].body);
    expect(body).not.toHaveProperty('escalation_allowed');
  });

  it('the escalation save sends only escalation_allowed', async () => {
    apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ escalation_allowed: false }), { status: 200 }));

    const saved = await saveContactEscalation('c1', false);

    expect(apiFetch.mock.calls[0][0]).toBe('/api/v1/user-management/contacts/c1/chatbot');
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ escalation_allowed: false });
    expect(saved.escalation_allowed).toBe(false);
  });
});

describe('contactChatbotService - skip date-range questions switch (DO-PERIOD-SKIP)', () => {
  it('reads the switch off the contact, and a contact without the field defaults off', async () => {
    getContact.mockResolvedValueOnce({ chatbot_skip_period_questions: true });
    expect((await getContactChatbotProfile('c1')).skip_period_questions).toBe(true);

    getContact.mockResolvedValueOnce({});
    expect((await getContactChatbotProfile('c1')).skip_period_questions).toBe(false);
  });

  it('sends the switch as chatbot_skip_period_questions on save, true and false', async () => {
    getContact.mockResolvedValueOnce({});
    const profile = await getContactChatbotProfile('c1');
    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ chatbot_skip_period_questions: true }), { status: 200 }),
    );

    const saved = await saveContactChatbotProfile('c1', { ...profile, skip_period_questions: true });

    expect(JSON.parse(apiFetch.mock.calls[0][1].body).chatbot_skip_period_questions).toBe(true);
    expect(saved.skip_period_questions).toBe(true);

    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ chatbot_skip_period_questions: false }), { status: 200 }),
    );
    await saveContactChatbotProfile('c1', { ...profile, skip_period_questions: false });
    expect(JSON.parse(apiFetch.mock.calls[1][1].body).chatbot_skip_period_questions).toBe(false);
  });
});

describe('contactChatbotService - send files as links switch (PHOTO-SEND-CAP)', () => {
  it('reads the switch off the contact, and a contact without the field defaults off', async () => {
    getContact.mockResolvedValueOnce({ chatbot_files_as_links: true });
    expect((await getContactChatbotProfile('c1')).files_as_links).toBe(true);

    getContact.mockResolvedValueOnce({});
    expect((await getContactChatbotProfile('c1')).files_as_links).toBe(false);
  });

  it('sends the switch as chatbot_files_as_links on save, true and false', async () => {
    getContact.mockResolvedValueOnce({});
    const profile = await getContactChatbotProfile('c1');
    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ chatbot_files_as_links: true }), { status: 200 }),
    );

    const saved = await saveContactChatbotProfile('c1', { ...profile, files_as_links: true });

    expect(JSON.parse(apiFetch.mock.calls[0][1].body).chatbot_files_as_links).toBe(true);
    expect(saved.files_as_links).toBe(true);

    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ chatbot_files_as_links: false }), { status: 200 }),
    );
    await saveContactChatbotProfile('c1', { ...profile, files_as_links: false });
    expect(JSON.parse(apiFetch.mock.calls[1][1].body).chatbot_files_as_links).toBe(false);
  });
});
