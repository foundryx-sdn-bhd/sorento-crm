/**
 * The ticket drawer and the SLA detail page's "Chat Records" must be the SAME
 * chat panel (UAC AC-N8; captain's feedback 2026-08-16, item 2).
 *
 * They were two call sites rendering the same `RespondChatList` with different
 * prop sets, and the detail page quietly lacked attachments, the snippet
 * picker, emoji, AI assist, the manual template send, the real 24h-window state
 * and the note composer. Both now mount `TicketConversationPanel`, and this
 * file pins it from the outside: render each surface, capture what actually
 * reached the shared thread + composer, and compare.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { InterventionTicketDetail } from '../services/interventionTicketService';

const chatListProps: Record<string, unknown>[] = [];
const composerProps: Record<string, unknown>[] = [];

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
  useResolveInterventionTicket: () => ({ mutate: vi.fn(), isPending: false }),
  useSendInterventionTicketMessage: () => ({ mutateAsync: vi.fn() }),
  useDraftInterventionTicketReply: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock('../hooks/useTeamPendingSLA', () => ({
  useReassignSLATracking: () => ({ mutate: vi.fn(), isPending: false }),
  useTakeoverSLATracking: () => ({ mutate: vi.fn(), isPending: false }),
  useVisibleUsers: () => ({ data: [], isLoading: false, error: null }),
}));

vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));

vi.mock('@/components/common/conversation/useConversationEvents', () => ({
  useConversationEvents: () => ({ connected: false }),
}));

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock('@/components/common/RespondChatList', () => ({
  default: (props: Record<string, unknown>) => {
    chatListProps.push(props);
    return <div data-testid="chat-list" />;
  },
}));

vi.mock('@/components/common/conversation/SharedConversationComposer', () => ({
  default: (props: Record<string, unknown>) => {
    composerProps.push(props);
    return <div data-testid="composer" />;
  },
}));

vi.mock('@/components/common/conversation/InternalCommentComposer', () => ({
  default: () => <div data-testid="note-composer" />,
}));

import InterventionTicketDrawer from './InterventionTicketDrawer';
import SlaTrackingChatRecords from './SlaTrackingChatRecords';

const TICKET: InterventionTicketDetail = {
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
};

beforeEach(() => {
  chatListProps.length = 0;
  composerProps.length = 0;
  slaConversation.mockReset().mockReturnValue({
    data: { items: [{ messageId: 1, traffic: 'incoming', message: { type: 'text', text: 'hi' } }] },
    isLoading: false,
    isError: false,
    isRefetching: false,
    error: null,
    refetch: vi.fn(),
  });
  ticketComments.mockReset().mockReturnValue({ data: [{ id: 'c1' }], isLoading: false });
  interventionTicket.mockReset().mockReturnValue({
    data: TICKET,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
});

/** Prop names whose VALUE is legitimately surface-specific. */
const SURFACE_SPECIFIC = new Set(['maxHeightClass', 'entityId', 'notAvailableMessage']);

/** Comparable shape: functions collapse to a marker (identity differs per render). */
function shape(props: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(props)) {
    if (SURFACE_SPECIFIC.has(key)) {
      out[key] = value == null ? null : 'set';
      continue;
    }
    if (typeof value === 'function') {
      out[key] = 'fn';
      continue;
    }
    if (Array.isArray(value)) {
      out[key] = `array(${value.length})`;
      continue;
    }
    if (value && typeof value === 'object') {
      out[key] = JSON.stringify(value);
      continue;
    }
    out[key] = value;
  }
  return out;
}

function renderDrawer() {
  render(
    <InterventionTicketDrawer ticketId="t1" open onOpenChange={vi.fn()} />,
  );
  return { chatList: chatListProps.at(-1)!, composer: composerProps.at(-1)! };
}

function renderChatRecords() {
  render(
    <SlaTrackingChatRecords trackingId="t1" respondInboxUrl="https://respond.io/x" showAsPopup />,
  );
  return { chatList: chatListProps.at(-1)!, composer: composerProps.at(-1)! };
}

describe('the ticket drawer and Chat Records are one chat panel', () => {
  it('hands RespondChatList the same props from both surfaces', () => {
    const drawer = renderDrawer();
    chatListProps.length = 0;
    const records = renderChatRecords();

    expect(screen.getAllByTestId('chat-list').length).toBeGreaterThan(0);
    expect(shape(records.chatList)).toEqual(shape(drawer.chatList));
  });

  it('hands the composer the same props from both surfaces', () => {
    const drawer = renderDrawer();
    composerProps.length = 0;
    const records = renderChatRecords();

    expect(shape(records.composer)).toEqual(shape(drawer.composer));
  });

  it('the capabilities the captain found missing are on BOTH', () => {
    const drawer = renderDrawer();
    composerProps.length = 0;
    const records = renderChatRecords();

    for (const surface of [drawer.composer, records.composer]) {
      expect(surface.attachmentsEnabled).toBe(true);
      expect(surface.snippetsEnabled).toBe(true);
      expect(surface.emojiEnabled).toBe(true);
      expect(typeof surface.onAiAssist).toBe('function');
      expect(typeof surface.sendAdapter).toBe('function');
      expect(surface.templateSendTrackingId).toBe('t1');
      expect(surface.windowStateOverride).toEqual({ closed: false, template: null });
    }
    for (const surface of [drawer.chatList, records.chatList]) {
      expect(surface.contactName).toBe('Aisyah Rahman');
      expect(typeof surface.searchController).toBe('object');
      expect(typeof surface.onLoadOlder).toBe('function');
      expect(typeof surface.mediaProxy).toBe('function');
      expect(surface.comments).toHaveLength(1);
      // B2 / R4: the reply-to quote's fetch-back loader (`thread.jumpToMessage`)
      // has to actually reach RespondChatList on BOTH surfaces - deleting the
      // wiring in TicketConversationPanel left every other assertion in this
      // file green, since the shape() comparison only checks the two surfaces
      // agree with EACH OTHER, not that either of them got a real function.
      expect(typeof surface.onJumpToMessage).toBe('function');
      // #1317: Reply (bubble menu + swipe) reaches both mounts. The menu itself
      // is on every RespondChatList (owner answer 3), so there is no opt-in.
      expect(typeof surface.onReply).toBe('function');
    }
  });

  it('both offer the Reply / Comment switch', () => {
    renderDrawer();
    expect(screen.getByTestId('composer-mode-reply')).toBeInTheDocument();
    expect(screen.getByTestId('composer-mode-comment')).toBeInTheDocument();

    screen.getByTestId('composer-mode-reply');
    renderChatRecords();
    expect(screen.getAllByTestId('composer-mode-comment').length).toBe(2);
  });
});
