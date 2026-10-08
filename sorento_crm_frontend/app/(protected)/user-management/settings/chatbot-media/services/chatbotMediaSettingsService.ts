import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

/**
 * Chatbot media settings (PLAN-chatbot-media-endpoint, slice S1, UAC S1-04 / S1-08).
 *
 * Every number the media endpoint enforces is an operator-editable column on the
 * `system_settings` singleton, not a constant in code. That was the captain's
 * requirement, and it is why this surface exists at all.
 *
 * ---------------------------------------------------------------------------
 * API CONTRACT - written in Phase 1, built to in Phase 2
 * ---------------------------------------------------------------------------
 *
 * READ   GET /api/v1/user-management/settings
 *   The existing settings endpoint, whose hand-written response dict now carries the
 *   `media_*` keys below. A column added to the model but not to that dict never
 *   reaches this page (UAC S1-08, and a documented repeat failure in this repo).
 *   200 -> { "settings": { ...existing..., "media_image_monthly_limit": 50, ... } }
 *
 * WRITE  POST /api/v1/user-management/settings/general
 *   The existing general-settings setattr path, which applies any provided snake_case
 *   column. The same keys are declared on `SystemSettingUpdate` - BOTH manual builders,
 *   or the value saves and never comes back.
 *   body: the `ChatbotMediaSettings` object below.
 *   200 -> { "message": ..., "data": <the updated settings row> }
 *   400 when `media_extraction_timeout_seconds` is below `media_sync_wait_seconds`:
 *   a job that outlives the synchronous wait would be killed instead of degrading.
 *   The page refuses that pair inline, so the 400 is the backstop rather than the
 *   way an operator finds out.
 *
 * `apiFetch('/api/user-management/...')` maps straight to FastAPI `/api/v1/user-management/...`
 * and bypasses any Next.js route.ts proxy, so no proxy route is added for this.
 *
 * Nullable columns and what NULL means:
 *   media_image_provider / media_image_model  NULL -> fall back to the AIAssistantConfig row
 *   media_image_degraded_model                NULL -> degradation is impossible, so the
 *                                             monthly quota becomes a hard refusal instead
 *                                             of an accepted-but-degraded extraction
 *   media_voice_degraded_model                NULL -> the same hard refusal for voice, and
 *                                             this one ships NULL on purpose (plan section
 *                                             16.1): the image tiers were measured, no
 *                                             cheaper transcription model has been, so none
 *                                             is seeded and none is suggested here
 */

export type MediaLanguageMode = 'pinned' | 'hints' | 'auto';

export interface ChatbotMediaSettings {
  /** Items per contact per calendar month, image modality. */
  media_image_monthly_limit: number;
  /** Items per contact per calendar month, voice modality. */
  media_voice_monthly_limit: number;
  /** A longer clip is refused before any transcription is attempted. */
  media_voice_max_seconds: number;
  /** Items allowed inside one burst window before pacing kicks in. */
  media_burst_limit: number;
  media_burst_window_seconds: number;
  /** Percentage of the monthly allowance at which the contact is warned once. */
  media_warn_threshold_percent: number;
  /** null = inherit the AI assistant provider. */
  media_image_provider: string | null;
  /** null = inherit the AI assistant model. */
  media_image_model: string | null;
  /** null = no degradation possible; the monthly quota becomes a hard stop. */
  media_image_degraded_model: string | null;
  media_transcribe_model: string;
  /**
   * null = no degradation possible for voice; the monthly quota becomes a hard stop.
   * Unlike the image tier this is NOT seeded, so null is the shipped state rather
   * than a value someone cleared.
   */
  media_voice_degraded_model: string | null;
  media_language_mode: MediaLanguageMode;
  /** Used in `pinned` mode. */
  media_language_pinned: string;
  /** CSV, used in `hints` mode. */
  media_language_hints: string;
  /**
   * How long the endpoint awaits the worker before it answers `pending`. This is the
   * one number that bounds how long the dispatcher holds its per-contact lock, so it
   * is editable here rather than being a constant only a deploy can move. Range 5-90.
   */
  media_sync_wait_seconds: number;
  /**
   * Hard ceiling on the queued extraction, well inside the dispatcher's 120s lock TTL.
   * Must be at least `media_sync_wait_seconds`. Range 5-110.
   */
  media_extraction_timeout_seconds: number;
  /** Entities emitted per image before the result is truncated and says so. */
  media_max_entities: number;
  /**
   * How long the low stock report route holds the chat turn open, waiting for the fresh
   * plan and its workbook, before it answers `pending` and leaves delivery to the worker
   * push (PLAN-low-stock-report S5, AC-7/AC-43). It sits beside the media wait because it
   * is the same kind of number - the one bound on how long a turn can be held - and the
   * same singleton owns it. Range 5-90; 40 keeps it inside the chatbot's own queue-wait
   * budget. REAL since S5: `system_settings.low_stock_sync_wait_seconds` exists, is in
   * both manual dict builders, and is validated 5..90 on PUT - so this reads and writes
   * the stored value, and the fallback below only covers a settings row that predates the
   * column, exactly like every `media_*` key beside it.
   */
  low_stock_sync_wait_seconds: number;
  /**
   * The chatbot report engine's cap: at most this many reports per contact in the window
   * below (lane REPORT-RATE-LIMIT-CONFIG). 0 means no limit. Range 0-1000.
   */
  report_ask_rate_limit: number;
  /** The report cap's window in minutes. Range 1-1440. */
  report_ask_rate_window_minutes: number;
}

const MEDIA_KEYS: (keyof ChatbotMediaSettings)[] = [
  'media_image_monthly_limit',
  'media_voice_monthly_limit',
  'media_voice_max_seconds',
  'media_burst_limit',
  'media_burst_window_seconds',
  'media_warn_threshold_percent',
  'media_image_provider',
  'media_image_model',
  'media_image_degraded_model',
  'media_transcribe_model',
  'media_voice_degraded_model',
  'media_language_mode',
  'media_language_pinned',
  'media_language_hints',
  'media_sync_wait_seconds',
  'media_extraction_timeout_seconds',
  'media_max_entities',
  'low_stock_sync_wait_seconds',
  'report_ask_rate_limit',
  'report_ask_rate_window_minutes',
];

/** The plan's section 2.4 defaults, used when the settings row predates the columns. */
const FALLBACKS: ChatbotMediaSettings = {
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
  // Deliberately null, and deliberately not the image tier's seeded model: no cheaper
  // transcription model has been measured, so naming one here would claim a
  // degradation nobody has evidence for.
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
};

/**
 * The four columns where NULL is a real stored value ("inherit" / "no degraded
 * tier"). Every other key is non-nullable in the draft, and the settings GET emits
 * every key as an explicit null when no `system_settings` row exists yet, so for
 * those a null must fall back exactly like a missing key.
 */
const NULLABLE_KEYS: ReadonlySet<keyof ChatbotMediaSettings> = new Set([
  'media_image_provider',
  'media_image_model',
  'media_image_degraded_model',
  'media_voice_degraded_model',
]);

function pickMediaSettings(row: Record<string, unknown> | null | undefined): ChatbotMediaSettings {
  const picked = { ...FALLBACKS } as Record<string, unknown>;
  if (row) {
    for (const key of MEDIA_KEYS) {
      const value = row[key];
      if (value === undefined) continue;
      if (value === null && !NULLABLE_KEYS.has(key)) continue;
      picked[key] = value;
    }
  }
  return picked as unknown as ChatbotMediaSettings;
}

export async function getChatbotMediaSettings(): Promise<ChatbotMediaSettings> {
  const response = await apiFetch('/api/user-management/settings');
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load settings'));
  }
  const data = await response.json();
  return pickMediaSettings(data?.settings);
}

export async function saveChatbotMediaSettings(
  input: ChatbotMediaSettings,
): Promise<ChatbotMediaSettings> {
  const response = await apiFetch('/api/user-management/settings/general', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save settings'));
  }
  const data = await response.json();
  return pickMediaSettings(data?.data);
}
