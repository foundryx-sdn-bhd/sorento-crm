# PLAN: product name hidden when it equals the code (NAME-EQUALS-CODE)

Status: planned, awaiting card answers. Track: full (M; ~130 frontend sites, diff over 300
lines; no migration, no auth/RBAC change). Plan created: 2026-10-14T00:00:00Z.

UAC: `name-equals-code-acceptance-criteria.md`. Card: `CARD-name-equals-code.md`.

## Why

Owner 14 Oct 2026: "the product name please don't show if same with product code, we should have
this principle across our system". Dev data: 15,339 of 15,339 products have
`product_name = product_code`, so every code + name spot prints the code twice today.

## Seam

One frontend helper, one backend twin. Nothing else new.

- `sorento_crm_frontend/lib/product-display.ts`: `sameDisplayText(a, b)`, `distinctName(code, name)`,
  `codeWithName(code, name, sep = ' - ')` (UAC AC-1).
- `sorento_crm_backend/app/utils/product_display.py`: `same_display_text`, `distinct_name`,
  `code_with_name` (same table).
- MCP keeps its own `_distinct_name` (separate package, no backend import); its comparison changes
  to the same normalisation.

## Slices (commits on this one branch)

S0 `test(red):` tester writes the red tests alone (helper both sides, one per component type).
S1 helpers + shared renderers (SearchableSelect, SearchableMultiSelect, portal AsyncCombobox,
   AsyncMultiCombobox, MultiPillInput) + existing guards folded onto the helper.
S2 joined-label builders (group A below).
S3 cells, dialogs, tooltips (groups C, D, E).
S4 backend + MCP (group G).

## Red tests (tester, before any code)

- `lib/product-display.test.ts`: AC-1 table.
- `components/common/SearchableSelect.test.tsx` (extend): option with description equal to label
  renders one line; differing description renders; searching the hidden text still finds it.
- `components/common/SearchableMultiSelect.test.tsx` (extend): same.
- Joined label: `scmOptionsService.searchProductOptions` returns label `1861` for code=name=1861
  and `SRT6641 · X` when differing (mock `api`).
- Cell: one code-over-name cell (e.g. `ProductDataUpdatedDialog` or a reorder grid cell) renders
  the code once.
- Dialog: `components/dealer-kit/ProductDataUpdatedDialog.tsx` no `11X11 / 11X11`.
- Backend: `tests/test_product_display.py` (helper table) + `_set_member_text` in
  `app/services/dealer_kit/tag_data_service.py` + `scm/policy_service._label` for sku scope.

## Site checklist (paths under `sorento_crm_frontend/`, AP = `app/(protected)`)

### Shared choke points (S1)
- `components/common/SearchableSelect.tsx:509-519` option body; `SearchableMultiSelect.tsx:505-520`.
- `app/(auth)/portal/components/AsyncCombobox.tsx:350-352`, `AsyncMultiCombobox.tsx:236-238`,
  `MultiPillInput.tsx:263-264`.
- Existing guards to fold: `lib/dealer-kit/product-block.ts:57` nameOrBlankIfCode;
  `AP/scm/components/ContainerRequestSection.tsx:150` productNameDiffersFromCode;
  `AP/scm/components/PlanRowDialog.tsx:2122`; `AP/scm/purchase-orders/.../PurchaseOrderDetail.tsx:500`;
  `AP/scm/sales-orders/.../SalesOrderDetail.tsx:826`; `SPODocumentDetail.tsx:432`;
  `AP/project-sales/stock-debt/components/StockDebtCellDialog.tsx:677`;
  `AP/project-sales/fulfilment-planning/components/BoardTransfersPanel.tsx:139-160`;
  `AP/project-sales/order-inquiries/components/orderInquiryWorklistColumns.tsx:742`;
  `OrderInquiryDocumentDialog.tsx:315-326`; `AP/dealer-kit/.../RequestTagDesigner.tsx:2006`;
  `AP/dealer-kit/flyer-readings/components/MatchReportSections.tsx:248-291`;
  procurement `ProductCombobox.tsx:78/84` selected display.

### A. Joined labels (S2)
- `AP/scm/services/scmOptionsService.ts:173,186`; `AP/scm/policies/services/scmPolicyService.ts:325`
  (+ mock `scmPolicyMock.ts:235,251,380`); `AP/scm/components/productOrSetPicker.tsx:81,100`;
  `AP/scm/purchase-orders/[id]/components/PurchaseOrderDetail.tsx:256`;
  `AP/scm/sales-orders/[id]/components/salesOrderLineDraft.ts:67`.
- `AP/master-data-management/products/[id]/components/ProductCombosSection.tsx:41`,
  `ProductVariantPickerDialog.tsx:36`, `AddCompanionRuleModal.tsx:29`;
  `product-specifications/services/productSpecService.ts:834`;
  `certificates/services/certificateService.ts:197`; `product-sets/services/productSetService.ts:177`.
- `AP/procurement-management/packing-lists/components/ProductCombobox.tsx:72`,
  `ProductComboboxSearchable.tsx:38`; `spo-allocations/components/ProductCombobox.tsx:78`.
- `AP/sales/opportunities/services/salesOpportunityService.ts:122`;
  `sales/opportunities/[id]/components/SalesOpportunityDetail.tsx:178,511-512`;
  `AP/sales/targets/services/salesTargetService.ts:124`.
- `AP/order-management/orders/components/OrderLinesCard.tsx:116-123`, `OrderLineDeleteDialog.tsx:49-51`.
- `AP/marketing-management/promotions/components/PromotionDetail.tsx:821,826`.
- `AP/user-management/contacts/[id]/services/contactChatbotService.ts:316-319`.
- `AP/dealer-kit/tag-templates/components/TagCanvasEditor.tsx:282-287`;
  `AP/dealer-kit/price-tag-requests/components/PriceTagRequestDetail.tsx:1318-1319`.
- Portal: `app/(auth)/portal/sales_opportunity/components/PortalOpportunityLineRow.tsx:59`,
  `SalesOpportunityPortalDetail.tsx:166,450`; `app/(auth)/portal/components/PriceTagRequestForm.tsx:3553`;
  `DOFilterMultiSelect.tsx:536-537`.

### B. label = code, description = name (covered by S1, no edit unless a custom renderer)
- Custom renderers that bypass S1: `AP/project-sales/[projectId]/delivery-schedules/components/DeliveryScheduleProductPicker.tsx:112-120`;
  `AP/dealer-kit/design/components/RoomDesigner.tsx:1173-1179`; `AP/dealer-kit/bundles/components/BundleDialog.tsx:174-180`;
  `AP/scm/components/productOrSetPicker.tsx:118`.
- Portal `SubmissionForm.tsx:1823,2639,2973,3040` and `PriceTagRequestForm.tsx:1013,1415,3196`
  (`description: "Product - CODE"` under `label: name || code`: when name equals code the
  description repeats it, drop it).

### C. Cells (S3)
- SCM reorder: `PoWorklistView.tsx:165-172`, `PlanExceptionsView.tsx:124-131`,
  `NeedsLevelView.tsx:108-113`, `ReorderResultsGrid.tsx:320-322`, `DispositionResultsGrid.tsx:186-188`,
  `LevelChangesPanel.tsx:136-140`, `ProductPhotoPopover.tsx:169-174`.
- `AP/scm/components/ProductPerspectiveGrid.tsx:189-191`, `SupplierPerspectiveList.tsx:197-199`,
  `ProductListDialog.tsx:488-493` (keep warehouse).
- Master data product detail: `ProductAlternativesSection.tsx:84-90`, `ProductCompatibilitySection.tsx:56-62`,
  `ProductCombosSection.tsx:66-70`, `ProductVariantsTab.tsx:248/254,318/320,380/382`;
  `flyer-spec-proposals/components/ProductProposalGroup.tsx:275/291`;
  `product-sets/components/ProductSetDetail.tsx:458,462/479`.
- Inventory: `stock-ledger/components/StockLedgerList.tsx:63-64`,
  `stock-transfers/[id]/components/StockTransferDetail.tsx:155-158`,
  `stock/[productId]/[warehouseId]/page.tsx:241-245,281-282`.
- Project sales: `stock-debt/components/StockDebtClient.tsx:459-474`;
  `series/[seriesId]/components/SeriesProductsTable.tsx:79-86` (`-` when equal);
  `[projectId]/components/AmendmentAutocountChangeList.tsx:66-70` (`-` when equal).
- Dealer kit: `design/summary/components/DesignSummary.tsx:192-197`, `editions/[editionId]/components/EditionDetail.tsx:315-317,333-334`,
  `collections/components/CollectionDialog.tsx:179-181`, `components/ProductPickerDialog.tsx:122-127`,
  `components/TileGrid.tsx:198-205`, `components/BundleCard.tsx:57-65`,
  `brochure-images/components/BrochureImageDialog.tsx:63-65`, `price-tag-requests/components/PriceTagRequestDetail.tsx:1153-1161`,
  `flyer-readings/components/MatchReportSections.tsx:360-375`.
- `components/stock-asks/AskConversationPanel.tsx:115-116`.
- Portal: `DOFilterMultiSelect.tsx:409-418`, `PriceTagRequestForm.tsx:2257-61,2314-17,3130-32`.

### D. Dialogs / headers / inline (S3)
- SCM reorder: `ReorderExplanationDialog.tsx:984-985`, `RejectRecommendationDialog.tsx:61`,
  `PlanExceptionReviewSheet.tsx:239-246`, `CoverageTimelinePanel.tsx:633-634`, `DemandDrillPopover.tsx:128-129`,
  `PlanRowPanel.tsx:667-669`, `PlanRowDialogs.tsx:899-912`, `AdjustRecommendationModal.tsx:144/160`,
  `DispositionResultsGrid.tsx:98-101`.
- `AP/scm/loading-plan/components/ContainerRequestRowDialog.tsx:72/96,92-94`;
  `AP/scm/policies/components/ResolutionPreviewCard.tsx:193-194`.
- `AP/master-data-management/flyer-spec-proposals/components/AddProposalRowDialog.tsx:103`;
  `products/[id]/components/ProductDetail.tsx:158/166`; `products/actions.tsx:43`.
- `components/dealer-kit/ProductDataReviewDialog.tsx:73-75`, `ProductDataUpdatedDialog.tsx:78-80`.
- `AP/project-sales/fulfilment-planning/components/SupplyLineCard.tsx:125-128,469-472` and
  `FulfilmentBoardPanel.tsx:1952-1956` print line `description` (document text): untouched.

### E. Tooltips (S3)
- `AP/master-data-management/products/components/ProductsList.tsx:475`, `ProductVariantsTab.tsx:246,315,377`;
  `StockTransfersPanel.tsx:337`; `AP/project-sales/stock-claims/components/StockClaimsClient.tsx:137`;
  `AP/order-management/customers/components/CustomerAsksTab.tsx:56-57`;
  `components/stock-asks/AskCard.tsx:69`; portal `CustomerAsksHistory.tsx:134`.

### F. Untouched (Q1 a / Q2 a)
Master/list grids with separate Code and Name columns; line `description` columns that are stored
document text; quotation line description default; client CSV with separate columns
(`LevelChangesPanel.tsx:52-56`); `store-admin/`, `store-client/` demo templates.

### G. Backend (S4, paths under `sorento_crm_backend/`)
- `app/services/dealer_kit/tag_data_service.py:469-472` `_set_member_text`, `:726-731` `_package_text`.
- `app/services/scm/policy_service.py:105` `_label`, `:493` resolve.
- `app/services/sales/target_service.py:858` (product only; `_code_name` stays for category/brand).
- `app/services/order_service.py:1680` analytics label.
- `app/services/marketing_service.py:196` `_product_display_label`.
- `app/services/stock_ask_service.py:474` (already guarded, route through helper).
- `sorento_crm_mcp/sorento_crm_mcp/presenters.py:204` `_distinct_name` normalisation.

## Out of scope / triggers

- Chatbot prompts: unchanged (chatbot freeze; replies already print the code only,
  `app/services/chatbot/lanes/business/answer.py:2367`).
- Changing what is stored (quotation description default): separate lane if the owner wants it.
