import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

/**
 * Chatbot access model: roles, the access registry and per-contact access.
 * Backend: app/api/v1/system/chatbot_roles.py (permissions: access_agents.view reads,
 * reference_data.manage role writes, contacts.view / contacts.edit contact access).
 */

const BASE = '/api/v1/system/chatbot';

export type AudienceTier = 'dealer' | 'office' | 'end_user';

export interface RegistryField {
  key: string;
  label: string;
  kind: 'field' | 'report' | string;
}

export interface RegistryDomain {
  name: string;
  label: string;
  supported?: boolean;
  access_section?: string | null;
  group?: string | null;
  access_label?: string | null;
  access_description?: string | null;
  escalation_agent_code?: string | null;
  escalation_agent_name?: string | null;
  escalation_team_code?: string | null;
  escalation_tier1_team?: string | null;
  fields: RegistryField[];
}

export interface Registry {
  domains: RegistryDomain[];
}

export interface ChatbotRole {
  id: string;
  code: string;
  name: string;
  description?: string | null;
  sees_all_customers: boolean;
  audience_tier?: AudienceTier | null;
  domains: string[];
  fields: string[];
  contact_count: number;
}

export interface RoleInput {
  name: string;
  description?: string | null;
  sees_all_customers?: boolean;
  audience_tier?: AudienceTier | string | null;
  clear_audience_tier?: boolean;
}

export interface RoleGrants {
  domains: string[];
  fields: string[];
}

export interface RoleContact {
  id: string;
  name: string | null;
}

export interface AccessOverride {
  domain_name: string;
  field_key: string | null;
  granted: boolean;
}

export interface ContactAccess {
  roles: { id: string; code: string; name: string }[];
  overrides: AccessOverride[];
  effective: {
    domains: string[];
    attributes: string[];
    sees_all_customers: boolean;
    tiers?: string[];
  };
}

export interface ContactAccessInput {
  role_ids: string[];
  overrides: AccessOverride[];
}

async function send<T>(
  url: string,
  fallback: string,
  init?: { method: string; body?: unknown },
  parse = true,
): Promise<T> {
  const response = await apiFetch(url, {
    method: init?.method ?? 'GET',
    ...(init?.body !== undefined
      ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(init.body) }
      : {}),
  });
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  return (parse ? await response.json() : undefined) as T;
}

export function getRegistry(): Promise<Registry> {
  return send(`${BASE}/access/registry`, 'Failed to load the access registry');
}

export async function listRoles(): Promise<ChatbotRole[]> {
  const body = await send<ChatbotRole[] | { items: ChatbotRole[] }>(
    `${BASE}/roles`,
    'Failed to load roles',
  );
  return Array.isArray(body) ? body : body.items;
}

export function getRole(id: string): Promise<ChatbotRole> {
  return send(`${BASE}/roles/${id}`, 'Failed to load the role');
}

export function createRole(input: RoleInput): Promise<ChatbotRole> {
  return send(`${BASE}/roles`, 'Failed to create the role', { method: 'POST', body: input });
}

export function updateRole(id: string, input: Partial<RoleInput>): Promise<ChatbotRole> {
  return send(`${BASE}/roles/${id}`, 'Failed to update the role', { method: 'PATCH', body: input });
}

export async function deleteRole(id: string): Promise<void> {
  await send<void>(`${BASE}/roles/${id}`, 'Failed to delete the role', { method: 'DELETE' }, false);
}

export function setRoleGrants(id: string, grants: RoleGrants): Promise<ChatbotRole> {
  return send(`${BASE}/roles/${id}/grants`, 'Failed to save the role', { method: 'PUT', body: grants });
}

export function listRoleContacts(id: string): Promise<RoleContact[]> {
  return send(`${BASE}/roles/${id}/contacts`, 'Failed to load the role contacts');
}

export function addRoleContacts(id: string, contactIds: string[]): Promise<RoleContact[]> {
  return send(`${BASE}/roles/${id}/contacts`, 'Failed to add the contacts', {
    method: 'POST',
    body: { contact_ids: contactIds },
  });
}

export function getContactAccess(contactId: string): Promise<ContactAccess> {
  return send(`${BASE}/contacts/${contactId}/access`, 'Failed to load the contact access');
}

export function setContactAccess(
  contactId: string,
  input: ContactAccessInput,
): Promise<ContactAccess> {
  return send(`${BASE}/contacts/${contactId}/access`, 'Failed to save the contact access', {
    method: 'PUT',
    body: input,
  });
}
