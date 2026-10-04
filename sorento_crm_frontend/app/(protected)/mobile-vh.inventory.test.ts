/**
 * M6-02 / M6-03 - the fixed-viewport-unit sweep.
 *
 * `100vh` (and its Tailwind shorthands `h-screen` / `min-h-screen`) is taller
 * than the visible viewport on mobile Safari, whose address bar and toolbar
 * come and go: a surface sized off it has its bottom edge sitting under
 * chrome the reader cannot scroll past. `dvh` (dynamic viewport height)
 * tracks the ACTUAL visible area instead.
 *
 * Three named sites were the original M6 targets - `notifications-sheet.tsx`,
 * `AIAssistantBubble.tsx`, `ConversationsInbox.tsx` - and a fix round added
 * three more that are shipped shell / phone-facing surfaces: `demo1`'s
 * `sidebar-menu.tsx` (the ACTIVE shell, not the unused demo1-10 variants),
 * `PortalVerifyCard.tsx` and `SubmissionForm.tsx` (both portal, both on a
 * phone in the reader's hand). All six are converted.
 *
 * The pattern was also widened this round from the literal `100vh` to any
 * `<digits>vh` (`max-h-[80vh]`, `max-h-[85vh]`, ...), which is the same defect
 * at a fraction of the viewport instead of the whole thing. That widened net
 * catches 128 more files (185 lines) with no existing entry here - each is a
 * mechanical swap on its own, but 128 of them is not a "keep the sweep
 * converged" fix round, so they land under ONE shared reason as follow-up
 * #567 rather than blocking this one. The original 2 Sep audit's 21 remaining
 * entries (after the three conversions above) keep their own per-file reason.
 *
 * Two of those 128 left the list on 5 Sep 2026 - `CellStockTable.tsx` and
 * `StockDocumentsPanel.tsx`. Not as part of #567's mechanical `vh` -> `dvh`
 * swap: their `max-h-[50vh]` / `max-h-[35vh]` were two of four nested scroll
 * regions on the cell breakdown dialog's Stock tab, and they were deleted
 * outright rather than converted. `cell-breakdown-scroll.inventory.test.ts`
 * is what keeps them gone, so the baseline below drops by 2 files / 3 lines
 * (one of the three was a comment naming the other file's 50vh).
 *
 * Purchasing consolidation batch (lane C, 6 Sep 2026) added one more new site,
 * same reason as the 128 - the supplier-documents upload dialog's body is
 * `max-h-[65vh]`, same convention its sibling `SpoScheduleMatrixTable.tsx`
 * already carries. Baseline below is +1 file / +1 line.
 *
 * PLAN-scm-supplier-documents-pi-first.md (S3, 10 Sep 2026) MOVED that dialog rather
 * than adding a site: `packing-lists/components/PackingListUploadDialog.tsx` became
 * `scm/proforma-invoices/components/SupplierDocumentsUploadDialog.tsx` when Proforma
 * Invoices became its only home. Same one line, same reason, new path - the baseline
 * is unchanged by it.
 *
 * PO schedule redesign fix round 3 (owner hand test 25 Sep 2026, item 4) dropped two sites
 * outright rather than converting them. `POIntakeAnnotationsGrid.tsx`'s own `max-h-[calc(100
 * vh-14rem)]` and its two `max-h-[85vh]` dialogs (3 lines) are gone with the component they
 * were on - the Documents-tab annotations grid the owner asked removed. `POIntakeDocument
 * Viewer.tsx`'s three `h-[45vh]` (3 lines) were the short fixed-height strip the same item
 * replaced with the tab's own full height, on `dvh` this time rather than re-adding `vh`
 * (M6-02/M6-03) - see that file's own comment. Baseline below is -2 files / -6 lines.
 *
 * PLAN-project-form-28sep.md (#1339) deleted `pipeline/components/RegisterProjectDialog.tsx`
 * outright - the dialog it fixed-height-scrolled is gone, moved to a page (`ProjectForm.tsx`,
 * no dialog, no `vh`), not converted. Baseline below is -1 file / -4 lines.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const PATTERN = /\d+vh|(?<![\w-])h-screen(?![\w-])|min-h-screen/;

const FRACTIONAL_VH_FOLLOWUP = 'fractional vh, follow-up #567';

/** The widened-pattern sweep (this round): every file it newly caught, none
 *  of which had an entry here before. One shared reason - see the header. */
const FRACTIONAL_VH_FILES = [
  'app/(auth)/portal/components/AIExtractDialog.tsx',
  'app/(auth)/portal/components/AttachmentDropzone.tsx',
  'app/(auth)/portal/components/PortalLanding.tsx',
  'app/(auth)/portal/ticket-draft/[token]/page.tsx',
  'app/(protected)/complaint-management/_shared/LinkedComplaintsChip.tsx',
  'app/(protected)/complaint-management/complaints/components/ComplaintConversationPanel.tsx',
  'app/(protected)/dealer-kit/brochure-images/components/BrochureImageDialog.tsx',
  'app/(protected)/dealer-kit/flyer-readings/components/DimensionReviewSection.tsx',
  'app/(protected)/integration-management/integrations/components/IntegrationFormDialog.tsx',
  'app/(protected)/integration-management/integrations/components/IssuedKeyDialog.tsx',
  'app/(protected)/inventory-management/stock-transfers/components/StockTransferActions.tsx',
  'app/(protected)/marketing-management/promotion-types/components/PromotionTypeFormModal.tsx',
  'app/(protected)/marketing-management/promotions/components/PromotionAttachmentsTab.tsx',
  'app/(protected)/master-data-management/certificates/components/CertificateFormDialog.tsx',
  'app/(protected)/master-data-management/flyer-spec-proposals/components/AddProposalRowDialog.tsx',
  'app/(protected)/master-data-management/flyer-spec-proposals/components/FlyerSpecReviewScreen.tsx',
  'app/(protected)/master-data-management/products/components/LinkAttachmentBrowserDialog.tsx',
  'app/(protected)/master-data-management/products/components/ProductAttachmentsTab.tsx',
  'app/(protected)/scm/proforma-invoices/components/SupplierDocumentsUploadDialog.tsx',
  'app/(protected)/procurement-management/packing-lists/components/SpoScheduleMatrixTable.tsx',
  'app/(protected)/procurement-management/purchase-requests/components/PurchaseRequestConversationPanel.tsx',
  'app/(protected)/procurement-management/stock-inquiries/components/StockInquiryConversationPanel.tsx',
  'app/(protected)/project-sales/[projectId]/components/AmendmentCreateDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/DeliveryScheduleUploadDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/DismissReasonDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/POIntakeAnnotationEditDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/POIntakeExtractionStatus.tsx',
  'app/(protected)/project-sales/[projectId]/components/POIntakeUploadDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/ProjectAccessPanel.tsx',
  'app/(protected)/project-sales/[projectId]/components/ProjectDocumentsPanel.tsx',
  'app/(protected)/project-sales/[projectId]/components/PurchaseOrderDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/QuotationDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/QuotationOutcomeDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/SalesOrderBuildDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/SalesOrderPublishDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/SalesOrderRegroupDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/SampleDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/StakeholdersPanel.tsx',
  'app/(protected)/project-sales/[projectId]/components/TaskFormDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/TaskHistoryDialog.tsx',
  'app/(protected)/project-sales/[projectId]/components/TaskStatusDialog.tsx',
  'app/(protected)/project-sales/[projectId]/delivery-schedules/components/DeliveryScheduleByDateMatrix.tsx',
  'app/(protected)/project-sales/[projectId]/delivery-schedules/components/DeliveryScheduleConfirmDialog.tsx',
  'app/(protected)/project-sales/[projectId]/delivery-schedules/components/DeliveryScheduleMatrix.tsx',
  'app/(protected)/project-sales/[projectId]/quotation-documents/[documentId]/components/QuotationApprovalPanel.tsx',
  'app/(protected)/project-sales/_shared/components/LinkDocumentDialog.tsx',
  'app/(protected)/project-sales/_shared/components/PriceFloorDialog.tsx',
  'app/(protected)/project-sales/divergences/components/DivergenceRowDialog.tsx',
  'app/(protected)/project-sales/fulfilment-planning/components/BoardCellBreakdownDialog.tsx',
  'app/(protected)/project-sales/fulfilment-planning/components/BoardRankPopover.tsx',
  'app/(protected)/project-sales/fulfilment-planning/components/BoardTrailPopover.tsx',
  'app/(protected)/project-sales/fulfilment-planning/components/BorrowAddDialog.tsx',
  'app/(protected)/project-sales/fulfilment-planning/components/ClassificationProofPopover.tsx',
  'app/(protected)/project-sales/fulfilment-planning/components/FulfilmentBoardMatrix.tsx',
  'app/(protected)/project-sales/fulfilment-planning/components/PileQueueDialog.tsx',
  'app/(protected)/project-sales/fulfilment-planning/components/ReserveAddDialog.tsx',
  'app/(protected)/project-sales/lead-acceptance/components/NudgeAssigneeDialog.tsx',
  'app/(protected)/project-sales/leads/[leadId]/components/DisqualifyLeadDialog.tsx',
  'app/(protected)/project-sales/leads/[leadId]/components/EditLeadInformantDialog.tsx',
  'app/(protected)/project-sales/leads/[leadId]/components/QualifyLeadDialog.tsx',
  'app/(protected)/project-sales/leads/components/AssignLeadDialog.tsx',
  'app/(protected)/project-sales/leads/components/DeclineLeadDialog.tsx',
  'app/(protected)/project-sales/leads/components/LeadWizardDialog.tsx',
  'app/(protected)/project-sales/order-inquiries/components/OrderInquiryDocumentDialog.tsx',
  'app/(protected)/project-sales/order-inquiries/components/OrderInquiryMatrixCellDrilldown.tsx',
  'app/(protected)/project-sales/order-inquiries/components/OrderInquiryScheduleMatrix.tsx',
  'app/(protected)/project-sales/parties/components/PartyFormDialog.tsx',
  'app/(protected)/project-sales/setup/components/ProjectTemplateDialog.tsx',
  'app/(protected)/project-sales/setup/components/ProjectTypeDialog.tsx',
  'app/(protected)/project-sales/setup/components/TemplateChecklistPanel.tsx',
  'app/(protected)/project-sales/stock-debt/components/StockDebtCellDialog.tsx',
  'app/(protected)/resource-management/attachment-directories/components/BulkAttachmentAccessLevelsDialog.tsx',
  'app/(protected)/resource-management/attachment-directories/components/MoveToDialog.tsx',
  'app/(protected)/resource-management/attachments/components/AttachmentDetailModal.tsx',
  'app/(protected)/resource-management/attachments/components/AttachmentUploadDialog.tsx',
  'app/(protected)/resource-management/attachments/components/FilePreviewModal.tsx',
  'app/(protected)/resource-management/attachments/components/ManageFieldLinksDialog.tsx',
  'app/(protected)/scm/components/PlanRowDialog.tsx',
  'app/(protected)/scm/loading-plan/components/ContainerRequestRowDialog.tsx',
  'app/(protected)/scm/loading-plan/components/ContainerRequestScheduleMatrix.tsx',
  'app/(protected)/scm/loading-plan/components/PlanContainerDialog.tsx',
  'app/(protected)/scm/loading-plan/components/SendRequestDialog.tsx',
  'app/(protected)/scm/proforma-invoices/components/ProformaUploadDialog.tsx',
  'app/(protected)/scm/reorder/components/OrderInquiryUploadDialog.tsx',
  'app/(protected)/scm/reorder/components/OutstandingUploadDialog.tsx',
  'app/(protected)/scm/reorder/components/PlanOrderQtyLedger.tsx',
  'app/(protected)/scm/reorder/components/PlanRowDialogs.tsx',
  'app/(protected)/scm/reorder/components/PlanTrendPopover.tsx',
  'app/(protected)/sla-management/_shared/FormSkipAction.tsx',
  'app/(protected)/sla-management/conversation-sla-tracking/components/ConversationSLATrackingDetail.tsx',
  'app/(protected)/sla-management/conversation-sla-tracking/components/SlaTrackingChatRecords.tsx',
  'app/(protected)/sla-management/conversation-sla-tracking/components/TicketConversationPanel.tsx',
  'app/(protected)/sla-management/conversations/components/ConversationThreadPane.tsx',
  'app/(protected)/sla-management/message-snippets/components/MessageSnippetFormDialog.tsx',
  'app/(protected)/system-management/app-store/components/ModuleBundlesAdmin.tsx',
  'app/(protected)/system-management/automation/components/AutomationForm.tsx',
  'app/(protected)/system-management/email-outbox/components/EmailOutboxList.tsx',
  'app/(protected)/system-management/outgoing-mails/components/OutgoingMailsList.tsx',
  'app/(protected)/system-management/status-graphs/components/StatusDeleteDialog.tsx',
  'app/(protected)/system-management/status-graphs/components/StatusFormDialog.tsx',
  'app/(protected)/system-management/status-graphs/components/TransitionFormDialog.tsx',
  'app/(protected)/user-management/access-agents/components/AccessAgentFormModal.tsx',
  'app/(protected)/user-management/access-agents/components/ContactFieldAccessDialog.tsx',
  'app/(protected)/user-management/contact-access-types/components/ContactAccessTypesAdmin.tsx',
  'app/(protected)/user-management/contacts/[id]/components/ContactAttachmentTypesSection.tsx',
  'app/(protected)/user-management/contacts/[id]/components/ContactMediaAccessSection.tsx',
  'app/(protected)/user-management/onboarding-requests/[id]/components/OnboardingRequestDetail.tsx',
  'app/(protected)/user-management/onboarding-requests/components/NewOnboardingRequestDialog.tsx',
  'app/(protected)/user-management/roles/components/role-edit-dialog.tsx',
  'app/(protected)/user-management/users/[id]/components/user-profile-edit-dialog.tsx',
  'app/components/common/AccessDenied.tsx',
  'app/components/layouts/demo1/components/quick-access-block.tsx',
  'components/common/AttachmentPreviewModal.tsx',
  'components/common/BulkUpdateDialog/BulkUpdateDialog.tsx',
  'components/common/LinkAttachmentBrowserDialog.tsx',
  'components/common/PillOverflow.tsx',
  'components/common/RespondChatList.tsx',
  'components/common/RevisionSnapshotDialog.tsx',
  'components/common/TruncatedTextCell.tsx',
  'components/list/ListQueryExportDialog.tsx',
  'components/list/ListQueryFilterDialog.tsx',
  'components/list/OrderFilterFieldSelect.tsx',
  'components/my-downloads/EntityDownloadsButton.tsx',
  'components/template/TemplateUploadDialog.tsx',
  'components/ui/data-grid-column-visibility.tsx',
  'components/ui/data-grid-list-toolbar.tsx',
  'components/ui/drawer.tsx',
];

/** file -> why it is still on `vh`/`-screen`, not yet converted. */
const ALLOWLIST = new Map<string, string>([
  ['app/unsubscribe/daily-sla-summary/page.tsx', 'M6 follow-up: unauthenticated one-off page'],
  ['app/components/layouts/demo10/components/sidebar-menu.tsx', 'M6 follow-up: unused demo shell'],
  ['app/components/layouts/demo3/components/sidebar.tsx', 'M6 follow-up: unused demo shell'],
  ['app/components/layouts/demo4/components/sidebar-secondary.tsx', 'M6 follow-up: unused demo shell'],
  ['app/components/layouts/demo6/components/sidebar-menu.tsx', 'M6 follow-up: unused demo shell'],
  ['app/components/layouts/demo8/components/sidebar-menu.tsx', 'M6 follow-up: unused demo shell'],
  [
    'app/(protected)/forms-management/forms/[id]/builder/components/FormBuilder.tsx',
    'M6 follow-up: desktop-only builder canvas',
  ],
  [
    'app/(protected)/project-sales/[projectId]/components/POIntakeLinesGrid.tsx',
    'M6 follow-up: desktop-only intake grid',
  ],
  [
    'app/(protected)/resource-management/attachment-directories/page.tsx',
    'M6 follow-up: desktop-only file browser',
  ],
  [
    'app/(protected)/store-admin/components/create-shipping-label-sheet/sheet.tsx',
    'M6 follow-up: internal admin sheet',
  ],
  ['app/(public)/c/[company]/[slug]/page.tsx', 'M6 follow-up: public catalogue landing'],
  ['app/(auth)/view/stock-inquiry/page.tsx', 'M6 follow-up: emailed read-only view'],
  ['app/(auth)/view/complaint/page.tsx', 'M6 follow-up: emailed read-only view'],
  ['app/(auth)/view/request/page.tsx', 'M6 follow-up: emailed read-only view'],
  ['app/(auth)/approval/page.tsx', 'M6 follow-up: emailed approval screen'],
  ['components/my-downloads/MyDownloadsDrawer.tsx', 'M6 follow-up: desktop-oriented drawer'],
  ['components/upload-activity/UploadActivityDrawer.tsx', 'M6 follow-up: desktop-oriented drawer'],
  ...FRACTIONAL_VH_FILES.map((file): [string, string] => [file, FRACTIONAL_VH_FOLLOWUP]),
]);

const CONVERTED_M6_SITES = [
  'app/components/partials/topbar/notifications-sheet.tsx',
  'app/components/common/AIAssistantBubble.tsx',
  'app/(protected)/sla-management/conversations/components/ConversationsInbox.tsx',
  'app/components/layouts/demo1/components/sidebar-menu.tsx',
  'app/(auth)/portal/components/PortalVerifyCard.tsx',
  'app/(auth)/portal/components/SubmissionForm.tsx',
];

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || entry.name === '.next') continue;
        walk(full);
      } else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.includes('.test.')) {
        out.push(full);
      }
    }
  };
  walk('app');
  walk('components');
  return out;
}

describe('fixed viewport-height sweep (M6-02 / M6-03)', () => {
  it('every fractional-or-whole vh / h-screen / min-h-screen site is either converted or allowlisted with a reason', () => {
    const offenders: string[] = [];
    for (const file of sourceFiles()) {
      if (ALLOWLIST.has(file)) continue;
      const src = fs.readFileSync(file, 'utf8');
      if (PATTERN.test(src)) offenders.push(file);
    }
    expect(offenders).toEqual([]);
  });

  it('the six named M6 sites use dvh, not vh', () => {
    for (const file of CONVERTED_M6_SITES) {
      const src = fs.readFileSync(file, 'utf8');
      expect(src, file).not.toMatch(PATTERN);
      expect(src, file).toMatch(/dvh/);
    }
  });

  it('the allowlist matches its baseline (209 lines, 144 files)', () => {
    let matchingLines = 0;
    for (const file of ALLOWLIST.keys()) {
      const lines = fs.readFileSync(file, 'utf8').split('\n');
      matchingLines += lines.filter((line) => PATTERN.test(line)).length;
    }
    expect(ALLOWLIST.size).toBe(144);
    expect(matchingLines).toBe(209);
  });
});
