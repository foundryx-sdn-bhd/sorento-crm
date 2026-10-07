/**
 * QUOTED-REPLY AC-QR-23 (UI): the "Reply to" picker also lists the customer's own bubbles. A
 * customer bubble posts `source: "contact"` with the response's `message_id`; a bot bubble posts
 * `source: "n8n"` (asserted explicitly: the hook sends it, the server default is the fallback).
 * Red until the response type carries `message_id` and the picker lists customer bubbles.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ChatbotConsole from './ChatbotConsole';
import { CONSOLE_GREETING_MESSAGES } from '../types/chatbotConsole.types';

const postConsoleTurn = vi.fn();
const getConsolePromptVersions = vi.fn();

vi.mock('../services/chatbotConsoleService', () => ({
  postConsoleTurn: (...args: unknown[]) => postConsoleTurn(...args),
  getConsolePromptVersions: (...args: unknown[]) => getConsolePromptVersions(...args),
  getConsoleMediaStatus: vi.fn(),
}));

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock('../../respond-contacts/services/respondContactOutboundService', () => ({
  getRespondContactsOutbound: vi.fn().mockResolvedValue({
    data: [],
    pagination: { total: 0, page: 1, limit: 20 },
    empty: true,
    counts: { enabled: 0, disabled: 0, total: 0 },
  }),
}));

vi.mock('../../chat-history/services/chatbotTurnService', () => ({
  getChatbotTurns: vi.fn().mockResolvedValue({ items: [], next_cursor: null, retry_available: false }),
}));

function turn(id: string, reply: string, extra: string[] = [], messageId?: string) {
  return {
    ...(messageId ? { message_id: messageId } : {}),
    turn_id: id,
    branch_kind: 'business_query',
    reply_text: reply,
    quick_replies: [],
    send_messages: extra,
    session_vars: {},
    trace_summary: { tool: null, args_short: null, crossdomain_rungs: [], reveals_dropped: [] },
  };
}

function renderConsole() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatbotConsole />
    </QueryClientProvider>,
  );
}

function textarea(): HTMLTextAreaElement {
  return screen.getByPlaceholderText('Ask, or attach voice/image') as HTMLTextAreaElement;
}

async function send(text: string) {
  fireEvent.change(textarea(), { target: { value: text } });
  fireEvent.keyDown(textarea(), { key: 'Enter' });
}

/** The "Reply to" SearchableSelect trigger: found by its accessible name or visible text. */
function replyTrigger(): HTMLElement | null {
  const triggers = Array.from(
    document.querySelectorAll<HTMLElement>('[data-slot="searchable-select-trigger"]'),
  );
  return (
    triggers.find((t) => /reply to/i.test(`${t.getAttribute('aria-label') ?? ''} ${t.textContent ?? ''}`)) ?? null
  );
}

async function openReplyOptions(): Promise<HTMLElement[]> {
  const trigger = replyTrigger();
  expect(trigger, 'a "Reply to" SearchableSelect trigger').not.toBeNull();
  fireEvent.click(trigger!);
  return screen.findAllByRole('option');
}

beforeEach(() => {
  postConsoleTurn.mockReset();
  getConsolePromptVersions.mockReset();
  getConsolePromptVersions.mockResolvedValue([]);
  window.localStorage.clear();
  window.localStorage.setItem(
    'chatbot-console:last-contact',
    JSON.stringify({ id: 'ZZT-contact-1', label: 'Test Contact' }),
  );
});

afterEach(() => cleanup());

describe('ChatbotConsole - own-message quotes (AC-QR-23)', () => {
  it('lists the customer bubble beside bot bubbles, newest first, greetings excluded', async () => {
    postConsoleTurn.mockResolvedValueOnce(turn('t1', 'Which product do you mean?', [], 'm-1'));
    renderConsole();
    await send('SRTBF11841 have stock?');
    await screen.findByText('Which product do you mean?');

    const labels = (await openReplyOptions()).map((o) => o.textContent ?? '');
    for (const greeting of CONSOLE_GREETING_MESSAGES) {
      expect(labels.some((l) => l.includes(greeting))).toBe(false);
    }
    expect(labels).toHaveLength(2);
    expect(labels[0]).toContain('Which product do you mean?');
    expect(labels[1]).toContain('SRTBF11841 have stock?');
  });

  it('choosing the customer bubble posts reply_to {id: message_id, text, source: contact}', async () => {
    postConsoleTurn
      .mockResolvedValueOnce(turn('t1', 'Which product do you mean?', [], 'm-1'))
      .mockResolvedValueOnce(turn('t2', 'Noted.', [], 'm-2'));
    renderConsole();
    await send('SRTBF11841 have stock?');
    await screen.findByText('Which product do you mean?');

    const options = await openReplyOptions();
    const own = options.find((o) => (o.textContent ?? '').includes('SRTBF11841 have stock?'));
    expect(own, 'the customer bubble option').toBeTruthy();
    fireEvent.click(own!);
    await send('and eta?');

    await waitFor(() => expect(postConsoleTurn).toHaveBeenCalledTimes(2));
    const payload = postConsoleTurn.mock.calls[1][0];
    expect(payload.text).toBe('and eta?');
    expect(payload.reply_to).toEqual({ id: 'm-1', text: 'SRTBF11841 have stock?', source: 'contact' });
  });

  it('choosing a bot bubble posts source n8n', async () => {
    postConsoleTurn
      .mockResolvedValueOnce(turn('t1', 'Which product do you mean?', [], 'm-1'))
      .mockResolvedValueOnce(turn('t2', 'Noted.', [], 'm-2'));
    renderConsole();
    await send('price?');
    await screen.findByText('Which product do you mean?');

    const options = await openReplyOptions();
    const bot = options.find((o) => (o.textContent ?? '').includes('Which product do you mean?'));
    fireEvent.click(bot!);
    await send('1 unit');

    await waitFor(() => expect(postConsoleTurn).toHaveBeenCalledTimes(2));
    expect(postConsoleTurn.mock.calls[1][0].reply_to).toEqual({
      id: 'bot-t1-0',
      text: 'Which product do you mean?',
      source: 'n8n',
    });
  });

  it('the customer bubble shows the quoted own text', async () => {
    postConsoleTurn
      .mockResolvedValueOnce(turn('t1', 'Which product do you mean?', [], 'm-1'))
      .mockResolvedValueOnce(turn('t2', 'Noted.', [], 'm-2'));
    renderConsole();
    await send('SRTBF11841 have stock?');
    await screen.findByText('Which product do you mean?');

    const options = await openReplyOptions();
    fireEvent.click(options.find((o) => (o.textContent ?? '').includes('SRTBF11841 have stock?'))!);
    await send('and eta?');

    const typed = await screen.findByText('and eta?');
    let node: HTMLElement | null = typed;
    let found = false;
    while (node && node !== document.body) {
      if (within(node).queryByText('and eta?') && within(node).queryAllByText('SRTBF11841 have stock?').length >= 1) {
        // The original customer bubble also holds this text, so the enclosing node must be
        // the new bubble only: it holds exactly one copy of the typed text and one of the quote.
        if (within(node).queryAllByText('SRTBF11841 have stock?').length === 1 && node.textContent!.length < 200) {
          found = true;
        }
        break;
      }
      node = node.parentElement;
    }
    expect(found).toBe(true);
  });
});
