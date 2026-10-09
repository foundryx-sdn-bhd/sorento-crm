'use client';

import { ReactNode } from 'react';
import { FileText, MessageSquare, Timer } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import FormSLATrackingTab from './FormSLATrackingTab';
import type { FormSLASourceType } from './formSLAService';
import { FormComments } from '@/components/common/comments/FormComments';
import { useFormCommentsQuery } from '@/hooks/useFormComments';
import { useComplaint } from '@/app/(protected)/complaint-management/complaints/hooks/useComplaints';
import { useStockInquiry } from '@/app/(protected)/procurement-management/stock-inquiries/hooks/useStockInquiries';
import { usePurchaseRequest } from '@/app/(protected)/procurement-management/purchase-requests/hooks/usePurchaseRequests';
import type { FormCommentKind } from '@/types/formComments';

export interface FormDetailExtraTab {
  value: string;
  label: string;
  content: ReactNode;
  /**
   * Bare lucide icon for the trigger (no size class - the `line` variant sizes
   * it). Optional so a caller that has nothing meaningful to draw renders a
   * label-only trigger rather than a filler icon.
   */
  icon?: ReactNode;
}

interface FormDetailWithSLATabsProps {
  sourceEntityType: FormSLASourceType;
  sourceEntityId: string;
  children: ReactNode;
  /** Entity-specific tabs inserted between "Details" and "SLA Tracking". */
  extraTabs?: FormDetailExtraTab[];
  /** Prepends a "Comments (N)" tab right after Details, holding that form's comment thread. */
  commentsKind?: FormCommentKind;
}

/** The thread's tab: the count shares the thread's query, and a voided form reads only. */
function useCommentsTab(kind: FormCommentKind, id: string): FormDetailExtraTab {
  const { data } = useFormCommentsQuery(kind, id);
  const complaint = useComplaint(kind === 'complaint' ? id : null);
  const inquiry = useStockInquiry(kind === 'stock_inquiry' ? id : null);
  const request = usePurchaseRequest(
    kind === 'purchase_request' || kind === 'sponsorship_form' ? id : null,
  );
  const status = (complaint.data ?? inquiry.data ?? request.data)?.status;
  const isVoided = (status ?? '').trim().toLowerCase() === 'voided';
  const count = (data ?? []).filter((c) => c.kind !== 'response' && !c.deleted).length;
  return {
    value: 'comments',
    label: `Comments (${count})`,
    icon: <MessageSquare />,
    content: <FormComments kind={kind} entityId={id} readOnly={isVoided} />,
  };
}

export default function FormDetailWithSLATabs(props: FormDetailWithSLATabsProps) {
  if (props.commentsKind) return <WithCommentsTab {...props} commentsKind={props.commentsKind} />;
  return <Shell {...props} />;
}

function WithCommentsTab({
  commentsKind,
  extraTabs = [],
  ...rest
}: FormDetailWithSLATabsProps & { commentsKind: FormCommentKind }) {
  const tab = useCommentsTab(commentsKind, rest.sourceEntityId);
  return <Shell {...rest} extraTabs={[tab, ...extraTabs]} />;
}

function Shell({
  sourceEntityType,
  sourceEntityId,
  children,
  extraTabs = [],
}: FormDetailWithSLATabsProps) {
  return (
    <Tabs defaultValue="details">
      {/* The house underlined strip (same as the product/user detail pages), so
          every record in the system wears one tab style. The list carries its
          own bottom margin, so the root drops space-y. */}
      <TabsList variant="line" className="mb-5 w-full justify-start">
        <TabsTrigger value="details">
          <FileText />
          <span>Details</span>
        </TabsTrigger>
        {extraTabs.map((tab) => (
          <TabsTrigger key={tab.value} value={tab.value}>
            {tab.icon}
            <span>{tab.label}</span>
          </TabsTrigger>
        ))}
        <TabsTrigger value="sla">
          <Timer />
          <span>SLA Tracking</span>
        </TabsTrigger>
      </TabsList>
      <TabsContent value="details" className="m-0">
        {children}
      </TabsContent>
      {extraTabs.map((tab) => (
        <TabsContent key={tab.value} value={tab.value} className="m-0">
          {tab.content}
        </TabsContent>
      ))}
      <TabsContent value="sla" className="m-0">
        <FormSLATrackingTab
          sourceEntityType={sourceEntityType}
          sourceEntityId={sourceEntityId}
        />
      </TabsContent>
    </Tabs>
  );
}
