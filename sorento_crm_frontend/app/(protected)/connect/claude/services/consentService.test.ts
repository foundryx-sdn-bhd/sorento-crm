/**
 * CLAUDE-CONNECTOR S3 red test.
 *   file  app/(protected)/connect/claude/services/consentService.ts
 *   exports getConsent(requestId), approveConsent(requestId), denyConsent(requestId)
 * Each failure throws an Error carrying `code` (the backend `detail`, e.g. "expired",
 * "impersonation_not_allowed") and `status`, so the card can pick the right variant.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import { approveConsent, denyConsent, getConsent } from './consentService';

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const fail = (status: number, detail: string) =>
  ({ ok: false, status, json: async () => ({ detail }), text: async () => JSON.stringify({ detail }) }) as Response;

beforeEach(() => apiFetch.mockReset());

describe('consentService', () => {
  it('reads the consent details', async () => {
    const d = { request_id: 'r1', client_name: 'Claude', redirect_host: 'claude.ai', user_name: 'Ann', allowed: true };
    apiFetch.mockResolvedValue(ok(d));
    expect(await getConsent('r1')).toEqual(d);
    expect(apiFetch.mock.calls[0][0]).toBe('/api/v1/analytics-connector/authorize/r1');
  });

  it('posts approve and deny and returns redirect_to', async () => {
    apiFetch.mockResolvedValue(ok({ redirect_to: 'https://claude.ai/api/mcp/auth_callback?code=x' }));
    expect((await approveConsent('r1')).redirect_to).toContain('code=x');
    expect(apiFetch.mock.calls[0][0]).toBe('/api/v1/analytics-connector/authorize/r1/approve');
    expect(apiFetch.mock.calls[0][1].method).toBe('POST');
    await denyConsent('r1');
    expect(apiFetch.mock.calls[1][0]).toBe('/api/v1/analytics-connector/authorize/r1/deny');
    expect(apiFetch.mock.calls[1][1].method).toBe('POST');
  });

  it('throws an error carrying code and status', async () => {
    apiFetch.mockResolvedValue(fail(410, 'expired'));
    await expect(getConsent('r1')).rejects.toMatchObject({ code: 'expired', status: 410 });
    apiFetch.mockResolvedValue(fail(403, 'impersonation_not_allowed'));
    await expect(approveConsent('r1')).rejects.toMatchObject({ code: 'impersonation_not_allowed', status: 403 });
  });
});
