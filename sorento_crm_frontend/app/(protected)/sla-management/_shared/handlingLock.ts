import type { FormSLASourceType } from './formSLAService';

/**
 * "I'm handling this" handling-lock - pure state resolver + types.
 *
 * Once a form-SLA stage tracker is ESCALATED (`escalated_at` set - NOT `current_tier > 1`,
 * since a config may START above tier 1, e.g. project_sales begins at tier 2) AND the per-form
 * feature flag is on, the state-changing business CTAs (approve/reject/process/close/
 * submit/reopen) disable for everyone until an eligible team-chain member claims the
 * lock via "I'm handling this". The lock (`handled_by_id`) is SEPARATE from the
 * assignee (`assigned_to_id`) and is NOT the existing reassign-`takeover`.
 *
 * This module is intentionally framework-free (no React) so the resolver can be unit
 * tested in isolation and reused by the hook + banner. See PLAN-form-handling-lock.md
 * §5 (state machine) and §12 (Phase 1 UAC).
 */

export type HandlingLockState =
  | 'not_escalated'
  | 'unclaimed'
  | 'mine'
  | 'other_holds'
  | 'not_eligible'
  | 'admin_unclaimed'
  | 'admin_other_holds';

/** Minimal shape the lock cares about - a superset-compatible slice of the SLA tracker. */
export interface HandlingLockTracker {
  id: string;
  source_entity_type?: string | null;
  current_tier: number;
  /** Stamped only on a real escalation (never on initial assignment). NULL = never escalated. */
  escalated_at?: string | null;
  is_resolved?: boolean;
  /** The active handler lock. NULL/absent = unclaimed. */
  handled_by_id?: string | null;
  handled_by_name?: string | null;
  /** Bare wa.me digits (e.g. `60123...`) of the lock holder, when resolvable. NULL → plain text. */
  handled_by_wa_phone?: string | null;
  /** Naive-UTC ISO string of when the current lock was claimed. */
  handled_at?: string | null;
  assigned_to_id?: string | null;
}

export interface ResolveHandlingLockInput {
  activeTracker: HandlingLockTracker | null | undefined;
  /** Impersonation-aware acting user id (see useEffectiveUserId). */
  currentUserId: string | null;
  /** Is the acting user a member of the form's escalation team-chain? */
  isEligible: boolean;
  /** Is the acting user admin/superadmin? */
  isAdmin: boolean;
  /** Is the handling-lock feature flag on for this tracker's source_entity_type? */
  flagEnabledForType: boolean;
}

/**
 * Resolve the guided handling-lock state for the current viewer. Order matters - 
 * "mine" wins even for an admin who happens to hold the lock. See PLAN §5.
 */
export function resolveHandlingLockState(input: ResolveHandlingLockInput): HandlingLockState {
  const { activeTracker, currentUserId, isEligible, isAdmin, flagEnabledForType } = input;

  // No lock today: flag off, no active tracker, resolved, or never escalated.
  // "Escalated" = escalated_at stamped, NOT current_tier > 1 - a config may start above
  // tier 1 (project_sales begins at tier 2), so tier alone would falsely lock a fresh form.
  if (
    !flagEnabledForType ||
    !activeTracker ||
    activeTracker.is_resolved ||
    !activeTracker.escalated_at
  ) {
    return 'not_escalated';
  }

  const holderId = activeTracker.handled_by_id ?? null;
  const unclaimed = holderId == null;
  const isMine =
    currentUserId != null && holderId != null && String(holderId) === String(currentUserId);

  // I hold the lock - enabled - regardless of admin/eligibility.
  if (isMine) return 'mine';

  // Admin/superadmin: bypass only when unclaimed; must take over an active holder.
  if (isAdmin) return unclaimed ? 'admin_unclaimed' : 'admin_other_holds';

  // Eligible team-chain member.
  if (isEligible) return unclaimed ? 'unclaimed' : 'other_holds';

  // Page-permission-only viewer: read-only, no claim button.
  return 'not_eligible';
}

/**
 * Whether the state-changing business CTAs are enabled for this state. The lock is an
 * ADDITIONAL gate ANDed on top of the existing status+permission gates - when the state
 * is `not_escalated` this is `true`, so behaviour is exactly as today.
 */
export function businessCtasEnabledForState(state: HandlingLockState): boolean {
  return state === 'not_escalated' || state === 'mine' || state === 'admin_unclaimed';
}

/** Human-readable form label for banner copy (avoids leaking the snake_case type). */
export const HANDLING_LOCK_TYPE_LABELS: Record<FormSLASourceType, string> = {
  stock_inquiry: 'Stock Inquiry',
  purchase_request: 'Purchase Request',
  sponsorship_form: 'Sponsorship Form',
  complaint: 'Complaint',
  ticket: 'Ticket',
  workflow_submission: 'Workflow Submission',
  price_tag_request: 'Price Tag Request',
  sample_request: 'Sample Request',
};
