/**
 * Settings -> Integrations, runtime settings cards (SETTINGS-IN-DB-SORENTO, UAC AC-9).
 * Hooks are mocked so each test drives the query / mutation state directly.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
  ResizeObserverStub;

const mockQuery = vi.fn();
const mockMutate = vi.fn();

vi.mock('../hooks/useRuntimeSettings', () => ({
  useRuntimeSettings: () => mockQuery(),
  useSaveRuntimeSettings: () => ({ mutate: mockMutate, isPending: false }),
}));

import RuntimeSettingsCards from './RuntimeSettingsCards';
import type {
  RuntimeSettingField,
  RuntimeSettingName,
  RuntimeSettings,
} from '../services/runtimeSettingsService';

const unset: RuntimeSettingField = {
  source: 'unset',
  secret: false,
  value: null,
  masked: null,
  unreadable: false,
};

function data(
  overrides: Partial<
    Record<RuntimeSettingName, Partial<RuntimeSettingField>>
  > = {},
  extra: Partial<RuntimeSettings> = {},
) {
  const secret = { ...unset, secret: true };
  const fields: Record<RuntimeSettingName, RuntimeSettingField> = {
    chat_topic_labeller_enabled: { ...unset, value: false },
    connector_ss_to_product_secret: secret,
    connector_product_to_ss_secret: secret,
    connector_connection_id: unset,
    connector_ss_callback_url: unset,
    analytics_ro_database_url: secret,
    respond_webhook_secret: secret,
    do_assume_delivered_before: unset,
  };
  for (const [k, v] of Object.entries(overrides)) {
    fields[k as RuntimeSettingName] = {
      ...fields[k as RuntimeSettingName],
      ...v,
    };
  }
  const result: RuntimeSettings = {
    fields,
    respond_webhook_path: '/api/v1/public/respond/webhook',
    respond_signature_headers: ['x-respond-signature', 'x-webhook-signature'],
    respond_shared_secret_header: 'X-Respond-Webhook-Secret',
    delivered_order_status_exists: true,
    ...extra,
  };
  return result;
}

beforeEach(() => {
  mockQuery.mockReset();
  mockMutate.mockReset();
});
afterEach(cleanup);

describe('RuntimeSettingsCards', () => {
  it('shows a retry instead of hanging when the load fails', () => {
    const refetch = vi.fn();
    mockQuery.mockReturnValue({
      isLoading: false,
      isError: true,
      error: new Error('Permission required'),
      refetch,
      isFetching: false,
    });
    render(<RuntimeSettingsCards />);
    expect(screen.getByText('Permission required')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalled();
  });

  it('renders the three cards, the webhook URL and the signature header', () => {
    mockQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: data(),
    });
    render(<RuntimeSettingsCards />);
    expect(screen.getByText('Chat analytics')).toBeTruthy();
    expect(screen.getByText('Claude connector')).toBeTruthy();
    expect(screen.getByText('Respond.io webhook')).toBeTruthy();
    const url = screen.getByLabelText('Webhook URL') as HTMLInputElement;
    expect(url.value).toBe(
      `${window.location.origin}/api/v1/public/respond/webhook`,
    );
    expect(
      screen.getByText('x-respond-signature or x-webhook-signature'),
    ).toBeTruthy();
    expect(screen.getByText('X-Respond-Webhook-Secret')).toBeTruthy();
  });

  it('shows the source of each value and the masked secret, never a prefilled secret', () => {
    mockQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: data({
        respond_webhook_secret: { source: 'db', masked: '****9f3c' },
        connector_connection_id: { source: 'env', value: 'conn-env-1' },
        connector_ss_to_product_secret: {
          source: 'db',
          unreadable: true,
          masked: null,
        },
      }),
    });
    render(<RuntimeSettingsCards />);
    const secret = screen.getByLabelText('Signing keys') as HTMLTextAreaElement;
    expect(secret.value).toBe('');
    expect(secret.placeholder).toContain('****9f3c');
    expect(screen.getAllByText('Saved in CRM').length).toBeGreaterThan(0);
    expect(screen.getByText('Server env')).toBeTruthy();
    expect(
      (screen.getByLabelText('Connection ID') as HTMLInputElement).placeholder,
    ).toBe('conn-env-1');
    expect(
      screen.getByText('Saved value unreadable, enter it again'),
    ).toBeTruthy();
  });

  it('saves only the fields that changed', () => {
    mockQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: data(),
    });
    render(<RuntimeSettingsCards />);
    fireEvent.change(screen.getByLabelText('Connection ID'), {
      target: { value: 'conn-42' },
    });
    const saves = screen.getAllByRole('button', { name: 'Save' });
    // Cards in order: Chat analytics, Claude connector, Respond.io webhook.
    fireEvent.click(saves[1]);
    expect(mockMutate).toHaveBeenCalledWith(
      { connector_connection_id: 'conn-42' },
      expect.anything(),
    );
  });

  it('turns the labeller on', () => {
    mockQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: data(),
    });
    render(<RuntimeSettingsCards />);
    fireEvent.click(
      screen.getByRole('switch', { name: 'Nightly topic labeller' }),
    );
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[0]);
    expect(mockMutate).toHaveBeenCalledWith(
      { chat_topic_labeller_enabled: true },
      expect.anything(),
    );
  });

  it('clears a saved value back to the server env', () => {
    mockQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: data({
        respond_webhook_secret: { source: 'db', masked: '****9f3c' },
      }),
    });
    render(<RuntimeSettingsCards />);
    fireEvent.click(screen.getByRole('button', { name: 'Clear saved value' }));
    expect(mockMutate).toHaveBeenCalledWith({ respond_webhook_secret: null });
  });

  it('typing a secret and erasing it keeps the saved value', () => {
    mockQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: data({
        respond_webhook_secret: { source: 'db', masked: '****9f3c' },
      }),
    });
    render(<RuntimeSettingsCards />);
    const secret = screen.getByLabelText('Signing keys');
    fireEvent.change(secret, { target: { value: 'abc' } });
    fireEvent.change(secret, { target: { value: '' } });
    const save = screen.getAllByRole('button', {
      name: 'Save',
    })[2] as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(save);
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it('switching the labeller on and off again leaves nothing to save', () => {
    mockQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: data(),
    });
    render(<RuntimeSettingsCards />);
    const toggle = screen.getByRole('switch', {
      name: 'Nightly topic labeller',
    });
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    const save = screen.getAllByRole('button', {
      name: 'Save',
    })[0] as HTMLButtonElement;
    expect(save.disabled).toBe(true);
  });

  it('takes one Respond.io signing key per line in a box that is never prefilled', () => {
    mockQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: data({
        respond_webhook_secret: { source: 'db', masked: '****Ek4=' },
      }),
    });
    render(<RuntimeSettingsCards />);
    const keys = screen.getByLabelText('Signing keys') as HTMLTextAreaElement;
    expect(keys.tagName).toBe('TEXTAREA');
    expect(keys.value).toBe('');
    expect(keys.placeholder).toContain('****Ek4=');
    expect(
      screen.getByText(
        "Paste each Respond.io webhook's signing key, one per line.",
      ),
    ).toBeTruthy();
    const typed = 'k9/Fq+2Zr8Lm=\nZx+7mP/2Qw9e=';
    fireEvent.change(keys, { target: { value: typed } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[2]);
    expect(mockMutate).toHaveBeenCalledWith(
      { respond_webhook_secret: typed },
      expect.anything(),
    );
  });

  describe('AutoCount: Assume DOs delivered before (DO-BACKFILL-SETTING)', () => {
    const LABEL = 'Assume DOs delivered before';
    // Cards in order: Chat analytics, Claude connector, Respond.io webhook, AutoCount.
    const autocountSave = () =>
      screen.getAllByRole('button', { name: 'Save' })[3] as HTMLButtonElement;

    it('shows the AutoCount card with the saved date in DD/MM/YYYY', () => {
      mockQuery.mockReturnValue({
        isLoading: false,
        isError: false,
        data: data({
          do_assume_delivered_before: { source: 'db', value: '2026-07-01' },
        }),
      });
      render(<RuntimeSettingsCards />);
      expect(screen.getByText('AutoCount')).toBeTruthy();
      const input = screen.getByLabelText(LABEL) as HTMLInputElement;
      expect(input.value).toBe('01/07/2026');
    });

    it('saves a typed date as YYYY-MM-DD', () => {
      mockQuery.mockReturnValue({
        isLoading: false,
        isError: false,
        data: data(),
      });
      render(<RuntimeSettingsCards />);
      expect(autocountSave().disabled).toBe(true);
      fireEvent.change(screen.getByLabelText(LABEL), {
        target: { value: '01/07/2026' },
      });
      fireEvent.click(autocountSave());
      expect(mockMutate).toHaveBeenCalledWith(
        { do_assume_delivered_before: '2026-07-01' },
        expect.anything(),
      );
    });

    it('shows the env date as the placeholder while nothing is saved', () => {
      mockQuery.mockReturnValue({
        isLoading: false,
        isError: false,
        data: data({
          do_assume_delivered_before: { source: 'env', value: '2026-07-01' },
        }),
      });
      render(<RuntimeSettingsCards />);
      const input = screen.getByLabelText(LABEL) as HTMLInputElement;
      expect(input.value).toBe('');
      expect(input.placeholder).toBe('01/07/2026');
      expect(screen.getByText('Server env')).toBeTruthy();
    });

    it('clears the saved date', () => {
      mockQuery.mockReturnValue({
        isLoading: false,
        isError: false,
        data: data({
          do_assume_delivered_before: { source: 'db', value: '2026-07-01' },
        }),
      });
      render(<RuntimeSettingsCards />);
      fireEvent.click(screen.getByRole('button', { name: 'Clear saved value' }));
      expect(mockMutate).toHaveBeenCalledWith({
        do_assume_delivered_before: null,
      });
    });

    it('warns when no delivered order status exists', () => {
      mockQuery.mockReturnValue({
        isLoading: false,
        isError: false,
        data: data({}, { delivered_order_status_exists: false }),
      });
      render(<RuntimeSettingsCards />);
      expect(screen.getByRole('alert').textContent).toContain(
        'No order status with code delivered',
      );
    });

    it('shows no warning when a delivered order status exists', () => {
      mockQuery.mockReturnValue({
        isLoading: false,
        isError: false,
        data: data(),
      });
      render(<RuntimeSettingsCards />);
      expect(screen.queryByRole('alert')).toBeNull();
    });
  });
});
