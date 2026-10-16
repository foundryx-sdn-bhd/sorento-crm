import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SystemHealthSettingsPage from './page';

// --- settings + roles context mock ------------------------------------------
type MockSettings = {
  healthDigestEnabled: boolean;
  healthAlertsEnabled: boolean;
  healthNotifyRoleIds: string[];
  healthIntegrationFailThreshold: number;
  healthAuditVolumeFloor: number;
  chatLatencyTargetSeconds: number;
  chatLatencyPercentile: number;
  chatLatencyCeilingMultiplier: number;
  chatLatencyNoReplyMinutes: number;
  chatLatencyMinSample: number;
};

const mockSettings: MockSettings = {
  healthDigestEnabled: true,
  healthAlertsEnabled: false,
  healthNotifyRoleIds: ['role-admin'],
  healthIntegrationFailThreshold: 10,
  healthAuditVolumeFloor: 5,
  chatLatencyTargetSeconds: 10,
  chatLatencyPercentile: 99,
  chatLatencyCeilingMultiplier: 3,
  chatLatencyNoReplyMinutes: 5,
  chatLatencyMinSample: 30,
};

const mockRoles = [
  { id: 'role-admin', name: 'Administrator' },
  { id: 'role-ops', name: 'Operations' },
];

vi.mock('../components/settings-context', () => ({
  useSettings: () => ({ settings: mockSettings, roles: mockRoles }),
}));

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));
vi.mock('@/lib/api-client', () => ({
  extractApiError: vi.fn(async () => 'Failed to save'),
}));
vi.mock('@/lib/toast', () => ({ toast: { custom: vi.fn() } }));

/** The settings POST, located by URL.
 *
 * `apiFetch` is also used by the page's user-select query, so it is not
 * necessarily call[0] - indexing by position made these tests depend on request
 * ordering they do not control. */
function saveCall(): [string, { body: string; method?: string }] {
  const call = apiFetch.mock.calls.find(
    ([url]: unknown[]) => typeof url === 'string' && url.includes('/settings/general'),
  );
  if (!call) throw new Error('settings save was never issued');
  return call as [string, { body: string; method?: string }];
}

function savedBody(): Record<string, unknown> {
  return JSON.parse(saveCall()[1].body);
}

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

/** Route the shared `apiFetch` mock by URL.
 *
 * One mock serves both the settings POST and the page's user-select query. A
 * blanket `mockResolvedValue({ json: () => ({}) })` therefore handed the
 * user-select query an object, and `users?.map(...)` threw - optional chaining
 * guards null, not a non-array. Those escaped as unhandled rejections: the
 * assertions still passed, but vitest exited non-zero, which the deploy gate
 * treats as a failure. `/users/select` really does return an array
 * (`response_model=list[UserSelectResponse]`), so mirror that. */
function stubApiFetch(users: unknown[] = []) {
  apiFetch.mockImplementation(async (url: string) => ({
    ok: true,
    json: async () => (typeof url === 'string' && url.includes('/users/select') ? users : {}),
  }));
}

function resetSettings() {
  mockSettings.healthDigestEnabled = true;
  mockSettings.healthAlertsEnabled = false;
  mockSettings.healthNotifyRoleIds = ['role-admin'];
  mockSettings.healthIntegrationFailThreshold = 10;
  mockSettings.healthAuditVolumeFloor = 5;
  mockSettings.chatLatencyTargetSeconds = 10;
  mockSettings.chatLatencyPercentile = 99;
  mockSettings.chatLatencyCeilingMultiplier = 3;
  mockSettings.chatLatencyNoReplyMinutes = 5;
  mockSettings.chatLatencyMinSample = 30;
}

describe('SystemHealthSettingsPage', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    resetSettings();
  });

  it('seeds both toggles, the role multi-select and the two number inputs from settings', () => {
    wrap(<SystemHealthSettingsPage />);

    expect(screen.getByTestId('health-digest-enabled').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('health-alerts-enabled').getAttribute('aria-checked')).toBe('false');
    // selected role rendered as a human-readable badge (no raw UUID)
    expect(screen.getByText('Administrator')).toBeInTheDocument();
    expect(screen.queryByText('role-admin')).not.toBeInTheDocument();
    // number inputs seeded
    expect((screen.getByTestId('health-integration-fail-threshold') as HTMLInputElement).value).toBe(
      '10',
    );
    expect((screen.getByTestId('health-audit-volume-floor') as HTMLInputElement).value).toBe('5');
  });

  it('saves snake_case keys with the current values', async () => {
    stubApiFetch();
    wrap(<SystemHealthSettingsPage />);

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => expect(saveCall()).toBeDefined());
    const [url, opts] = saveCall();
    expect(url).toBe('/api/user-management/settings/general');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({
      health_digest_enabled: true,
      health_alerts_enabled: false,
      health_notify_role_ids: ['role-admin'],
      // The page has always sent this; the expectation omitted it, which the
      // exact deep-equal now catches.
      health_notify_user_ids: [],
      health_integration_fail_threshold: 10,
      health_audit_volume_floor: 5,
      // Latency SLA - asserted exactly rather than via toMatchObject, so a key
      // silently added to or dropped from the payload fails here.
      chat_latency_p99_target_seconds: 10,
      chat_latency_percentile: 99,
      chat_latency_ceiling_multiplier: 3,
      chat_latency_no_reply_minutes: 5,
      chat_latency_min_sample: 30,
    });
  });

  it('keeps a blank threshold as typed and blocks the save with a sentence', () => {
    stubApiFetch();
    wrap(<SystemHealthSettingsPage />);

    const field = screen.getByTestId('health-integration-fail-threshold');
    fireEvent.change(field, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    expect(field).toHaveValue(null);
    expect(screen.getByText('Enter a whole number between 1 and 100000.')).toBeInTheDocument();
    expect(
      apiFetch.mock.calls.find(([url]: unknown[]) => String(url).includes('/settings/general')),
    ).toBeUndefined();
  });

  it('keeps a negative audit floor as typed and blocks the save with a sentence', () => {
    stubApiFetch();
    wrap(<SystemHealthSettingsPage />);

    const field = screen.getByTestId('health-audit-volume-floor');
    fireEvent.change(field, { target: { value: '-4' } });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    expect(field).toHaveValue(-4);
    expect(screen.getByText('Enter a whole number between 0 and 1000000.')).toBeInTheDocument();
    expect(
      apiFetch.mock.calls.find(([url]: unknown[]) => String(url).includes('/settings/general')),
    ).toBeUndefined();
  });

  it('persists a valid edited integer value', async () => {
    stubApiFetch();
    wrap(<SystemHealthSettingsPage />);

    fireEvent.change(screen.getByTestId('health-integration-fail-threshold'), {
      target: { value: '25' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    const body = savedBody();
    expect(body.health_integration_fail_threshold).toBe(25);
  });

  it('warns about no recipients when a channel is on but no roles are selected', () => {
    mockSettings.healthNotifyRoleIds = [];
    wrap(<SystemHealthSettingsPage />);
    expect(
      screen.getByText(/no roles or users selected/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/fall back to superadmin and admin users/i),
    ).toBeInTheDocument();
  });

  it('does not warn when notifications are on and at least one role is selected', () => {
    wrap(<SystemHealthSettingsPage />);
    expect(screen.queryByText(/no roles or users selected/i)).not.toBeInTheDocument();
  });
});

describe('SystemHealthSettingsPage - WhatsApp round-trip latency (OBS-S4-21)', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    stubApiFetch();
    resetSettings();
  });

  function payload() {
    return savedBody();
  }

  it('seeds every latency field from saved settings', () => {
    mockSettings.chatLatencyTargetSeconds = 12;
    mockSettings.chatLatencyMinSample = 40;
    wrap(<SystemHealthSettingsPage />);

    expect((screen.getByTestId('chat-latency-target') as HTMLInputElement).value).toBe('12');
    expect((screen.getByTestId('chat-latency-min-sample') as HTMLInputElement).value).toBe('40');
  });

  it('saves the latency settings as snake_case keys', async () => {
    wrap(<SystemHealthSettingsPage />);
    fireEvent.change(screen.getByTestId('chat-latency-target'), { target: { value: '15' } });
    fireEvent.change(screen.getByTestId('chat-latency-no-reply'), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const body = payload();
    expect(body.chat_latency_p99_target_seconds).toBe(15);
    expect(body.chat_latency_no_reply_minutes).toBe(7);
    expect(body.chat_latency_percentile).toBe(99);
  });

  it('keeps a blank or zero duration as typed and blocks the save with a sentence', () => {
    // 0 is not a meaningful duration here - it would either disable the check
    // silently or collapse the window, so it must not be persisted.
    wrap(<SystemHealthSettingsPage />);
    fireEvent.change(screen.getByTestId('chat-latency-target'), { target: { value: '' } });
    fireEvent.change(screen.getByTestId('chat-latency-min-sample'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    expect(screen.getByTestId('chat-latency-target')).toHaveValue(null);
    expect(screen.getByTestId('chat-latency-min-sample')).toHaveValue(0);
    expect(screen.getByText('Enter a whole number between 1 and 3600.')).toBeInTheDocument();
    expect(screen.getByText('Enter a whole number between 1 and 100000.')).toBeInTheDocument();
    expect(
      apiFetch.mock.calls.find(([url]: unknown[]) => String(url).includes('/settings/general')),
    ).toBeUndefined();
  });

  it('summarises all three triggers in plain language', () => {
    mockSettings.chatLatencyTargetSeconds = 10;
    mockSettings.chatLatencyCeilingMultiplier = 3;
    wrap(<SystemHealthSettingsPage />);

    const summary = screen.getByTestId('chat-latency-summary');
    expect(summary).toHaveTextContent('p99 exceeds 10s');
    expect(summary).toHaveTextContent('30 turns');
    // ceiling is derived, not typed - the operator should not have to multiply
    expect(summary).toHaveTextContent('30s');
    expect(summary).toHaveTextContent('5 minutes');
  });

  it('recomputes the derived ceiling as the inputs change', () => {
    wrap(<SystemHealthSettingsPage />);
    fireEvent.change(screen.getByTestId('chat-latency-target'), { target: { value: '20' } });

    expect(screen.getByTestId('chat-latency-summary')).toHaveTextContent('60s');
  });

  it('reset restores the saved latency values', () => {
    wrap(<SystemHealthSettingsPage />);
    fireEvent.change(screen.getByTestId('chat-latency-target'), { target: { value: '99' } });
    fireEvent.click(screen.getByRole('button', { name: /reset/i }));

    expect((screen.getByTestId('chat-latency-target') as HTMLInputElement).value).toBe('10');
  });
});

describe('system-health field rules equal the backend bounds (S6)', () => {
  it('refuses a fail threshold of 0 (backend ge=1, le=100000) on the field and sends nothing', () => {
    stubApiFetch();
    wrap(<SystemHealthSettingsPage />);
    const field = screen.getByTestId('health-integration-fail-threshold');
    fireEvent.change(field, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter a whole number between 1 and 100000.')).toBeInTheDocument();
    expect(
      apiFetch.mock.calls.find(([url]: unknown[]) => String(url).includes('/settings/general')),
    ).toBeUndefined();
  });

  it('refuses a latency target above 3600 (backend le=3600) on the field and sends nothing', () => {
    stubApiFetch();
    wrap(<SystemHealthSettingsPage />);
    const field = screen.getByTestId('chat-latency-target');
    fireEvent.change(field, { target: { value: '5000' } });
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter a whole number between 1 and 3600.')).toBeInTheDocument();
    expect(
      apiFetch.mock.calls.find(([url]: unknown[]) => String(url).includes('/settings/general')),
    ).toBeUndefined();
  });
});

