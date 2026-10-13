import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';

const DASH = '—';

const summary = vi.fn();
const topUsers = vi.fn();
const topContacts = vi.fn();
const recent = vi.fn();

const ok = (data: unknown) => ({ data, isLoading: false, isError: false });

vi.mock('@/components/common/container', () => ({ Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock('@/components/common/PageHeader', () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('../hooks/useAIUsage', () => ({
  useUsageSummary: () => summary(),
  useUsageByDay: () => ok([]),
  useTopUsers: () => topUsers(),
  useTopContacts: () => topContacts(),
  useRecentQueries: () => recent(),
  useQueryDetail: () => ({ data: undefined, isLoading: false, isError: false }),
}));

import AIUsagePage from './page';

const SUMMARY = {
  total_messages: 10,
  prompt_tokens: 1000,
  completion_tokens: 100,
  total_tokens: 1100,
  cost_usd: 0.0372,
  unpriced_calls: 6,
};

function query(over: Record<string, unknown>) {
  return {
    message_id: null,
    user_name: 'Ali',
    contact_name: null,
    contact_phone: null,
    feature: 'chatbot_parser',
    form_key: null,
    query_preview: 'stock for X',
    response_time_ms: 120,
    tokens: 100,
    created_at: '2026-10-13T14:23:33',
    model: 'gpt-5.4-mini',
    cost_usd: 0.00482715,
    priced: true,
    cached_price_missing: false,
    ...over,
  };
}

beforeEach(() => {
  summary.mockReset().mockReturnValue(ok(SUMMARY));
  topUsers.mockReset().mockReturnValue(ok([]));
  topContacts.mockReset().mockReturnValue(ok([]));
  recent.mockReset().mockReturnValue(ok([]));
});
afterEach(() => cleanup());

/** The summary card is the "Cost (USD)" label that is not a table header. */
function summaryCard(): HTMLElement {
  const label = screen.getAllByText('Cost (USD)').find((el) => el.closest('table') === null);
  expect(label, 'a Cost (USD) summary card').toBeDefined();
  let node: HTMLElement = label as HTMLElement;
  while (node.parentElement && !node.textContent?.match(/\$|—/)) node = node.parentElement;
  return node;
}

describe('Usage page cost (UC-3)', () => {
  it('Recent queries has a Cost (USD) header and prices a row to 4 decimals', () => {
    recent.mockReturnValue(ok([query({})]));
    render(<AIUsagePage />);
    const recentTable = screen.getByText('stock for X').closest('table') as HTMLElement;
    expect(within(recentTable).getByText('Cost (USD)')).toBeInTheDocument();
    expect(within(recentTable).getByText('$0.0048')).toBeInTheDocument();
  });

  it('an unpriced row shows a dash with a "No price for <model>" tooltip', () => {
    recent.mockReturnValue(
      ok([query({ model: 'gemini-3.5-flash', cost_usd: null, priced: false })]),
    );
    render(<AIUsagePage />);
    const cell = screen.getByTitle('No price for gemini-3.5-flash');
    expect(cell.textContent).toBe(DASH);
  });

  it('a cached-price-missing row shows a dash with the cached tooltip', () => {
    recent.mockReturnValue(
      ok([query({ model: 'zzt-model', cost_usd: null, priced: false, cached_price_missing: true })]),
    );
    render(<AIUsagePage />);
    expect(screen.getByTitle('No cached-input price for zzt-model').textContent).toBe(DASH);
  });
});

describe('Usage page time (TZ-1)', () => {
  it('renders created_at in Malaysia time', () => {
    recent.mockReturnValue(ok([query({})]));
    render(<AIUsagePage />);
    expect(screen.getByText('13/10/2026, 10:23 pm')).toBeInTheDocument();
  });
});

describe('Usage page summary (UC-4)', () => {
  it('shows the cost card and how many calls were not priced', () => {
    render(<AIUsagePage />);
    expect(summaryCard().textContent).toContain('$0.0372');
    expect(screen.getByText('6 calls not priced')).toBeInTheDocument();
  });

  it('shows no "not priced" line when every call is priced', () => {
    summary.mockReturnValue(ok({ ...SUMMARY, unpriced_calls: 0 }));
    render(<AIUsagePage />);
    expect(screen.queryByText(/calls? not priced/)).not.toBeInTheDocument();
  });

  it('shows a dash when every call is unpriced', () => {
    summary.mockReturnValue(ok({ ...SUMMARY, cost_usd: 0, unpriced_calls: 10 }));
    render(<AIUsagePage />);
    expect(summaryCard().textContent).toContain(DASH);
    expect(summaryCard().textContent).not.toContain('$0.0000');
    expect(screen.getByText('10 calls not priced')).toBeInTheDocument();
  });
});

describe('Usage page top tables (UC-5)', () => {
  it('Top users has a Cost (USD) column: 4 dp, dash when all calls are unpriced', () => {
    topUsers.mockReturnValue(
      ok([
        { user_id: 'u1', name: 'Priced Person', messages: 4, tokens: 400, cost_usd: 1.23456, unpriced_calls: 0 },
        { user_id: 'u2', name: 'Unpriced Person', messages: 3, tokens: 300, cost_usd: 0, unpriced_calls: 3 },
      ]),
    );
    render(<AIUsagePage />);
    const table = screen.getByText('Priced Person').closest('table') as HTMLElement;
    expect(within(table).getByText('Cost (USD)')).toBeInTheDocument();
    const priced = screen.getByText('Priced Person').closest('tr') as HTMLElement;
    const unpriced = screen.getByText('Unpriced Person').closest('tr') as HTMLElement;
    expect(within(priced).getByText('$1.2346')).toBeInTheDocument();
    expect(within(unpriced).getByText(DASH)).toBeInTheDocument();
    expect(within(unpriced).queryByText('$0.0000')).not.toBeInTheDocument();
  });

  it('Top contacts has a Cost (USD) column: 4 dp, dash when all calls are unpriced', () => {
    topContacts.mockReturnValue(
      ok([
        { contact_id: 'c1', name: 'Priced Contact', phone_number: '+60100000001', messages: 2, tokens: 20, cost_usd: 2.5, unpriced_calls: 0 },
        { contact_id: 'c2', name: 'Unpriced Contact', phone_number: '+60100000002', messages: 2, tokens: 20, cost_usd: 0, unpriced_calls: 2 },
      ]),
    );
    render(<AIUsagePage />);
    const table = screen.getByTestId('ai-usage-top-contacts');
    expect(within(table).getByText('Cost (USD)')).toBeInTheDocument();
    const priced = screen.getByText('Priced Contact').closest('tr') as HTMLElement;
    const unpriced = screen.getByText('Unpriced Contact').closest('tr') as HTMLElement;
    expect(within(priced).getByText('$2.5000')).toBeInTheDocument();
    expect(within(unpriced).getByText(DASH)).toBeInTheDocument();
  });
});
