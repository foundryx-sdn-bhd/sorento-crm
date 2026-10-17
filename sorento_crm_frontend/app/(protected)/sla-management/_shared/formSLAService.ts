import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { ConversationSLATrackingDetail } from '@/app/(protected)/sla-management/conversation-sla-tracking/types/conversationSLATracking.types';

/* -------------------------------------------------------------------------------------
 * "I'm handling this" handling-lock (PLAN-form-handling-lock.md §8) - LIVE (Phase 2).
 *
 * The lock is a SEPARATE field (`handled_by_id`) from the assignee and is distinct from
 * the existing reassign-`takeover`. It only bites while a FORM-SLA tracker is escalated
 * (`current_tier > 1`) AND the per-form feature flag is on. The active-tracker read
 * (`getFormHandlingTrackers`) also returns viewer-scoped inputs the FE state resolver
 * needs: `flag_enabled`, `viewer_eligible`, `viewer_is_admin`.
 *
 * All routes are form-SLA scoped under /api/v1/sla-management/form-sla-tracking:
 *
 *   GET  ?source_entity_type=<type>&source_entity_id=<id>
 *     res:  200 { data: FormHandlingTracker[] }  (active/unresolved stage rows)
 *
 *   POST .../{tracking_id}/claim
 *     req:  {}                              (actor from auth principal)
 *     res:  200 { handled_by_id, handled_by_name, handled_at, ... }
 *     guard: flag on for source_entity_type; tracker is form + escalated + unresolved;
 *            actor in the escalation team-chain; atomic UPDATE ... WHERE handled_by_id IS NULL.
 *     errors: 409 already claimed; 403 not eligible / not escalated / flag off.
 *
 *   POST .../{tracking_id}/take-over
 *     req:  { expected_handler_id: string }  (optimistic-concurrency guard - ALWAYS the
 *                                             current holder's id; 409 if null/omitted)
 *     res:  200 { handled_by_id (=actor), handled_by_name, handled_at, previous_handler_id }
 *     guard: same eligibility; conditional UPDATE ... WHERE handled_by_id = expected_handler_id.
 *     errors: 409 optimistic mismatch (lock moved); 403 not eligible.
 *
 *   POST .../{tracking_id}/release
 *     req:  {}
 *     res:  200 { handled_by_id: null, ... }
 *     guard: actor == current handled_by_id (holder-only; admin must take-over then act).
 *     errors: 403 non-holder.
 *
 * BUSINESS-CTA GUARD (server-side, every approve/reject/process/close/submit/reopen route):
 * - flag off for type OR tracker not escalated → allow (today's behaviour).
 * - else require actor.id == handled_by_id OR (actor admin/superadmin AND handled_by_id IS NULL).
 * - else 403 "This form is being handled by <name>. Take over to act."
 *
 * PR/SF share a FE component - gate on the ACTIVE tracker's `source_entity_type`, not a
 * hardcoded form name. The BE re-checks every guard server-side (never trust the FE).
 * ----------------------------------------------------------------------------------- */

export type FormSLASourceType =
  | 'stock_inquiry'
  | 'purchase_request'
  | 'sponsorship_form'
  | 'complaint'
  | 'ticket'
  | 'workflow_submission'
  | 'price_tag_request'
  | 'sample_request';

export async function getFormSLATrackers(
  sourceEntityType: FormSLASourceType,
  sourceEntityId: string,
): Promise<ConversationSLATrackingDetail[]> {
  const sp = new URLSearchParams({
    source_entity_type: sourceEntityType,
    source_entity_id: sourceEntityId,
  });
  const r = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/by-source?${sp.toString()}`,
  );
  if (!r.ok) {
    throw new Error('Failed to load SLA trackers');
  }
  return r.json();
}

export interface FormSLAEscalateResult {
  tracking_id: string;
  current_tier: number;
  assigned_to_id: string | null;
  assigned_to_name: string | null;
  due_at: string | null;
  escalation_reason: string | null;
}

/**
 * Manually force-escalate a form-SLA tracker to the next tier (TCK-28).
 * Backend gates on the `sla.form.escalate` permission; 422 on top-tier /
 * resolved / no-assignee, 404 if missing.
 */
export async function escalateFormTracking(
  trackingId: string,
  reason: string,
): Promise<FormSLAEscalateResult> {
  const r = await apiFetch(
    `/api/v1/sla-management/form-sla-tracking/${trackingId}/escalate`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    },
  );
  if (!r.ok) throw new Error(await _readError(r));
  return r.json();
}

/**
 * Active form-SLA stage row enriched with the handling-lock + viewer-scoped fields the
 * FE state resolver needs. Returned by GET /form-sla-tracking?source_entity_type&source_entity_id.
 */
export interface FormHandlingTracker {
  tracking_id: string;
  current_tier: number;
  due_at: string | null;
  due_at_resolution: string | null;
  is_resolved: boolean;
  assigned_to_id: string | null;
  assigned_to_name: string | null;
  source_entity_type: FormSLASourceType;
  source_entity_id: string;
  escalation_reason: string | null;
  /** Stamped only on a real escalation (never on initial assignment). NULL = never escalated. */
  escalated_at: string | null;
  /** The active handler lock. NULL = unclaimed. */
  handled_by_id: string | null;
  handled_by_name: string | null;
  handled_at: string | null;
  /** Per-form feature flag; the FE hides the whole lock UI when off. */
  flag_enabled: boolean;
  /** Viewer is a member of this tracker's escalation team-chain. */
  viewer_eligible?: boolean;
  /** Viewer is admin/superadmin. */
  viewer_is_admin?: boolean;
  /** Only present on a take-over response. */
  previous_handler_id?: string | null;
  /* --- Skip capability (UAC-form-sla-skip-stage) ------------------------------ *
   * Rides along on this query rather than costing a second round-trip. NULL
   * `skip_event` = this stage declares no skip. `can_skip` is the SERVER's verdict
   * (stage skippable AND viewer holds the adapter's per-entity permission) - the
   * permission is never inferable from config alone.                             */
  skip_event?: string | null;
  skip_action_label?: string | null;
  can_skip?: boolean;
}

/**
 * Active (unresolved) handling-lock stage rows for a form entity. The active stage is the
 * first `is_resolved === false` row. Carries `flag_enabled` / `viewer_eligible` /
 * `viewer_is_admin` - the inputs `resolveHandlingLockState` cannot compute client-side.
 */
export async function getFormHandlingTrackers(
  sourceEntityType: FormSLASourceType,
  sourceEntityId: string,
): Promise<FormHandlingTracker[]> {
  const sp = new URLSearchParams({
    source_entity_type: sourceEntityType,
    source_entity_id: sourceEntityId,
  });
  const r = await apiFetch(
    `/api/v1/sla-management/form-sla-tracking?${sp.toString()}`,
  );
  if (!r.ok) throw new Error(await extractApiError(r, 'Failed to load handling status'));
  const body = await r.json();
  return (body?.data ?? []) as FormHandlingTracker[];
}

/** Claim the handling lock ("I'm handling this"). 409 if already held; 403 if not eligible. */
export async function claimHandling(trackingId: string): Promise<FormHandlingTracker> {
  const r = await apiFetch(
    `/api/v1/sla-management/form-sla-tracking/${trackingId}/claim`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' } },
  );
  if (!r.ok) throw new Error(await extractApiError(r, 'Failed to claim handling'));
  return r.json();
}

/**
 * Take over the handling lock from the current holder. `expectedHandlerId` MUST be the
 * current holder's id (optimistic-concurrency guard) - the backend 409s on mismatch or
 * when it is null/omitted, so callers always pass the tracker's current `handled_by_id`.
 */
export async function takeOverHandling(
  trackingId: string,
  expectedHandlerId: string | null,
): Promise<FormHandlingTracker> {
  const r = await apiFetch(
    `/api/v1/sla-management/form-sla-tracking/${trackingId}/take-over`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_handler_id: expectedHandlerId }),
    },
  );
  if (!r.ok) throw new Error(await extractApiError(r, 'Failed to take over handling'));
  return r.json();
}

/** Release the handling lock. Holder-only (403 otherwise). */
export async function releaseHandling(trackingId: string): Promise<FormHandlingTracker> {
  const r = await apiFetch(
    `/api/v1/sla-management/form-sla-tracking/${trackingId}/release`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' } },
  );
  if (!r.ok) throw new Error(await extractApiError(r, 'Failed to release handling'));
  return r.json();
}

export interface FormSLAConfig {
  id: string;
  source_entity_type: FormSLASourceType;
  stage_code: string;
  policy_id: string;
  agent_code: string;
  team_set_code: string | null;
  start_event: string;
  respond_event: string | null;
  resolve_event: string | null;
  next_config_id: string | null;
  advance_on_event: string | null;
  is_active: boolean;
  notify_assignee?: boolean;
  notify_on_escalation?: boolean;
  /** Undo grace window for this stage, in seconds. null = inherit the global default. */
  grace_seconds?: number | null;
  policy_code?: string | null;
  policy_name?: string | null;
  next_stage_code?: string | null;
  created_at: string;
  updated_at: string;
}

export interface FormSLAConfigInput {
  source_entity_type: FormSLASourceType;
  stage_code: string;
  policy_id: string;
  agent_code: string;
  team_set_code?: string | null;
  start_event: string;
  respond_event?: string | null;
  resolve_event?: string | null;
  next_config_id?: string | null;
  advance_on_event?: string | null;
  is_active?: boolean;
  notify_assignee?: boolean;
  notify_on_escalation?: boolean;
  /** null = inherit the global default rather than pinning this stage to no grace. */
  grace_seconds?: number | null;
}

export async function listFormSLAConfigs(filters: {
  source_entity_type?: FormSLASourceType;
  is_active?: boolean;
} = {}): Promise<FormSLAConfig[]> {
  const sp = new URLSearchParams();
  if (filters.source_entity_type) sp.set('source_entity_type', filters.source_entity_type);
  if (filters.is_active !== undefined) sp.set('is_active', String(filters.is_active));
  const qs = sp.toString();
  const r = await apiFetch(
    `/api/v1/sla-management/form-sla-config${qs ? `?${qs}` : ''}`,
  );
  if (!r.ok) {
    throw new Error('Failed to load form SLA configs');
  }
  return r.json();
}

export async function getFormSLAConfig(id: string): Promise<FormSLAConfig> {
  const r = await apiFetch(`/api/v1/sla-management/form-sla-config/${id}`);
  if (!r.ok) {
    throw new Error('Failed to load form SLA config');
  }
  return r.json();
}

async function _readError(r: Response): Promise<string> {
  try {
    const j = await r.json();
    const d = j.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg).filter(Boolean).join(' ');
    if (d && typeof d === 'object' && 'message' in d) return String((d as { message?: string }).message);
    return j.message || 'Request failed';
  } catch {
    return 'Request failed';
  }
}

export async function createFormSLAConfig(input: FormSLAConfigInput): Promise<FormSLAConfig> {
  const r = await apiFetch(`/api/v1/sla-management/form-sla-config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!r.ok) throw new Error(await _readError(r));
  return r.json();
}

export async function updateFormSLAConfig(
  id: string,
  input: Partial<FormSLAConfigInput>,
): Promise<FormSLAConfig> {
  const r = await apiFetch(`/api/v1/sla-management/form-sla-config/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!r.ok) throw new Error(await _readError(r));
  return r.json();
}

export async function deleteFormSLAConfig(id: string): Promise<void> {
  const r = await apiFetch(`/api/v1/sla-management/form-sla-config/${id}`, {
    method: 'DELETE',
  });
  if (!r.ok) throw new Error(await _readError(r));
}

/**
 * Allowed event names per form type. Source of truth is the backend service:
 * any event added here must also be emitted by the backend on the corresponding
 * state transition (see procurement_service.py / complaints_service.py).
 */
export const FORM_SLA_EVENT_OPTIONS: Record<FormSLASourceType, readonly string[]> = {
  stock_inquiry: [
    'submit',
    'project_sales_approve',
    'project_sales_reject',
    'purchasing_decide',
    'purchasing_respond',
    'voided',
  ],
  purchase_request: [
    'submit',
    'send_for_approval',
    'reject_submitted',
    'approved',
    'approval_rejected',
    'resolved',
    'voided',
  ],
  sponsorship_form: [
    'submit',
    'send_for_approval',
    'reject_submitted',
    'approved',
    'approval_rejected',
    'resolved',
    'voided',
  ],
  complaint: [
    'submit',
    'technical_team_response',
    'approved',
    'rejected',
    // Skip event: resolves the technical stage WITHOUT advancing to customer service.
    // Deliberately absent from `advance_on_event` - that is what closes the chain.
    'settled_on_site',
    'resolved',
    'voided',
  ],
  ticket: [
    'submit',
    'assigned',
    'responded',
    'resolved',
  ],
  // Mirrors the events the live workflow_submission stage row already declares.
  workflow_submission: [
    'submission_created',
    'submitted',
    'approved',
    'rejected',
  ],
  // Single marketing stage (D8): the portal submit route emits only
  // 'submit'. No Void event here - void is a CRM action, not a form event.
  price_tag_request: ['submit'],
  // No stage config ships for sample requests; the detail tab reads empty.
  sample_request: [],
};

export const FORM_SLA_TYPE_LABELS: Record<FormSLASourceType, string> = {
  stock_inquiry: 'Stock Inquiry',
  purchase_request: 'Purchase Request',
  sponsorship_form: 'Sponsorship Form',
  complaint: 'Complaint',
  ticket: 'Ticket',
  workflow_submission: 'Workflow Submission',
  price_tag_request: 'Price Tag Request',
  sample_request: 'Sample Request',
};
