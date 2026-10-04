/**
 * IDEATION-IN-CRM Phase 2 (red until the mock delegation is replaced): every Ideas service
 * function hits the gateway path and method the UAC documents (AC-A-08, AC-C-05, AC-D-04).
 *
 * The seam is `apiFetch` from `@/lib/api` (what every real feature service uses); nothing here
 * may reach `ideasService.mock.ts`. A function still delegating to the mock never calls
 * `apiFetch`, which is exactly how these fail today.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import * as service from './ideasService';

const BASE = '/api/v1/ideation';

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

/** [path, method, parsed JSON body or undefined, raw init] of call `n`. */
function call(n = 0) {
  const [url, init] = apiFetch.mock.calls[n] as [string, RequestInit | undefined];
  const parsed = new URL(url, 'http://crm.test');
  return {
    path: parsed.pathname,
    params: parsed.searchParams,
    method: (init?.method ?? 'GET').toUpperCase(),
    body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    rawBody: init?.body,
  };
}

const IDEA = { id: 'idea-1', title: 'Faster quotes' };

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue(ok(IDEA));
});

describe('reads', () => {
  it('listIdeas: GET /ideas with the status as `filter` and the search as `search`', async () => {
    apiFetch.mockResolvedValue(ok([]));
    await service.listIdeas({ query: 'quote', status: 'archived' });
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas`, 'GET']);
    expect(c.params.get('filter')).toBe('archived');
    expect(c.params.get('search')).toBe('quote');
    expect(c.params.has('query')).toBe(false);
  });

  it('listIdeas: no filter and no query when neither is set', async () => {
    apiFetch.mockResolvedValue(ok([]));
    await service.listIdeas({});
    const c = call();
    expect(c.params.get('filter') || '').toBe('');
    expect(c.params.get('search') || '').toBe('');
  });

  it('listIdeas: mine true adds mine=true to the query', async () => {
    apiFetch.mockResolvedValue(ok([]));
    await service.listIdeas({ mine: true });
    expect(call().params.get('mine')).toBe('true');
  });

  it('listIdeas: no mine param when mine is unset or false', async () => {
    apiFetch.mockResolvedValue(ok([]));
    await service.listIdeas({});
    await service.listIdeas({ mine: false });
    expect(call(0).params.has('mine')).toBe(false);
    expect(call(1).params.has('mine')).toBe(false);
  });

  it('getBoard: GET /ideas/board', async () => {
    apiFetch.mockResolvedValue(ok({ columns: [] }));
    await service.getBoard();
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/board`, 'GET']);
  });

  it('getIdea: GET /ideas/{id}', async () => {
    await service.getIdea('idea-1');
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-1`, 'GET']);
  });

  it('getMergedChildren: GET /ideas/{id}/merged', async () => {
    apiFetch.mockResolvedValue(ok([]));
    await service.getMergedChildren('idea-1');
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-1/merged`, 'GET']);
  });

  it('listComments: GET /ideas/{id}/comments', async () => {
    apiFetch.mockResolvedValue(ok([]));
    await service.listComments('idea-1');
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-1/comments`, 'GET']);
  });

  it('a gateway refusal is thrown as an Error carrying the server message', async () => {
    apiFetch.mockResolvedValue(fail(502, "The Ideas workspace isn't reachable right now."));
    await expect(service.getIdea('idea-1')).rejects.toThrow("The Ideas workspace isn't reachable right now.");
  });

  it('exposes Archived in the status filter options', () => {
    expect(service.IDEA_STATUS_FILTER_OPTIONS).toEqual(
      expect.arrayContaining([expect.objectContaining({ value: 'archived', label: 'Archived' })]),
    );
  });
});

describe('capture (AC-C-05)', () => {
  it('createIdea: POST /ideas with the capture fields, no product, no files in the JSON', async () => {
    await service.createIdea({
      problem: 'Quotes are slow',
      proposedSolution: 'Templates',
      impact: 'A day a week',
      department: 'Sales',
    });
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas`, 'POST']);
    expect(c.body).toMatchObject({
      problem: 'Quotes are slow',
      proposedSolution: 'Templates',
      impact: 'A day a week',
      department: 'Sales',
    });
    expect(c.body).not.toHaveProperty('productId');
    expect(c.body).not.toHaveProperty('files');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('createIdea: then uploads each dropped file to the NEW idea, in order', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'new-7' }, 201));
    const a = new File(['a'], 'a.png', { type: 'image/png' });
    const b = new File(['b'], 'b.pdf', { type: 'application/pdf' });
    await service.createIdea({ problem: 'Slow', files: [a, b] });

    expect(apiFetch).toHaveBeenCalledTimes(3);
    expect([call(0).path, call(0).method]).toEqual([`${BASE}/ideas`, 'POST']);
    for (const [n, file] of [
      [1, a],
      [2, b],
    ] as const) {
      const c = call(n);
      expect([c.path, c.method]).toEqual([`${BASE}/ideas/new-7/attachments`, 'POST']);
      expect(c.rawBody).toBeInstanceOf(FormData);
      expect((c.rawBody as FormData).get('file')).toBe(file);
    }
  });
});

describe('writes', () => {
  it('updateIdea: PATCH /ideas/{id} with the edit fields', async () => {
    await service.updateIdea('idea-1', { problem: 'Edited', impact: null });
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1`, 'PATCH']);
    expect(c.body).toEqual({ problem: 'Edited', impact: null });
  });

  it('voteIdea: POST /ideas/{id}/vote and the body is always {dir: "up"}', async () => {
    await service.voteIdea('idea-1');
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1/vote`, 'POST']);
    expect(c.body).toEqual({ dir: 'up' });
  });

  it('moveIdeaToStatus: POST /ideas/{id}/status with {toStatusId}', async () => {
    await service.moveIdeaToStatus('idea-1', 'st-9');
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1/status`, 'POST']);
    expect(c.body).toEqual({ toStatusId: 'st-9' });
  });

  it('restoreIdea (AC-D-02): POST /ideas/{id}/status with the transition target, no hardcoded status key', async () => {
    await service.restoreIdea('idea-1', 'st-9');
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1/status`, 'POST']);
    expect(c.body).toEqual({ toStatusId: 'st-9' });
  });

  it('a gateway refusal carries its HTTP status so the page can tell 404 from the rest (AC-D-06)', async () => {
    apiFetch.mockResolvedValue(fail(404, 'Idea not found.'));
    await expect(service.getIdea('x')).rejects.toMatchObject({ status: 404 });
  });

  it('reorderIdeas: PUT /ideas/reorder with {orderedIds}', async () => {
    apiFetch.mockResolvedValue(ok([]));
    await service.reorderIdeas(['b', 'a']);
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/reorder`, 'PUT']);
    expect(c.body).toEqual({ orderedIds: ['b', 'a'] });
  });

  it('mergeIdeas: POST /ideas/merge with {survivorId, ideaIds}', async () => {
    await service.mergeIdeas({ survivorId: 'idea-1', ideaIds: ['idea-2'] });
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/merge`, 'POST']);
    expect(c.body).toEqual({ survivorId: 'idea-1', ideaIds: ['idea-2'] });
  });

  it('unmergeIdea: POST /ideas/{id}/unmerge', async () => {
    apiFetch.mockResolvedValue(ok([IDEA]));
    await service.unmergeIdea('idea-2');
    expect([call().path, call().method]).toEqual([`${BASE}/ideas/idea-2/unmerge`, 'POST']);
  });

  it('promoteIdea: POST /ideas/promote with {ideaIds: [id], title} (one click, no extra fields)', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'br-1' }, 201));
    await service.promoteIdea('idea-1', { title: 'Faster quotes' });
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/promote`, 'POST']);
    expect(c.body).toEqual({ ideaIds: ['idea-1'], title: 'Faster quotes' });
  });

  it('promoteIdea: an ss 403 reaches the caller as the Error message', async () => {
    apiFetch.mockResolvedValue(fail(403, 'This user has no Business Requirements access in the Ideas workspace.'));
    await expect(service.promoteIdea('idea-1', { title: 'T' })).rejects.toThrow(
      'This user has no Business Requirements access in the Ideas workspace.',
    );
  });

  it('uploadAttachment: POST /ideas/{id}/attachments as multipart with the file', async () => {
    const file = new File(['x'], 'brief.txt', { type: 'text/plain' });
    apiFetch.mockResolvedValue(ok({ id: 'att-1', kind: 'file', name: 'brief.txt' }, 201));
    await service.uploadAttachment('idea-1', file);
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1/attachments`, 'POST']);
    expect(c.rawBody).toBeInstanceOf(FormData);
    expect((c.rawBody as FormData).get('file')).toBe(file);
  });
});

describe('comments (AC-E-02)', () => {
  it('addComment: POST /ideas/{id}/comments with {body, parentId}', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'c-1' }, 201));
    await service.addComment('idea-1', { body: 'Agreed', parentId: 'c-root' });
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1/comments`, 'POST']);
    expect(c.body).toMatchObject({ body: 'Agreed', parentId: 'c-root' });
  });

  it('addComment: a top-level comment carries no parent', async () => {
    apiFetch.mockResolvedValue(ok({ id: 'c-1' }, 201));
    await service.addComment('idea-1', { body: 'Agreed' });
    expect(call().body.parentId ?? null).toBeNull();
  });

  it('editComment: PATCH /ideas/{id}/comments/{cid} with {body}', async () => {
    await service.editComment('idea-1', 'c-1', 'Agreed, sorry');
    const c = call();
    expect([c.path, c.method]).toEqual([`${BASE}/ideas/idea-1/comments/c-1`, 'PATCH']);
    expect(c.body).toEqual({ body: 'Agreed, sorry' });
  });

  it('listComments maps the ss comment to the FE comment (deleted, canEdit, canDelete, edited time)', async () => {
    apiFetch.mockResolvedValue(
      ok([
        {
          id: 'c-1',
          ideaId: 'idea-1',
          parentId: null,
          authorName: 'Alex Staff',
          authorKind: 'embed',
          body: 'Looks good',
          isDeleted: false,
          isMine: true,
          canEdit: true,
          canDelete: true,
          createdAt: '2026-10-01T09:00:00Z',
          editedAt: '2026-10-01T10:00:00Z',
        },
        {
          id: 'c-2',
          ideaId: 'idea-1',
          parentId: 'c-1',
          authorName: 'Pat',
          authorKind: 'embed',
          body: null,
          isDeleted: true,
          isMine: false,
          canEdit: false,
          canDelete: false,
          createdAt: '2026-10-01T11:00:00Z',
          editedAt: null,
        },
      ]),
    );
    const out = await service.listComments('idea-1');
    expect(out[0]).toMatchObject({
      id: 'c-1',
      parentId: null,
      authorName: 'Alex Staff',
      body: 'Looks good',
      deleted: false,
      canEdit: true,
      canDelete: true,
      editedAt: '2026-10-01T10:00:00Z',
    });
    expect(out[1]).toMatchObject({ id: 'c-2', parentId: 'c-1', deleted: true, canEdit: false, canDelete: false });
    expect(out[1].body ?? '').toBe('');
  });
});

describe('Archive and Delete are NOT direct service calls', () => {
  it('the service no longer exports archiveIdea / deleteIdea / deleteComment (they are server-deferred pending actions)', () => {
    const exported = service as Record<string, unknown>;
    expect(exported.archiveIdea).toBeUndefined();
    expect(exported.deleteIdea).toBeUndefined();
    expect(exported.deleteComment).toBeUndefined();
  });
});
