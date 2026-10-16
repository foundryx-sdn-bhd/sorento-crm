import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import TraceSettingsCard from './TraceSettingsCard';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    React.createElement(QueryClientProvider, { client }, React.createElement(TraceSettingsCard)),
  );
}

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({
    ok: true,
    json: async () => ({
      settings: {
        ai_assistant_role_split_enabled: false,
        ai_trace_ttl_days: 7,
        ai_trace_error_ttl_days: 45,
        ai_trace_max_payload_bytes: 4096,
      },
    }),
  } as unknown as Response);
});

afterEach(() => cleanup());

describe('TraceSettingsCard number fields', () => {
  it('leaves a cleared retention empty (no snap back) and blocks Save with a sentence', async () => {
    renderCard();
    const field = await screen.findByLabelText('Trace retention (days)');
    fireEvent.change(field, { target: { value: '' } });
    expect(field).toHaveValue(null);

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByText('Enter a whole number of at least 1.')).toBeInTheDocument();
    expect(field).toHaveValue(null);
    // Only the initial read happened; nothing was posted.
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('keeps a payload cap below the minimum as typed and blocks Save', async () => {
    renderCard();
    const field = await screen.findByLabelText('Payload cap (bytes)');
    fireEvent.change(field, { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(field).toHaveValue(100);
    expect(screen.getByText('Enter a whole number of at least 512.')).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
});
