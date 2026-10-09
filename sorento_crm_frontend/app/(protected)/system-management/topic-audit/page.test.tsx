/**
 * CA-2 Topic audit page (tester-first RED). UAC U17-U23; build contract in
 * documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md.
 *
 * Page: app/(protected)/system-management/topic-audit/page.tsx (default export).
 * Service module mocked: services/topicAuditService.ts exporting
 *   getTopicAudit({ version?, show?, subject? })        -> the GET payload
 *   drawTopicAuditSample(version)                        -> { drawn }
 *   saveTopicAuditVerdict(id, { verdict, corrected_topic?, products_verdict? }) -> the row
 * Test ids the page must carry (the plan does not name any, these tests pin them):
 *   topic-audit-empty                       the empty state block (heading + hint, NO button inside)
 *   audit-tile-reviewed | -subject | -topic | -product   the four tiles
 * Verdict buttons: "Right", "Wrong topic", "Wrong subject"; a row with products has a second
 * pair named "Products right" / "Products wrong".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  cleanup,
  within,
  fireEvent,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import TopicAuditPage from './page';

const getTopicAudit = vi.fn();
const drawTopicAuditSample = vi.fn();
const saveTopicAuditVerdict = vi.fn();

vi.mock('@/services/topicAuditService', () => ({
  getTopicAudit: (...a: unknown[]) => getTopicAudit(...a),
  drawTopicAuditSample: (...a: unknown[]) => drawTopicAuditSample(...a),
  saveTopicAuditVerdict: (...a: unknown[]) => saveTopicAuditVerdict(...a),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/system-management/topic-audit',
  useSearchParams: () => new URLSearchParams(),
}));

beforeEach(() => {
  getTopicAudit.mockReset();
  drawTopicAuditSample.mockReset();
  saveTopicAuditVerdict.mockReset();
  if (!window.matchMedia) {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  }
  if (!('ResizeObserver' in window)) {
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => cleanup());

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TopicAuditPage />
    </QueryClientProvider>,
  );
}

const ROW = {
  id: 'audit-1',
  asked_at: '2026-09-10T03:00:00Z',
  message: 'ZZT do you have the grey tile in stock',
  subject: 'inventory',
  subject_label: 'Stock',
  topic: 'inventory.availability',
  topic_label: 'Availability',
  confidence: 0.93,
  products: ['ZZTAUD-A'],
  verdict: null,
  corrected_topic: null,
  corrected_topic_label: null,
  products_verdict: null,
  reviewed_by_name: null,
};

const TARGETS = { subject: 0.9, topic: 0.8, product: 0.95 };

function payload(overrides: Record<string, unknown> = {}) {
  return {
    version: 2,
    versions: [1, 2],
    sample_size: 300,
    reviewed: 2,
    rates: { subject: 1, topic: 0.5, product: 1 },
    targets: TARGETS,
    rows: [ROW],
    ...overrides,
  };
}

describe('Topic audit page: states', () => {
  it('empty state: heading and hint, the only Draw sample button is in the header', async () => {
    getTopicAudit.mockResolvedValue(
      payload({
        sample_size: 0,
        reviewed: 0,
        rows: [],
        rates: { subject: null, topic: null, product: null },
      }),
    );

    renderPage();

    const empty = await screen.findByTestId('topic-audit-empty');
    expect(
      within(empty).getByText(/No sample for taxonomy v2 yet/i),
    ).toBeTruthy();
    expect(
      within(empty).getByText(/300 labelled asks, drawn once per taxonomy version/i),
    ).toBeTruthy();
    expect(within(empty).queryAllByRole('button')).toHaveLength(0);
    expect(screen.getAllByRole('button', { name: /draw sample/i })).toHaveLength(1);
  });

  it('Draw sample calls the service with the shown version', async () => {
    getTopicAudit.mockResolvedValue(
      payload({ sample_size: 0, reviewed: 0, rows: [], rates: { subject: null, topic: null, product: null } }),
    );
    drawTopicAuditSample.mockResolvedValue({ drawn: 300 });
    renderPage();
    await screen.findByTestId('topic-audit-empty');

    fireEvent.click(screen.getByRole('button', { name: /draw sample/i }));

    await waitFor(() => expect(drawTopicAuditSample).toHaveBeenCalledTimes(1));
    expect(drawTopicAuditSample.mock.calls[0][0]).toBe(2);
  });

  it('loading: no empty state and no rows are flashed while the query is pending', () => {
    getTopicAudit.mockReturnValue(new Promise(() => {}));

    renderPage();

    expect(screen.queryByTestId('topic-audit-empty')).toBeNull();
    expect(screen.queryByText(ROW.message)).toBeNull();
  });

  it('error: the failure message is shown', async () => {
    getTopicAudit.mockRejectedValue(new Error('Topic audit is unavailable'));

    renderPage();

    expect(await screen.findByText(/Topic audit is unavailable/i)).toBeTruthy();
    expect(screen.queryByTestId('topic-audit-empty')).toBeNull();
  });
});

describe('Topic audit page: data', () => {
  it('shows the sample row with its message, subject, topic, confidence and products', async () => {
    getTopicAudit.mockResolvedValue(payload());

    renderPage();

    expect(await screen.findByText(ROW.message)).toBeTruthy();
    expect(screen.getByText('Stock')).toBeTruthy();
    expect(screen.getByText('Availability')).toBeTruthy();
    expect(screen.getByText('0.93')).toBeTruthy();
    expect(screen.getByText('ZZTAUD-A')).toBeTruthy();
    expect(screen.queryByText('audit-1')).toBeNull(); // no id on the screen
    expect(screen.queryByTestId('topic-audit-empty')).toBeNull();
  });

  it('tiles read Reviewed n / 300 and the three percentages', async () => {
    getTopicAudit.mockResolvedValue(payload());

    renderPage();
    await screen.findByText(ROW.message);

    expect(within(screen.getByTestId('audit-tile-reviewed')).getByText(/2 \/ 300/)).toBeTruthy();
    expect(within(screen.getByTestId('audit-tile-subject')).getByText('100%')).toBeTruthy();
    expect(within(screen.getByTestId('audit-tile-topic')).getByText('50%')).toBeTruthy();
    expect(within(screen.getByTestId('audit-tile-product')).getByText('100%')).toBeTruthy();
    expect(within(screen.getByTestId('audit-tile-subject')).getByText(/90%/)).toBeTruthy();
    expect(within(screen.getByTestId('audit-tile-topic')).getByText(/80%/)).toBeTruthy();
    expect(within(screen.getByTestId('audit-tile-product')).getByText(/95%/)).toBeTruthy();
  });

  it('a tile under its target renders red and one at or over it does not', async () => {
    getTopicAudit.mockResolvedValue(payload());

    renderPage();
    await screen.findByText(ROW.message);

    const below = within(screen.getByTestId('audit-tile-topic')).getByText('50%');
    const met = within(screen.getByTestId('audit-tile-subject')).getByText('100%');
    expect(below.className).toMatch(/(danger|destructive|red)/);
    expect(met.className).not.toMatch(/(danger|destructive|red)/);
  });

  it('a tile with nothing judged shows a dash, not 0%', async () => {
    getTopicAudit.mockResolvedValue(
      payload({ reviewed: 0, rates: { subject: null, topic: null, product: null } }),
    );

    renderPage();
    await screen.findByText(ROW.message);

    const tile = screen.getByTestId('audit-tile-topic');
    expect(within(tile).queryByText('0%')).toBeNull();
    expect(within(tile).getByText('-')).toBeTruthy();
  });
});

describe('Topic audit page: verdicts', () => {
  it('Right saves the verdict through the service', async () => {
    getTopicAudit.mockResolvedValue(payload());
    saveTopicAuditVerdict.mockResolvedValue({ ...ROW, verdict: 'right' });
    renderPage();
    await screen.findByText(ROW.message);

    fireEvent.click(screen.getByRole('button', { name: /^right$/i }));

    await waitFor(() => expect(saveTopicAuditVerdict).toHaveBeenCalledTimes(1));
    expect(saveTopicAuditVerdict.mock.calls[0][0]).toBe('audit-1');
    expect(saveTopicAuditVerdict.mock.calls[0][1]).toMatchObject({ verdict: 'right' });
  });

  it('a row with products has a second Right / Wrong pair for them', async () => {
    getTopicAudit.mockResolvedValue(payload());
    saveTopicAuditVerdict.mockResolvedValue({ ...ROW, verdict: 'right', products_verdict: 'wrong' });
    renderPage();
    await screen.findByText(ROW.message);

    fireEvent.click(screen.getByRole('button', { name: /products wrong/i }));

    await waitFor(() => expect(saveTopicAuditVerdict).toHaveBeenCalled());
    expect(saveTopicAuditVerdict.mock.calls[0][1]).toMatchObject({ products_verdict: 'wrong' });
    expect(screen.getByRole('button', { name: /products right/i })).toBeTruthy();
  });

  it('a row without products has no products pair', async () => {
    getTopicAudit.mockResolvedValue(payload({ rows: [{ ...ROW, products: [] }] }));
    renderPage();
    await screen.findByText(ROW.message);

    expect(screen.queryByRole('button', { name: /products (right|wrong)/i })).toBeNull();
  });

  it('every row offers the three topic verdicts', async () => {
    getTopicAudit.mockResolvedValue(payload());
    renderPage();
    await screen.findByText(ROW.message);

    expect(screen.getByRole('button', { name: /^right$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^wrong topic$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^wrong subject$/i })).toBeTruthy();
  });

  it('a failed save keeps the page up and shows the error', async () => {
    getTopicAudit.mockResolvedValue(payload());
    saveTopicAuditVerdict.mockRejectedValue(new Error('Could not save the verdict'));
    renderPage();
    await screen.findByText(ROW.message);

    fireEvent.click(screen.getByRole('button', { name: /^right$/i }));

    expect(await screen.findByText(/Could not save the verdict/i)).toBeTruthy();
    expect(screen.getByText(ROW.message)).toBeTruthy();
  });
});
