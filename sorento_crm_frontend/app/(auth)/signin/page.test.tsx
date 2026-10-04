/**
 * `/signin` phone entry (PLAN-unified-identity-26sep.md S1, AC-20, AC-25,
 * AC-29; fix round 1, 29 Sep 2026: the Email | Phone toggle became an
 * "or Log in with" divider plus one round phone icon button). The Phase 1 frontend already ships this against the mocked
 * `phoneSigninService` and NextAuth's `phone-otp` provider; these tests pin
 * the contract's exact words and DOM shape so a later change cannot drift
 * from it silently.
 *
 * Mirrors app/(auth)/portal/components/PortalVerifyCard.test.tsx for how
 * that card is rendered and mocked - AC-25 renders BOTH components in this
 * file to prove they share the one `OtpCodeField`.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TooltipProvider } from '@/components/ui/tooltip';

const mockPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('next-auth/react', () => ({
  signIn: vi.fn(),
  getSession: vi.fn(),
}));

vi.mock('@/services/phoneSigninService', () => ({
  requestSigninCode: vi.fn(),
}));

vi.mock('../portal/lib/portal-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../portal/lib/portal-client')>();
  return {
    ...original,
    fetchSlugInfo: vi.fn(),
    fetchTokenInfo: vi.fn(),
    requestOtp: vi.fn(),
    verifyOtp: vi.fn(),
  };
});

import Page from './page';
import { PortalVerifyCard } from '../portal/components/PortalVerifyCard';
import { getSession, signIn } from 'next-auth/react';
import { requestSigninCode } from '@/services/phoneSigninService';
import { fetchSlugInfo, requestOtp } from '../portal/lib/portal-client';

const mockSignIn = vi.mocked(signIn);
const mockGetSession = vi.mocked(getSession);
const mockRequestSigninCode = vi.mocked(requestSigninCode);
const mockFetchSlugInfo = vi.mocked(fetchSlugInfo);
const mockRequestOtp = vi.mocked(requestOtp);

function wrapper({ children }: { children: React.ReactNode }) {
  // The app mounts its one TooltipProvider in ClientProviders.tsx; the test
  // stands in for it so the phone button's tooltip has a provider.
  return (
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  );
}

function openPhone() {
  fireEvent.click(screen.getByRole('button', { name: 'Phone number' }));
}

function renderSignin() {
  return render(<Page />, { wrapper });
}

const CODE_RESULT = { sent_to: '+60•••6789', expires_in_seconds: 600, resend_in_seconds: 60 };

// Every accepted spelling of 012-345 6789 reaches the backend as this one value
// (owner ruling 29 Sep 2026: the shared PhoneInput sends E.164).
const E164 = '+60123456789';

async function toPhoneCodeStep(typedPhone = '012-345 6789') {
  mockRequestSigninCode.mockResolvedValue(CODE_RESULT);
  renderSignin();
  openPhone();
  fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: typedPhone } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  await waitFor(() => expect(screen.getByTestId('otp-code-field')).toBeInTheDocument());
  return E164;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetSession.mockResolvedValue(null as unknown as Awaited<ReturnType<typeof getSession>>);
});

afterEach(() => {
  if (vi.isFakeTimers()) vi.useRealTimers();
});

describe('AC-20: /signin phone entry', () => {
  it('renders the heading, no Email/Phone tablist, and an "or Log in with" divider with one phone icon button under Continue', () => {
    renderSignin();

    expect(screen.getByRole('heading', { name: 'Sign in to Sorento' })).toBeInTheDocument();
    expect(screen.queryByRole('tablist')).toBeNull();
    expect(screen.queryByRole('tab')).toBeNull();

    const continueButton = screen.getByRole('button', { name: 'Continue' });
    const divider = screen.getByText('or Log in with');
    const phoneButton = screen.getByRole('button', { name: 'Phone number' });
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(continueButton.compareDocumentPosition(divider) & FOLLOWING).toBeTruthy();
    expect(divider.compareDocumentPosition(phoneButton) & FOLLOWING).toBeTruthy();
    expect(phoneButton.className).toContain('rounded-full');
    // No Facebook, Google or Sign Up entry.
    expect(screen.queryByText(/facebook|google|sign up/i)).toBeNull();
  });

  it('the phone icon button swaps the card body to the phone flow, and Back to email returns to the email form', () => {
    renderSignin();
    openPhone();

    expect(screen.getByLabelText('Phone number')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Your email')).toBeNull();
    expect(screen.queryByText('or Log in with')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Phone number' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Back to email' }));

    expect(screen.getByPlaceholderText('Your email')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Phone number' })).toBeNull();
    expect(screen.getByText('or Log in with')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Back to email' })).toBeNull();
  });

  it('Back to email from the code step clears the error slot', async () => {
    await toPhoneCodeStep();
    mockSignIn.mockResolvedValue({
      error: JSON.stringify({ code: 401, message: 'That code is not right. 4 tries left.' }),
    } as Awaited<ReturnType<typeof signIn>>);
    const input = within(screen.getByTestId('otp-code-field')).getByPlaceholderText('6-digit code');
    fireEvent.change(input, { target: { value: '000000' } });
    await waitFor(() =>
      expect(screen.getByText('That code is not right. 4 tries left.')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Back to email' }));

    expect(screen.queryByText('That code is not right. 4 tries left.')).toBeNull();
    expect(screen.getByPlaceholderText('Your email')).toBeInTheDocument();
  });

  it('Email mode shows Email, Password, Forgot Password and Continue in that DOM order, and no Remember me', () => {
    renderSignin();

    const emailInput = screen.getByPlaceholderText('Your email');
    const passwordInput = screen.getByPlaceholderText('Your password');
    const forgotLink = screen.getByRole('link', { name: 'Forgot Password?' });
    const continueButton = screen.getByRole('button', { name: 'Continue' });

    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(emailInput.compareDocumentPosition(passwordInput) & FOLLOWING).toBeTruthy();
    expect(emailInput.compareDocumentPosition(forgotLink) & FOLLOWING).toBeTruthy();
    expect(forgotLink.compareDocumentPosition(continueButton) & FOLLOWING).toBeTruthy();
    // SIGNIN-ALWAYS-SLIDE: every sign-in is the 30-day sliding session, so there is no box to tick.
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByText('Remember me')).toBeNull();
  });

  it('Phone mode shows Phone number and Continue, and no Remember me', () => {
    renderSignin();
    openPhone();

    expect(screen.getByLabelText('Phone number')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByText('Remember me')).toBeNull();
  });

  it('SIGNIN-ALWAYS-SLIDE regression: the email submit payload carries no rememberMe', async () => {
    mockSignIn.mockResolvedValue({ error: null, ok: true } as Awaited<ReturnType<typeof signIn>>);
    mockGetSession.mockResolvedValue(null);
    renderSignin();

    fireEvent.change(screen.getByPlaceholderText('Your email'), {
      target: { value: 'staff@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('Your password'), {
      target: { value: 'correct-horse' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    await waitFor(() => expect(mockSignIn).toHaveBeenCalledWith('credentials', expect.anything()));
    const call = mockSignIn.mock.calls.find(([provider]) => provider === 'credentials');
    const payload = call?.[1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('rememberMe');
    expect(payload).not.toHaveProperty('remember_me');
    expect(payload).toEqual({
      redirect: false,
      email: 'staff@example.com',
      password: 'correct-horse',
    });
  });

  it('the phone field is the shared PhoneInput, Malaysia (+60) by default', () => {
    renderSignin();
    openPhone();

    expect(screen.getByRole('button', { name: 'Country: Malaysia (+60)' })).toBeInTheDocument();
    expect(screen.getByLabelText('Phone number')).toHaveAttribute('type', 'tel');
  });

  it.each([['0123456789'], ['60123456789'], ['+60 12-345 6789']])(
    'Continue after entering %s requests the code for +60123456789',
    async (typed) => {
      const sent = await toPhoneCodeStep(typed);
      expect(sent).toBe(E164);
      expect(mockRequestSigninCode).toHaveBeenCalledWith(E164);
    },
  );

  it('Continue with an incomplete number shows the field error and requests nothing', () => {
    renderSignin();
    openPhone();
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '01234' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(screen.getByText('Enter a complete phone number.')).toBeInTheDocument();
    expect(screen.getByLabelText('Phone number')).toHaveAttribute('aria-invalid', 'true');
    expect(mockRequestSigninCode).not.toHaveBeenCalled();
    expect(screen.queryByTestId('otp-code-field')).toBeNull();
  });

  it('Continue requests the code with the typed number, then shows the WhatsApp line, Change number and the code field', async () => {
    const typed = await toPhoneCodeStep('012-345 6789');

    expect(mockRequestSigninCode).toHaveBeenCalledWith(typed);
    expect(screen.getByText(/We.ll send a code to your WhatsApp/)).toBeInTheDocument();
    expect(screen.getByText(CODE_RESULT.sent_to)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Change number' })).toBeInTheDocument();
    expect(screen.getByTestId('otp-code-field')).toBeInTheDocument();
  });

  it('Change number returns to step 1', async () => {
    await toPhoneCodeStep('0123456789');

    fireEvent.click(screen.getByRole('button', { name: 'Change number' }));

    expect(screen.getByLabelText('Phone number')).toBeInTheDocument();
    expect(screen.queryByTestId('otp-code-field')).toBeNull();
  });
});

describe('AC-25: /signin and PortalVerifyCard share the one OtpCodeField', () => {
  it('/signin phone step 2 renders the shared field with the contract input attributes', async () => {
    await toPhoneCodeStep();

    const field = screen.getByTestId('otp-code-field');
    const input = within(field).getByPlaceholderText('6-digit code');
    expect(input).toHaveAttribute('inputmode', 'numeric');
    expect(input).toHaveAttribute('autocomplete', 'one-time-code');
  });

  it('PortalVerifyCard otp state renders the SAME shared field with the same attributes', async () => {
    mockFetchSlugInfo.mockResolvedValue({
      contact_id: 'c1',
      space_id: 's1',
      name: 'Ahmad Tester',
      masked_phone: '+60•••1234',
      whatsapp_number: '60123456789',
    });
    mockRequestOtp.mockResolvedValue({ sent_to: '+60•••1234', expires_at: 'x' });
    window.history.pushState({}, '', '/portal/c/SLUG123456/verify');

    render(<PortalVerifyCard slug="SLUG123456" />);

    const field = await screen.findByTestId('otp-code-field');
    const input = within(field).getByPlaceholderText('6-digit code');
    expect(input).toHaveAttribute('inputmode', 'numeric');
    expect(input).toHaveAttribute('autocomplete', 'one-time-code');
  });

  it('typing 6 digits on /signin calls signIn("phone-otp", ...) exactly once, with no submit button anywhere on screen', async () => {
    const typed = await toPhoneCodeStep('0123456789');
    mockSignIn.mockResolvedValue({ error: null, ok: true } as Awaited<ReturnType<typeof signIn>>);

    expect(document.querySelector('button[type="submit"]')).toBeNull();

    const field = screen.getByTestId('otp-code-field');
    const input = within(field).getByPlaceholderText('6-digit code');
    fireEvent.change(input, { target: { value: '123456' } });

    await waitFor(() => expect(mockSignIn).toHaveBeenCalledTimes(1));
    expect(mockSignIn).toHaveBeenCalledWith('phone-otp', {
      redirect: false,
      phone: typed,
      code: '123456',
    });
  });

  it('the resend button reads "Resend in 60s" right after the code is requested, then "Resend code" once the cooldown lapses', async () => {
    vi.useFakeTimers();
    mockRequestSigninCode.mockResolvedValue(CODE_RESULT);
    renderSignin();
    openPhone();
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '0123456789' } });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByRole('button', { name: 'Resend in 60s' })).toBeInTheDocument();

    // One second at a time: the cooldown re-arms its own setTimeout from a
    // useEffect keyed on the just-updated state, so a single 60s jump can
    // leave the second half of the countdown never scheduled. Sixty separate
    // flushes give each tick's effect a chance to register the next one.
    for (let i = 0; i < 60; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    }

    expect(screen.getByRole('button', { name: 'Resend code' })).toBeInTheDocument();
  });
});

describe('error words', () => {
  it('a wrong-code sign-in shows the exact tries-left message and clears the code', async () => {
    await toPhoneCodeStep();
    mockSignIn.mockResolvedValue({
      error: JSON.stringify({ code: 401, message: 'That code is not right. 4 tries left.' }),
    } as Awaited<ReturnType<typeof signIn>>);

    const input = within(screen.getByTestId('otp-code-field')).getByPlaceholderText(
      '6-digit code',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '000000' } });

    await waitFor(() =>
      expect(screen.getByText('That code is not right. 4 tries left.')).toBeInTheDocument(),
    );
    expect(input.value).toBe('');
  });

  it('a refused request-code shows the rate-limit message verbatim', async () => {
    mockRequestSigninCode.mockRejectedValue(new Error('Too many tries. Try again in 12 minutes.'));
    renderSignin();
    openPhone();
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '0123456789' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    await waitFor(() =>
      expect(screen.getByText('Too many tries. Try again in 12 minutes.')).toBeInTheDocument(),
    );
  });
});

describe('fix round 4: feedback from the sixth digit to the destination (#1307)', () => {
  function codeInput() {
    return within(screen.getByTestId('otp-code-field')).getByPlaceholderText(
      '6-digit code',
    ) as HTMLInputElement;
  }

  it('the sixth digit locks the field and shows "Signing you in" with the number, at once', async () => {
    await toPhoneCodeStep();
    // A verify that has not answered yet: the state must be up before it does.
    mockSignIn.mockReturnValue(new Promise(() => {}));

    fireEvent.change(codeInput(), { target: { value: '123456' } });

    const status = await screen.findByTestId('phone-signin-verifying');
    expect(status).toHaveAttribute('role', 'status');
    expect(status).toHaveTextContent('Signing you in as +60•••6789');
    expect(codeInput()).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Change number' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Back to email' })).toBeDisabled();
    // The resend button's place is taken, not stacked under the status.
    expect(screen.queryByRole('button', { name: /resend/i })).toBeNull();
  });

  it('a successful verify keeps the state up through the navigation instead of re-enabling the field', async () => {
    await toPhoneCodeStep();
    mockSignIn.mockResolvedValue({ error: null, ok: true } as Awaited<ReturnType<typeof signIn>>);

    fireEvent.change(codeInput(), { target: { value: '123456' } });

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/'));
    // router.push has returned, the destination has not painted: still locked.
    expect(screen.getByTestId('phone-signin-verifying')).toBeInTheDocument();
    expect(codeInput()).toBeDisabled();
  });

  it('a wrong code clears the field, re-enables and refocuses it, and drops the status', async () => {
    await toPhoneCodeStep();
    mockSignIn.mockResolvedValue({
      error: JSON.stringify({ code: 401, message: 'That code is not right. 4 tries left.' }),
    } as Awaited<ReturnType<typeof signIn>>);

    fireEvent.change(codeInput(), { target: { value: '000000' } });

    await waitFor(() =>
      expect(screen.getByText('That code is not right. 4 tries left.')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('phone-signin-verifying')).toBeNull();
    expect(codeInput()).not.toBeDisabled();
    expect(codeInput().value).toBe('');
    expect(document.activeElement).toBe(codeInput());
    expect(screen.getByRole('button', { name: /resend/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to email' })).not.toBeDisabled();
  });

  it('fix round 5: "Back to email" unlocks in the same commit that brings the field back', async () => {
    await toPhoneCodeStep();
    mockSignIn.mockResolvedValue({
      error: JSON.stringify({ code: 401, message: 'That code is not right. 4 tries left.' }),
    } as Awaited<ReturnType<typeof signIn>>);

    // Read the page at the first DOM commit that shows the error, before any
    // later commit could catch "Back to email" up. waitFor would retry past a
    // one-commit lag; this does not.
    let firstSeen: { field: boolean; back: boolean } | null = null;
    const observer = new MutationObserver(() => {
      if (firstSeen || !screen.queryByText('That code is not right. 4 tries left.')) return;
      const back = screen.getByRole('button', { name: 'Back to email' }) as HTMLButtonElement;
      firstSeen = { field: codeInput().disabled, back: back.disabled };
    });
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      characterData: true,
    });

    fireEvent.change(codeInput(), { target: { value: '000000' } });

    await waitFor(() => expect(firstSeen).not.toBeNull());
    observer.disconnect();
    expect(firstSeen).toEqual({ field: false, back: false });
  });

  it('email Continue reads "Signing you in" and stays locked through the navigation', async () => {
    mockSignIn.mockResolvedValue({ error: null, ok: true } as Awaited<ReturnType<typeof signIn>>);
    renderSignin();
    fireEvent.change(screen.getByPlaceholderText('Your email'), {
      target: { value: 'a@b.co' },
    });
    fireEvent.change(screen.getByPlaceholderText('Your password'), {
      target: { value: 'secret123' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/'));
    const button = screen.getByRole('button', { name: 'Signing you in' });
    expect(button).toBeDisabled();
    expect(screen.getByPlaceholderText('Your email')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Phone number' })).toBeDisabled();
  });

  it('a refused email sign-in unlocks the form and shows Continue again', async () => {
    mockSignIn.mockResolvedValue({
      error: JSON.stringify({ message: 'Invalid email or password.' }),
    } as Awaited<ReturnType<typeof signIn>>);
    renderSignin();
    fireEvent.change(screen.getByPlaceholderText('Your email'), {
      target: { value: 'a@b.co' },
    });
    fireEvent.change(screen.getByPlaceholderText('Your password'), {
      target: { value: 'wrong-one' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    await waitFor(() =>
      expect(screen.getByText('Invalid email or password.')).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: 'Continue' })).not.toBeDisabled();
    expect(screen.getByPlaceholderText('Your email')).not.toBeDisabled();
    expect(mockPush).not.toHaveBeenCalled();
  });
});

describe('AC-29: the Email form carries no change beyond the phone entry below it', () => {
  it('the Email form contains exactly its existing controls and no explanatory paragraph', () => {
    const { container } = renderSignin();
    const form = container.querySelector('form');
    expect(form).not.toBeNull();
    const scoped = within(form as HTMLElement);

    expect(scoped.getByPlaceholderText('Your email')).toBeInTheDocument();
    expect(scoped.getByPlaceholderText('Your password')).toBeInTheDocument();
    expect(scoped.getAllByRole('link')).toHaveLength(1);
    expect(scoped.getByRole('link', { name: 'Forgot Password?' })).toBeInTheDocument();
    // No Remember me box since SIGNIN-ALWAYS-SLIDE: every sign-in is the 30-day slide.
    expect(scoped.queryAllByRole('checkbox')).toHaveLength(0);
    // The eye/show-password toggle plus "Continue" - nothing else.
    expect(scoped.getAllByRole('button')).toHaveLength(2);
    expect(scoped.getByRole('button', { name: 'Continue' })).toBeInTheDocument();
    expect((form as HTMLElement).querySelectorAll('p').length).toBe(0);
    expect(scoped.queryByTestId('otp-code-field')).toBeNull();
  });
});
