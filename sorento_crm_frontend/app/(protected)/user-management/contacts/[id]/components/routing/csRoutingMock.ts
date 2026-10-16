/**
 * Feature service - Contact CS-routing predicate rules (R2). Phase-2 WIRED to the
 * real FastAPI endpoints under /api/v1/user-management/contacts.
 *
 *   GET    .../{contactId}/cs-routing                → { pins: RoutingRule[] }
 *   GET    .../cs-routing/candidates                 → { candidates: CsCandidate[] }
 *   GET    .../cs-routing/fields?use_case=<uc>       → { fields: RoutableField[] }
 *   PUT    .../{contactId}/cs-routing                body { rules: [{ use_case, cs_pic_user_id, match_conditions, priority }] }
 *                                                    → { pins: RoutingRule[] }
 *
 * Uniqueness: one rule per (contact, use_case, canonical(match_conditions)). Save
 * = replace, in ONE request: the server validates every rule before it replaces
 * any, so a refused rule leaves the stored rules untouched. Priority = list order
 * (lowest priority wins in the resolver).
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { Predicate, RoutableField } from './predicateTypes';

const BASE = '/api/v1/user-management/contacts';

export interface CsCandidate {
  id: string;
  name: string;
  email: string;
}

export interface RoutingRule {
  id: string;
  use_case: string;
  cs_pic_user_id: string | null;
  cs_pic_name: string | null;
  // No CS: the form finishes at approval. The server stores it as a null PIC, which a new
  // rule with nothing chosen yet also has, so the editor keeps the choice in this flag.
  no_cs?: boolean;
  match_conditions: Predicate[];
  priority: number;
}

/** Server rules come back with a null PIC for No CS. */
function fromServer(pins: RoutingRule[]): RoutingRule[] {
  return pins
    .map((p) => ({ ...p, no_cs: p.cs_pic_user_id == null }))
    .sort((a, b) => a.priority - b.priority);
}

export const CS_USE_CASES: { key: string; label: string }[] = [
  { key: 'purchase_request', label: 'Purchase Request' },
  { key: 'sponsorship_form', label: 'Sponsorship Form' },
];

export async function getRoutingRules(contactId: string): Promise<RoutingRule[]> {
  const r = await apiFetch(`${BASE}/${contactId}/cs-routing`);
  if (!r.ok) throw new Error(await extractApiError(r, 'Failed to load routing rules'));
  const data = await r.json();
  return fromServer((data?.pins ?? []) as RoutingRule[]);
}

export async function getCsCandidates(): Promise<CsCandidate[]> {
  const r = await apiFetch(`${BASE}/cs-routing/candidates`);
  if (!r.ok) throw new Error(await extractApiError(r, 'Failed to load CS candidates'));
  const data = await r.json();
  return (data?.candidates ?? []) as CsCandidate[];
}

export async function getRoutableFields(useCase: string): Promise<RoutableField[]> {
  const r = await apiFetch(`${BASE}/cs-routing/fields?use_case=${encodeURIComponent(useCase)}`);
  if (!r.ok) throw new Error(await extractApiError(r, 'Failed to load routable fields'));
  const data = await r.json();
  return (data?.fields ?? []) as RoutableField[];
}

/**
 * Replace this contact's routing config with `rules` in one request, priority = each
 * rule's position in the list. Rules with neither a CS PIC nor No CS chosen are skipped
 * (incomplete). A refused rule throws the server's message ("Rule 2: ...") and
 * nothing is saved. Returns the freshly-persisted rules (with server-assigned ids).
 */
export async function saveRoutingRules(
  contactId: string,
  rules: RoutingRule[],
): Promise<RoutingRule[]> {
  const payload = rules.flatMap((rule, i) =>
    rule.cs_pic_user_id || rule.no_cs
      ? [
          {
            use_case: rule.use_case,
            cs_pic_user_id: rule.no_cs ? null : rule.cs_pic_user_id,
            match_conditions: rule.match_conditions ?? [],
            priority: i,
          },
        ]
      : [],
  );
  const r = await apiFetch(`${BASE}/${contactId}/cs-routing`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rules: payload }),
  });
  if (!r.ok) throw new Error(await extractApiError(r, 'Failed to save routing rules'));
  const data = await r.json();
  return fromServer((data?.pins ?? []) as RoutingRule[]);
}
