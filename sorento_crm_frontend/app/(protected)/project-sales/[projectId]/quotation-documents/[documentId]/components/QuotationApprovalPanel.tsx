'use client';

import * as React from 'react';
import { ShieldAlert, ShieldCheck } from 'lucide-react';
import { isBlockedByApproval } from '../../../../_shared/lib/quotationDecision';
import type { QuotationDocument } from '../../../../_shared/services/quotationDocumentService';

/** The grant a manager needs to decide a below-floor quotation. */
export const APPROVE_PERMISSION = 'projects.quotations.approve';

export { isBlockedByApproval };

function BlockLine({ children }: { children: React.ReactNode }) {
  return <p className="min-w-0 text-sm text-foreground">{children}</p>;
}

/**
 * The price-floor approval gate, stated on the screen it blocks.
 *
 * Information only: every action (Send for approval, Approve, Reject) is the header's one
 * primary slot, so this banner never carries a button.
 *
 * Renders NOTHING at all for the ordinary quotation - no line below its floor - so the common
 * case gains no chrome.
 */
export function QuotationApprovalPanel({ document }: { document: QuotationDocument }) {
  const statusKey = document.approval_status_key ?? null;
  const belowFloor = document.below_floor_line_count ?? 0;
  const blocked = isBlockedByApproval(document);

  if (belowFloor === 0) return null;

  const priced =
    belowFloor === 1
      ? 'One line is priced below its floor'
      : `${belowFloor} lines are priced below their floor`;

  const tone = blocked
    ? 'border-amber-500/40 bg-amber-500/5'
    : 'border-emerald-500/40 bg-emerald-500/5';

  return (
    <div className={`rounded-lg border px-4 py-3 ${tone}`}>
      <div className="flex min-w-0 gap-2.5">
        {blocked ? (
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
        ) : (
          <ShieldCheck className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-hidden />
        )}
        <div className="min-w-0 space-y-1">
          {statusKey === 'pending_approval' && (
            <BlockLine>
              {`${priced}, so this quotation is with a manager for approval. It cannot be sent to the customer until they decide.`}
            </BlockLine>
          )}
          {statusKey === 'rejected' && (
            <>
              <BlockLine>
                A manager sent this back. Re-price the lines below the floor, then ask again.
              </BlockLine>
              {document.approval_rejected_reason && (
                <p className="min-w-0 break-words text-sm text-muted-foreground">
                  {`Their reason: ${document.approval_rejected_reason}`}
                </p>
              )}
            </>
          )}
          {statusKey === 'approved' && (
            <BlockLine>
              {`${priced}, and a manager has approved it. It can be sent to the customer.`}
            </BlockLine>
          )}
          {blocked && statusKey !== 'pending_approval' && statusKey !== 'rejected' && (
            <BlockLine>
              {`${priced}, so this quotation needs a manager's approval before it can be sent to the customer.`}
            </BlockLine>
          )}
        </div>
      </div>
    </div>
  );
}
