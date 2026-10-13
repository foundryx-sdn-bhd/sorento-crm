/**
 * R9 (CHAT-LOCAL-FIRST): after a ticket reply the panel re-reads the thread
 * endpoint and shows what that read returns, so the row the CRM mirrors at send
 * time reaches the screen. This is a regression guard for the post-send refetch
 * (it exists today); the backend half is tests/test_ticket_panel_local_first.py.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import type { InterventionTicketDetail } from '../services/interventionTicketService';

const getConversation = vi.fn();
const sendTicketMessage = vi.fn();

vi.mock('../services/conversationSLATrackingService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getSlaTrackingConversation: (...a: unknown[]) => getConversation(...a),
}));

vi.mock('../services/interventionTicketService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  sendInterventionTicketMessage: (...a: unknown[]) => sendTicketMessage(...a),
}));

const TICKET = {
  id: 't1',
  contact_name: 'Zzt Contact',
  respond_io_id: 'ZZT100000001',
  source_message_id: '11',
  is_resolved: false,
  can_send: true,
  send_capabilities: ['text'],
  window: { open: true, expires_at: null },
  chat_template: null,
} as unknown as InterventionTicketDetail;

vi.mock('../hooks/useInterventionTickets', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useInterventionTicket: () => ({
    data: TICKET,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useDraftInterventionTicketReply: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock('../hooks/useTicketComments', () => ({
  ticketCommentsKey: (id: string | null) => ['ticket-comments', id],
  useTicketComments: () => ({ data: [], isLoading: false }),
  useCreateTicketComment: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock('@/components/common/conversation/useConversationEvents', () => ({
  useConversationEvents: () => ({ connected: false }),
}));

vi.mock('@/components/common/RespondChatList', () => ({
  default: ({ items }: { items: { messageId?: number; message?: { text?: string } }[] }) => (
    <ul data-testid="thread">
      {items.map((i) => (
        <li key={String(i.messageId)}>{i.message?.text}</li>
      ))}
    </ul>
  ),
}));

vi.mock('@/components/common/conversation/SharedConversationComposer', () => ({
  default: (props: Record<string, unknown>) => (
    <button
      type="button"
      data-testid="send"
      onClick={async () => {
        await (props.sendAdapter as (p: unknown) => Promise<unknown>)({
          text: 'Zzt reply from the CRM',
          files: [],
        });
        await (props.onSent as () => unknown)();
      }}
    >
      send
    </button>
  ),
}));

vi.mock('@/components/common/conversation/InternalCommentComposer', () => ({
  default: () => <div />,
}));

import TicketConversationPanel from './TicketConversationPanel';

const EXISTING = {
  messageId: 1786751891000000,
  traffic: 'incoming',
  message: { type: 'text', text: 'Zzt customer question' },
  source: 'local',
};
const SENT = {
  messageId: 1786751892000000,
  traffic: 'outgoing',
  message: { type: 'text', text: 'Zzt reply from the CRM' },
  source: 'local',
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TicketConversationPanel ticketId="t1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getConversation.mockReset();
  sendTicketMessage.mockReset().mockResolvedValue({ sent_as: 'text' });
});

describe('TicketConversationPanel local-first refetch', () => {
  it('R9: a send re-reads the thread endpoint and renders the sent row', async () => {
    getConversation
      .mockResolvedValueOnce({ items: [EXISTING] })
      .mockResolvedValue({ items: [EXISTING, SENT] });

    renderPanel();
    await screen.findByText('Zzt customer question');
    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Zzt reply from the CRM')).toBeNull();

    fireEvent.click(screen.getByTestId('send'));

    await waitFor(() => expect(sendTicketMessage).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getConversation.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText('Zzt reply from the CRM')).toBeInTheDocument();
  });
});
