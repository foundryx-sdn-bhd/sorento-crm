'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAction } from '@/lib/useAction';
import { apiFetch } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { Edit, Trash2, Send, Copy, Check, Clock, MessageSquare, FileDown, Printer, Link2, ScrollText, BadgeCheck, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useBackToListHref } from '@/components/common/BackToList';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import type { ColumnDef } from '@tanstack/react-table';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import type { PurchaseRequestLine } from '../types/purchaseRequest.types';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { EntityDownloadsButton } from '@/components/my-downloads/EntityDownloadsButton';
import {
  usePurchaseRequest,
  useExportPurchaseRequestPdf,
  usePurchaseRequestRevisions,
} from '../hooks/usePurchaseRequests';
import { formatDate, formatCurrency } from '@/lib/helpers';
import { useCurrencyFormat } from '@/hooks/useCurrencyFormat';
import PurchaseRequestDeleteDialog from './purchase-request-delete-dialog';
import AuditTrail from '@/components/audit/AuditTrail';
import { purchaseRequestsPagerQuery } from '../hooks/usePurchaseRequests';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import DetailActions from '@/components/common/DetailActions';
import {
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import { sendApprovalLink, setPendingApproval, getUsersForApproverSelect, getOrCreateViewLink, rejectSubmittedPurchaseRequest, processPurchaseRequestByCs, closePurchaseRequestByCs, submitApprovalDecision, isDeferredDecision, getPurchaseRequestRevisions } from '../services/purchaseRequestService';
import { getFormSLATrackers, escalateFormTracking } from '@/app/(protected)/sla-management/_shared/formSLAService';
import { SlaActiveTrackerControls } from '@/app/(protected)/sla-management/_shared/SlaActiveTrackerControls';
import { SlaExtendMenuItem, SlaExtendDialog } from '@/app/(protected)/sla-management/_shared/SlaExtendAction';
import { useHandlingLock } from '@/app/(protected)/sla-management/_shared/useHandlingLock';
import { HandlingLockBanner } from '@/app/(protected)/sla-management/_shared/HandlingLockBanner';
import { useFormAction } from '@/app/(protected)/sla-management/_shared/useFormAction';
import { FormActionBanner } from '@/app/(protected)/sla-management/_shared/FormActionBanner';
import { UndoActionDialog } from '@/app/(protected)/sla-management/_shared/UndoActionDialog';
import { HandlingLockReleaseMenuItem } from '@/app/(protected)/sla-management/_shared/HandlingLockActions';
import ReassignDialog from '@/app/(protected)/sla-management/conversation-sla-tracking/components/ReassignDialog';
import { useReassignSLATracking } from '@/app/(protected)/sla-management/conversation-sla-tracking/hooks/useTeamPendingSLA';
import { RejectionReasonBanner } from '@/components/common/RejectionReasonBanner';
import { RevisionBanner } from '@/components/common/RevisionBanner';
import { VoidBanner } from '@/components/common/VoidBanner';
import { VoidDialog } from '@/components/common/VoidDialog';
import { useFormVoid } from '@/hooks/useFormVoid';
import { withRevisionSuffix } from '@/lib/document-number';
import { statusPillClass, STATUS_PILL_BASE } from '@/lib/status-pill';
import { ArrowUpCircle, ThumbsUp, ThumbsDown, Ban, UserRoundCog, Undo2 } from 'lucide-react';
import { useHasPermission } from '@/hooks/usePermissions';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  createSalesTypeLabelResolver,
  exportPurchaseRequestOrSponsorshipToExcel,
  exportPurchaseRequestOrSponsorshipWithRevisionsToExcel,
} from '../lib/purchase-request-excel-export';
import { ExportWithRevisionsDialog } from '@/components/common/ExportWithRevisionsDialog';
import { useRevisionEnabledMap } from '@/app/(protected)/sla-management/_shared/useRevisionEnabledMap';
import { useLookupOptionsByBinding } from '@/hooks/useLookupOptionsByBinding';
import { toast } from '@/lib/toast';
import LookupBoundLabel from '@/components/common/LookupBoundLabel';
import PurchaseRequestAttachmentsSection from './PurchaseRequestAttachmentsSection';
import PurchaseRequestConversationPanel from './PurchaseRequestConversationPanel';
import { PurchaseRequestSignoffFooter } from './PurchaseRequestSignoffFooter';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { numberFieldError } from '@/lib/number-field';
import { QUANTITY_RULES } from '../forms/purchase-request-schema';
import { usePublicViewLinksEnabled } from '@/hooks/usePublicViewLinksEnabled';
import {
  purchaseRequestNumberReplyPhrase,
  requestTypeLabel,
  requestTypeLabelLower,
} from '../lib/purchase-request-field-labels';

const DEFAULT_BASE_PATH = '/procurement-management/purchase-requests';
const SPONSORSHIP_FORMS_PATH = '/procurement-management/sponsorship-forms';
const PURCHASE_REQUESTS_PATH = '/procurement-management/purchase-requests';

interface PurchaseRequestDetailProps {
  requestId: string;
  /** Base path for list and edit links (e.g. /procurement-management/sponsorship-forms). */
  basePath?: string;
}

/**
 * The read-only "Line items" table on the printable-style detail card.
 *
 * Two shapes: a purchase request's lines carry no price (`showPricing=false`,
 * header "#"), a sponsorship form's carry unit price + total (`showPricing=true`,
 * header "NO.") - the same difference the two raw tables this replaces had.
 */
function PurchaseRequestLineItemsGrid({
  lines,
  numberHeader,
  showPricing,
  currencyFormat,
}: {
  lines: PurchaseRequestLine[];
  numberHeader: string;
  showPricing: boolean;
  currencyFormat: Parameters<typeof formatCurrency>[1];
}) {
  const columns = useMemo<ColumnDef<PurchaseRequestLine>[]>(() => {
    const base: ColumnDef<PurchaseRequestLine>[] = [
      {
        id: 'index',
        header: ({ column }) => <DataGridColumnHeader title={numberHeader} column={column} />,
        cell: ({ row }) => row.index + 1,
        size: 60,
        enableResizing: false,
        meta: { headerTitle: numberHeader },
      },
      {
        id: 'item_code',
        accessorFn: (row) => row.item_code ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Item Code" column={column} />,
        cell: ({ row }) => row.original.item_code ?? ' - ',
        size: 160,
        meta: { headerTitle: 'Item Code' },
      },
      {
        id: 'quantity',
        accessorFn: (row) => row.quantity ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
        // An external-intake line can arrive with no quantity: staff see what to enter,
        // not a dash.
        cell: ({ row }) =>
          row.original.quantity ?? (
            <span className="whitespace-normal break-words text-xs text-destructive">
              {numberFieldError(null, QUANTITY_RULES)}
            </span>
          ),
        size: 100,
        meta: { headerTitle: 'Qty' },
      },
    ];
    const pricing: ColumnDef<PurchaseRequestLine>[] = showPricing
      ? [
          {
            id: 'unit_price',
            accessorFn: (row) => row.unit_price ?? 0,
            header: ({ column }) => <DataGridColumnHeader title="U/P" column={column} />,
            cell: ({ row }) => (
              <span className="block text-right">
                {row.original.unit_price != null
                  ? formatCurrency(row.original.unit_price, currencyFormat)
                  : ' - '}
              </span>
            ),
            size: 120,
            meta: { headerTitle: 'U/P' },
          },
          {
            id: 'total',
            accessorFn: (row) => row.total ?? 0,
            header: ({ column }) => <DataGridColumnHeader title="Total" column={column} />,
            cell: ({ row }) => (
              <span className="block text-right">
                {row.original.total != null
                  ? formatCurrency(row.original.total, currencyFormat)
                  : ' - '}
              </span>
            ),
            size: 130,
            meta: { headerTitle: 'Total' },
          },
        ]
      : [];
    const remark: ColumnDef<PurchaseRequestLine> = {
      id: 'remark',
      accessorFn: (row) => row.remark ?? '',
      header: ({ column }) => <DataGridColumnHeader title="Remark" column={column} />,
      cell: ({ row }) => row.original.remark ?? ' - ',
      size: 220,
      meta: { headerTitle: 'Remark' },
    };
    return [...base, ...pricing, remark];
  }, [numberHeader, showPricing, currencyFormat]);

  return (
    <PanelDataGrid<PurchaseRequestLine>
      columns={columns}
      rows={lines}
      getRowId={(row) => row.id}
      listingKey={`procurement.purchase_requests.view::lines-${showPricing ? 'sponsorship' : 'purchase-request'}`}
      emptyTitle="No line items."
      // SF-8 (M5 run 3 review): a document's own line table renders every
      // row - a page-2 would hide lines the reader expects in one scroll.
      paginate={false}
    />
  );
}

export default function PurchaseRequestDetail({
  requestId,
  basePath = DEFAULT_BASE_PATH,
}: PurchaseRequestDetailProps) {
  const router = useRouter();
  const backHref = useBackToListHref(basePath);
  const isValidId = requestId && requestId !== 'new' && requestId !== 'edit';
  const queryClient = useQueryClient();
  // Triage a SUBMITTED request: change to pending approval, or reject it.
  const canSendForApproval = useHasPermission(
    'procurement.purchase_requests.send_for_approval',
  );
  // The approver's decision once a request is PENDING APPROVAL. Deliberately a
  // separate permission: a sales admin triages without becoming an approver.
  const canApprove = useHasPermission('procurement.purchase_requests.approve');
  const canProcess = useHasPermission('procurement.purchase_requests.process');
  const canClose = useHasPermission('procurement.purchase_requests.close');
  const canVoid = useHasPermission('procurement.purchase_requests.void');
  const canReassign = useHasPermission('sla_management.conversation_sla_tracking.reassign');
  const reassignMutation = useReassignSLATracking();
  const [reassignOpen, setReassignOpen] = useState(false);
  const [voidDialogOpen, setVoidDialogOpen] = useState(false);
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [rejecting, setRejecting] = useState(false);
  // In-system approver decision (Approve / Reject) - same behaviour as the email link.
  const [approving, setApproving] = useState(false);
  const [decisionRejectOpen, setDecisionRejectOpen] = useState(false);
  const [decisionRejectReason, setDecisionRejectReason] = useState('');
  const [decisionRejecting, setDecisionRejecting] = useState(false);
  // Escalate the active form-SLA stage straight from the form.
  const [escalateOpen, setEscalateOpen] = useState(false);
  const [extendOpen, setExtendOpen] = useState(false);
  const [escalateReason, setEscalateReason] = useState('');
  const [escalating, setEscalating] = useState(false);
  const [processDialogOpen, setProcessDialogOpen] = useState(false);
  const [closeCsDialogOpen, setCloseCsDialogOpen] = useState(false);
  const [finalizeNote, setFinalizeNote] = useState('');
  const [finalizing, setFinalizing] = useState(false);
  const { data: request, isLoading } = usePurchaseRequest(
    isValidId ? requestId : null,
  );
  const requestTypeForNav = basePath.includes('sponsorship-forms')
    ? 'sponsorship_form'
    : 'purchase_request';
  // Active form-SLA stage tracker for this form - enables the in-form Escalate button.
  const { data: slaTrackers } = useQuery({
    // Key on the request's updated_at so the active tracker (and the escalation
    // banner) refetches the moment a resolve/approve/process bumps the entity
    // the stage changes, the banner must clear without a manual refresh.
    queryKey: ['form-sla-trackers', requestTypeForNav, requestId, request?.updated_at],
    queryFn: () => getFormSLATrackers(requestTypeForNav, requestId),
    enabled: !!isValidId,
  });
  const activeTracker = (slaTrackers ?? []).find((t) => !t.is_resolved) ?? null;
  // Handling-lock ("I'm handling this") - live off the form-SLA handling tracker query.
  // Gate on the ACTIVE tracker's form type (purchase_request vs sponsorship_form), not a
  // hardcoded name - this component serves both.
  const handlingLock = useHandlingLock({
    sourceEntityType: requestTypeForNav,
    sourceEntityId: isValidId ? requestId : null,
    entityKey: request?.updated_at,
  });
  // A voided form is fully read-only - every business CTA is suppressed. Folding
  // it into businessCtasEnabled kills all the handling-gated CTAs at once; the
  // few ungated actions (Edit / Delete) are guarded on !isVoided individually.
  const isVoided = (request?.status ?? '').trim().toLowerCase() === 'voided';
  // Form-action deferral (PLAN-form-sla-undo.md). While an action is pending its grace
  // window, EVERY business CTA is suppressed - the form must commit against the state the
  // action was requested on (AC-D-10), and a second action cannot be queued (AC-D-7).
  const formAction = useFormAction({
    sourceEntityType: requestTypeForNav,
    sourceEntityId: isValidId ? requestId : null,
    entityKey: request?.updated_at,
  });
  const businessCtasEnabled =
    handlingLock.businessCtasEnabled && !isVoided && !formAction.ctasDisabled;
  const voidMutation = useFormVoid('procurement/purchase-requests', requestId, {
    queryKeysToInvalidate: [['purchase-request', requestId]],
  });
  const currencyFormat = useCurrencyFormat();
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [downloadsOpen, setDownloadsOpen] = useState(false);
  const [undoDialogOpen, setUndoDialogOpen] = useState(false);
  const [approvalDialogOpen, setApprovalDialogOpen] = useState(false);
  const [approverUserId, setApproverUserId] = useState<string>('');
  const [approverEmail, setApproverEmail] = useState('');
  const [approvalLink, setApprovalLink] = useState<string | null>(null);
  const [approvalSending, setApprovalSending] = useState(false);
  const [approvalAction, setApprovalAction] = useState<'create' | 'send' | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // Change-to-pending action via the shared one-shot guard (ref-lock kills same-tick
  // double-click; awaiting the refetch inside keeps it disabled until status flips,
  // so it can't re-fire on a stale view). Backend idempotency middleware is the net.
  const changeToPending = useAction(async () => {
    if (!requestId) return;
    try {
      const result = await setPendingApproval(requestId);
      await queryClient.invalidateQueries({ queryKey: ['purchase-request', requestId] });
      if (isDeferredDecision(result)) {
        formAction.refresh();
        toast.success('Sending for approval - you can still undo.');
      } else {
        toast.success('Status set to Pending approval');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to set pending approval');
    }
  });
  const [exportingExcel, setExportingExcel] = useState(false);
  const exportPdfMutation = useExportPurchaseRequestPdf();
  // Same source LookupBoundLabel reads, so the sheet says "Cash Sales" where
  // the screen does, never the raw `cash_sales` code.
  const salesTypeOptions = useLookupOptionsByBinding('purchase_requests', 'sales_type');
  const [viewLinkCopying, setViewLinkCopying] = useState(false);
  const [approvalLinkCopying, setApprovalLinkCopying] = useState(false);
  // Each item ticks in place and the menu stays open to be read (S7-05).
  const [approvalLinkCopied, setApprovalLinkCopied] = useState(false);
  const [viewLinkCopied, setViewLinkCopied] = useState(false);
  const [conversationSheetOpen, setConversationSheetOpen] = useState(false);
  const [replyComposePrefill, setReplyComposePrefill] = useState<{
    key: number;
    text: string;
  } | null>(null);
  const [openingReplySheet, setOpeningReplySheet] = useState(false);
  const publicViewLinksEnabled = usePublicViewLinksEnabled();
  // Denormalized revision counter (UAC H4). Drives the banner, the number
  // suffix and the revision-lineage refetch.
  const revisionNo = Number(request?.revision_no ?? 0);
  const revisionsQuery = usePurchaseRequestRevisions(
    isValidId ? requestId : null,
    revisionNo,
  );
  // The newest entry carries the reason and submitter the banner quotes verbatim.
  const latestRevision = (revisionsQuery.data ?? [])
    .filter((entry) => (entry.revision_no ?? 0) > 0)
    .at(-1);

  /**
   * "Include revisions?" is only a real question when this record HAS revisions
   * and the type has them switched on (round 6, 6.4). Anywhere else both exports
   * behave exactly as they always have, with no dialog in the way. PR and SF are
   * separate types in the config, so the flag is read per request type.
   */
  const { data: revisionEnabledMap } = useRevisionEnabledMap();
  const canOfferRevisionExport =
    revisionEnabledMap?.[request?.request_type ?? ''] === true && revisionNo > 0;
  const [exportChoice, setExportChoice] = useState<'excel' | 'pdf' | null>(null);

  const runExcelExport = async (includeRevisions: boolean) => {
    if (!request) return;
    setExportingExcel(true);
    try {
      const resolve = createSalesTypeLabelResolver(salesTypeOptions.data?.options);
      if (includeRevisions) {
        // The lineage as it stands at export time. The banner query has usually
        // already cached it (keyed on `revision_no`, so it cannot be stale after
        // a revision lands); the direct read is the fallback for a first click
        // before it resolves.
        const entries = revisionsQuery.data ?? (await getPurchaseRequestRevisions(requestId));
        await exportPurchaseRequestOrSponsorshipWithRevisionsToExcel(request, entries, resolve);
      } else {
        await exportPurchaseRequestOrSponsorshipToExcel(request, resolve(request.sales_type));
      }
      toast.success(
        request.request_type === 'sponsorship_form'
          ? 'Sponsorship form exported to Excel'
          : 'Purchase request exported to Excel',
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExportingExcel(false);
    }
  };

  const handleExportExcel = async () => {
    if (!request) return;
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
    exportPdfMutation.mutate(requestId);
  };

  const handleExportConfirmed = (includeRevisions: boolean) => {
    const choice = exportChoice;
    setExportChoice(null);
    if (choice === 'pdf') {
      // No option chosen, no body: the request stays the one this export has
      // always sent.
      exportPdfMutation.mutate(
        includeRevisions ? { id: requestId, options: { include_revisions: true } } : requestId,
      );
      return;
    }
    void runExcelExport(includeRevisions);
  };

  // Narrow, flat projection of system settings open to every authenticated user.
  // The full blob is gated on `user_management.settings.view`, which the roles that
  // work purchase requests do not hold, and only the default approver is needed here.
  const { data: appConfig } = useQuery({
    queryKey: ['system-app-config'],
    queryFn: async () => {
      const r = await apiFetch('/api/user-management/settings/app-config');
      if (!r.ok) throw new Error('Failed to load settings');
      return r.json() as Promise<Record<string, unknown>>;
    },
    staleTime: 60_000,
  });

  const openUpdateAndReplyInChat = useCallback(async () => {
    if (!request || !requestId || !isValidId) return;
    let viewUrl = '';
    if (publicViewLinksEnabled) {
      try {
        const baseUrl = typeof window !== 'undefined' ? window.location.origin : undefined;
        const { view_url } = await getOrCreateViewLink(requestId, baseUrl);
        viewUrl = view_url ?? '';
      } catch {
        toast.error('Could not generate view link. You can still edit the message in chat.');
      }
    }
    const typeLabelVal = requestTypeLabel(request.request_type);
    const idPhrase = purchaseRequestNumberReplyPhrase(
      request.request_type,
      request.request_number,
      request.revision_no,
    );
    let fullMessage = `This is the ${idPhrase} for ${typeLabelVal} for project title ${request.project_title ?? ''}.`;
    if (viewUrl) {
      fullMessage += `\n\nView full details: ${viewUrl}`;
    }
    setReplyComposePrefill((p) => ({
      key: (p?.key ?? 0) + 1,
      text: fullMessage,
    }));
    setConversationSheetOpen(true);
  }, [request, requestId, isValidId, publicViewLinksEnabled, request?.request_type]);

  const { data: usersForApprover = [] } = useQuery({
    queryKey: ['users-for-approver'],
    queryFn: getUsersForApproverSelect,
    enabled: approvalDialogOpen,
  });

  const configuredDefaultApproverUserId = useMemo(() => {
    const s = appConfig;
    if (!s || !request?.request_type) return null;
    if (request.request_type === 'purchase_request') {
      const id = s.purchase_request_default_approver_user_id;
      return typeof id === 'string' && id.length > 0 ? id : null;
    }
    const id = s.sponsorship_form_default_approver_user_id;
    return typeof id === 'string' && id.length > 0 ? id : null;
  }, [appConfig, request?.request_type]);

  const configuredDefaultApproverEmail = useMemo(() => {
    const s = appConfig;
    if (!s || !request?.request_type) return null;
    if (request.request_type === 'purchase_request') {
      const e = s.purchase_request_default_approver_email;
      return typeof e === 'string' && e.length > 0 ? e : null;
    }
    const e = s.sponsorship_form_default_approver_email;
    return typeof e === 'string' && e.length > 0 ? e : null;
  }, [appConfig, request?.request_type]);

  const listLabel =
    basePath.includes('sponsorship-forms') ? 'Sponsorship Forms' : 'Purchase Requests';

  // Redirect to the correct section if record type doesn't match (e.g. opened purchase-requests/123 but record is sponsorship_form)
  useEffect(() => {
    if (!requestId || !request?.request_type) return;
    const onSponsorshipForms = basePath.includes('sponsorship-forms');
    if (onSponsorshipForms && request.request_type === 'purchase_request') {
      router.replace(`${PURCHASE_REQUESTS_PATH}/${requestId}`);
    } else if (!onSponsorshipForms && request.request_type === 'sponsorship_form') {
      router.replace(`${SPONSORSHIP_FORMS_PATH}/${requestId}`);
    }
  }, [requestId, request?.request_type, basePath, router]);

  if (!isValidId) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Invalid ID</p>
        <Button
          variant="outline"
          onClick={() => router.push(basePath)}
          className="mt-4"
        >
          Back to {listLabel}
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

  if (!request) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Request not found</p>
        <Button
          variant="outline"
          onClick={() => router.push(basePath)}
          className="mt-4"
        >
          Back to {listLabel}
        </Button>
      </div>
    );
  }

  const typeLabel =
    requestTypeLabel(request.request_type);
  const isPurchaseRequest = request.request_type === 'purchase_request';
  const expectedPoDisplay =
    request.expected_po_date_text?.trim() ||
    (request.expected_po_date
      ? formatDate(new Date(request.expected_po_date))
      : null);
  const sponsorshipTotalDisplay =
    request.total_project_value_text?.trim() ||
    (request.total_project_value != null
      ? Number(request.total_project_value).toLocaleString()
      : null);

  const approvalStatusNorm = (request.approval_status ?? '').trim();
  const lifecycleStatusNorm = (request.status ?? '').trim().toLowerCase();
  const isDraftLifecycle = lifecycleStatusNorm === 'draft';
  const isSubmittedLifecycle = lifecycleStatusNorm === 'submitted';
  const isDraftLike =
    approvalStatusNorm === '' || approvalStatusNorm === 'draft';
  const isRejected = approvalStatusNorm === 'rejected';
  const isPendingApproval = approvalStatusNorm === 'pending';
  const isApprovedStatus = approvalStatusNorm === 'approved';
  // "Change to pending approval" + "Reject" only when the salesperson has
  // submitted from the portal (status='submitted') AND no approval decision has
  // been recorded yet. Once rejected, only the salesperson re-submits via the
  // portal - reviewer cannot bypass that loop by moving straight to pending.
  const showPrimaryChangeToPending =
    canSendForApproval &&
    isSubmittedLifecycle &&
    !isApprovedStatus &&
    !isPendingApproval &&
    !isRejected &&
    isDraftLike;
  const showPrimarySendForApproval =
    isPendingApproval && !isApprovedStatus;
  const showRejectSubmitted =
    canSendForApproval && isSubmittedLifecycle && isDraftLike && !isRejected;
  // Customer-service handoff: an approved request enters the CS stage. CS marks it
  // processed or closed; both finalize the customer-service form-SLA stage.
  const isProcessedByCs = lifecycleStatusNorm === 'processed_by_cs';
  const isClosedByCs = lifecycleStatusNorm === 'closed';
  const isCsFinalized = isProcessedByCs || isClosedByCs;
  // A No CS form stays Approved but never gets a customer-service stage, so the CS
  // actions need that stage's tracker (active or resolved); hidden while trackers load.
  const hasCsStage = (slaTrackers ?? []).some((t) => t.team_set_code === 'customer_service');
  const showCsActions = isApprovedStatus && !isCsFinalized && hasCsStage;
  const csFinalizeLabel = isProcessedByCs
    ? 'Processed by CS'
    : isClosedByCs
      ? 'Closed'
      : null;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1 min-w-0">
          <h2 className="text-2xl font-bold break-words">
            {typeLabel}
            {request.request_number
              ? ` - ${withRevisionSuffix(request.request_number, revisionNo)}`
              : request.customer_name
                ? ` - ${request.customer_name}`
                : request.project_title
                  ? ` - ${request.project_title}`
                  : ''}
          </h2>
          <p className="text-sm text-muted-foreground">
            {request.submitted_at
              ? formatDate(new Date(request.submitted_at))
              : '-'}{' '}
            · {typeLabel}
          </p>
        </div>
        <DetailActions
          pager={{
            ...purchaseRequestsPagerQuery,
            detailPath: basePath,
            currentId: requestId,
            ariaLabel: requestTypeLabelLower(request.request_type),
          }}
          gear={
            <DetailActionsMenu ariaLabel="Request actions">
              {/* The download history is an action, so it sits in the gear with the rest.
                  Edit is the record's primary button and Delete is the last item here; the
                  card carries the pager, the gear and the workflow CTAs, nothing else. */}
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
                  <Undo2 className="size-4" />
                  Undo last action
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
              {businessCtasEnabled && showCsActions && canClose && (
                <DropdownMenuItem
                  disabled={finalizing}
                  onClick={() => {
                    setFinalizeNote('');
                    setCloseCsDialogOpen(true);
                  }}
                >
                  <XCircle className="size-4" />
                  Mark as closed
                </DropdownMenuItem>
              )}
              {showPrimarySendForApproval && (
                <DropdownMenuItem
                  disabled={approvalLinkCopying}
                  onSelect={async (e) => {
                    e.preventDefault();
                    if (!requestId) return;
                    const approverUserId =
                      request.approver_user_id ?? configuredDefaultApproverUserId ?? undefined;
                    const approverEmail =
                      request.approver_email ?? configuredDefaultApproverEmail ?? undefined;
                    setApprovalLinkCopying(true);
                    try {
                      const baseUrl =
                        typeof window !== 'undefined' ? window.location.origin : undefined;
                      const res = await sendApprovalLink(requestId, {
                        approver_email: approverEmail,
                        approver_user_id: approverUserId,
                        expires_hours: 24,
                        send_email: false,
                        base_url: baseUrl,
                      });
                      if (res.approval_url) {
                        await navigator.clipboard.writeText(res.approval_url);
                        setApprovalLinkCopied(true);
                        window.setTimeout(() => setApprovalLinkCopied(false), 2000);
                      } else {
                        toast.error('Could not generate approval link');
                      }
                    } catch (err) {
                      toast.error(
                        err instanceof Error ? err.message : 'Could not generate approval link',
                      );
                    } finally {
                      setApprovalLinkCopying(false);
                    }
                  }}
                >
                  {approvalLinkCopied ? <Check className="size-4" /> : <Link2 className="size-4" />}
                  {approvalLinkCopying
                    ? 'Generating…'
                    : approvalLinkCopied
                      ? 'Copied'
                      : 'Copy approval link'}
                </DropdownMenuItem>
              )}
              {publicViewLinksEnabled && (
                <DropdownMenuItem
                  disabled={viewLinkCopying}
                  onSelect={async (e) => {
                    e.preventDefault();
                    if (!requestId) return;
                    setViewLinkCopying(true);
                    try {
                      const baseUrl = typeof window !== 'undefined' ? window.location.origin : undefined;
                      const { view_url } = await getOrCreateViewLink(requestId, baseUrl);
                      if (view_url) {
                        await navigator.clipboard.writeText(view_url);
                        setViewLinkCopied(true);
                        window.setTimeout(() => setViewLinkCopied(false), 2000);
                      } else {
                        toast.error('Could not generate view link');
                      }
                    } catch {
                      toast.error('Could not generate view link');
                    } finally {
                      setViewLinkCopying(false);
                    }
                  }}
                >
                  {viewLinkCopied ? <Check className="size-4" /> : <Link2 className="size-4" />}
                  {viewLinkCopying ? 'Generating…' : viewLinkCopied ? 'Copied' : 'Copy view link'}
                </DropdownMenuItem>
              )}
              <DropdownMenuItem
                disabled={exportingExcel}
                onClick={async (e) => {
                  e.preventDefault();
                  await handleExportExcel();
                }}
              >
                <FileDown className="size-4" />
                {exportingExcel ? 'Exporting…' : 'Export to Excel'}
              </DropdownMenuItem>
              <DropdownMenuItem
                data-guide-target="procurement.purchase-requests.download-pdf"
                disabled={exportPdfMutation.isPending}
                onSelect={(e) => {
                  e.preventDefault();
                  handleExportPdf();
                }}
              >
                <Printer className="size-4" />
                {exportPdfMutation.isPending ? 'Preparing…' : 'Print / Download PDF'}
              </DropdownMenuItem>
              {request.respond_inbox_url && (
                <>
                  <DropdownMenuItem onClick={() => setConversationSheetOpen(true)}>
                    <ScrollText className="size-4" />
                    Chat records
                  </DropdownMenuItem>
                  {businessCtasEnabled && (
                  <DropdownMenuItem
                    disabled={openingReplySheet}
                    onClick={async (e) => {
                      e.preventDefault();
                      setOpeningReplySheet(true);
                      try {
                        await openUpdateAndReplyInChat();
                      } finally {
                        setOpeningReplySheet(false);
                      }
                    }}
                  >
                    <Send className="size-4" />
                    {openingReplySheet ? 'Opening…' : 'Update & Reply'}
                  </DropdownMenuItem>
                  )}
                </>
              )}
              {canVoid && !isVoided && (
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
                  onSelect={() => setDeleteDialogOpen(true)}
                >
                  <Trash2 className="size-4" />
                  Delete
                </DropdownMenuItem>
              )}
            </DetailActionsMenu>
          }
          primary={
            <>
            {/* Business CTAs HIDE (not disable) while the handling lock is held by
                someone else / unclaimed - keeps the header uncluttered. When the lock
                does not bite (tier 1, flag off, or I hold it) businessCtasEnabled is
                true and they render on their normal status+permission gates. */}
            {businessCtasEnabled && showPrimaryChangeToPending && (
              <Button
                disabled={changeToPending.running}
                onClick={() => changeToPending.run()}
                data-guide-target="procurement.approvals.change-to-pending-approval-button"
              >
                <Clock className="size-4" />
                {changeToPending.running ? 'Updating…' : 'Change to pending approval'}
              </Button>
            )}
            {businessCtasEnabled && showRejectSubmitted && (
              <Button
                variant="outline"
                className="border-destructive text-destructive hover:bg-destructive/10"
                disabled={rejecting}
                onClick={() => {
                  setRejectReason('');
                  setRejectDialogOpen(true);
                }}
              >
                <Trash2 className="size-4" />
                Reject
              </Button>
            )}
            {/* "Send for approval" email button retired: moving to pending approval
                fires the SLA assignment, which notifies the approver, who then uses
                the in-system Approve/Reject below. The emailed link survives as an
                optional external fallback under the gear ("Copy approval link"). */}
            {/* In-system approver decision - same effect as the emailed approval link,
                so the approver can decide without leaving the system. */}
            {businessCtasEnabled && isPendingApproval && canApprove && (
              <>
                <Button
                  data-guide-target="procurement.purchase-requests.approve-button"
                  className="bg-emerald-600 text-white hover:bg-emerald-700"
                  disabled={approving}
                  onClick={async () => {
                    setApproving(true);
                    try {
                      const result = await submitApprovalDecision(requestId, 'approved');
                      queryClient.invalidateQueries({ queryKey: ['purchase-request', requestId] });
                      queryClient.invalidateQueries({ queryKey: ['form-sla-trackers', requestTypeForNav, requestId] });
                      // A deferred decision has written nothing and sent nothing, so
                      // saying "approved" would be a lie. Refresh the form-action read
                      // instead so the countdown + Undo appear.
                      if (isDeferredDecision(result)) {
                        formAction.refresh();
                        toast.success('Approving - you can still undo.');
                      } else {
                        toast.success('Request approved');
                      }
                    } catch (e) {
                      toast.error(e instanceof Error ? e.message : 'Failed to approve');
                    } finally {
                      setApproving(false);
                    }
                  }}
                >
                  <ThumbsUp className="size-4" />
                  {approving ? 'Approving…' : 'Approve'}
                </Button>
                <Button
                  data-guide-target="procurement.purchase-requests.reject-button"
                  variant="outline"
                  className="border-destructive text-destructive hover:bg-destructive/10"
                  disabled={decisionRejecting}
                  onClick={() => {
                    setDecisionRejectReason('');
                    setDecisionRejectOpen(true);
                  }}
                >
                  <ThumbsDown className="size-4" />
                  Reject
                </Button>
              </>
            )}
            {businessCtasEnabled && showCsActions && canProcess && (
              <Button
                disabled={finalizing}
                className="bg-emerald-600 text-white hover:bg-emerald-700"
                onClick={() => {
                  setFinalizeNote('');
                  setProcessDialogOpen(true);
                }}
              >
                <BadgeCheck className="size-4" />
                Processed by CS
              </Button>
            )}
              {!isVoided && !formAction.ctasDisabled && (
                <Button
                  variant="outline"
                  onClick={() => router.push(`${basePath}/${requestId}/edit`)}
                >
                  <Edit className="size-4" />
                  Edit
                </Button>
              )}
            </>
          }
          dialogs={
            <EntityDownloadsButton
              entityType="purchase_request"
              entityId={requestId}
              label={request.request_number ?? undefined}
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
          entityLabel={request.request_number || typeLabel}
          onConfirm={(reason) => {
            formAction.undo(reason);
            setUndoDialogOpen(false);
          }}
          isSubmitting={formAction.isMutating}
        />
      )}

      <Dialog open={approvalDialogOpen} onOpenChange={setApprovalDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Send for approval</DialogTitle>
            <DialogDescription>
              Choose a user to pull their email, or enter an email if the approver is not in the system. Create a one-time approval link only, or create and send it by email to the approver.
            </DialogDescription>
          </DialogHeader>
          {!approvalLink ? (
            <>
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label>Choose approver (optional)</Label>
                  <SearchableSelect
                    value={approverUserId || '__email_only__'}
                    onChange={(v) => {
                      if (v === '__email_only__') {
                        setApproverUserId('');
                        return;
                      }
                      const u = usersForApprover.find((x) => x.id === v);
                      setApproverUserId(v);
                      // Picking a known approver also fills the email field, as before.
                      setApproverEmail(u?.email ?? '');
                    }}
                    placeholder="Select approver"
                    emptyMessage="No approver found."
                    triggerClassName="w-full"
                    options={[
                      { value: '__email_only__', label: 'Enter email only (not in system)' },
                      ...usersForApprover.map((u) => ({
                        value: u.id,
                        label: `${u.name?.trim() || u.email} (${u.email})`,
                        searchText: `${u.name?.trim() || u.email} ${u.email}`.trim(),
                      })),
                    ]}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="approver_email">Approver email</Label>
                  <Input
                    id="approver_email"
                    type="email"
                    value={approverEmail}
                    onChange={(e) => setApproverEmail(e.target.value)}
                    placeholder="approver@example.com"
                  />
                </div>
              </div>
              {approvalError && (
                <p className="text-sm text-destructive">{approvalError}</p>
              )}
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setApprovalDialogOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  variant="outline"
                  data-guide-target="procurement.approvals.create-link-only-button"
                  disabled={!approverEmail.trim() || approvalSending}
                  onClick={async () => {
                    setApprovalError(null);
                    setApprovalSending(true);
                    setApprovalAction('create');
                    try {
                      const baseUrl = typeof window !== 'undefined' ? window.location.origin : undefined;
                      const res = await sendApprovalLink(requestId, {
                        approver_email: approverEmail.trim(),
                        approver_user_id: approverUserId || undefined,
                        expires_hours: 24,
                        send_email: false,
                        base_url: baseUrl,
                      });
                      const url = res.approval_url.startsWith('http')
                        ? res.approval_url
                        : typeof window !== 'undefined'
                          ? `${window.location.origin}${res.approval_url.startsWith('/') ? res.approval_url : `/${res.approval_url}`}`
                          : res.approval_url;
                      setApprovalLink(url);
                      void queryClient.invalidateQueries({ queryKey: ['purchase-request', requestId] });
                      toast.success('Approval link created. Copy the link below to share.');
                    } catch (e) {
                      setApprovalError(e instanceof Error ? e.message : 'Failed to create link');
                    } finally {
                      setApprovalSending(false);
                      setApprovalAction(null);
                    }
                  }}
                >
                  {approvalSending && approvalAction === 'create' ? 'Creating…' : 'Create link only'}
                </Button>
                <Button
                  data-guide-target="procurement.approvals.create-link-and-send-button"
                  disabled={!approverEmail.trim() || approvalSending}
                  onClick={async () => {
                    setApprovalError(null);
                    setApprovalSending(true);
                    setApprovalAction('send');
                    try {
                      const baseUrl = typeof window !== 'undefined' ? window.location.origin : undefined;
                      const res = await sendApprovalLink(requestId, {
                        approver_email: approverEmail.trim(),
                        approver_user_id: approverUserId || undefined,
                        expires_hours: 24,
                        send_email: true,
                        base_url: baseUrl,
                      });
                      const url = res.approval_url.startsWith('http')
                        ? res.approval_url
                        : typeof window !== 'undefined'
                          ? `${window.location.origin}${res.approval_url.startsWith('/') ? res.approval_url : `/${res.approval_url}`}`
                          : res.approval_url;
                      setApprovalLink(url);
                      void queryClient.invalidateQueries({ queryKey: ['purchase-request', requestId] });
                      if (res.email_sent) {
                        toast.success(`Approval link created and sent to ${approverEmail.trim()}`);
                      } else if (res.email_error) {
                        toast.warning(`Link created but email could not be sent: ${res.email_error}. You can copy the link below.`);
                      }
                    } catch (e) {
                      setApprovalError(e instanceof Error ? e.message : 'Failed to create link');
                    } finally {
                      setApprovalSending(false);
                      setApprovalAction(null);
                    }
                  }}
                >
                  {approvalSending && approvalAction === 'send' ? 'Creating & sending…' : 'Create link & send email'}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <div className="space-y-2">
                <Label>Approval link (one-time use)</Label>
                <div className="flex gap-2">
                  <Input readOnly value={approvalLink} className="font-mono text-sm" />
                  <Button
                    size="icon"
                    variant="outline"
                    onClick={() => {
                      void navigator.clipboard.writeText(approvalLink);
                      setCopied(true);
                      setTimeout(() => setCopied(false), 2000);
                    }}
                    aria-label="Copy approval link"
                  >
                    {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button onClick={() => { setApprovalDialogOpen(false); setApprovalLink(null); }}>
                  Done
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      <PurchaseRequestDeleteDialog
        open={deleteDialogOpen}
        closeDialog={() => setDeleteDialogOpen(false)}
        request={request}
        entityLabel={typeLabel}
        onSuccess={() => router.push(backHref)}
      />

      {request.approval_status === 'rejected' && (
        // PR/SF store the rejection reason in approval_comments (both CS-reject
        // before approval and approver-reject write it there), not a dedicated
        // rejection_reason column.
        <RejectionReasonBanner
          reason={request.approval_comments}
          // BE emits `rejected_by_name` / `rejected_by_wa_phone` from the new
          // `rejected_by_id` column, and already applies the legacy `approved_by`
          // fallback server-side WITH a bare-UUID guard (HIST-3). Do NOT re-add an
          // unguarded `?? request.approved_by` here - that would leak a raw UUID into
          // the UI when approved_by holds an id. WHEN sourced from `approved_at`.
          rejectedByName={(request as { rejected_by_name?: string | null }).rejected_by_name ?? undefined}
          rejectedByWaPhone={(request as { rejected_by_wa_phone?: string | null }).rejected_by_wa_phone ?? undefined}
          rejectedAt={request.approved_at}
        />
      )}

      {/* Contact revised their submission (UAC H1). Renders nothing at revision 0. */}
      <RevisionBanner
        revisionNo={revisionNo}
        documentNumber={request.request_number}
        revisedAt={request.last_revised_at}
        revisedByName={latestRevision?.submitted_by}
        reason={latestRevision?.reason}
      />

      <VoidBanner
        voided={isVoided}
        voidedByName={request.voided_by_name}
        voidedAt={request.voided_at}
        voidReason={request.void_reason}
      />

      <VoidDialog
        open={voidDialogOpen}
        onOpenChange={setVoidDialogOpen}
        isPending={voidMutation.isPending}
        onConfirm={(reason) => voidMutation.mutateAsync({ void_reason: reason })}
      />

      <SlaActiveTrackerControls
        activeTracker={activeTracker}
        label={`${typeLabel}${request.request_number ? ` · ${request.request_number}` : ''}`}
        onExtended={() =>
          void queryClient.invalidateQueries({
            queryKey: ['form-sla-trackers', requestTypeForNav, requestId],
          })
        }
      />


      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {isPurchaseRequest ? (
          <div className="lg:col-span-2 w-full">
            <Card className="border-2 shadow-sm">
              <CardContent className="pt-6 pb-8 px-5 sm:px-10">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">{typeLabel}</Badge>
                    {(() => {
                      const statusText =
                        request.approval_status === 'pending'
                          ? 'Pending approval'
                          : request.approval_status === 'approved'
                            ? (csFinalizeLabel ?? 'Approved')
                            : request.approval_status === 'rejected'
                              ? 'Rejected'
                              : isSubmittedLifecycle
                                ? 'Submitted'
                                : isDraftLifecycle
                                  ? 'Draft'
                                  : (request.approval_status || request.status || 'Draft');
                      return (
                        <span className={`${STATUS_PILL_BASE} ${statusPillClass(statusText)}`}>
                          {statusText}
                        </span>
                      );
                    })()}
                  </div>
                </div>
                <h2 className="text-center text-xl font-semibold tracking-tight border-b border-border pb-4 mb-6">
                  Purchase Request
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-10 gap-y-5 [&>div]:min-w-0 [&_p]:break-words">
                  <div>
                    <p className="text-sm text-muted-foreground">Purchase request number</p>
                    <p className="font-medium tabular-nums">
                      {withRevisionSuffix(request.request_number, revisionNo) || ' - '}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Submitted date</p>
                    <p className="font-medium">
                      {request.submitted_at
                        ? formatDate(new Date(request.submitted_at))
                        : ' - '}
                    </p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Customer Name</p>
                    <p className="font-medium">{request.customer_name || ' - '}</p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">PIC</p>
                    <p className="font-medium whitespace-pre-wrap">{request.pic || ' - '}</p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Project Title</p>
                    <p className="font-medium">{request.project_title || ' - '}</p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Purpose</p>
                    <p className="font-medium">{request.purpose || ' - '}</p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Sales Type</p>
                    <p className="font-medium">
                      {request.sales_type ? (
                        <LookupBoundLabel
                          table="purchase_requests"
                          column="sales_type"
                          value={request.sales_type}
                        />
                      ) : (
                        ' - '
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Expected date of delivery</p>
                    <p className="font-medium">
                      {request.expected_delivery_date
                        ? formatDate(new Date(request.expected_delivery_date))
                        : ' - '}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Expected date to receive PO</p>
                    <p className="font-medium">{expectedPoDisplay || ' - '}</p>
                  </div>
                  {(request.approver_email || request.approver_user_id) && !request.approved_at && (
                    <div className="sm:col-span-2">
                      <p className="text-sm text-muted-foreground">Approver</p>
                      <p className="font-medium">
                        {request.approver_display_name
                          ? `${request.approver_display_name} (${request.approver_email})`
                          : request.approver_email}
                      </p>
                    </div>
                  )}
                  {request.respond_inbox_url && (
                    <div className="sm:col-span-2">
                      <p className="text-sm text-muted-foreground">Respond conversation</p>
                      <div className="flex flex-col sm:flex-row sm:items-center gap-2 py-0.5">
                        <a
                          href={request.respond_inbox_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline text-sm break-all font-medium"
                        >
                          {request.respond_inbox_url}
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
                    </div>
                  )}
                </div>

                <div className="mt-8">
                  <p className="text-sm font-medium mb-3">Line items</p>
                  {request.lines && request.lines.length > 0 ? (
                    <PurchaseRequestLineItemsGrid
                      lines={request.lines}
                      numberHeader="#"
                      showPricing={false}
                      currencyFormat={currencyFormat}
                    />
                  ) : (
                    <p className="text-sm text-muted-foreground">No line items.</p>
                  )}
                </div>

                <PurchaseRequestSignoffFooter request={request} variant="detailCard" />
              </CardContent>
            </Card>
          </div>
        ) : (
          <div className="lg:col-span-2 w-full">
            <Card className="border-2 shadow-sm">
              <CardContent className="pt-6 pb-8 px-5 sm:px-10">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">{typeLabel}</Badge>
                    {(() => {
                      const statusText =
                        request.approval_status === 'pending'
                          ? 'Pending approval'
                          : request.approval_status === 'approved'
                            ? (csFinalizeLabel ?? 'Approved')
                            : request.approval_status === 'rejected'
                              ? 'Rejected'
                              : isSubmittedLifecycle
                                ? 'Submitted'
                                : isDraftLifecycle
                                  ? 'Draft'
                                  : (request.approval_status || request.status || 'Draft');
                      return (
                        <span className={`${STATUS_PILL_BASE} ${statusPillClass(statusText)}`}>
                          {statusText}
                        </span>
                      );
                    })()}
                  </div>
                </div>
                <h2 className="text-center text-xl font-semibold tracking-tight border-b border-border pb-4 mb-6">
                  Project Sales Sponsorship Form
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-10 gap-y-5 [&>div]:min-w-0 [&_p]:break-words">
                  <div>
                    <p className="text-sm text-muted-foreground">Sponsorship form number</p>
                    <p className="font-medium tabular-nums">
                      {withRevisionSuffix(request.request_number, revisionNo) || ' - '}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Submitted date</p>
                    <p className="font-medium">
                      {request.submitted_at
                        ? formatDate(new Date(request.submitted_at))
                        : ' - '}
                    </p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Customer Name</p>
                    <p className="font-medium">{request.customer_name || ' - '}</p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">PIC</p>
                    <p className="font-medium whitespace-pre-wrap">{request.pic || ' - '}</p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Delivery Address</p>
                    <p className="font-medium whitespace-pre-wrap">{request.delivery_address || ' - '}</p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Project Title</p>
                    <p className="font-medium">{request.project_title || ' - '}</p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Total Project Value</p>
                    <p className="font-medium">{sponsorshipTotalDisplay || ' - '}</p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Sponsor Subject</p>
                    <p className="font-medium">
                      {request.sponsor_subject ? (
                        <LookupBoundLabel
                          table="purchase_requests"
                          column="sponsor_subject"
                          value={request.sponsor_subject}
                          fallback=" - "
                        />
                      ) : (
                        ' - '
                      )}
                      {request.sponsor_subject === 'others' && request.sponsor_subject_other
                        ? `: ${request.sponsor_subject_other}`
                        : ''}
                    </p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Sales Type</p>
                    <p className="font-medium">
                      {request.sales_type ? (
                        <LookupBoundLabel
                          table="purchase_requests"
                          column="sales_type"
                          value={request.sales_type}
                        />
                      ) : (
                        ' - '
                      )}
                    </p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-sm text-muted-foreground">Date of Delivery</p>
                    <p className="font-medium">
                      {request.expected_delivery_date
                        ? formatDate(new Date(request.expected_delivery_date))
                        : ' - '}
                    </p>
                  </div>
                  {(request.approver_email || request.approver_user_id) && !request.approved_at && (
                    <div className="sm:col-span-2">
                      <p className="text-sm text-muted-foreground">Approver</p>
                      <p className="font-medium">
                        {request.approver_display_name
                          ? `${request.approver_display_name} (${request.approver_email})`
                          : request.approver_email}
                      </p>
                    </div>
                  )}
                  {request.respond_inbox_url && (
                    <div className="sm:col-span-2">
                      <p className="text-sm text-muted-foreground">Respond conversation</p>
                      <div className="flex flex-col sm:flex-row sm:items-center gap-2 py-0.5">
                        <a
                          href={request.respond_inbox_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline text-sm break-all font-medium"
                        >
                          {request.respond_inbox_url}
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
                    </div>
                  )}
                </div>

                <div className="mt-8">
                  <p className="text-sm font-medium mb-3">Line items</p>
                  {request.lines && request.lines.length > 0 ? (
                    <>
                      <PurchaseRequestLineItemsGrid
                        lines={request.lines}
                        numberHeader="NO."
                        showPricing
                        currencyFormat={currencyFormat}
                      />
                      {request.grand_total != null && (
                        <div className="mt-4 flex justify-end">
                          <p className="text-sm font-semibold">
                            Grand Total: {formatCurrency(request.grand_total, currencyFormat)}
                          </p>
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="text-sm text-muted-foreground">No line items.</p>
                  )}
                </div>

                <PurchaseRequestSignoffFooter request={request} variant="detailCard" />
              </CardContent>
            </Card>
          </div>
        )}

        <div className="lg:col-span-2">
          <PurchaseRequestAttachmentsSection
            requestId={requestId}
            attachments={request.attachments}
          />
        </div>

        {/* The lineage lives in the page's own "Revisions" TAB (round 6), not in
            this grid: it is reference material, and burying it under the whole
            form meant scrolling past everything to read it. The query stays here
            because the revise banner above quotes the newest entry. */}

        {request?.respond_inbox_url && (
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
                <PurchaseRequestConversationPanel
                  requestId={requestId}
                  requestNumber={request.request_number ?? undefined}
                  canReply
                  respondInboxUrl={request.respond_inbox_url}
                  showAsPopup
                  replyComposePrefill={replyComposePrefill}
                  onGetViewLink={
                    publicViewLinksEnabled
                      ? async () => {
                          const baseUrl = typeof window !== 'undefined' ? window.location.origin : undefined;
                          const res = await getOrCreateViewLink(requestId, baseUrl);
                          return res.view_url ?? '';
                        }
                      : undefined
                  }
                />
              </div>
            </SheetContent>
          </Sheet>
        )}

        {/* Direct child of the 2-col grid - without the span it renders half-width while
            every sibling block above it is full-width. */}
        <div className="lg:col-span-2 w-full">
          <AuditTrail entityType="purchase_request" entityId={requestId} title="Audit Trail" />
        </div>

        <AlertDialog open={rejectDialogOpen} onOpenChange={setRejectDialogOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Reject this submission</AlertDialogTitle>
              <AlertDialogDescription>
                Provide a reason. This will mark the request as rejected and send an
                update message to the contact via Respond.io. This action cannot be
                undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-2 py-2">
              <Label htmlFor="reject-reason">Reason</Label>
              <Textarea
                id="reject-reason"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Why is this submission being rejected?"
                rows={4}
              />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={rejecting}>Cancel</AlertDialogCancel>
              <AlertDialogAction
                data-guide-target="procurement.approvals.reject-confirm-button"
                disabled={rejecting || !rejectReason.trim()}
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                onClick={async (e) => {
                  e.preventDefault();
                  if (!rejectReason.trim()) {
                    toast.error('Reason is required');
                    return;
                  }
                  setRejecting(true);
                  try {
                    const rejectResult = await rejectSubmittedPurchaseRequest(
                      requestId,
                      rejectReason.trim(),
                    );
                    queryClient.invalidateQueries({
                      queryKey: ['purchase-request', requestId],
                    });
                    if (isDeferredDecision(rejectResult)) {
                      // Nothing sent yet, so claiming the contact was notified is false.
                      formAction.refresh();
                      toast.success('Rejecting - you can still undo.');
                    } else {
                      toast.success('Submission rejected; contact has been notified.');
                    }
                    setRejectDialogOpen(false);
                  } catch (err) {
                    toast.error(
                      err instanceof Error ? err.message : 'Failed to reject',
                    );
                  } finally {
                    setRejecting(false);
                  }
                }}
              >
                {rejecting ? 'Rejecting…' : 'Confirm reject'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {/* In-system approver rejection (pending approval) - same as the email link. */}
        <AlertDialog open={decisionRejectOpen} onOpenChange={setDecisionRejectOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Reject this request</AlertDialogTitle>
              <AlertDialogDescription>
                Provide a reason. This records the rejection and notifies the
                requester and contact - the same as rejecting via the approval link.
                This action cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-2 py-2">
              <Label htmlFor="decision-reject-reason">Reason</Label>
              <Textarea
                id="decision-reject-reason"
                value={decisionRejectReason}
                onChange={(e) => setDecisionRejectReason(e.target.value)}
                placeholder="Why is this request being rejected?"
                rows={4}
              />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={decisionRejecting}>Cancel</AlertDialogCancel>
              <AlertDialogAction
                disabled={decisionRejecting || !decisionRejectReason.trim()}
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                onClick={async (e) => {
                  e.preventDefault();
                  if (!decisionRejectReason.trim()) {
                    toast.error('Reason is required');
                    return;
                  }
                  setDecisionRejecting(true);
                  try {
                    const result = await submitApprovalDecision(
                      requestId,
                      'rejected',
                      decisionRejectReason.trim(),
                    );
                    queryClient.invalidateQueries({ queryKey: ['purchase-request', requestId] });
                    queryClient.invalidateQueries({ queryKey: ['form-sla-trackers', requestTypeForNav, requestId] });
                    if (isDeferredDecision(result)) {
                      // Nobody has been notified yet - saying they have would be wrong.
                      formAction.refresh();
                      toast.success('Rejecting - you can still undo.');
                    } else {
                      toast.success('Request rejected; requester and contact notified.');
                    }
                    setDecisionRejectOpen(false);
                  } catch (err) {
                    toast.error(err instanceof Error ? err.message : 'Failed to reject');
                  } finally {
                    setDecisionRejecting(false);
                  }
                }}
              >
                {decisionRejecting ? 'Rejecting…' : 'Confirm reject'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {/* Escalate the active form-SLA stage to the next tier. */}
        <AlertDialog open={escalateOpen} onOpenChange={setEscalateOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Escalate SLA</AlertDialogTitle>
              <AlertDialogDescription>
                Force-escalate the current SLA stage to the next tier and reassign per
                the escalation policy. Optionally add a reason.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-2 py-2">
              <Label htmlFor="escalate-reason">Reason*</Label>
              <Textarea
                id="escalate-reason"
                value={escalateReason}
                onChange={(e) => setEscalateReason(e.target.value)}
                placeholder="Why escalate now?"
                rows={3}
              />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={escalating}>Cancel</AlertDialogCancel>
              <AlertDialogAction
                disabled={escalating || !activeTracker}
                onClick={async (e) => {
                  e.preventDefault();
                  if (!activeTracker) return;
                  setEscalating(true);
                  try {
                    const res = await escalateFormTracking(activeTracker.id, escalateReason.trim());
                    queryClient.invalidateQueries({ queryKey: ['form-sla-trackers', requestTypeForNav, requestId] });
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
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        <SlaExtendDialog
          activeTracker={activeTracker}
          label={`${typeLabel}${request.request_number ? ` · ${request.request_number}` : ''}`}
          open={extendOpen}
          onOpenChange={setExtendOpen}
          onExtended={() =>
            void queryClient.invalidateQueries({
              queryKey: ['form-sla-trackers', requestTypeForNav, requestId],
            })
          }
        />

        <ReassignDialog
          open={reassignOpen}
          onOpenChange={setReassignOpen}
          taskLabel={`${typeLabel}${request.request_number ? ` · ${request.request_number}` : ''}`}
          submitting={reassignMutation.isPending}
          onConfirm={(userId) => {
            if (!activeTracker) return;
            reassignMutation.mutate(
              { id: activeTracker.id, userId },
              {
                onSuccess: () => {
                  queryClient.invalidateQueries({
                    queryKey: ['form-sla-trackers', requestTypeForNav, requestId],
                  });
                  handlingLock.refresh(); // lock banner keys on a separate query - refetch so it appears without reload
                  setReassignOpen(false);
                },
              },
            );
          }}
        />

        <AlertDialog open={processDialogOpen} onOpenChange={setProcessDialogOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Mark as processed by CS?</AlertDialogTitle>
              <AlertDialogDescription>
                This closes the customer-service stage and sets the status to{' '}
                <span className="font-medium">processed by CS</span>. A status update
                is sent to the contact. This action cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-1 py-2">
              <Label htmlFor="process-note">Message to contact (optional)</Label>
              <Textarea
                id="process-note"
                value={finalizeNote}
                onChange={(e) => setFinalizeNote(e.target.value)}
                placeholder="Add an optional note for the customer…"
                rows={3}
              />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={finalizing}>Cancel</AlertDialogCancel>
              <AlertDialogAction
                disabled={finalizing}
                className="bg-emerald-600 text-white hover:bg-emerald-700"
                onClick={async (e) => {
                  e.preventDefault();
                  setFinalizing(true);
                  try {
                    const processResult = await processPurchaseRequestByCs(requestId, finalizeNote);
                    queryClient.invalidateQueries({ queryKey: ['purchase-request', requestId] });
                    if (isDeferredDecision(processResult)) {
                      formAction.refresh();
                      toast.success('Processing - you can still undo.');
                    } else {
                      toast.success('Marked as processed by CS.');
                    }
                    setProcessDialogOpen(false);
                  } catch (err) {
                    toast.error(err instanceof Error ? err.message : 'Failed to mark processed by CS');
                  } finally {
                    setFinalizing(false);
                  }
                }}
              >
                {finalizing ? 'Saving…' : 'Processed by CS'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        <AlertDialog open={closeCsDialogOpen} onOpenChange={setCloseCsDialogOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Mark as closed?</AlertDialogTitle>
              <AlertDialogDescription>
                Closes the customer-service stage and sets the status to{' '}
                <span className="font-medium">closed</span> (could not be fulfilled).
                A status update is sent to the contact. This action cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-1 py-2">
              <Label htmlFor="close-note">Message to contact (optional)</Label>
              <Textarea
                id="close-note"
                value={finalizeNote}
                onChange={(e) => setFinalizeNote(e.target.value)}
                placeholder="Add an optional note for the customer…"
                rows={3}
              />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={finalizing}>Cancel</AlertDialogCancel>
              <AlertDialogAction
                disabled={finalizing}
                onClick={async (e) => {
                  e.preventDefault();
                  setFinalizing(true);
                  try {
                    const closeResult = await closePurchaseRequestByCs(requestId, finalizeNote);
                    queryClient.invalidateQueries({ queryKey: ['purchase-request', requestId] });
                    if (isDeferredDecision(closeResult)) {
                      formAction.refresh();
                      toast.success('Closing - you can still undo.');
                    } else {
                      toast.success('Marked as closed.');
                    }
                    setCloseCsDialogOpen(false);
                  } catch (err) {
                    toast.error(err instanceof Error ? err.message : 'Failed to close request');
                  } finally {
                    setFinalizing(false);
                  }
                }}
              >
                {finalizing ? 'Saving…' : 'Confirm close'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </div>
  );
}
