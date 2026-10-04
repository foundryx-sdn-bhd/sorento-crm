// eslint.config.mjs
import { FlatCompat } from '@eslint/eslintrc';

// Create a FlatCompat instance to support legacy "extends" syntax.
const compat = new FlatCompat({
  baseDirectory: import.meta.dirname,
});

// Apple Alignment S9-01 guardrail: bans arbitrary `text-[Npx]` font-size utilities in
// className strings. The type scale in `css/config.reui.css` (S2-03) already covers
// every step a design needs (2xs/xs/sm/base/lg/xl/2xl with tracking+leading baked in),
// so a literal px size is always a step someone skipped rather than a gap in the scale.
// A tiny inline rule is the simplest thing that works here - no published package
// exists for this one project-specific string shape, and the alternative
// (`no-restricted-syntax` with a regex selector) can't be scoped to `text-[`+digits+`px]`
// without also matching unrelated bracket classes, so a rule gets its own AST walk of
// string/template literals instead.
const PX_TEXT_RE = /text-\[\d+(?:\.\d+)?px\]/g;
const noPxTextClassRule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow arbitrary text-[Npx] utility classes; use the type scale from css/config.reui.css instead.',
    },
    schema: [],
    messages: {
      noPxText:
        'Do not use "{{match}}" in className. Use the type scale (text-2xs/xs/sm/base/lg/xl/2xl) from css/config.reui.css - see documentation/plans/design-system/apple-alignment-acceptance-criteria.md S9-01.',
    },
  },
  create(context) {
    function check(node, raw) {
      if (typeof raw !== 'string') return;
      for (const match of raw.matchAll(PX_TEXT_RE)) {
        context.report({ node, messageId: 'noPxText', data: { match: match[0] } });
      }
    }
    return {
      Literal(node) {
        check(node, node.value);
      },
      TemplateElement(node) {
        check(node, node.value.raw);
      },
    };
  },
};

const eslintConfig = [
  ...compat.config({
    extends: ['next/core-web-vitals', 'next/typescript', 'prettier'],
    // Plugins in legacy format must be an array of plugin names.
    plugins: ['react-hooks'],
    rules: {
      // Disable react-in-jsx-scope (not needed in React 17+)
      'react/react-in-jsx-scope': 'off',
      'react/no-unescaped-entities': 'off',
      // React Hooks rules
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      '@next/next/no-img-element': 'off',
      // Apple Alignment S9-01 guardrail: the a11y sweep (S9-02) fixed the sites the
      // audit found; these stay 'warn' so a future click handler on a div or an icon
      // button that loses its label is a code-review catch, not a silent regression.
      // `jsx-a11y` is already a dependency of `next/core-web-vitals` (registered as
      // the `jsx-a11y` plugin below), so no new package is needed.
      'jsx-a11y/click-events-have-key-events': 'warn',
      'jsx-a11y/no-static-element-interactions': 'warn',
      'jsx-a11y/control-has-associated-label': 'warn',
      // Apple alignment preview Finding 1's root cause, generalised: a component
      // declared INSIDE another component's render body gets a new identity on
      // every parent re-render, so React remounts it - which is exactly how the
      // Users list search box lost focus on every keystroke (the toolbar was
      // nested, and the search box's own state lived in the parent). 'warn' so
      // the ~pre-existing offenders this run found don't fail CI outright,
      // matching the jsx-a11y trio above. `allowAsProps` because a render-prop
      // (a DataGrid cell renderer built inline, say) is a legitimate pattern
      // this rule would otherwise also catch.
      'react/no-unstable-nested-components': ['warn', { allowAsProps: true }],
      // Searchable Dropdown Standard (PLAN-searchable-dropdown-standard). Doctrine:
      // every dropdown-select must be searchable and use the standard component
      // (@/components/common/SearchableSelect | SearchableMultiSelect). 'error' so any
      // violation fails CI. The migration is COMPLETE — the whole codebase is on the
      // standard, the burn-down allowlist has been deleted, and ui/select.tsx is gone.
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@/components/ui/select',
              message:
                'Radix Select is not searchable. Use SearchableSelect / SearchableMultiSelect from @/components/common. See documentation/reference/ADR-PRODUCT-STANDARDS.md.',
            },
            {
              name: '@/components/ui/command',
              importNames: ['CommandInput'],
              message:
                'Do not hand-roll a searchable dropdown with CommandInput. Use SearchableSelect / SearchableMultiSelect from @/components/common. (Genuine Cmd+K command palettes are exempt in eslint.config.mjs.)',
            },
          ],
        },
      ],
      // Architecture guards (PLAN-fix-security-cluster Sub-plan E). 'warn' so the
      // ~283 pre-existing sites don't fail `eslint .` (no --max-warnings), while
      // any NEW violation is surfaced in editor/PR/CI. Fix opportunistically.
      'no-restricted-syntax': [
        'warn',
        {
          // Hand-rolled error parsing: response.json().catch(() => ({}))
          selector:
            "CallExpression[callee.property.name='catch'][callee.object.callee.property.name='json']",
          message:
            'Use extractApiError(response, fallback) from @/lib/api-client instead of hand-rolling response.json().catch().',
        },
        {
          // Native confirm() — not allowed per ADR (use a dialog).
          selector: "CallExpression[callee.name='confirm']",
          message:
            'Do not use native confirm(). Use ConfirmDeleteDialog / AlertDialog from @/components/ui.',
        },
        {
          selector:
            "CallExpression[callee.object.name='window'][callee.property.name='confirm']",
          message:
            'Do not use native window.confirm(). Use ConfirmDeleteDialog / AlertDialog from @/components/ui.',
        },
        {
          // Raw native <select> — not searchable. (warn: only advisory; the real gate
          // is the no-restricted-imports ban on @/components/ui/select above.)
          selector: "JSXOpeningElement[name.name='select']",
          message:
            'Native <select> is not searchable. Use SearchableSelect from @/components/common.',
        },
        // NOTE: no rule for `new URLSearchParams` — it has legit non-DataGrid uses
        // (simple query strings) that a blanket AST selector can't distinguish from
        // the buildDataGridParams cases, so it stays a manual/review cleanup (E).
      ],
    },
  }),
  {
    // Permanent exemptions from the dropdown ban:
    // - the standard components themselves (they legitimately wrap Command/CommandInput)
    // - genuine Cmd+K command palettes (a Command menu, NOT a dropdown-select)
    files: [
      'components/common/**',
      'components/ui/**',
      'app/components/partials/dialogs/search/search-dialog.tsx',
    ],
    rules: { 'no-restricted-imports': 'off' },
  },
  {
    // Apple Alignment S9-01: text-[Npx] is an error everywhere except the vendor
    // Metronic demo shell (`layouts/demo2` through `demo10`) and the unused
    // starter-kit `partials/` tree (S5-01 already exempted the same paths from
    // the PageHeader sweep for the same reason - no page of ours renders them).
    // `demo1` is NOT in this ignore: it is `Demo1Layout`, the one
    // `app/(protected)/layout.tsx` actually mounts (see the a11y guardrail
    // inventory's own note on this), so its offending files go in the dated
    // debt list below instead of being exempted outright.
    files: ['**/*.{ts,tsx}'],
    ignores: [
      'app/components/layouts/demo2/**',
      'app/components/layouts/demo3/**',
      'app/components/layouts/demo4/**',
      'app/components/layouts/demo5/**',
      'app/components/layouts/demo6/**',
      'app/components/layouts/demo7/**',
      'app/components/layouts/demo8/**',
      'app/components/layouts/demo9/**',
      'app/components/layouts/demo10/**',
      'app/components/partials/**',
      // Test fixtures that deliberately contain the banned string as an example
      // of what the rule catches (S9-01's own "the rule fires" proof).
      'eslint.config.text-px-rule.test.ts',
    ],
    plugins: {
      local: { rules: { 'no-px-text-class': noPxTextClassRule } },
    },
    rules: { 'local/no-px-text-class': 'error' },
  },
  {
    // Pre-existing text-[Npx] usage that predates this guardrail (measured 31 Aug
    // 2026 against the tree, 82 files - the audit that seeded S9-01 counted 74
    // before the tree moved under it): dense data-grid and matrix typography,
    // mostly in project-sales, that a designed type-scale step doesn't cleanly
    // replace (11px/13px rows between text-2xs and text-xs). Rewriting 82 files'
    // typography is a remediation project, not a guardrail - S9's job is to stop
    // the count growing, not to burn it down in the same PR. Fix opportunistically;
    // a file leaves this list the day it stops needing an arbitrary px size.
    files: [
      'app/(auth)/portal/components/BookmarkHint.tsx',
      'app/(protected)/components/demo1/light-sidebar/components/earnings-chart.tsx',
      'app/(protected)/dealer-kit/components/BlockPreview.tsx',
      'app/(protected)/dealer-kit/components/BundleCard.tsx',
      'app/(protected)/dealer-kit/components/TileGrid.tsx',
      'app/(protected)/master-data-management/products/components/ProductAttachmentsTab.tsx',
      'app/(protected)/procurement-management/packing-lists/components/SpoScheduleMatrixTable.tsx',
      'app/(protected)/project-sales/*/components/AmendmentDeltaTable.tsx',
      'app/(protected)/project-sales/*/components/DeliverySchedulesPanel.tsx',
      'app/(protected)/project-sales/*/components/POIntakeAnnotationsGrid.tsx',
      'app/(protected)/project-sales/*/components/POIntakeLinesGrid.tsx',
      'app/(protected)/project-sales/*/components/POIntakeVersionsStrip.tsx',
      'app/(protected)/project-sales/*/components/ProjectActivityPanel.tsx',
      'app/(protected)/project-sales/*/components/PurchaseOrderLinesEditor.tsx',
      'app/(protected)/project-sales/*/components/PurchaseOrdersPanel.tsx',
      'app/(protected)/project-sales/*/components/QuotationLinePhoto.tsx',
      'app/(protected)/project-sales/*/components/QuotationVersionEditor.tsx',
      'app/(protected)/project-sales/*/components/QuotationsPanel.tsx',
      'app/(protected)/project-sales/*/components/SamplesPanel.tsx',
      'app/(protected)/project-sales/*/components/StakeholdersPanel.tsx',
      'app/(protected)/project-sales/*/components/TaskTimelineView.tsx',
      'app/(protected)/project-sales/*/components/TasksPanel.tsx',
      'app/(protected)/project-sales/*/delivery-schedules/components/DeliveryScheduleByDateMatrix.tsx',
      'app/(protected)/project-sales/*/delivery-schedules/components/DeliveryScheduleColumnCards.tsx',
      'app/(protected)/project-sales/*/delivery-schedules/components/DeliveryScheduleMatrix.tsx',
      'app/(protected)/project-sales/*/delivery-schedules/components/DeliveryScheduleProductPicker.tsx',
      'app/(protected)/project-sales/*/delivery-schedules/components/DeliveryScheduleRevisionDiff.tsx',
      'app/(protected)/project-sales/*/quotation-documents/*/components/QuotationSignatureBlock.tsx',
      'app/(protected)/project-sales/_shared/components/InlineLineTable.tsx',
      'app/(protected)/project-sales/_shared/components/LinkDocumentDialog.tsx',
      'app/(protected)/project-sales/fulfilment-planning/components/BoardChangeTable.tsx',
      'app/(protected)/project-sales/fulfilment-planning/components/BorrowAddDialog.tsx',
      'app/(protected)/project-sales/fulfilment-planning/components/DecisionStrip.tsx',
      'app/(protected)/project-sales/fulfilment-planning/components/FulfilmentBoardMatrix.tsx',
      'app/(protected)/project-sales/fulfilment-planning/components/StockDocumentsPanel.tsx',
      'app/(protected)/project-sales/leads/components/LeadWizardDialog.tsx',
      'app/(protected)/project-sales/my-tasks/components/MyTasksClient.tsx',
      'app/(protected)/project-sales/order-inquiries/components/OrderInquiryScheduleMatrix.tsx',
      'app/(protected)/project-sales/order-inquiries/components/orderInquiryWorklistColumns.tsx',
      'app/(protected)/project-sales/pipeline/components/ClashWarningPanel.tsx',
      'app/(protected)/project-sales/pipeline/components/PipelineBoard.tsx',
      'app/(protected)/project-sales/pipeline/components/PipelineClient.tsx',
      'app/(protected)/project-sales/reports/components/ForecastClient.tsx',
      'app/(protected)/project-sales/setup/components/TemplateChecklistPanel.tsx',
      'app/(protected)/resource-management/attachment-directories/components/AccessLevelsCell.tsx',
      'app/(protected)/scm/loading-plan/components/ContainerRequestHistory.tsx',
      'app/(protected)/scm/loading-plan/components/ContainerRequestScheduleMatrix.tsx',
      'app/(protected)/scm/sales-orders/*/components/SalesOrderDetail.tsx',
      'app/(protected)/scm/sales-orders/components/SalesOrdersGrid.tsx',
      'app/(protected)/sla-management/conversation-sla-tracking/components/MyPendingSLAWidget.tsx',
      'app/(protected)/sla-management/conversation-sla-tracking/components/ReassignDialog.tsx',
      'app/(protected)/sla-management/conversation-sla-tracking/components/TicketSlaChips.tsx',
      'app/(protected)/sla-management/conversations/components/ConversationListPane.tsx',
      'app/(protected)/store-client/home/special-offers/card1.tsx',
      'app/(protected)/system-management/activity/components/ActivityTimeline.tsx',
      'app/(protected)/system-management/ai-assistant/components/TraceView.tsx',
      'app/(protected)/system-management/ai-assistant/prompts/components/PromptDetail.tsx',
      'app/(protected)/system-management/ai-assistant/prompts/components/PromptsList.tsx',
      'app/(protected)/system-management/api-call-logs/components/ApiCallDetailDrawer.tsx',
      'app/(protected)/system-management/app-store/components/AppStoreAdmin.tsx',
      'app/(protected)/system-management/chat-history/components/ChatTranscript.tsx',
      'app/(protected)/system-management/chat-history/components/StateTracePanel.tsx',
      'app/(protected)/system-management/health/components/HealthDashboard.tsx',
      'app/(protected)/system-management/import-jobs/components/OutcomeBreakdownCard.tsx',
      'app/(protected)/ticket-management/tickets/components/TicketsKanban.tsx',
      'app/(protected)/user-management/access-agents/components/MemberBrandEditor.tsx',
      'app/(protected)/user-management/access-agents/components/MemberMarketSegmentEditor.tsx',
      'app/(protected)/user-management/teams/components/team-member-popover.tsx',
      'app/(public)/c/*/supplier-request/*/page.tsx',
      'app/components/common/AIAssistantBubble.tsx',
      // demo1 is `Demo1Layout` (the mounted shell, not exempted above like
      // demo2-10), so these four go through the same measured-debt door as
      // everything else in this list rather than an ignore.
      'app/components/layouts/demo1/components/header.tsx',
      'app/components/layouts/demo1/components/mega-menu-mobile.tsx',
      'app/components/layouts/demo1/components/quick-access-block.tsx',
      'app/components/layouts/demo1/components/sidebar-menu.tsx',
      'components/common/ActivitiesNotesPanel/EntityActivitiesLayout.tsx',
      'components/common/ActivitiesNotesPanel/index.tsx',
      'components/common/RespondChatList.tsx',
      'components/common/conversation/InternalCommentComposer.tsx',
      'components/common/find-in-text/FindBar.tsx',
      'components/my-downloads/MyDownloadsIcon.tsx',
      'components/spec-proposals/SpecProposalReview.tsx',
      'components/spec-table/SpecSourceBadge.tsx',
      'components/spec-table/SpecValueCell.tsx',
      'components/ui/data-grid-list-toolbar.tsx',
      'components/upload-activity/UploadActivityIcon.tsx',
      'components/upload-activity/UploadSessionRow.tsx',
    ],
    rules: { 'local/no-px-text-class': 'off' },
  },
  {
    // .next-*: the NEXT_DIST_DIR builds (.next-dev, .next-never-stuck).
    ignores: ['.next/**', '.next-*/**', 'node_modules/**', 'prisma/**'],
  },
];

// Exported (in addition to the default config) so
// `eslint.config.text-px-rule.test.ts` can drive the rule directly through
// ESLint's own `Linter` class - see S9-01.
export { noPxTextClassRule };
export default eslintConfig;
