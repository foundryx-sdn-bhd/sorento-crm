/**
 * "Sign-in" section, S3 2.2 / AC-52.
 *
 * `useUserActions` (the invite dialog + confirmation) has its own file,
 * `../../actions.invite.test.tsx` - here it is stubbed to a plain
 * `{ resendInviteAction, dialogs: null }` so this file stays about the
 * SECTION's own rows: labels, empty states, and the two things that need the
 * owner's action (an invite never sent, a phone that no longer matches its
 * WhatsApp contact).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { User } from '@/app/models/user';
import { UserStatus } from '@/app/models/user';

/** A real-shaped UUID, so the "no UUID in rendered text" checks mean something. */
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

const canEditRef = vi.hoisted(() => ({ current: true }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => canEditRef.current,
}));

const deferredActionInput = vi.fn();
const unlinkStart = vi.fn();
const unlinkCancel = vi.fn();
let unlinkPending: { id: string } | null = null;
vi.mock('@/hooks/useDeferredAction', () => ({
  useDeferredAction: (input: unknown) => {
    deferredActionInput(input);
    return {
      pending: unlinkPending,
      isPending: !!unlinkPending,
      isBlocked: false,
      start: unlinkStart,
      cancel: unlinkCancel,
      countdown: null,
    };
  },
}));

const resendInviteActionRef = vi.hoisted(() => ({
  current: null as null | { label: string; run: () => void; disabled?: boolean },
}));
vi.mock('../../actions', () => ({
  useUserActions: () => ({ resendInviteAction: resendInviteActionRef.current, dialogs: null }),
}));

const useNewNumberMock = vi.fn();
vi.mock('../../services/userService', () => ({
  useNewNumber: (...a: unknown[]) => useNewNumberMock(...a),
}));

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import UserSignInSection from './user-sign-in-section';

function baseUser(over: Partial<User> = {}): User {
  return {
    id: 'user-1',
    email: 'kia@zzt.test',
    name: 'Kia Yee',
    status: UserStatus.ACTIVE,
    isTrashed: false,
    isProtected: false,
    contactNumber: null,
    phoneVerifiedAt: null,
    linkedContact: null,
    phoneDiffersFromContact: false,
    lastSignInAt: null,
    lastSignInMethod: null,
    needsInvitation: false,
    ...over,
  } as unknown as User;
}

function renderSection(user: User) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UserSignInSection user={user} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  canEditRef.current = true;
  resendInviteActionRef.current = null;
  unlinkPending = null;
  useNewNumberMock.mockResolvedValue({});
});

afterEach(() => cleanup());

describe('UserSignInSection - Email row', () => {
  it('shows the address', () => {
    renderSection(baseUser({ email: 'kia@zzt.test' }));
    expect(screen.getByText('kia@zzt.test')).toBeInTheDocument();
  });

  it('shows "No email" for an email-less user, with no Invitation badge', () => {
    renderSection(baseUser({ email: '', needsInvitation: false }));
    expect(screen.getByText('No email')).toBeInTheDocument();
    expect(screen.queryByText('Invitation not sent')).not.toBeInTheDocument();
  });

  it('shows "Invitation not sent" for a never-invited email-only user', () => {
    renderSection(baseUser({ email: 'kia@zzt.test', needsInvitation: true }));
    expect(screen.getByText('Invitation not sent')).toBeInTheDocument();
  });
});

describe('UserSignInSection - Phone row', () => {
  it('shows the empty state when there is no phone', () => {
    renderSection(baseUser({ contactNumber: null }));
    expect(
      screen.getByText('No phone yet. Add one to allow phone sign-in.'),
    ).toBeInTheDocument();
  });

  it('masks a verified phone as the contract specifies, +60 12-*** 6789', () => {
    // s3-contract.md 2.2: "Phone: masked `+60 12-*** 6789`". The component's
    // `maskPhone` produces `+60 *** 6789` (drops the "12-" prefix segment) -
    // this assertion is written to the contract's literal example and is
    // expected to fail until the mask matches it.
    renderSection(
      baseUser({ contactNumber: '+60123456789', phoneVerifiedAt: '2026-01-01T00:00:00Z' }),
    );
    expect(screen.getByText(/\+60 12-\*\*\* 6789/)).toBeInTheDocument();
    expect(screen.getByText('verified')).toBeInTheDocument();
  });

  it('reads "not verified yet" when the phone has no verified_at', () => {
    renderSection(baseUser({ contactNumber: '+60123456789', phoneVerifiedAt: null }));
    expect(screen.getByText('not verified yet')).toBeInTheDocument();
  });
});

describe('UserSignInSection - WhatsApp contact row', () => {
  const CONTACT_ID = 'c3333333-3333-4333-8333-333333333333';

  it('shows "Not linked" when there is no linked contact', () => {
    renderSection(baseUser({ linkedContact: null }));
    expect(screen.getByText('Not linked')).toBeInTheDocument();
  });

  it('links to the contact, reading "name - masked phone" per the contract', () => {
    // s3-contract.md 2.2: "WhatsApp contact: `<name> - <masked phone>` linking
    // to `/user-management/contacts/<id>`". The component joins with ` · `
    // (a middle dot) instead of a hyphen - written to the contract's literal
    // example, expected to fail until the separator matches it.
    renderSection(
      baseUser({
        linkedContact: { id: CONTACT_ID, name: 'Aisyah Rahman', phone_number: '+60123456789' },
      }),
    );
    const link = screen.getByRole('link', { name: /Aisyah Rahman/ });
    expect(link).toHaveAttribute('href', `/user-management/contacts/${CONTACT_ID}`);
    expect(screen.getByText(/Aisyah Rahman - \+60 12-\*\*\* 6789/)).toBeInTheDocument();
  });
});

describe('UserSignInSection - Last sign-in row', () => {
  it('reads "Never" with no session yet', () => {
    renderSection(baseUser({ lastSignInAt: null }));
    expect(screen.getByText('Never')).toBeInTheDocument();
  });

  // Fix round 2, S1: the fixtures are the backend's real `AUTH_METHODS`
  // (`user_session_service.py`): password, phone_otp, portal_link, impersonation.
  // The old fixture used 'email', a value the backend never writes.
  it('reads a password session as "email"', () => {
    renderSection(
      baseUser({ lastSignInAt: '2026-02-01T03:00:00Z' as unknown as Date, lastSignInMethod: 'password' }),
    );
    expect(screen.getByText(/2026/)).toBeInTheDocument();
    expect(screen.getByText(/, email$/)).toBeInTheDocument();
    expect(screen.queryByText(/password/)).not.toBeInTheDocument();
  });

  it('reads a phone code session as "phone"', () => {
    renderSection(
      baseUser({ lastSignInAt: '2026-02-01T03:00:00Z' as unknown as Date, lastSignInMethod: 'phone_otp' }),
    );
    expect(screen.getByText(/, phone$/)).toBeInTheDocument();
    expect(screen.queryByText(/phone_otp/)).not.toBeInTheDocument();
  });

  it('reads a portal session as "portal link"', () => {
    renderSection(
      baseUser({
        lastSignInAt: '2026-02-01T03:00:00Z' as unknown as Date,
        lastSignInMethod: 'portal_link',
      }),
    );
    expect(screen.getByText(/, portal link$/)).toBeInTheDocument();
  });

  it('names no method for an impersonated session, and never shows a raw code', () => {
    const { container } = renderSection(
      baseUser({
        lastSignInAt: '2026-02-01T03:00:00Z' as unknown as Date,
        lastSignInMethod: 'impersonation',
      }),
    );
    expect(screen.getByText(/2026/)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/impersonat/);
  });
});

describe('UserSignInSection - Needs attention', () => {
  it('shows the warning and "Use new number" when the phone differs from the contact', () => {
    renderSection(
      baseUser({
        phoneDiffersFromContact: true,
        linkedContact: { id: 'c-1', name: 'Aisyah', phone_number: '+60120000000' },
      }),
    );
    expect(
      screen.getByText('Needs attention: phone differs from WhatsApp contact.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Use new number' })).toBeInTheDocument();
  });

  it('applies the contact phone through the service when "Use new number" is clicked', async () => {
    renderSection(
      baseUser({
        phoneDiffersFromContact: true,
        linkedContact: { id: 'c-1', name: 'Aisyah', phone_number: '+60120000000' },
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Use new number' }));
    await waitFor(() =>
      expect(useNewNumberMock).toHaveBeenCalledWith('user-1', '+60120000000'),
    );
  });

  it('hides "Use new number" without edit permission', () => {
    canEditRef.current = false;
    renderSection(
      baseUser({
        phoneDiffersFromContact: true,
        linkedContact: { id: 'c-1', name: 'Aisyah', phone_number: '+60120000000' },
      }),
    );
    expect(screen.queryByRole('button', { name: 'Use new number' })).not.toBeInTheDocument();
  });

  it('shows no warning when the phone matches', () => {
    renderSection(baseUser({ phoneDiffersFromContact: false }));
    expect(
      screen.queryByText('Needs attention: phone differs from WhatsApp contact.'),
    ).not.toBeInTheDocument();
  });
});

describe('UserSignInSection - Unlink', () => {
  it('is visible only when a contact is linked and the viewer can edit', () => {
    renderSection(
      baseUser({ linkedContact: { id: 'c-1', name: 'Aisyah', phone_number: '+60120000000' } }),
    );
    expect(screen.getByRole('button', { name: 'Unlink WhatsApp contact' })).toBeInTheDocument();
  });

  it('is absent with no linked contact', () => {
    renderSection(baseUser({ linkedContact: null }));
    expect(screen.queryByRole('button', { name: 'Unlink WhatsApp contact' })).not.toBeInTheDocument();
  });

  it('is absent without edit permission, even when linked', () => {
    canEditRef.current = false;
    renderSection(
      baseUser({ linkedContact: { id: 'c-1', name: 'Aisyah', phone_number: '+60120000000' } }),
    );
    expect(screen.queryByRole('button', { name: 'Unlink WhatsApp contact' })).not.toBeInTheDocument();
  });

  it('parks the deferred action against the right key and entity', () => {
    renderSection(
      baseUser({ linkedContact: { id: 'c-1', name: 'Aisyah', phone_number: '+60120000000' } }),
    );
    expect(deferredActionInput).toHaveBeenCalledWith(
      expect.objectContaining({ actionKey: 'user.unlink_contact', entityType: 'user', entityId: 'user-1' }),
    );
  });
});

describe('UserSignInSection - the header invite button', () => {
  it('renders the resend-invite action label when useUserActions offers one', () => {
    resendInviteActionRef.current = { label: 'Send invitation email', run: vi.fn() };
    renderSection(baseUser());
    expect(screen.getByRole('button', { name: 'Send invitation email' })).toBeInTheDocument();
  });

  it('renders nothing when useUserActions offers none (no email)', () => {
    resendInviteActionRef.current = null;
    renderSection(baseUser({ email: '' }));
    expect(screen.queryByRole('button', { name: 'Send invitation email' })).not.toBeInTheDocument();
  });
});

describe('UserSignInSection - no UUID leaks into rendered text', () => {
  it('never renders the contact id as text', () => {
    const contactId = 'd4444444-4444-4444-8444-444444444444';
    renderSection(
      baseUser({
        id: 'e5555555-5555-4555-8555-555555555555',
        linkedContact: { id: contactId, name: 'Aisyah Rahman', phone_number: '+60123456789' },
      }),
    );
    expect(document.body.textContent ?? '').not.toMatch(UUID_RE);
  });
});
