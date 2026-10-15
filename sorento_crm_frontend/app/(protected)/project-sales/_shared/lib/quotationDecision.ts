import type { QuotationDocument } from '../services/quotationDocumentService';

/**
 * Where a quotation stands, in the one word a row or a badge has space for.
 *
 * The backend resolves `customer_decision` (acceptance beats a request when both stamps are set,
 * because a customer who asked for a lower price and then signed has accepted). This turns that
 * answer into a label and a tone, in ONE place, so the project's quotation list and the document
 * itself cannot end up telling a salesperson two different things about the same record.
 *
 * The customer's decision REPLACES Issued rather than sitting beside it: an issued quotation the
 * customer has answered is not still waiting, and a row carrying both reads as neither.
 */
export type QuotationStanding = {
  label: string;
  variant: 'success' | 'warning' | 'secondary' | 'primary' | 'destructive';
};

export function quotationStanding(document: QuotationDocument): QuotationStanding {
  // The approval rungs come first (A1-A5): the next revision's standing outranks what the
  // customer said about the one they hold.
  if (document.approval_status_key === 'pending_approval') {
    return { label: 'Pending approval', variant: 'warning' };
  }
  if (document.approval_status_key === 'approved') {
    return { label: 'Approved', variant: 'primary' };
  }
  if (document.approval_status_key === 'rejected') {
    return { label: 'Rejected', variant: 'destructive' };
  }
  if (document.customer_decision === 'accepted') {
    return { label: 'Accepted', variant: 'success' };
  }
  if (document.customer_decision === 'changes_requested') {
    return { label: 'Changes requested', variant: 'warning' };
  }
  return document.is_issued
    ? { label: 'Issued', variant: 'success' }
    : { label: 'Draft', variant: 'secondary' };
}

/**
 * Whether the customer is currently waiting on a revision.
 *
 * Read off the resolved decision and never off `changes_requested_at`: those words stay on the
 * row as history after an acceptance, and a banner that read the raw stamp would go on asking
 * somebody to revise a quotation that is already won.
 */
export function hasOpenChangeRequest(document: QuotationDocument): boolean {
  return document.customer_decision === 'changes_requested';
}

/**
 * Whether this quotation is currently blocked from being sent: a line is below its floor and no
 * manager has approved it. One expression, read by the next-action choice and the banner.
 */
export function isBlockedByApproval(document: QuotationDocument): boolean {
  return Boolean(document.requires_approval) && document.approval_status_key !== 'approved';
}

/** Pending or approved: nobody edits the quotation until the creator revises it (D1, D2). */
export function isLockedForApproval(document: QuotationDocument): boolean {
  return (
    document.approval_status_key === 'pending_approval' ||
    document.approval_status_key === 'approved'
  );
}

export type NextAction = {
  kind: 'send_for_approval' | 'approve' | 'sign' | 'send' | 'none';
  /** One short sentence for the header when there is no button to press. */
  hint?: string;
};

/**
 * The ONE next step the header leads with. A manager on a pending quotation decides it; a
 * salesperson signs first, then sends for approval when a line is below its floor, otherwise
 * sends to the customer. Everything else lives behind the gear.
 */
export function nextAction(
  document: QuotationDocument,
  { canEdit, canApprove, isSigned }: { canEdit: boolean; canApprove: boolean; isSigned: boolean },
): NextAction {
  if (document.approval_status_key === 'pending_approval') {
    if (canApprove) return { kind: 'approve' };
    if (canEdit && !isSigned) return { kind: 'sign', hint: 'With a manager for approval' };
    return { kind: 'none', hint: 'With a manager for approval' };
  }
  if (!canEdit) return { kind: 'none' };
  if (isBlockedByApproval(document)) return { kind: 'send_for_approval' };
  return { kind: isSigned ? 'send' : 'sign' };
}
