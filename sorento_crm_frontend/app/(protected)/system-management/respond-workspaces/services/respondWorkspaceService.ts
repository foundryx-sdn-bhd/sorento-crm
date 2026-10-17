import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

export interface RespondWorkspace {
  id: string;
  space_id: string;
  name: string | null;
  base_url: string | null;
  whatsapp_number: string | null;
  is_active: boolean;
  is_default: boolean;
  api_key_masked: string | null;
  ideation_shared_service_url: string | null;
  ideation_product_id: string | null;
  ideation_intake_api_key_masked: string | null;
  ideation_embed_connection_id: string | null;
  ideation_embed_fe_base_url: string | null;
  ideation_embed_signing_secret_masked: string | null;
  /** Chatbot retry ingress (AC-804). The URL is plain; the key is NEVER echoed -
   *  the GET says only whether one is stored, because that key authorises injecting
   *  a message back into a real customer's WhatsApp conversation. */
  chatbot_retry_ingress_url: string | null;
  has_chatbot_retry_key: boolean;
  /** CHAT-SPEED: the thin n8n sender a webhook-started turn posts to. Key write-only. */
  chatbot_sender_url: string | null;
  has_chatbot_sender_key: boolean;
  created_at: string;
  updated_at: string;
}

export interface RespondWorkspaceSelectItem {
  id: string;
  space_id: string;
  name: string | null;
  is_default: boolean;
}

export interface RespondWorkspaceCreateBody {
  space_id: string;
  name?: string | null;
  base_url?: string | null;
  whatsapp_number?: string | null;
  is_active?: boolean;
  is_default?: boolean;
  api_key: string;
  ideation_shared_service_url?: string | null;
  ideation_product_id?: string | null;
  ideation_intake_api_key?: string | null;
  ideation_embed_connection_id?: string | null;
  ideation_embed_fe_base_url?: string | null;
  ideation_embed_signing_secret?: string | null;
  chatbot_retry_ingress_url?: string | null;
  chatbot_retry_ingress_key?: string | null;
}

export interface RespondWorkspaceUpdateBody {
  space_id?: string;
  name?: string | null;
  base_url?: string | null;
  whatsapp_number?: string | null;
  is_active?: boolean;
  is_default?: boolean;
  api_key?: string | null;
  ideation_shared_service_url?: string | null;
  ideation_product_id?: string | null;
  ideation_intake_api_key?: string | null;
  ideation_embed_connection_id?: string | null;
  ideation_embed_fe_base_url?: string | null;
  ideation_embed_signing_secret?: string | null;
}

/**
 * The two chatbot retry fields, which have their OWN route and their own permission
 * (`user_management.settings.edit` OR `system.respond_workspaces.edit`). They are not on
 * `RespondWorkspaceUpdateBody` on purpose: putting them on the row PUT is what made the
 * Settings slug reach `api_key`, `base_url` and `is_default` as well.
 *
 * Omit a field to leave it alone; send `''` to CLEAR it. That is what turns Retry off and
 * what revokes the stored key.
 */
export interface RespondWorkspaceChatbotRetryBody {
  chatbot_retry_ingress_url?: string | null;
  /** Write-only: sent when the admin types a new key or asks for the stored one to go. */
  chatbot_retry_ingress_key?: string | null;
  chatbot_sender_url?: string | null;
  chatbot_sender_key?: string | null;
}

export interface IdeationProductOption {
  id: string;
  name: string;
}

export interface IdeationProductsResult {
  products: IdeationProductOption[];
  error: string | null;
}

const base = '/api/v1/system/respond-workspaces';

/**
 * Fetch the shared-service software-product catalog for the Ideation-product
 * dropdown, via the sorento proxy. Live-preview (base_url + api_key the admin
 * typed but hasn't saved) wins over the saved workspace_id. The proxy always
 * responds 200 with `{products, error}`; a non-2xx here is a transport failure.
 */
export async function listIdeationProducts(params: {
  workspaceId?: string | null;
  baseUrl?: string | null;
  apiKey?: string | null;
}): Promise<IdeationProductsResult> {
  const qs = new URLSearchParams();
  if (params.workspaceId) qs.set('workspace_id', params.workspaceId);
  if (params.baseUrl) qs.set('base_url', params.baseUrl);
  if (params.apiKey) qs.set('api_key', params.apiKey);
  const response = await apiFetch(`${base}/ideation-products?${qs.toString()}`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to fetch ideation products'));
  return response.json();
}

export async function listRespondWorkspaces(): Promise<RespondWorkspace[]> {
  const response = await apiFetch(base);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to fetch workspaces'));
  return response.json();
}

export async function listRespondWorkspaceSelect(): Promise<RespondWorkspaceSelectItem[]> {
  const response = await apiFetch(`${base}/select`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to fetch workspace select'));
  return response.json();
}

export async function createRespondWorkspace(
  body: RespondWorkspaceCreateBody,
): Promise<RespondWorkspace> {
  const response = await apiFetch(base, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to create workspace'));
  return response.json();
}

export async function updateRespondWorkspace(
  id: string,
  body: RespondWorkspaceUpdateBody,
): Promise<RespondWorkspace> {
  const response = await apiFetch(`${base}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to update workspace'));
  return response.json();
}

export async function updateRespondWorkspaceChatbotRetry(
  id: string,
  body: RespondWorkspaceChatbotRetryBody,
): Promise<RespondWorkspace> {
  const response = await apiFetch(`${base}/${id}/chatbot-retry`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to update chatbot retry settings'));
  return response.json();
}

export async function deleteRespondWorkspace(id: string): Promise<void> {
  const response = await apiFetch(`${base}/${id}`, { method: 'DELETE' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to delete workspace'));
}

export async function setDefaultRespondWorkspace(id: string): Promise<RespondWorkspace> {
  const response = await apiFetch(`${base}/${id}/set-default`, { method: 'POST' });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to set default workspace'));
  return response.json();
}
