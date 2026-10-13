/**
 * IDEA-CRM-SIDE Phase 2 red tests: the portal service (`services/ideasPortalService.ts`) hits the
 * gateway paths `/api/v1/ideation/portal/...` (AC-E-02..E-07 data seams).
 *
 * Seam: `apiFetch` from `@/lib/api`, as `ideasService.test.ts` does.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import * as service from './ideasPortalService';

const BASE = '/api/v1/ideation/portal';

function ok(body: unknown, status = 200) {
  return {
    ok: true,
    status,
    headers: { get: () => 'application/json' },
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function fail(status: number, detail: string) {
  return {
    ok: false,
    status,
    headers: { get: () => 'application/json' },
    json: async () => ({ detail }),
    text: async () => JSON.stringify({ detail }),
  } as unknown as Response;
}

function call(n = 0) {
  const [url, init] = apiFetch.mock.calls[n] as [string, RequestInit | undefined];
  const parsed = new URL(url, 'http://crm.test');
  return {
    path: parsed.pathname,
    params: parsed.searchParams,
    method: (init?.method ?? 'GET').toUpperCase(),
    body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
  };
}

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue(ok({}));
});

describe('reads', () => {
  it('getRoadmap: GET /roadmap and returns the columns', async () => {
    const body = { columns: [{ key: 'planned', title: 'Planned', color: 'blue', count: 1, ideas: [] }] };
    apiFetch.mockResolvedValue(ok(body));
    expect(await service.getRoadmap()).toEqual(body);
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/roadmap`, 'GET']);
    expect(c.params.has('mine')).toBe(false);
  });

  it('getRoadmap({mine}): sends mine=true', async () => {
    await service.getRoadmap({ mine: true });
    expect(call().params.get('mine')).toBe('true');
  });

  it('listFeedback: GET /ideas with q, sort, mine, page and pageSize', async () => {
    const body = { items: [], total: 0, page: 2, pageSize: 25 };
    apiFetch.mockResolvedValue(ok(body));
    expect(await service.listFeedback({ q: 'quote', sort: 'new', mine: true, page: 2, pageSize: 25 })).toEqual(body);
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas`, 'GET']);
    expect(c.params.get('q')).toBe('quote');
    expect(c.params.get('sort')).toBe('new');
    expect(c.params.get('mine')).toBe('true');
    expect(c.params.get('page')).toBe('2');
    expect(c.params.get('pageSize')).toBe('25');
    expect(c.params.has('productId')).toBe(false);
  });

  it('listFeedback: unset params are not sent', async () => {
    await service.listFeedback({});
    const c = call();
    expect([...c.params.keys()]).toEqual([]);
  });

  it('listFeedback: a blank search and mine=false are not sent', async () => {
    await service.listFeedback({ q: '   ', mine: false, sort: 'top' });
    const c = call();
    expect(c.params.has('q')).toBe(false);
    expect(c.params.has('mine')).toBe(false);
    expect(c.params.get('sort')).toBe('top');
  });

  it('getChangelog: GET /changelog', async () => {
    const body = { items: [{ id: 'i-1', deliveredAt: '2026-10-01T00:00:00Z', note: 'Shipped' }] };
    apiFetch.mockResolvedValue(ok(body));
    expect(await service.getChangelog()).toEqual(body);
    expect([call().path, call().method]).toEqual([`${BASE}/changelog`, 'GET']);
  });

  it('getPortalIdea: GET /ideas/{id}', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'idea-1', title: 'T', timeline: [] }));
    expect(await service.getPortalIdea('idea-1')).toMatchObject({ id: 'idea-1', title: 'T' });
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-1`, 'GET']);
  });

  it('getVoters falls back to the role when ss sends no name (dealer voters)', async () => {
    apiFetch.mockResolvedValue(ok([{ name: null, role: 'Dealer', isMe: false }]));
    expect(await service.getVoters('idea-1')).toEqual([{ name: 'Dealer', role: null, isMe: false }]);
  });

  it('getVoters: GET /ideas/{id}/voters', async () => {
    apiFetch.mockResolvedValue(ok([{ name: 'Jane', role: 'Sales', isMe: true }]));
    expect(await service.getVoters('idea-1')).toEqual([{ name: 'Jane', role: 'Sales', isMe: true }]);
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-1/voters`, 'GET']);
  });

  it('getActivity: GET /ideas/{id}/activity', async () => {
    apiFetch.mockResolvedValue(ok([]));
    expect(await service.getActivity('idea-1')).toEqual([]);
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-1/activity`, 'GET']);
  });

  it('getActivity maps ss main PortalActivityOut (kind, at, by, fromLabel, toLabel, comment) to rows with unique ids', async () => {
    // ss main 812dac2b `PortalActivityOut` (crew real-SS check, 13 Oct): no id, no label, actor is `by`.
    const at = '2026-10-01T00:00:00Z';
    apiFetch.mockResolvedValue(
      ok([
        { kind: 'status', at, by: 'Sam Wong', source: 'staff', fromLabel: 'Captured', toLabel: 'Triaged', toColor: 'blue', comment: null, replies: [], commentId: null },
        { kind: 'status', at, by: null, source: 'system', fromLabel: null, toLabel: 'Planned', toColor: 'amber', comment: null, replies: [], commentId: null },
        { kind: 'comment', at, by: 'Ana', source: 'portal', fromLabel: null, toLabel: null, toColor: null, comment: { id: 'c-1', authorName: 'Ana', body: 'hello' }, replies: [], commentId: 'c-1' },
        { kind: 'vote', at, by: 'Bo', source: 'portal', fromLabel: null, toLabel: null, toColor: null, comment: null, replies: [], commentId: null },
        { kind: 'vote', at, by: 'Bo', source: 'portal', fromLabel: null, toLabel: null, toColor: null, comment: null, replies: [], commentId: null },
        { kind: 'portal_hidden', at, by: null, source: 'staff', fromLabel: null, toLabel: null, toColor: null, comment: null, replies: [], commentId: null },
      ]),
    );
    const rows = await service.getActivity('idea-1');
    expect(rows.map((r) => r.label)).toEqual([
      'Moved from Captured to Triaged',
      'Moved to Planned',
      'Commented: hello',
      'Upvoted',
      'Upvoted',
      'Portal hidden',
    ]);
    expect(rows.map((r) => r.actorName)).toEqual(['Sam Wong', null, 'Ana', 'Bo', 'Bo', null]);
    expect(rows.every((r) => r.at === at)).toBe(true);
    expect(new Set(rows.map((r) => r.id)).size).toBe(rows.length);
  });

  it('listPortalComments: GET /ideas/{id}/comments and maps isDeleted to deleted, keeping role and likes', async () => {
    apiFetch.mockResolvedValue(
      ok([
        {
          id: 'c-1',
          parentId: null,
          authorName: 'Alex Staff',
          authorRole: 'Sales',
          body: 'Looks good',
          createdAt: '2026-10-01T10:00:00Z',
          isDeleted: false,
          canEdit: false,
          canDelete: true,
          likeCount: 2,
          likedByMe: true,
        },
        { id: 'c-2', parentId: 'c-1', authorName: 'X', body: 'gone', createdAt: '2026-10-01T11:00:00Z', isDeleted: true },
      ]),
    );
    const out = await service.listPortalComments('idea-1');
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-1/comments`, 'GET']);
    expect(out[0]).toMatchObject({
      id: 'c-1',
      authorName: 'Alex Staff',
      authorRole: 'Sales',
      body: 'Looks good',
      deleted: false,
      canDelete: true,
      likeCount: 2,
      likedByMe: true,
    });
    expect(out[1]).toMatchObject({ id: 'c-2', parentId: 'c-1', deleted: true });
    expect(out[1].body ?? '').toBe('');
  });

  it('a gateway refusal is thrown with the server message and status', async () => {
    apiFetch.mockResolvedValue(fail(502, "The Ideas workspace isn't reachable right now."));
    await expect(service.getRoadmap()).rejects.toMatchObject({
      message: "The Ideas workspace isn't reachable right now.",
      status: 502,
    });
  });
});

describe('writes', () => {
  it('togglePortalVote: POST /ideas/{id}/vote', async () => {
    apiFetch.mockResolvedValue(ok({ myVote: true, upvotes: 4 }));
    expect(await service.togglePortalVote('idea-1')).toMatchObject({ upvotes: 4 });
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-1/vote`, 'POST']);
  });

  it('postPortalComment: POST /ideas/{id}/comments with {body, parentId}', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'c-9', body: 'hi', createdAt: '2026-10-01T10:00:00Z', authorName: 'Pat' }));
    await service.postPortalComment('idea-1', { body: 'hi', parentId: 'c-1' });
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1/comments`, 'POST']);
    expect(c.body).toEqual({ body: 'hi', parentId: 'c-1' });
  });

  it('postPortalComment: a top-level comment carries no parentId', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'c-9', body: 'hi', createdAt: '2026-10-01T10:00:00Z', authorName: 'Pat' }));
    await service.postPortalComment('idea-1', { body: 'hi' });
    expect(call().body).toEqual({ body: 'hi' });
  });

  it('likePortalComment: POST /ideas/{id}/comments/{cid}/like', async () => {
    apiFetch.mockResolvedValue(ok({ likeCount: 3, likedByMe: true }));
    await service.likePortalComment('idea-1', 'c-1');
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-1/comments/c-1/like`, 'POST']);
  });

  it('setPortalHidden: PUT /ideas/{id}/hidden with {hidden}', async () => {
    await service.setPortalHidden('idea-1', true);
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1/hidden`, 'PUT']);
    expect(c.body).toEqual({ hidden: true });
  });

  it('setPortalNote: PUT /ideas/{id}/note with {note}', async () => {
    await service.setPortalNote('idea-1', 'Shipped in v2');
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1/note`, 'PUT']);
    expect(c.body).toEqual({ note: 'Shipped in v2' });
  });

  it('a write refusal is thrown with the server message', async () => {
    apiFetch.mockResolvedValue(fail(403, 'Forbidden'));
    await expect(service.setPortalHidden('idea-1', true)).rejects.toMatchObject({ message: 'Forbidden', status: 403 });
  });
});
