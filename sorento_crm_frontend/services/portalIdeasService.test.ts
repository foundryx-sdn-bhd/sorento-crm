/**
 * IDEATION-IN-CRM Phase 2 red tests for the public track-page service (AC-H-01, AC-H-02, AC-H-05,
 * AC-H-06 from the browser's side). Plain `fetch` against the CRM public routes: the token is the
 * credential, so no Authorization header and nothing from the CRM session.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import {
  PortalIdeaNotFoundError,
  getPortalIdea,
  listPortalComments,
  postPortalComment,
} from './portalIdeasService';

const TOKEN = 'Ab3dEf9hJk2LmN0p';
const fetchMock = vi.fn();

function reply(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k: string) => (k.toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this;
    },
  } as unknown as Response;
}

function lastCall(n = 0) {
  expect(fetchMock, 'the service must call fetch (it still delegates to the mock)').toHaveBeenCalled();
  const [url, init] = fetchMock.mock.calls[n] as [unknown, RequestInit | undefined];
  const headers = new Headers((init?.headers as HeadersInit | undefined) ?? {});
  return {
    url: String(url),
    method: (init?.method ?? 'GET').toUpperCase(),
    headers,
    body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('getPortalIdea', () => {
  it('GETs the CRM public route with no credentials and maps the ss page', async () => {
    fetchMock.mockResolvedValue(
      reply(200, {
        title: 'Faster quotes',
        status: 'In review',
        ideaNumber: 'IDEA-0042',
        statusColor: 'info',
        submittedAt: '2026-09-30T08:15:00Z',
        submitterFirstName: 'Jane',
        upvotes: 7,
        problem: 'Quotes take too long',
        proposedSolution: 'Templates',
        impact: null,
        department: 'Sales',
      }),
    );
    const idea = await getPortalIdea(TOKEN);
    const c = lastCall();
    expect(c.url).toContain(`/api/v1/public/portal/ideas/${TOKEN}`);
    expect(c.url).not.toContain('/comments');
    expect(c.method).toBe('GET');
    expect(c.headers.get('authorization')).toBeNull();
    expect(idea).toMatchObject({
      title: 'Faster quotes',
      statusLabel: 'In review',
      ideaNumber: 'IDEA-0042',
      upvotes: 7,
      problem: 'Quotes take too long',
      proposedSolution: 'Templates',
      submitterFirstName: 'Jane',
    });
  });

  it('a 404 is a PortalIdeaNotFoundError', async () => {
    fetchMock.mockResolvedValue(reply(404, { detail: 'Not found.' }));
    await expect(getPortalIdea(TOKEN)).rejects.toBeInstanceOf(PortalIdeaNotFoundError);
  });

  it('any other failure is an Error with the server message, not a not-found', async () => {
    fetchMock.mockResolvedValue(reply(502, { detail: "The Ideas workspace isn't reachable right now." }));
    const error = await getPortalIdea(TOKEN).catch((e) => e);
    expect(error).toBeInstanceOf(Error);
    expect(error).not.toBeInstanceOf(PortalIdeaNotFoundError);
    expect(error.message).toBe("The Ideas workspace isn't reachable right now.");
  });
});

describe('listPortalComments', () => {
  it('GETs /comments and maps isDeleted to deleted', async () => {
    fetchMock.mockResolvedValue(
      reply(200, [
        {
          id: 'c-1',
          parentId: null,
          authorName: 'Alex',
          isSubmitter: false,
          body: 'Hi',
          isDeleted: false,
          createdAt: '2026-10-01T09:00:00Z',
          editedAt: null,
        },
        {
          id: 'c-2',
          parentId: 'c-1',
          authorName: 'Jane',
          isSubmitter: true,
          body: null,
          isDeleted: true,
          createdAt: '2026-10-01T10:00:00Z',
          editedAt: null,
        },
      ]),
    );
    const out = await listPortalComments(TOKEN);
    const c = lastCall();
    expect(c.url).toContain(`/api/v1/public/portal/ideas/${TOKEN}/comments`);
    expect(c.method).toBe('GET');
    expect(c.headers.get('authorization')).toBeNull();
    expect(out[0]).toMatchObject({ id: 'c-1', authorName: 'Alex', isSubmitter: false, deleted: false });
    expect(out[1]).toMatchObject({ id: 'c-2', parentId: 'c-1', isSubmitter: true, deleted: true });
  });
});

describe('postPortalComment', () => {
  it('POSTs {body} and nothing else: no name, no email, no author fields', async () => {
    fetchMock.mockResolvedValue(
      reply(201, {
        id: 'c-9',
        parentId: null,
        authorName: 'Jane',
        isSubmitter: true,
        body: 'Thanks',
        isDeleted: false,
        createdAt: '2026-10-01T09:00:00Z',
        editedAt: null,
      }),
    );
    const created = await postPortalComment(TOKEN, 'Thanks');
    const c = lastCall();
    expect(c.url).toContain(`/api/v1/public/portal/ideas/${TOKEN}/comments`);
    expect(c.method).toBe('POST');
    expect(c.headers.get('authorization')).toBeNull();
    expect(c.body).toEqual({ body: 'Thanks' });
    expect(created).toMatchObject({ id: 'c-9', isSubmitter: true, authorName: 'Jane', deleted: false });
  });

  it('a 429 surfaces the server message', async () => {
    fetchMock.mockResolvedValue(reply(429, { detail: 'Too many comments. Try again later.' }));
    await expect(postPortalComment(TOKEN, 'x')).rejects.toThrow('Too many comments. Try again later.');
  });
});
