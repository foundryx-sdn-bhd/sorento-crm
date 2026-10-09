/**
 * CLAUDE-CONNECTOR r3 red test (contract documentation/plans/analytics/CONTRACT-claude-connector-ss-09oct.md, section 2).
 *   file     app/(protected)/connect/claude/services/consentService.ts
 *   exports  inspectConsent(req), approveConsent(req), denyConsent(req)
 *   calls    POST /api/v1/connector/consent/{inspect|approve|deny}, JSON body { req }
 *   returns  inspect: { request_id, client_name, redirect_host, expires_at, user_name, allowed }
 *            approve: { return_url, assertion, request_id }   deny: { return_url, request_id }
 * A failure throws an Error carrying `code` (the backend's error.code, e.g. "request_expired",
 * "request_used", "impersonation_not_allowed") and `status`, so the card picks the right variant.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import { approveConsent, denyConsent, inspectConsent } from './consentService';

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const fail = (status: number, code: string) => {
  const body = { error: { code, message: 'zzt message' } };
  return {
    ok: false,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this;
    },
  } as unknown as Response;
};

const REQ = 'eyJ.zzt.req';

beforeEach(() => apiFetch.mockReset());

function sent(i: number) {
  const [url, init] = apiFetch.mock.calls[i];
  return { url, method: init?.method, body: JSON.parse(String(init?.body ?? 'null')) };
}

describe('consentService', () => {
  it('inspects the request with a POST carrying req in the body, never in the URL', async () => {
    const d = { request_id: 'r1', client_name: 'Claude', redirect_host: 'claude.ai', user_name: 'Ann', allowed: true };
    apiFetch.mockResolvedValue(ok(d));
    expect(await inspectConsent(REQ)).toEqual(d);
    const s = sent(0);
    expect(s.url).toBe('/api/v1/connector/consent/inspect');
    expect(s.method).toBe('POST');
    expect(s.body).toEqual({ req: REQ });
  });

  it('posts approve and returns return_url + assertion', async () => {
    apiFetch.mockResolvedValue(ok({ return_url: 'https://ss.example.com/cb', assertion: 'a.b.c', request_id: 'r1' }));
    const r = await approveConsent(REQ);
    expect(r).toMatchObject({ return_url: 'https://ss.example.com/cb', assertion: 'a.b.c', request_id: 'r1' });
    const s = sent(0);
    expect(s.url).toBe('/api/v1/connector/consent/approve');
    expect(s.method).toBe('POST');
    expect(s.body).toEqual({ req: REQ });
  });

  it('posts deny and returns return_url', async () => {
    apiFetch.mockResolvedValue(ok({ return_url: 'https://ss.example.com/cb', request_id: 'r1' }));
    expect((await denyConsent(REQ)).return_url).toBe('https://ss.example.com/cb');
    const s = sent(0);
    expect(s.url).toBe('/api/v1/connector/consent/deny');
    expect(s.method).toBe('POST');
    expect(s.body).toEqual({ req: REQ });
  });

  it('throws an error carrying the backend error.code and status', async () => {
    apiFetch.mockResolvedValue(fail(400, 'request_expired'));
    await expect(inspectConsent(REQ)).rejects.toMatchObject({ code: 'request_expired', status: 400 });
    apiFetch.mockResolvedValue(fail(400, 'request_used'));
    await expect(approveConsent(REQ)).rejects.toMatchObject({ code: 'request_used', status: 400 });
    apiFetch.mockResolvedValue(fail(403, 'impersonation_not_allowed'));
    await expect(approveConsent(REQ)).rejects.toMatchObject({ code: 'impersonation_not_allowed', status: 403 });
    apiFetch.mockResolvedValue(fail(400, 'bad_request'));
    await expect(denyConsent(REQ)).rejects.toMatchObject({ code: 'bad_request', status: 400 });
  });
});
