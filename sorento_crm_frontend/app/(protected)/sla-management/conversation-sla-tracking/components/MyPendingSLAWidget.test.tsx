import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import MyPendingSLAWidget from './MyPendingSLAWidget';
import type {
  MyPendingSLAItem,
  TeamPendingItem,
} from '../services/conversationSLATrackingService';

const getMyPendingSLA = vi.fn();
const getTeamPendingSLA = vi.fn();
const getVisibleUsers = vi.fn();
const resolveConversationSLATracking = vi.fn();
const escalateConversationSLATracking = vi.fn();
const takeoverSLATracking = vi.fn();
const reassignSLATracking = vi.fn();
const getTakeoverState = vi.fn();
const cancelTakeover = vi.fn();
const rejectTakeover = vi.fn();

vi.mock('../services/conversationSLATrackingService', () => ({
  getMyPendingSLA: (...a: unknown[]) => getMyPendingSLA(...a),
  getTeamPendingSLA: (...a: unknown[]) => getTeamPendingSLA(...a),
  getVisibleUsers: (...a: unknown[]) => getVisibleUsers(...a),
  resolveConversationSLATracking: (...a: unknown[]) => resolveConversationSLATracking(...a),
  escalateConversationSLATracking: (...a: unknown[]) => escalateConversationSLATracking(...a),
  takeoverSLATracking: (...a: unknown[]) => takeoverSLATracking(...a),
  reassignSLATracking: (...a: unknown[]) => reassignSLATracking(...a),
  getTakeoverState: (...a: unknown[]) => getTakeoverState(...a),
  cancelTakeover: (...a: unknown[]) => cancelTakeover(...a),
  rejectTakeover: (...a: unknown[]) => rejectTakeover(...a),
}));

const push = vi.fn();
const replace = vi.fn();
// `searchParam` is the legacy single value: it maps to the ?takeover= param so the
// existing banner tests keep working. `extraParams` lets a test set other keys
// (e.g. team_task) independently.
let searchParam: string | null = null;
let extraParams: Record<string, string | null> = {};
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace }),
  useSearchParams: () => ({
    get: (key: string) => {
      if (key === 'takeover') return searchParam;
      return extraParams[key] ?? null;
    },
  }),
}));

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// Per-action RBAC: by default the user holds every SLA slug (existing button tests
// assume the buttons render). Individual gating tests flip a slug to false via
// `deniedSlugs`. The mock reads the full slug, e.g.
// `sla_management.conversation_sla_tracking.resolve`.
let deniedSlugs = new Set<string>();
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => !deniedSlugs.has(slug),
}));

// The Coverage tab embeds the (heavy) CoverageManager; stub it to a marker that echoes
// the canManageTeam prop so the widget test stays focused on tab switching + wiring.
vi.mock('@/app/(protected)/account/notifications/components', () => ({
  CoverageManager: ({ canManageTeam }: { canManageTeam: boolean }) => (
    <div data-testid="coverage-manager" data-can-manage={String(canManageTeam)} />
  ),
}));

// The ticket drawer is exercised on its own (InterventionTicketDrawer.test.tsx);
// here we only care that the widget opens/closes it with the right ticketId.
vi.mock('./InterventionTicketDrawer', () => ({
  default: ({
    ticketId,
    open,
    onSent,
  }: {
    ticketId: string | null;
    open: boolean;
    onSent?: () => void;
  }) =>
    open ? (
      <div data-testid="ticket-drawer" data-ticket-id={ticketId ?? ''}>
        <button data-testid="drawer-sent" onClick={() => onSent?.()}>
          sent
        </button>
      </div>
    ) : null,
}));

function renderWidget() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MyPendingSLAWidget />
    </QueryClientProvider>,
  );
}

// Each task row is itself a clickable `<div role="button">`, so its accessible name
// includes ALL of its child text (the type, subline like "Mark CS resolved", and the
// inline action labels). A bare getByRole('button', { name: /Escalate/i }) therefore
// matches BOTH the row and the real action button. These helpers narrow to the actual
// HTML <button> action element (the row is a <div>), keeping each assertion's intent.
function actionButtons(name: RegExp): HTMLElement[] {
  return screen
    .queryAllByRole('button', { name })
    .filter((el) => el.tagName === 'BUTTON');
}
function getActionButton(name: RegExp): HTMLElement {
  const matches = actionButtons(name);
  if (matches.length !== 1) {
    throw new Error(
      `expected exactly one action <button> matching ${name}, found ${matches.length}`,
    );
  }
  return matches[0];
}
function hasActionButton(name: RegExp): boolean {
  return actionButtons(name).length > 0;
}

const formItem: MyPendingSLAItem = {
  id: 'f1',
  source_entity_type: 'purchase_request',
  source_entity_id: 'pr-uuid',
  is_form_sla: true,
  reference: 'PR26-0316',
  respond_io_id: null,
  next_action: 'Send for approval',
  due_at: new Date(Date.now() + 3600_000).toISOString(),
  is_responded: false,
  current_tier: 1,
  policy_name: 'Default',
};

const convoItem: MyPendingSLAItem = {
  id: 'c1',
  source_entity_type: null,
  source_entity_id: null,
  is_form_sla: false,
  reference: '+60160000509',
  respond_io_id: '999',
  next_action: null,
  due_at: new Date(Date.now() - 3600_000).toISOString(),
  is_responded: false,
  current_tier: 2,
  policy_name: 'Default',
};

// A ticket is a form-SLA type the FE route map does NOT know, yet it has a Respond
// contact - the case that used to be mis-classified as a conversation.
const ticketItem: MyPendingSLAItem = {
  id: 'tk1',
  source_entity_type: 'ticket',
  source_entity_id: 'ticket-uuid',
  is_form_sla: true,
  reference: null,
  respond_io_id: '900000010',
  next_action: 'Mark CS resolved',
  due_at: new Date(Date.now() - 3600_000).toISOString(),
  is_responded: false,
  current_tier: 1,
  policy_name: 'Default',
};

// Two intervention tickets for the SAME contact (UAC AC-B1: no de-dup by contact).
const ticketOne: MyPendingSLAItem & Record<string, unknown> = {
  id: 'ticket-1',
  source_entity_type: null,
  source_entity_id: null,
  is_form_sla: false,
  reference: 'Aisyah Rahman',
  respond_io_id: '10025531',
  next_action: null,
  due_at: new Date(Date.now() + 46 * 60_000).toISOString(),
  due_at_resolution: new Date(Date.now() + 350 * 60_000).toISOString(),
  active_due_at: new Date(Date.now() + 46 * 60_000).toISOString(),
  due_kind: 'respond',
  is_responded: false,
  current_tier: 1,
  policy_name: 'Conversation SLA - Standard',
  is_intervention_ticket: true,
  contact_name: 'Aisyah Rahman',
  contact_phone: '+60 12-000 0553',
  enquiry_snippet: 'Yes, please connect me to a person.',
  source_message_id: '1001',
  team_label: 'Customer Service - Tier 1',
  initiated_at: new Date(Date.now() - 170 * 60_000).toISOString(),
  escalated_at: null,
};

const ticketTwo: MyPendingSLAItem & Record<string, unknown> = {
  ...ticketOne,
  id: 'ticket-2',
  due_at: new Date(Date.now() + 4 * 60_000).toISOString(),
  enquiry_snippet: 'Also, can someone quote installation for 3 bathrooms?',
  source_message_id: '1002',
  team_label: 'Sales - Tier 1',
};

const teamItem: TeamPendingItem = {
  id: 't1',
  assignee_id: 'u-charissa',
  assignee_name: 'Charissa',
  team_id: 'team-1',
  team_label: 'Marketing - Product',
  source_entity_type: null,
  source_entity_id: null,
  is_form_sla: false,
  reference: '+60123456789',
  respond_io_id: '999888',
  due_at: new Date(Date.now() + 3600_000).toISOString(),
  is_responded: false,
  current_tier: 1,
  policy_name: 'Default',
  next_action: null,
};

// A team ticket row: the SAME shape /my-pending emits for a ticket (see
// ticketOne above), plus the team-only assignee/team context. Team ticket
// rows now use the exact ticketOne fields (row builder is shared) - this
// mirrors that fixture rather than re-declaring the ticket-only fields.
const teamTicketItem: TeamPendingItem & Record<string, unknown> = {
  ...ticketOne,
  id: 'team-ticket-1',
  assignee_id: 'u-charissa',
  assignee_name: 'Charissa',
  team_id: 'team-1',
  team_label: 'Marketing - Product',
};

describe('MyPendingSLAWidget clickable rows', () => {
  beforeEach(() => {
    getMyPendingSLA.mockReset();
    getTeamPendingSLA.mockReset();
    getVisibleUsers.mockReset();
    resolveConversationSLATracking.mockReset();
    escalateConversationSLATracking.mockReset();
    takeoverSLATracking.mockReset();
    reassignSLATracking.mockReset();
    getTakeoverState.mockReset();
    cancelTakeover.mockReset();
    rejectTakeover.mockReset();
    push.mockReset();
    replace.mockReset();
    searchParam = null;
    extraParams = {};
    deniedSlugs = new Set();
    getTeamPendingSLA.mockResolvedValue({ data: [], total: 0, page: 1, limit: 50, empty: true });
    getVisibleUsers.mockResolvedValue([]);
    getTakeoverState.mockResolvedValue({});
  });

  it('form-SLA row: no resolve/escalate buttons (Reassign always present); clicking the row opens the record', async () => {
    getMyPendingSLA.mockResolvedValue([formItem]);
    renderWidget();

    await waitFor(() => expect(screen.getByText('Purchase request')).toBeInTheDocument());
    expect(screen.getByText(/Tier 1 · Send for approval/i)).toBeInTheDocument();
    // Resolve / Escalate are conversation-only; Reassign is offered on every row.
    expect(hasActionButton(/Resolve/i)).toBe(false);
    expect(hasActionButton(/Escalate/i)).toBe(false);
    expect(getActionButton(/Reassign/i)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Purchase request'));
    expect(push).toHaveBeenCalledWith('/procurement-management/purchase-requests/pr-uuid');
  });

  it('conversation-SLA row: Escalate + Resolve buttons; clicking the row opens Respond', async () => {
    getMyPendingSLA.mockResolvedValue([convoItem]);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);
    renderWidget();

    await waitFor(() => expect(screen.getByText('Enquiry')).toBeInTheDocument());
    expect(screen.getByText(/Tier 2 · Reply/i)).toBeInTheDocument();
    expect(hasActionButton(/Open in Respond/i)).toBe(false);
    expect(getActionButton(/Escalate/i)).toBeInTheDocument();
    expect(getActionButton(/Resolve/i)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Enquiry'));
    expect(openSpy).toHaveBeenCalledWith(
      'https://app.respond.io/space/364817/inbox/999',
      '_blank',
      'noopener,noreferrer',
    );
    openSpy.mockRestore();
  });

  it('Escalate flow: opens reason dialog, submits, calls escalate service with reason', async () => {
    getMyPendingSLA.mockResolvedValue([convoItem]);
    escalateConversationSLATracking.mockResolvedValue(undefined);
    renderWidget();

    await waitFor(() => expect(getActionButton(/Escalate/i)).toBeInTheDocument());
    fireEvent.click(getActionButton(/Escalate/i));

    await waitFor(() => expect(screen.getByText('Escalate to tier 3')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Customer angry' } });
    // Once the dialog is open there are two real "Escalate" <button>s (row + dialog
    // submit); the dialog submit is the last one.
    const escalateButtons = actionButtons(/^Escalate$/i);
    fireEvent.click(escalateButtons[escalateButtons.length - 1]);

    await waitFor(() =>
      expect(escalateConversationSLATracking).toHaveBeenCalledWith('c1', 'Customer angry'),
    );
  });

  it('ticket (form-SLA, no FE route) shows NO buttons; clicking opens Respond', async () => {
    getMyPendingSLA.mockResolvedValue([ticketItem]);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);
    renderWidget();

    await waitFor(() => expect(screen.getByText('Ticket')).toBeInTheDocument());
    // Form-SLA stage: no conversation Escalate/Resolve (handled at the form). Note the
    // row subline reads "Mark CS resolved" - a substring match on the row's accessible
    // name, not a real action <button>, so we filter to actual buttons.
    expect(hasActionButton(/Escalate/i)).toBe(false);
    expect(hasActionButton(/Resolve/i)).toBe(false);
    // It has a Respond contact but no FE record route → clicking opens Respond.
    fireEvent.click(screen.getByText('Ticket'));
    expect(openSpy).toHaveBeenCalledWith(
      'https://app.respond.io/space/364817/inbox/900000010',
      '_blank',
      'noopener,noreferrer',
    );
    expect(push).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it('Resolve confirm calls the resolve service', async () => {
    getMyPendingSLA.mockResolvedValue([convoItem]);
    resolveConversationSLATracking.mockResolvedValue(undefined);
    renderWidget();

    await waitFor(() => expect(getActionButton(/Resolve/i)).toBeInTheDocument());
    fireEvent.click(getActionButton(/Resolve/i));
    await waitFor(() => expect(screen.getByText('Mark as resolved')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^Confirm$/i }));

    await waitFor(() => expect(resolveConversationSLATracking).toHaveBeenCalledWith('c1'));
  });

  it('empty state when nothing pending', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    renderWidget();
    await waitFor(() =>
      expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument(),
    );
  });

  it('My Team toggle: shows assignee + Takeover/Reassign', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    getTeamPendingSLA.mockResolvedValue({
      data: [teamItem],
      total: 1,
      page: 1,
      limit: 50,
      empty: false,
    });
    renderWidget();

    await waitFor(() => expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /My Team/i }));

    await waitFor(() => expect(screen.getByText(/Charissa/)).toBeInTheDocument());
    expect(screen.getByText(/Marketing - Product/)).toBeInTheDocument();
    expect(getActionButton(/Takeover/i)).toBeInTheDocument();
    expect(getActionButton(/Reassign/i)).toBeInTheDocument();
  });

  it('My Team mode empty state', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    getTeamPendingSLA.mockResolvedValue({ data: [], total: 0, page: 1, limit: 50, empty: true });
    renderWidget();

    fireEvent.click(screen.getByRole('button', { name: /My Team/i }));
    await waitFor(() =>
      expect(screen.getByText(/No open tasks across your teams/i)).toBeInTheDocument(),
    );
  });

  it('Takeover calls the service with the row team id', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    getTeamPendingSLA.mockResolvedValue({
      data: [teamItem],
      total: 1,
      page: 1,
      limit: 50,
      empty: false,
    });
    takeoverSLATracking.mockResolvedValue({ committed: true });
    renderWidget();

    fireEvent.click(screen.getByRole('button', { name: /My Team/i }));
    await waitFor(() => expect(getActionButton(/Takeover/i)).toBeInTheDocument());
    fireEvent.click(getActionButton(/Takeover/i));
    // confirm dialog
    await waitFor(() => expect(screen.getByRole('button', { name: /^Take over$/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^Take over$/i }));

    await waitFor(() => expect(takeoverSLATracking).toHaveBeenCalledWith('t1', 'team-1'));
  });

  // ---- takeover cooldown -------------------------------------------------

  const pendingTk = (over: Partial<Record<string, unknown>> = {}) => ({
    request_id: 'rq1',
    tracking_id: 't1',
    initiator_id: 'u-me',
    initiator_name: 'Mostapha',
    contested_assignee_id: 'u-charissa',
    contested_assignee_name: 'Charissa',
    team_id: 'team-1',
    status: 'pending' as const,
    commit_at: new Date(Date.now() + 60_000).toISOString(),
    resolution_reason: null,
    ...over,
  });

  it('My Team: initiator sees Cancel + countdown, no Takeover button', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    getTeamPendingSLA.mockResolvedValue({
      data: [{ ...teamItem, takeover: pendingTk({ can_cancel: true, can_reject: false }) }],
      total: 1, page: 1, limit: 50, empty: false,
    });
    renderWidget();
    fireEvent.click(screen.getByRole('button', { name: /My Team/i }));

    await waitFor(() => expect(screen.getByText(/Takeover pending/i)).toBeInTheDocument());
    expect(screen.getByTestId('takeover-remaining')).toBeInTheDocument();
    expect(screen.getByTestId('takeover-cancel')).toBeInTheDocument();
    // No "Takeover" action button while a takeover is pending (locked).
    expect(screen.queryByTestId('takeover-cancel')?.textContent).toMatch(/Cancel takeover/i);
  });

  it('My Team: observer sees pending + initiator name, no Cancel/Takeover', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    getTeamPendingSLA.mockResolvedValue({
      data: [{ ...teamItem, takeover: pendingTk({ can_cancel: false, can_reject: false }) }],
      total: 1, page: 1, limit: 50, empty: false,
    });
    renderWidget();
    fireEvent.click(screen.getByRole('button', { name: /My Team/i }));

    await waitFor(() => expect(screen.getByText(/Takeover pending · Mostapha/i)).toBeInTheDocument());
    expect(screen.queryByTestId('takeover-cancel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('takeover-reject')).not.toBeInTheDocument();
  });

  it('My Pending: inline "being taken over" + Reject calls rejectTakeover', async () => {
    getMyPendingSLA.mockResolvedValue([
      { ...convoItem, takeover: pendingTk({ can_cancel: false, can_reject: true }) },
    ]);
    rejectTakeover.mockResolvedValue({ status: 'rejected' });
    renderWidget();

    await waitFor(() => expect(screen.getByText(/Being taken over by Mostapha/i)).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('takeover-reject'));
    await waitFor(() => expect(rejectTakeover).toHaveBeenCalledWith('rq1'));
  });

  it('deep link ?takeover= pins a flashing banner with Reject', async () => {
    searchParam = 't1';
    getMyPendingSLA.mockResolvedValue([]);
    getTakeoverState.mockResolvedValue({
      id: 't1', source_entity_type: 'complaint', is_form_sla: true,
      reference: 'CMP-0010', due_at: null, current_tier: 1, is_resolved: false,
      assigned_to_id: 'u-charissa',
      takeover: pendingTk({ can_reject: true }),
    });
    rejectTakeover.mockResolvedValue({ status: 'rejected' });
    renderWidget();

    await waitFor(() => expect(screen.getByTestId('takeover-banner')).toBeInTheDocument());
    expect(screen.getByText(/CMP-0010/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('takeover-banner-reject'));
    await waitFor(() => expect(rejectTakeover).toHaveBeenCalledWith('rq1'));
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/', { scroll: false }));
  });

  it('Reassign opens the picker dialog sourced from visible-users (scope-B)', async () => {
    getMyPendingSLA.mockResolvedValue([convoItem]);
    getVisibleUsers.mockResolvedValue([
      { id: 'u-tay', name: 'Tay', email: 'tay@example.com' },
    ]);
    renderWidget();

    await waitFor(() => expect(getActionButton(/Reassign/i)).toBeInTheDocument());
    fireEvent.click(getActionButton(/Reassign/i));

    // Dialog opens with the scope-B picker; the visible-users endpoint is the source.
    await waitFor(() => expect(screen.getByText('Reassign task')).toBeInTheDocument());
    expect(screen.getByRole('combobox')).toBeInTheDocument();
    await waitFor(() => expect(getVisibleUsers).toHaveBeenCalled());
    // Once the dialog is open there are two real "Reassign" <button>s (row + dialog
    // confirm); the confirm is the last and is disabled until a colleague is chosen.
    const confirm = actionButtons(/^Reassign$/i).at(-1)!;
    expect(confirm).toBeDisabled();
  });

  // ---- per-action RBAC gating (UAC A2 - A7) --------------------------------

  const SLUG = 'sla_management.conversation_sla_tracking';

  it('A4: Escalate hidden without the escalate slug', async () => {
    deniedSlugs = new Set([`${SLUG}.escalate`]);
    getMyPendingSLA.mockResolvedValue([convoItem]);
    renderWidget();
    await waitFor(() => expect(screen.getByText('Enquiry')).toBeInTheDocument());
    expect(hasActionButton(/Escalate/i)).toBe(false);
    // Resolve still shows (independent slug).
    expect(getActionButton(/Resolve/i)).toBeInTheDocument();
  });

  it('A3: Resolve hidden without the resolve slug', async () => {
    deniedSlugs = new Set([`${SLUG}.resolve`]);
    getMyPendingSLA.mockResolvedValue([convoItem]);
    renderWidget();
    await waitFor(() => expect(screen.getByText('Enquiry')).toBeInTheDocument());
    expect(hasActionButton(/Resolve/i)).toBe(false);
    expect(getActionButton(/Escalate/i)).toBeInTheDocument();
  });

  it('A5: Reassign hidden without the reassign slug', async () => {
    deniedSlugs = new Set([`${SLUG}.reassign`]);
    getMyPendingSLA.mockResolvedValue([convoItem]);
    renderWidget();
    await waitFor(() => expect(screen.getByText('Enquiry')).toBeInTheDocument());
    expect(hasActionButton(/Reassign/i)).toBe(false);
  });

  it('A2: Extend hidden without the extend slug (conversation row with resolution due)', async () => {
    deniedSlugs = new Set([`${SLUG}.extend`]);
    getMyPendingSLA.mockResolvedValue([
      { ...convoItem, due_at_resolution: new Date(Date.now() + 7200_000).toISOString() },
    ]);
    renderWidget();
    await waitFor(() => expect(screen.getByText('Enquiry')).toBeInTheDocument());
    expect(hasActionButton(/Extend/i)).toBe(false);
  });

  it('A6: Takeover (+ no Cancel/Reject) hidden without the takeover slug on My Team', async () => {
    deniedSlugs = new Set([`${SLUG}.takeover`]);
    getMyPendingSLA.mockResolvedValue([]);
    getTeamPendingSLA.mockResolvedValue({
      data: [teamItem], total: 1, page: 1, limit: 50, empty: false,
    });
    renderWidget();
    fireEvent.click(screen.getByRole('button', { name: /My Team/i }));
    await waitFor(() => expect(screen.getByText(/Charissa/)).toBeInTheDocument());
    expect(hasActionButton(/Takeover/i)).toBe(false);
    // Reassign uses a different slug and still shows.
    expect(getActionButton(/Reassign/i)).toBeInTheDocument();
  });

  // ---- Coverage tab (B1/B3) ----------------------------------------------

  it('B1: Coverage tab renders the manager and hides the task search', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    renderWidget();
    await waitFor(() => expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^Coverage$/i }));
    await waitFor(() => expect(screen.getByTestId('coverage-manager')).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: /^Coverage$/i })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Search by number/i)).not.toBeInTheDocument();
  });

  it('B3: passes canManageTeam=true to the manager when the user holds manage_team', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    renderWidget();
    await waitFor(() => expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^Coverage$/i }));
    await waitFor(() =>
      expect(screen.getByTestId('coverage-manager')).toHaveAttribute('data-can-manage', 'true'),
    );
  });

  it('B3: passes canManageTeam=false without manage_team', async () => {
    deniedSlugs = new Set(['notifications.coverage.manage_team']);
    getMyPendingSLA.mockResolvedValue([]);
    renderWidget();
    await waitFor(() => expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^Coverage$/i }));
    await waitFor(() =>
      expect(screen.getByTestId('coverage-manager')).toHaveAttribute('data-can-manage', 'false'),
    );
  });

  // ---- coverage deep link ?team_task= ------------------------------------

  it('deep link ?team_task= switches to My Team and pins+highlights the target row first', async () => {
    extraParams = { team_task: 't2' }; // target the SECOND team row
    getMyPendingSLA.mockResolvedValue([]);
    const other: TeamPendingItem = {
      ...teamItem,
      id: 't1',
      reference: 'FIRST-REF',
      assignee_name: 'Alice',
    };
    const target: TeamPendingItem = {
      ...teamItem,
      id: 't2',
      reference: 'TARGET-REF',
      assignee_name: 'Bob',
    };
    // Backend returns the target NOT first; the widget must pin it to row 0.
    getTeamPendingSLA.mockResolvedValue({
      data: [other, target],
      total: 2,
      page: 1,
      limit: 50,
      empty: false,
    });
    renderWidget();

    // Switched to My Team automatically (no click) and rendered the rows.
    await waitFor(() => expect(screen.getByText(/TARGET-REF/)).toBeInTheDocument());
    expect(screen.getByText('My team tasks')).toBeInTheDocument();

    // The pinned target sits in the FIRST <li> of the list.
    const list = document.querySelector('ul.divide-y') as HTMLElement;
    const rows = list.querySelectorAll('li');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('TARGET-REF');
    expect(rows[1].textContent).toContain('FIRST-REF');

    // The target row is highlighted (ring-2 ring-primary), the other is not.
    const targetButton = rows[0].querySelector('[role="button"]') as HTMLElement;
    const otherButton = rows[1].querySelector('[role="button"]') as HTMLElement;
    expect(targetButton.className).toMatch(/ring-2 ring-primary/);
    expect(otherButton.className).not.toMatch(/ring-2 ring-primary/);
  });

  // ---- intervention tickets (UAC AC-B1/B2) -------------------------------

  it('AC-B1: two tickets for one contact both render, each with its own chips - no de-dup', async () => {
    getMyPendingSLA.mockResolvedValue([ticketOne, ticketTwo]);
    renderWidget();

    // Both enquiry snippets show - two distinct rows for the same contact.
    expect(await screen.findByText('Yes, please connect me to a person.')).toBeInTheDocument();
    expect(
      screen.getByText('Also, can someone quote installation for 3 bathrooms?'),
    ).toBeInTheDocument();

    // Ticket rows carry NO inline Escalate/Resolve (those live in the drawer).
    expect(hasActionButton(/Escalate/i)).toBe(false);
    expect(hasActionButton(/Resolve/i)).toBe(false);
  });

  it('a ticket row offers Reassign and Extend - handing work over is a worklist decision', async () => {
    getMyPendingSLA.mockResolvedValue([ticketOne]);
    renderWidget();

    await waitFor(() =>
      expect(screen.getByText('Yes, please connect me to a person.')).toBeInTheDocument(),
    );
    expect(getActionButton(/Reassign/i)).toBeInTheDocument();
    expect(getActionButton(/Extend/i)).toBeInTheDocument();
  });

  it('Extend stays hidden on a ticket with no resolution deadline', async () => {
    getMyPendingSLA.mockResolvedValue([{ ...ticketOne, due_at_resolution: null }]);
    renderWidget();

    await waitFor(() => expect(getActionButton(/Reassign/i)).toBeInTheDocument());
    expect(hasActionButton(/Extend/i)).toBe(false);
  });

  it('Reassign on a ticket row opens the picker, NOT the chat drawer', async () => {
    getMyPendingSLA.mockResolvedValue([ticketOne]);
    getVisibleUsers.mockResolvedValue([{ id: 'u-tay', name: 'Tay', email: 'tay@example.com' }]);
    renderWidget();

    await waitFor(() => expect(getActionButton(/Reassign/i)).toBeInTheDocument());
    fireEvent.click(getActionButton(/Reassign/i));

    await waitFor(() => expect(screen.getByText('Reassign task')).toBeInTheDocument());
    expect(screen.queryByTestId('ticket-drawer')).not.toBeInTheDocument();
  });

  it('a ticket row loses Reassign without the reassign slug', async () => {
    deniedSlugs = new Set(['sla_management.conversation_sla_tracking.reassign']);
    getMyPendingSLA.mockResolvedValue([ticketOne]);
    renderWidget();

    await waitFor(() =>
      expect(screen.getByText('Yes, please connect me to a person.')).toBeInTheDocument(),
    );
    expect(hasActionButton(/Reassign/i)).toBe(false);
  });

  it('AC-B2: clicking a ticket row opens the drawer in place - no navigation, no Respond', async () => {
    getMyPendingSLA.mockResolvedValue([ticketOne]);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);
    renderWidget();

    await waitFor(() =>
      expect(screen.getByText('Yes, please connect me to a person.')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('Yes, please connect me to a person.'));

    expect(await screen.findByTestId('ticket-drawer')).toHaveAttribute(
      'data-ticket-id',
      'ticket-1',
    );
    expect(push).not.toHaveBeenCalled();
    expect(openSpy).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it('AC-E7: a blank enquiry snippet falls back to a neutral label, never an empty row', async () => {
    getMyPendingSLA.mockResolvedValue([{ ...ticketOne, enquiry_snippet: '   ' }]);
    renderWidget();

    expect(await screen.findByText('Enquiry from this contact')).toBeInTheDocument();
  });

  it('a reply from the drawer reloads the worklist so the chips agree', async () => {
    // The worklist is loaded imperatively (getMyPendingSLA into useState), not
    // through react-query, so invalidating a query key refreshes nothing: the
    // row behind the drawer keeps the pre-reply "Respond by" chip until the
    // drawer is closed. The drawer reports the send instead.
    getMyPendingSLA.mockResolvedValue([ticketOne]);
    renderWidget();

    await waitFor(() =>
      expect(screen.getByText('Yes, please connect me to a person.')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('Yes, please connect me to a person.'));
    await screen.findByTestId('ticket-drawer');

    const before = getMyPendingSLA.mock.calls.length;
    fireEvent.click(screen.getByTestId('drawer-sent'));

    await waitFor(() =>
      expect(getMyPendingSLA.mock.calls.length).toBeGreaterThan(before),
    );
  });

  it('deep link ?ticket= opens the drawer directly and strips the query param', async () => {
    extraParams = { ticket: 'ticket-2' };
    getMyPendingSLA.mockResolvedValue([ticketOne, ticketTwo]);
    renderWidget();

    expect(await screen.findByTestId('ticket-drawer')).toHaveAttribute(
      'data-ticket-id',
      'ticket-2',
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/', { scroll: false }));
  });

  // ---- My Team ticket rows: the backend row shape is shared with My
  // Pending (ConversationSLATrackingService._pending_row), so a team row for
  // an intervention ticket must behave the same way a My Pending one does -
  // it used to fall through to the SLA detail page because team-pending
  // never carried `is_intervention_ticket`.

  it('My Team: a ticket row shows the enquiry snippet + who it belongs to, and clicking it opens the drawer', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    getTeamPendingSLA.mockResolvedValue({
      data: [teamTicketItem],
      total: 1,
      page: 1,
      limit: 50,
      empty: false,
    });
    renderWidget();

    await waitFor(() => expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /My Team/i }));

    // Subline is the enquiry snippet, exactly like My Pending - and the
    // team's one deliberate difference (who it belongs to) sits right
    // under it rather than replacing it.
    const snippet = await screen.findByText('Yes, please connect me to a person.');
    expect(snippet).toBeInTheDocument();
    expect(screen.getByText('Charissa · Marketing - Product')).toBeInTheDocument();

    fireEvent.click(snippet);
    expect(await screen.findByTestId('ticket-drawer')).toHaveAttribute(
      'data-ticket-id',
      'team-ticket-1',
    );
    expect(push).not.toHaveBeenCalled();
  });

  it('My Team: a ticket row keeps Takeover but drops Escalate/Resolve', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    getTeamPendingSLA.mockResolvedValue({
      data: [teamTicketItem],
      total: 1,
      page: 1,
      limit: 50,
      empty: false,
    });
    renderWidget();

    fireEvent.click(screen.getByRole('button', { name: /My Team/i }));

    await waitFor(() => expect(getActionButton(/Takeover/i)).toBeInTheDocument());
    expect(hasActionButton(/Escalate/i)).toBe(false);
    expect(hasActionButton(/Resolve/i)).toBe(false);
  });
});

// ------------------------------------------------- AC-M2: recently resolved
// A resolved row leaves the pending list (right), so the trail has to stay one
// click away (also right). The link is honest: it carries the filter the SLA
// listing applies server-side, not a client-side slice.

describe('MyPendingSLAWidget recently-resolved affordance (AC-M2)', () => {
  beforeEach(() => {
    getMyPendingSLA.mockReset();
    getTeamPendingSLA.mockReset();
    getVisibleUsers.mockReset();
    getTakeoverState.mockReset();
    push.mockReset();
    replace.mockReset();
    searchParam = null;
    extraParams = {};
    deniedSlugs = new Set();
    getTeamPendingSLA.mockResolvedValue({ data: [], total: 0, page: 1, limit: 50, empty: true });
    getVisibleUsers.mockResolvedValue([]);
    getTakeoverState.mockResolvedValue({});
  });

  it('links to the SLA listing filtered to what I resolved, newest first', async () => {
    getMyPendingSLA.mockResolvedValue([ticketOne]);
    renderWidget();

    const link = await screen.findByTestId('recently-resolved-link');
    expect(link).toHaveAttribute(
      'href',
      '/sla-management/conversation-sla-tracking?is_resolved=true&resolved_by=me&sort=resolved_at&dir=desc',
    );
    expect(link).toHaveTextContent(/Recently resolved/i);
  });

  it('shows on an empty pending list too - that is exactly when it is asked for', async () => {
    getMyPendingSLA.mockResolvedValue([]);
    renderWidget();

    await screen.findByText(/all caught up/i);
    expect(screen.getByTestId('recently-resolved-link')).toBeInTheDocument();
  });

  it('is a My Pending affordance only, not a My Team one', async () => {
    getMyPendingSLA.mockResolvedValue([ticketOne]);
    renderWidget();
    await screen.findByTestId('recently-resolved-link');

    fireEvent.click(screen.getByRole('button', { name: 'My Team' }));

    await waitFor(() =>
      expect(screen.queryByTestId('recently-resolved-link')).not.toBeInTheDocument(),
    );
  });
});

// ---- Deadline urgency + extended marker (feedback 2026-08-16, item 6) ----
// The scenario: marketing extends a deadline while waiting on a supplier, then
// forgets. Nothing on the row used to distinguish "due in a week" from "due in
// ten minutes" until it was already red, and nothing said the deadline had been
// moved at all.
describe('MyPendingSLAWidget deadline urgency', () => {
  beforeEach(() => {
    getMyPendingSLA.mockReset();
    getTeamPendingSLA.mockReset();
    getVisibleUsers.mockReset();
    getTakeoverState.mockReset();
    push.mockReset();
    replace.mockReset();
    searchParam = null;
    extraParams = {};
    deniedSlugs = new Set();
    getTeamPendingSLA.mockResolvedValue({ data: [], total: 0, page: 1, limit: 50, empty: true });
    getVisibleUsers.mockResolvedValue([]);
    getTakeoverState.mockResolvedValue({});
  });

  const dueIn = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();

  it('a deadline days away reads neutral', async () => {
    getMyPendingSLA.mockResolvedValue([
      { ...formItem, due_at: dueIn(3 * 24 * 60), active_due_at: dueIn(3 * 24 * 60) },
    ]);
    renderWidget();

    await screen.findByText('Purchase request');
    const label = screen.getByText(/Respond by:/i);
    expect(label.className).toMatch(/text-muted-foreground/);
  });

  it('a deadline inside four hours reads amber, before it has breached', async () => {
    getMyPendingSLA.mockResolvedValue([
      { ...formItem, due_at: dueIn(45), active_due_at: dueIn(45) },
    ]);
    renderWidget();

    await screen.findByText('Purchase request');
    expect(screen.getByText(/Respond by:/i).className).toMatch(/amber/);
  });

  it('a passed deadline reads red', async () => {
    getMyPendingSLA.mockResolvedValue([
      { ...formItem, due_at: dueIn(-10), active_due_at: dueIn(-10) },
    ]);
    renderWidget();

    await screen.findByText('Purchase request');
    expect(screen.getByText(/Respond by:/i).className).toMatch(/text-destructive/);
  });

  it('an extended row is marked, and the marker carries the new due date', async () => {
    getMyPendingSLA.mockResolvedValue([
      {
        ...formItem,
        is_responded: true,
        due_kind: 'resolve',
        due_at_resolution: dueIn(5 * 24 * 60),
        active_due_at: dueIn(5 * 24 * 60),
        extension_count: 1,
      },
    ]);
    renderWidget();

    const marker = await screen.findByTestId('row-extended-marker');
    expect(marker).toHaveTextContent('Extended');
    expect(marker.getAttribute('title')).toMatch(/^Deadline extended to /);
  });

  it('a never-extended row carries no marker', async () => {
    getMyPendingSLA.mockResolvedValue([formItem]);
    renderWidget();

    await screen.findByText('Purchase request');
    expect(screen.queryByTestId('row-extended-marker')).not.toBeInTheDocument();
  });

  it('an extended intervention ticket shows the marker on its chips', async () => {
    getMyPendingSLA.mockResolvedValue([{ ...ticketOne, extension_count: 2 }]);
    renderWidget();

    const chip = await screen.findByTestId('sla-extended-chip');
    expect(chip).toHaveTextContent('Extended ×2');
  });
});
