/**
 * SPEC-REGISTRY-UI F7 (red tests): Try in chatbot - type a customer message, see
 * what the chatbot's parser would pick. Dry run: nothing is sent or saved.
 * The real service runs over a mocked apiFetch so the endpoint and body are asserted.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/master-data-management/product-specifications',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import { apiFetch } from '@/lib/api';
import { TryInChatbotDialog } from './TryInChatbotDialog';

const mockedFetch = vi.mocked(apiFetch);

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TryInChatbotDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

const input = () => screen.getByLabelText('Customer message') as HTMLInputElement;
const runButton = () => screen.getByRole('button', { name: /^Run$/ });

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => cleanup());

describe('TryInChatbotDialog (F7)', () => {
  it('has the title, the message input capped at 500, and the dry-run line', () => {
    renderDialog();

    expect(screen.getByText('Try in chatbot')).toBeInTheDocument();
    expect(input().maxLength).toBe(500);
    expect(screen.getByText('Dry run. Nothing is sent and nothing is saved.')).toBeInTheDocument();
  });

  it('Run is disabled while the message is empty or blank', () => {
    renderDialog();
    expect(runButton()).toBeDisabled();

    fireEvent.change(input(), { target: { value: '   ' } });
    expect(runButton()).toBeDisabled();

    fireEvent.change(input(), { target: { value: '300mm trap' } });
    expect(runButton()).toBeEnabled();
  });

  it('posts {text} to the parser-try endpoint', async () => {
    mockedFetch.mockResolvedValue(jsonResponse({ prompt_version: 63, picks: [] }));
    renderDialog();

    fireEvent.change(input(), { target: { value: '300mm trap' } });
    fireEvent.click(runButton());

    await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(1));
    const [url, init] = mockedFetch.mock.calls[0];
    expect(url).toBe('/api/v1/system/chatbot/parser-try');
    expect((init as RequestInit).method).toBe('POST');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ text: '300mm trap' });
  });

  it('renders Words / Read as / Value columns and a row per pick', async () => {
    mockedFetch.mockResolvedValue(
      jsonResponse({
        prompt_version: 63,
        picks: [
          { raw: '300mm', kind: 'specification', field_label: 'Trap outlet length', value: '300 mm' },
        ],
      }),
    );
    renderDialog();

    fireEvent.change(input(), { target: { value: '300mm trap' } });
    fireEvent.click(runButton());

    expect(await screen.findByText('300mm')).toBeInTheDocument();
    expect(screen.getByText('Trap outlet length')).toBeInTheDocument();
    expect(screen.getByText('300 mm')).toBeInTheDocument();
    expect(screen.getByText('Words')).toBeInTheDocument();
    expect(screen.getByText('Read as')).toBeInTheDocument();
    expect(screen.getByText('Value')).toBeInTheDocument();
  });

  it('Read as falls back to the kind when a pick has no field label', async () => {
    mockedFetch.mockResolvedValue(
      jsonResponse({
        prompt_version: 63,
        picks: [{ raw: 'wall hung', kind: 'category', field_label: null, value: 'wall hung' }],
      }),
    );
    renderDialog();

    fireEvent.change(input(), { target: { value: 'wall hung' } });
    fireEvent.click(runButton());

    expect(await screen.findByText('category')).toBeInTheDocument();
  });

  it('says "Nothing picked" when the parser picked nothing', async () => {
    mockedFetch.mockResolvedValue(jsonResponse({ prompt_version: 63, picks: [] }));
    renderDialog();

    fireEvent.change(input(), { target: { value: 'hello' } });
    fireEvent.click(runButton());

    expect(await screen.findByText('Nothing picked')).toBeInTheDocument();
  });

  it('shows the server message in an alert when the run is refused', async () => {
    const MESSAGE = 'The chatbot reader is not available right now.';
    mockedFetch.mockResolvedValue(
      jsonResponse({ detail: MESSAGE, code: 'parser_try_unavailable' }, 422),
    );
    renderDialog();

    fireEvent.change(input(), { target: { value: 'hello' } });
    fireEvent.click(runButton());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(MESSAGE);
    expect(screen.queryByText('Nothing picked')).toBeNull();
  });
});
