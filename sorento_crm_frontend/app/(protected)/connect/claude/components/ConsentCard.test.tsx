/**
 * CLAUDE-CONNECTOR S3 red test (mockup sections 1 and 1b).
 *   file  app/(protected)/connect/claude/components/ConsentCard.tsx  (default export, prop { requestId })
 *   page  app/(protected)/connect/claude/page.tsx  reads ?request=<id> and renders <ConsentCard/>
 * Navigation to the returned redirect_to is window.location.assign(url).
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const getConsent = vi.fn();
const approveConsent = vi.fn();
const denyConsent = vi.fn();
vi.mock('../services/consentService', () => ({
  getConsent: (...a: unknown[]) => getConsent(...a),
  approveConsent: (...a: unknown[]) => approveConsent(...a),
  denyConsent: (...a: unknown[]) => denyConsent(...a),
}));

import ConsentCard from './ConsentCard';

const DETAILS = {
  request_id: 'r1',
  client_name: 'Claude',
  redirect_host: 'claude.ai',
  user_name: 'Sales Manager A',
  scope: 'analytics.read',
  expires_at: new Date(Date.now() + 9 * 60_000).toISOString().replace('Z', ''),
  allowed: true,
};
const apiError = (code: string, status: number) => Object.assign(new Error(code), { code, status });

const assign = vi.fn();
const original = window.location;
const renderCard = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConsentCard requestId="r1" />
    </QueryClientProvider>,
  );

beforeEach(() => {
  [getConsent, approveConsent, denyConsent, assign].forEach((m) => m.mockReset());
  Object.defineProperty(window, 'location', { configurable: true, value: { ...original, assign } });
});
afterEach(() => {
  cleanup();
  Object.defineProperty(window, 'location', { configurable: true, value: original });
});

describe('ConsentCard', () => {
  it('names the app, the user, the redirect host and offers Allow and Deny', async () => {
    getConsent.mockResolvedValue(DETAILS);
    renderCard();
    expect(await screen.findByText(/wants read-only access to CRM data as Sales Manager A/i)).toBeInTheDocument();
    expect(screen.getByText(/Claude/)).toBeInTheDocument();
    expect(screen.getByText('claude.ai')).toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^allow$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^deny$/i })).toBeInTheDocument();
  });

  it('Allow posts approve and navigates to the returned redirect_to', async () => {
    getConsent.mockResolvedValue(DETAILS);
    approveConsent.mockResolvedValue({ redirect_to: 'https://claude.ai/api/mcp/auth_callback?code=abc&state=s' });
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: /^allow$/i }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://claude.ai/api/mcp/auth_callback?code=abc&state=s'));
    expect(approveConsent).toHaveBeenCalledWith('r1');
    expect(denyConsent).not.toHaveBeenCalled();
  });

  it('Deny posts deny and navigates to its redirect_to', async () => {
    getConsent.mockResolvedValue(DETAILS);
    denyConsent.mockResolvedValue({ redirect_to: 'https://claude.ai/api/mcp/auth_callback?error=access_denied' });
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: /^deny$/i }));
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith('https://claude.ai/api/mcp/auth_callback?error=access_denied'),
    );
    expect(approveConsent).not.toHaveBeenCalled();
  });

  it('without the connector permission shows the refusal and no Allow button', async () => {
    getConsent.mockResolvedValue({ ...DETAILS, allowed: false });
    renderCard();
    expect(await screen.findByText(/does not have access to the Claude connector/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^allow$/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^deny$/i })).toBeInTheDocument();
  });

  it('shows the expired variant with no buttons', async () => {
    getConsent.mockRejectedValue(apiError('expired', 410));
    renderCard();
    expect(await screen.findByText(/this request has expired/i)).toBeInTheDocument();
    expect(screen.getByText(/return to claude and connect again/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^allow$/i })).not.toBeInTheDocument();
  });

  it('shows the impersonation refusal and no Allow', async () => {
    getConsent.mockRejectedValue(apiError('impersonation_not_allowed', 403));
    renderCard();
    expect(await screen.findByText(/signed in as another user/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^allow$/i })).not.toBeInTheDocument();
  });
});
