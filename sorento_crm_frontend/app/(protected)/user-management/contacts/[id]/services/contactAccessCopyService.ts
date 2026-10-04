import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

/**
 * CONTACT-BULK-ACCESS: copy one contact's access set to many contacts.
 *
 *   POST /api/v1/user-management/contacts/bulk-copy-access
 *     { source_contact_id, target_contact_ids, dry_run }
 *
 * One endpoint for the preview (`dry_run: true`, writes nothing) and the apply, so the
 * preview is exactly what apply writes. Contract: PLAN-contact-bulk-access-3oct.md.
 */

export type AccessCopyStatus = 'changed' | 'unchanged' | 'skipped' | 'failed';

export interface AccessCopyChange {
  facet: string;
  label: string;
  before: unknown;
  after: unknown;
  /** Human labels, never ids (list facets only; empty for a switch or the tier). */
  added: string[];
  removed: string[];
}

export interface AccessCopyResult {
  contact_id: string;
  label: string | null;
  status: AccessCopyStatus;
  changes: AccessCopyChange[];
  error: string | null;
}

export interface AccessCopyResponse {
  dry_run: boolean;
  /** `summary`: the source's access set as display lines (label, value), for step 1. */
  source: { id: string; label: string; summary: { label: string; value: string }[] };
  results: AccessCopyResult[];
  counts: Record<AccessCopyStatus, number>;
}

export async function bulkCopyContactAccess(body: {
  sourceContactId: string;
  targetContactIds: string[];
  dryRun: boolean;
}): Promise<AccessCopyResponse> {
  const response = await apiFetch('/api/user-management/contacts/bulk-copy-access', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source_contact_id: body.sourceContactId,
      target_contact_ids: body.targetContactIds,
      dry_run: body.dryRun,
    }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to copy access'));
  }
  return (await response.json()) as AccessCopyResponse;
}
