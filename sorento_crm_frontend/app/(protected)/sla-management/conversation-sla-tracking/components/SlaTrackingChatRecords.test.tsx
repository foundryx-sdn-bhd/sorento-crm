import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

/**
 * "Chat Records" on the SLA detail page (UAC AC-N8).
 *
 * It is the ticket drawer's panel, mounted: `TicketConversationPanel` is one
 * component with two call sites (the parity of the two is pinned in
 * ChatPanelParity.test.tsx). What this file covers is the card chrome around
 * it - the header actions and the panel's states as they appear here.
 */
const slaConversation = vi.fn();
const ticketComments = vi.fn();
const interventionTicket = vi.fn();

vi.mock('../hooks/useConversationSLATracking', () => ({
  useSlaTrackingConversation: (...a: unknown[]) => slaConversation(...a),
  useSlaTrackingThreadLoaders: () => ({
    loadPage: vi.fn().mockResolvedValue({
      items: [],
      has_more_older: false,
      has_more_newer: false,
      oldest_message_id: null,
      newest_message_id: null,
    }),
    searchMessages: vi.fn().mockResolvedValue([]),
  }),
  useSlaTrackingMediaProxy: () => async () => new Response(),
}));

vi.mock('../hooks/useTicketComments', () => ({
  ticketCommentsKey: (id: string | null) => ['ticket-comments', id],
  useTicketComments: (...a: unknown[]) => ticketComments(...a),
  useCreateTicketComment: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock('../hooks/useInterventionTickets', () => ({
  useInterventionTicket: (...a: unknown[]) => interventionTicket(...a),
  useSendInterventionTicketMessage: () => ({ mutateAsync: vi.fn() }),
  useDraftInterventionTicketReply: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock('@/components/common/conversation/useConversationEvents', () => ({
  useConversationEvents: () => ({ connected: false }),
}));

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock('@/components/common/RespondChatList', () => ({
  default: ({
    items,
    comments = [],
    mediaProxy,
    searchController,
    onLoadOlder,
    maxHeightClass,
  }: {
    items: unknown[];
    comments?: unknown[];
    mediaProxy?: unknown;
    searchController?: unknown;
    onLoadOlder?: () => void;
    maxHeightClass?: string;
  }) => (
    <div
      data-testid="chat-list"
      data-notes={comments.length}
      data-has-media-proxy={mediaProxy ? 'yes' : 'no'}
      data-has-search={searchController ? 'yes' : 'no'}
      data-has-scrollback={onLoadOlder ? 'yes' : 'no'}
      data-max-height={maxHeightClass ?? ''}
    >
      {items.length} message(s)
    </div>
  ),
}));

vi.mock('@/components/common/conversation/InternalCommentComposer', () => ({
  default: () => <div data-testid="note-composer" />,
}));

vi.mock('@/components/common/conversation/SharedConversationComposer', () => ({
  default: ({
    canReply,
    notAvailableMessage,
    attachmentsEnabled,
    snippetsEnabled,
    emojiEnabled,
    onAiAssist,
  }: {
    canReply: boolean;
    notAvailableMessage?: string;
    attachmentsEnabled?: boolean;
    snippetsEnabled?: boolean;
    emojiEnabled?: boolean;
    onAiAssist?: unknown;
  }) =>
    canReply ? (
      <button
        type="button"
        data-testid="composer-send"
        data-attachments-enabled={String(!!attachmentsEnabled)}
        data-snippets-enabled={String(!!snippetsEnabled)}
        data-emoji-enabled={String(!!emojiEnabled)}
        data-ai-assist={String(!!onAiAssist)}
      >
        Send
      </button>
    ) : (
      <p data-testid="composer-unavailable">{notAvailableMessage}</p>
    ),
}));

import SlaTrackingChatRecords from './SlaTrackingChatRecords';
import type { InterventionTicketDetail } from '../services/interventionTicketService';

function queryState(over: Record<string, unknown> = {}) {
  return {
    data: { items: [{ messageId: 1, traffic: 'incoming', message: { type: 'text', text: 'hi' } }] },
    isLoading: false,
    isError: false,
    isRefetching: false,
    error: null,
    refetch: vi.fn(),
    ...over,
  };
}

function ticket(over: Partial<InterventionTicketDetail> = {}): InterventionTicketDetail {
  return {
    id: 't1',
    contact_name: 'Aisyah Rahman',
    contact_phone: '+60 12-000 0553',
    respond_io_id: '10025531',
    source_message_id: '123',
    source_message_text: 'Yes, please connect me to a person.',
    source_message_at: '2026-08-12T02:00:00',
    team_label: 'Customer Service - Tier 1',
    assignee_name: 'You',
    policy_name: 'Conversation SLA - Standard',
    initiated_at: '2026-08-12T02:00:00',
    current_tier: 1,
    escalated_at: null,
    escalation_reason: null,
    due_at: '2026-08-12T03:00:00',
    due_at_resolution: '2026-08-12T08:00:00',
    is_responded: false,
    responded_at: null,
    is_resolved: false,
    resolved_at: null,
    can_send: true,
    can_resolve: true,
    is_assignee: true,
    assignee_team_id: 'team-1',
    send_capabilities: ['text', 'attachment'],
    window: { open: true, expires_at: null },
    chat_template: null,
    ...over,
  };
}

beforeEach(() => {
  slaConversation.mockReset().mockReturnValue(queryState());
  ticketComments.mockReset().mockReturnValue({ data: [{ id: 'c1' }], isLoading: false });
  interventionTicket.mockReset().mockReturnValue({
    data: ticket(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
});

describe('SlaTrackingChatRecords (AC-N8)', () => {
  it('renders the shared thread with scroll-back, search, notes and the media proxy', () => {
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" showAsPopup />);

    const list = screen.getByTestId('chat-list');
    expect(list).toHaveTextContent('1 message(s)');
    expect(list).toHaveAttribute('data-has-scrollback', 'yes');
    expect(list).toHaveAttribute('data-has-search', 'yes');
    expect(list).toHaveAttribute('data-has-media-proxy', 'yes');
    expect(list).toHaveAttribute('data-notes', '1');
  });

  it('carries the drawer composer: attachments, snippets, emoji and AI assist', () => {
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" showAsPopup />);

    const send = screen.getByTestId('composer-send');
    expect(send).toHaveAttribute('data-attachments-enabled', 'true');
    expect(send).toHaveAttribute('data-snippets-enabled', 'true');
    expect(send).toHaveAttribute('data-emoji-enabled', 'true');
    expect(send).toHaveAttribute('data-ai-assist', 'true');
  });

  it('offers the internal-note composer, so a note can be left from here too', () => {
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" showAsPopup />);

    fireEvent.click(screen.getByTestId('composer-mode-comment'));
    expect(screen.getByTestId('note-composer')).toBeInTheDocument();
    expect(screen.queryByTestId('composer-send')).not.toBeInTheDocument();
  });

  it('loading state', () => {
    slaConversation.mockReturnValue(queryState({ data: undefined, isLoading: true }));
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" />);
    expect(screen.getByTestId('chat-records-loading')).toBeDefined();
  });

  it('error state offers a retry', () => {
    const refetch = vi.fn();
    slaConversation.mockReturnValue(
      queryState({
        data: undefined,
        isError: true,
        error: new Error('Failed to load the conversation.'),
        refetch,
      }),
    );
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" />);
    expect(screen.getByTestId('chat-records-error')).toHaveTextContent(
      'Failed to load the conversation.',
    );
    fireEvent.click(screen.getByRole('button', { name: /Try again/i }));
    expect(refetch).toHaveBeenCalled();
  });

  it('says why replying is unavailable when no Respond conversation is linked', () => {
    // No ticket detail either (a form-scope tracker 404s that route), so the
    // panel falls back to what this surface knows: no inbox link, no reply.
    interventionTicket.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('not found'),
      refetch: vi.fn(),
    });
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl={null} />);
    expect(screen.getByTestId('composer-unavailable')).toHaveTextContent(
      'No Respond conversation link available for this contact.',
    );
  });

  it('a linked contact can still reply when the ticket detail is out of reach', () => {
    interventionTicket.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('not found'),
      refetch: vi.fn(),
    });
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" />);
    expect(screen.getByTestId('composer-send')).toBeInTheDocument();
    // Nothing ticket-scoped is offered - there is no ticket to stamp.
    expect(screen.getByTestId('composer-send')).toHaveAttribute('data-snippets-enabled', 'false');
  });

  it('refresh re-reads the thread', () => {
    const refetch = vi.fn();
    slaConversation.mockReturnValue(queryState({ refetch }));
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" showAsPopup />);

    fireEvent.click(screen.getByRole('button', { name: 'Refresh messages' }));
    expect(refetch).toHaveBeenCalled();
  });

  // R3: the popup thread flex-fills the sheet, like the worklist drawer.
  it('AC-CP-5: showAsPopup gives the thread the flex-fill treatment, never the 55vh cap', () => {
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" showAsPopup />);

    const panel = screen.getByTestId('ticket-conversation-panel');
    // Fix round 5: `min-h-40`, not `min-h-0` - the floor has to sit on this
    // root too, not only the inner scroll box (AC-CP-B1 375px overlap).
    expect(panel.className).toContain('min-h-40');
    expect(panel.className).toContain('flex-1');

    // Fix round 6: the INNER scroll box no longer floors itself - the root
    // does (above), and a second, equal floor on the inner box overflowed
    // the root by exactly the header/search chrome's height.
    const maxHeight = screen.getByTestId('chat-list').getAttribute('data-max-height') ?? '';
    expect(maxHeight).toBe('min-h-0 flex-1');
    expect(maxHeight).not.toContain('max-h-[55vh]');
  });

  it('AC-CP-6: the inline (non-popup) mount keeps the fixed 400px cap', () => {
    render(<SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" />);

    expect(screen.getByTestId('chat-list')).toHaveAttribute('data-max-height', 'max-h-[400px]');
  });
});
