/**
 * The captain's review screen.
 *
 * The properties under test are the ones the product standard names: every
 * section renders even when empty, a rejection cannot be sent without a reason,
 * and the action that creates real users is only offered when the request is
 * actually in review.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
/* The grace window is the server's; what this file proves is that the control parks one. */
const createPendingAction = vi.fn().mockResolvedValue({
  id: 'pa-1',
  action_key: 'onboarding_request.delete',
  entity_type: 'onboarding_request',
  entity_id: 'req-1',
  commit_at: '2026-08-30T10:00:10',
  window_seconds: 10,
});
vi.mock('@/lib/toast', () => ({
  // `dismiss` is load-bearing: the countdown's toast is dismissed when the record
  // unmounts, and a stub without it throws out of an effect no assertion catches.
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn(), dismiss: vi.fn() },
}));

vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) => createPendingAction(...args),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));

import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { OnboardingRequestDetail as Detail } from '@/components/common/onboarding/types';
import { OnboardingRequestDetail } from './OnboardingRequestDetail';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

// Container pulls SettingsProvider context this unit test does not need.
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const routerPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
  usePathname: () => '/user-management/onboarding-requests/req-1',
  useSearchParams: () => new URLSearchParams(),
}));

const copyToClipboard = vi.fn();
vi.mock('@/hooks/use-copy-to-clipboard', () => ({
  useCopyToClipboard: ({ onCopy }: { onCopy?: () => void } = {}) => ({
    isCopied: false,
    copyToClipboard: (value: string) => {
      copyToClipboard(value);
      onCopy?.();
    },
  }),
}));

const getOnboardingRequest = vi.fn();
const rejectOnboardingPerson = vi.fn();
const approveOnboardingRequest = vi.fn();
const approveOnboardingPerson = vi.fn();
const startOnboardingReview = vi.fn();
const updateOnboardingPerson = vi.fn();
const deleteOnboardingRequest = vi.fn();
const revokeOnboardingRequest = vi.fn();
const regenerateOnboardingToken = vi.fn();
const sendOnboardingRequest = vi.fn();

vi.mock('../../services/onboardingService', () => ({
  getOnboardingRequest: (...a: unknown[]) => getOnboardingRequest(...a),
  listOnboardingRequests: vi.fn(),
  createOnboardingRequest: vi.fn(),
  rejectOnboardingPerson: (...a: unknown[]) => rejectOnboardingPerson(...a),
  approveOnboardingRequest: (...a: unknown[]) => approveOnboardingRequest(...a),
  approveOnboardingPerson: (...a: unknown[]) => approveOnboardingPerson(...a),
  startOnboardingReview: (...a: unknown[]) => startOnboardingReview(...a),
  updateOnboardingPerson: (...a: unknown[]) => updateOnboardingPerson(...a),
  deleteOnboardingRequest: (...a: unknown[]) => deleteOnboardingRequest(...a),
  revokeOnboardingRequest: (...a: unknown[]) => revokeOnboardingRequest(...a),
  regenerateOnboardingToken: (...a: unknown[]) => regenerateOnboardingToken(...a),
  sendOnboardingRequest: (...a: unknown[]) => sendOnboardingRequest(...a),
}));

/**
 * Record actions live behind the gear menu, so open it before clicking one.
 * Radix opens on pointerdown, which jsdom does not synthesize from a click, so
 * drive it by keyboard instead (ArrowDown opens and focuses the first item).
 */
async function openGearMenu() {
  const trigger = await screen.findByRole('button', { name: /Request actions/i });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'ArrowDown', code: 'ArrowDown' });
  return screen.findByRole('menu');
}

function detail(overrides: Partial<Detail> = {}): Detail {
  return {
    id: 'req-1',
    title: 'MOCHA staff onboarding',
    company_name: 'MOCHA Sdn Bhd',
    requester_name: 'Esther Lim',
    requester_email: 'person13@example.com',
    status: 'in_review',
    people_count: 1,
    approved_count: 0,
    rejected_count: 0,
    submitted_at: '2026-08-14T09:12:00',
    created_at: '2026-08-12T10:00:00',
    expires_at: '2026-08-26T10:00:00',
    revoked_at: null,
    intake_url: 'https://crm.example.com/onboarding/TOKEN',
    reviewer_note: null,
    requester_note: null,
    reviewed_by_name: null,
    provisioned_at: null,
    templates: [
      {
        id: 'tpl-sales',
        name: 'Salesperson',
        description: 'Sees their own orders.',
        default_needs_system_account: true,
        default_needs_respond_contact: true,
        default_needs_agent_seat: false,
      },
    ],
    people: [
      {
        id: 'p1',
        row_number: 1,
        full_name: 'Nurul Aisyah',
        nick_name: 'Aisyah',
        role_label: 'Sales admin',
        phone_raw: '010-0000002',
        email_raw: 'person4@example.com',
        template_id: 'tpl-sales',
        requester_note: null,
        reviewer_note: null,
        needs_system_account: true,
        needs_respond_contact: true,
        needs_agent_seat: false,
        review_status: 'proposed',
        rejection_reason: null,
        collisions: [],
        user_step: 'pending',
        user_error: null,
        user_label: null,
        contact_step: 'pending',
        contact_error: null,
        agent_step: 'pending',
        agent_error: null,
      },
    ],
    ...overrides,
  };
}

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OnboardingRequestDetail requestId="req-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('OnboardingRequestDetail', () => {
  it('renders a loading state', () => {
    getOnboardingRequest.mockReturnValue(new Promise(() => {}));
    const { container } = renderDetail();
    expect(container.querySelectorAll('[data-slot="skeleton"], .animate-pulse').length)
      .toBeGreaterThan(0);
  });

  it('says so when the request cannot be loaded', async () => {
    getOnboardingRequest.mockRejectedValue(new Error('nope'));
    renderDetail();
    expect(
      await screen.findByText(/could not be loaded/i),
    ).toBeInTheDocument();
  });

  it('renders every section, with an empty state where there is nothing', async () => {
    getOnboardingRequest.mockResolvedValue(detail({ requester_note: null }));
    renderDetail();
    // The notes section exists even with no note - a hidden section reads as
    // data loss to whoever came looking for it.
    expect(await screen.findByText('Notes')).toBeInTheDocument();
    expect(screen.getByText('No notes.')).toBeInTheDocument();
    // "People" and "Provisioning" also name grid columns, so assert the section
    // headings exist rather than that the words appear exactly once.
    expect(screen.getAllByText('People').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Provisioning').length).toBeGreaterThan(0);
    expect(screen.getByText('Nothing provisioned yet.')).toBeInTheDocument();
    expect(screen.getByText('Intake link')).toBeInTheDocument();
  });

  it('shows the link is live without ever printing the tokenised URL', async () => {
    getOnboardingRequest.mockResolvedValue(detail({ status: 'sent' }));
    const { container } = renderDetail();
    expect(await screen.findByText('Link active')).toBeInTheDocument();
    // A tokenised URL on screen is a credential on screen: it is copied from
    // the gear menu, never rendered.
    expect(container.textContent).not.toContain('onboarding/TOKEN');
  });

  it('says when the link was revoked instead of offering it', async () => {
    getOnboardingRequest.mockResolvedValue(
      detail({ status: 'sent', revoked_at: '2026-08-15T09:00:00' }),
    );
    renderDetail();
    expect(await screen.findByText(/^Revoked on /)).toBeInTheDocument();
    expect(screen.queryByText('Link active')).not.toBeInTheDocument();
  });

  it('says so when no link could be built at all', async () => {
    getOnboardingRequest.mockResolvedValue(detail({ status: 'sent', intake_url: null }));
    renderDetail();
    expect(await screen.findByText('No intake link available.')).toBeInTheDocument();
  });

  it('keeps read-only metadata in the header strip, not inside a section', async () => {
    getOnboardingRequest.mockResolvedValue(detail());
    renderDetail();
    expect(await screen.findByText('Requester email')).toBeInTheDocument();
    expect(screen.getByText('Link expires')).toBeInTheDocument();
    expect(screen.getByText('Reviewed by')).toBeInTheDocument();
    // "Source file" went with the upload path: every batch is typed in now, so
    // the field could only ever have said one thing.
    expect(screen.queryByText('Source file')).not.toBeInTheDocument();
  });

  it('offers approve only while the request is in review', async () => {
    getOnboardingRequest.mockResolvedValue(detail({ status: 'submitted' }));
    const { unmount } = renderDetail();
    expect(await screen.findByRole('button', { name: /Start review/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Approve and provision/ })).not.toBeInTheDocument();
    unmount();

    getOnboardingRequest.mockResolvedValue(detail({ status: 'in_review' }));
    renderDetail();
    expect(
      await screen.findByRole('button', { name: /Approve and provision/ }),
    ).toBeInTheDocument();
  });

  it('will not send a rejection without a reason', async () => {
    getOnboardingRequest.mockResolvedValue(detail());
    renderDetail();
    fireEvent.click((await screen.findAllByRole('button', { name: 'Reject' }))[0]);

    // The dialog's own Reject is the last one in the tree; the row buttons that
    // opened it are still mounted behind it.
    const dialog = await screen.findByRole('dialog');
    const inDialog = within(dialog).getByRole('button', { name: 'Reject' });
    expect(inDialog).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText('Reason'), {
      target: { value: 'Left the company.' },
    });
    await waitFor(() => expect(inDialog).not.toBeDisabled());
    fireEvent.click(inDialog);
    await waitFor(() =>
      expect(rejectOnboardingPerson).toHaveBeenCalledWith('req-1', 'p1', 'Left the company.'),
    );
  });

  it('puts the server value back when an edit is refused', async () => {
    // A refused edit used to sit on screen looking saved: the grid buffers by
    // cell and only tells the parent on blur, so it then believed the typed
    // value WAS the committed one and a second blur was a no-op. Concretely -
    // one tab approves the batch, another still shows it in review, the edit
    // there comes back 409, and the wrong value simply stays.
    getOnboardingRequest.mockResolvedValue(detail());
    updateOnboardingPerson.mockRejectedValue(
      new Error('This batch is no longer open for review.'),
    );
    renderDetail();

    const email = within(await screen.findByTestId('people-grid')).getByLabelText(
      'Email, row 1',
    );
    fireEvent.change(email, { target: { value: 'person39@example.com' } });
    fireEvent.blur(email);

    await waitFor(() => expect(updateOnboardingPerson).toHaveBeenCalled());
    // The detail query is refetched, so the server's value comes back rather
    // than the refused one staying put.
    await waitFor(() => expect(getOnboardingRequest).toHaveBeenCalledTimes(2));
  });

  it('lets a second need be ticked while the first is still saving', async () => {
    // The reviewer's grid showed the row as it was BEFORE the click until the
    // save answered, so the next click was computed against the stale row: on
    // the needs multi-select that made a second tick send the first one back as
    // false, which is a single select. The edit now lands on the cached row at
    // click time, so the second click toggles against the first.
    getOnboardingRequest.mockResolvedValue(
      detail({
        people: [
          {
            ...detail().people[0],
            needs_system_account: true,
            needs_respond_contact: false,
            needs_agent_seat: false,
          },
        ],
      }),
    );
    // Still in flight: nothing has come back to correct the row.
    updateOnboardingPerson.mockReturnValue(new Promise(() => {}));
    renderDetail();

    const needs = within(await screen.findByTestId('people-grid')).getByLabelText('Needs');
    fireEvent.click(needs);
    fireEvent.click(await screen.findByRole('option', { name: 'Access to chatbot AI' }));
    await waitFor(() =>
      expect(updateOnboardingPerson).toHaveBeenLastCalledWith('req-1', 'p1', {
        needs_system_account: true,
        needs_respond_contact: true,
        needs_agent_seat: false,
      }),
    );

    fireEvent.click(screen.getByRole('option', { name: 'Respond.io account' }));
    await waitFor(() =>
      expect(updateOnboardingPerson).toHaveBeenLastCalledWith('req-1', 'p1', {
        needs_system_account: true,
        needs_respond_contact: true,
        needs_agent_seat: true,
      }),
    );
    // And the menu says so, rather than only the row just clicked reading as chosen.
    expect(screen.getByRole('option', { name: 'System account' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByRole('option', { name: 'Access to chatbot AI' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByRole('option', { name: 'Respond.io account' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });

  it('does not let a finished save wipe the one still in flight behind it', async () => {
    // Two saves overlap whenever the reviewer ticks a second need without
    // waiting: save B goes out, save C goes out, B answers first. When B's
    // success refetched the request, that read was answered with the row as the
    // server knew it BEFORE C - so C's tick disappeared from the grid, and a
    // third click was computed against a row that had lost it. Same failure as
    // the stale-row one above, arriving from the other side.
    const row = detail().people[0];
    const asLoaded = detail({
      people: [
        { ...row, needs_system_account: true, needs_respond_contact: false, needs_agent_seat: false },
      ],
    });
    // What a refetch triggered by B would be answered with: B's need is saved,
    // C's is not there yet.
    const asServerKnowsItAfterB = detail({
      people: [
        { ...row, needs_system_account: true, needs_respond_contact: true, needs_agent_seat: false },
      ],
    });
    getOnboardingRequest
      .mockResolvedValueOnce(asLoaded)
      .mockResolvedValue(asServerKnowsItAfterB);

    // B settles on our command; C never settles, so it is still in flight when
    // B's success handler runs - the exact window the race lives in.
    let settleB: () => void = () => {};
    updateOnboardingPerson
      .mockReturnValueOnce(
        new Promise<void>((resolve) => {
          settleB = resolve;
        }),
      )
      .mockReturnValue(new Promise(() => {}));

    renderDetail();

    const needs = within(await screen.findByTestId('people-grid')).getByLabelText('Needs');
    fireEvent.click(needs);
    // B: tick the chatbot need, and let it reach the row before ticking again -
    // the reviewer's second click is against the row as it now reads.
    fireEvent.click(await screen.findByRole('option', { name: 'Access to chatbot AI' }));
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'Access to chatbot AI' })).toHaveAttribute(
        'aria-checked',
        'true',
      ),
    );
    // C: tick the Respond.io need. B has still not answered.
    fireEvent.click(screen.getByRole('option', { name: 'Respond.io account' }));
    await waitFor(() => expect(updateOnboardingPerson).toHaveBeenCalledTimes(2));

    // B answers, with C still in flight.
    settleB();
    // Long enough that anything B's settlement schedules - a read, and the
    // repaint that read would cause - has landed on screen before we look.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    // The row keeps BOTH ticks, and nothing read the detail out from under C:
    // the only read is the one that loaded the page.
    expect(screen.getByRole('option', { name: 'Access to chatbot AI' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByRole('option', { name: 'Respond.io account' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(getOnboardingRequest).toHaveBeenCalledTimes(1);
  });

  it('stops offering edits the server would refuse once the batch is finished', async () => {
    // The backend's REVIEWER_WRITABLE is (submitted, in_review) and answers 409
    // for the rest, so a completed batch used to render a grid of controls that
    // could only ever fail - and the reviewer found that out by trying.
    getOnboardingRequest.mockResolvedValue(
      detail({
        status: 'completed',
        provisioned_at: '2026-08-15T11:00:00',
        people: [
          {
            ...detail().people[0],
            review_status: 'approved',
            collisions: [{ kind: 'user_email', label: 'Already a user: Tan Wei Ming' }],
            user_step: 'done',
            user_label: 'Nurul Aisyah',
          },
        ],
      }),
    );
    renderDetail();

    const grid = within(await screen.findByTestId('people-grid'));
    expect(grid.queryByRole('textbox')).not.toBeInTheDocument();
    expect(grid.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(grid.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
    // No pickers either: the template and the needs multi-select both fall back
    // to plain text once the batch has left review.
    expect(grid.queryByRole('combobox')).not.toBeInTheDocument();
    expect(grid.getAllByText(/System account, Access to chatbot AI/).length).toBeGreaterThan(0);

    // What a finished batch is FOR still renders: the verdict, what already
    // existed, and where each lane got to.
    expect(grid.getByText('Approved')).toBeInTheDocument();
    expect(grid.getByText('Already a user: Tan Wei Ming')).toBeInTheDocument();
    expect(grid.getAllByText('System account').length).toBeGreaterThan(0);
  });

  it('keeps the grid editable while the batch is still in review', async () => {
    getOnboardingRequest.mockResolvedValue(detail({ status: 'in_review' }));
    renderDetail();
    const grid = within(await screen.findByTestId('people-grid'));
    expect(grid.getByLabelText('Name, row 1')).toBeEnabled();
    expect(grid.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('counts what the reviewer needs to decide', async () => {
    getOnboardingRequest.mockResolvedValue(
      detail({
        people: [
          {
            ...detail().people[0],
            collisions: [{ kind: 'user_email', label: 'Already a user: Tan Wei Ming' }],
            needs_agent_seat: true,
          },
        ],
      }),
    );
    renderDetail();
    expect(await screen.findByText('Submitted 1')).toBeInTheDocument();
    expect(screen.getByText('Existing 1')).toBeInTheDocument();
    expect(screen.getByText('Respond.io accounts 1')).toBeInTheDocument();
  });

  it('names the failures rather than only counting successes', async () => {
    getOnboardingRequest.mockResolvedValue(
      detail({
        status: 'partially_completed',
        people: [
          {
            ...detail().people[0],
            user_step: 'failed',
            user_error: 'Email already registered.',
          },
        ],
      }),
    );
    renderDetail();
    expect(await screen.findByText('Failed 1')).toBeInTheDocument();
    expect(screen.getAllByText('Email already registered.').length).toBeGreaterThan(0);
  });

  it('puts the record actions behind the gear, not in a flat button row', async () => {
    getOnboardingRequest.mockResolvedValue(detail({ status: 'sent' }));
    renderDetail();
    const menu = await openGearMenu();
    for (const label of ['Copy link', 'Revoke link', 'Issue a new link', 'Delete']) {
      expect(within(menu).getByRole('menuitem', { name: label })).toBeInTheDocument();
    }
  });

  it('copies the link from the gear rather than showing it', async () => {
    getOnboardingRequest.mockResolvedValue(detail({ status: 'sent' }));
    renderDetail();
    const menu = await openGearMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Copy link' }));
    expect(copyToClipboard).toHaveBeenCalledWith('https://crm.example.com/onboarding/TOKEN');
  });

  it('parks the delete rather than opening a dialog (S6-10)', async () => {
    getOnboardingRequest.mockResolvedValue(detail());
    renderDetail();
    const menu = await openGearMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Delete' }));

    // D7: the menu item IS the action, and Cancel in the countdown is the way back.
    // Its people go with the request when the server applies it, not on the click.
    await waitFor(() =>
      expect(createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({
          actionKey: 'onboarding_request.delete',
          entityType: 'onboarding_request',
        }),
      ),
    );
    expect(deleteOnboardingRequest).not.toHaveBeenCalled();
    expect(screen.queryByText('Confirm delete')).not.toBeInTheDocument();
  });

  it('carries the reviewer back to the queue', async () => {
    getOnboardingRequest.mockResolvedValue(detail());
    renderDetail();
    expect(
      await screen.findByRole('link', { name: /Back to onboarding requests/ }),
    ).toHaveAttribute('href', '/user-management/onboarding-requests');
  });
});
