/**
 * ============================================================================
 * Product-discontinued notification scopes - feature service
 * ============================================================================
 * Layering: UI (product-discontinued-scope-editor) -> hook
 * (use-product-discontinued-scope-brands) -> THIS service -> lib/api -> backend.
 * No component fetches directly.
 *
 * A scope = one (company, brand) pair on a user. company_id null = all companies
 * (which forces brand null); brand_id null = all brands in that company. The
 * channel toggles stay HOW the user is notified; scopes decide WHAT they hear
 * about. See documentation/plans/PLAN-product-discontinued-brand-scope.md.
 *
 * -- BACKEND CONTRACT (live since slice S2) -----------------------------------
 *
 * 1. Brand options for one company (the ONLY new call this screen needs)
 *
 *    GET /api/v1/master-data/brands/select?company_id=<uuid>
 *      200 -> [{ id, brand_code, brand_name }]
 *      Returns the active brands of the NAMED company, independent of the
 *      caller's active-company scope (an admin edits scopes for companies they
 *      are not currently switched into). Omitting `company_id` keeps today's
 *      behaviour exactly. Open to any signed-in user since NS-SHARED-LOOKUPS (1 Oct 2026);
 *      the company must still be one the caller can reach.
 *
 * 2. Read - the scopes ride the existing user payload, no dedicated GET
 *
 *    GET /api/v1/user-management/users/{id}   (and /users/me)
 *      200 -> { ..., product_discontinued_scopes: [
 *                 { id, company_id, company_name, brand_id, brand_code, brand_name }
 *               ] }
 *      company_id null = all companies, brand_id null = all brands. The field
 *      must be added to ALL THREE manual `UserResponse(**user_dict)` builders
 *      or it never reaches this screen.
 *
 * 3. Write - replace-all on the existing profile PUT, no new endpoint
 *
 *    PUT /api/v1/user-management/users/{id}
 *      body { ..., product_discontinued_scopes: [ { company_id, brand_id } ] }
 *    - omitted  -> scopes untouched
 *    - []       -> all scopes cleared (the user then hears nothing)
 *    - provided -> replace-all, deduped server-side
 *      422 (AppException) when a brand does not belong to the named company.
 *      company_id null forces brand_id null.
 *      Permission: the same dependency that already guards the notification
 *      toggles on this route - no new permission.
 *
 * 4. Deep link consumed by the notice (already served by the products list GET)
 *
 *    GET /api/v1/master-data/products?discontinued_batch_id=<uuid>&brand_id=a,b
 *      `brand_id` accepts a comma-separated list and filters with IN; a single
 *      value behaves exactly as before.
 * ============================================================================
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

export interface ScopeBrandOption {
  id: string;
  brand_code: string;
  brand_name: string;
}

/** Active brands of one company, for the scope editor's brand picker. */
export async function getScopeBrandOptions(
  companyId: string,
): Promise<ScopeBrandOption[]> {
  const response = await apiFetch(
    `/api/v1/master-data/brands/select?company_id=${encodeURIComponent(companyId)}`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load brands'));
  }
  return response.json();
}
