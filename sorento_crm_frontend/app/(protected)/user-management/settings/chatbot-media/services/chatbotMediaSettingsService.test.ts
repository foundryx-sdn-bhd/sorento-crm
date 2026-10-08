/**
 * The settings GET emits every `media_*` key as an explicit `null` when no
 * `system_settings` row exists yet (each is built as `value if settings else None`).
 * A null on a non-nullable key must fall back exactly like a missing key, or a
 * fresh install renders `null.trim()` into the error boundary instead of the form.
 * The four nullable model columns keep null as a real value.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import {
  getChatbotMediaSettings,
  saveChatbotMediaSettings,
  type ChatbotMediaSettings,
} from './chatbotMediaSettingsService';

function ok(body: unknown) {
  return { ok: true, json: async () => body };
}

const ALL_NULL = {
  media_image_monthly_limit: null,
  media_voice_monthly_limit: null,
  media_voice_max_seconds: null,
  media_burst_limit: null,
  media_burst_window_seconds: null,
  media_warn_threshold_percent: null,
  media_image_provider: null,
  media_image_model: null,
  media_image_degraded_model: null,
  media_transcribe_model: null,
  media_voice_degraded_model: null,
  media_language_mode: null,
  media_language_pinned: null,
  media_language_hints: null,
  media_sync_wait_seconds: null,
  media_extraction_timeout_seconds: null,
  media_max_entities: null,
};

describe('getChatbotMediaSettings', () => {
  beforeEach(() => apiFetch.mockReset());

  it('treats an explicit null as missing on every non-nullable key (fresh install)', async () => {
    apiFetch.mockResolvedValue(ok({ settings: ALL_NULL }));

    const settings = await getChatbotMediaSettings();

    expect(settings.media_transcribe_model).toBe('whisper-1');
    expect(settings.media_language_mode).toBe('pinned');
    expect(settings.media_language_pinned).toBe('en');
    expect(settings.media_language_hints).toBe('en,ms,zh');
    expect(settings.media_image_monthly_limit).toBe(50);
    expect(settings.media_voice_monthly_limit).toBe(100);
    expect(settings.media_sync_wait_seconds).toBe(30);
    expect(settings.media_extraction_timeout_seconds).toBe(45);
    expect(settings.media_max_entities).toBe(10);
    // Lane REPORT-RATE-LIMIT-CONFIG: the report cap's own defaults, 10 per 10 minutes.
    expect(settings.report_ask_rate_limit).toBe(10);
    expect(settings.report_ask_rate_window_minutes).toBe(10);
  });

  it('reads the stored report rate limit, a stored 0 included (0 means no limit)', async () => {
    apiFetch.mockResolvedValue(
      ok({ settings: { ...ALL_NULL, report_ask_rate_limit: 0, report_ask_rate_window_minutes: 3 } }),
    );

    const settings = await getChatbotMediaSettings();

    expect(settings.report_ask_rate_limit).toBe(0);
    expect(settings.report_ask_rate_window_minutes).toBe(3);
  });

  it('keeps null as a real value on the four nullable model columns', async () => {
    apiFetch.mockResolvedValue(
      ok({
        settings: {
          ...ALL_NULL,
          media_image_provider: null,
          media_image_model: null,
          media_image_degraded_model: null,
          media_voice_degraded_model: null,
        },
      }),
    );

    const settings = await getChatbotMediaSettings();

    expect(settings.media_image_provider).toBeNull();
    expect(settings.media_image_model).toBeNull();
    expect(settings.media_image_degraded_model).toBeNull();
    expect(settings.media_voice_degraded_model).toBeNull();
  });

  it('a stored value still wins over the fallback', async () => {
    apiFetch.mockResolvedValue(
      ok({
        settings: {
          ...ALL_NULL,
          media_transcribe_model: 'whisper-large',
          media_image_model: 'gpt-4o',
          media_image_monthly_limit: 7,
        },
      }),
    );

    const settings = await getChatbotMediaSettings();

    expect(settings.media_transcribe_model).toBe('whisper-large');
    expect(settings.media_image_model).toBe('gpt-4o');
    expect(settings.media_image_monthly_limit).toBe(7);
  });

  it('a missing key falls back the same way a null does', async () => {
    apiFetch.mockResolvedValue(ok({ settings: {} }));

    const settings = await getChatbotMediaSettings();

    expect(settings.media_transcribe_model).toBe('whisper-1');
    expect(settings.media_image_provider).toBeNull();
  });

  it('reads from the settings endpoint', async () => {
    apiFetch.mockResolvedValue(ok({ settings: {} }));

    await getChatbotMediaSettings();

    expect(apiFetch).toHaveBeenCalledWith('/api/user-management/settings');
  });
});

describe('saveChatbotMediaSettings', () => {
  beforeEach(() => apiFetch.mockReset());

  function input(overrides: Partial<ChatbotMediaSettings> = {}): ChatbotMediaSettings {
    return {
      media_image_monthly_limit: 50,
      media_voice_monthly_limit: 100,
      media_voice_max_seconds: 120,
      media_burst_limit: 5,
      media_burst_window_seconds: 60,
      media_warn_threshold_percent: 80,
      media_image_provider: null,
      media_image_model: null,
      media_image_degraded_model: null,
      media_transcribe_model: 'whisper-1',
      media_voice_degraded_model: null,
      media_language_mode: 'pinned',
      media_language_pinned: 'en',
      media_language_hints: 'en,ms,zh',
      media_sync_wait_seconds: 30,
      media_extraction_timeout_seconds: 45,
      media_max_entities: 10,
      low_stock_sync_wait_seconds: 40,
      report_ask_rate_limit: 10,
      report_ask_rate_window_minutes: 10,
      ...overrides,
    };
  }

  it('POSTs the settings to the general endpoint', async () => {
    apiFetch.mockResolvedValue(ok({ message: 'ok', data: ALL_NULL }));

    await saveChatbotMediaSettings(input({ media_image_monthly_limit: 7 }));

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [url, options] = apiFetch.mock.calls[0] as [
      string,
      { method: string; body: string },
    ];
    expect(url).toBe('/api/user-management/settings/general');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body).media_image_monthly_limit).toBe(7);
  });

  it('normalizes the write envelope (`data`), which differs from the read one (`settings`)', async () => {
    apiFetch.mockResolvedValue(
      ok({
        message: 'ok',
        data: {
          ...ALL_NULL,
          media_transcribe_model: 'whisper-large',
          media_image_monthly_limit: 9,
        },
      }),
    );

    const saved = await saveChatbotMediaSettings(input());

    expect(saved.media_transcribe_model).toBe('whisper-large');
    expect(saved.media_image_monthly_limit).toBe(9);
    expect(saved.media_voice_monthly_limit).toBe(100);
  });
});
