/**
 * ContactBrandScopeSection - the "Brands" card on the contact Profile tab (CONTACT-BRAND-SCOPE,
 * AC-4). Phase 2 RED: the component, its hook and its service do not exist yet.
 *
 *   - empty (unscoped) shows the "All brands" placeholder
 *   - a scoped contact shows its brand NAMES, never an id
 *   - Save PUTs the picked ids to /api/v1/user-management/contacts/{id}/brands
 *   - clearing every brand and saving sends `[]` (the server stores NULL = all brands)
 *   - a failed save toasts the server message
 *
 * Mocked at the `apiFetch` boundary so the card, hook and service are all real; the shared
 * multi-select is a deterministic stub (the technique StockVisibilitySection.test.tsx uses)
 * that exposes its placeholder, its selected ids and whether it can be cleared.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  custom: vi.fn(),
  message: vi.fn(),
  dismiss: vi.fn(),
}));
vi.mock('@/lib/toast', () => ({ toast: toastMock }));

const apiFetchMock = vi.fn();
vi.mock('@/lib/api', () => ({
  apiFetch: (...a: unknown[]) => apiFetchMock(...a),
}));

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: ({
    value,
    onChange,
    options,
    placeholder,
    clearable,
  }: {
    value: string[];
    onChange: (v: string[]) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
    clearable?: boolean;
  }) => (
    <div
      data-testid="brands-picker"
      data-placeholder={placeholder ?? ''}
      data-value={value.join(',')}
      data-clearable={String(clearable)}
    >
      {value.length === 0 ? <span>{placeholder}</span> : null}
      {(options ?? [])
        .filter((o) => value.includes(o.value))
        .map((o) => (
          <span key={o.value} data-testid="brand-chip">
            {o.label}
          </span>
        ))}
      {(options ?? []).map((o) => (
        <button key={o.value} type="button" onClick={() => onChange([...value, o.value])}>
          pick {o.label}
        </button>
      ))}
      <button type="button" onClick={() => onChange([])}>
        clear brands
      </button>
    </div>
  ),
}));

const MOCHA = { id: '11111111-1111-4111-8111-111111111111', brand_name: 'Mocha' };
const SORENTO = { id: '22222222-2222-4222-8222-222222222222', brand_name: 'Sorento' };

function ok(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body, headers: new Headers() } as unknown as Response;
}

function serve(saved: { id: string; brand_name: string }[]) {
  apiFetchMock.mockImplementation(async (url: string, init?: { method?: string; body?: string }) => {
    if (String(url).includes('/master-data/brands/select')) return ok([MOCHA, SORENTO]);
    if (String(url).includes('/contacts/c1/brands')) {
      if (init?.method === 'PUT') {
        const ids = JSON.parse(init.body ?? '{}').brand_ids as string[];
        return ok({ brand_ids: ids, brands: [MOCHA, SORENTO].filter((b) => ids.includes(b.id)) });
      }
      return ok({ brand_ids: saved.map((b) => b.id), brands: saved });
    }
    return ok({});
  });
}

import ContactBrandScopeSection from './ContactBrandScopeSection';

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactBrandScopeSection contactId="c1" />
    </QueryClientProvider>,
  );
}

function putCalls() {
  return apiFetchMock.mock.calls.filter(([, init]) => (init as { method?: string } | undefined)?.method === 'PUT');
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe('ContactBrandScopeSection', () => {
  it('reads "All brands" when the contact has none set, and the picker can be cleared', async () => {
    serve([]);
    renderCard();
    const picker = await screen.findByTestId('brands-picker');
    expect(picker).toHaveAttribute('data-placeholder', 'All brands');
    expect(picker).toHaveAttribute('data-clearable', 'true');
    expect(picker).toHaveAttribute('data-value', '');
  });

  it('shows the saved brands by name and never an id', async () => {
    serve([MOCHA]);
    renderCard();
    await waitFor(() => expect(screen.getAllByTestId('brand-chip').map((c) => c.textContent)).toEqual(['Mocha']));
    expect(document.body.textContent).not.toContain(MOCHA.id);
  });

  it('Save sends the picked brand ids with PUT', async () => {
    serve([]);
    renderCard();
    fireEvent.click(await screen.findByText('pick Mocha'));
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(putCalls()).toHaveLength(1));
    const [url, init] = putCalls()[0] as [string, { body: string }];
    expect(url).toContain('/api/v1/user-management/contacts/c1/brands');
    expect(JSON.parse(init.body)).toEqual({ brand_ids: [MOCHA.id] });
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
  });

  it('clearing every brand and saving sends an empty list (all brands)', async () => {
    serve([MOCHA, SORENTO]);
    renderCard();
    await waitFor(() => expect(screen.getAllByTestId('brand-chip')).toHaveLength(2));
    fireEvent.click(screen.getByText('clear brands'));
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(putCalls()).toHaveLength(1));
    expect(JSON.parse((putCalls()[0][1] as { body: string }).body)).toEqual({ brand_ids: [] });
  });

  it('a rejected save toasts the server message', async () => {
    apiFetchMock.mockImplementation(async (url: string, init?: { method?: string }) => {
      if (String(url).includes('/master-data/brands/select')) return ok([MOCHA]);
      if (init?.method === 'PUT') {
        return {
          ok: false,
          status: 422,
          headers: new Headers({ 'content-type': 'application/json' }),
          json: async () => ({ message: 'Unknown brand id' }),
          text: async () => JSON.stringify({ message: 'Unknown brand id' }),
        } as unknown as Response;
      }
      return ok({ brand_ids: [], brands: [] });
    });
    renderCard();
    fireEvent.click(await screen.findByText('pick Mocha'));
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining('Unknown brand id')));
  });
});
