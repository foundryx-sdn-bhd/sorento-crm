import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchFileLinkContent, readFileLink } from './fileLinkService';

const fetchMock = vi.fn();

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function stubFetch() {
  vi.stubGlobal('fetch', fetchMock);
}

describe('readFileLink', () => {
  it('returns the link for a file, url included', async () => {
    stubFetch();
    const body = { filename: 'a.jpg', mime_type: 'image/jpeg', kind: 'file', url: 'https://cdn.test/a.jpg' };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(body), { status: 200 }));

    const result = await readFileLink('abc123');

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/public/file-links/abc123');
    expect(result).toEqual({ status: 'ok', link: body });
  });

  it('returns a sheet link with no url', async () => {
    stubFetch();
    const body = { filename: 'r.xlsx', mime_type: null, kind: 'sheet', url: null };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(body), { status: 200 }));

    expect(await readFileLink('abc123')).toEqual({ status: 'ok', link: body });
  });

  it('maps 404 to expired', async () => {
    stubFetch();
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'This link has expired.' }), { status: 404 }),
    );

    expect(await readFileLink('abc123')).toEqual({ status: 'expired' });
  });

  it('maps 429 to error', async () => {
    stubFetch();
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 429 }));

    expect(await readFileLink('abc123')).toEqual({ status: 'error' });
  });

  it('maps a network failure to error', async () => {
    stubFetch();
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    expect(await readFileLink('abc123')).toEqual({ status: 'error' });
  });
});

describe('fetchFileLinkContent', () => {
  it('plain-fetches the content route for the code', async () => {
    stubFetch();
    fetchMock.mockResolvedValueOnce(new Response('bytes', { status: 200 }));

    const response = await fetchFileLinkContent('abc123');

    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/v1\/public\/file-links\/abc123\/content$/);
    expect(response.ok).toBe(true);
  });
});
