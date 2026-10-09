'use client';

import { ReactNode } from 'react';
import { History } from 'lucide-react';
import FormDetailWithSLATabs, { type FormDetailExtraTab } from './FormDetailWithSLATabs';
import FormRevisionsTab, { type FormRevisionsKind } from './FormRevisionsTab';
import { useRevisionEnabledMap } from './useRevisionEnabledMap';
import { useStockInquiry } from '@/app/(protected)/procurement-management/stock-inquiries/hooks/useStockInquiries';
import { usePurchaseRequest } from '@/app/(protected)/procurement-management/purchase-requests/hooks/usePurchaseRequests';
import type { FormSLASourceType } from './formSLAService';
import type { FormCommentKind } from '@/types/formComments';

interface FormDetailTabsWithRevisionsProps {
  sourceEntityType: FormSLASourceType;
  sourceEntityId: string;
  children: ReactNode;
  /** Entity-specific tabs, kept ahead of Revisions. */
  extraTabs?: FormDetailExtraTab[];
  /** Which revision lineage this form reads (PR and SF share one route). */
  revisionsKind: FormRevisionsKind;
  /** Adds the "Comments (N)" tab right after Details. */
  commentsKind?: FormCommentKind;
}

/**
 * `FormDetailWithSLATabs` plus the Revisions tab (UAC H2 / H7, round 6).
 *
 * The tab shows when the TYPE is enabled **or** this record already has a
 * lineage. The kill switch governs whether new revisions can be CREATED; it must
 * not hide what already happened, and the portal's own branch keeps showing the
 * contact their history when the type is switched off - so hiding it here would
 * leave the office arguing about a record only one side can see (UAC H6).
 *
 * Enabled therefore always renders the tab, carrying H2's explicit empty state
 * for a record with no revisions yet. Disabled renders it only for a record that
 * actually has some.
 *
 * The lineage signal is the record's own denormalized `revision_no` (UAC H4),
 * read through the SAME query the detail page already issues
 * (`['stock-inquiry', id]` / `['purchase-request', id]`), so it costs no extra
 * request - and it is only subscribed to when the type is disabled, so the
 * common enabled case does not even do that. Fetching the lineage itself here
 * would be a second request on every load of every form; the tab body fetches it
 * when it opens. `revision_no > 0` is exactly "this record has revisions"; a
 * lineage made only of a resubmit after an office rejection (which writes a
 * history row without consuming a revision, UAC C4) is not covered by it, and
 * on a switched-off type that one case stays hidden - the price of not fetching
 * a lineage for every disabled record that will never have one.
 *
 * While the map is loading, or if it fails, the tab is absent: it appears once
 * the answer arrives. A tab that flashes in and out is worse than one that
 * arrives a beat late, and a failed read must not invent a tab whose content
 * would 404.
 */
export default function FormDetailTabsWithRevisions({
  sourceEntityType,
  sourceEntityId,
  children,
  extraTabs = [],
  revisionsKind,
  commentsKind,
}: FormDetailTabsWithRevisionsProps) {
  const { data: enabledMap } = useRevisionEnabledMap();
  const revisionsEnabled = enabledMap?.[revisionsKind] === true;
  // Only a DISABLED type has to ask whether this record has history; an enabled
  // one shows the tab regardless, so both queries stay switched off (null id).
  const probeForHistory = !!enabledMap && !revisionsEnabled;
  const isStockInquiry = revisionsKind === 'stock_inquiry';
  const inquiry = useStockInquiry(probeForHistory && isStockInquiry ? sourceEntityId : null);
  const request = usePurchaseRequest(probeForHistory && !isStockInquiry ? sourceEntityId : null);

  const record = isStockInquiry ? inquiry.data : request.data;
  const hasLineage = Number(record?.revision_no ?? 0) > 0;
  const showRevisions = revisionsEnabled || (probeForHistory && hasLineage);

  const tabs: FormDetailExtraTab[] = showRevisions
    ? [
        ...extraTabs,
        {
          value: 'revisions',
          label: 'Revisions',
          icon: <History />,
          content: <FormRevisionsTab kind={revisionsKind} entityId={sourceEntityId} />,
        },
      ]
    : extraTabs;

  return (
    <FormDetailWithSLATabs
      sourceEntityType={sourceEntityType}
      sourceEntityId={sourceEntityId}
      extraTabs={tabs}
      commentsKind={commentsKind}
    >
      {children}
    </FormDetailWithSLATabs>
  );
}
