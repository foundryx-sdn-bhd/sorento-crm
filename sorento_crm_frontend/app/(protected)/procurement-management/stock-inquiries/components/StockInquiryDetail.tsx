'use client';

import { useState, useCallback } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Edit, Plus, Trash2, FileDown, Printer, Send, Link2, ExternalLink, CheckCircle, XCircle, RotateCcw, MessageSquare, ArrowUpCircle, Ban, UserRoundCog } from 'lucide-react';
import { getFormSLATrackers, escalateFormTracking } from '@/app/(protected)/sla-management/_shared/formSLAService';
import { SlaActiveTrackerControls } from '@/app/(protected)/sla-management/_shared/SlaActiveTrackerControls';
import { SlaExtendMenuItem, SlaExtendDialog } from '@/app/(protected)/sla-management/_shared/SlaExtendAction';
import { useHandlingLock } from '@/app/(protected)/sla-management/_shared/useHandlingLock';
import { HandlingLockBanner } from '@/app/(protected)/sla-management/_shared/HandlingLockBanner';
import { HandlingLockReleaseMenuItem } from '@/app/(protected)/sla-management/_shared/HandlingLockActions';
import { useFormAction } from '@/app/(protected)/sla-management/_shared/useFormAction';
import { FormActionBanner } from '@/app/(protected)/sla-management/_shared/FormActionBanner';
import { UndoActionDialog } from '@/app/(protected)/sla-management/_shared/UndoActionDialog';
import ReassignDialog from '@/app/(protected)/sla-management/conversation-sla-tracking/components/ReassignDialog';
import { useReassignSLATracking } from '@/app/(protected)/sla-management/conversation-sla-tracking/hooks/useTeamPendingSLA';
import ResponseAttachmentDropzone from '@/app/(protected)/complaint-management/complaints/components/ResponseAttachmentDropzone';
import { RejectionReasonBanner } from '@/components/common/RejectionReasonBanner';
import { useBackToListHref } from '@/components/common/BackToList';
import { RevisionBanner } from '@/components/common/RevisionBanner';
import { VoidBanner } from '@/components/common/VoidBanner';
import { VoidDialog } from '@/components/common/VoidDialog';
import { useFormVoid } from '@/hooks/useFormVoid';
import { withRevisionSuffix } from '@/lib/document-number';
import { statusPillClass, STATUS_PILL_BASE } from '@/lib/status-pill';
import { Button } from '@/components/ui/button';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  exportStockInquiryToExcel,
  exportStockInquiryWithRevisionsToExcel,
} from '../utils/exportStockInquiryToExcel';
import { ExportWithRevisionsDialog } from '@/components/common/ExportWithRevisionsDialog';
import { useRevisionEnabledMap } from '@/app/(protected)/sla-management/_shared/useRevisionEnabledMap';
import { format } from 'date-fns';
import {
  ProductInquiryFormLayout,
  InquiryFormTableRow,
  InquiryReadValue,
} from './ProductInquiryFormLayout';
import { Skeleton } from '@/components/ui/skeleton';
import {
  useStockInquiry,
  useUpdateStockInquiry,
  useUpdateStockInquiryAndReply,
  useSubmitStockInquiryForProjectSales,
  useProjectSalesApproveStockInquiry,
  useProjectSalesRejectStockInquiry,
  usePurchasingRejectStockInquiry,
  useReopenStockInquiry,
  useUploadStockInquiryResponseAttachments,
  useExportStockInquiryPdf,
  useStockInquiryRevisions,
} from '../hooks/useStockInquiries';
import {
  getOrCreateStockInquiryViewLink,
  getStockInquiryRevisions,
} from '../services/stockInquiryService';
import { toast } from '@/lib/toast';
import { formatDate } from '@/lib/helpers';
import { useHasPermission } from '@/hooks/usePermissions';
import StockInquiryDeleteDialog from './stock-inquiry-delete-dialog';
import AuditTrail from '@/components/audit/AuditTrail';
import { stockInquiriesPagerQuery } from '../hooks/useStockInquiries';
import { useStockInquiryApplicationsQuery } from '../../new-product-applications/hooks/useNewProductApplications';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import DetailActions from '@/components/common/DetailActions';
import { EntityDownloadsButton } from '@/components/my-downloads/EntityDownloadsButton';
import StockInquiryAttachmentsSection from './StockInquiryAttachmentsSection';
import StockInquiryConversationPanel from './StockInquiryConversationPanel';
import { STOCK_INQUIRY_STATUS_LABELS } from '../types/stockInquiry.types';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { usePublicViewLinksEnabled } from '@/hooks/usePublicViewLinksEnabled';

interface StockInquiryDetailProps {
  inquiryId: string;
}

export default function StockInquiryDetail({
  inquiryId,
}: StockInquiryDetailProps) {
  const router = useRouter();
  const backHref = useBackToListHref('/procurement-management/stock-inquiries');
  const isValidId = inquiryId && inquiryId !== 'new' && inquiryId !== 'edit';
  const { data: inquiry, isLoading } = useStockInquiry(
    isValidId ? inquiryId : null,
  );
  const updateInquiryMutation = useUpdateStockInquiry();
  const updateAndReplyMutation = useUpdateStockInquiryAndReply();
  const submitForProjectSalesMutation = useSubmitStockInquiryForProjectSales();
  const projectSalesApproveMutation = useProjectSalesApproveStockInquiry();
  const projectSalesRejectMutation = useProjectSalesRejectStockInquiry();
  const purchasingRejectMutation = usePurchasingRejectStockInquiry();
  const reopenMutation = useReopenStockInquiry();
  const uploadResponseAttachmentsMutation = useUploadStockInquiryResponseAttachments();
  const exportPdfMutation = useExportStockInquiryPdf();
  const reassignMutation = useReassignSLATracking();
  const canReassign = useHasPermission('sla_management.conversation_sla_tracking.reassign');
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [downloadsOpen, setDownloadsOpen] = useState(false);
  const [replyComposePrefill, setReplyComposePrefill] = useState<{
    key: number;
    text: string;
  } | null>(null);
  const [openingReplySheet, setOpeningReplySheet] = useState(false);
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [rejectAction, setRejectAction] = useState<'project_sales' | 'purchasing' | null>(null);
  const [reopenDialogOpen, setReopenDialogOpen] = useState(false);
  const [reopenReason, setReopenReason] = useState('');
  // Escalate the active form-SLA stage straight from the form (gear menu).
  const queryClient = useQueryClient();
  const [escalateOpen, setEscalateOpen] = useState(false);
  const [extendOpen, setExtendOpen] = useState(false);
  const [escalateReason, setEscalateReason] = useState('');
  const [escalating, setEscalating] = useState(false);
  const { data: slaTrackers } = useQuery({
    // Key on updated_at so the escalation banner clears on resolve without a refresh.
    queryKey: ['form-sla-trackers', 'stock_inquiry', inquiryId, inquiry?.updated_at],
    queryFn: () => getFormSLATrackers('stock_inquiry', inquiryId),
    enabled: !!isValidId,
  });
  const activeTracker = (slaTrackers ?? []).find((t) => !t.is_resolved) ?? null;
  // Handling-lock ("I'm handling this") - live off the form-SLA handling tracker query.
  const handlingLock = useHandlingLock({
    sourceEntityType: 'stock_inquiry',
    sourceEntityId: isValidId ? inquiryId : null,
    entityKey: inquiry?.updated_at,
  });
  // A voided stock inquiry is fully read-only - suppress every business CTA.
  const isVoided = (inquiry?.status ?? '').trim().toLowerCase() === 'voided';
  // Form-action deferral (PLAN-form-sla-undo.md). While an action is pending its grace
  // window, EVERY business CTA is suppressed - the form must commit against the state
  // the action was requested on (AC-D-10), and a second action cannot queue (AC-D-7).
  const formAction = useFormAction({
    sourceEntityType: 'stock_inquiry',
    sourceEntityId: isValidId ? inquiryId : null,
    entityKey: inquiry?.updated_at,
  });
  const businessCtasEnabled =
    handlingLock.businessCtasEnabled && !isVoided && !formAction.ctasDisabled;
  const [undoDialogOpen, setUndoDialogOpen] = useState(false);
  const lockedCtaTitle = !businessCtasEnabled
    ? `Being handled by ${handlingLock.tracker?.handled_by_name ?? 'someone else'} - take over to act`
    : undefined;
  const [viewLinkCopying, setViewLinkCopying] = useState(false);
  // The item ticks in place and the menu stays open to be read (S7-05).
  const [viewLinkCopied, setViewLinkCopied] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [conversationSheetOpen, setConversationSheetOpen] = useState(false);
  const [editPurchasingResponseOpen, setEditPurchasingResponseOpen] = useState(false);
  const [editPurchasingResponseValue, setEditPurchasingResponseValue] = useState('');
  const [responseAttachmentFiles, setResponseAttachmentFiles] = useState<File[]>([]);
  const [reassignOpen, setReassignOpen] = useState(false);
  const [voidDialogOpen, setVoidDialogOpen] = useState(false);
  const canSubmitForProjectSales = useHasPermission('procurement.stock_inquiries.submit_for_project_sales');
  const canProjectSalesApprove = useHasPermission('procurement.stock_inquiries.project_sales_approve');
  const canProjectSalesReject = useHasPermission('procurement.stock_inquiries.project_sales_reject');
  const canPurchasingReject = useHasPermission('procurement.stock_inquiries.purchasing_reject');
  const canReopen = useHasPermission('procurement.stock_inquiries.reopen');
  const canVoid = useHasPermission('procurement.stock_inquiries.void');
  // New product applications raised from this inquiry (NEW-PRODUCT-APP). The row is for users
  // who hold any application permission; the gear item starts one and needs `create`.
  const canCreateApplication = useHasPermission('procurement.new_product_applications.create');
  const canSeeApplications = [
    useHasPermission('procurement.new_product_applications.view'),
    canCreateApplication,
    useHasPermission('procurement.new_product_applications.approve'),
    useHasPermission('procurement.new_product_applications.open_code'),
  ].some(Boolean);
  const { data: applications } = useStockInquiryApplicationsQuery(
    isValidId ? inquiryId : null,
    canSeeApplications,
  );
  const voidMutation = useFormVoid('procurement/stock-inquiries', inquiryId, {
    queryKeysToInvalidate: [['stock-inquiry', inquiryId]],
  });
  const publicViewLinksEnabled = usePublicViewLinksEnabled();
  // Denormalized revision counter (UAC H4). Drives the banner, the number
  // suffix and the revision-lineage refetch.
  const revisionNo = Number(inquiry?.revision_no ?? 0);
  const revisionsQuery = useStockInquiryRevisions(
    isValidId ? inquiryId : null,
    revisionNo,
  );
  // The purchasing response may only be written while the inquiry is still with
  // purchasing (UAC O1) - the backend returns 422 outside those statuses, so the
  // affordance is REMOVED rather than left to fail on a toast. Chat Records
  // stays available at every status (UAC O2).
  //
  // The decision is the SERVER's, read straight off the payload. A status list
  // kept here as well would be a second source for one rule, and the first time
  // the two drifted this would either hide a working button or show one that
  // 422s. Absent means not gated, as on the backend.
  const responseWritable = inquiry?.response_write_allowed !== false;
  // The newest entry carries the reason and submitter the banner quotes verbatim.
  const latestRevision = (revisionsQuery.data ?? [])
    .filter((entry) => (entry.revision_no ?? 0) > 0)
    .at(-1);

  /**
   * "Include revisions?" is only a real question when this record HAS revisions
   * and the type has them switched on (round 6, 6.4). Anywhere else both exports
   * behave exactly as they always have, with no dialog in the way. Enablement is
   * the server's answer (global kill switch + per-type config collapsed into one
   * boolean); "has any" is the record's own denormalized counter, so deciding
   * costs no lineage read.
   */
  const { data: revisionEnabledMap } = useRevisionEnabledMap();
  const canOfferRevisionExport =
    revisionEnabledMap?.stock_inquiry === true && revisionNo > 0;
  const [exportChoice, setExportChoice] = useState<'excel' | 'pdf' | null>(null);

  const runExcelExport = async (includeRevisions: boolean) => {
    if (!inquiry) return;
    setExporting(true);
    try {
      if (includeRevisions) {
        // The lineage as it stands at export time. The banner query has usually
        // already cached it (keyed on `revision_no`, so it cannot be stale after
        // a revision lands); the direct read is the fallback for a first click
        // before it resolves.
        const entries = revisionsQuery.data ?? (await getStockInquiryRevisions(inquiryId));
        await exportStockInquiryWithRevisionsToExcel(inquiry, entries);
      } else {
        await exportStockInquiryToExcel(inquiry);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  };

  const handleExportExcel = async () => {
    if (!inquiry) return;
    if (canOfferRevisionExport) {
      setExportChoice('excel');
      return;
    }
    await runExcelExport(false);
  };

  const handleExportPdf = () => {
    if (canOfferRevisionExport) {
      setExportChoice('pdf');
      return;
    }
    exportPdfMutation.mutate(inquiryId);
  };

  const handleExportConfirmed = (includeRevisions: boolean) => {
    const choice = exportChoice;
    setExportChoice(null);
    if (choice === 'pdf') {
      // No option chosen, no body: the request stays the one this export has
      // always sent.
      exportPdfMutation.mutate(
        includeRevisions ? { id: inquiryId, options: { include_revisions: true } } : inquiryId,
      );
      return;
    }
    void runExcelExport(includeRevisions);
  };

  /**
   * Only the purchasing wording. The preamble and the contact link are composed
   * server-side by `StockInquiryService.compose_stock_inquiry_reply_message`, which
   * resolves the interactive portal link and puts it last on its own line.
   *
   * Composing here previously sent the read-only `/view/...?token=` page built on the
   * staff browser's own origin, with a ':' glued to the URL - WhatsApp pulled the colon
   * into the href and the contact got an invalid link.
   */
  const buildPurchasingRespondMessage = useCallback(
    async (purchasingResponseText: string) => ({
      body: (purchasingResponseText ?? '').trim(),
    }),
    [],
  );

  /** Persists the purchasing response when `skipSaveShort` is false, then sends it via Respond.io */
  const sendPurchasingUpdateAndReplyViaRespond = useCallback(
    async (purchasingResponseText: string, options?: { skipSaveShort?: boolean }) => {
      const { body } = await buildPurchasingRespondMessage(purchasingResponseText);
      if (!body) {
        toast.error('Enter a purchasing response before sending.');
        return;
      }
      if (!options?.skipSaveShort) {
        await updateInquiryMutation.mutateAsync({
          id: inquiryId,
          data: { purchasing_response: body },
        });
      }
      await updateAndReplyMutation.mutateAsync({
        id: inquiryId,
        data: { purchasing_response: body },
      });
    },
    [
      buildPurchasingRespondMessage,
      inquiryId,
      updateInquiryMutation,
      updateAndReplyMutation,
    ],
  );

  const sendPurchasingUpdateAndReplyFromSavedRecord = useCallback(async () => {
    if (!inquiry) return;
    await sendPurchasingUpdateAndReplyViaRespond(inquiry.purchasing_response ?? '', {
      skipSaveShort: true,
    });
  }, [inquiry, sendPurchasingUpdateAndReplyViaRespond]);

  const closeEditPurchasingResponse = useCallback(() => {
    setEditPurchasingResponseOpen(false);
    setResponseAttachmentFiles([]);
  }, []);

  /** Uploads staged attachments before the response text is saved. Returns false
   *  (and leaves the popup open with the files intact) on any upload failure, so
   *  the response text is never silently saved without the attachments. */
  const uploadStagedResponseAttachments = useCallback(async () => {
    if (responseAttachmentFiles.length === 0) return true;
    try {
      await uploadResponseAttachmentsMutation.mutateAsync({
        inquiryId,
        files: responseAttachmentFiles,
      });
      setResponseAttachmentFiles([]);
      return true;
    } catch {
      return false; // toast already shown by the mutation
    }
  }, [responseAttachmentFiles, uploadResponseAttachmentsMutation, inquiryId]);

  if (!isValidId) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Invalid stock inquiry ID</p>
        <Button
          variant="outline"
          onClick={() => router.push('/procurement-management/stock-inquiries')}
          className="mt-4"
        >
          Back to Stock Inquiries
        </Button>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!inquiry) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Stock inquiry not found</p>
        <Button
          variant="outline"
          onClick={() => router.push('/procurement-management/stock-inquiries')}
          className="mt-4"
        >
          Back to Stock Inquiries
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1 min-w-0">
          <h2 className="text-2xl font-bold break-words">
            Stock Inquiry -{' '}
            {withRevisionSuffix(inquiry.inquiry_number, revisionNo) ||
              inquiry.product_code ||
              'Details'}
          </h2>
          <p className="text-sm text-muted-foreground">
            Created:{' '}
            {inquiry.created_at
              ? formatDate(new Date(inquiry.created_at))
              : '-'}
            {inquiry.status && (
              <>
                {' · '}
                <span className={`${STATUS_PILL_BASE} ${statusPillClass(inquiry.status)}`}>
                  {STOCK_INQUIRY_STATUS_LABELS[inquiry.status] ?? inquiry.status}
                </span>
              </>
            )}
          </p>
          {inquiry.last_responded_at && (
            <p className="text-sm text-muted-foreground">
              Last responded: {formatDate(new Date(inquiry.last_responded_at))}
              {(inquiry.last_responded_by_name ?? inquiry.last_responded_by) &&
                ` by ${inquiry.last_responded_by_name ?? inquiry.last_responded_by}`}
            </p>
          )}
        </div>
        <DetailActions
          pager={{
            ...stockInquiriesPagerQuery,
            detailPath: '/procurement-management/stock-inquiries',
            currentId: inquiryId,
            ariaLabel: 'stock inquiry',
          }}
          gear={
            <DetailActionsMenu ariaLabel="Stock inquiry actions">
              {/* The download history is an action, so it sits in the gear with the rest;
                  the record card carries the pager, the gear and the workflow CTAs, and
                  nothing else. */}
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault();
                  setDownloadsOpen(true);
                }}
              >
                <Printer className="size-4" />
                Download history
              </DropdownMenuItem>
              {/* Post-grace Undo. Rendered only when the server says the last committed
                  action is reversible - eligibility is a server read, never a client
                  guess, and it is re-checked at execute time (AC-PG-6/7). */}
              {formAction.view.kind === 'undoable' && (
                <DropdownMenuItem
                  onSelect={(e) => {
                    e.preventDefault();
                    setUndoDialogOpen(true);
                  }}
                  data-testid="undo-action-menu-item"
                >
                  <RotateCcw className="size-4" />
                  Undo last action
                </DropdownMenuItem>
              )}
              {/* Edit is not lock-gated (deliberate), but a pending form action DOES
                  block it: the action must commit against the state it was requested on
                  (AC-D-10). The backend enforces the same rule; this keeps the UI honest. */}
              {!isVoided && !formAction.ctasDisabled && (
                <DropdownMenuItem
                  onClick={() =>
                    router.push(
                      `/procurement-management/stock-inquiries/${inquiryId}/edit`,
                    )
                  }
                >
                  <Edit className="size-4" />
                  Edit
                </DropdownMenuItem>
              )}
              {activeTracker && (
                <DropdownMenuItem
                  onSelect={(e) => {
                    e.preventDefault();
                    setEscalateReason('');
                    setEscalateOpen(true);
                  }}
                >
                  <ArrowUpCircle className="size-4" />
                  Escalate SLA
                </DropdownMenuItem>
              )}
              <SlaExtendMenuItem activeTracker={activeTracker} onSelect={() => setExtendOpen(true)} />
              {canReassign && activeTracker && !isVoided && (
                <DropdownMenuItem
                  onSelect={(e) => {
                    e.preventDefault();
                    setReassignOpen(true);
                  }}
                >
                  <UserRoundCog className="size-4" />
                  Reassign
                </DropdownMenuItem>
              )}
              <HandlingLockReleaseMenuItem
                state={handlingLock.state}
                onRelease={handlingLock.release}
              />
              {inquiry.respond_inbox_url && (
                <DropdownMenuItem onClick={() => setConversationSheetOpen(true)}>
                  <MessageSquare className="size-4" />
                  Chat records
                </DropdownMenuItem>
              )}
              {publicViewLinksEnabled && (
                <DropdownMenuItem
                  disabled={viewLinkCopying}
                  onSelect={async (event) => {
                    event.preventDefault();
                    try {
                      setViewLinkCopying(true);
                      const baseUrl = typeof window !== 'undefined' ? window.location.origin : undefined;
                      const { view_url } = await getOrCreateStockInquiryViewLink(inquiryId, baseUrl);
                      await navigator.clipboard.writeText(view_url);
                      setViewLinkCopied(true);
                      window.setTimeout(() => setViewLinkCopied(false), 2000);
                    } catch {
                      toast.error('Failed to copy view link');
                    } finally {
                      setViewLinkCopying(false);
                    }
                  }}
                >
                  {viewLinkCopied ? <Check className="size-4" /> : <Link2 className="size-4" />}
                  {viewLinkCopying ? 'Copying…' : viewLinkCopied ? 'Copied' : 'Copy view link'}
                </DropdownMenuItem>
              )}
              {publicViewLinksEnabled && (
                <DropdownMenuItem
                  onClick={async () => {
                    try {
                      const baseUrl = typeof window !== 'undefined' ? window.location.origin : undefined;
                      const { view_url } = await getOrCreateStockInquiryViewLink(inquiryId, baseUrl);
                      window.open(view_url, '_blank');
                    } catch {
                      toast.error('Failed to open view link');
                    }
                  }}
                >
                  <ExternalLink className="size-4" />
                  View in system
                </DropdownMenuItem>
              )}
              {businessCtasEnabled &&
                inquiry.respond_inbox_url &&
                responseWritable && (
                  <DropdownMenuItem
                    disabled={openingReplySheet || updateAndReplyMutation.isPending}
                    onClick={async () => {
                      setOpeningReplySheet(true);
                      try {
                        await sendPurchasingUpdateAndReplyFromSavedRecord();
                      } finally {
                        setOpeningReplySheet(false);
                      }
                    }}
                  >
                    <Send className="size-4" />
                    {openingReplySheet || updateAndReplyMutation.isPending
                      ? 'Sending…'
                      : 'Update & Reply'}
                  </DropdownMenuItem>
                )}
              <DropdownMenuItem
                data-guide-target="procurement.stock-inquiries.download-pdf"
                disabled={exportPdfMutation.isPending}
                onSelect={(e) => {
                  e.preventDefault();
                  handleExportPdf();
                }}
              >
                <Printer className="size-4" />
                {exportPdfMutation.isPending ? 'Preparing…' : 'Print / Download PDF'}
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={handleExportExcel}
                disabled={exporting}
              >
                <FileDown className="size-4" />
                {exporting ? 'Exporting…' : 'Export to Excel'}
              </DropdownMenuItem>
              {inquiry.status === 'responded' && canCreateApplication && (
                <DropdownMenuItem
                  onClick={() =>
                    router.push(
                      `/procurement-management/new-product-applications/new?stock_inquiry_id=${inquiryId}`,
                    )
                  }
                >
                  <Plus className="size-4" />
                  Create new product application
                </DropdownMenuItem>
              )}
              {canVoid && !isVoided && !formAction.ctasDisabled && (
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => setVoidDialogOpen(true)}
                >
                  <Ban className="size-4" />
                  Void
                </DropdownMenuItem>
              )}
              {!isVoided && !formAction.ctasDisabled && (
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => setDeleteDialogOpen(true)}
                >
                  <Trash2 className="size-4" />
                  Delete
                </DropdownMenuItem>
              )}
            </DetailActionsMenu>
          }
          primary={
            <>
            {/* Workflow actions: HIDDEN (not disabled) while the handling lock is held
                by someone else / unclaimed - keeps the header uncluttered. When the lock
                does not bite (tier 1, flag off, or I hold it) businessCtasEnabled is true
                and they render on their normal status+permission gates. */}
            {businessCtasEnabled && inquiry.status === 'new' && canSubmitForProjectSales && (
              <Button
                variant="primary"
                size="sm"
                disabled={submitForProjectSalesMutation.isPending}
                onClick={() => submitForProjectSalesMutation.mutate(inquiryId)}
              >
                {submitForProjectSalesMutation.isPending ? 'Submitting…' : 'Submit for project sales'}
              </Button>
            )}
            {businessCtasEnabled && inquiry.status === 'pending_project_sales' && (
              <>
                {canProjectSalesApprove && (
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={projectSalesApproveMutation.isPending}
                    onClick={() => projectSalesApproveMutation.mutate(inquiryId)}
                    data-guide-target="procurement.stock-inquiries.approve-button"
                  >
                    <CheckCircle className="size-4 mr-1" />
                    {projectSalesApproveMutation.isPending ? 'Approving…' : 'Approve (send to purchasing)'}
                  </Button>
                )}
                {canProjectSalesReject && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={projectSalesRejectMutation.isPending}
                    onClick={() => {
                      setRejectAction('project_sales');
                      setRejectReason('');
                      setRejectDialogOpen(true);
                    }}
                    data-guide-target="procurement.stock-inquiries.reject-button"
                    className="text-destructive border-destructive/40 hover:bg-destructive/10"
                  >
                    <XCircle className="size-4 mr-1" />
                    Reject
                  </Button>
                )}
              </>
            )}
            {businessCtasEnabled && responseWritable && (
              <Button
                // Pending purchasing = the purchasing response is the next action →
                // primary CTA; once responded it's a secondary edit.
                variant={inquiry.status === 'pending_purchasing' ? 'primary' : 'outline'}
                size="sm"
                onClick={() => {
                  setEditPurchasingResponseValue(inquiry.purchasing_response ?? '');
                  setEditPurchasingResponseOpen(true);
                }}
                data-guide-target="procurement.stock-inquiries.edit-purchasing-response-button"
              >
                <Edit className="size-4 mr-1" />
                Edit purchasing response
              </Button>
            )}
            {businessCtasEnabled &&
              (inquiry.status === 'pending_purchasing' ||
                inquiry.status === 'responded') &&
              canPurchasingReject && (
              <Button
                variant="outline"
                size="sm"
                disabled={purchasingRejectMutation.isPending}
                onClick={() => {
                  setRejectAction('purchasing');
                  setRejectReason('');
                  setRejectDialogOpen(true);
                }}
                data-guide-target="procurement.stock-inquiries.reject-button"
                className="text-destructive border-destructive/40 hover:bg-destructive/10"
              >
                <XCircle className="size-4 mr-1" />
                Reject
              </Button>
            )}
            {businessCtasEnabled && inquiry.status === 'rejected' && canReopen && (
              <Button
                variant="outline"
                size="sm"
                disabled={reopenMutation.isPending}
                onClick={() => {
                  setReopenReason('');
                  setReopenDialogOpen(true);
                }}
                data-guide-target="procurement.stock-inquiries.reopen-button"
              >
                <RotateCcw className="size-4 mr-1" />
                {inquiry.rejected_from === 'pending_purchasing'
                  ? 'Reopen to pending purchasing'
                  : 'Reopen to pending project sales'}
              </Button>
            )}
            </>
          }
          dialogs={
            <EntityDownloadsButton
              entityType="stock_inquiry"
              entityId={inquiryId}
              label={inquiry.inquiry_number ?? undefined}
              open={downloadsOpen}
              onOpenChange={setDownloadsOpen}
            />
          }
        />
      </div>

      <ExportWithRevisionsDialog
        open={exportChoice !== null}
        onOpenChange={(open) => !open && setExportChoice(null)}
        title={exportChoice === 'pdf' ? 'Print / Download PDF' : 'Export to Excel'}
        onConfirm={handleExportConfirmed}
      />

      <HandlingLockBanner
        state={handlingLock.state}
        tracker={handlingLock.tracker}
        onClaim={handlingLock.claim}
        onTakeOver={handlingLock.takeOver}
      />

      <FormActionBanner
        view={formAction.view}
        onCancel={formAction.cancel}
        onExpire={formAction.refresh}
        onDismissOutcome={formAction.refresh}
        isCancelling={formAction.isMutating}
      />

      {formAction.view.kind === 'undoable' && (
        <UndoActionDialog
          open={undoDialogOpen}
          onOpenChange={setUndoDialogOpen}
          eligibility={formAction.view.eligibility}
          entityLabel={inquiry.inquiry_number || 'this stock inquiry'}
          onConfirm={(reason) => {
            formAction.undo(reason);
            setUndoDialogOpen(false);
          }}
          isSubmitting={formAction.isMutating}
        />
      )}

      {/* Reject dialog */}
      <Dialog
        open={rejectDialogOpen}
        onOpenChange={(open) => {
          setRejectDialogOpen(open);
          if (!open) {
            setRejectReason('');
            setRejectAction(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Reject stock inquiry</DialogTitle>
            <DialogDescription>Enter a reason for the rejection. This is required.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="reject-reason">
              Rejection reason <span className="text-destructive">*</span>
            </Label>
            <Textarea
              id="reject-reason"
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Reason for rejection..."
              rows={3}
              className="resize-none"
              required
              aria-required
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectDialogOpen(false)}>Cancel</Button>
            <Button
              variant="destructive"
              data-guide-target="procurement.stock-inquiries.reject-confirm-button"
              disabled={
                !rejectReason.trim() ||
                (rejectAction === 'project_sales' && projectSalesRejectMutation.isPending) ||
                (rejectAction === 'purchasing' && purchasingRejectMutation.isPending)
              }
              onClick={async () => {
                const reason = rejectReason.trim();
                if (!reason) return;
                if (rejectAction === 'project_sales') {
                  await projectSalesRejectMutation.mutateAsync({ id: inquiryId, reason });
                } else if (rejectAction === 'purchasing') {
                  await purchasingRejectMutation.mutateAsync({ id: inquiryId, reason });
                }
                setRejectDialogOpen(false);
                setRejectAction(null);
              }}
            >
              Reject
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reopen dialog */}
      <Dialog open={reopenDialogOpen} onOpenChange={setReopenDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Reopen stock inquiry</DialogTitle>
            <DialogDescription>
              {inquiry.rejected_from === 'pending_purchasing'
                ? 'Reopen to pending purchasing. Optionally provide a reason.'
                : 'Reopen to pending project sales. Optionally provide a reason.'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="reopen-reason">Reason*</Label>
            <Textarea
              id="reopen-reason"
              value={reopenReason}
              onChange={(e) => setReopenReason(e.target.value)}
              placeholder="Reason for reopening..."
              rows={3}
              className="resize-none"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setReopenDialogOpen(false)}>Cancel</Button>
            <Button
              disabled={reopenMutation.isPending}
              data-guide-target="procurement.stock-inquiries.reopen-confirm-button"
              onClick={async () => {
                await reopenMutation.mutateAsync({ id: inquiryId, reason: reopenReason.trim() || undefined });
                setReopenDialogOpen(false);
              }}
            >
              Reopen
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={escalateOpen} onOpenChange={setEscalateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Escalate SLA</DialogTitle>
            <DialogDescription>
              Force-escalate the current SLA stage to the next tier and reassign per
              the escalation policy. Optionally add a reason.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="si-escalate-reason">Reason*</Label>
            <Textarea
              id="si-escalate-reason"
              value={escalateReason}
              onChange={(e) => setEscalateReason(e.target.value)}
              placeholder="Why escalate now?"
              rows={3}
              className="resize-none"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEscalateOpen(false)} disabled={escalating}>
              Cancel
            </Button>
            <Button
              disabled={escalating || !activeTracker}
              onClick={async () => {
                if (!activeTracker) return;
                setEscalating(true);
                try {
                  const res = await escalateFormTracking(activeTracker.id, escalateReason.trim());
                  queryClient.invalidateQueries({ queryKey: ['form-sla-trackers', 'stock_inquiry', inquiryId] });
                  handlingLock.refresh(); // lock banner keys on a separate query - refetch so it appears without reload
                  toast.success(`Escalated to tier ${res.current_tier}`);
                  setEscalateOpen(false);
                } catch (err) {
                  toast.error(err instanceof Error ? err.message : 'Failed to escalate');
                } finally {
                  setEscalating(false);
                }
              }}
            >
              {escalating ? 'Escalating…' : 'Escalate'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <SlaExtendDialog
        activeTracker={activeTracker}
        label={`Stock Inquiry${inquiry.inquiry_number ? ` · ${inquiry.inquiry_number}` : ''}`}
        open={extendOpen}
        onOpenChange={setExtendOpen}
        onExtended={() =>
          void queryClient.invalidateQueries({ queryKey: ['form-sla-trackers', 'stock_inquiry', inquiryId] })
        }
      />

      <ReassignDialog
        open={reassignOpen}
        onOpenChange={setReassignOpen}
        taskLabel={`Stock Inquiry${inquiry.inquiry_number ? ` · ${inquiry.inquiry_number}` : ''}`}
        submitting={reassignMutation.isPending}
        onConfirm={(userId) => {
          if (!activeTracker) return;
          reassignMutation.mutate(
            { id: activeTracker.id, userId },
            {
              onSuccess: () => {
                queryClient.invalidateQueries({ queryKey: ['form-sla-trackers', 'stock_inquiry', inquiryId] });
                handlingLock.refresh(); // lock banner keys on a separate query - refetch so it appears without reload
                setReassignOpen(false);
              },
            },
          );
        }}
      />

      {inquiry && (
        <StockInquiryDeleteDialog
          open={deleteDialogOpen}
          closeDialog={() => setDeleteDialogOpen(false)}
          inquiry={inquiry}
          onSuccess={() => {
            router.push(backHref);
          }}
        />
      )}

      <Dialog
        open={editPurchasingResponseOpen}
        onOpenChange={(open) => {
          setEditPurchasingResponseOpen(open);
          if (!open) setResponseAttachmentFiles([]);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Edit purchasing response</DialogTitle>
            <DialogDescription>
              Save updates the record only. Update &amp; Reply saves your text and sends it to the contact.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="edit-purchasing-response">Purchasing response</Label>
            <Textarea
              id="edit-purchasing-response"
              value={editPurchasingResponseValue}
              onChange={(e) => setEditPurchasingResponseValue(e.target.value)}
              placeholder="Response text..."
              rows={5}
              className="resize-none"
            />
          </div>
          <div className="space-y-2">
            <Label>Attachments</Label>
            <ResponseAttachmentDropzone
              files={responseAttachmentFiles}
              onFilesChange={setResponseAttachmentFiles}
              disabled={uploadResponseAttachmentsMutation.isPending}
            />
          </div>
          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button variant="outline" onClick={closeEditPurchasingResponse}>
              Cancel
            </Button>
            <Button
              variant="outline"
              data-guide-target="procurement.stock-inquiries.save-response-button"
              // Blank response = nothing to save or send. Disable up front instead
              // of accepting the click and rejecting it with a toast afterwards.
              disabled={
                !editPurchasingResponseValue.trim() ||
                updateInquiryMutation.isPending ||
                uploadResponseAttachmentsMutation.isPending
              }
              title={
                !editPurchasingResponseValue.trim()
                  ? 'Enter a purchasing response first.'
                  : undefined
              }
              onClick={async () => {
                const uploaded = await uploadStagedResponseAttachments();
                if (!uploaded) return;
                try {
                  await updateInquiryMutation.mutateAsync({
                    id: inquiryId,
                    data: { purchasing_response: editPurchasingResponseValue.trim() },
                  });
                  setEditPurchasingResponseOpen(false);
                } catch {
                  // toast from mutation
                }
              }}
            >
              {updateInquiryMutation.isPending || uploadResponseAttachmentsMutation.isPending
                ? 'Saving…'
                : 'Save only'}
            </Button>
            {inquiry.respond_inbox_url && responseWritable && (
                <Button
                  variant="primary"
                  data-guide-target="procurement.stock-inquiries.update-and-reply-button"
                  disabled={
                    !editPurchasingResponseValue.trim() ||
                    updateInquiryMutation.isPending ||
                    openingReplySheet ||
                    updateAndReplyMutation.isPending ||
                    uploadResponseAttachmentsMutation.isPending ||
                    !businessCtasEnabled
                  }
                  title={
                    !editPurchasingResponseValue.trim()
                      ? 'Enter a purchasing response first.'
                      : lockedCtaTitle
                  }
                  onClick={async () => {
                    const uploaded = await uploadStagedResponseAttachments();
                    if (!uploaded) return;
                    setOpeningReplySheet(true);
                    try {
                      await sendPurchasingUpdateAndReplyViaRespond(editPurchasingResponseValue);
                      setEditPurchasingResponseOpen(false);
                    } catch {
                      // toast from mutation
                    } finally {
                      setOpeningReplySheet(false);
                    }
                  }}
                >
                  <Send className="size-4 mr-1" />
                  {openingReplySheet || updateAndReplyMutation.isPending
                    ? 'Sending…'
                    : 'Update & Reply'}
                </Button>
              )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {inquiry.status === 'rejected' && (
        <RejectionReasonBanner
          reason={inquiry.rejection_reason}
          rejectedByName={inquiry.rejected_by_name}
          // Phase 2: BE to emit `rejected_by_wa_phone` on the detail DTO.
          rejectedByWaPhone={(inquiry as { rejected_by_wa_phone?: string | null }).rejected_by_wa_phone ?? undefined}
          rejectedAt={inquiry.rejected_at}
        />
      )}

      {/* Contact revised their submission (UAC H1). Renders nothing at revision 0. */}
      <RevisionBanner
        revisionNo={revisionNo}
        documentNumber={inquiry.inquiry_number}
        revisedAt={inquiry.last_revised_at}
        revisedByName={latestRevision?.submitted_by}
        reason={latestRevision?.reason}
        restartedAtLabel="Project Sales"
      />

      <VoidBanner
        voided={isVoided}
        voidedByName={inquiry.voided_by_name}
        voidedAt={inquiry.voided_at}
        voidReason={inquiry.void_reason}
      />

      <VoidDialog
        open={voidDialogOpen}
        onOpenChange={setVoidDialogOpen}
        isPending={voidMutation.isPending}
        onConfirm={(reason) => voidMutation.mutateAsync({ void_reason: reason })}
      />

      <SlaActiveTrackerControls
        activeTracker={activeTracker}
        label={`Stock Inquiry${inquiry.inquiry_number ? ` · ${inquiry.inquiry_number}` : ''}`}
        onExtended={() =>
          void queryClient.invalidateQueries({
            queryKey: ['form-sla-trackers', 'stock_inquiry', inquiryId],
          })
        }
      />


      <ProductInquiryFormLayout>
        <InquiryFormTableRow label="Date">
          <InquiryReadValue empty=" - ">
            {inquiry.created_at
              ? format(new Date(inquiry.created_at), 'dd/MM/yy')
              : ''}
          </InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Stock inquiry number">
          <InquiryReadValue>{inquiry.inquiry_number}</InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Sales person">
          <InquiryReadValue>{inquiry.salesperson_contact_name ?? inquiry.salesperson}</InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Product code">
          <InquiryReadValue>{inquiry.product_code}</InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Item description" labelClassName="items-start pt-3">
          <InquiryReadValue>{inquiry.item_description}</InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Project customer">
          <InquiryReadValue>{inquiry.project_customer}</InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Project name">
          <InquiryReadValue>{inquiry.project_name}</InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Qty">
          <InquiryReadValue>{inquiry.quantity != null ? String(inquiry.quantity) : ''}</InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Delivery date">
          <InquiryReadValue>{inquiry.delivery_date}</InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Remark" labelClassName="items-start pt-3">
          <InquiryReadValue>{inquiry.remark}</InquiryReadValue>
        </InquiryFormTableRow>
        <InquiryFormTableRow label="Additional remark" labelClassName="items-start pt-3">
          <InquiryReadValue>{inquiry.additional_remark}</InquiryReadValue>
        </InquiryFormTableRow>
        {inquiry.respond_inbox_url && (
          <InquiryFormTableRow label="Respond inbox">
            <div className="flex flex-col sm:flex-row sm:items-center gap-2 py-0.5">
              <a
                href={inquiry.respond_inbox_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline text-sm break-all font-medium"
              >
                {inquiry.respond_inbox_url}
              </a>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0 w-fit"
                onClick={() => setConversationSheetOpen(true)}
                aria-label="Open chat records"
              >
                <MessageSquare className="size-4 mr-1" />
                Chat
              </Button>
            </div>
          </InquiryFormTableRow>
        )}
        <InquiryFormTableRow
          label="Comment / reply by purchasing"
          labelClassName="items-start pt-3 sm:whitespace-normal"
        >
          <div className="space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <InquiryReadValue>{inquiry.purchasing_response}</InquiryReadValue>
              </div>
              {!isVoided && responseWritable && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="shrink-0"
                  onClick={() => {
                    setEditPurchasingResponseValue(inquiry.purchasing_response ?? '');
                    setEditPurchasingResponseOpen(true);
                  }}
                  aria-label="Edit purchasing response"
                >
                  <Edit className="size-4" />
                  Edit
                </Button>
              )}
            </div>
          </div>
        </InquiryFormTableRow>
        {canSeeApplications && (
          <InquiryFormTableRow label="New product applications" labelClassName="items-start pt-3">
            {applications && applications.length > 0 ? (
              <ul className="space-y-1.5 py-2 text-sm">
                {applications.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/procurement-management/new-product-applications/${a.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {a.application_no}
                    </Link>
                    <span className={`${STATUS_PILL_BASE} ${statusPillClass(a.status)}`}>
                      {a.status_label}
                    </span>
                    {a.opened_codes.length > 0 && (
                      <span className="break-words">{a.opened_codes.join(', ')}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <InquiryReadValue>None yet</InquiryReadValue>
            )}
          </InquiryFormTableRow>
        )}
        {(inquiry.last_responded_at || inquiry.last_responded_by) && (
          <>
            <InquiryFormTableRow label="Last responded by">
              <InquiryReadValue empty=" - ">
                {inquiry.last_responded_by_name ?? inquiry.last_responded_by}
              </InquiryReadValue>
            </InquiryFormTableRow>
            <InquiryFormTableRow label="Last responded at">
              <InquiryReadValue empty=" - ">
                {inquiry.last_responded_at
                  ? formatDate(new Date(inquiry.last_responded_at))
                  : ''}
              </InquiryReadValue>
            </InquiryFormTableRow>
          </>
        )}
        {(inquiry.rejected_by ||
          inquiry.rejected_at ||
          (inquiry.rejection_reason != null && inquiry.rejection_reason !== '')) && (
          <>
            <InquiryFormTableRow label="Rejected by">
              <InquiryReadValue empty=" - ">
                {inquiry.rejected_by_name ?? inquiry.rejected_by}
              </InquiryReadValue>
            </InquiryFormTableRow>
            <InquiryFormTableRow label="Rejected at">
              <InquiryReadValue empty=" - ">
                {inquiry.rejected_at ? formatDate(new Date(inquiry.rejected_at)) : ''}
              </InquiryReadValue>
            </InquiryFormTableRow>
            <InquiryFormTableRow label="Rejection reason" labelClassName="items-start pt-3">
              <InquiryReadValue>{inquiry.rejection_reason}</InquiryReadValue>
            </InquiryFormTableRow>
          </>
        )}
        {((inquiry.reopen_reason != null && inquiry.reopen_reason !== '') ||
          inquiry.reopened_at ||
          inquiry.reopened_by) && (
          <>
            <InquiryFormTableRow label="Reopen reason" labelClassName="items-start pt-3">
              <InquiryReadValue>{inquiry.reopen_reason}</InquiryReadValue>
            </InquiryFormTableRow>
            <InquiryFormTableRow label="Reopened by">
              <InquiryReadValue empty=" - ">
                {inquiry.reopened_by_name ?? inquiry.reopened_by}
              </InquiryReadValue>
            </InquiryFormTableRow>
            <InquiryFormTableRow label="Reopened at">
              <InquiryReadValue empty=" - ">
                {inquiry.reopened_at ? formatDate(new Date(inquiry.reopened_at)) : ''}
              </InquiryReadValue>
            </InquiryFormTableRow>
          </>
        )}
      </ProductInquiryFormLayout>

      {inquiry.respond_inbox_url && (
        <Sheet
          open={conversationSheetOpen}
          onOpenChange={(open) => {
            setConversationSheetOpen(open);
            if (!open) {
              setReplyComposePrefill(null);
            }
          }}
        >
          <SheetContent side="right" className="flex flex-col w-full sm:max-w-lg overflow-y-auto">
            <SheetHeader className="sr-only">
              <SheetTitle>Chat Records</SheetTitle>
            </SheetHeader>
            <div className="flex-1 min-h-0 pt-2">
              <StockInquiryConversationPanel
                inquiryId={inquiryId}
                canReply
                respondInboxUrl={inquiry.respond_inbox_url}
                showAsPopup
                purchasingResponse={inquiry.purchasing_response}
                replyComposePrefill={replyComposePrefill}
                onGetViewLink={
                  publicViewLinksEnabled
                    ? async () => {
                        const baseUrl = typeof window !== 'undefined' ? window.location.origin : undefined;
                        const res = await getOrCreateStockInquiryViewLink(inquiryId, baseUrl);
                        return res.view_url ?? '';
                      }
                    : undefined
                }
              />
            </div>
          </SheetContent>
        </Sheet>
      )}

      <StockInquiryAttachmentsSection
        inquiryId={inquiryId}
        attachments={inquiry.attachments ?? []}
      />

      {/* The lineage lives in the page's own "Revisions" TAB (round 6), not in
          this stack: it is reference material, and burying it under the whole
          form meant scrolling past everything to read it. The query stays here
          because the revise banner above quotes the newest entry. */}

      <AuditTrail entityType="stock_inquiry" entityId={inquiryId} title="Audit Trail" />
    </div>
  );
}
