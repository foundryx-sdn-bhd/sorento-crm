/**
 * QUOTED-REPLY AC-QR-19: the console's "Reply to" control. Red until the picker, the
 * `reply_to` request field and the quoted user bubble exist.
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

function turn(id: string, reply: string, extra: string[] = []) {
  return {
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

describe('ChatbotConsole - Reply to control (AC-QR-19)', () => {
  it('renders a Reply to picker once a bot reply exists, listing bot bubbles only, newest first', async () => {
    postConsoleTurn
      .mockResolvedValueOnce(turn('t1', 'Which product do you mean?'))
      .mockResolvedValueOnce(turn('t2', 'Stock is 12 on hand.', ['Anything else?']));
    renderConsole();
    await send('first');
    await screen.findByText('Which product do you mean?');
    await send('second');
    await screen.findByText('Anything else?');

    const options = await openReplyOptions();
    const labels = options.map((o) => o.textContent ?? '');
    // Greeting lines are never quotable.
    for (const greeting of CONSOLE_GREETING_MESSAGES) {
      expect(labels.some((l) => l.includes(greeting))).toBe(false);
    }
    // User bubbles are never quotable.
    expect(labels.some((l) => l.includes('first') && !l.includes('Which'))).toBe(false);
    // Newest first: turn 2's last bubble, its first bubble, then turn 1's reply.
    expect(labels).toHaveLength(3);
    expect(labels[0]).toContain('Anything else?');
    expect(labels[1]).toContain('Stock is 12 on hand.');
    expect(labels[2]).toContain('Which product do you mean?');
  });

  it('sending after choosing a bubble posts reply_to {id, text} with the typed text', async () => {
    postConsoleTurn
      .mockResolvedValueOnce(turn('t1', 'Which product do you mean?'))
      .mockResolvedValueOnce(turn('t2', 'Noted.'));
    renderConsole();
    await send('price?');
    await screen.findByText('Which product do you mean?');

    const options = await openReplyOptions();
    fireEvent.click(options[0]);
    await send('1 unit');

    await waitFor(() => expect(postConsoleTurn).toHaveBeenCalledTimes(2));
    const payload = postConsoleTurn.mock.calls[1][0];
    expect(payload.text).toBe('1 unit');
    expect(payload.reply_to).toEqual({ id: 'bot-t1-0', text: 'Which product do you mean?' });
  });

  it('the user bubble shows the quoted text and the typed text', async () => {
    postConsoleTurn
      .mockResolvedValueOnce(turn('t1', 'Which product do you mean?'))
      .mockResolvedValueOnce(turn('t2', 'Noted.'));
    renderConsole();
    await send('price?');
    await screen.findByText('Which product do you mean?');

    const options = await openReplyOptions();
    fireEvent.click(options[0]);
    await send('1 unit');

    const typed = await screen.findByText('1 unit');
    const userBubble = typed.closest('[data-role="user"], [data-testid*="user"], div') as HTMLElement;
    // The quote is rendered inside the same bubble as the typed text.
    let node: HTMLElement | null = typed;
    let found = false;
    while (node && node !== document.body) {
      if (within(node).queryByText('Which product do you mean?') && within(node).queryByText('1 unit')) {
        // Must not be the whole thread: the bot bubble's own copy would also match there.
        if (within(node).queryAllByText('Which product do you mean?').length === 1) found = true;
        break;
      }
      node = node.parentElement;
    }
    expect(userBubble).toBeTruthy();
    expect(found).toBe(true);
  });

  it('clears the picker after the send, so the next send carries no reply_to', async () => {
    postConsoleTurn
      .mockResolvedValueOnce(turn('t1', 'Which product do you mean?'))
      .mockResolvedValueOnce(turn('t2', 'Noted.'))
      .mockResolvedValueOnce(turn('t3', 'Done.'));
    renderConsole();
    await send('price?');
    await screen.findByText('Which product do you mean?');

    const options = await openReplyOptions();
    fireEvent.click(options[0]);
    await send('1 unit');
    await screen.findByText('Noted.');

    // Picker is back to its empty state: it no longer shows the quoted bubble's text.
    const trigger = replyTrigger();
    expect(trigger).not.toBeNull();
    expect(trigger!.textContent).not.toContain('Which product do you mean?');

    await send('and 2 units');
    await waitFor(() => expect(postConsoleTurn).toHaveBeenCalledTimes(3));
    expect(postConsoleTurn.mock.calls[2][0]).not.toHaveProperty('reply_to');
  });

  it('with nothing chosen the request has no reply_to key', async () => {
    postConsoleTurn.mockResolvedValueOnce(turn('t1', 'Which product do you mean?'));
    renderConsole();
    await send('price?');
    await waitFor(() => expect(postConsoleTurn).toHaveBeenCalledTimes(1));
    expect(postConsoleTurn.mock.calls[0][0]).not.toHaveProperty('reply_to');
  });
});
