/**
 * The one rule about what the customer did, and the one place it is expressed.
 *
 * Both stamps can be set at once: somebody who asked for a lower price can still sign. Every
 * surface has to reach the SAME answer about that document - the counter-sign page, the
 * Signatures badge, the banner on the document and the Status column of the project's quotation
 * list - so the ranking lives here and is read, never re-derived from the two dates.
 */
import { describe, expect, it } from 'vitest';
import type { QuotationDocument } from '../services/quotationDocumentService';
import { hasOpenChangeRequest, quotationStanding } from './quotationDecision';

function document(overrides: Partial<QuotationDocument> = {}): QuotationDocument {
  return {
    id: 'd1',
    project_id: 'p1',
    document_no: 'SRT/Q/2026/0141',
    our_ref: 'SRT/Q/2026/0141',
    your_ref: null,
    doc_date: null,
    recipient_party_id: null,
    recipient_name_snapshot: null,
    recipient_address_snapshot: null,
    recipient_phone_snapshot: null,
    attn_name: null,
    subject_title: null,
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
    ...overrides,
  };
}

describe('quotationStanding', () => {
  it('says Draft until anything has been issued', () => {
    expect(quotationStanding(document())).toEqual({ label: 'Draft', variant: 'secondary' });
  });

  it('says Issued once the customer holds a revision and has not answered', () => {
    expect(quotationStanding(document({ is_issued: true }))).toEqual({
      label: 'Issued',
      variant: 'success',
    });
  });

  it('says Changes requested when that is where the current revision stands', () => {
    expect(
      quotationStanding(document({ is_issued: true, customer_decision: 'changes_requested' })),
    ).toEqual({ label: 'Changes requested', variant: 'warning' });
  });

  it('says Accepted, and acceptance wins over an older request', () => {
    // The asymmetry the backend enforces: a request cannot land on an accepted issue, but an
    // accepted issue CAN carry an older request. A row still reading "Changes requested" on a
    // quotation that is already won is the thing this ranking exists to stop.
    expect(
      quotationStanding(
        document({
          is_issued: true,
          customer_decision: 'accepted',
          changes_requested_at: '2026-08-06T02:15:00',
          changes_requested_note: 'can you provide me more discount',
        }),
      ),
    ).toEqual({ label: 'Accepted', variant: 'success' });
  });
});

describe('quotationStanding: approval rung (QUOTE-APPROVAL-UX A1-A5)', () => {
  it('A1: pending_approval reads Pending approval on warning', () => {
    expect(quotationStanding(document({ approval_status_key: 'pending_approval' }))).toEqual({
      label: 'Pending approval',
      variant: 'warning',
    });
  });

  it('A2: approved reads Approved on primary (blue)', () => {
    expect(quotationStanding(document({ approval_status_key: 'approved' }))).toEqual({
      label: 'Approved',
      variant: 'primary',
    });
  });

  it('A3: rejected reads Rejected on destructive', () => {
    expect(quotationStanding(document({ approval_status_key: 'rejected' }))).toEqual({
      label: 'Rejected',
      variant: 'destructive',
    });
  });

  it('A4: NULL, draft and issued rungs keep the existing reading', () => {
    expect(quotationStanding(document({ approval_status_key: null }))).toEqual({
      label: 'Draft',
      variant: 'secondary',
    });
    expect(quotationStanding(document({ approval_status_key: 'draft' }))).toEqual({
      label: 'Draft',
      variant: 'secondary',
    });
    expect(
      quotationStanding(document({ approval_status_key: 'issued', is_issued: true })),
    ).toEqual({ label: 'Issued', variant: 'success' });
    expect(
      quotationStanding(
        document({
          approval_status_key: 'issued',
          is_issued: true,
          customer_decision: 'accepted',
        }),
      ),
    ).toEqual({ label: 'Accepted', variant: 'success' });
    expect(
      quotationStanding(
        document({
          approval_status_key: 'issued',
          is_issued: true,
          customer_decision: 'changes_requested',
        }),
      ),
    ).toEqual({ label: 'Changes requested', variant: 'warning' });
  });

  it('A5: an issued quotation whose next revision is pending approval reads Pending approval', () => {
    expect(
      quotationStanding(
        document({
          is_issued: true,
          customer_decision: 'changes_requested',
          approval_status_key: 'pending_approval',
        }),
      ),
    ).toEqual({ label: 'Pending approval', variant: 'warning' });
  });
});

describe('hasOpenChangeRequest', () => {
  it('is false on a quotation nobody has answered', () => {
    expect(hasOpenChangeRequest(document({ is_issued: true }))).toBe(false);
  });

  it('is true while the request is the standing answer', () => {
    expect(
      hasOpenChangeRequest(document({ is_issued: true, customer_decision: 'changes_requested' })),
    ).toBe(true);
  });

  it('is false once the customer signed, even with the words still on record', () => {
    expect(
      hasOpenChangeRequest(
        document({
          is_issued: true,
          customer_decision: 'accepted',
          changes_requested_note: 'can you provide me more discount',
        }),
      ),
    ).toBe(false);
  });
});
