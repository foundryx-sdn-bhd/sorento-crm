/**
 * LINK-XLSX-VIEWER page (AC-F1..F6): `/f/{code}` opens a sheet straight into the full-screen
 * viewer. fetch and both parser entry points are mocked (so either the worker helper or the
 * main-thread parser satisfies the page). AC-F7 (375/1280 layout) is browser-verified.
 */
import React, { Suspense } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const parse = vi.hoisted(() => vi.fn());
vi.mock('@/lib/sheet-viewer/parseWorkbook', () => ({ parseWorkbook: parse }));
vi.mock('@/lib/sheet-viewer/parseWorkbookInWorker', () => ({ parseWorkbookInWorker: parse }));

import FileLinkPage from './page';

const SHEET_LINK = { filename: 'Packing List.xlsx', mime_type: null, kind: 'sheet', url: null };
const MODEL = {
  sheets: [
    {
      title: 'Packing List',
      columns: [],
      rows: [
        ['Code', 'Qty'],
        ['A-1', '40.00'],
      ],
      excelChrome: true,
    },
  ],
};

const fetchMock = vi.fn();

function respond(link: unknown, linkStatus = 200, contentStatus = 200) {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/content')) {
      if (contentStatus === 413) {
        return new Response(JSON.stringify({ detail: 'This file is too big to show here.' }), {
          status: 413,
        });
      }
      return new Response(new Uint8Array([1, 2, 3]), { status: contentStatus });
    }
    return new Response(JSON.stringify(link), { status: linkStatus });
  });
}

async function open() {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(
      <Suspense fallback={null}>
        <FileLinkPage params={Promise.resolve({ code: 'ABC1234567' })} />
      </Suspense>,
    );
  });
  return view;
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  parse.mockResolvedValue(MODEL);
});
afterEach(() => {
  fetchMock.mockReset();
  parse.mockReset();
  vi.unstubAllGlobals();
});

describe('AC-F1 opens straight into the viewer', () => {
  it('shows filename and Download in the top bar, the grid, no View button, no dialog', async () => {
    respond(SHEET_LINK);
    await open();
    await waitFor(() => expect(screen.getByRole('grid')).toBeInTheDocument());
    expect(screen.getByText('Packing List.xlsx')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'View' })).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByText('40.00')).toBeInTheDocument();
  });
});

describe('AC-F2 too big', () => {
  it('shows the too-big line and a Download button on a 413', async () => {
    respond(SHEET_LINK, 200, 413);
    await open();
    expect(await screen.findByText('This file is too big to show here.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download/i })).toBeInTheDocument();
    expect(screen.queryByRole('grid')).not.toBeInTheDocument();
  });
});

describe('AC-F3 cannot be shown', () => {
  it('shows the cannot-be-shown line and Download when parsing fails or times out', async () => {
    respond(SHEET_LINK);
    parse.mockRejectedValue(new Error('timed out'));
    await open();
    expect(await screen.findByText('This file cannot be shown here.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download/i })).toBeInTheDocument();
  });
});

describe('AC-F4 download', () => {
  it('fetches content?download=1 and saves under the link filename', async () => {
    respond(SHEET_LINK);
    await open();
    await waitFor(() => expect(screen.getByRole('grid')).toBeInTheDocument());
    const saved: string[] = [];
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} }));
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        saved.push(this.download);
      });
    fetchMock.mockClear();
    await userEvent.click(screen.getByRole('button', { name: /download/i }));
    await waitFor(() => expect(saved).toEqual(['Packing List.xlsx']));
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => /\/content\?download=1$/.test(u))).toBe(true);
    click.mockRestore();
  });
});

describe('AC-F5 truncated sheet', () => {
  it('says how many rows are shown and points to Download', async () => {
    respond(SHEET_LINK);
    parse.mockResolvedValue({
      sheets: [{ ...MODEL.sheets[0], truncatedRows: true, totalRows: 6000 }],
    });
    await open();
    expect(
      await screen.findByText('Showing first 5,000 rows. Download for the full sheet.'),
    ).toBeInTheDocument();
  });
});

describe('AC-F6 unchanged states', () => {
  it('keeps the expired wording', async () => {
    respond({ detail: 'This link has expired.' }, 404);
    await open();
    expect(
      await screen.findByText('This link has expired. Ask us on WhatsApp for a new one.'),
    ).toBeInTheDocument();
  });

  it('keeps the error wording and Retry', async () => {
    respond({}, 500);
    await open();
    expect(await screen.findByText('Something went wrong. Please try again.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('still redirects a file link to its https url', async () => {
    const replace = vi.fn();
    const original = window.location;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...original, replace, port: '', hostname: 'localhost', protocol: 'http:' },
    });
    respond({ filename: 'a.jpg', mime_type: 'image/jpeg', kind: 'file', url: 'https://cdn.test/a.jpg?sig=1' });
    await open();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('https://cdn.test/a.jpg?sig=1'));
    Object.defineProperty(window, 'location', { configurable: true, value: original });
  });
});

describe('unmount cancels parsing', () => {
  it('passes an AbortSignal to the parser and aborts it when the page unmounts', async () => {
    respond(SHEET_LINK);
    let received: unknown;
    parse.mockImplementation(async (_b: ArrayBuffer, _n: string, signal?: AbortSignal) => {
      received = signal;
      return MODEL;
    });
    const view = await open();
    await waitFor(() => expect(screen.getByRole('grid')).toBeInTheDocument());
    expect(received).toBeInstanceOf(AbortSignal);
    expect((received as AbortSignal).aborted).toBe(false);
    view.unmount();
    expect((received as AbortSignal).aborted).toBe(true);
  });
});

describe('content fetch lifecycle', () => {
  function stallContent() {
    const inits: (RequestInit | undefined)[] = [];
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/content')) {
        inits.push(init);
        return { ok: true, status: 200, arrayBuffer: () => new Promise(() => {}) } as unknown as Response;
      }
      return new Response(JSON.stringify(SHEET_LINK), { status: 200 });
    });
    return inits;
  }

  it('gives up on a body that never arrives: cannot-be-shown after 30 s, no spinner', async () => {
    vi.useFakeTimers();
    try {
      stallContent();
      await open();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(screen.getByText('This file cannot be shown here.')).toBeInTheDocument();
      expect(screen.queryByLabelText('Loading')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('passes an AbortSignal to the content fetch and aborts it on unmount', async () => {
    const inits = stallContent();
    const view = await open();
    await waitFor(() => expect(inits.length).toBeGreaterThan(0));
    const signal = inits[0]?.signal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect((signal as AbortSignal).aborted).toBe(false);
    view.unmount();
    expect((signal as AbortSignal).aborted).toBe(true);
  });
});

describe('idle download limit', () => {
  function streamed(chunks: number, every: number, stallAfter = false) {
    let sent = 0;
    const read = () =>
      new Promise<{ done: boolean; value?: Uint8Array }>((resolve) => {
        if (sent >= chunks) {
          if (stallAfter) return; // never resolves
          return resolve({ done: true });
        }
        setTimeout(() => {
          sent += 1;
          resolve({ done: false, value: new Uint8Array([1, 2, 3, 4]) });
        }, every);
      });
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes('/content')) {
        return {
          ok: true,
          status: 200,
          body: { getReader: () => ({ read, cancel: async () => {}, releaseLock: () => {} }) },
          arrayBuffer: () => new Promise(() => {}),
        } as unknown as Response;
      }
      return new Response(JSON.stringify(SHEET_LINK), { status: 200 });
    });
  }

  it('a slow but steady download (60 s, a chunk every 10 s) succeeds', async () => {
    vi.useFakeTimers();
    try {
      streamed(6, 10_000);
      await open();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(65_000);
      });
      expect(screen.queryByText('This file cannot be shown here.')).not.toBeInTheDocument();
      expect(parse).toHaveBeenCalled();
      const bytes = parse.mock.calls[0][0] as ArrayBuffer;
      expect(bytes.byteLength).toBe(24);
    } finally {
      vi.useRealTimers();
    }
  });

  it('a stall after the first chunk gives up 30 s later', async () => {
    vi.useFakeTimers();
    try {
      streamed(1, 1_000, true);
      await open();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(31_500);
      });
      expect(screen.getByText('This file cannot be shown here.')).toBeInTheDocument();
      expect(parse).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
