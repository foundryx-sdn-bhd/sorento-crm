/**
 * Internal notes rendered inline in the shared thread (UAC AC-L1).
 *
 * Notes are drawer-side data merged at RENDER time - they are never written
 * into `chat_histories` - so the ordering guarantee is this component's, and
 * this is where it is pinned.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import RespondChatList, { type ConversationCommentRenderable } from './RespondChatList';
import { formatBubbleTime, type RespondMessageRenderable } from '@/lib/respondIoChatRender';

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

/** Respond message ids ARE epoch-microsecond timestamps. */
function msgAt(iso: string, text: string): RespondMessageRenderable {
  return {
    messageId: Date.parse(`${iso}Z`) * 1000,
    traffic: 'incoming',
    message: { type: 'text', text },
    status: [],
  };
}

function note(iso: string, body: string, over: Partial<ConversationCommentRenderable> = {}) {
  return {
    id: `note-${iso}`,
    body,
    author_name: 'Agent One',
    created_at: iso,
    source: 'crm' as const,
    ...over,
  };
}

describe('RespondChatList internal notes (AC-L1)', () => {
  it('renders a note as a distinct Internal bubble with its author and body', () => {
    render(
      <RespondChatList
        items={[msgAt('2026-08-15T02:00:00', 'hello')]}
        comments={[note('2026-08-15T02:05:00', 'Waiting on the warehouse.')]}
      />,
    );

    const notes = screen.getAllByTestId('chat-internal-note');
    expect(notes).toHaveLength(1);
    expect(notes[0]).toHaveTextContent('Internal');
    expect(notes[0]).toHaveTextContent('Agent One');
    expect(notes[0]).toHaveTextContent('Waiting on the warehouse.');
  });

  it('shows who the note tags, the way the Respond inbox does', () => {
    render(
      <RespondChatList
        items={[]}
        comments={[
          note('2026-08-15T02:05:00', 'Team: it_admin', {
            author_name: 'Sorento Bot',
            mentioned_names: ['Jayson Foundryx', 'Manager KDS'],
          }),
        ]}
      />,
    );

    const mentions = screen.getByTestId('chat-internal-note-mentions');
    expect(mentions).toHaveTextContent('@Jayson Foundryx');
    expect(mentions).toHaveTextContent('@Manager KDS');
  });

  it('a CRM note whose body already names the tagged user draws no duplicate mention row', () => {
    render(
      <RespondChatList
        items={[]}
        comments={[
          note('2026-08-15T02:05:00', '@Kia Yee Please assist on this', {
            author_name: 'Tay Zhi Yang',
            source: 'crm',
            mentioned_names: ['Kia Yee'],
          }),
        ]}
      />,
    );
    expect(screen.queryByTestId('chat-internal-note-mentions')).toBeNull();
    expect(screen.getByTestId('chat-internal-note')).toHaveTextContent('@Kia Yee Please assist on this');
  });

  it('a note tagging nobody draws no mention row', () => {
    render(<RespondChatList items={[]} comments={[note('2026-08-15T02:05:00', 'plain')]} />);
    expect(screen.queryByTestId('chat-internal-note-mentions')).toBeNull();
  });

  it('styles the note amber, so it can never read as a message to the contact', () => {
    render(<RespondChatList items={[]} comments={[note('2026-08-15T02:05:00', 'internal')]} />);

    const bubble = screen.getByText('internal').closest('div.rounded-lg');
    expect(bubble?.className).toMatch(/amber/);
  });

  it('interleaves notes with messages chronologically, not appended at the end', () => {
    render(
      <RespondChatList
        items={[
          msgAt('2026-08-15T01:00:00', 'first message'),
          msgAt('2026-08-15T03:00:00', 'third message'),
        ]}
        comments={[note('2026-08-15T02:00:00', 'second note')]}
      />,
    );

    const container = screen.getByTestId('chat-scroll-container');
    const rendered = container.textContent ?? '';
    expect(rendered.indexOf('first message')).toBeLessThan(rendered.indexOf('second note'));
    expect(rendered.indexOf('second note')).toBeLessThan(rendered.indexOf('third message'));
  });

  it('a thread with only notes is not the empty state', () => {
    render(
      <RespondChatList
        items={[]}
        comments={[note('2026-08-15T02:00:00', 'only a note')]}
        emptyHint="No messages in this conversation yet."
      />,
    );

    expect(screen.queryByText('No messages in this conversation yet.')).not.toBeInTheDocument();
    expect(screen.getByText('only a note')).toBeInTheDocument();
  });

  it('surfaces that pass no notes render exactly as before', () => {
    render(<RespondChatList items={[msgAt('2026-08-15T02:00:00', 'hello')]} />);

    expect(screen.queryByTestId('chat-internal-note')).not.toBeInTheDocument();
    expect(screen.getByText('hello')).toBeInTheDocument();
  });

  it('stamps the note on the same clock as the messages around it', () => {
    // FINDING 9: notes rendered Malaysia wall-clock AND a full date while the
    // bubble beside them rendered browser-local time only, so two entries a
    // minute apart could read hours apart.
    const createdAt = '2026-08-15T02:05:00';
    render(
      <RespondChatList
        items={[msgAt('2026-08-15T02:00:00', 'hello')]}
        comments={[note(createdAt, 'Waiting on the warehouse.')]}
      />,
    );

    const stamp = screen.getByTestId('chat-internal-note-time');
    expect(stamp.textContent).toBe(formatBubbleTime(Date.parse(`${createdAt}Z`)));
    // The day lives on the date pill, exactly as it does for a message.
    expect(stamp.textContent).not.toMatch(/2026/);
  });

  it('a Respond-side note renders with its inbox author, not a blank', () => {
    render(
      <RespondChatList
        items={[]}
        comments={[
          note('2026-08-15T02:00:00', 'Spoke to him on the phone.', {
            author_name: 'Inbox Agent',
            source: 'respond',
          }),
        ]}
      />,
    );

    expect(screen.getByTestId('chat-internal-note')).toHaveTextContent('Inbox Agent');
  });

  it('makes a url pasted into a note clickable, opening in a new tab', () => {
    const url = 'https://app.respond.io/space/364817/inbox/900000108#1787975493000000';
    render(
      <RespondChatList
        items={[]}
        comments={[note('2026-08-15T02:00:00', `Ref conversation ${url} for context.`)]}
      />,
    );

    const link = screen.getByRole('link', { name: url });
    expect(link).toHaveAttribute('href', url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    // The words around the link are still there, as text.
    expect(screen.getByTestId('chat-internal-note')).toHaveTextContent('for context.');
  });
});
