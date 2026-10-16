/**
 * CHAT-HISTORY-FORMAT (owner, 16 Oct 2026): "the chat history is not rendering
 * the bold and link, like i cannot directly click on the link". The transcript
 * behind the Chat History drawer and the contact page's Chat history section
 * renders a bot reply the way the handset does - WhatsApp markup applied, URLs
 * clickable - and the in-conversation search still marks its hits.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';

import { ChatTranscript } from './ChatTranscript';
import type { ChatMessageRow } from '../types/chatHistory.types';

vi.mock('./TurnPanel', () => ({ TurnPanel: () => null }));
vi.mock('./StateTracePanel', () => ({ StateTracePanel: () => null }));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(() => cleanup());

const URL = 'https://fe-sorento.foundryx.my/f/R6E6GC49A42JCC65';
const REPLY = `*Product Code:* SRTSC07\n*ETA:* 2026-10-07\n*Link:* ${URL}\n🚩 *(PENDING ALLOCATION)*`;

function message(over: Partial<ChatMessageRow>): ChatMessageRow {
  return {
    id: 1,
    contact_id: 'ZZT-contact',
    type: 'incoming',
    message: 'srtsc07 incoming ?',
    sent_at: '2026-10-16T03:00:00.000Z',
    latency_seconds: null,
    delivery_status: null,
    message_id: null,
    turn_id: null,
    state_trace: null,
    ...over,
  } as ChatMessageRow;
}

const THREAD = [message({}), message({ id: 2, type: 'outgoing', message: REPLY, latency_seconds: 9.4 })];

describe('ChatTranscript WhatsApp formatting', () => {
  it('renders the bot reply bold with a clickable link and no literal asterisks', () => {
    const { container } = render(<ChatTranscript messages={THREAD} />);

    expect(screen.getByText('Product Code:').tagName).toBe('STRONG');
    expect(screen.getByText('(PENDING ALLOCATION)').tagName).toBe('STRONG');
    const link = screen.getByRole('link', { name: URL });
    expect(link).toHaveAttribute('href', URL);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(container.textContent).not.toContain('*ETA:*');
  });

  it('search still finds and marks a term inside a bold label', () => {
    render(<ChatTranscript messages={THREAD} />);
    fireEvent.change(screen.getByPlaceholderText('Search in conversation'), {
      target: { value: 'eta' },
    });
    expect(screen.getByText('1 / 1')).toBeInTheDocument();
    const mark = screen.getByText('ETA');
    expect(mark.tagName).toBe('MARK');
    expect(mark.closest('strong')).not.toBeNull();
  });

  it('does not count a hit on the markup itself, which is no longer on screen', () => {
    render(<ChatTranscript messages={THREAD} />);
    fireEvent.change(screen.getByPlaceholderText('Search in conversation'), {
      target: { value: '*eta' },
    });
    expect(screen.getByText('0 / 0')).toBeInTheDocument();
  });

  it('the contact\'s own text is formatted too, and stays escaped', () => {
    const { container } = render(
      <ChatTranscript messages={[message({ message: '*urgent* <b>x</b>' })]} />,
    );
    expect(within(container).getByText('urgent').tagName).toBe('STRONG');
    expect(container.querySelector('b')).toBeNull();
  });

  it('counts a hit only in what the bubble shows, not in a split-off quote', () => {
    render(
      <ChatTranscript
        messages={[message({ id: 3, type: 'outgoing', message: '> quoteword here\nYes, 3 units.' })]}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText('Search in conversation'), {
      target: { value: 'quoteword' },
    });
    expect(screen.getByText('0 / 0')).toBeInTheDocument();
  });
});
