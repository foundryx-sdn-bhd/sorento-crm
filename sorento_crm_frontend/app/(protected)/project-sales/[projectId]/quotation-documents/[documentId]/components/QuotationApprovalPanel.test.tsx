/**
 * S14-S16 - the price-floor approval block.
 *
 * The claim that matters most is the FIRST one, and it is the same one the backend suite pins
 * hardest: the ordinary quotation must be completely unaffected. This block sits above the
 * letterhead on a screen every salesperson uses every day, so a version of it that rendered an
 * empty amber bar, or a "Send for approval" button, on a quotation nobody ever meant to gate
 * would be a daily irritation that looks like the feature working.
 *
 * The rest are the gate's own promises:
 *
 * - a block NAMES the reason and the state (QUOTE-APPROVAL-UX E8: information only)
 * - it carries NO buttons: every action (Send for approval, Approve, Reject) lives in the
 *   header's single primary slot, and there is no "Back to draft" anywhere (C3). The header's
 *   own behaviour is pinned in QuotationDocumentClient.test.tsx.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { StatusGraph } from '@/app/(protected)/system-management/status-graphs/types/statusGraph.types';
import type { QuotationDocument } from '../../../../_shared/services/quotationDocumentService';

let granted = new Set<string>();

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => granted.has(slug),
  useHasAnyPermission: (slugs: string[]) => slugs.some((slug) => granted.has(slug)),
  usePermissions: () => ({ permissions: [...granted], permissionSet: granted, isLoading: false }),
}));

import { QuotationApprovalPanel, isBlockedByApproval } from './QuotationApprovalPanel';

/** The seeded graph, in the shape `/quotation-approval-graph` answers with. */
function graph(): StatusGraph {
  const rungs = [
    { key: 'draft', label: 'Draft', sort_order: 0, is_initial: true },
    { key: 'rejected', label: 'Rejected', sort_order: 1, is_initial: false },
    { key: 'pending_approval', label: 'Pending Approval', sort_order: 2, is_initial: false },
    { key: 'approved', label: 'Approved', sort_order: 3, is_initial: false },
    { key: 'issued', label: 'Issued', sort_order: 4, is_initial: false },
  ];
  const statuses = rungs.map((rung) => ({
    id: `s-${rung.key}`,
    entity_type: 'quotation',
    scope_id: null,
    key: rung.key,
    label: rung.label,
    category: null,
    color_hex: null,
    description: null,
    sort_order: rung.sort_order,
    is_initial: rung.is_initial,
    is_terminal: false,
    is_active: true,
    is_archived: false,
    is_default: rung.is_initial,
    is_system: false,
  }));
  const edges = [
    ['draft', 'pending_approval', 'Send for approval', 0],
    ['pending_approval', 'approved', 'Approve', 0],
    ['pending_approval', 'rejected', 'Reject', 1],
    ['rejected', 'draft', 'Back to draft', 0],
    ['approved', 'issued', 'Issued to the customer', 0],
    ['issued', 'pending_approval', 'Send for approval', 0],
  ] as const;
  return {
    entity_type: 'quotation',
    requested_scope_id: null,
    resolved_scope_id: null,
    is_fork: false,
    statuses,
    transitions: edges.map(([from, to, label, sort_order], index) => ({
      id: `t${index}`,
      entity_type: 'quotation',
      scope_id: null,
      from_status_id: `s-${from}`,
      to_status_id: `s-${to}`,
      label,
      sort_order,
      trigger_mode: 'manual' as const,
      conditions_json: null,
    })),
  };
}

function quotationDocument(overrides: Partial<QuotationDocument> = {}): QuotationDocument {
  return {
    id: 'd1',
    project_id: 'p1',
    document_no: 'SRT/Q/2026/0141',
    our_ref: 'SRT/Q/2026/0141',
    your_ref: null,
    doc_date: '2026-02-26',
    recipient_party_id: null,
    recipient_name_snapshot: 'Nadi Cergas Sdn Bhd',
    recipient_address_snapshot: null,
    recipient_phone_snapshot: null,
    attn_name: null,
    subject_title: 'CADANGAN MEMBINA PANGSAPURI',
    cover_letter_html: null,
    terms_html: null,
    signatory_name: null,
    signatory_phone: null,
    scopes: [],
    grand_total: '0.00',
    issue_count: 0,
    current_issue_no: null,
    is_issued: false,
    created_at: null,
    updated_at: null,
    approval_status_id: null,
    approval_status_key: null,
    approval_status_label: null,
    approval_rejected_reason: null,
    requires_approval: false,
    below_floor_line_count: 0,
    ...overrides,
  };
}

function renderPanel(
  document: QuotationDocument,
  handlers: {
    onMove?: (id: string) => void;
    onApprove?: () => void;
    onReject?: (reason: string) => void;
  } = {},
  canEdit = true,
) {
  return render(
    <QuotationApprovalPanel document={document} />,
  );
}

describe('QuotationApprovalPanel: the ordinary quotation', () => {
  it('renders nothing at all when no line is below its floor', () => {
    granted = new Set(['projects.quotations.approve']);
    const { container } = renderPanel(quotationDocument());

    expect(container).toBeEmptyDOMElement();
    expect(isBlockedByApproval(quotationDocument())).toBe(false);
  });

  it('renders nothing once a re-priced quotation is back above the floor', () => {
    // Approved once, then edited back up. There is nothing left to say about the floor, so the
    // block goes away rather than lingering as a green bar nobody needs.
    granted = new Set();
    const { container } = renderPanel(
      quotationDocument({
        approval_status_key: 'approved',
        approval_status_id: 's-approved',
        requires_approval: false,
        below_floor_line_count: 0,
      }),
    );

    expect(container).toBeEmptyDOMElement();
  });
});

describe('QuotationApprovalPanel: the block', () => {
  it('E8: names the reason in a sentence and renders no buttons at all', () => {
    granted = new Set(['projects.quotations.approve']);
    renderPanel(quotationDocument({ requires_approval: true, below_floor_line_count: 2 }));

    expect(screen.getByText(/2 lines are priced below their floor/i)).toBeInTheDocument();
    // The next action moved to the header; the banner only states the situation.
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('counts one line in the singular', () => {
    granted = new Set();
    renderPanel(quotationDocument({ requires_approval: true, below_floor_line_count: 1 }));

    expect(screen.getByText(/One line is priced below its floor/i)).toBeInTheDocument();
  });

  it('offers a reader with no edit rights the reason but no action', () => {
    granted = new Set();
    renderPanel(
      quotationDocument({ requires_approval: true, below_floor_line_count: 1 }),
      {},
      false,
    );

    expect(screen.getByText(/One line is priced below its floor/i)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Send for approval' }),
    ).not.toBeInTheDocument();
  });

  it('says it is waiting, and offers no self-serve move, while it sits with a manager', () => {
    granted = new Set();
    renderPanel(
      quotationDocument({
        approval_status_id: 's-pending_approval',
        approval_status_key: 'pending_approval',
        requires_approval: true,
        below_floor_line_count: 1,
      }),
    );

    expect(screen.getByText(/with a manager for approval/i)).toBeInTheDocument();
    // Approving is not something the salesperson can do to their own quotation.
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
  });

  it('C3: shows the manager reason on a rejected quotation, with no Back to draft and no buttons', () => {
    granted = new Set();
    renderPanel(
      quotationDocument({
        approval_status_id: 's-rejected',
        approval_status_key: 'rejected',
        approval_rejected_reason: 'Bring the WC suite back to RM 240.',
        requires_approval: true,
        below_floor_line_count: 1,
      }),
    );

    expect(screen.getByText(/A manager sent this back/i)).toBeInTheDocument();
    expect(screen.getByText(/Bring the WC suite back to RM 240\./i)).toBeInTheDocument();
    expect(screen.queryByText(/Back to draft/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('says an approved quotation can be issued', () => {
    granted = new Set();
    renderPanel(
      quotationDocument({
        approval_status_id: 's-approved',
        approval_status_key: 'approved',
        requires_approval: true,
        below_floor_line_count: 3,
      }),
    );

    expect(screen.getByText(/a manager has approved it/i)).toBeInTheDocument();
    expect(isBlockedByApproval(
      quotationDocument({ requires_approval: true, approval_status_key: 'approved' }),
    )).toBe(false);
  });
});

describe('QuotationApprovalPanel: the manager', () => {
  const pending = () =>
    quotationDocument({
      approval_status_id: 's-pending_approval',
      approval_status_key: 'pending_approval',
      requires_approval: true,
      below_floor_line_count: 1,
    });

  it('E8: a holder of the approve grant still sees sentences only - Approve and Reject are in the header', () => {
    granted = new Set(['projects.quotations.approve']);
    renderPanel(pending());

    expect(screen.getByText(/with a manager for approval/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Reject/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
