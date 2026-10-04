/**
 * Parser-per-audience, AC-PA-3. Phase 2 red test: the Field reveals card names the
 * chatbot prompt blocks a key also removes (`prompt_blocks` from field-reveal-keys).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactFieldRevealsSection from './ContactFieldRevealsSection';

const apiFetch = vi.fn();

vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function ok(body: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
}

const KEYS = [
  {
    key: 'sales_orders.sales_report',
    label: 'Sales report',
    prompt_blocks: ['SALES REPORT', 'SALES ANALYSIS', 'TOP SELLING'],
  },
  { key: 'inventory.sellable', label: 'Sellable stock', prompt_blocks: [] },
];

function renderSection() {
  apiFetch.mockImplementation((url: string) => {
    if (url.includes('/field-reveal-keys')) return ok({ items: KEYS });
    return ok({ granted: [] });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactFieldRevealsSection contactId="c1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiFetch.mockReset();
});
afterEach(() => cleanup());

describe('ContactFieldRevealsSection prompt blocks (AC-PA-3)', () => {
  it('prints the blocks a key also removes, joined with a comma', async () => {
    renderSection();
    await screen.findByText('Sales report');
    expect(
      screen.getByText(
        'Also removes from the chatbot prompt: SALES REPORT, SALES ANALYSIS, TOP SELLING',
      ),
    ).toBeInTheDocument();
  });

  it('prints the line once: a key with no blocks shows none', async () => {
    renderSection();
    await screen.findByText('Sellable stock');
    expect(screen.getAllByText(/also removes from the chatbot prompt/i)).toHaveLength(1);
  });
});
