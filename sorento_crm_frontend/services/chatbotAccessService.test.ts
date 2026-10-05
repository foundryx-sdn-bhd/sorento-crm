/**
 * Access model S6 (AC-AM-1, AC-AM-2, AC-AM-3, AC-AM-4). Red test: the service does not exist yet.
 * Mocks the apiFetch boundary like contactFieldRevealService callers do.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

import {
  getRegistry,
  listRoles,
  createRole,
  updateRole,
  deleteRole,
  setRoleGrants,
  getContactAccess,
  setContactAccess,
} from './chatbotAccessService';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

function ok(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: () => Promise.resolve(body),
  });
}
function fail(status: number, body: unknown) {
  return Promise.resolve({
    ok: false,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}
function lastCall() {
  const c = apiFetch.mock.calls.at(-1) as [string, { method?: string; body?: string }?];
  return { url: c[0], method: c[1]?.method ?? 'GET', body: c[1]?.body ? JSON.parse(c[1].body) : undefined };
}

beforeEach(() => apiFetch.mockReset());

describe('chatbotAccessService', () => {
  it('AC-AM-15 getRegistry GETs the registry and returns {domains}', async () => {
    const registry = { domains: [{ name: 'inventory', label: 'Stock', fields: [] }] };
    apiFetch.mockReturnValue(ok(registry));
    expect(await getRegistry()).toEqual(registry);
    expect(lastCall()).toMatchObject({ url: '/api/v1/system/chatbot/access/registry', method: 'GET' });
  });

  it('AC-AM-1 listRoles GETs /roles; the backend answers a bare array', async () => {
    apiFetch.mockReturnValue(ok([{ id: 'r1', name: 'Purchasing' }]));
    expect(await listRoles()).toEqual([{ id: 'r1', name: 'Purchasing' }]);
    expect(lastCall()).toMatchObject({ url: '/api/v1/system/chatbot/roles', method: 'GET' });
  });

  it('AC-AM-1 and AC-AM-23 createRole POSTs audience_tier and sees_all_customers to /roles', async () => {
    const input = { name: 'Auditor', description: 'x', audience_tier: 'office', sees_all_customers: true };
    apiFetch.mockReturnValue(ok({ id: 'r9', ...input }));
    await createRole(input);
    expect(lastCall()).toMatchObject({ url: '/api/v1/system/chatbot/roles', method: 'POST', body: input });
  });

  it('AC-AM-23 updateRole PATCHes tier and customers on /roles/{id}', async () => {
    apiFetch.mockReturnValue(ok({ id: 'r1' }));
    await updateRole('r1', { name: 'Buying', audience_tier: 'dealer', sees_all_customers: false });
    expect(lastCall()).toMatchObject({
      url: '/api/v1/system/chatbot/roles/r1',
      method: 'PATCH',
      body: { name: 'Buying', audience_tier: 'dealer', sees_all_customers: false },
    });
  });

  it('AC-AM-3 deleteRole DELETEs /roles/{id}', async () => {
    apiFetch.mockReturnValue(ok({}));
    await deleteRole('r1');
    expect(lastCall()).toMatchObject({ url: '/api/v1/system/chatbot/roles/r1', method: 'DELETE' });
  });

  it('AC-AM-3 deleteRole 409 surfaces the server detail via extractApiError', async () => {
    apiFetch.mockReturnValue(fail(409, { detail: 'Role is held by 5 contacts' }));
    await expect(deleteRole('r1')).rejects.toThrow('Role is held by 5 contacts');
  });

  it('AC-AM-2 setRoleGrants PUTs the full replace to /roles/{id}/grants', async () => {
    apiFetch.mockReturnValue(ok({ id: 'r1' }));
    await setRoleGrants('r1', { domains: ['inventory'], fields: ['eta'] });
    expect(lastCall()).toMatchObject({
      url: '/api/v1/system/chatbot/roles/r1/grants',
      method: 'PUT',
      body: { domains: ['inventory'], fields: ['eta'] },
    });
  });

  it('AC-AM-4 getContactAccess GETs /contacts/{id}/access', async () => {
    const body = { roles: [], overrides: [], effective: { domains: [], attributes: [], sees_all_customers: false } };
    apiFetch.mockReturnValue(ok(body));
    expect(await getContactAccess('c1')).toEqual(body);
    expect(lastCall()).toMatchObject({ url: '/api/v1/system/chatbot/contacts/c1/access', method: 'GET' });
  });

  it('AC-AM-4 setContactAccess PUTs role_ids and overrides', async () => {
    apiFetch.mockReturnValue(ok({}));
    const payload = {
      role_ids: ['r1'],
      overrides: [{ domain_name: 'incoming', field_key: 'consignee', granted: true }],
    };
    await setContactAccess('c1', payload);
    expect(lastCall()).toMatchObject({
      url: '/api/v1/system/chatbot/contacts/c1/access',
      method: 'PUT',
      body: payload,
    });
  });

  it('AC-AM-4 a 422 on setContactAccess rejects with the extracted message', async () => {
    apiFetch.mockReturnValue(fail(422, { detail: 'Unknown role' }));
    await expect(setContactAccess('c1', { role_ids: ['x'], overrides: [] })).rejects.toThrow('Unknown role');
  });
});
