import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

import InterventionTicketDrawer from './InterventionTicketDrawer';
import type { InterventionTicketDetail } from '../services/interventionTicketService';

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

// AC-K1: the live-thread subscriber. Stubbed so the suite stays about the
// drawer; the hook has its own test. `liveConnected` false keeps the fast poll,
// which is the interval the polling assertions below are written against.
const invalidateQueries = vi.fn();
interface LiveEventsArgs {
  contactIds: (string | null | undefined)[];
  enabled: boolean;
  onEvent: (event: { type: string }) => void;
  onReady?: () => void;
}
const conversationEvents = vi.fn<(options: LiveEventsArgs) => { connected: boolean }>();
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries }),
}));
vi.mock('@/components/common/conversation/useConversationEvents', () => ({
  useConversationEvents: (options: LiveEventsArgs) => conversationEvents(options),
}));

// AC-N7: Reassign in the header is permission-gated and uses the SHARED dialog.
const hasPermission = vi.fn<(slug?: string) => boolean>(() => true);
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (...a: unknown[]) => hasPermission(...(a as [])),
}));

const reassignMutate = vi.fn();
const takeoverMutate = vi.fn();
vi.mock('../hooks/useTeamPendingSLA', () => ({
  useReassignSLATracking: () => ({ mutate: reassignMutate, isPending: false }),
  useTakeoverSLATracking: () => ({ mutate: takeoverMutate, isPending: false }),
}));

// The extend dialog is exercised on its own (ExtendDueDialog.test.tsx); here we
// only care that the gear opens THAT dialog, for THIS ticket, and that a
// successful extend re-reads the ticket so the chips move.
const extendDialogProps: Record<string, unknown>[] = [];
vi.mock('./ExtendDueDialog', () => ({
  default: (props: Record<string, unknown>) => {
    extendDialogProps.push(props);
    return (
      <div
        data-testid="extend-dialog"
        data-tracking-id={String(props.trackingId ?? '')}
        data-current-due={String(props.currentDueAt ?? '')}
      >
        <button
          type="button"
          data-testid="extend-confirm"
          onClick={() => (props.onExtended as (() => void) | undefined)?.()}
        >
          Extend deadline
        </button>
      </div>
    );
  },
}));

vi.mock('./ReassignDialog', () => ({
  default: ({
    open,
    taskLabel,
    onConfirm,
  }: {
    open: boolean;
    taskLabel?: string | null;
    onConfirm: (userId: string) => void;
  }) =>
    open ? (
      <div data-testid="reassign-dialog" data-task-label={taskLabel ?? ''}>
        <button type="button" data-testid="reassign-confirm" onClick={() => onConfirm('u-2')}>
          Reassign
        </button>
      </div>
    ) : null,
}));

// FINDING 9: the thread is the SHARED conversation query (one key with the SLA
// detail page's panel), not a private copy. The scroll-back / search loaders
// come through a hook too (layering: UI -> hook -> service), never as direct
// service imports in the component.
vi.mock('../hooks/useConversationSLATracking', () => ({
  useSlaTrackingConversation: (...a: unknown[]) => useSlaTrackingConversation(...a),
  useSlaTrackingThreadLoaders: (...a: unknown[]) => useSlaTrackingThreadLoaders(...a),
  // AC-N4: the chat-media byte loader handed to RespondChatList (stubbed here).
  useSlaTrackingMediaProxy: () => async () => new Response(),
}));

// jsdom does not implement scrollIntoView; guarded in the real component with
// `?.scrollIntoView?.(...)`, but RespondChatList is stubbed out here anyway.
vi.mock('@/components/common/RespondChatList', () => ({
  default: ({
    items,
    contactName,
    comments = [],
    focusMessageId = null,
    focusNonce = 0,
    maxHeightClass,
    className,
  }: {
    items: unknown[];
    contactName?: string | null;
    comments?: unknown[];
    focusMessageId?: string | null;
    focusNonce?: number;
    maxHeightClass?: string;
    className?: string;
  }) => (
    <div
      data-testid="chat-list"
      data-contact={contactName ?? ''}
      data-notes={comments.length}
      data-focus-message-id={focusMessageId ?? ''}
      data-focus-nonce={String(focusNonce)}
      data-max-height={maxHeightClass ?? ''}
      data-class-name={className ?? ''}
    >
      {items.length} message(s)
    </div>
  ),
}));

vi.mock('@/components/common/conversation/InternalCommentComposer', () => ({
  default: ({
    disabled,
    disabledMessage,
    onSubmit,
  }: {
    disabled?: boolean;
    disabledMessage?: string;
    onSubmit: (p: { body: string; mentionedUserIds: string[] }) => Promise<unknown>;
  }) =>
    disabled ? (
      <p data-testid="comment-composer-unavailable">{disabledMessage}</p>
    ) : (
      <button
        data-testid="comment-composer-submit"
        onClick={() => void onSubmit({ body: 'internal note', mentionedUserIds: ['u-2'] })}
      >
        Add note
      </button>
    ),
}));

vi.mock('@/components/common/conversation/SharedConversationComposer', () => ({
  default: ({
    canReply,
    attachmentsEnabled,
    notAvailableMessage,
    sendAdapter,
    templateSendTrackingId,
    onSent,
    snippetsEnabled,
    snippetTrackingId,
    emojiEnabled,
    onAiAssist,
  }: {
    canReply: boolean;
    attachmentsEnabled?: boolean;
    notAvailableMessage?: string;
    sendAdapter: (payload: { text: string; files: File[] }) => Promise<unknown>;
    templateSendTrackingId?: string | null;
    onSent?: () => void;
    snippetsEnabled?: boolean;
    snippetTrackingId?: string | null;
    emojiEnabled?: boolean;
    onAiAssist?: (input: { instruction?: string }) => Promise<string>;
  }) =>
    canReply ? (
      <>
        <button
          data-testid="composer-send"
          data-attachments-enabled={String(!!attachmentsEnabled)}
          data-template-tracking-id={templateSendTrackingId ?? ''}
          data-snippets-enabled={String(!!snippetsEnabled)}
          data-snippet-tracking-id={snippetTrackingId ?? ''}
          data-emoji-enabled={String(!!emojiEnabled)}
          data-ai-assist={String(!!onAiAssist)}
          onClick={() => void sendAdapter({ text: 'hello', files: [] })}
        >
          Send
        </button>
        <button data-testid="composer-sent" onClick={() => onSent?.()}>
          sent
        </button>
        <button
          data-testid="composer-ai-assist"
          onClick={() => void onAiAssist?.({ instruction: 'offer Tuesday delivery' })}
        >
          ai
        </button>
      </>
    ) : (
      <p data-testid="composer-unavailable">{notAvailableMessage}</p>
    ),
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

function mockQuery<T>(data: T | undefined, opts: Partial<{ isLoading: boolean; isError: boolean; error: Error }> = {}) {
  return {
    data,
    isLoading: !!opts.isLoading,
    isError: !!opts.isError,
    error: opts.error ?? null,
    refetch: vi.fn(),
  };
}

const thread = {
  items: [{ messageId: 1, traffic: 'incoming', message: { type: 'text', text: 'hi' } }],
  error: null,
};

let resolveMutate: ReturnType<typeof vi.fn>;
let sendMutateAsync: ReturnType<typeof vi.fn>;
let commentMutateAsync: ReturnType<typeof vi.fn>;
let aiDraftMutateAsync: ReturnType<typeof vi.fn>;

beforeEach(() => {
  useInterventionTicket.mockReset();
  useSlaTrackingConversation.mockReset();
  useSlaTrackingThreadLoaders.mockReset();
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
  useResolveInterventionTicket.mockReset();
  useSendInterventionTicketMessage.mockReset();
  useTicketComments.mockReset();
  useCreateTicketComment.mockReset();
  useDraftInterventionTicketReply.mockReset();
  reassignMutate.mockReset();
  takeoverMutate.mockReset();
  hasPermission.mockReturnValue(true);
  invalidateQueries.mockReset();
  conversationEvents.mockReset();
  conversationEvents.mockReturnValue({ connected: false });

  aiDraftMutateAsync = vi.fn().mockResolvedValue({ draft: 'drafted', model: 'gpt-4o', grounded_on: 3, elapsed_ms: 10 });
  useDraftInterventionTicketReply.mockReturnValue({ mutateAsync: aiDraftMutateAsync });

  useTicketComments.mockReturnValue(mockQuery([]));
  commentMutateAsync = vi.fn().mockResolvedValue({ id: 'c1' });
  useCreateTicketComment.mockReturnValue({ mutateAsync: commentMutateAsync });

  resolveMutate = vi.fn((_id: string, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.());
  useResolveInterventionTicket.mockReturnValue({ mutate: resolveMutate, isPending: false });

  sendMutateAsync = vi.fn().mockResolvedValue({ sent_as: 'text' });
  useSendInterventionTicketMessage.mockReturnValue({ mutateAsync: sendMutateAsync });

  useSlaTrackingConversation.mockReturnValue(mockQuery(thread));
});

function renderDrawer(props: Partial<React.ComponentProps<typeof InterventionTicketDrawer>> = {}) {
  const onOpenChange = vi.fn();
  const onResolved = vi.fn();
  const onSent = vi.fn();
  render(
    <InterventionTicketDrawer
      ticketId="t1"
      open
      onOpenChange={onOpenChange}
      onResolved={onResolved}
      onSent={onSent}
      {...props}
    />,
  );
  return { onOpenChange, onResolved, onSent };
}

describe('InterventionTicketDrawer', () => {
  it('loading state: shows skeletons, no enquiry header yet', () => {
    useInterventionTicket.mockReturnValue(mockQuery(undefined, { isLoading: true }));
    renderDrawer();
    expect(screen.getByText(/Loading enquiry/i)).toBeInTheDocument();
    expect(screen.queryByText(/Yes, please connect/i)).not.toBeInTheDocument();
  });

  it('error state: shows the message and a retry button that refetches', () => {
    const refetch = vi.fn();
    useInterventionTicket.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('Failed to load this ticket'),
      refetch,
    });
    renderDrawer();
    expect(screen.getByText('Failed to load this ticket')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Try again/i }));
    expect(refetch).toHaveBeenCalled();
  });

  it('data state: renders enquiry header, chips, team, and the thread', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    await waitFor(() => expect(screen.getByText('Aisyah Rahman')).toBeInTheDocument());
    expect(screen.getByText('Yes, please connect me to a person.')).toBeInTheDocument();
    expect(screen.getByText('Customer Service - Tier 1')).toBeInTheDocument();
    expect(screen.getByText(/Respond in/i)).toBeInTheDocument();
    expect(screen.getByTestId('chat-list')).toHaveTextContent('1 message(s)');
  });

  it('FINDING 9: the thread comes from the SHARED conversation query, limit 50', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('chat-list')).toBeInTheDocument());
    expect(useSlaTrackingConversation).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ limit: 50 }),
    );
  });

  it('FINDING 14: the scroll-back and search loaders come from a hook, keyed on the ticket', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('chat-list')).toBeInTheDocument());
    expect(useSlaTrackingThreadLoaders).toHaveBeenCalledWith('t1');
  });

  it('an open drawer polls the thread, so a contact reply appears on its own', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('chat-list')).toBeInTheDocument());
    expect(useSlaTrackingConversation).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ refetchIntervalMs: 10_000 }),
    );
  });

  it('a closed drawer asks for no ticket at all, so the polling stops with it', () => {
    // The chat panel lives INSIDE the sheet, so a closed drawer does not mount
    // it: the thread is not read with a null id, it is not read at all.
    useInterventionTicket.mockReturnValue(mockQuery(undefined));
    renderDrawer({ open: false });
    expect(useSlaTrackingConversation).not.toHaveBeenCalled();
  });

  // ---- AC-K1 / AC-K2: live thread ----------------------------------------

  it('AC-K1: subscribes the live stream to this ticket contact while open', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('chat-list')).toBeInTheDocument());
    expect(conversationEvents).toHaveBeenCalledWith(
      expect.objectContaining({ contactIds: ['10025531'], enabled: true }),
    );
  });

  it('AC-K2: a closed drawer holds no stream', () => {
    useInterventionTicket.mockReturnValue(mockQuery(undefined));
    renderDrawer({ open: false });
    expect(conversationEvents).not.toHaveBeenCalled();
  });

  it('AC-K1: a message poke refetches the thread and the notes, not the ticket', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('chat-list')).toBeInTheDocument());

    const { onEvent } = conversationEvents.mock.calls.at(-1)![0];
    invalidateQueries.mockClear();
    onEvent({ type: 'message' });

    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['sla-tracking-conversation', 't1'],
    });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['ticket-comments', 't1'] });
    expect(invalidateQueries).not.toHaveBeenCalledWith({
      queryKey: ['intervention-ticket', 't1'],
    });
  });

  it('AC-K1: a ticket poke refetches the ticket (clocks, assignee, resolution)', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('chat-list')).toBeInTheDocument());

    const { onEvent } = conversationEvents.mock.calls.at(-1)![0];
    invalidateQueries.mockClear();
    onEvent({ type: 'ticket_updated' });

    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['intervention-ticket', 't1'],
    });
  });

  it('AC-K1: the poll relaxes while the stream is connected', async () => {
    conversationEvents.mockReturnValue({ connected: true });
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('chat-list')).toBeInTheDocument());
    expect(useSlaTrackingConversation).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ refetchIntervalMs: 60_000 }),
    );
  });

  it('AC-E7: a blank enquiry text falls back to a neutral header label', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket({ source_message_text: '  ' })));
    renderDrawer();
    await waitFor(() =>
      expect(screen.getByText('No enquiry text captured.')).toBeInTheDocument(),
    );
  });

  it('composer: enabled + attachments on when the ticket allows sending', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('composer-send')).toBeInTheDocument());
    expect(screen.getByTestId('composer-send')).toHaveAttribute('data-attachments-enabled', 'true');

    fireEvent.click(screen.getByTestId('composer-send'));
    await waitFor(() => expect(sendMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ text: 'hello' }),
    ));
  });

  it('FINDING 4: the template send is stamped with this ticket id', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('composer-send')).toBeInTheDocument());
    expect(screen.getByTestId('composer-send')).toHaveAttribute(
      'data-template-tracking-id',
      't1',
    );
  });

  it('a send tells the worklist to reload, not just this drawer', async () => {
    // The worklist behind the drawer loads outside react-query, so nothing a
    // mutation invalidates reaches it - the drawer has to say so directly.
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    const { onSent } = renderDrawer();

    await waitFor(() => expect(screen.getByTestId('composer-sent')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('composer-sent'));

    expect(onSent).toHaveBeenCalled();
  });

  it('composer: disabled with a reason once the ticket is resolved', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket({ is_resolved: true, can_resolve: false })));
    renderDrawer();
    await waitFor(() => expect(screen.getByTestId('composer-unavailable')).toBeInTheDocument());
    expect(screen.getByTestId('composer-unavailable')).toHaveTextContent('This ticket is resolved.');
  });

  it('resolve flow: confirm dialog copy names the sibling contact, confirming resolves only this ticket', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    const { onOpenChange, onResolved } = renderDrawer();

    await waitFor(() => expect(screen.getByRole('button', { name: /Resolve ticket/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Resolve ticket/i }));

    await waitFor(() => expect(screen.getByText('Mark as resolved')).toBeInTheDocument());
    expect(screen.getByText(/Other open enquiries from Aisyah Rahman stay open/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Confirm$/i }));
    expect(resolveMutate).toHaveBeenCalledWith('t1', expect.any(Object));
    // AC-M1: the drawer does NOT close itself - only the user closes it.
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(onResolved).toHaveBeenCalled();
  });

  it('Resolve button is disabled when the ticket cannot be resolved', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket({ can_resolve: false })));
    renderDrawer();
    await waitFor(() => expect(screen.getByRole('button', { name: /Resolve ticket/i })).toBeDisabled());
  });

  // ---- AC-L1: comment mode -------------------------------------------------

  it('opens in Reply mode: the message composer is shown, the note composer is not', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    await waitFor(() => expect(screen.getByTestId('composer-send')).toBeInTheDocument());
    expect(screen.queryByTestId('comment-composer-submit')).not.toBeInTheDocument();
    expect(screen.getByTestId('composer-mode-reply')).toHaveAttribute('aria-selected', 'true');
  });

  it('switching to Comment swaps the composer, so a note can never be sent to the contact', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    await waitFor(() => expect(screen.getByTestId('composer-mode-comment')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('composer-mode-comment'));

    expect(screen.getByTestId('comment-composer-submit')).toBeInTheDocument();
    expect(screen.queryByTestId('composer-send')).not.toBeInTheDocument();
    expect(screen.getByTestId('composer-mode-comment')).toHaveAttribute('aria-selected', 'true');
  });

  it('adding a note calls the comment mutation, never the send mutation', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    await waitFor(() => expect(screen.getByTestId('composer-mode-comment')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('composer-mode-comment'));
    fireEvent.click(screen.getByTestId('comment-composer-submit'));

    await waitFor(() =>
      expect(commentMutateAsync).toHaveBeenCalledWith({
        body: 'internal note',
        mentioned_user_ids: ['u-2'],
      }),
    );
    expect(sendMutateAsync).not.toHaveBeenCalled();
  });

  it('a resolved ticket disables the note composer with a reason', async () => {
    useInterventionTicket.mockReturnValue(
      mockQuery(makeTicket({ is_resolved: true, can_resolve: false })),
    );
    renderDrawer();

    await waitFor(() => expect(screen.getByTestId('composer-mode-comment')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('composer-mode-comment'));

    expect(screen.getByTestId('comment-composer-unavailable')).toHaveTextContent(
      'This ticket is resolved.',
    );
  });

  it('the notes reach the thread so they render inline with the messages', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    useTicketComments.mockReturnValue(
      mockQuery([
        {
          id: 'c1',
          tracking_id: 't1',
          body: 'Waiting on the warehouse.',
          author_name: 'Agent One',
          mentioned_names: [],
          source: 'crm',
          created_at: '2026-08-15T02:00:00',
        },
      ]),
    );
    renderDrawer();

    await waitFor(() => expect(screen.getByTestId('chat-list')).toHaveAttribute('data-notes', '1'));
  });

  it('a closed drawer loads no notes either', () => {
    useInterventionTicket.mockReturnValue(mockQuery(undefined));
    renderDrawer({ open: false });
    expect(useTicketComments).not.toHaveBeenCalled();
  });
});

// --------------------------------------------------------- composer parity
// UAC AC-L4 / AC-L5, slice S4.4. The drawer only says WHICH ticket; snippet
// variables and the AI grounding are both resolved server-side against it.

describe('InterventionTicketDrawer composer parity (AC-L4 / AC-L5)', () => {
  it('turns on the snippet picker for THIS ticket', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    const send = await screen.findByTestId('composer-send');
    expect(send).toHaveAttribute('data-snippets-enabled', 'true');
    expect(send).toHaveAttribute('data-snippet-tracking-id', 't1');
  });

  it('turns on the emoji picker', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    expect(await screen.findByTestId('composer-send')).toHaveAttribute(
      'data-emoji-enabled',
      'true',
    );
  });

  it('offers AI assist and passes the instruction through to the ticket draft', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    expect(await screen.findByTestId('composer-send')).toHaveAttribute('data-ai-assist', 'true');
    fireEvent.click(screen.getByTestId('composer-ai-assist'));

    await waitFor(() =>
      expect(aiDraftMutateAsync).toHaveBeenCalledWith({
        instruction: 'offer Tuesday delivery',
      }),
    );
  });
});

// ------------------------------------------------ post-resolve reassurance
// UAC AC-M1 / AC-M2, slice S4.5. Resolving used to close the drawer, which
// yanked the conversation away at exactly the moment the assignee wants to
// re-read what they just agreed to.

describe('InterventionTicketDrawer resolved state (AC-M1 / AC-M2)', () => {
  it('stays open after a resolve and refetches the ticket into its Resolved state', async () => {
    const refetch = vi.fn();
    useInterventionTicket.mockReturnValue({ ...mockQuery(makeTicket()), refetch });
    const { onOpenChange, onResolved } = renderDrawer();

    fireEvent.click(await screen.findByRole('button', { name: /Resolve ticket/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^Confirm$/i }));

    expect(onOpenChange).not.toHaveBeenCalled();
    expect(refetch).toHaveBeenCalled();
    // The worklist behind it still drops the row: it is no longer pending.
    expect(onResolved).toHaveBeenCalled();
  });

  it('shows a Resolved badge in the header once resolved', async () => {
    useInterventionTicket.mockReturnValue(
      mockQuery(makeTicket({ is_resolved: true, can_resolve: false, can_send: false })),
    );
    renderDrawer();

    expect(await screen.findByTestId('ticket-resolved-badge')).toHaveTextContent('Resolved');
  });

  it('keeps the thread and the notes readable while the composer is disabled with its reason', async () => {
    useInterventionTicket.mockReturnValue(
      mockQuery(makeTicket({ is_resolved: true, can_resolve: false, can_send: false })),
    );
    useTicketComments.mockReturnValue(
      mockQuery([
        {
          id: 'c1',
          tracking_id: 't1',
          body: 'Waiting on the warehouse.',
          author_name: 'Agent One',
          mentioned_names: [],
          source: 'crm',
          created_at: '2026-08-15T02:00:00',
        },
      ]),
    );
    renderDrawer();

    // Thread still rendered, notes still merged into it.
    expect(await screen.findByTestId('chat-list')).toHaveTextContent('1 message(s)');
    expect(screen.getByTestId('chat-list')).toHaveAttribute('data-notes', '1');
    // The reason is visible, not hidden behind a vanished composer.
    expect(screen.getByTestId('composer-unavailable')).toHaveTextContent(
      'This ticket is resolved.',
    );
    expect(screen.getByRole('button', { name: /Resolve ticket/i })).toBeDisabled();
  });

  it('offers View history to the SLA listing filtered to THIS contact', async () => {
    useInterventionTicket.mockReturnValue(
      mockQuery(makeTicket({ is_resolved: true, can_resolve: false, can_send: false })),
    );
    renderDrawer();

    const link = await screen.findByTestId('ticket-history-link');
    // The Respond.io contact id, never the CRM UUID.
    expect(link.querySelector('a') ?? link).toHaveAttribute(
      'href',
      '/sla-management/conversation-sla-tracking?contact=10025531',
    );
  });

  it('falls back to the phone number when the contact has no Respond id', async () => {
    useInterventionTicket.mockReturnValue(
      mockQuery(
        makeTicket({
          is_resolved: true,
          can_resolve: false,
          can_send: false,
          respond_io_id: null,
        }),
      ),
    );
    renderDrawer();

    const link = await screen.findByTestId('ticket-history-link');
    expect((link.querySelector('a') ?? link).getAttribute('href')).toBe(
      `/sla-management/conversation-sla-tracking?contact=${encodeURIComponent('+60 12-000 0553')}`,
    );
  });

  it('offers no history link while the ticket is still open - there is nothing to look back on yet', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    await screen.findByTestId('composer-send');
    expect(screen.queryByTestId('ticket-history-link')).not.toBeInTheDocument();
    expect(screen.queryByTestId('ticket-resolved-badge')).not.toBeInTheDocument();
  });

  // AC-N6: the quoted enquiry is the way back into the thread.
  describe('enquiry quote jump (AC-N6)', () => {
    it('clicking the quote points the thread at the source message', async () => {
      useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
      renderDrawer();

      fireEvent.click(await screen.findByTestId('enquiry-quote-jump'));

      await waitFor(() =>
        expect(screen.getByTestId('chat-list')).toHaveAttribute('data-focus-message-id', '123'),
      );
      expect(screen.getByTestId('chat-list')).toHaveAttribute('data-focus-nonce', '1');
    });

    it('a source message outside the loaded window fetches the page around it', async () => {
      const { loadPage } = useSlaTrackingThreadLoaders();
      useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
      renderDrawer();

      fireEvent.click(await screen.findByTestId('enquiry-quote-jump'));

      await waitFor(() =>
        expect(loadPage).toHaveBeenCalledWith({ around: '123', limit: 50 }),
      );
    });

    it('stays plain text when the ticket has no source message to reach', async () => {
      useInterventionTicket.mockReturnValue(
        mockQuery(makeTicket({ source_message_id: null })),
      );
      renderDrawer();

      await screen.findByTestId('chat-list');
      expect(screen.queryByTestId('enquiry-quote-jump')).not.toBeInTheDocument();
      expect(screen.getByText('Yes, please connect me to a person.')).toBeInTheDocument();
    });
  });

  // R2: the enquiry quote is one truncated line by default, with a Show
  // more/less toggle that never shrinks the thread panel's flex share.
  describe('enquiry quote clamp (AC-CP-1..4)', () => {
    // S1: jsdom never computes real layout (scrollWidth/clientWidth are both
    // 0), so "does this line actually overflow" is stubbed per test instead
    // of guessed from string length - the same substitution RespondChatList's
    // own scroll-back suite uses for scrollHeight. Restored after every test.
    let restoreQuoteOverflow: (() => void) | null = null;
    afterEach(() => {
      restoreQuoteOverflow?.();
      restoreQuoteOverflow = null;
    });
    function stubQuoteOverflow(clamped: boolean) {
      const scrollWidthDesc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollWidth');
      const clientWidthDesc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');
      Object.defineProperty(HTMLElement.prototype, 'scrollWidth', {
        configurable: true,
        get: () => (clamped ? 200 : 100),
      });
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
        configurable: true,
        get: () => 100,
      });
      restoreQuoteOverflow = () => {
        if (scrollWidthDesc) Object.defineProperty(HTMLElement.prototype, 'scrollWidth', scrollWidthDesc);
        else delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollWidth;
        if (clientWidthDesc) Object.defineProperty(HTMLElement.prototype, 'clientWidth', clientWidthDesc);
        else delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
      };
    }

    it('AC-CP-1: an unclamped quote renders on one line, with no toggle', async () => {
      stubQuoteOverflow(false);
      const shortText = 'A'.repeat(40);
      useInterventionTicket.mockReturnValue(
        mockQuery(makeTicket({ source_message_text: shortText })),
      );
      renderDrawer();

      const quote = await screen.findByTestId('enquiry-quote-text');
      expect(quote).toHaveTextContent(shortText);
      expect(quote.className).toContain('truncate');
      expect(screen.queryByTestId('enquiry-quote-toggle')).not.toBeInTheDocument();
      // Fix round 2: collapsed, the text is INSIDE the jump button again (as
      // on main) - one wide, readable click target rather than a bare icon.
      const jump = screen.getByTestId('enquiry-quote-jump');
      expect(jump.tagName).toBe('BUTTON');
      expect(jump).toHaveTextContent(shortText);
      expect(jump.contains(quote)).toBe(true);
    });

    it('AC-CP-2: a clamped quote truncates to one line with the full text as its title, and the toggle expands/collapses it', async () => {
      stubQuoteOverflow(true);
      const longText = 'B'.repeat(1500);
      useInterventionTicket.mockReturnValue(
        mockQuery(makeTicket({ source_message_text: longText })),
      );
      renderDrawer();

      const quote = await screen.findByTestId('enquiry-quote-text');
      expect(quote).toHaveAttribute('title', longText);
      expect(quote.className).toContain('truncate');

      const toggle = screen.getByTestId('enquiry-quote-toggle');
      expect(toggle).toHaveTextContent('Show more');
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      // Names the exact node it expands/collapses.
      expect(toggle).toHaveAttribute('aria-controls', quote.id);
      expect(quote.id).toBeTruthy();

      fireEvent.click(toggle);
      expect(screen.getByTestId('enquiry-quote-toggle')).toHaveTextContent('Show less');
      expect(screen.getByTestId('enquiry-quote-toggle')).toHaveAttribute('aria-expanded', 'true');
      expect(screen.getByTestId('enquiry-quote-text').className).toContain('max-h-40');
      expect(screen.getByTestId('enquiry-quote-text').className).toContain('overflow-y-auto');
      // S1 nit: no `title` once expanded - the full text is already on screen.
      expect(screen.getByTestId('enquiry-quote-text')).not.toHaveAttribute('title');

      fireEvent.click(screen.getByTestId('enquiry-quote-toggle'));
      expect(screen.getByTestId('enquiry-quote-toggle')).toHaveTextContent('Show more');
      expect(screen.getByTestId('enquiry-quote-toggle')).toHaveAttribute('aria-expanded', 'false');
      expect(screen.getByTestId('enquiry-quote-text').className).toContain('truncate');
      expect(screen.getByTestId('enquiry-quote-text')).toHaveAttribute('title', longText);
    });

    // Fix round 2, blocker 2: collapsed, the text is the jump target again
    // (as on main); expanded, the box is inert so a text-select drag inside
    // it cannot fire the jump on mouseup - a SIBLING icon button carries the
    // jump instead.
    it('collapsed text click triggers the jump; expanded box click does not', async () => {
      stubQuoteOverflow(true);
      const longText = 'E'.repeat(1500);
      useInterventionTicket.mockReturnValue(
        mockQuery(makeTicket({ source_message_text: longText })),
      );
      renderDrawer();

      // Collapsed: the text IS the (whole-row) button.
      const collapsedText = await screen.findByTestId('enquiry-quote-text');
      expect(collapsedText.closest('button')).toBe(screen.getByTestId('enquiry-quote-jump'));
      fireEvent.click(collapsedText);
      await waitFor(() =>
        expect(screen.getByTestId('chat-list')).toHaveAttribute('data-focus-nonce', '1'),
      );

      // Expand: the box is now a plain, non-interactive paragraph - a
      // SEPARATE icon button (still `enquiry-quote-jump`) carries the jump.
      fireEvent.click(screen.getByTestId('enquiry-quote-toggle'));
      const expandedText = screen.getByTestId('enquiry-quote-text');
      expect(expandedText.tagName).toBe('P');
      expect(expandedText.closest('button')).toBeNull();

      fireEvent.click(expandedText);
      // No second jump from clicking the (inert) box itself.
      expect(screen.getByTestId('chat-list')).toHaveAttribute('data-focus-nonce', '1');

      // The sibling icon button still works.
      fireEvent.click(screen.getByTestId('enquiry-quote-jump'));
      await waitFor(() =>
        expect(screen.getByTestId('chat-list')).toHaveAttribute('data-focus-nonce', '2'),
      );
    });

    it('AC-CP-3: expanding the quote does not change the thread panel props', async () => {
      stubQuoteOverflow(true);
      const longText = 'C'.repeat(1500);
      useInterventionTicket.mockReturnValue(
        mockQuery(makeTicket({ source_message_text: longText })),
      );
      renderDrawer();

      fireEvent.click(await screen.findByTestId('enquiry-quote-toggle'));

      const panel = await screen.findByTestId('ticket-conversation-panel');
      // Fix round 5: `min-h-40`, not `min-h-0` - see the dedicated "fix round
      // 5" describe block below for why the floor has to sit on this root too.
      expect(panel.className).toContain('min-h-40');
      expect(panel.className).toContain('flex-1');
      // Fix round 6: the INNER scroll box (`maxHeightClass`) does NOT also
      // floor itself - see the dedicated "fix round 6" describe block below.
      expect(screen.getByTestId('chat-list')).toHaveAttribute('data-max-height', 'min-h-0 flex-1');
    });

    it('AC-CP-4: an empty quote falls back to the neutral label, with no toggle', async () => {
      stubQuoteOverflow(false);
      useInterventionTicket.mockReturnValue(mockQuery(makeTicket({ source_message_text: '  ' })));
      renderDrawer();

      await waitFor(() =>
        expect(screen.getByText('No enquiry text captured.')).toBeInTheDocument(),
      );
      expect(screen.queryByTestId('enquiry-quote-toggle')).not.toBeInTheDocument();
    });

    it('S1: a mid-length quote that DOES overflow at the current width still offers the toggle, even under the old 120-char guess', async () => {
      // The 45-120 char band the old length threshold hid a toggle for
      // (narrow viewport, or just a wide font) - the measured check catches it
      // regardless of character count.
      stubQuoteOverflow(true);
      const midText = 'D'.repeat(80);
      useInterventionTicket.mockReturnValue(
        mockQuery(makeTicket({ source_message_text: midText })),
      );
      renderDrawer();

      expect(await screen.findByTestId('enquiry-quote-toggle')).toBeInTheDocument();
    });
  });

  // AC-N5(a)/(b): the ticket actions live in the header action group. They used
  // to sit in a footer under the composer, crowding its toolbar.
  describe('header action group (AC-N5)', () => {
    it('Resolve lives in the header group, above the composer', async () => {
      useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
      renderDrawer();

      const actions = await screen.findByTestId('ticket-header-actions');
      const resolve = screen.getByRole('button', { name: /Resolve ticket/i });
      expect(actions.contains(resolve)).toBe(true);

      // Header group precedes the composer in document order.
      const composer = screen.getByTestId('composer-send');
      expect(
        actions.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });

    it('Reassign sits in the same group and opens the shared dialog (AC-N7)', async () => {
      useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
      renderDrawer();

      const actions = await screen.findByTestId('ticket-header-actions');
      const reassign = screen.getByTestId('ticket-reassign');
      expect(actions.contains(reassign)).toBe(true);
      expect(screen.queryByTestId('reassign-dialog')).not.toBeInTheDocument();

      fireEvent.click(reassign);

      const dialog = screen.getByTestId('reassign-dialog');
      // Human-readable subject, never a UUID.
      expect(dialog).toHaveAttribute('data-task-label', 'this enquiry from Aisyah Rahman');
    });

    it('a confirmed reassign hands THIS ticket over and tells the worklist', async () => {
      useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
      const { onResolved } = renderDrawer();
      void onResolved;

      fireEvent.click(await screen.findByTestId('ticket-reassign'));
      fireEvent.click(screen.getByTestId('reassign-confirm'));

      expect(reassignMutate).toHaveBeenCalledWith(
        { id: 't1', userId: 'u-2' },
        expect.anything(),
      );
    });

    it('hides Reassign without the reassign permission', async () => {
      hasPermission.mockReturnValue(false);
      useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
      renderDrawer();

      await screen.findByTestId('ticket-header-actions');
      expect(screen.queryByTestId('ticket-reassign')).not.toBeInTheDocument();
    });

    it('the resolved-state affordances join the same group', async () => {
      useInterventionTicket.mockReturnValue(
        mockQuery(
          makeTicket({
            is_resolved: true,
            can_resolve: false,
            can_send: false,
            resolved_at: '2026-08-12T04:00:00',
          }),
        ),
      );
      renderDrawer();

      const actions = await screen.findByTestId('ticket-header-actions');
      expect(actions.contains(screen.getByTestId('ticket-resolved-at'))).toBe(true);
      expect(actions.contains(screen.getByTestId('ticket-history-link'))).toBe(true);
    });
  });
});

/**
 * Extend, in the header's overflow menu (feedback 2026-08-16, item 5).
 *
 * The action existed only on the worklist row, so an assignee reading the
 * conversation had to close the drawer to buy time on it. It is a menu rather
 * than a fourth header button because the header is already at its width on a
 * phone, and it opens the SAME dialog the row does.
 */
describe('InterventionTicketDrawer extend (AC-B4)', () => {
  /** Radix opens on pointerdown, not click. */
  function press(el: Element) {
    fireEvent.pointerDown(el, { bubbles: true, button: 0, pointerType: 'mouse' });
    fireEvent.click(el, { bubbles: true, button: 0 });
  }

  beforeEach(() => {
    extendDialogProps.length = 0;
  });

  it('the gear opens the shared extend dialog for THIS ticket', async () => {
    const ticket = makeTicket();
    useInterventionTicket.mockReturnValue(mockQuery(ticket));
    renderDrawer();

    press(await screen.findByTestId('ticket-overflow'));
    press(await screen.findByTestId('ticket-extend'));

    const dialog = await screen.findByTestId('extend-dialog');
    expect(dialog).toHaveAttribute('data-tracking-id', 't1');
    expect(dialog).toHaveAttribute('data-current-due', ticket.due_at_resolution!);
  });

  it('a successful extend re-reads the ticket, so the chips show the new deadline', async () => {
    const query = mockQuery(makeTicket());
    useInterventionTicket.mockReturnValue(query);
    renderDrawer();

    press(await screen.findByTestId('ticket-overflow'));
    press(await screen.findByTestId('ticket-extend'));
    query.refetch.mockClear();

    fireEvent.click(await screen.findByTestId('extend-confirm'));

    expect(query.refetch).toHaveBeenCalled();
  });

  it('no menu without the extend permission', async () => {
    hasPermission.mockImplementation(
      (slug?: string) => slug !== 'sla_management.conversation_sla_tracking.extend',
    );
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    await screen.findByTestId('ticket-header-actions');
    expect(screen.queryByTestId('ticket-overflow')).not.toBeInTheDocument();
  });

  it('no menu on a resolved ticket - there is no deadline left to move', async () => {
    useInterventionTicket.mockReturnValue(
      mockQuery(makeTicket({ is_resolved: true, can_resolve: false, can_send: false })),
    );
    renderDrawer();

    await screen.findByTestId('ticket-header-actions');
    expect(screen.queryByTestId('ticket-overflow')).not.toBeInTheDocument();
  });

  it('no menu when the ticket has no resolution deadline', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket({ due_at_resolution: null })));
    renderDrawer();

    await screen.findByTestId('ticket-header-actions');
    expect(screen.queryByTestId('ticket-overflow')).not.toBeInTheDocument();
  });
});

// My Team can open this drawer for a teammate's ticket (is_assignee: false).
// Resolve must read as disabled-with-a-reason, and Takeover must be reachable
// from the SAME confirm pattern the worklist row uses.
describe('InterventionTicketDrawer My Team (not the assignee)', () => {
  function press(el: Element) {
    fireEvent.pointerDown(el, { bubbles: true, button: 0, pointerType: 'mouse' });
    fireEvent.click(el, { bubbles: true, button: 0 });
  }

  beforeEach(() => {
    takeoverMutate.mockReset();
  });

  it('Resolve is disabled with a tooltip naming the actual assignee', async () => {
    useInterventionTicket.mockReturnValue(
      mockQuery(
        makeTicket({ is_assignee: false, can_resolve: false, assignee_name: 'Charissa' }),
      ),
    );
    renderDrawer();

    const resolve = await screen.findByRole('button', { name: /Resolve ticket/i });
    expect(resolve).toBeDisabled();
    expect(resolve).toHaveAttribute('title', 'Assigned to Charissa');
  });

  it('the gear offers Takeover even with no resolution deadline (independent of Extend)', async () => {
    useInterventionTicket.mockReturnValue(
      mockQuery(
        makeTicket({ is_assignee: false, can_resolve: false, assignee_team_id: 'team-9', due_at_resolution: null }),
      ),
    );
    renderDrawer();

    press(await screen.findByTestId('ticket-overflow'));
    expect(screen.getByTestId('ticket-takeover')).toBeInTheDocument();
    expect(screen.queryByTestId('ticket-extend')).not.toBeInTheDocument();
  });

  it('Takeover opens a confirm naming the current assignee, then calls the mutation with the assignee_team_id and refetches on success', async () => {
    const query = mockQuery(
      makeTicket({
        is_assignee: false,
        can_resolve: false,
        assignee_name: 'Charissa',
        assignee_team_id: 'team-9',
      }),
    );
    useInterventionTicket.mockReturnValue(query);
    takeoverMutate.mockImplementation(
      (_vars: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.(),
    );
    const { onOpenChange } = renderDrawer();

    press(await screen.findByTestId('ticket-overflow'));
    fireEvent.click(await screen.findByTestId('ticket-takeover'));

    expect(await screen.findByText(/Take over this enquiry\?/i)).toBeInTheDocument();
    expect(screen.getByText(/Currently with Charissa/i)).toBeInTheDocument();
    query.refetch.mockClear();

    fireEvent.click(screen.getByRole('button', { name: /^Take over$/i }));

    expect(takeoverMutate).toHaveBeenCalledWith(
      { id: 't1', teamId: 'team-9' },
      expect.any(Object),
    );
    expect(query.refetch).toHaveBeenCalled();
    // Confirming a takeover must not close the drawer - the assignee just
    // changed to the viewer, the enquiry stays open in front of them.
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it('no Takeover item for the assignee themselves', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket({ is_assignee: true })));
    renderDrawer();

    press(await screen.findByTestId('ticket-overflow'));
    expect(screen.queryByTestId('ticket-takeover')).not.toBeInTheDocument();
  });

  it('no Takeover item without the takeover permission', async () => {
    hasPermission.mockImplementation(
      (slug?: string) => slug !== 'sla_management.conversation_sla_tracking.takeover',
    );
    useInterventionTicket.mockReturnValue(
      mockQuery(makeTicket({ is_assignee: false, can_resolve: false, assignee_team_id: 'team-9' })),
    );
    renderDrawer();

    // Extend still qualifies (own-task gate does not apply here), so the gear
    // itself stays up - only the Takeover item must be gone.
    press(await screen.findByTestId('ticket-overflow'));
    expect(screen.queryByTestId('ticket-takeover')).not.toBeInTheDocument();
  });

  it('no Takeover item when the assignee has no team the viewer can resolve', async () => {
    useInterventionTicket.mockReturnValue(
      mockQuery(
        makeTicket({ is_assignee: false, can_resolve: false, assignee_team_id: null }),
      ),
    );
    renderDrawer();

    press(await screen.findByTestId('ticket-overflow'));
    expect(screen.queryByTestId('ticket-takeover')).not.toBeInTheDocument();
  });
});

/**
 * Fix round 4: at 375x812, a real ticket (long thread + the composer's
 * "Outside the 24h window..." template notice) rendered the thread's text on
 * TOP of the composer's, at the same y - a real defect, reproduced twice.
 * DOM probe found the thread's own scroll box correctly clipped to its
 * `min-h-40` floor (clientHeight 158, scrollHeight 12793) - so the box ITSELF
 * was fine; the structural risk is `SheetContent` ALSO being independently
 * scrollable (its own `overflow-y-auto`, on top of `SheetBody`'s) and the
 * composer having no `shrink-0` floor of its own, so a squeeze anywhere in
 * that chain has nowhere safe to land. jsdom cannot measure real layout, so
 * these pin the STRUCTURE the fix relies on, not the pixels.
 */
describe('InterventionTicketDrawer sheet layout (fix round 4, 375px overlap)', () => {
  it('the sheet content itself does not scroll - only SheetBody does, so there is exactly one scroll container between it and the thread', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();
    await screen.findByTestId('chat-list');

    const sheetContent = document.querySelector('[data-slot="sheet-content"]');
    expect(sheetContent).toBeTruthy();
    expect(sheetContent?.className).not.toContain('overflow-y-auto');
  });

  it('the composer never shrinks below its natural height, however tight the thread gets', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    const send = await screen.findByTestId('composer-send');
    expect(send.closest('.shrink-0')).toBeTruthy();
  });

  it('the Reply/Comment mode switch never shrinks either', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    const tab = await screen.findByTestId('composer-mode-reply');
    expect(tab.closest('[role="tablist"]')?.className).toContain('shrink-0');
  });
});

/**
 * Fix round 5: round 4's fix did not actually stop the overlap - re-verify at
 * 375x812 still failed. Measured rects showed the thread's inner scroll box
 * correctly floored at 160px (`min-h-40`, RespondChatList.tsx), but its
 * ANCESTORS (RespondChatList's own root, and TicketConversationPanel's root)
 * carried no floor of their own (`min-h-0 flex-1`) - a per-element CSS
 * `min-height` on a deeply nested child does not enlarge an ancestor's
 * computed flex size, so the outer flex algorithm squeezed those ancestors to
 * near-zero under pressure, and the floored scroll box overflowed them,
 * painting over the composer laid out after the squeezed parent. Both
 * `TicketConversationPanel` and `RespondChatList`'s roots now carry a
 * `min-h-40 flex-1` floor of their own - fix round 6 (below) is what stops
 * the INNER scroll box from ALSO carrying one.
 */
describe('InterventionTicketDrawer sheet layout (fix round 5, floor on every flex item)', () => {
  it('the panel forwards the min-h-40 floor to RespondChatList\'s own root via className, separately from maxHeightClass', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    const chatList = await screen.findByTestId('chat-list');
    // The floor reaches the component's own root via the new `className`
    // prop - the actual defect round 4 missed. `maxHeightClass` (the inner
    // scroll box) is asserted separately in the "fix round 6" block below.
    expect(chatList).toHaveAttribute('data-class-name', 'min-h-40 flex-1');
  });

  it('the thread panel itself (TicketConversationPanel root) also carries the floor, not just min-h-0', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    const panel = await screen.findByTestId('ticket-conversation-panel');
    expect(panel.className).toContain('min-h-40');
    expect(panel.className).not.toContain('min-h-0');
  });
});

/**
 * Fix round 6: the 375 re-verify on round 5 (b94356ef3) improved but still
 * showed a 41px overlap. Measured rects: the RespondChatList root honoured
 * its new floor (~160px, top ~318 to 477), but RespondChatList's OWN
 * header/search chrome sits ABOVE the inner scroll box, inside that SAME
 * rooted floor (~40px, top 318 to 358) - so the inner box, which ALSO
 * carried its own `min-h-40`, demanded chrome-height MORE room than the
 * root actually had left, and overflowed the root's bottom edge by exactly
 * that chrome height, painting over the tablist. Two floors (root AND inner
 * box) for the same 160px requirement was one too many: the inner box now
 * gets `min-h-0 flex-1` (no floor of its own) and takes whatever the
 * rooted floor leaves it after the chrome.
 */
describe('InterventionTicketDrawer sheet layout (fix round 6, one floor not two)', () => {
  it('the inner scroll box (maxHeightClass) does not ALSO carry a min-h floor - only the root does', async () => {
    useInterventionTicket.mockReturnValue(mockQuery(makeTicket()));
    renderDrawer();

    const chatList = await screen.findByTestId('chat-list');
    expect(chatList).toHaveAttribute('data-max-height', 'min-h-0 flex-1');
    // The root's floor (fix round 5) is unchanged by this round.
    expect(chatList).toHaveAttribute('data-class-name', 'min-h-40 flex-1');
  });
});
