/**
 * The chatbot retry save, asserted at the WIRE (AC-804, browser evidence 6 Sep 2026).
 *
 * Every other test in this folder mocks `../services/respondWorkspaceService`, so they
 * prove what the component hands the service and nothing about what leaves the browser.
 * The evidence run found the gap that leaves: blanking the retry URL and pressing Update
 * did not clear it, and the captured request body carried the stale URL. So this file
 * mocks ONE layer lower, at `apiFetch`, and reads the actual JSON body.
 *
 * Same reason the fix lives where it does: the value sent has to be the value the field
 * holds, and only a test that reads the request can say whether it was.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import RespondWorkspacesAdmin from './RespondWorkspacesAdmin';

const STORED_URL = 'https://automate-sorento.foundryx.my/webhook/sorento-main-inject';

const ROW = {
  id: 'ws-1',
  space_id: '364817',
  name: 'Sorento Main',
  base_url: 'https://api.respond.io',
  whatsapp_number: '60123456789',
  is_active: true,
  is_default: true,
  api_key_masked: '****abcd',
  ideation_shared_service_url: null,
  ideation_product_id: null,
  ideation_intake_api_key_masked: null,
  ideation_embed_connection_id: null,
  ideation_embed_fe_base_url: null,
  ideation_embed_signing_secret_masked: null,
  chatbot_retry_ingress_url: STORED_URL,
  has_chatbot_retry_key: true,
  chatbot_sender_url: null,
  has_chatbot_sender_key: false,
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
};

/** Every request the component made, in order, with its parsed body. */
let calls: { url: string; method: string; body: Record<string, unknown> | null }[] = [];

const apiFetch = vi.fn(async (url: string, init?: RequestInit) => {
  calls.push({
    url,
    method: (init?.method ?? 'GET').toUpperCase(),
    body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
  });
  const payload = url.includes('/ideation-products')
    ? { products: [], error: null }
    : url.includes('/select')
      ? []
      : url.endsWith('respond-workspaces')
        ? [ROW]
        : ROW;
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    clone: () => ({ json: async () => payload }),
  } as unknown as Response;
});

vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...(a as [string, RequestInit])) }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn() },
}));

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RespondWorkspacesAdmin />
    </QueryClientProvider>,
  );
}

const retryCall = () => calls.find((c) => c.url.includes('/chatbot-retry'));

beforeEach(() => {
  calls = [];
  apiFetch.mockClear();
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
});

afterEach(() => cleanup());

async function openEditDialog() {
  renderWithClient();
  await screen.findByRole('button', { name: /edit/i });
  fireEvent.click(screen.getByRole('button', { name: /edit/i }));
  await waitFor(() =>
    expect(screen.getByText('Ideas embed (iframe SSO)')).toBeInTheDocument(),
  );
}

describe('the chatbot retry save, at the wire', () => {
  it('sends chatbot_retry_ingress_url: "" when the field is blanked', async () => {
    await openEditDialog();

    const url = screen.getByLabelText(/Chatbot retry webhook URL/i) as HTMLInputElement;
    expect(url.value).toBe(STORED_URL);
    fireEvent.change(url, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^update$/i }));

    await waitFor(() => expect(retryCall()).toBeDefined());
    const sent = retryCall()!;
    expect(sent.method).toBe('PUT');
    expect(sent.url).toContain('/api/v1/system/respond-workspaces/ws-1/chatbot-retry');
    // The whole point: "" is what clears the column, and the stale URL must not be
    // what leaves the browser.
    expect(sent.body?.chatbot_retry_ingress_url).toBe('');
  });

  it('sends the typed URL unchanged when the field is edited rather than cleared', async () => {
    await openEditDialog();

    const url = screen.getByLabelText(/Chatbot retry webhook URL/i) as HTMLInputElement;
    fireEvent.change(url, { target: { value: 'https://automate-sorento.foundryx.my/webhook/other' } });
    fireEvent.click(screen.getByRole('button', { name: /^update$/i }));

    await waitFor(() => expect(retryCall()).toBeDefined());
    expect(retryCall()!.body?.chatbot_retry_ingress_url).toBe(
      'https://automate-sorento.foundryx.my/webhook/other',
    );
  });

  it('sends the retry PUT even when the draft equals the row it was opened from', async () => {
    // The retry call is never skipped on a change comparison. `editing` is a snapshot
    // taken when the dialog opened, so after a save that did not refresh it a genuine
    // edit can compare EQUAL to a stale row and be dropped while the dialog reports
    // success. Blanking the URL is where that costs the most: "Retry is still on" is
    // the state the operator was trying to leave.
    await openEditDialog();

    fireEvent.click(screen.getByRole('button', { name: /^update$/i }));

    await waitFor(() => expect(retryCall()).toBeDefined());
    expect(retryCall()!.body?.chatbot_retry_ingress_url).toBe(STORED_URL);
  });

  it('does not send the row PUT when only the retry URL changed', async () => {
    // AC-804: the row route keeps the stronger slug, so a settings.edit-only principal
    // must not be 403'd on the way to the narrow one.
    await openEditDialog();

    const url = screen.getByLabelText(/Chatbot retry webhook URL/i) as HTMLInputElement;
    fireEvent.change(url, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /^update$/i }));

    await waitFor(() => expect(retryCall()).toBeDefined());
    const rowPut = calls.find(
      (c) => c.method === 'PUT' && !c.url.includes('/chatbot-retry'),
    );
    expect(rowPut).toBeUndefined();
  });
});

// CHAT-SPEED: the chatbot sender pair rides the same narrow route as the retry pair.
describe('the chatbot sender save, at the wire', () => {
  it('sends the typed sender URL and key on the chatbot-retry route', async () => {
    await openEditDialog();

    const url = screen.getByLabelText(/Chatbot sender webhook URL/i) as HTMLInputElement;
    expect(url.value).toBe('');
    fireEvent.change(url, {
      target: { value: 'https://automate-zzt.example.com/webhook/chatbot-sender' },
    });
    fireEvent.change(screen.getByLabelText(/^Sender key/i), {
      target: { value: 'zzt-sender-key' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^update$/i }));

    await waitFor(() => expect(retryCall()).toBeDefined());
    const body = retryCall()!.body!;
    expect(body.chatbot_sender_url).toBe(
      'https://automate-zzt.example.com/webhook/chatbot-sender',
    );
    expect(body.chatbot_sender_key).toBe('zzt-sender-key');
    // The retry URL rides along unchanged.
    expect(body.chatbot_retry_ingress_url).toBe(STORED_URL);
  });

  it('leaves the sender key unmentioned when its input is left blank', async () => {
    await openEditDialog();
    fireEvent.click(screen.getByRole('button', { name: /^update$/i }));
    await waitFor(() => expect(retryCall()).toBeDefined());
    const body = retryCall()!.body!;
    expect(body.chatbot_sender_url).toBe('');
    expect('chatbot_sender_key' in body).toBe(false);
  });
});

describe('the chatbot sender key, removed', () => {
  it('sends chatbot_sender_key: "" after "Remove stored sender key"', async () => {
    ROW.has_chatbot_sender_key = true;
    try {
      await openEditDialog();
      fireEvent.click(screen.getByRole('button', { name: /remove stored sender key/i }));
      fireEvent.click(screen.getByRole('button', { name: /^update$/i }));
      await waitFor(() => expect(retryCall()).toBeDefined());
      expect(retryCall()!.body!.chatbot_sender_key).toBe('');
    } finally {
      ROW.has_chatbot_sender_key = false;
    }
  });
});
