import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

/**
 * Runtime settings that used to be prod env vars (lane SETTINGS-IN-DB-SORENTO,
 * documentation/plans/system/PLAN-settings-in-db-sorento.md, "Contract").
 *
 * READ   GET /api/v1/user-management/settings/runtime
 * WRITE  PUT /api/v1/user-management/settings/runtime
 *   Both need `user_management.settings.secrets` (admin only).
 *   PUT body: only the fields being changed. A secret or text field set to null clears the
 *   saved value (the server env value, if any, applies again); the switch set to null goes
 *   back to the server default. PUT answers with the same shape as GET.
 *
 * A saved value wins over the server env value; `source` says which one is live.
 * Secrets never come back in full: `masked` is "****" plus the last 4 characters.
 */

export const RUNTIME_SETTINGS_PERMISSION = 'user_management.settings.secrets';

export type RuntimeSettingName =
  | 'chat_topic_labeller_enabled'
  | 'connector_ss_to_product_secret'
  | 'connector_product_to_ss_secret'
  | 'connector_connection_id'
  | 'connector_ss_callback_url'
  | 'analytics_ro_database_url'
  | 'respond_webhook_secret';

export type RuntimeSettingSource = 'db' | 'env' | 'unset';

export interface RuntimeSettingField {
  source: RuntimeSettingSource;
  secret: boolean;
  /** Non-secret effective value; always null for a secret. */
  value: string | boolean | null;
  /** Secret only: "****" + last 4 of the live value, null when unset. */
  masked: string | null;
  /** The saved secret cannot be decrypted (the encryption key changed). */
  unreadable: boolean;
}

export interface RuntimeSettings {
  fields: Record<RuntimeSettingName, RuntimeSettingField>;
  respond_webhook_path: string;
  respond_signature_headers: string[];
  respond_shared_secret_header: string;
}

export type RuntimeSettingsUpdate = Partial<Record<RuntimeSettingName, string | boolean | null>>;

const ENDPOINT = '/api/user-management/settings/runtime';

export async function getRuntimeSettings(): Promise<RuntimeSettings> {
  const response = await apiFetch(ENDPOINT);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load integration settings'));
  }
  return (await response.json()) as RuntimeSettings;
}

export async function saveRuntimeSettings(
  update: RuntimeSettingsUpdate,
): Promise<RuntimeSettings> {
  const response = await apiFetch(ENDPOINT, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save integration settings'));
  }
  return (await response.json()) as RuntimeSettings;
}
