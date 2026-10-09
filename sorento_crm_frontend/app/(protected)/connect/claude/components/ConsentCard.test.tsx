/**
 * CLAUDE-CONNECTOR r3 red test (contract documentation/plans/analytics/CONTRACT-claude-connector-ss-09oct.md, section 2).
 *   file  app/(protected)/connect/claude/components/ConsentCard.tsx  (default export, prop { req })
 *   page  app/(protected)/connect/claude/page.tsx  reads ?req=<consent_request JWT> and renders <ConsentCard req/>
 *
 * Allow: approveConsent(req) -> { return_url, assertion, request_id }, then the card builds a hidden
 *   <form method="POST" action={return_url}> with inputs request_id + assertion, and submits it.
 *   The assertion never goes into a URL (no window.location / assign / replace with it).
 * Deny: denyConsent(req) -> { return_url, request_id }, then a form POST of request_id + error=access_denied.
 * Variants from inspect: allowed=false (Deny only), request_expired, request_used, impersonation_not_allowed.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const inspectConsent = vi.fn();
const approveConsent = vi.fn();
const denyConsent = vi.fn();
vi.mock('../services/consentService', () => ({
  inspectConsent: (...a: unknown[]) => inspectConsent(...a),
  approveConsent: (...a: unknown[]) => approveConsent(...a),
  denyConsent: (...a: unknown[]) => denyConsent(...a),
}));

import ConsentCard from './ConsentCard';

const REQ = 'eyJ.zzt.req';
const RETURN_URL = 'https://ss.example.com/connector/consent/callback';
const ASSERTION = 'eyJhbGciOiJIUzI1NiJ9.zzt-assertion.sig';
const DETAILS = {
  request_id: 'req-1',
  client_name: 'Claude',
  redirect_host: 'claude.ai',
  user_name: 'Sales Manager A',
  expires_at: new Date(Date.now() + 9 * 60_000).toISOString(),
  allowed: true,
};
const apiError = (code: string, status: number) => Object.assign(new Error(code), { code, status });

type Posted = { method: string; action: string; fields: Record<string, string>; inDocument: boolean };
let posted: Posted[] = [];
const assign = vi.fn();
const replace = vi.fn();
const original = window.location;

function capture(this: HTMLFormElement) {
  const fields: Record<string, string> = {};
  for (const el of Array.from(this.elements) as HTMLInputElement[]) {
    if (el.name) fields[el.name] = el.value;
  }
  posted.push({
    method: (this.getAttribute('method') || this.method || '').toUpperCase(),
    action: this.getAttribute('action') || this.action,
    fields,
    inDocument: document.body.contains(this),
  });
}

const renderCard = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConsentCard req={REQ} />
    </QueryClientProvider>,
  );

beforeEach(() => {
  [inspectConsent, approveConsent, denyConsent, assign, replace].forEach((m) => m.mockReset());
  posted = [];
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(capture);
  vi.spyOn(HTMLFormElement.prototype, 'requestSubmit').mockImplementation(capture);
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...original, assign, replace, href: 'http://localhost:3000/connect/claude' },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Object.defineProperty(window, 'location', { configurable: true, value: original });
});

function expectNoAssertionInAnyUrl() {
  for (const call of [...assign.mock.calls, ...replace.mock.calls]) {
    expect(String(call[0])).not.toContain(ASSERTION);
  }
  expect(String(window.location.href)).not.toContain(ASSERTION);
}

describe('ConsentCard', () => {
  it('inspects on load and names the app, the user, the redirect host; offers Allow and Deny', async () => {
    inspectConsent.mockResolvedValue(DETAILS);
    renderCard();
    expect(await screen.findByText(/wants read-only access to CRM data as Sales Manager A/i)).toBeInTheDocument();
    expect(inspectConsent).toHaveBeenCalledWith(REQ);
    expect(screen.getByText('claude.ai')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^allow$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^deny$/i })).toBeInTheDocument();
  });

  it('Allow posts approve then form-POSTs request_id + assertion to return_url', async () => {
    inspectConsent.mockResolvedValue(DETAILS);
    approveConsent.mockResolvedValue({ return_url: RETURN_URL, assertion: ASSERTION, request_id: 'req-1' });
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: /^allow$/i }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(approveConsent).toHaveBeenCalledWith(REQ);
    expect(denyConsent).not.toHaveBeenCalled();
    const form = posted[0];
    expect(form.method).toBe('POST');
    expect(form.action).toBe(RETURN_URL);
    expect(form.fields).toEqual({ request_id: 'req-1', assertion: ASSERTION });
    expect(form.inDocument).toBe(true);
    expect(form.action).not.toContain(ASSERTION);
    expectNoAssertionInAnyUrl();
    expect(assign).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it('Deny posts deny then form-POSTs request_id + error=access_denied, no assertion', async () => {
    inspectConsent.mockResolvedValue(DETAILS);
    denyConsent.mockResolvedValue({ return_url: RETURN_URL, request_id: 'req-1' });
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: /^deny$/i }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(denyConsent).toHaveBeenCalledWith(REQ);
    expect(approveConsent).not.toHaveBeenCalled();
    expect(posted[0].method).toBe('POST');
    expect(posted[0].action).toBe(RETURN_URL);
    expect(posted[0].fields).toEqual({ request_id: 'req-1', error: 'access_denied' });
    expect(assign).not.toHaveBeenCalled();
  });

  it('without the connector permission shows the refusal and Deny only', async () => {
    inspectConsent.mockResolvedValue({ ...DETAILS, allowed: false });
    renderCard();
    expect(await screen.findByText(/does not have access to the Claude connector/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^allow$/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^deny$/i })).toBeInTheDocument();
  });

  it('shows the expired variant with no buttons', async () => {
    inspectConsent.mockRejectedValue(apiError('request_expired', 400));
    renderCard();
    expect(await screen.findByText(/this request has expired/i)).toBeInTheDocument();
    expect(screen.getByText(/return to claude and connect again/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^allow$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^deny$/i })).not.toBeInTheDocument();
  });

  it('shows the used variant with no buttons', async () => {
    inspectConsent.mockRejectedValue(apiError('request_used', 400));
    renderCard();
    expect(await screen.findByText(/this request has already been used/i)).toBeInTheDocument();
    expect(screen.getByText(/return to claude and connect again/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^allow$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^deny$/i })).not.toBeInTheDocument();
  });

  it('shows the impersonation refusal and no buttons', async () => {
    inspectConsent.mockRejectedValue(apiError('impersonation_not_allowed', 403));
    renderCard();
    expect(await screen.findByText(/signed in as another user/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^allow$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^deny$/i })).not.toBeInTheDocument();
  });

  it('a request used between inspect and Allow shows the used variant and posts nothing', async () => {
    inspectConsent.mockResolvedValue(DETAILS);
    approveConsent.mockRejectedValue(apiError('request_used', 400));
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: /^allow$/i }));
    expect(await screen.findByText(/this request has already been used/i)).toBeInTheDocument();
    expect(posted).toHaveLength(0);
    expect(assign).not.toHaveBeenCalled();
  });
});
