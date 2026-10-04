/**
 * DEV-LOGIN-BYPASS sign-in picker (AC-10): renders nothing when off, auto-signs in as the
 * default dev user once per tab, and offers "Sign in as" afterwards (e.g. after sign-out).
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({ signIn: vi.fn() }));
vi.mock('@/services/devLoginService', () => ({ getDevLoginUsers: vi.fn() }));

import { signIn } from 'next-auth/react';
import { getDevLoginUsers } from '@/services/devLoginService';
import { DEV_LOGIN_AUTO_KEY, DevLoginPicker } from './dev-login-picker';

const mockSignIn = vi.mocked(signIn);
const mockUsers = vi.mocked(getDevLoginUsers);

const USERS = [
  { email: 'admin@example.com', name: 'Dev Admin', role_name: 'Admin' },
  { email: 'test.ideas.viewer@example.com', name: 'Ideas Viewer', role_name: 'Viewer' },
];

function renderPicker(onSignedIn = vi.fn(), onError = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DevLoginPicker onSignedIn={onSignedIn} onError={onError} />
    </QueryClientProvider>,
  );
  return { onSignedIn, onError };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  mockSignIn.mockResolvedValue({ ok: true, error: null, status: 200, url: null } as never);
});

describe('DevLoginPicker', () => {
  it('renders nothing and never signs in when dev sign-in is off', async () => {
    mockUsers.mockResolvedValue(null);
    renderPicker();
    await waitFor(() => expect(mockUsers).toHaveBeenCalled());
    expect(screen.queryByTestId('dev-login-picker')).toBeNull();
    expect(mockSignIn).not.toHaveBeenCalled();
  });

  it('auto-signs in as the first (default) dev user once per tab', async () => {
    mockUsers.mockResolvedValue(USERS);
    const { onSignedIn } = renderPicker();
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledTimes(1));
    expect(mockSignIn).toHaveBeenCalledTimes(1);
    expect(mockSignIn).toHaveBeenCalledWith('dev-login', { redirect: false, email: 'admin@example.com' });
    expect(window.sessionStorage.getItem(DEV_LOGIN_AUTO_KEY)).toBe('1');
  });

  it('after the auto sign-in already ran in this tab (e.g. sign-out), shows the picker instead', async () => {
    window.sessionStorage.setItem(DEV_LOGIN_AUTO_KEY, '1');
    mockUsers.mockResolvedValue(USERS);
    renderPicker();
    expect(await screen.findByTestId('dev-login-picker')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign in as user' })).toBeEnabled();
    expect(mockSignIn).not.toHaveBeenCalled();
  });

  it('switching signs in as the chosen user through the dev-login provider', async () => {
    window.sessionStorage.setItem(DEV_LOGIN_AUTO_KEY, '1');
    mockUsers.mockResolvedValue(USERS);
    const { onSignedIn } = renderPicker();
    await screen.findByTestId('dev-login-picker');
    // The default is preselected; the picker exposes the allowlisted users by name + role only.
    expect(screen.getByRole('combobox', { name: 'Sign in as' })).toHaveTextContent('Dev Admin (Admin)');
    screen.getByRole('button', { name: 'Sign in as user' }).click();
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
    expect(mockSignIn).toHaveBeenCalledWith('dev-login', { redirect: false, email: 'admin@example.com' });
  });

  it('a refused dev sign-in surfaces an error and unlocks', async () => {
    window.sessionStorage.setItem(DEV_LOGIN_AUTO_KEY, '1');
    mockUsers.mockResolvedValue(USERS);
    mockSignIn.mockResolvedValue({ ok: false, error: JSON.stringify({ code: 404, message: 'Dev sign-in is not available.' }), status: 401, url: null } as never);
    const { onSignedIn, onError } = renderPicker();
    await screen.findByTestId('dev-login-picker');
    screen.getByRole('button', { name: 'Sign in as user' }).click();
    await waitFor(() => expect(onError).toHaveBeenCalledWith('Dev sign-in is not available.'));
    expect(onSignedIn).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Sign in as user' })).toBeEnabled();
  });
});
