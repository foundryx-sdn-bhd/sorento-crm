import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';

import InterventionTicketDrawer from './InterventionTicketDrawer';
import type { InterventionTicketDetail } from '../services/interventionTicketService';

/**
 * Reassign and Resolve driven from an OPEN drawer, with the real dialogs
 * mounted (the sibling suite stubs ReassignDialog out, which is why nothing
 * caught this).
 *
 * The regression: both dialogs were rendered as siblings AFTER `</Sheet>`.
 * Radix decides "was that click outside me?" by walking the REACT tree, so a
 * pointerdown in the dialog, or in its portalled dropdown, counted as an
 * outside click on the drawer and dismissed it - the panel dropped away and
 * `ticketId` went null, after which Reassign did nothing at all. These tests
 * pin the drawer staying open through the whole interaction.
 */

const useInterventionTicket = vi.fn();
const useSlaTrackingConversation = vi.fn();
const useSlaTrackingThreadLoaders = vi.fn();
const useResolveInterventionTicket = vi.fn();
const useSendInterventionTicketMessage = vi.fn();
const useTicketComments = vi.fn();
const useCreateTicketComment = vi.fn();
const useDraftInterventionTicketReply = vi.fn();

vi.mock('../hooks/useInterventionTickets', () => ({
  useInterventionTicket: (...a: unknown[]) => useInterventionTicket(...a),
  useResolveInterventionTicket: (...a: unknown[]) => useResolveInterventionTicket(...a),
  useSendInterventionTicketMessage: (...a: unknown[]) => useSendInterventionTicketMessage(...a),
  useDraftInterventionTicketReply: (...a: unknown[]) => useDraftInterventionTicketReply(...a),
}));

vi.mock('../hooks/useTicketComments', () => ({
  ticketCommentsKey: (id: string | null) => ['ticket-comments', id],
  useTicketComments: (...a: unknown[]) => useTicketComments(...a),
  useCreateTicketComment: (...a: unknown[]) => useCreateTicketComment(...a),
}));

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock('@/components/common/conversation/useConversationEvents', () => ({
  useConversationEvents: () => ({ connected: false }),
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
}));

const USERS = [
  { id: 'u-1', name: 'Aisyah Rahman', email: 'aisyah@sorento.test', respond_linked: true },
  { id: 'u-2', name: 'Ben Lim', email: 'ben@sorento.test', respond_linked: false },
];
const reassignMutate = vi.fn();
const takeoverMutate = vi.fn();
vi.mock('../hooks/useTeamPendingSLA', () => ({
  useReassignSLATracking: () => ({ mutate: reassignMutate, isPending: false }),
  useTakeoverSLATracking: () => ({ mutate: takeoverMutate, isPending: false }),
  useVisibleUsers: () => ({ data: USERS, isLoading: false, error: null }),
}));

vi.mock('../hooks/useConversationSLATracking', () => ({
  useSlaTrackingConversation: (...a: unknown[]) => useSlaTrackingConversation(...a),
  useSlaTrackingThreadLoaders: (...a: unknown[]) => useSlaTrackingThreadLoaders(...a),
  useSlaTrackingMediaProxy: () => async () => new Response(),
}));

vi.mock('@/components/common/RespondChatList', () => ({
  default: () => <div data-testid="chat-list" />,
}));
vi.mock('@/components/common/conversation/InternalCommentComposer', () => ({
  default: () => <div data-testid="comment-composer" />,
}));
vi.mock('@/components/common/conversation/SharedConversationComposer', () => ({
  default: () => <div data-testid="composer" />,
}));

function makeTicket(over: Partial<InterventionTicketDetail> = {}): InterventionTicketDetail {
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
    due_at: new Date(Date.now() + 46 * 60_000).toISOString(),
    due_at_resolution: new Date(Date.now() + 350 * 60_000).toISOString(),
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

function mockQuery<T>(data: T | undefined) {
  return { data, isLoading: false, isError: false, error: null, refetch: vi.fn() };
}

let resolveMutate: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  useSlaTrackingThreadLoaders.mockReturnValue({
    loadPage: vi.fn().mockResolvedValue({
      items: [],
      has_more_older: false,
      has_more_newer: false,
      oldest_message_id: null,
      newest_message_id: null,
    }),
    searchMessages: vi.fn().mockResolvedValue([]),
  });
  useDraftInterventionTicketReply.mockReturnValue({ mutateAsync: vi.fn() });
  useTicketComments.mockReturnValue(mockQuery([]));
  useCreateTicketComment.mockReturnValue({ mutateAsync: vi.fn() });
  resolveMutate = vi.fn();
  useResolveInterventionTicket.mockReturnValue({ mutate: resolveMutate, isPending: false });
  useSendInterventionTicketMessage.mockReturnValue({ mutateAsync: vi.fn() });
  useSlaTrackingConversation.mockReturnValue(mockQuery({ items: [], error: null }));
  useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
});

/**
 * A pointerdown + click pair. The pointerdown is the half that matters: it is
 * what Radix's outside-dismissal watches, and `fireEvent.click` alone would
 * never have caught this.
 */
function press(el: Element) {
  fireEvent.pointerDown(el, { bubbles: true, button: 0, pointerType: 'mouse' });
  fireEvent.click(el, { bubbles: true, button: 0 });
}

function renderDrawer() {
  const onOpenChange = vi.fn();
  const onReassigned = vi.fn();
  const onResolved = vi.fn();
  render(
    <InterventionTicketDrawer
      ticketId="t1"
      open
      onOpenChange={onOpenChange}
      onReassigned={onReassigned}
      onResolved={onResolved}
    />,
  );
  return { onOpenChange, onReassigned, onResolved };
}

describe('acting on the ticket from inside the open drawer', () => {
  it('reassigns to the chosen colleague without the drawer closing', async () => {
    const { onOpenChange } = renderDrawer();

    press(await screen.findByTestId('ticket-reassign'));
    const dialog = await screen.findByRole('dialog', { name: 'Reassign task' });
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    press(within(dialog).getByRole('combobox'));
    press(await screen.findByText('Ben Lim'));
    // Opening the dropdown and picking from it both happen outside the
    // dialog's own DOM (the popover is portalled), and neither may touch the
    // drawer.
    expect(onOpenChange).not.toHaveBeenCalledWith(false);

    const confirm = within(dialog).getByRole('button', { name: 'Reassign' });
    await waitFor(() => expect(confirm).not.toBeDisabled());
    press(confirm);

    expect(reassignMutate).toHaveBeenCalledWith({ id: 't1', userId: 'u-2' }, expect.anything());
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('AC-M1: confirming a resolve leaves the drawer open too', async () => {
    const { onOpenChange } = renderDrawer();

    press(await screen.findByRole('button', { name: /Resolve ticket/i }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Mark as resolved' });

    press(within(dialog).getByRole('button', { name: 'Confirm' }));

    expect(resolveMutate).toHaveBeenCalledWith('t1', expect.anything());
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
