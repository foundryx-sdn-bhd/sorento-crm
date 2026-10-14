/**
 * Option sets for the portal revision config modal.
 *
 * `allowed_statuses` is matched against the entity's own lifecycle `status`
 * (see `PortalRevisionService.resolve_policy`), so these lists mirror the
 * lifecycle statuses each type can actually carry - not its approval status.
 */

export const PORTAL_REVISION_TYPE_LABELS: Record<string, string> = {
  stock_inquiry: 'Stock Inquiry',
  purchase_request: 'Purchase Request',
  sponsorship_form: 'Sponsorship Form',
  complaint: 'Complaint',
  price_tag_request: 'Price Tag Request',
};

export const PORTAL_REVISION_STATUS_OPTIONS: Record<
  string,
  ReadonlyArray<{ value: string; label: string }>
> = {
  stock_inquiry: [
    { value: 'new', label: 'New' },
    { value: 'pending_project_sales', label: 'Pending project sales' },
    { value: 'pending_purchasing', label: 'Pending purchasing' },
    { value: 'responded', label: 'Responded' },
    { value: 'rejected', label: 'Rejected' },
  ],
  purchase_request: [
    { value: 'draft', label: 'Draft' },
    { value: 'submitted', label: 'Submitted' },
    { value: 'approved', label: 'Approved' },
    { value: 'rejected', label: 'Rejected' },
    { value: 'processed_by_cs', label: 'Processed by CS' },
    { value: 'closed', label: 'Closed' },
  ],
  sponsorship_form: [
    { value: 'draft', label: 'Draft' },
    { value: 'submitted', label: 'Submitted' },
    { value: 'approved', label: 'Approved' },
    { value: 'rejected', label: 'Rejected' },
    { value: 'processed_by_cs', label: 'Processed by CS' },
    { value: 'closed', label: 'Closed' },
  ],
  complaint: [
    { value: 'new', label: 'New' },
    { value: 'submitted', label: 'Submitted' },
    { value: 'updated', label: 'Updated' },
    { value: 'responded', label: 'Responded' },
    { value: 'approved', label: 'Approved' },
    { value: 'rejected', label: 'Rejected' },
    { value: 'processed_by_cs', label: 'Processed by CS' },
    { value: 'closed', label: 'Closed' },
  ],
  price_tag_request: [
    { value: 'new', label: 'New' },
    { value: 'designing', label: 'Designing' },
    { value: 'changes_requested', label: 'Changes requested' },
  ],
};

export function portalRevisionTypeLabel(sourceEntityType: string): string {
  return (
    PORTAL_REVISION_TYPE_LABELS[sourceEntityType] ??
    sourceEntityType.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

export function portalRevisionStatusLabel(
  sourceEntityType: string,
  status: string,
): string {
  const match = (PORTAL_REVISION_STATUS_OPTIONS[sourceEntityType] ?? []).find(
    (option) => option.value === status,
  );
  return match?.label ?? status.replace(/[_-]+/g, ' ');
}
